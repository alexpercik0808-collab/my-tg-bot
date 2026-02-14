import os
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI, Request
from groq import Groq

BOT_TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ["BASE_URL"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
BOT_USERNAME = os.environ["BOT_USERNAME"]
SUPPORT_USERNAME = os.environ["SUPPORT_USERNAME"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = BASE_URL + WEBHOOK_PATH

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()
client = Groq(api_key=GROQ_API_KEY)

conn = sqlite3.connect("ads.db")
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS ads(
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
text TEXT,
address TEXT,
price TEXT,
photos TEXT,
status TEXT
)
""")
conn.commit()

user_data = {}

# ================= MENU =================

def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Создать объявление", callback_data="new")],
        [InlineKeyboardButton(text="🛠 Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}")]
    ])

# ================= START =================

@dp.message(Command("start"))
async def start(m: types.Message):
    await m.answer("👋 Нажми кнопку ниже или отправь текст объявления", reply_markup=menu())

# ================= NEW =================

@dp.callback_query(F.data == "new")
async def new(c: types.CallbackQuery):
    user_data[c.from_user.id] = {"step": "text"}
    await c.message.answer("✏️ Отправь описание товара")
    await c.answer()

# ================= TEXT =================

def improve(t):
    try:
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system", 
                    "content": "ты—технический редактор. твоя задача оформить текст пользователя в красивый список, без удаления характеристик. не сокращай характеристики товара, например состояние, материал и прочее если есть. Выпиши их все через буллиты '•'."
                },
                {"role": "user", "content": f"Оформи объявление:\n{t}"}
            ]
        )
        return r.choices[0].message.content
    except Exception as e:
        return t

@dp.message(F.text)
async def text(m: types.Message):
    d = user_data.get(m.from_user.id)
    if not d: return

    if d["step"] == "text":
        imp = improve(m.text)
        d["text"] = imp
        d["step"] = "photo"
        await m.answer(f"✨ Вариант:\n\n{imp}\n\n📸 Теперь фото (до 10)")
        return

    if d["step"] == "address":
        d["address"] = m.text
        d["step"] = "price"
        await m.answer("💰 Цена?")
        return

    if d["step"] == "price":
        d["price"] = m.text
        await send_admin(m.from_user.id)
        await m.answer("✅ На модерации", reply_markup=menu())
        return

# ================= PHOTO =================

@dp.message(F.photo)
async def photo(m: types.Message):
    d = user_data.get(m.from_user.id)
    if not d or d["step"] != "photo":
        await m.answer("❌ Нужно фото")
        return

    d.setdefault("photos", []).append(m.photo[-1].file_id)
    if len(d["photos"]) >= 10:
        d["step"] = "address"
        await m.answer("📍 Адрес?")
    else:
        await m.answer("👍 Ещё фото или напиши «стоп»")

@dp.message(F.text.lower() == "стоп")
async def stop(m: types.Message):
    d = user_data.get(m.from_user.id)
    if d and d["step"] == "photo":
        d["step"] = "address"
        await m.answer("📍 Адрес?")

# ================= ADMIN =================

async def send_admin(uid):
    d = user_data[uid]
    cur.execute("INSERT INTO ads(user_id,text,address,price,photos,status) VALUES(?,?,?,?,?,?)",
                (uid,d["text"],"", "", ",".join(d["photos"]), "pending"))
    conn.commit()
    ad_id = cur.lastrowid

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub_{ad_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dec_{ad_id}")
        ]
    ])

    await bot.send_message(ADMIN_ID, f"Новое объявление #{ad_id}", reply_markup=kb)

# ================= PUBLISH =================

@dp.callback_query(F.data.startswith("pub_"))
async def pub(c: types.CallbackQuery):
    ad_id = int(c.data.split("_")[1])

    cur.execute("SELECT * FROM ads WHERE id=?", (ad_id,))
    ad = cur.fetchone()
    if not ad or ad[6] != "pending":
        await c.answer("Уже обработано", show_alert=True)
        return

    cur.execute("UPDATE ads SET status='done' WHERE id=?", (ad_id,))
    conn.commit()

    uid,text,address,price,photos = ad[1],ad[2],ad[3],ad[4],ad[5].split(",")

    title = text.split("\n")[0][:50]

    caption = (
        f"📌 <u>{title}</u>\n\n"
        f"{text}\n\n"
        f"💰 Цена — {price}\n"
        f"📍 Адрес — {address}\n\n"
        f"———————————————\n"
        f"‼️ <a href='https://t.me/{BOT_USERNAME}'>Как разместить объявление</a>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Написать продавцу", url=f"tg://user?id={uid}")]
    ])

    media=[InputMediaPhoto(media=p,caption=caption if i==0 else None) for i,p in enumerate(photos)]
    await bot.send_media_group(CHANNEL_ID, media)
    await bot.send_message(CHANNEL_ID, " ", reply_markup=kb)

    await c.answer("Опубликовано")

# ================= DECLINE =================

@dp.callback_query(F.data.startswith("dec_"))
async def dec(c: types.CallbackQuery):
    ad_id = int(c.data.split("_")[1])
    cur.execute("SELECT status FROM ads WHERE id=?", (ad_id,))
    s = cur.fetchone()
    if not s or s[0] != "pending":
        await c.answer("Уже обработано", show_alert=True)
        return
    cur.execute("UPDATE ads SET status='declined' WHERE id=?", (ad_id,))
    conn.commit()
    await c.answer("Отклонено")

# ================= WEBHOOK =================

@app.post(WEBHOOK_PATH)
async def webhook(req: Request):
    upd = types.Update.model_validate(await req.json(), context={"bot":bot})
    await dp.feed_update(bot, upd)
    return {"ok":True}

@app.on_event("startup")
async def start():
    await bot.set_webhook(WEBHOOK_URL)
