
import os
import logging
import sqlite3
import base64
import re
import json
from datetime import datetime
from io import BytesIO

# Используем aiohttp для работы с Groq API без внешних библиотек
import aiohttp

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# ================================ КОНФИГУРАЦИЯ =================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "СЮДА_ВСТАВЬ_ТОКЕН_ОТ_BOTFATHER")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "СЮДА_ВСТАВЬ_КЛЮЧ_GROQ")

ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@твой_канал")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/твой_канал")

# Настройки API Groq
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ===================== Настройка Логирования, Бота и FSM =====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

if "СЮДА_ВСТАВЬ" in TELEGRAM_TOKEN or TELEGRAM_TOKEN == "":
    logging.error("КРИТИЧЕСКАЯ ОШИБКА: Замени токен в конфигурации бота!")
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ================================ База Данных SQLite ===============================
def init_db():
    conn = sqlite3.connect("bot_diet_data.db")
    cursor = conn.cursor()
    # Таблица пользователей с целями и рассчитанным БЖУ
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            weight REAL,
            height REAL,
            age INTEGER,
            gender TEXT,
            activity TEXT,
            goal TEXT,
            target_kcal INTEGER,
            target_p INTEGER,
            target_f INTEGER,
            target_c INTEGER
        )
    """)
    # Таблица дневника питания
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            food_name TEXT,
            kcal INTEGER,
            p INTEGER,
            f INTEGER,
            c INTEGER
        )
    """)
    conn.commit()
    conn.close()

def save_user_profile(user_id, weight, height, age, gender, activity, goal, kcal, p, f, c):
    conn = sqlite3.connect("bot_diet_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, weight, height, age, gender, activity, goal, target_kcal, target_p, target_f, target_c)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, weight, height, age, gender, activity, goal, kcal, p, f, c))
    conn.commit()
    conn.close()

def get_user_profile(user_id):
    conn = sqlite3.connect("bot_diet_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT weight, height, age, gender, activity, goal, target_kcal, target_p, target_f, target_c FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def add_food_to_log(user_id, food_name, kcal, p, f, c):
    conn = sqlite3.connect("bot_diet_data.db")
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        INSERT INTO food_log (user_id, date, food_name, kcal, p, f, c)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, today, food_name, kcal, p, f, c))
    conn.commit()
    conn.close()

def get_today_food_log(user_id):
    conn = sqlite3.connect("bot_diet_data.db")
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT food_name, kcal, p, f, c FROM food_log WHERE user_id = ? AND date = ?", (user_id, today))
    rows = cursor.fetchall()
    conn.close()
    return rows

init_db()

# ============================ Формула Расчета КБЖУ ============================
def calculate_diet_targets(weight, height, age, gender, activity, goal):
    # Миффлин-Сан Жеор BMR
    if gender == "Мужской":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    # Коэффициент активности
    activity_factors = {
        "Минимальная (сидячий быт)": 1.2,
        "Легкая (1-3 тренировки)": 1.375,
        "Средняя (3-5 тренировок)": 1.55,
        "Высокая (тяжелый спорт/работа)": 1.725
    }
    factor = activity_factors.get(activity, 1.2)
    tdee = bmr * factor

    # Корректировка под цель
    if goal == "Дефицит калорий 📉":
        kcal = tdee * 0.85
        p = weight * 1.8
        f = weight * 0.9
    elif goal == "Сушка организма ⚡":
        kcal = tdee * 0.80
        p = weight * 2.2
        f = weight * 0.8
    elif goal == "Набор массы 📈":
        kcal = tdee * 1.15
        p = weight * 1.6
        f = weight * 1.1
    else: # Поддержание ⚖️
        kcal = tdee
        p = weight * 1.6
        f = weight * 1.0

    # Углеводы рассчитываются по остаточному принципу
    kcal_from_pf = (p * 4) + (f * 9)
    c = max(0, (kcal - kcal_from_pf) / 4)

    return int(kcal), int(p), int(f), int(c)

# ================================ Состояния FSM ================================
class ProfileStates(StatesGroup):
    weight = State()
    height = State()
    age = State()
    gender = State()
    activity = State()
    goal = State()

class FoodStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

class AssistantStates(StatesGroup):
    waiting_for_question = State()

# ================================ Клавиатуры ================================
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton(text="📸 Фото еды"), types.KeyboardButton(text="📝 Внести еду"))
    keyboard.add(types.KeyboardButton(text="📊 Моё БЖУ"), types.KeyboardButton(text="🤖 AI Ассистент"))
    keyboard.add(types.KeyboardButton(text="📔 Дневник питания"), types.KeyboardButton(text="🔄 Заполнить данные заново"))
    keyboard.add(types.KeyboardButton(text="🏠 Меню"), types.KeyboardButton(text="⬅️ Назад"))
    return keyboard

def get_gender_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton(text="Мужской"), types.KeyboardButton(text="Женский"))
    keyboard.add(types.KeyboardButton(text="🏠 Меню"), types.KeyboardButton(text="⬅️ Назад"))
    return keyboard

def get_activity_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton(text="Минимальная (сидячий быт)"))
    keyboard.add(types.KeyboardButton(text="Легкая (1-3 тренировки)"))
    keyboard.add(types.KeyboardButton(text="Средняя (3-5 тренировок)"))
    keyboard.add(types.KeyboardButton(text="Высокая (тяжелый спорт/работа)"))
    keyboard.add(types.KeyboardButton(text="🏠 Меню"), types.KeyboardButton(text="⬅️ Назад"))
    return keyboard

def get_goal_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton(text="Дефицит калорий 📉"), types.KeyboardButton(text="Поддержание веса ⚖️"))
    keyboard.add(types.KeyboardButton(text="Набор массы 📈"), types.KeyboardButton(text="Сушка организма ⚡"))
    keyboard.add(types.KeyboardButton(text="🏠 Меню"), types.KeyboardButton(text="⬅️ Назад"))
    return keyboard

# ============================ Асинхронные запросы к ИИ через aiohttp ============================
async def ask_groq_ai(payload: dict) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(GROQ_API_URL, json=payload, headers=headers) as response:
            if response.status == 200:
                result = await response.json()
                return result['choices'][0]['message']['content']
            else:
                error_text = await response.text()
                logging.error(f"Groq API Error: {error_text}")
                raise Exception(f"API status {response.status}")

# =================================== ОБРАБОТЧИКИ ===================================

# --- Старт ---
@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    user_id = message.from_user.id
    profile = get_user_profile(user_id)
    
    if profile:
        await message.answer(
            f"👋 С возвращением, <b>{message.from_user.first_name}</b>!\n"
            f"Я твой умный диетолог. Ты можешь прислать мне фото своей еды, внести приём пищи текстом или пообщаться со мной в чате.",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            f"Привет! Я твой персональный AI-ассистент по питанию и расчету БЖУ.\n"
            f"Чтобы начать, мне нужно рассчитать твой дневной рацион. Давай заполним анкету!",
            reply_markup=get_main_keyboard()
        )
        await ProfileStates.weight.set()
        await message.answer("Шаг 1: Напиши свой актуальный вес (в кг):")

# --- Назад / Меню ---
@dp.message_handler(lambda m: m.text == "🏠 Меню", state='*')
async def cmd_menu(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Ты в главном меню бота.", reply_markup=get_main_keyboard())

@dp.message_handler(lambda m: m.text == "⬅️ Назад", state='*')
async def cmd_back(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Ты уже в меню.", reply_markup=get_main_keyboard())
        return

    # Цепочка назад в анкете
    if current_state == ProfileStates.height.state:
        await ProfileStates.weight.set()
        await message.answer("Шаг 1: Напиши свой вес (в кг):")
    elif current_state == ProfileStates.age.state:
        await ProfileStates.height.set()
        await message.answer("Шаг 2: Напиши свой рост (в см):")
    elif current_state == ProfileStates.gender.state:
        await ProfileStates.age.set()
        await message.answer("Шаг 3: Напиши свой возраст:")
    elif current_state == ProfileStates.activity.state:
        await ProfileStates.gender.set()
        await message.answer("Шаг 4: Выбери свой пол:", reply_markup=get_gender_keyboard())
    elif current_state == ProfileStates.goal.state:
        await ProfileStates.activity.set()
        await message.answer("Шаг 5: Выбери уровень активности:", reply_markup=get_activity_keyboard())
    else:
        await state.finish()
        await message.answer("Действие отменено.", reply_markup=get_main_keyboard())

# --- Опрос анкеты ---
@dp.message_handler(state=ProfileStates.weight)
async def process_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", "."))
        await state.update_data(weight=weight)
        await ProfileStates.height.set()
        await message.answer("Шаг 2: Напиши свой рост (в см):")
    except ValueError:
        await message.answer("Введите числовое значение веса!")

@dp.message_handler(state=ProfileStates.height)
async def process_height(message: types.Message, state: FSMContext):
    try:
        height = float(message.text.replace(",", "."))
        await state.update_data(height=height)
        await ProfileStates.age.set()
        await message.answer("Шаг 3: Напиши свой возраст:")
    except ValueError:
        await message.answer("Введите числовое значение роста!")

@dp.message_handler(state=ProfileStates.age)
async def process_age(message: types.Message, state: FSMContext):
    try:
        age = int(message.text)
        await state.update_data(age=age)
        await ProfileStates.gender.set()
        await message.answer("Шаг 4: Выбери свой пол:", reply_markup=get_gender_keyboard())
    except ValueError:
        await message.answer("Введите числовой возраст!")

@dp.message_handler(state=ProfileStates.gender)
async def process_gender(message: types.Message, state: FSMContext):
    if message.text in ["Мужской", "Женский"]:
        await state.update_data(gender=message.text)
        await ProfileStates.activity.set()
        await message.answer("Шаг 5: Выбери уровень физической активности:", reply_markup=get_activity_keyboard())
    else:
        await message.answer("Используйте кнопки для выбора пола!")

@dp.message_handler(state=ProfileStates.activity)
async def process_activity(message: types.Message, state: FSMContext):
    if message.text in ["Минимальная (сидячий быт)", "Легкая (1-3 тренировки)", "Средняя (3-5 тренировок)", "Высокая (тяжелый спорт/работа)"]:
        await state.update_data(activity=message.text)
        await ProfileStates.goal.set()
        await message.answer("Шаг 6: Выбери свою фитнес-цель:", reply_markup=get_goal_keyboard())
    else:
        await message.answer("Используйте кнопки для выбора активности!")

@dp.message_handler(state=ProfileStates.goal)
async def process_goal(message: types.Message, state: FSMContext):
    if message.text in ["Дефицит калорий 📉", "Поддержание веса ⚖️", "Набор массы 📈", "Сушка организма ⚡"]:
        goal = message.text
        data = await state.get_data()
        await state.finish()

        # Расчет параметров
        kcal, p, f, c = calculate_diet_targets(
            data['weight'], data['height'], data['age'], data['gender'], data['activity'], goal
        )

        save_user_profile(message.from_user.id, data['weight'], data['height'], data['age'], data['gender'], data['activity'], goal, kcal, p, f, c)

        await message.answer(
            f"🎉 <b>Расчет завершен! Твоя программа питания создана:</b>\n\n"
            f"🎯 <b>Цель:</b> {goal}\n"
            f"🔥 <b>Калории:</b> {kcal} ккал\n"
            f"🥩 <b>Белки:</b> {p} г\n"
            f"🥑 <b>Жиры:</b> {f} г\n"
            f"🌾 <b>Углеводы:</b> {c} г\n\n"
            f"Теперь присылай фото еды, чтобы вести дневник!",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer("Используйте кнопки для выбора цели!")

# --- Сброс данных ---
@dp.message_handler(lambda m: m.text == "🔄 Заполнить данные заново", state='*')
async def cmd_reset(message: types.Message, state: FSMContext):
    await state.finish()
    await ProfileStates.weight.set()
    await message.answer("Окей, погнали заново. Шаг 1: Напиши свой вес (в кг):", reply_markup=get_main_keyboard())

# --- Кнопка "Моё БЖУ" ---
@dp.message_handler(lambda m: m.text == "📊 Моё БЖУ", state='*')
async def cmd_my_macros(message: types.Message, state: FSMContext):
    await state.finish()
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await message.answer("Твой профиль еще не создан. Нажми '🔄 Заполнить данные заново'.")
        return

    weight, height, age, gender, activity, goal, target_kcal, target_p, target_f, target_c = profile
    today_meals = get_today_food_log(message.from_user.id)

    # Суммируем съеденное за сегодня
    total_kcal = sum(meal[1] for meal in today_meals)
    total_p = sum(meal[2] for meal in today_meals)
    total_f = sum(meal[3] for meal in today_meals)
    total_c = sum(meal[4] for meal in today_meals)

    await message.answer(
        f"📋 <b>ТВОЙ ДНЕВНОЙ ПЛАН КБЖУ:</b>\n\n"
        f"🎯 <b>Цель:</b> {goal}\n"
        f"⚖️ Вес: {weight} кг | Рост: {height} см | Возраст: {age}\n\n"
        f"📊 <b>Прогресс на сегодня:</b>\n"
        f"🔥 <b>Калории:</b> {total_kcal} / {target_kcal} ккал\n"
        f"🥩 <b>Белки:</b> {total_p} / {target_p} г\n"
        f"🥑 <b>Жиры:</b> {total_f} / {target_f} г\n"
        f"🌾 <b>Углеводы:</b> {total_c} / {target_c} г",
        reply_markup=get_main_keyboard()
    )

# --- Кнопка "Дневник питания" ---
@dp.message_handler(lambda m: m.text == "📔 Дневник питания", state='*')
async def cmd_diary(message: types.Message, state: FSMContext):
    await state.finish()
    today_meals = get_today_food_log(message.from_user.id)
    if not today_meals:
        await message.answer("Дневник на сегодня пуст. Отправь фото или запиши еду текстом, чтобы добавить её!")
        return

    text = "📔 <b>ТВОЙ ДНЕВНИК ПИТАНИЯ ЗА СЕГОДНЯ:</b>\n\n"
    total_kcal = 0
    for idx, meal in enumerate(today_meals, 1):
        name, kcal, p, f, c = meal
        text += f"{idx}. <b>{name}</b>\n └ {kcal} ккал | Б: {p}г, Ж: {f}г, У: {c}г\n"
        total_kcal += kcal

    text += f"\n<b>Всего за день:</b> {total_kcal} ккал"
    await message.answer(text, reply_markup=get_main_keyboard())

# --- Режим AI Ассистента (Чат) ---
@dp.message_handler(lambda m: m.text == "🤖 AI Ассистент", state='*')
async def cmd_ai_assistant(message: types.Message, state: FSMContext):
    await state.finish()
    await AssistantStates.waiting_for_question.set()
    await message.answer(
        "💬 Ты перешел в режим чата с AI Нутрициологом.\n"
        "Задай мне любой вопрос о диетах, тренировках, продуктах или рецептах! Я готов ответить.",
        reply_markup=get_main_keyboard()
    )

@dp.message_handler(state=AssistantStates.waiting_for_question)
async def process_ai_question(message: types.Message, state: FSMContext):
    if message.text in ["🏠 Меню", "⬅️ Назад", "📸 Фото еды", "📝 Внести еду", "📊 Моё БЖУ", "🤖 AI Ассистент", "📔 Дневник питания", "🔄 Заполнить данные заново"]:
        await state.finish()
        # Перенаправляем на стандартные команды
        return await default_handler(message)

    await message.answer("⏳ Думаю над ответом...")
    payload = {
        "model": "llama-3.2-11b-vision-preview",
        "messages": [
            {"role": "system", "content": "Ты высококлассный фитнес-тренер и спортивный диетолог. Отвечай экспертно, вежливо, на русском языке."},
            {"role": "user", "content": message.text}
        ],
        "max_tokens": 1000
    }
    try:
        response = await ask_groq_ai(payload)
        await message.answer(response, reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer("Не удалось получить ответ от ИИ. Попробуй позже.")

# --- Ввод еды текстом ---
@dp.message_handler(lambda m: m.text == "📝 Внести еду", state='*')
async def cmd_food_text(message: types.Message, state: FSMContext):
    await state.finish()
    await FoodStates.waiting_for_text.set()
    await message.answer("Напиши текстом, что ты съел (например: 'Овсянка на молоке 200г, банан 1шт'):")

@dp.message_handler(state=FoodStates.waiting_for_text)
async def process_food_text(message: types.Message, state: FSMContext):
    if message.text in ["🏠 Меню", "⬅️ Назад"]:
        await state.finish()
        return await cmd_menu(message, state)

    food_desc = message.text
    await message.answer("⏳ ИИ анализирует КБЖУ...")
    await state.finish()

    payload = {
        "model": "llama-3.2-11b-vision-preview",
        "messages": [
            {"role": "system", "content": 
             "Ты диетолог. Рассчитай КБЖУ для указанной еды. "
             "Отвечай на русском языке. В самом конце ответа обязательно выведи параметры еды в строгом формате JSON: "
             "JSON_DATA: {\"name\": \"название блюда\", \"kcal\": 250, \"p\": 15, \"f\": 8, \"c\": 30}"},
            {"role": "user", "content": food_desc}
        ],
        "max_tokens": 800
    }

    try:
        answer = await ask_groq_ai(payload)
        
        # Вырезаем JSON из ответа
        match = re.search(r'JSON_DATA:\s*(\{.*\})', answer)
        if match:
            try:
                js = json.loads(match.group(1))
                add_food_to_log(message.from_user.id, js['name'], js['kcal'], js['p'], js['f'], js['c'])
                answer = answer.split("JSON_DATA")[0] # Убираем технический JSON из сообщения
                await message.answer(answer + "\n\n✅ <b>Блюдо добавлено в твой дневник питания!</b>", reply_markup=get_main_keyboard())
            except Exception as e:
                await message.answer(answer, reply_markup=get_main_keyboard())
        else:
            await message.answer(answer, reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer("Ошибка связи с ИИ.")

# --- Ввод еды по ФОТО ---
@dp.message_handler(lambda m: m.text == "📸 Фото еды", state='*')
async def cmd_food_photo(message: types.Message, state: FSMContext):
    await state.finish()
    await FoodStates.waiting_for_photo.set()
    await message.answer("Отправь мне фото своего блюда:")

@dp.message_handler(state=FoodStates.waiting_for_photo, content_types=types.ContentType.PHOTO)
@dp.message_handler(content_types=types.ContentType.PHOTO, state='*')
async def process_food_photo(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("⏳ Сканирую фото блюда и рассчитываю нутриенты...")

    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        photo_buffer = await bot.download_file(file_info.file_path)
        base64_image = base64.b64encode(photo_buffer.read()).decode('utf-8')

        payload = {
            "model": "llama-3.2-11b-vision-preview",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": "Ты — нутрициолог. Распознай блюдо на фото, оцени размер порции и рассчитай КБЖУ. "
                                    "Отвечай на русском языке. В самом конце ответа обязательно выведи параметры еды в строгом формате JSON: "
                                    "JSON_DATA: {\"name\": \"название блюда\", \"kcal\": 250, \"p\": 15, \"f\": 8, \"c\": 30}"
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
            "max_tokens": 800
        }

        answer = await ask_groq_ai(payload)
        
        # Вырезаем JSON для логирования в дневник
        match = re.search(r'JSON_DATA:\s*(\{.*\})', answer)
        if match:
            try:
                js = json.loads(match.group(1))
                add_food_to_log(message.from_user.id, js['name'], js['kcal'], js['p'], js['f'], js['c'])
                answer = answer.split("JSON_DATA")[0]
                await message.answer(answer + "\n\n✅ <b>Блюдо успешно распознано и внесено в дневник!</b>", reply_markup=get_main_keyboard())
            except Exception as e:
                await message.answer(answer, reply_markup=get_main_keyboard())
        else:
            await message.answer(answer, reply_markup=get_main_keyboard())

    except Exception as e:
        logging.error(f"Error processing photo: {e}")
        await message.answer("Не удалось считать фото. Убедись, что на фото еда и API-ключ настроен верно.")

# --- Обработка любого нераспознанного текста ---
@dp.message_handler(state='*')
async def default_handler(message: types.Message):
    await message.answer(
        "Я не понял команду. Нажми одну из кнопок в меню ниже или пришли фото своей еды!",
        reply_markup=get_main_keyboard()
    )

# ================================= Запуск =================================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
