
import logging
import sqlite3
import base64
from io import BytesIO

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from openai import AsyncOpenAI

# ================================ КОНФИГУРАЦИЯ =================================
# !!! ОБЯЗАТЕЛЬНО ЗАМЕНИ ЭТИ ЗНАЧЕНИЯ НА СВОИ !!!

TELEGRAM_TOKEN = "ТВОЙ_ТЕЛЕГРАМ_ТОКЕН"   # Токен твоего бота от BotFather
GROQ_API_KEY = "ТВОЙ_GROQ_API_КЛЮЧ"      # Ключ API от Groq (https://console.groq.com/keys)

ADMIN_ID = 123456789                     # Твой Telegram ID (число)
CHANNEL_ID = "@твой_канал"               # ID твоего канала (начинается с @)
CHANNEL_URL = "https://t.me/твой_канал"  # Полная ссылка на твой канал

# ============================ Инициализация ИИ (Groq) ============================
# Используем модель llama-3.2-11b-vision-preview от Groq (быстрая, бесплатная и видит фото)
ai_client = AsyncOpenAI(
    api_key=GROQ_API_KEY, 
    base_url="https://api.groq.com/openai/v1"
)

# ===================== Настройка Логирования, Бота и FSM =====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
storage = MemoryStorage() # Хранилище состояний в оперативной памяти (нужно для aiogram 2.x)
dp = Dispatcher(bot, storage=storage)

# ================================ База Данных SQLite ===============================
def init_db():
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
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, weight, height, gender)
        VALUES (?, ?, ?, ?)
    """, (user_id, weight, height, gender))
    conn.commit()
    conn.close()
    logging.info(f"Профиль пользователя {user_id} обновлен.")

def get_user_profile(user_id):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT weight, height, gender FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row

init_db()

# ================================ Состояния FSM (Диалоги) ================================
class ProfileStates(StatesGroup):
    weight = State()
    height = State()
    gender = State()

class FoodStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# ================================ Клавиатуры (Reply Keyboard) ================================
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton(text="📸 Фото еды"), types.KeyboardButton(text="📝 Внести еду"))
    keyboard.add(types.KeyboardButton(text="🏠 Меню"), types.KeyboardButton(text="⬅️ Назад"))
    keyboard.add(types.KeyboardButton(text="🔄 Заполнить данные заново"))
    return keyboard

def get_gender_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton(text="Мужской 👦"), types.KeyboardButton(text="Женский 👧"))
    keyboard.add(types.KeyboardButton(text="🏠 Меню"), types.KeyboardButton(text="⬅️ Назад"))
    return keyboard

# =================================== ОБРАБОТЧИКИ (HANDLERS) ===================================

# --- 1. Обработчик команды /start ---
@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish() # Сбрасываем любые состояния при старте
    user_id = message.from_user.id
    profile = get_user_profile(user_id)
    
    if profile:
        weight, height, gender = profile
        await message.answer(
            f"💪 С возвращением, <b>{message.from_user.first_name}</b>! Твои текущие параметры:\n"
            f"⚖️ <b>Вес:</b> {weight} кг\n"
            f"📏 <b>Рост:</b> {height} см\n"
            f"🚻 <b>Пол:</b> {gender}\n\n"
            f"Я готов работать! Пришли мне фото еды или используй кнопки ниже. "
            f"Подписывайся на наш канал: {CHANNEL_URL}",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            f"Привет! Я твой персональный AI-ассистент по питанию. "
            f"Давай настроим твой профиль для более точных расчетов.\n"
            f"Подписывайся на наш канал: {CHANNEL_URL}",
            reply_markup=get_main_keyboard()
        )
        await ProfileStates.weight.set()
        await message.answer("<b>Шаг 1:</b> Напиши свой актуальный вес (в кг):")


# --- 2. Обработчик кнопки "🏠 Меню" ---
@dp.message_handler(lambda message: message.text == "🏠 Меню", state='*')
async def cmd_menu(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(
        "Ты в главном меню! Выбери удобное действие на кнопках снизу 👇",
        reply_markup=get_main_keyboard()
    )


# --- 3. Обработчик кнопки "🔄 Заполнить данные заново" ---
@dp.message_handler(lambda message: message.text == "🔄 Заполнить данные заново", state='*')
async def cmd_reset_profile(message: types.Message, state: FSMContext):
    await state.finish()
    await ProfileStates.weight.set()
    await message.answer(
        "Окей, обновляем твои параметры. <b>Шаг 1:</b> Введи свой актуальный вес (в кг):",
        reply_markup=get_main_keyboard()
    )


# --- 4. Умная кнопка "⬅️ Назад" ---
@dp.message_handler(lambda message: message.text == "⬅️ Назад", state='*')
async def cmd_back(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state is None:
        await message.answer("Ты уже в главном меню.", reply_markup=get_main_keyboard())
        return
        
    # Сравниваем строковые значения состояний в aiogram 2.x
    if current_state == ProfileStates.height.state:
        await ProfileStates.weight.set()
        await message.answer("Возвращаемся назад. <b>Шаг 1:</b> Напиши свой вес (в кг):", reply_markup=get_main_keyboard())
    elif current_state == ProfileStates.gender.state:
        await ProfileStates.height.set()
        await message.answer("Возвращаемся назад. <b>Шаг 2:</b> Напиши свой рост (в см):", reply_markup=get_main_keyboard())
    else:
        await state.finish()
        await message.answer("Действие отменено. Возвращаю тебя в меню.", reply_markup=get_main_keyboard())


# ================================ Опрос профиля (FSM) ================================

@dp.message_handler(state=ProfileStates.weight)
async def process_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", "."))
        await state.update_data(weight=weight)
        await ProfileStates.height.set()
        await message.answer("<b>Шаг 2:</b> Напиши свой рост (в см):", reply_markup=get_main_keyboard())
    except ValueError:
        await message.answer("Пожалуйста, введи вес числом (например: 75.5):", reply_markup=get_main_keyboard())


@dp.message_handler(state=ProfileStates.height)
async def process_height(message: types.Message, state: FSMContext):
    try:
        height = float(message.text.replace(",", "."))
        await state.update_data(height=height)
        await ProfileStates.gender.set()
        await message.answer("<b>Шаг 3:</b> Выбери свой пол:", reply_markup=get_gender_keyboard())
    except ValueError:
        await message.answer("Пожалуйста, введи рост числом (например: 180):", reply_markup=get_main_keyboard())


@dp.message_handler(state=ProfileStates.gender)
async def process_gender(message: types.Message, state: FSMContext):
    if message.text in ["Мужской 👦", "Женский 👧"]:
        gender = "Мужской" if "Мужской" in message.text else "Женский"
        data = await state.get_data()
        
        save_user_profile(message.from_user.id, data['weight'], data['height'], gender)
        await state.finish()
        
        await message.answer(
            "🎉 Профиль успешно настроен! Данные сохранены.\n"
            "Теперь ты можешь пользоваться ботом без ограничений.",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer("Пожалуйста, выбери пол, используя кнопки снизу:", reply_markup=get_gender_keyboard())


# ====================== Подсчет калорий через ИИ (Текст и Фото) ======================

# --- Обработчик кнопки "📝 Внести еду" ---
@dp.message_handler(lambda message: message.text == "📝 Внести еду", state='*')
async def request_food_text(message: types.Message, state: FSMContext):
    await state.finish()
    await FoodStates.waiting_for_text.set()
    await message.answer(
        "Напиши, что ты съел(а) и примерный вес (например: 'курица гриль 150г, рис 100г, огурец 50г'):",
        reply_markup=get_main_keyboard()
    )


# --- Логика обработки текстового запроса (ИИ) ---
@dp.message_handler(state=FoodStates.waiting_for_text)
async def process_food_text(message: types.Message, state: FSMContext):
    food_desc = message.text
    await message.answer("⏳ Анализирую состав еды...", reply_markup=get_main_keyboard())
    await state.finish()

    try:
        response = await ai_client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[
                {"role": "system", "content": 
                 "Ты профессиональный диетолог и нутрициолог. Рассчитай примерный КБЖУ для указанной еды. "
                 "Отвечай на русском языке, структурированно. "
                 "В конце добавь примечание, что расчет приблизительный."},
                {"role": "user", "content": food_desc}
            ],
            max_tokens=800
        )
        answer = response.choices[0].message.content
        await message.answer(answer, reply_markup=get_main_keyboard())
    except Exception as e:
        logging.error(f"Ошибка ИИ: {e}")
        await message.answer("Извини, произошла ошибка. Попробуй позже.", reply_markup=get_main_keyboard())


# --- Обработчик кнопки "📸 Фото еды" ---
@dp.message_handler(lambda message: message.text == "📸 Фото еды", state='*')
async def request_food_photo(message: types.Message, state: FSMContext):
    await state.finish()
    await FoodStates.waiting_for_photo.set()
    await message.answer(
        "Отправь мне фото своего блюда:",
        reply_markup=get_main_keyboard()
    )


# --- Логика обработки фото (ИИ VISION) ---
@dp.message_handler(state=FoodStates.waiting_for_photo, content_types=types.ContentType.PHOTO)
@dp.message_handler(content_types=types.ContentType.PHOTO, state='*') # Также обрабатываем фото присланные без команды
async def process_food_photo(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("⏳ Скачиваю фото и сканирую блюдо...", reply_markup=get_main_keyboard())
    
    try:
        # Скачиваем файл в aiogram 2.x
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        
        # Скачиваем файл в BytesIO напрямую
        photo_buffer = await bot.download_file(file_info.file_path)
        base64_image = base64.b64encode(photo_buffer.read()).decode('utf-8')
        
        response = await ai_client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": "Ты — нутрициолог. Проанализируй фото еды. "
                                    "Определи блюда, оцени порцию, ингредиенты и распиши примерный КБЖУ. "
                                    "Отвечай на русском языке. "
                                    "В конце добавь дисклеймер: '⚠️ Важно: Подсчет по фото примерный и может отличаться от фактического на 15-20%.'"
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
            max_tokens=800
        )
        
        answer = response.choices[0].message.content
        await message.answer(answer, reply_markup=get_main_keyboard())
        
    except Exception as e:
        logging.error(f"Ошибка Vision API: {e}")
        await message.answer(
            "⚠️ Не удалось проанализировать фото. Убедись, что на фото еда и API ключ Groq настроен верно.",
            reply_markup=get_main_keyboard()
        )


# --- Обработчик любого нераспознанного текста ---
@dp.message_handler(state='*')
async def default_handler(message: types.Message):
    await message.answer(
        "Я не совсем понял запрос. Нажми одну из кнопок снизу или пришли фото еды!",
        reply_markup=get_main_keyboard()
    )


# ================================= Запуск =================================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
