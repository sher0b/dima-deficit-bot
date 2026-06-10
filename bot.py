
import asyncio
import logging
import sqlite3
import base64
from io import BytesIO # Используется для in-memory работы с файлами

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
from openai import AsyncOpenAI # Используем библиотеку OpenAI, так как Groq совместим с ее API

# ================================ КОНФИГУРАЦИЯ =================================
# !!! ОБЯЗАТЕЛЬНО ЗАМЕНИ ЭТИ ЗНАЧЕНИЯ НА СВОИ !!!

TELEGRAM_TOKEN = "ТВОЙ_ТЕЛЕГРАМ_ТОКЕН" # Токен твоего бота от BotFather
GROQ_API_KEY = "ТВОЙ_GROQ_API_КЛЮЧ"  # Ключ API от Groq (https://console.groq.com/keys)
                                     # Если хочешь использовать OpenAI, замени на OPENAI_API_KEY
                                     # и в ai_client убери base_url

ADMIN_ID = 123456789 # Твой Telegram ID (число) для админских уведомлений или функций
CHANNEL_ID = "@твой_канал" # ID твоего канала (начинается с @)
CHANNEL_URL = "https://t.me/твой_канал" # Полная ссылка на твой канал

# ============================ Инициализация ИИ (Groq) ============================
# Используем модель llama-3.2-11b-vision-preview от Groq, она быстрая и хорошо справляется с фото.
# Groq API совместим с OpenAI библиотекой, просто указываем base_url.
ai_client = AsyncOpenAI(
    api_key=GROQ_API_KEY, 
    base_url="https://api.groq.com/openai/v1" # Важно для Groq
)

# ========================== Настройка Логирования и Бота ==========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML") # parse_mode="HTML" позволяет использовать HTML-разметку в ответах
dp = Dispatcher()

# ================================ База Данных SQLite ===============================
def init_db():
    """Инициализирует базу данных и создает таблицу users, если ее нет."""
    conn = sqlite3.connect("bot_data.db")
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
    """Сохраняет или обновляет профиль пользователя в БД."""
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, weight, height, gender)
        VALUES (?, ?, ?, ?)
    """, (user_id, weight, height, gender))
    conn.commit()
    conn.close()
    logging.info(f"Профиль пользователя {user_id} сохранен/обновлен.")

def get_user_profile(user_id):
    """Возвращает профиль пользователя из БД."""
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT weight, height, gender FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row

# Инициализируем БД при старте скрипта
init_db()

# ================================ Состояния FSM (Диалоги) ================================
class ProfileStates(StatesGroup):
    """Состояния для сбора данных профиля пользователя."""
    weight = State()
    height = State()
    gender = State()

class FoodStates(StatesGroup):
    """Состояния для запросов о еде."""
    waiting_for_text = State()
    waiting_for_photo = State()

# ================================ Клавиатуры (Reply Keyboard) ================================
def get_main_keyboard():
    """Возвращает основную клавиатуру бота."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📸 Фото еды"), KeyboardButton(text="📝 Внести еду")],
            [KeyboardButton(text="🏠 Меню"), KeyboardButton(text="⬅️ Назад")],
            [KeyboardButton(text="🔄 Заполнить данные заново")]
        ],
        resize_keyboard=True,  # Делает кнопки аккуратными по размеру экрана
        is_persistent=True     # Кнопки не будут скрываться после нажатия
    )

def get_gender_keyboard():
    """Возвращает клавиатуру для выбора пола."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Мужской 👦"), KeyboardButton(text="Женский 👧")],
            [KeyboardButton(text="🏠 Меню"), KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True, is_persistent=True
    )

# =================================== ОБРАБОТЧИКИ (HANDLERS) ===================================

# --- 1. Обработчик команды /start ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear() # На старте всегда сбрасываем состояние
    user_id = message.from_user.id
    profile = get_user_profile(user_id)
    
    if profile:
        weight, height, gender = profile
        await message.answer(
            f"💪 С возвращением, <b>{message.from_user.first_name}</b>! Твои текущие параметры:\n"
            f"⚖️ <b>Вес:</b> {weight} кг\n"
            f"📏 <b>Рост:</b> {height} см\n"
            f"🚻 <b>Пол:</b> {gender}\n\n"
            f"Я готов работать! Пришли мне фото еды или нажми на кнопки ниже. "
            f"Не забудь подписаться на наш канал: {CHANNEL_URL}",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            f"Привет! Я твой персональный AI-ассистент по питанию. "
            f"Давай настроим твой профиль для более точных расчетов.\n"
            f"Не забудь подписаться на наш канал: {CHANNEL_URL}",
            reply_markup=get_main_keyboard()
        )
        await state.set_state(ProfileStates.weight)
        await message.answer("<b>Шаг 1:</b> Напиши свой актуальный вес (в кг):")


# --- 2. Обработчик кнопки "🏠 Меню" ---
@dp.message(F.text == "🏠 Меню")
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear() # Всегда сбрасываем состояние при переходе в меню
    await message.answer(
        "Ты в главном меню! Выбери удобное действие на кнопках снизу 👇",
        reply_markup=get_main_keyboard()
    )


# --- 3. Обработчик кнопки "🔄 Заполнить данные заново" ---
@dp.message(F.text == "🔄 Заполнить данные заново")
async def cmd_reset_profile(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ProfileStates.weight)
    await message.answer(
        "Окей, обновляем твои параметры. <b>Шаг 1:</b> Введи свой актуальный вес (в кг):",
        reply_markup=get_main_keyboard()
    )


# --- 4. Умная кнопка "⬅️ Назад" ---
@dp.message(F.text == "⬅️ Назад")
async def cmd_back(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state is None:
        await message.answer("Ты уже в главном меню.", reply_markup=get_main_keyboard())
        return
        
    # Логика отката по цепочке состояний профиля
    if current_state == ProfileStates.height:
        await state.set_state(ProfileStates.weight)
        await message.answer("Возвращаемся назад. <b>Шаг 1:</b> Напиши свой вес (в кг):", reply_markup=get_main_keyboard())
    elif current_state == ProfileStates.gender:
        await state.set_state(ProfileStates.height)
        await message.answer("Возвращаемся назад. <b>Шаг 2:</b> Напиши свой рост (в см):", reply_markup=get_main_keyboard())
    elif current_state == ProfileStates.weight: # Если это первый шаг профиля
        await state.clear()
        await message.answer("Действие отменено. Возвращаю тебя в меню.", reply_markup=get_main_keyboard())
    # Откат из состояний ввода еды
    elif current_state in [FoodStates.waiting_for_photo, FoodStates.waiting_for_text]:
        await state.clear()
        await message.answer("Ввод еды отменен. Возвращаю тебя в меню.", reply_markup=get_main_keyboard())
    else:
        # Если находимся в любом другом месте или откатывать некуда, возвращаем в меню
        await state.clear()
        await message.answer("Возвращаю тебя в главное меню.", reply_markup=get_main_keyboard())


# ================================ Опрос профиля (FSM) ================================

@dp.message(ProfileStates.weight)
async def process_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", ".")) # Поддерживаем ввод через запятую
        await state.update_data(weight=weight)
        await state.set_state(ProfileStates.height)
        await message.answer("<b>Шаг 2:</b> Напиши свой рост (в см):", reply_markup=get_main_keyboard())
    except ValueError:
        await message.answer("Пожалуйста, введи вес числом (например: 75.5):", reply_markup=get_main_keyboard())

@dp.message(ProfileStates.height)
async def process_height(message: Message, state: FSMContext):
    try:
        height = float(message.text.replace(",", ".")) # Поддерживаем ввод через запятую
        await state.update_data(height=height)
        await state.set_state(ProfileStates.gender)
        await message.answer("<b>Шаг 3:</b> Выбери свой пол:", reply_markup=get_gender_keyboard())
    except ValueError:
        await message.answer("Пожалуйста, введи рост числом (например: 180):", reply_markup=get_main_keyboard())

@dp.message(ProfileStates.gender, F.text.in_(["Мужской 👦", "Женский 👧"]))
async def process_gender(message: Message, state: FSMContext):
    gender = "Мужской" if "Мужской" in message.text else "Женский"
    data = await state.get_data()
    
    # Сохраняем данные профиля в БД
    save_user_profile(message.from_user.id, data['weight'], data['height'], gender)
    await state.clear()
    
    await message.answer(
        "🎉 Профиль успешно настроен! Данные сохранены.\n"
        "Теперь ты можешь пользоваться ботом без каких-либо лимитов.",
        reply_markup=get_main_keyboard()
    )

@dp.message(ProfileStates.gender) # Если ввел что-то кроме кнопок пола
async def process_gender_invalid(message: Message):
    await message.answer("Пожалуйста, выбери пол, используя кнопки.", reply_markup=get_gender_keyboard())


# ====================== Подсчет калорий через ИИ (Текст и Фото) ======================

# --- Обработчик кнопки "📝 Внести еду" ---
@dp.message(F.text == "📝 Внести еду")
async def request_food_text(message: Message, state: FSMContext):
    await state.set_state(FoodStates.waiting_for_text)
    await message.answer(
        "Напиши, что ты съел(а) и примерный вес (например: 'курица гриль 150г, рис 100г, огурец 50г'). "
        "Чем подробнее, тем точнее будет расчет.",
        reply_markup=get_main_keyboard()
    )

# --- Логика обработки текстового запроса (ИИ) ---
@dp.message(FoodStates.waiting_for_text)
async def process_food_text(message: Message, state: FSMContext):
    food_desc = message.text
    await message.answer("⏳ Анализирую состав еды...", reply_markup=get_main_keyboard())
    await state.clear() # Сбрасываем состояние после получения текста

    try:
        # Безлимитный запрос к Vision модели Groq (аналог GPT-4o-mini Vision)
        response = await ai_client.chat.completions.create(
            model="llama-3.2-11b-vision-preview", # Модель от Groq для Vision
            messages=[
                {"role": "system", "content": 
                 "Ты профессиональный диетолог и нутрициолог. Твоя задача — "
                 "максимально точно оценить КБЖУ (Калории, Белки, Жиры, Углеводы) для указанной еды. "
                 "Отвечай на русском языке, делай ответ понятным, структурированным и дружелюбным. "
                 "В конце ОБЯЗАТЕЛЬНО добавь дисклеймер, что это ИИ-оценка и может быть неточной."},
                {"role": "user", "content": food_desc}
            ],
            max_tokens=800 # Увеличил лимит токенов для более развернутого ответа
        )
        answer = response.choices[0].message.content
        await message.answer(answer, reply_markup=get_main_keyboard())
    except Exception as e:
        logging.error(f"Ошибка ИИ при текстовом запросе: {e}")
        await message.answer(
            "Извини, произошла ошибка при анализе твоей еды. Попробуй описать еду по-другому или позже.",
            reply_markup=get_main_keyboard()
        )


# --- Обработчик кнопки "📸 Фото еды" ---
@dp.message(F.text == "📸 Фото еды")
async def request_food_photo(message: Message, state: FSMContext):
    await state.set_state(FoodStates.waiting_for_photo)
    await message.answer(
        "Отправь мне фото своего блюда. Для лучшего результата: "
        "хорошее освещение, еда полностью в кадре, без лишних предметов.",
        reply_markup=get_main_keyboard()
    )

# --- Логика обработки фото (ИИ VISION) ---
# Ловит фото, если бот ждал фото (FoodStates.waiting_for_photo) ИЛИ если фото прислали просто так.
@dp.message(FoodStates.waiting_for_photo, F.photo)
@dp.message(F.photo & ~F.text) # Ловит любое фото, если нет активного состояния FSM (чтобы сразу обрабатывать)
async def process_food_photo(message: Message, state: FSMContext):
    await state.clear() # Сбрасываем состояние после получения фото
    await message.answer("⏳ Скачиваю фото и сканирую блюдо...", reply_markup=get_main_keyboard())
    
    try:
        # Скачиваем файл из телеграма напрямую в память
        photo = message.photo[-1] # Берем фото наилучшего качества
        file_info = await bot.get_file(photo.file_id)
        
        # Скачиваем файл в BytesIO (чтобы не сохранять на диск)
        photo_buffer = BytesIO()
        await bot.download_file(file_info.file_path, destination=photo_buffer)
        
        # Кодируем в base64 для передачи в Vision API
        base64_image = base64.b64encode(photo_buffer.getvalue()).decode('utf-8')
        
        # Запрос к Vision модели Groq
        response = await ai_client.chat.completions.create(
            model="llama-3.2-11b-vision-preview", # Модель от Groq для Vision
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": "Ты — опытный нутрициолог. Проанализируй это фото еды. "
                                    "Определи блюда, оцени порцию, ингредиенты и распиши примерный КБЖУ "
                                    "(Калории, Белки, Жиры, Углеводы). Пиши на русском языке, дружелюбно, аккуратными пунктами. "
                                    "В конце ОБЯЗАТЕЛЬНО добавь дисклеймер: '⚠️ <b>Важно:</b> Подсчет по фото является приблизительным и может отличаться от фактического на 15-20%. Используйте его как ориентир, а не точное значение.'"
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
            max_tokens=800 # Увеличил лимит токенов для более детального ответа
        )
        
        answer = response.choices[0].message.content
        await message.answer(answer, reply_markup=get_main_keyboard())
        
    except Exception as e:
        logging.error(f"Ошибка Vision API при обработке фото для пользователя {message.from_user.id}: {e}")
        await message.answer(
            "⚠️ Не удалось проанализировать фото. Убедитесь, что на фото действительно еда и API ключ настроен правильно. Попробуйте снова или опишите еду текстом.",
            reply_markup=get_main_keyboard()
        )


# --- Обработчик любого другого текста, если никакое состояние не активно ---
@dp.message()
async def default_text_handler(message: Message):
    await message.answer(
        "Я не совсем понял твой запрос. Нажми одну из кнопок ниже или пришли фото еды!",
        reply_markup=get_main_keyboard()
    )


# =================================== Запуск Бота ===================================
async def main():
    logging.info("Бот запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
