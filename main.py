import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, InputMediaPhoto
)
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from openai import AsyncOpenAI

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())
openai_client = AsyncOpenAI(api_key=OPENAI_KEY)

# =============================

pending_ads = {}
user_active_ad = {}

# =============================

class AdForm(StatesGroup):
    title = State()
    description = State()
    price = State()
    address = State()
    photos = State()
    confirm = State()

# =============================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Подать объявление")],
            [KeyboardButton(text="🆘 Поддержка")]
        ],
        resize_keyboard=True
    )

def support_menu():
    return "По вопросам рекламы и сотрудничества пишите администратору."

# =============================

def format_description(text: str):
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join([f"• {line}" for line in lines])

# =============================

async def ai_format(text: str):
    if not OPENAI_KEY:
        return format_description(text)

    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content":
                "Ты — технический редактор. Твоя задача оформить текст пользователя в красивый список. "
                "Не удаляй характеристики. Не сокращай состояние, материал и прочее. "
                "Выпиши всё через буллиты '•'. Не выдумывай лишнего."
            },
            {
                "role": "user",
                "content": text
            }
        ]
    )
    return response.choices[0].message.content.strip()

# =============================

@dp.message(F.text == "/start")
async def start(message: Message):
    await message.answer("Добро пожаловать!", reply_markup=main_menu())

# =============================

@dp.message(F.text == "🆘 Поддержка")
async def support(message: Message):
    await message.answer(support_menu())

# =============================

@dp.message(F.text == "📤 Подать объявление")
async def new_ad(message: Message, state: FSMContext):
    if user_active_ad.get(message.from_user.id):
        await message.answer("У вас уже есть объявление на модерации.")
        return

    await state.set_state(AdForm.title)
    await message.answer("Введите название товара (например: Samsung A32)")

# =============================

@dp.message(AdForm.title)
async def get_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(AdForm.description)
    await message.answer("Введите описание товара:")

# =============================

@dp.message(AdForm.description)
async def get_description(message: Message, state: FSMContext):
    formatted = await ai_format(message.text)
    await state.update_data(description=formatted)
    await state.set_state(AdForm.price)
    await message.answer("Введите цену:")

# =============================

@dp.message(AdForm.price)
async def get_price(message: Message, state: FSMContext):
    await state.update_data(price=message.text)
    await state.set_state(AdForm.address)
    await message.answer("Введите адрес:")

# =============================

@dp.message(AdForm.address)
async def get_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text, photos=[])
    await state.set_state(AdForm.photos)
    await message.answer("Отправьте фото (можно несколько). После — напишите /done")

# =============================

@dp.message(AdForm.photos, F.photo)
async def add_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)

# =============================

@dp.message(AdForm.photos, F.text == "/done")
async def finish_photos(message: Message, state: FSMContext):
    data = await state.get_data()

    if not data.get("photos"):
        await message.answer("Вы не отправили фото.")
        return

    ad_id = len(pending_ads) + 1
    pending_ads[ad_id] = {
        **data,
        "user_id": message.from_user.id,
        "status": "pending"
    }

    user_active_ad[message.from_user.id] = ad_id

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"approve_{ad_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{ad_id}")
        ]
    ])

    await bot.send_message(
        ADMIN_ID,
        f"Новое объявление #{ad_id}",
        reply_markup=kb
    )

    await message.answer("Объявление отправлено на модерацию.", reply_markup=main_menu())
    await state.clear()

# =============================

def build_caption(ad):
    return (
        f"<u>{ad['title']}</u>\n\n"
        f"{ad['description']}\n\n"
        f"<u>💰 Цена:</u> {ad['price']}\n"
        f"<u>📍 Адрес:</u> {ad['address']}"
    )

# =============================

@dp.callback_query(F.data.startswith("approve_"))
async def approve(callback: CallbackQuery):
    ad_id = int(callback.data.split("_")[1])
    ad = pending_ads.get(ad_id)

    if not ad or ad["status"] != "pending":
        await callback.answer("Уже обработано")
        return

    ad["status"] = "approved"

    media = [
        InputMediaPhoto(media=photo)
        for photo in ad["photos"]
    ]

    await bot.send_media_group(CHANNEL_ID, media)

    seller = ad["user_id"]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✉️ Написать продавцу",
                url=f"tg://user?id={seller}"
            )
        ]
    ])

    await bot.send_message(
        CHANNEL_ID,
        build_caption(ad),
        reply_markup=kb
    )

    await bot.send_message(
        seller,
        "✅ Ваше объявление опубликовано!",
        reply_markup=main_menu()
    )

    await bot.send_message(
        ADMIN_ID,
        f"Объявление #{ad_id} опубликовано."
    )

    user_active_ad.pop(seller, None)

    await callback.answer("Опубликовано")

# =============================

@dp.callback_query(F.data.startswith("reject_"))
async def reject(callback: CallbackQuery):
    ad_id = int(callback.data.split("_")[1])
    ad = pending_ads.get(ad_id)

    if not ad or ad["status"] != "pending":
        await callback.answer("Уже обработано")
        return

    ad["status"] = "rejected"

    seller = ad["user_id"]

    await bot.send_message(
        seller,
        "❌ Ваше объявление отклонено.",
        reply_markup=main_menu()
    )

    await bot.send_message(
        ADMIN_ID,
        f"Объявление #{ad_id} отклонено."
    )

    user_active_ad.pop(seller, None)

    await callback.answer("Отклонено")

# =============================

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
