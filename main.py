import os
import asyncio
import sqlite3

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

ADMIN_ID = int(os.environ.get("ADMIN_ID", "5405313198"))
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1002407007220"))

BOT_USERNAME = os.environ.get("BOT_USERNAME", "your_bot_username")
SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", "Gaeid12")

WEBHOOK_URL = os.environ["WEBHOOK_URL"]

WEBHOOK_PATH = "/webhook"

# ================= INIT =================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())

app = FastAPI()

groq = Groq(api_key=GROQ_API_KEY)

# ================= DATABASE =================

conn = sqlite3.connect("db.sqlite3")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS ads (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
username TEXT,
text TEXT,
price TEXT,
photos TEXT
)
""")

conn.commit()

# ================= MEMORY =================

user_data = {}

# ================= AI =================

def improve_text(text):

    try:

        completion = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role":"system",
                    "content":
                    "ты— технический редактор. Твоя задача: оформить текст пользователя в красивый список."
                    "СТРОГО ЗАПРЕЩЕНО сокращать характеристики товара"
                    "Выпиши их все через буллиты '•'. "
                    "Если в исходном тексте есть подробности про состояние, характеристики товара—обязательно сохрани их"
                },
                {
                    "role":"user",
                    "content":text
                }
            ]
        )

        return completion.choices[0].message.content

    except Exception as e:

        return text

# ================= KEYBOARDS =================

def start_kb():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="📨 Подать объявление",
                    callback_data="new_ad"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🛠 Поддержка",
                    url=f"https://t.me/gaeid12{SUPPORT_USERNAME}"
                )
            ]

        ]
    )


def after_publish_kb():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="📨 Подать объявление",
                    url=f"https://t.me/{BOT_USERNAME}"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🛠 Поддержка",
                    url=f"https://t.me/gaeid12{SUPPORT_USERNAME}"
                )
            ]

        ]
    )

# ================= START =================

@dp.message(Command("start"))
async def start(message: types.Message):

    await message.answer(

        "👜 <b>новое объявление</b>\n\n"
        "Нажми кнопку ниже чтобы подать объявление",

        reply_markup=start_kb()

    )

# ================= NEW AD =================

@dp.callback_query(F.data == "new_ad")
async def new_ad(callback: types.CallbackQuery):

    user_data[callback.from_user.id] = {
        "step":"wait_text"
    }

    await callback.message.answer(
        "✏ Отправь текст объявления"
    )

# ================= TEXT =================

@dp.message(F.text)
async def text_handler(message: types.Message):

    uid = message.from_user.id

    if uid not in user_data:
        return

    step = user_data[uid]["step"]

    if step == "wait_text":

        user_data[uid]["username"] = message.from_user.username
        user_data[uid]["user_id"] = uid

        wait = await message.answer("🤖 ИИ обрабатывает...")

        improved = improve_text(message.text)

        user_data[uid]["improved"] = improved
        user_data[uid]["step"] = "wait_confirm"

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Оставить",
                        callback_data="accept"
                    ),
                    InlineKeyboardButton(
                        text="✏ Изменить",
                        callback_data="edit"
                    )
                ]
            ]
        )

        await wait.edit_text(
            improved,
            reply_markup=kb
        )

        return


    if step == "wait_manual":

        user_data[uid]["improved"] = message.text
        user_data[uid]["step"] = "wait_price"

        await message.answer("💰 Отправь цену")

        return


    if step == "wait_price":

        user_data[uid]["price"] = message.text
        user_data[uid]["step"] = "wait_photo"
        user_data[uid]["photos"] = []

        await message.answer("📸 Отправь фото (до 10)")

        return


    if step == "wait_photo":

        await message.answer("❗ Друг, отправь именно фото")

# ================= CALLBACKS =================

@dp.callback_query(F.data == "accept")
async def accept(callback: types.CallbackQuery):

    uid = callback.from_user.id

    user_data[uid]["step"] = "wait_price"

    await callback.message.answer("💰 Отправь цену")

@dp.callback_query(F.data == "edit")
async def edit(callback: types.CallbackQuery):

    uid = callback.from_user.id

    user_data[uid]["step"] = "wait_manual"

    await callback.message.answer("✏ Напиши свой текст")

# ================= PHOTO =================

@dp.message(F.photo)
async def photo_handler(message: types.Message):

    uid = message.from_user.id

    if uid not in user_data:
        return

    if user_data[uid]["step"] != "wait_photo":
        return

    photos = user_data[uid]["photos"]

    if len(photos) >= 10:

        await message.answer("❗ максимум 10 фото")

        return

    photos.append(message.photo[-1].file_id)

    user_data[uid]["photos"] = photos

    if "timer" in user_data[uid]:
        user_data[uid]["timer"].cancel()

    async def finalize():

        await asyncio.sleep(1)

        data = user_data[uid]

        cursor.execute(

            "INSERT INTO ads(user_id,username,text,price,photos) VALUES(?,?,?,?,?)",

            (
                data["user_id"],
                data["username"],
                data["improved"],
                data["price"],
                ",".join(data["photos"])
            )

        )

        conn.commit()

        caption = (

            f"{data['improved']}\n\n"
            f"💰 Цена: {data['price']}"

        )

        media = []

        for i,p in enumerate(data["photos"]):

            media.append(

                InputMediaPhoto(
                    media=p,
                    caption=caption if i == 0 else None
                )

            )

        await bot.send_media_group(ADMIN_ID,media)

        await bot.send_message(

            ADMIN_ID,

            "Новое объявление",

            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Опубликовать",
                            callback_data=f"publish_{uid}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Отклонить",
                            callback_data=f"decline_{uid}"
                        )
                    ]
                ]
            )

        )

        await message.answer(
            "✅ Отправлено админу"
        )

    user_data[uid]["timer"] = asyncio.create_task(finalize())

# ================= PUBLISH =================

@dp.callback_query(F.data.startswith("publish_"))
async def publish(callback: types.CallbackQuery):

    uid = int(callback.data.split("_")[1])

    data = user_data.get(uid)

    if not data:
        return

    caption = (

        f"{data['improved']}\n\n"
        f"💰 Цена: {data['price']}\n\n"
        f"<a href='https://t.me/{BOT_USERNAME}'>📨 Как подать объявление</a>"

    )

    media = []

    for i,p in enumerate(data["photos"]):

        media.append(

            InputMediaPhoto(
                media=p,
                caption=caption if i == 0 else None
            )

        )

    await bot.send_media_group(CHANNEL_ID,media)

    seller_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✉ Написать продавцу",
                    url=f"tg://user?id={uid}"
                )
            ]
        ]
    )

    await bot.send_message(
        CHANNEL_ID,
        "Связаться:",
        reply_markup=seller_kb
    )

    await bot.send_message(
        uid,
        "✅ Объявление опубликовано",
        reply_markup=after_publish_kb()
    )

# ================= DECLINE =================

@dp.callback_query(F.data.startswith("decline_"))
async def decline(callback: types.CallbackQuery):

    uid = int(callback.data.split("_")[1])

    await bot.send_message(
        uid,
        "❌ Объявление отклонено",
        reply_markup=start_kb()
    )

# ================= WEBHOOK =================

@app.post(WEBHOOK_PATH)
async def webhook(req: Request):

    update = Update.model_validate(

        await req.json(),
        context={"bot":bot}

    )

    await dp.feed_update(bot,update)

    return {"ok":True}

# ================= STARTUP =================

@app.on_event("startup")
async def startup():

    await bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)

@app.get("/")
async def root():
    return {"ok":True}
