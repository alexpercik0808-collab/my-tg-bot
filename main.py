import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from groq import Groq
from flask import Flask, request

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

ADMIN_ID = 5405313198
CHANNEL_ID = -1002407007220

SUPPORT_USERNAME = "Gaeid12"  # без @

# --- ИНИЦИАЛИЗАЦИЯ ---
client = Groq(api_key=GROQ_API_KEY)
app = Flask(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Хранилище данных пользователей
user_data = {}

# --- ИИ ---
def improve_text(user_input: str) -> str:
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — лаконичный менеджер барахолки. "
                        "Пиши кратко, по делу, без воды. "
                        "Структура: Название, Состояние, Описание (2 фразы). "
                        "Не придумывай факты."
                    )
                },
                {
                    "role": "user",
                    "content": f"Сделай краткое объявление: {user_input}"
                }
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"Ошибка ИИ: {e}"

# --- START ---
@dp.message(Command("start"))
async def start(message: types.Message):
    kb =
    ]
    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)

    await message.answer(
        "👋 <b>Здарова!</b>\n"
        "Пришли описание товара.\n\n"
        "Если есть вопросы — жми «Поддержка»."
    )

# --- ТЕКСТ ---
@dp.message(F.text & ~F.command)
async def handle_text(message: types.Message):
    uid = message.from_user.id

    # Ручное редактирование текста
    if uid in user_data and user_data[uid].get("step") == "wait_manual_text":
        user_data[uid]["improved"] = message.text
        user_data[uid]["step"] = "wait_price"
        await message.answer("💰 Теперь укажи <b>цену</b> товара.")
        return

    # Ожидание цены
    if uid in user_data and user_data[uid].get("step") == "wait_price":
        user_data[uid]["price"] = message.text
        user_data[uid]["step"] = "wait_photo"
        await message.answer("📸 Теперь отправь <b>фото</b> товара.")
        return

    # Новый товар
    user_data[uid] = {
        "username": message.from_user.username,
        "step": "wait_confirm"
    }

    wait_msg = await message.answer("🤖 ИИ думает...")
    new_text = improve_text(message.text)
    user_data[uid]["improved"] = new_text

    kb =
    ]

    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)

    await wait_msg.edit_text(
        f"✨ <b>Вариант ИИ:</b>\n\n{new_text}",
        reply_markup=markup
    )

# --- КНОПКИ ---
@dp.callback_query(F.data == "accept_text")
async def accept_text(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_data[uid]["step"] = "wait_price"
    await callback.message.edit_text(
        callback.message.text + "\n\n💰 <b>Укажи цену:</b>"
    )
    await callback.answer()

@dp.callback_query(F.data == "edit_manual")
async def edit_manual_text(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_data[uid]["step"] = "wait_manual_text"
    await callback.message.edit_text(
        "✍️ Введи свой вариант текста следующим сообщением."
    )
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
        f"💰 <b>Цена:</b> {data['price']}\n"
        f"👤 <b>Продавец:</b> {username}"
    )

    kb =
    ]

    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)

    await bot.send_photo(
        ADMIN_ID,
        photo=data["photo"],
        caption=f"📥 <b>Новое объявление:</b>\n\n{caption}",
        reply_markup=markup
    )

    # Сообщение пользователю + поддержка
    kb_user =
    ]
    markup_user = types.InlineKeyboardMarkup(inline_keyboard=kb_user)

    await message.answer(
        "⌛ Отправлено админу на проверку!\n"
        "Если нужно что-то уточнить — напиши в поддержку."
    )

# --- ПУБЛИКАЦИЯ ---
@dp.callback_query(F.data.startswith("pub_"))
async def publish_ad(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    data = user_data[user_id]

    username = f"@{data['username']}" if data['username'] else "Контакт скрыт"

    caption = (
        f"{data['improved']}\n\n"
        f"💰 <b>Цена:</b> {data['price']}\n"
        f"👤 <b>Продавец:</b> {username}"
    )

    await bot.send_photo(CHANNEL_ID, photo=data["photo"], caption=caption)
    await bot.send_message(user_id, "✅ Твоё объявление опубликовано!")
    await callback.message.edit_caption(
        callback.message.caption + "\n\n✅ <b>ОПУБЛИКОВАНО</b>"
    )
    await callback.answer()

# --- ОТКЛОНЕНИЕ ---
@dp.callback_query(F.data.startswith("decl_"))
async def decline_ad(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    await bot.send_message(user_id, "❌ Админ отклонил объявление.")
    await callback.message.delete()
    await callback.answer()

# --- ВЕБХУКИ ---
@app.route('/', methods=['POST'])
async def telegram_webhook():
    update = types.Update.model_validate_json(request.data)
    await dp.feed_update(bot, update)
    return 'ok'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
