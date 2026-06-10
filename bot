
import asyncio
import logging
import sqlite3
import base64
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    ReplyKeyboardRemove
)
from openai import AsyncOpenAI

# ================= Настройки API =================
BOT_TOKEN = "ТВОЙ_ТЕЛЕГРАМ_ТОКЕН"
OPENAI_API_KEY = "ТВОЙ_OPENAI_API_КЛЮЧ" 

# Инициализация ИИ клиента (по умолчанию используется gpt-4o-mini - она дешевая, быстрая и видит фото)
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= База Данных SQLite =================
def init_db():
    conn = sqlite3.connect("users_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            weight REAL,
            height REAL,
            gender TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_user_profile(user_id, weight, height, gender):
    conn = sqlite3.connect("users_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, weight, height, gender)
        VALUES (?, ?, ?, ?)
    """, (user_id, weight, height, gender))
    conn.commit()
    conn.close()

def get_user_profile(user_id):
    conn = sqlite3.connect("users_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT weight, height, gender FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row

# Инициализируем БД при старте скрипта
init_db()

# ================= Состояния FSM (Диалоги) =================
class ProfileStates(StatesGroup):
    weight = State()
    height = State()
    gender = State()

class FoodStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# ================= Клавиатуры (Reply Keyboard) =================
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📸 Фото еды"), KeyboardButton(text="📝 Внести еду")],
            [KeyboardButton(text="🏠 Меню"), KeyboardButton(text="⬅️ Назад")],
            [KeyboardButton(text="🔄 Заполнить данные заново")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

def get_gender_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Мужской 👦"), KeyboardButton(text="Женский 👧")],
            [KeyboardButton(text="🏠 Меню"), KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

# ================= Обработчики (Handlers) =================

# 1. Стартовая команда
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    profile = get_user_profile(user_id)
    
    if profile:
        weight, height, gender = profile
        await message.answer(
            f"💪 С возвращением! Твои текущие параметры:\n"
            f"Вес: {weight} кг | Рост: {height} см | Пол: {gender}\n\n"
            f"Я готов работать! Пришли мне фото еды или нажми на кнопки ниже.",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "Привет! Я твой персональный AI-ассистент по питанию. "
            "Давай настроим твой профиль для точных расчетов.",
            reply_markup=get_main_keyboard()
        )
        await state.set_state(ProfileStates.weight)
        await message.answer("Шаг 1: Напиши свой актуальный вес (в кг):")


# 2. Кнопка "🏠 Меню"
@dp.message(F.text == "🏠 Меню")
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Ты в главном меню! Выбери удобное действие на кнопках снизу 👇",
        reply_markup=get_main_keyboard()
    )


# 3. Кнопка "🔄 Заполнить данные заново"
@dp.message(F.text == "🔄 Заполнить данные заново")
async def cmd_reset(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ProfileStates.weight)
    await message.answer(
        "Окей, обновляем твои параметры. Шаг 1: Введи свой актуальный вес (в кг):",
        reply_markup=get_main_keyboard()
    )


# 4. Умная кнопка "⬅️ Назад"
@dp.message(F.text == "⬅️ Назад")
async def cmd_back(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state is None:
        await message.answer("Ты уже в главном меню.", reply_markup=get_main_keyboard())
        return
        
    # Откаты по цепочке профиля
    if current_state == ProfileStates.height:
        await state.set_state(ProfileStates.weight)
        await message.answer("Возвращаемся назад. Шаг 1: Напиши свой вес (в кг):")
    elif current_state == ProfileStates.gender:
        await state.set_state(ProfileStates.height)
        await message.answer("Возвращаемся назад. Шаг 2: Напиши свой рост (в см):")
    else:
        # Если находимся в любом другом месте (например, ввод еды), возвращаем в меню
        await state.clear()
        await message.answer("Действие отменено. Возвращаю тебя в меню.", reply_markup=get_main_keyboard())


# ================= Опрос профиля (FSM) =================

@dp.message(ProfileStates.weight)
async def process_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", "."))
        await state.update_data(weight=weight)
        await state.set_state(ProfileStates.height)
        await message.answer("Шаг 2: Напиши свой рост (в см):", reply_markup=get_main_keyboard())
    except ValueError:
        await message.answer("Пожалуйста, введи вес числом (например: 75.5):")

@dp.message(ProfileStates.height)
async def process_height(message: Message, state: FSMContext):
    try:
        height = float(message.text.replace(",", "."))
        await state.update_data(height=height)
        await state.set_state(ProfileStates.gender)
        await message.answer("Шаг 3: Выбери свой пол:", reply_markup=get_gender_keyboard())
    except ValueError:
        await message.answer("Пожалуйста, введи рост числом (например: 180):")

@dp.message(ProfileStates.gender, F.text.in_(["Мужской 👦", "Женский 👧"]))
async def process_gender(message: Message, state: FSMContext):
    gender = "Мужской" if "Мужской" in message.text else "Женский"
    data = await state.get_data()
    
    # Сохраняем в БД
    save_user_profile(message.from_user.id, data['weight'], data['height'], gender)
    await state.clear()
    
    await message.answer(
        "🎉 Профиль успешно настроен! Данные сохранены.\n\n"
        "Теперь ты можешь пользоваться ботом без каких-либо лимитов.",
        reply_markup=get_main_keyboard()
    )


# ================= Подсчет калорий через ИИ (Текст и Фото) =================

# Обработчик кнопки "📝 Внести еду"
@dp.message(F.text == "📝 Внести еду")
async def request_food_text(message: Message, state: FSMContext):
    await state.set_state(FoodStates.waiting_for_text)
    await message.answer("Напиши, что ты съел(а) и примерный вес (например: 'курица гриль 150г, рис 100г, огурец'):")

# Логика обработки текстовой еды
@dp.message(FoodStates.waiting_for_text)
async def process_food_text(message: Message, state: FSMContext):
    food_desc = message.text
    await message.answer("⏳ Анализирую состав еды...", reply_markup=get_main_keyboard())
    await state.clear()

    try:
        # Безлимитный запрос к OpenAI (GPT-4o-mini)
        response = await ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты профессиональный диетолог. Рассчитай примерный КБЖУ для указанной еды. Отвечай на русском языке, делай ответ понятным, структурированным и дружелюбным."},
                {"role": "user", "content": food_desc}
            ]
        )
        answer = response.choices[0].message.content
        await message.answer(answer, reply_markup=get_main_keyboard())
    except Exception as e:
        logging.error(f"Ошибка ИИ: {e}")
        await message.answer("Извини, произошла ошибка при общении с ИИ. Попробуй позже.", reply_markup=get_main_keyboard())


# Обработчик кнопки "📸 Фото еды"
@dp.message(F.text == "📸 Фото еды")
async def request_food_photo(message: Message, state: FSMContext):
    await state.set_state(FoodStates.waiting_for_photo)
    await message.answer("Отправь мне фото своего блюда (лучше всего, чтобы еда была хорошо видна при хорошем освещении):")

# Логика обработки фото (ИИ VISION)
@dp.message(FoodStates.waiting_for_photo, F.photo)
@dp.message(F.photo) # Также ловит фото, отправленные просто так, без нажатия кнопки
async def process_food_photo(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⏳ Скачиваю фото и сканирую блюдо...", reply_markup=get_main_keyboard())
    
    try:
        # Скачиваем файл из телеграма напрямую в память
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        
        # Кодируем в base64 для передачи в Vision API
        base64_image = base64.b64encode(photo_bytes.read()).decode('utf-8')
        
        # Запрос к GPT-4o-mini Vision
        response = await ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": "Ты — опытный нутрициолог. Проанализируй это фото еды. "
                                    "Определи блюда, оцени порцию, ингредиенты и распиши примерный КБЖУ "
                                    "(Калории, Белки, Жиры, Углеводы). Пиши на русском языке, дружелюбно, аккуратными пунктами. "
                                    "В конце обязательно добавь дисклеймер о том, что подсчет по фото приблизительный."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=600
        )
        
        answer = response.choices[0].message.content
        await message.answer(answer, reply_markup=get_main_keyboard())
        
    except Exception as e:
        logging.error(f"Ошибка Vision API: {e}")
        await message.answer(
            "⚠️ Не удалось проанализировать фото. Убедись, что на фото действительно еда и API ключ настроен правильно.",
            reply_markup=get_main_keyboard()
        )


# Обработчик любого другого текста, если состояние не активно
@dp.message()
async def default_handler(message: Message):
    await message.answer(
        "Я не совсем понял твой запрос. Нажми одну из кнопок ниже или пришли фото еды!",
        reply_markup=get_main_keyboard()
    )

# ================= Запуск бота =================
async def main():
    print("Бот запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
