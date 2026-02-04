import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from groq import Groq
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

ADMIN_ID = 5405313198
CHANNEL_ID = -1002407007220
SUPPORT_USERNAME = "Gaeid12"

client = Groq(api_key=GROQ_API_KEY)
app = Flask(__name__)

bot = Bot(token=BOT_TOKEN,
          default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

user_data = {}

# --- ИИ ---
def improve_text(user_input: str) -> str:
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system",
                 "content": "Ты — лаконичный менеджер барахолки. Пиши кратко. Структура: Название, Состояние, Описание (2 фразы)."},
                {"role": "user",
                 "content": f"Сделай краткое объявление: {user_input}"}
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"Ошибка ИИ: {e}"

# --- START ---
@dp.message(Command("start"))
async def start(message: types.Message):
    kb = [[types.InlineKeyboardButton(
        text="🛠 Поддержка",
        url=f"https://t.me/{SUPPORT_USERNAME}"
    )]]

    await message.answer(
        "👋 <b>Здарова!</b>\nПришли описание товара.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb)
    )

# --- ТЕКСТ ---
@dp.message(F.text & ~F.command)
async def handle_text(message: types.Message):
    uid = message.from_user.id

    if uid in user_data and user_data[uid].get("step") == "wait_manual_text":
        user_data[uid]["improved"] = message.text
        user_data[uid]["step"] = "wait_price"
        await message.answer("💰 Теперь укажи цену.")
        return

    if uid in user_data and user_data[uid].get("step") == "wait_price":
        user_data[uid]["price"] = message.text
        user_data[uid]["step"] = "wait_photo"
        await message.answer("📸 Отправь фото.")
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

# --- ФОТО ---
@dp.message(F.photo)
async def get_photo(message: types.Message):
    uid = message.from_user.id
    if uid not in user_data or user_data[uid].get("step") != "wait_photo":
        return

    user_data[uid]["photo"] = message.photo[-1].file_id
    data = user_data[uid]

    username = f"@{data['username']}" if data['username'] else "Контакт скрыт"

    caption = (
        f"{data['improved']}\n\n"
        f"💰 Цена: {data['price']}\n"
        f"👤 Продавец: {username}"
    )

    kb = [[
        types.InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub_{uid}"),
        types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decl_{uid}")
    ]]

    await bot.send_photo(
        ADMIN_ID,
        photo=data["photo"],
        caption=caption,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb)
    )

    await message.answer("⌛ Отправлено админу.")

# --- ПУБЛИКАЦИЯ ---
@dp.callback_query(F.data.startswith("pub_"))
async def publish(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    data = user_data[user_id]

    username = f"@{data['username']}" if data['username'] else "Контакт скрыт"

    caption = (
        f"{data['improved']}\n\n"
        f"💰 Цена: {data['price']}\n"
        f"👤 Продавец: {username}"
    )

    await bot.send_photo(CHANNEL_ID, photo=data["photo"], caption=caption)
    await bot.send_message(user_id, "✅ Опубликовано!")
    await callback.answer()

@dp.callback_query(F.data.startswith("decl_"))
async def decline(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    await bot.send_message(user_id, "❌ Отклонено.")
    await callback.answer()

# --- ВЕБХУК ---
@app.route('/', methods=['POST'])
async def webhook():
    update = types.Update.model_validate_json(request.data)
    asyncio.get_event_loop().create_task(dp.feed_update(bot, update))
    return "ok"

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


    
