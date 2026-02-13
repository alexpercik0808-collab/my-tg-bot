import os
import sqlite3
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto
)
from aiogram.filters import Command
from fastapi import FastAPI, Request

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME")
BASE_URL = os.getenv("BASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI()

# DATABASE

conn = sqlite3.connect("db.sqlite3")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS ads(
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
text TEXT,
status TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS photos(
id INTEGER PRIMARY KEY AUTOINCREMENT,
ad_id INTEGER,
file_id TEXT
)
""")

conn.commit()

# STATES

user_state = {}
user_photos = {}

# MENUS

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Подать объявление", callback_data="new_ad")],
        [InlineKeyboardButton(text="🛠 Поддержка", url=f"https://t.me/{SUPPORT_USERNAME.replace('@','')}")]
    ])

def approve_menu(ad_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"approve:{ad_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{ad_id}")
        ]
    ])

def publish_menu(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💬 Написать продавцу",
            url=f"tg://user?id={user_id}"
        )],
        [InlineKeyboardButton(
            text="📨 Подать объявление",
            url=f"https://t.me/{bot.username}"
        )],
        [InlineKeyboardButton(
            text="🛠 Поддержка",
            url=f"https://t.me/gaeid12{SUPPORT_USERNAME.replace('@','')}"
        )]
    ])

# START

@router.message(Command("start"))
async def start(msg: Message):
    await msg.answer(
        "Главное меню",
        reply_markup=main_menu()
    )

# NEW AD

@router.callback_query(F.data == "new_ad")
async def new_ad(call: CallbackQuery):

    user_state[call.from_user.id] = "text"

    await call.message.answer("Отправь текст объявления")

# TEXT

@router.message()
async def text_handler(msg: Message):

    state = user_state.get(msg.from_user.id)

    if state == "text":

        cursor.execute(
            "INSERT INTO ads(user_id,text,status) VALUES(?,?,?)",
            (msg.from_user.id, msg.text, "pending")
        )

        conn.commit()

        ad_id = cursor.lastrowid

        user_state[msg.from_user.id] = ("photo", ad_id)

        user_photos[ad_id] = []

        await msg.answer("Теперь отправь до 10 фото. Когда закончишь — /done")

# PHOTO

@router.message(F.photo)
async def photo(msg: Message):

    state = user_state.get(msg.from_user.id)

    if not state:
        return

    if state[0] != "photo":
        return

    ad_id = state[1]

    file_id = msg.photo[-1].file_id

    if len(user_photos[ad_id]) >= 10:

        await msg.answer("Максимум 10 фото")
        return

    user_photos[ad_id].append(file_id)

    cursor.execute(
        "INSERT INTO photos(ad_id,file_id) VALUES(?,?)",
        (ad_id, file_id)
    )

    conn.commit()

    await msg.answer(f"Фото {len(user_photos[ad_id])}/10")

# DONE

@router.message(Command("done"))
async def done(msg: Message):

    state = user_state.get(msg.from_user.id)

    if not state:
        return

    ad_id = state[1]

    cursor.execute(
        "SELECT text FROM ads WHERE id=?",
        (ad_id,)
    )

    text = cursor.fetchone()[0]

    await bot.send_message(
        ADMIN_ID,
        text,
        reply_markup=approve_menu(ad_id)
    )

    await msg.answer(
        "Отправлено на модерацию",
        reply_markup=main_menu()
    )

    user_state.pop(msg.from_user.id)

# APPROVE

@router.callback_query(F.data.startswith("approve"))
async def approve(call: CallbackQuery):

    ad_id = call.data.split(":")[1]

    cursor.execute(
        "SELECT user_id,text FROM ads WHERE id=?",
        (ad_id,)
    )

    user_id, text = cursor.fetchone()

    cursor.execute(
        "SELECT file_id FROM photos WHERE ad_id=?",
        (ad_id,)
    )

    photos = cursor.fetchall()

    media = []

    for i, photo in enumerate(photos):

        if i == 0:
            media.append(InputMediaPhoto(media=photo[0], caption=text))
        else:
            media.append(InputMediaPhoto(media=photo[0]))

    if media:
        await bot.send_media_group(CHANNEL_ID, media)
        await bot.send_message(
            CHANNEL_ID,
            "👇",
            reply_markup=publish_menu(user_id)
        )
    else:
        await bot.send_message(
            CHANNEL_ID,
            text,
            reply_markup=publish_menu(user_id)
        )

    await call.message.edit_text("Опубликовано")

# WEBHOOK

@app.post("/")
async def webhook(req: Request):
    data = await req.json()
    await dp.feed_raw_update(bot, data)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    await bot.set_webhook(BASE_URL)
