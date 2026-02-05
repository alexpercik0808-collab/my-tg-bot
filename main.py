import os
import asyncio
from asyncio import create_task

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update, InputMediaPhoto
from aiogram.fsm.storage.memory import MemoryStorage

from fastapi import FastAPI, Request
from groq import Groq

# ================== CONFIG ==================

ADMIN_ID = 5405313198
CHANNEL_ID = -1002407007220
SUPPORT_USERNAME = "Gaeid12"

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = "https://my-tg-bot-xt1p.onrender.com/webhook"

# ================== APP ==================

app = FastAPI()
dp = Dispatcher(storage=MemoryStorage())

bot: Bot | None = None
client: Groq | None = None

user_data = {}

photo_buffer = {}   # media_group_id -> [file_id]
photo_tasks = {}    # media_group_id -> asyncio.Task

# ================== AI ==================

def improve_text(text: str) -> str:
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "Ты — лаконичный менеджер барахолки. Пиши кратко. Структура: Название, Состояние, Описание (2 фразы)."
                },
                {
                    "role": "user",
                    "content": f"Сделай краткое объявление: {text}"
                }
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"Ошибка ИИ: {e}"

# ================== START ==================

@dp.message(Command("start"))
async def start(message: types.Message):
    kb = [[types.InlineKeyboardButton(
        text="🛠 Поддержка",
        url=f"https://t.me/{SUPPORT_USERNAME}"
    )]]

    await message.answer(
        "👋 <b>Привет!</b>\nПришли описание товара.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb)
    )

# ================== TEXT ==================

@dp.message(F.text & ~F.command)
async def handle_text(message: types.Message):
    uid = message.from_user.id

    if uid in user_data and user_data[uid].get("step") == "wait_manual_text":
        user_data[uid]["improved"] = message.text
        user_data[uid]["step"] = "wait_price"
        await message.answer("💰 Укажи цену.")
        return

    if uid in user_data and user_data[uid].get("step") == "wait_price":
        user_data[uid]["price"] = message.text
        user_data[uid]["step"] = "wait_photo"
        await message.answer("📸 Отправь фото (можно несколько).")
        return

    user_data[uid] = {
        "username": message.from_user.username,
        "step": "wait_confirm"
    }

    wait_msg = await message.answer("🤖 ИИ думает...")
    new_text = improve_text(message.text)
    user_data[uid]["improved"] = new_text

    kb = [[
        types.InlineKeyboardButton(text="✅ Оставить", callback_data="accept_text"),
        types.InlineKeyboardButton(text="✏️ Изменить", callback_data="edit_manual")
    ]]

    await wait_msg.edit_text(
        f"✨ <b>Вариант ИИ:</b>\n\n{new_text}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb)
    )

# ================== CALLBACKS ==================

@dp.callback_query(F.data == "accept_text")
async def accept_text(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_data[uid]["step"] = "wait_price"
    await callback.message.edit_text(callback.message.text + "\n\n💰 Укажи цену.")
    await callback.answer()

@dp.callback_query(F.data == "edit_manual")
async def edit_manual(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_data[uid]["step"] = "wait_manual_text"
    await callback.message.edit_text("✍️ Напиши свой текст.")
    await callback.answer()

# ================== PHOTOS (ALBUM SAFE) ==================

@dp.message(F.photo)
async def handle_photos(message: types.Message):
    uid = message.from_user.id

    if uid not in user_data or user_data[uid].get("step") != "wait_photo":
        return

    mgid = message.media_group_id or f"single_{message.message_id}"

    photo_buffer.setdefault(mgid, []).append(message.photo[-1].file_id)

    if mgid in photo_tasks:
        photo_tasks[mgid].cancel()

    photo_tasks[mgid] = create_task(process_album(mgid, uid))

async def process_album(media_group_id: str, uid: int):
    try:
        await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        return

    photos = photo_buffer.pop(media_group_id, [])
    photo_tasks.pop(media_group_id, None)

    if not photos:
        return

    data = user_data[uid]
    data["photos"] = photos

    username = f"@{data['username']}" if data["username"] else "Контакт скрыт"

    caption = (
        f"{data['improved']}\n\n"
        f"💰 Цена: {data['price']}\n"
        f"👤 Продавец: {username}"
    )

    media = [
        InputMediaPhoto(media=p, caption=caption if i == 0 else None)
        for i, p in enumerate(photos)
    ]

    kb = [[
        types.InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub_{uid}"),
        types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decl_{uid}")
    ]]

    await bot.send_media_group(ADMIN_ID, media)
    await bot.send_message(
        ADMIN_ID,
        "Что делаем?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb)
    )

    await bot.send_message(uid, "⌛ Отправлено админу.")

# ================== PUBLISH ==================

@dp.callback_query(F.data.startswith("pub_"))
async def publish(callback: types.CallbackQuery):
    uid = int(callback.data.split("_")[1])
    data = user_data.get(uid)
    if not data:
        return

    username = f"@{data['username']}" if data["username"] else "Контакт скрыт"

    caption = (
        f"{data['improved']}\n\n"
        f"💰 Цена: {data['price']}\n"
        f"👤 Продавец: {username}"
    )

    media = [
        InputMediaPhoto(media=p, caption=caption if i == 0 else None)
        for i, p in enumerate(data["photos"])
    ]

    await bot.send_media_group(CHANNEL_ID, media)
    await bot.send_message(uid, "✅ Опубликовано!")
    await callback.answer()

@dp.callback_query(F.data.startswith("decl_"))
async def decline(callback: types.CallbackQuery):
    uid = int(callback.data.split("_")[1])
    await bot.send_message(uid, "❌ Отклонено.")
    await callback.answer()

# ================== WEBHOOK ==================

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

# ================== HEALTHCHECK (для UptimeRobot) ==================

@app.get("/")
async def health():
    return {"status": "ok"}

# ================== STARTUP ==================

@app.on_event("startup")
async def on_startup():
    global bot, client

    bot = Bot(
        token=os.environ["BOT_TOKEN"],
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    await bot.set_webhook(WEBHOOK_URL)
