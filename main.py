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
                        "Твоя задача оформить текст пользователя в красивый список, без удаления характеристик. "
                        "Не сокращай характеристики товара, например состояние, материал и прочее если есть. "
                        "Выпиши их все через буллиты '•'. "
                        "Не выдумывай лишнего."
                    )
                },
                {"role": "user", "content": text}
            ]
        )
        return completion.choices[0].message.content
    except Exception:
        return text

# ================= MENU =================

def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 Создать объявление", callback_data="new_ad")],
            [InlineKeyboardButton(text="🛠 Поддержка", url="https://t.me/gaeid12")]
        ]
    )

# ================= START =================

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "👋 Привет!\nНажми кнопку ниже.",
        reply_markup=main_menu()
    )

# ================= NEW AD =================

@dp.callback_query(F.data == "new_ad")
async def new_ad(callback: types.CallbackQuery):
    uid = callback.from_user.id

    if uid in user_data and user_data[uid].get("status") == "pending":
        await callback.answer("У вас уже есть объявление на модерации", show_alert=True)
        return

    user_data[uid] = {"step": "wait_title"}
    await callback.message.answer("📝 Введите заголовок объявления (например: Samsung A32)")
    await callback.answer()

# ================= TEXT =================

@dp.message(F.text)
async def text_handler(message: types.Message):
    uid = message.from_user.id
    step = user_data.get(uid, {}).get("step")

    if step == "wait_title":
        user_data[uid]["title"] = message.text.strip()
        user_data[uid]["step"] = "wait_description"
        await message.answer("📄 Теперь отправьте описание товара.")
        return

    if step == "wait_description":
        improved = improve_text(message.text.strip())
        user_data[uid]["description"] = improved
        user_data[uid]["step"] = "wait_photo"
        await message.answer("📸 Отправьте до 10 фото.")
        return

    if step == "wait_address":
        user_data[uid]["address"] = message.text.strip()
        user_data[uid]["step"] = "wait_price"
        await message.answer("💰 Введите цену.")
        return

    if step == "wait_price":
        user_data[uid]["price"] = message.text.strip()
        user_data[uid]["status"] = "pending"
        await send_to_admin(uid)
        await message.answer("✅ Отправлено на модерацию.", reply_markup=main_menu())
        return

# ================= PHOTOS =================

@dp.message(F.photo)
async def photos(message: types.Message):
    uid = message.from_user.id
    if user_data.get(uid, {}).get("step") != "wait_photo":
        return

    mgid = message.media_group_id or str(message.message_id)
    photo_buffer.setdefault(mgid, []).append(message.photo[-1].file_id)

    if len(photo_buffer[mgid]) > 10:
        return

    if mgid in photo_tasks:
        photo_tasks[mgid].cancel()

    photo_tasks[mgid] = create_task(process_album(mgid, uid))

async def process_album(mgid, uid):
    await asyncio.sleep(1.5)
    photos = photo_buffer.pop(mgid, [])
    user_data[uid]["photos"] = photos
    user_data[uid]["step"] = "wait_address"
    await bot.send_message(uid, "📍 Введите адрес.")

# ================= SEND TO ADMIN =================

async def send_to_admin(uid):
    data = user_data[uid]

    caption = (
        f"<b>{data['title']}</b>\n\n"
        f"{data['description']}\n\n"
        f"📍 {data['address']}\n"
        f"💰 {data['price']}"
    )

    media = [
        InputMediaPhoto(media=p, caption=caption if i == 0 else None)
        for i, p in enumerate(data["photos"])
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub_{uid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decl_{uid}")
    ]])

    await bot.send_media_group(ADMIN_ID, media)
    await bot.send_message(ADMIN_ID, "Опубликовать?", reply_markup=kb)

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

    caption = (
        f"📌 <u>{data['title']}</u>\n\n"
        f"{data['description']}\n\n"
        f"💰 Цена — {data['price']}\n\n"
        f"📍 <u>Адрес:</u> {data['address']}\n\n"
        f"———————————————\n"
        f"❗ <a href='https://t.me/{BOT_USERNAME}'>Как разместить объявление</a>"
    )

    seller_link = f"tg://user?id={uid}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Написать продавцу", url=seller_link)]
        ]
    )

    media = [
        InputMediaPhoto(media=p, caption=caption if i == 0 else None)
        for i, p in enumerate(data["photos"])
    ]

    await bot.send_media_group(CHANNEL_ID, media)
    await bot.send_message(CHANNEL_ID, " ", reply_markup=kb)

    await bot.send_message(uid, "✅ Объявление опубликовано!", reply_markup=main_menu())
    await bot.send_message(ADMIN_ID, "✅ Объявление опубликовано")

    await callback.message.edit_reply_markup(reply_markup=None)
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
    await callback.answer("Отклонено")

# ================= WEBHOOK =================

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    await bot.set_webhook(WEBHOOK_URL)

