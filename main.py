import os
import asyncio
import sqlite3
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

# ================= DB =================

conn = sqlite3.connect("ads.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS ads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    address TEXT,
    price TEXT
)
""")
conn.commit()

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
                {"role": "system",
                 "content":
                 "Ты—технический редактор. твоя задача оформить текст пользователя в красивый спикок, без удаления характеристик."
                 "не сокращай характеристики товара, например состояние, материал и прочее если есть"
                 "Выпиши их все через буллиты '•'. "
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
            [InlineKeyboardButton(text="🛠 Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}")]
        ]
    )

# ================= START =================

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "👋 Привет!\nНажми кнопку ниже или отправь описание товара.",
        reply_markup=main_menu()
    )

# ================= NEW AD =================

@dp.callback_query(F.data == "new_ad")
async def new_ad(callback: types.CallbackQuery):
    user_data[callback.from_user.id] = {"step": "wait_text"}
    await callback.message.answer("✏️ Отправь описание товара.")
    await callback.answer()

# ================= TEXT =================

@dp.message(F.text)
async def text_handler(message: types.Message):
    uid = message.from_user.id
    step = user_data.get(uid, {}).get("step")

    if step == "wait_text":
        improved = improve_text(message.text)
        user_data[uid] = {
            "step": "confirm_text",
            "original": message.text,
            "improved": improved
        }

        kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="✅ Использовать", callback_data="ok_text"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data="edit_text")
            ]]
        )

        await message.answer(f"✨ Вариант ИИ:\n\n{improved}", reply_markup=kb)
        return

    if step == "wait_manual":
        user_data[uid]["improved"] = message.text
        user_data[uid]["step"] = "wait_photo"
        await message.answer("📸 Отправь до 10 фото.")
        return

    if step == "wait_address":
        user_data[uid]["address"] = message.text
        user_data[uid]["step"] = "wait_price"
        await message.answer("💰 Теперь отправь цену.")
        return

    if step == "wait_price":
        user_data[uid]["price"] = message.text
        await send_to_admin(uid)
        await message.answer("✅ Отправлено на модерацию.", reply_markup=main_menu())
        user_data.pop(uid, None)
        return

    if step == "wait_photo":
        await message.answer("❌ Нужно отправить фото.")
        return

# ================= CONFIRM =================

@dp.callback_query(F.data == "ok_text")
async def ok_text(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_data[uid]["step"] = "wait_photo"
    await callback.message.answer("📸 Отправь до 10 фото.")
    await callback.answer()

@dp.callback_query(F.data == "edit_text")
async def edit_text(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_data[uid]["step"] = "wait_manual"
    await callback.message.answer("✏️ Напиши текст вручную.")
    await callback.answer()

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
    await bot.send_message(uid, "📍 Отправь адрес.")

# ================= ADMIN =================

async def send_to_admin(uid):
    data = user_data[uid]

    caption = (
        f"{data['improved']}\n\n"
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
    uid = int(callback.data.split("_")[1])
    data = user_data.get(uid)

    title = data["improved"].split("\n")[0][:60]
    seller_link = f"tg://user?id={uid}"

    caption = (
        f"📌 <u>{title}</u>\n\n"
        f"{data['improved']}\n\n"
        f"💰 Цена — {data['price']}\n"
        f"📍 Адрес — {data['address']}\n\n"
        f"———————————————\n"
        f"‼️ <a href='https://t.me/{BOT_USERNAME}'>Как разместить объявление</a>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✉️ Написать продавцу", url=seller_link)
    ]])

    media = [
        InputMediaPhoto(media=p, caption=caption if i == 0 else None)
        for i, p in enumerate(data["photos"])
    ]

    await bot.send_media_group(CHANNEL_ID, media)
    await bot.send_message(CHANNEL_ID, " ", reply_markup=kb)

    await bot.send_message(uid, "✅ Объявление опубликовано!", reply_markup=main_menu())
    user_data.pop(uid, None)
    await callback.answer("Опубликовано")

# ================= DECLINE =================

@dp.callback_query(F.data.startswith("decl_"))
async def decline(callback: types.CallbackQuery):
    uid = int(callback.data.split("_")[1])
    await bot.send_message(uid, "❌ Объявление отклонено.", reply_markup=main_menu())
    await callback.answer()

# ================= WEBHOOK =================

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    await bot.set_webhook(WEBHOOK_URL)
