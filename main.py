import os
import asyncio
from asyncio import create_task

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    Update,
    InputMediaPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.fsm.storage.memory import MemoryStorage

from fastapi import FastAPI, Request
from groq import Groq

# ================= CONFIG =================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
BASE_URL = os.environ["BASE_URL"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
BOT_USERNAME = os.environ["BOT_USERNAME"]
SUPPORT_USERNAME = os.environ["SUPPORT_USERNAME"]

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = BASE_URL + WEBHOOK_PATH

# ================= INIT =================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())
client = Groq(api_key=GROQ_API_KEY)
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "working", "info": "Bot is active"}

user_data = {}
photo_buffer = {}
photo_tasks = {}

# ================= AI =================

def improve_text(text):
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — технический редактор. "
                        "Оформляй текст пользователя в красивый список, без удаления характеристик. "
                        "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать звездочки '*' для списка. "
                        "Используй ТОЛЬКО символ буллита '•' в начале каждой строки. "
                        "Не сокращай характеристики товара. "
                        "Не выдумывай лишнего."
                        "Весь текст описания оберни в теги <i>...</i> (курсив). "
                    )
                },
                {"role": "user", "content": text}
            ]
        )
        return completion.choices[0].message.content
    except Exception:
        return text

async def improve_text_async(text):
    return await asyncio.to_thread(improve_text, text)

# ================= MENU =================

def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 Создать объявление", callback_data="new_ad")],
            [InlineKeyboardButton(text="💬 Поддержка", callback_data="support")]
        ]
    )

# ================= START =================

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("👋 Добро пожаловать!", reply_markup=main_menu())

@dp.callback_query(F.data == "support")
async def support(callback: types.CallbackQuery):
    await callback.message.answer(f"📩 Свяжитесь с поддержкой: @{SUPPORT_USERNAME}")
    await callback.answer()

# ================= CREATE AD =================

@dp.callback_query(F.data == "new_ad")
async def new_ad(callback: types.CallbackQuery):
    uid = callback.from_user.id

    if uid in user_data:
        await callback.answer("У вас уже есть незавершённое объявление", show_alert=True)
        return

    user_data[uid] = {"step": "wait_title"}
    await callback.message.answer("📝 Введите заголовок:")
    await callback.answer()

# ================= TEXT HANDLER =================

@dp.message(F.text)
async def text_handler(message: types.Message):
    uid = message.from_user.id
    
    if uid not in user_data:
        await message.answer("Пожалуйста, начните с /start")
        return
        
    step = user_data[uid].get("step")

    if step == "wait_title":
        user_data[uid]["title"] = message.text.strip()
        user_data[uid]["step"] = "wait_description"
        await message.answer("📄 Отправьте описание товара:")
        return

    if step == "wait_description":
        improved = await improve_text_async(message.text.strip())
        user_data[uid]["raw_description"] = message.text.strip()
        user_data[uid]["description"] = improved
        user_data[uid]["step"] = "confirm_description"

        kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="✅ Оставить", callback_data="keep_desc"),
                InlineKeyboardButton(text="✏️ Изменить вручную", callback_data="edit_desc")
            ]]
        )
        await message.answer(f"Вот отредактированный текст:\n\n{improved}", reply_markup=kb)
        return

    if step == "manual_edit":
        user_data[uid]["description"] = message.text.strip()
        user_data[uid]["step"] = "wait_photo"
        await message.answer("📸 Отправьте до 10 фото.")
        return

    if step == "wait_address":
        user_data[uid]["address"] = message.text.strip()
        user_data[uid]["step"] = "wait_price"
        await message.answer("💰 Введите цену:")
        return

    if step == "wait_price":
        user_data[uid]["price"] = message.text.strip()
        user_data[uid]["status"] = "pending"
        await send_to_admin(uid)
        return

# ================= DESCRIPTION BUTTONS =================

@dp.callback_query(F.data == "keep_desc")
async def keep_desc(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if uid not in user_data:
        await callback.answer("Сессия истекла, начните заново", show_alert=True)
        return
        
    user_data[uid]["step"] = "wait_photo"
    await callback.message.answer("📸 Отправьте до 10 фото.")
    await callback.answer()

@dp.callback_query(F.data == "edit_desc")
async def edit_desc(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if uid not in user_data:
        await callback.answer("Сессия истекла, начните заново", show_alert=True)
        return
        
    user_data[uid]["step"] = "manual_edit"
    await callback.message.answer("✏️ Отправьте свой вариант описания:")
    await callback.answer()

# ================= PHOTOS =================

@dp.message(F.photo)
async def photos(message: types.Message):
    uid = message.from_user.id
    if uid not in user_data or user_data[uid].get("step") != "wait_photo":
        return

    mgid = f"{uid}_{message.media_group_id or message.message_id}"
    photo_buffer.setdefault(mgid, []).append(message.photo[-1].file_id)

    if len(photo_buffer[mgid]) > 10:
        await bot.send_message(uid, "📸 Максимум 10 фото")
        return

    if mgid in photo_tasks:
        photo_tasks[mgid].cancel()

    photo_tasks[mgid] = create_task(process_album(mgid, uid))

async def process_album(mgid, uid):
    await asyncio.sleep(1.5)
    if mgid in photo_buffer:
        photos = photo_buffer.pop(mgid, [])
        if uid in user_data:
            user_data[uid]["photos"] = photos
            user_data[uid]["step"] = "wait_address"
            await bot.send_message(uid, "📍 Введите адрес:")

# ================= SEND TO ADMIN =================

async def send_to_admin(uid):
    data = user_data.get(uid)
    if not data:
        return

    photos = data.get("photos", [])

    if not photos:
        await bot.send_message(uid, "❌ Ошибка: фото не получены. Попробуйте заново.")
        if uid in user_data:
            user_data.pop(uid, None)
        return

    caption = (
        f"{data['title']}\n\n"
        f"{data['description']}\n\n"
        f"📍 {data['address']}\n"
        f"💰 {data['price']}"
    )

    media = [
        InputMediaPhoto(media=p, caption=caption if i == 0 else None)
        for i, p in enumerate(photos)
    ]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub_{uid}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decl_{uid}")
        ]]
    )

    try:
        await bot.send_media_group(ADMIN_ID, media)
        await bot.send_message(ADMIN_ID, "Модерация:", reply_markup=kb)
        await bot.send_message(uid, "✅ Отправлено на модерацию.", reply_markup=main_menu())
    except Exception as e:
        print("ADMIN SEND ERROR:", e)
        if uid in user_data:
            user_data.pop(uid, None)
        await bot.send_message(uid, "❌ Ошибка отправки. Попробуйте создать объявление заново.")

# ================= PUBLISH =================

@dp.callback_query(F.data.startswith("pub_"))
async def publish(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    uid = int(callback.data.split("_")[1])
    data = user_data.get(uid)

    if not data or data.get("status") != "pending":
        await callback.answer("Уже обработано")
        return

    user_data[uid]["status"] = "approved"

    # Формируем подпись с текстом объявления
    caption = (
        f"📌 <b>{data['title']}</b>\n\n"         # Тот самый заголовок со скрепкой и жирным шрифтом
    f"{data['description']}\n\n"            # Описание (только один раз!)
    f"💰 <u>Цена: {data['price']}</u>\n"    # Подчеркнутая цена
    f"📍 <u>Адрес: {data['address']}</u>\n\n" # Подчеркнутый адрес
    f"— — — — — — — — — — — — \n"
    f"<a href='https://t.me/{BOT_USERNAME}'>Подать объявление</a>"
    )

    # Кнопка "Написать продавцу"
    seller_link = f"tg://user?id={uid}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✉️ Написать продавцу", url=seller_link)]]
    )

    # Альбом фото
    media = []
    for i, p in enumerate(data["photos"]):
        if i == 0:
            media.append(InputMediaPhoto(media=p, caption=caption, parse_mode=ParseMode.HTML))
        else:
            media.append(InputMediaPhoto(media=p))

    # Отправляем альбом и прикрепляем кнопку к первому фото
    await bot.send_media_group(CHANNEL_ID, media)
    await bot.send_message(CHANNEL_ID, "✉️ Написать продавцу", reply_markup=kb)

    # Уведомляем пользователя и администратора
    await bot.send_message(uid, "✅ Объявление опубликовано!", reply_markup=main_menu())
    await bot.send_message(ADMIN_ID, "✅ Объявление опубликовано")

    await callback.message.edit_reply_markup(reply_markup=None)
    if uid in user_data:
        user_data.pop(uid, None)
    await callback.answer("Опубликовано")

# ================= DECLINE =================

@dp.callback_query(F.data.startswith("decl_"))
async def decline(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    uid = int(callback.data.split("_")[1])
    data = user_data.get(uid)

    if not data or data.get("status") != "pending":
        await callback.answer("Уже обработано")
        return

    user_data[uid]["status"] = "declined"

    await bot.send_message(uid, "❌ Объявление отклонено.", reply_markup=main_menu())
    await bot.send_message(ADMIN_ID, "❌ Объявление отклонено")

    await callback.message.edit_reply_markup(reply_markup=None)
    if uid in user_data:
        user_data.pop(uid, None)
    await callback.answer("Отклонено")

# ================= WEBHOOK =================

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        asyncio.create_task(dp.feed_update(bot, update))
    except Exception as e:
        print("WEBHOOK ERROR:", e)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    await bot.set_webhook(WEBHOOK_URL)
