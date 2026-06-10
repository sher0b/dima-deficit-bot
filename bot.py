
import os
import logging
import sqlite3
import base64
import re
import json
from datetime import datetime
import aiohttp

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# ================================ КОНФИГУРАЦИЯ =================================
# Если ты запускаешь на хостинге, он подтянет ключи из переменных окружения.
# Или ты можешь вписать их прямо сюда вместо дефолтных значений.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "СЮДА_ВСТАВЬ_ТОКЕН_ОТ_BOTFATHER")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "СЮДА_ВСТАВЬ_КЛЮЧ_GROQ")

ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@твой_канал")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/твой_канал")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ===================== Настройка Логирования, Бота и FSM =====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

if "СЮДА_ВСТАВЬ" in TELEGRAM_TOKEN or TELEGRAM_TOKEN == "":
    logging.error("КРИТИЧЕСКАЯ ОШИБКА: Замени токен в конфигурации бота!")
    exit(1)

if "СЮДА_ВСТАВЬ" in GROQ_API_KEY or GROQ_API_KEY == "":
    logging.error("КРИТИЧЕСКАЯ ОШИБКА: Замени API ключ Groq!")
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ================================ База Данных SQLite ===============================
def init_db():
    conn = sqlite3.connect("bot_diet_data.db")
    cursor = conn.cursor()
    # Таблица профилей пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            weight REAL,
            height REAL,
            age INTEGER,
            gender TEXT,
            activity REAL,
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
def calculate_diet_targets(weight, height, age, gender, activity_factor, goal):
    # Формула Миффлина-Сан Жеора (BMR)
    if gender == "Мужской":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    # Общий расход калорий (TDEE)
    tdee = bmr * activity_factor

    # Настройки в зависимости от цели
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
    else: # Поддержание веса ⚖️
        kcal = tdee
        p = weight * 1.6
        f = weight * 1.0

    # Расчет углеводов по остаточному принципу
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

# ================================ КЛАВИАТУРЫ ================================
def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(text="📸 Фото еды"),
        types.KeyboardButton(text="📝 Внести еду"),
        types.KeyboardButton(text="📊 Моё БЖУ"),
        types.KeyboardButton(text="🤖 AI Ассистент"),
        types.KeyboardButton(text="📔 Дневник питания"),
        types.KeyboardButton(text="🔄 Заполнить данные заново")
    )
    return kb

def get_gender_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton(text="Мужской"), types.KeyboardButton(text="Женский"))
    kb.add(types.KeyboardButton(text="🏠 Меню"), types.KeyboardButton(text="⬅️ Назад"))
    return kb

def get_activity_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add(
        types.KeyboardButton(text="1.2 – Сидячий"),
        types.KeyboardButton(text="1.3 – Небольшая активность"),
        types.KeyboardButton(text="1.4 – Умеренная активность"),
        types.KeyboardButton(text="1.5 – Высокая активность"),
        types.KeyboardButton(text="1.6 – Очень высокая активность"),
        types.KeyboardButton(text="🏠 Меню"),
        types.KeyboardButton(text="⬅️ Назад")
    )
    return kb

def get_goal_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(text="Дефицит калорий 📉"),
        types.KeyboardButton(text="Поддержание веса ⚖️"),
        types.KeyboardButton(text="Набор массы 📈"),
        types.KeyboardButton(text="Сушка организма ⚡")
    )
    kb.add(types.KeyboardButton(text="🏠 Меню"), types.KeyboardButton(text="⬅️ Назад"))
    return kb

def get_ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(text="❌ Завершить диалог"))
    return kb

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
                logging.error(f"Groq API Error ({response.status}): {error_text}")
                raise Exception(f"API returned status {response.status}")

# =================================== ОБРАБОТЧИКИ (HANDLERS) ===================================

# --- Старт ---
@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    user_id = message.from_user.id
    profile = get_user_profile(user_id)
    
    if profile:
        await message.answer(
            f"👋 С возвращением, <b>{message.from_user.first_name}</b>!\n"
            f"Я готов помочь тебе отслеживать КБЖУ. Пришли фото еды, запиши её текстом или задай вопрос ИИ-ассистенту.",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            f"Привет! Я твой персональный AI-ассистент по питанию и расчету БЖУ.\n"
            f"Чтобы начать работу, давай создадим твой профиль рациона питания!",
            reply_markup=get_main_keyboard()
        )
        await ProfileStates.weight.set()
        await message.answer("<b>Шаг 1:</b> Напиши свой актуальный вес (в кг):")

# --- Меню и Назад ---
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

    if current_state == ProfileStates.height.state:
        await ProfileStates.weight.set()
        await message.answer("<b>Шаг 1:</b> Напиши свой вес (в кг):")
    elif current_state == ProfileStates.age.state:
        await ProfileStates.height.set()
        await message.answer("<b>Шаг 2:</b> Напиши свой рост (в см):")
    elif current_state == ProfileStates.gender.state:
        await ProfileStates.age.set()
        await message.answer("<b>Шаг 3:</b> Напиши свой возраст:")
    elif current_state == ProfileStates.activity.state:
        await ProfileStates.gender.set()
        await message.answer("<b>Шаг 4:</b> Выбери свой пол:", reply_markup=get_gender_keyboard())
    elif current_state == ProfileStates.goal.state:
        await ProfileStates.gender.set() # Перекидываем на пол, оттуда логичнее вернуться к выбору активности
        await message.answer("<b>Шаг 4:</b> Выбери свой пол:", reply_markup=get_gender_keyboard())
    else:
        await state.finish()
        await message.answer("Действие отменено.", reply_markup=get_main_keyboard())

# --- Заполнение анкеты ---
@dp.message_handler(state=ProfileStates.weight)
async def process_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", "."))
        await state.update_data(weight=weight)
        await ProfileStates.height.set()
        await message.answer("<b>Шаг 2:</b> Напиши свой рост (в см):")
    except ValueError:
        await message.answer("Пожалуйста, введи вес числом (например: 72.5):")

@dp.message_handler(state=ProfileStates.height)
async def process_height(message: types.Message, state: FSMContext):
    try:
        height = float(message.text.replace(",", "."))
        await state.update_data(height=height)
        await ProfileStates.age.set()
        await message.answer("<b>Шаг 3:</b> Напиши свой возраст:")
    except ValueError:
        await message.answer("Пожалуйста, введи рост числом (например: 178):")

@dp.message_handler(state=ProfileStates.age)
async def process_age(message: types.Message, state: FSMContext):
    try:
        age = int(message.text)
        await state.update_data(age=age)
        await ProfileStates.gender.set()
        await message.answer("<b>Шаг 4:</b> Выбери свой пол:", reply_markup=get_gender_keyboard())
    except ValueError:
        await message.answer("Пожалуйста, укажи возраст целым числом:")

@dp.message_handler(state=ProfileStates.gender)
async def process_gender(message: types.Message, state: FSMContext):
    if message.text in ["Мужской", "Женский"]:
        await state.update_data(gender=message.text)
        await ProfileStates.activity.set()
        await message.answer(
            "Пожалуйста, выберите коэффициент активности из предложенных ниже:\n\n"
            "<b>Как понять какой коэффициент выбрать?</b>\n"
            "• <b>1.2 – сидячий образ жизни</b>\n"
            "Минимум движений, офисная работа, &lt;5 тыс. шагов, без тренировок.\n\n"
            "• <b>1.3 – небольшая активность</b>\n"
            "Немного ходьбы (5–8 тыс. шагов), редкие лёгкие тренировки 0–1 раз в неделю.\n\n"
            "• <b>1.4 – умеренная активность</b>\n"
            "Ходьба 8–12 тыс. шагов, 2–3 тренировки средней интенсивности в неделю.\n\n"
            "• <b>1.5 – высокая активность</b>\n"
            "Более 12 тыс. шагов, регулярные силовые/кардио 3–5 раз в неделю, активная работа.\n\n"
            "• <b>1.6 – очень высокая активность</b>\n"
            "Физический труд + интенсивные тренировки почти ежедневно (спортсмены, рабочие тяжёлых профессий).\n\n"
            "<i>Выберите один из вариантов ниже:</i>",
            reply_markup=get_activity_keyboard()
        )
    else:
        await message.answer("Пожалуйста, воспользуйся кнопками для выбора пола!")

@dp.message_handler(state=ProfileStates.activity)
async def process_activity(message: types.Message, state: FSMContext):
    mapping = {
        "1.2 – Сидячий": 1.2,
        "1.3 – Небольшая активность": 1.3,
        "1.4 – Умеренная активность": 1.4,
        "1.5 – Высокая активность": 1.5,
        "1.6 – Очень высокая активность": 1.6
    }
    
    if message.text in mapping:
        await state.update_data(activity=mapping[message.text])
        await ProfileStates.goal.set()
        await message.answer("<b>Шаг 6:</b> Выбери фитнес-цель:", reply_markup=get_goal_keyboard())
    elif message.text == "🏠 Меню":
        await state.finish()
        await message.answer("Ты вышел в главное меню.", reply_markup=get_main_keyboard())
    else:
        await message.answer("Пожалуйста, выбери активность с помощью кнопок!")

@dp.message_handler(state=ProfileStates.goal)
async def process_goal(message: types.Message, state: FSMContext):
    if message.text in ["Дефицит калорий 📉", "Поддержание веса ⚖️", "Набор массы 📈", "Сушка организма ⚡"]:
        goal = message.text
        data = await state.get_data()
        await state.finish()

        # Магический расчет КБЖУ
        kcal, p, f, c = calculate_diet_targets(
            data['weight'], data['height'], data['age'], data['gender'], data['activity'], goal
        )

        save_user_profile(
            message.from_user.id, data['weight'], data['height'], data['age'], 
            data['gender'], data['activity'], goal, kcal, p, f, c
        )

        await message.answer(
            f"🎉 <b>Расчет завершен! Программа питания создана:</b>\n\n"
            f"🎯 <b>Цель:</b> {goal}\n"
            f"🔥 <b>Калории:</b> {kcal} ккал\n"
            f"🥩 <b>Белки:</b> {p} г\n"
            f"🥑 <b>Жиры:</b> {f} г\n"
            f"🌾 <b>Углеводы:</b> {c} г\n\n"
            f"Теперь ты можешь вносить еду и следить за прогрессом!",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer("Используй кнопки для выбора фитнес-цели!")

# --- Сброс / Заполнение заново ---
@dp.message_handler(lambda m: m.text == "🔄 Заполнить данные заново", state='*')
async def cmd_reset(message: types.Message, state: FSMContext):
    await state.finish()
    await ProfileStates.weight.set()
    await message.answer("Окей, обновим твои данные. <b>Шаг 1:</b> Напиши вес (в кг):", reply_markup=get_main_keyboard())

# --- Кнопка "Моё БЖУ" ---
@dp.message_handler(lambda m: m.text == "📊 Моё БЖУ", state='*')
async def cmd_my_macros(message: types.Message, state: FSMContext):
    await state.finish()
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await message.answer("Твой профиль еще не настроен. Нажми '🔄 Заполнить данные заново', чтобы начать!")
        return

    weight, height, age, gender, activity, goal, target_kcal, target_p, target_f, target_c = profile
    today_meals = get_today_food_log(message.from_user.id)

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
        await message.answer("Дневник пуст. Внеси еду текстом или пришли фото блюда!", reply_markup=get_main_keyboard())
        return

    text = "📔 <b>ТВОЙ ДНЕВНИК ПИТАНИЯ ЗА СЕГОДНЯ:</b>\n\n"
    total_kcal = 0
    for idx, meal in enumerate(today_meals, 1):
        name, kcal, p, f, c = meal
        text += f"{idx}. <b>{name}</b>\n └ {kcal} ккал | Б: {p}г, Ж: {f}г, У: {c}г\n"
        total_kcal += kcal

    text += f"\n<b>Всего съедено:</b> {total_kcal} ккал"
    await message.answer(text, reply_markup=get_main_keyboard())

# --- Режим AI Ассистента ---
@dp.message_handler(lambda m: m.text == "🤖 AI Ассистент", state='*')
async def cmd_ai_assistant(message: types.Message, state: FSMContext):
    await state.finish()
    await AssistantStates.waiting_for_question.set()
    await message.answer(
        "💬 <b>Режим AI-нутрициолога активен.</b>\n\n"
        "Спрашивай меня о чём угодно: о рецептах, замене продуктов, спорте.\n"
        "Чтобы выйти, нажми кнопку ниже 👇",
        reply_markup=get_ai_keyboard()
    )

@dp.message_handler(state=AssistantStates.waiting_for_question)
async def process_ai_question(message: types.Message, state: FSMContext):
    if message.text == "❌ Завершить диалог":
        await state.finish()
        await message.answer("Диалог завершен. Возвращаю тебя в главное меню.", reply_markup=get_main_keyboard())
        return

    msg = await message.answer("⏳ Составляю экспертный ответ...")
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
        await message.answer(response, reply_markup=get_ai_keyboard())
        await bot.delete_message(message.chat.id, msg.message_id)
    except Exception as e:
        logging.error(f"Error in AI Chat: {e}")
        await message.answer("Не удалось связаться с AI. Попробуй позже.", reply_markup=get_ai_keyboard())

# --- Ввод еды текстом ---
@dp.message_handler(lambda m: m.text == "📝 Внести еду", state='*')
async def cmd_food_text(message: types.Message, state: FSMContext):
    await state.finish()
    await FoodStates.waiting_for_text.set()
    await message.answer("Напиши текстом, что ты съел (например: 'Пюре картофельное 150г, две котлеты куриные'):")

@dp.message_handler(state=FoodStates.waiting_for_text)
async def process_food_text(message: types.Message, state: FSMContext):
    if message.text in ["🏠 Меню", "⬅️ Назад"]:
        await state.finish()
        return await cmd_menu(message, state)

    food_desc = message.text
    await message.answer("⏳ Анализирую состав еды...", reply_markup=get_main_keyboard())
    await state.finish()

    payload = {
        "model": "llama-3.2-11b-vision-preview",
        "messages": [
            {"role": "system", "content": 
             "Ты профессиональный диетолог. Рассчитай КБЖУ для указанной еды. "
             "Отвечай на русском языке. В самом конце ответа обязательно выведи параметры еды в строгом формате JSON: "
             "JSON_DATA: {\"name\": \"название блюда\", \"kcal\": 250, \"p\": 15, \"f\": 8, \"c\": 30}"},
            {"role": "user", "content": food_desc}
        ],
        "max_tokens": 800
    }

    try:
        answer = await ask_groq_ai(payload)
        match = re.search(r'JSON_DATA:\s*(\{.*\})', answer, re.DOTALL)
        if match:
            try:
                js = json.loads(match.group(1))
                add_food_to_log(message.from_user.id, js['name'], js['kcal'], js['p'], js['f'], js['c'])
                answer = answer.split("JSON_DATA")[0].strip()
                await message.answer(answer + "\n\n✅ <b>Блюдо добавлено в твой дневник питания!</b>", reply_markup=get_main_keyboard())
            except Exception as e:
                await message.answer(answer, reply_markup=get_main_keyboard())
        else:
            await message.answer(answer, reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer("Ошибка связи с ИИ. Попробуй позже.", reply_markup=get_main_keyboard())

# --- Ввод еды по фото ---
@dp.message_handler(lambda m: m.text == "📸 Фото еды", state='*')
async def cmd_food_photo(message: types.Message, state: FSMContext):
    await state.finish()
    await FoodStates.waiting_for_photo.set()
    await message.answer("Отправь мне фото своего блюда:")

@dp.message_handler(state=FoodStates.waiting_for_photo, content_types=types.ContentType.PHOTO)
@dp.message_handler(content_types=types.ContentType.PHOTO, state='*')
async def process_food_photo(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("⏳ Анализирую снимок блюда...", reply_markup=get_main_keyboard())

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
                            "text": "Ты — нутрициолог. Распознай блюдо на фото, оцени порцию и рассчитай КБЖУ. "
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
        
        match = re.search(r'JSON_DATA:\s*(\{.*\})', answer, re.DOTALL)
        if match:
            try:
                js = json.loads(match.group(1))
                add_food_to_log(message.from_user.id, js['name'], js['kcal'], js['p'], js['f'], js['c'])
                answer = answer.split("JSON_DATA")[0].strip()
                await message.answer(answer + "\n\n✅ <b>Блюдо успешно распознано и внесено в дневник!</b>", reply_markup=get_main_keyboard())
            except Exception as e:
                await message.answer(answer, reply_markup=get_main_keyboard())
        else:
            await message.answer(answer, reply_markup=get_main_keyboard())

    except Exception as e:
        logging.error(f"Error processing photo: {e}")
        await message.answer("Не удалось считать фото. Убедись, что на фото действительно еда и API-ключ настроен верно.", reply_markup=get_main_keyboard())

# --- Эхо на неизвестные команды ---
@dp.message_handler(state='*')
async def default_handler(message: types.Message):
    await message.answer(
        "Я не понял команду. Нажми одну из кнопок в меню ниже или пришли фото своей еды!",
        reply_markup=get_main_keyboard()
    )

# ================================= Запуск =================================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
