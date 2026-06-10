
import os
import logging
import sqlite3
import re
import json
import asyncio
import random
from datetime import datetime, timedelta
import aiohttp

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# Попытка импорта matplotlib для генерации графиков
try:
    import matplotlib
    matplotlib.use('Agg')  # Безэкранный режим для работы на серверах
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# ================================ КОНФИГУРАЦИЯ =================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "СЮДА_ВСТАВЬ_ТОКЕН_ОТ_BOTFATHER")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "СЮДА_ВСТАВЬ_КЛЮЧ_GROQ")

# Настройки Telegram-канала для обязательной подписки
CHANNEL_ID = os.getenv("CHANNEL_ID", "@твой_канал")  # ID канала (с @)
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/твой_канал")  # Ссылка на канал

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
TEXT_MODEL = "llama-3.3-70b-versatile" 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ================================ СОСТОЯНИЯ (FSM) ===============================
class ProfileStates(StatesGroup):
    weight = State()
    height = State()
    age = State()
    gender = State()
    activity = State()
    goal = State()

class UpdateProfileStates(StatesGroup):
    weight = State()
    height = State()
    age = State()
    gender = State()
    activity = State()

class FoodStates(StatesGroup):
    waiting_for_text = State()

class AssistantStates(StatesGroup):
    waiting_for_question = State()

# ================================ БАЗА ДАННЫХ ===============================
def init_db():
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, weight REAL, height REAL, age INTEGER, 
            gender TEXT, activity REAL, goal TEXT, target_kcal INTEGER, 
            target_p INTEGER, target_f INTEGER, target_c INTEGER
        )""")
    # Таблица дневника питания
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
            date TEXT, food_name TEXT, kcal INTEGER, p INTEGER, f INTEGER, c INTEGER
        )""")
    # Таблица трекера воды
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS water_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
            date TEXT, amount INTEGER
        )""")
    # Таблица истории сообщений для памяти ИИ
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
            role TEXT, content TEXT, timestamp TEXT
        )""")
    conn.commit()
    conn.close()

def save_user_profile(user_id, weight, height, age, gender, activity, goal, kcal, p, f, c):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?)", 
                   (user_id, weight, height, age, gender, activity, goal, kcal, p, f, c))
    conn.commit()
    conn.close()

def get_user_profile(user_id):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def update_user_goal_db(user_id, goal, kcal, p, f, c):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users 
        SET goal = ?, target_kcal = ?, target_p = ?, target_f = ?, target_c = ?
        WHERE user_id = ?
    """, (goal, kcal, p, f, c, user_id))
    conn.commit()
    conn.close()

def add_food_to_log(user_id, food_name, kcal, p, f, c):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("INSERT INTO food_log (user_id, date, food_name, kcal, p, f, c) VALUES (?,?,?,?,?,?,?)",
                   (user_id, today, food_name, kcal, p, f, c))
    conn.commit()
    conn.close()

def delete_food_from_log(meal_id, user_id):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM food_log WHERE id = ? AND user_id = ?", (meal_id, user_id))
    conn.commit()
    conn.close()

def add_water_to_log(user_id, amount):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("INSERT INTO water_log (user_id, date, amount) VALUES (?,?,?)", (user_id, today, amount))
    conn.commit()
    conn.close()

def get_today_water(user_id):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT SUM(amount) FROM water_log WHERE user_id = ? AND date = ?", (user_id, today))
    res = cursor.fetchone()[0]
    conn.close()
    return res or 0

def save_chat_msg(user_id, role, content):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO chat_history (user_id, role, content, timestamp) VALUES (?,?,?,?)",
                   (user_id, role, content, now_str))
    conn.commit()
    conn.close()

def get_chat_history(user_id, limit=6):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return list(reversed(rows))

init_db()

# ============================ 100 ПП СОВЕТОВ ============================
PP_TIPS = [
    "Пейте стакан теплой воды сразу после пробуждения, чтобы запустить метаболизм.",
    "Шаги — самый простой способ увеличить активность. Стремитесь к 10 000 шагов в день.",
    "Белок насыщает лучше всего. Добавляйте его в каждый основной прием пищи.",
    "Не исключайте жиры! Они необходимы для здоровой гормональной системы.",
    "Углеводы — это энергия, а не враг. Главное — выбирать сложные углеводы (крупы, овощи).",
    "Тщательно пережевывайте пищу. Насыщение приходит только через 20 минут.",
    "Спите не менее 7-8 часов. Недосып повышает уровень гормона голода кортизола.",
    "Жидкие калории (сладкий кофе, соки) незаметно разрушают дефицит. Выбирайте чистую воду.",
    "Зелень и овощи должны занимать половину вашей тарелки.",
    "Ограничьте добавленный сахар. Замените его фруктами или безопасными сахарозаменителями.",
    "Ешьте строго без телефона и телевизора. Так вы съедите меньше и качественнее.",
    "Планируйте рацион заранее, чтобы избежать спонтанных срывов на фастфуд.",
    "Силовые тренировки сохраняют мышцы при похудении. Худейте за счет жира, а не мышц.",
    "Не существует продуктов с 'отрицательной калорийностью' — это миф.",
    "Уменьшите количество соли: она задерживает лишнюю воду и вызывает отечность.",
    "Взвешивайте еду в сухом и сыром виде — так расчет калорий будет максимально точным.",
    "Кардио тренировки тренируют сердце, но КБЖУ — главный ключ к снижению веса.",
    "Не голодайте! Экстремальные диеты замедляют обмен веществ и ведут к срыву.",
    "Оливковое масло полезно, но калорийно. Используйте дозатор или чайную ложку.",
    "Авокадо — отличный источник полезных жиров, но половинки в день вполне достаточно.",
    "Приучите себя пить чай и кофе без сахара — ощутите настоящий вкус напитка.",
    "Замените белый хлеб на цельнозерновой или ржаной.",
    "Йогурт лучше покупать классический, без сладких фруктовых наполнителей.",
    "Меньше жарьте на масле. Запекайте в духовке, варите или готовьте на пару.",
    "Алкоголь содержит много скрытых калорий и сильно задерживает воду.",
    "Не путайте жажду с голодом. Хочется перекусить? Сначала выпейте стакан воды.",
    "Перекусывайте орехами, но помните: норма — это небольшая горсть (до 20-30 г).",
    "Рыба (особенно жирная) богата Омега-3 — старайтесь есть её 2 раза в неделю.",
    "Замените майонез в заправках на белый йогурт с горчицей и зеленью.",
    "Фрукты лучше есть целиком, а не пить в виде смузи: клетчатка замедляет усвоение сахара.",
    "Грейпфрут не сжигает жир сам по себе, но он богат витаминами и клетчаткой.",
    "Каша быстрого приготовления — это быстрые углеводы. Варите овсянку длительной варки.",
    "Творог — идеальный вечерний перекус благодаря медленному белку казеину.",
    "Не делите еду на 'плохую' и 'хорошую'. Главное — соблюдать баланс и норму калорий.",
    "Субпродукты (печень, сердечки) — отличный и бюджетный источник железа и белка.",
    "Старайтесь делать последний прием пищи за 2-3 часа до сна.",
    "Клетчатка (отруби, овощи) улучшает пищеварение и продлевает чувство сытости.",
    "Ходите по лестнице вместо лифта — это отличная ежедневная активность.",
    "ПП-выпечка тоже калорийна! Читайте состав и считайте калории даже в полезных сладостях.",
    "Не ориентируйтесь только на весы. Делайте замеры тела сантиметровой лентой.",
    "Вес может колебаться из-за воды, стресса или соленой еды. Это нормально.",
    "Для улучшения микрофлоры кишечника добавьте в рацион квашеную капусту.",
    "Яйца — один из самых сбалансированных источников белка и полезных микроэлементов.",
    "Снижайте стресс. Стресс провоцирует неосознанное переедание.",
    "Используйте тарелки меньшего размера, чтобы обмануть мозг визуальным объемом порции.",
    "Покупайте продукты сытыми. Поход в магазин на голодный желудок ведет к покупке вредностей.",
    "Обезжиренные продукты часто содержат много сахара для улучшения вкуса. Выбирайте умеренную жирность.",
    "Чечевица, нут и фасоль — прекрасные растительные источники белка.",
    "Движение — это жизнь. Делайте легкую разминку каждые 2 часа сидячей работы.",
    "Соблюдайте правило 80/20: 80% рациона — цельная здоровая еда, 20% — любимые лакомства.",
    "Вода с лимоном не сжигает жир, но помогает пить больше чистой воды тем, кому скучен её вкус.",
    "Коллаген лучше усваивается вместе с витамином C. Добавьте болгарский перец или цитрусы.",
    "Чипсы из фруктов и овощей часто калорийнее свежих аналогов. Будьте внимательны.",
    "Шоколад лучше выбирать горький (от 70% какао) — в нем меньше сахара.",
    "Устраивайте разгрузочные дни только по рекомендации врача, а не ради быстрого похудения.",
    "Ягоды — отличный низкокалорийный десерт, богатый антиоксидантами.",
    "Специи (острый перец, имбирь, корица) слегка ускоряют метаболизм и делают вкус ярче.",
    "Следите за осанкой: ровная спина визуально подтягивает живот.",
    "Ведение дневника питания помогает увидеть реальную картину переедания.",
    "Не корите себя за срывы. Срыв — это опыт. Просто продолжайте свой план на следующий день.",
    "ПП — это не временная диета, а образ жизни, который должен быть комфортным.",
    "Увеличивайте нагрузку на тренировках постепенно, чтобы избежать травм.",
    "Качественный зеленый чай содержит катехины, помогающие контролировать аппетит.",
    "Супы дают хорошее чувство сытости при относительно низкой калорийности.",
    "Грибы — низкокалорийный продукт, богатый белком и клетчаткой.",
    "Вместо сладких газировок выбирайте газированную воду с кусочками ягод или лимона.",
    "Дефицит калорий должен быть умеренным (15-20% от поддержки), а не экстремальным.",
    "Семена чиа и льна содержат много клетчатки и полезных жиров. Добавляйте их в каши.",
    "Уделяйте время полноценному отдыху. Перетренированность вредит результатам.",
    "Ореховая паста очень полезна, но 1 столовая ложка может содержать до 100 ккал.",
    "Не верьте детоксам на соках. Наша печень и почки прекрасно справляются с детоксом сами.",
    "Чеснок и лук укрепляют иммунитет и улучшают пищеварение.",
    "Свежие огурцы и сельдерей — отличный хрустящий перекус с минимумом калорий.",
    "Разнообразие в питании гарантирует, что организм получит все нужные витамины.",
    "Если безумно хочется сладкого, съешьте один фрукт или выпейте травяной чай.",
    "Соблюдайте режим питания. Еда в одно и то же время дисциплинирует пищеварение.",
    "Куриная грудка без кожи — эталон постного белка.",
    "Замените обычные макароны на макароны из твердых сортов пшеницы и варите их аль денте.",
    "Овсяный отвар или кисель мягко успокаивают слизистую желудка.",
    "Тофу — отличная альтернатива мясу для разнообразия рациона.",
    "Морская капуста (ламинария) — лучший природный источник йода для щитовидной железы.",
    "Запеченная тыква — прекрасный сладкий и малокалорийный гарнир.",
    "Обращайте внимание на скрытый сахар в соусах (кетчуп, барбекю, тераяки).",
    "Не отказывайтесь от ужина. Главное — вписаться в дневной лимит КБЖУ.",
    "Прогулка после ужина помогает снизить уровень сахара в крови.",
    "Шпинат содержит железо, но лучше всего оно усваивается с лимонным соком.",
    "Сыр — отличный источник кальция, но помните о его высокой жирности.",
    "Если вы сорвались, не пытайтесь 'отработать' еду голодом на следующий день.",
    "Замороженные овощи сохраняют почти все витамины — это отличный выбор зимой.",
    "Заменяйте десерты печеными яблоками с корицей.",
    "Потребляйте достаточно магния (орехи, бананы, зелень) для борьбы со стрессом и судорогами.",
    "Скарлет содержит много витамина C.",
    "Индейка — прекрасная альтернатива куриному филе, богатая триптофаном для хорошего сна.",
    "Для суставов полезны продукты с желатином и холодцы (в пределах КБЖУ).",
    "Красное мясо полезно, но его употребление стоит ограничить до 2-3 раз в неделю.",
    "Финики очень сладкие и полезные, но 3-4 штуки уже содержат около 100 ккал.",
    "Брокколи — суперфуд, содержащий сульфорафан, защищающий клетки организма.",
    "Чистите зубы сразу после ужина, чтобы психологически закрыть рот на замок.",
    "Никакая тренировка не сожжет последствия плохого питания. Контролируйте КБЖУ!",
    "Вы — это то, что вы едите. Наполняйте себя качественным и чистым топливом!"
]

# ============================ РАСЧЕТ КБЖУ И ВОДЫ ============================
def calculate_macros(weight, height, age, gender, activity, goal):
    if gender == "Мужской":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    tdee = bmr * activity

    # Оставлено 3 цели
    if goal == "Похудение (Сушка)":
        kcal = tdee * 0.82
        p = weight * 2.0
        f = weight * 0.8
    elif goal == "Набор массы":
        kcal = tdee * 1.15
        p = weight * 1.7
        f = weight * 1.1
    else:  # Поддержание
        kcal = tdee
        p = weight * 1.6
        f = weight * 1.0

    c = max(0, (kcal - (p * 4 + f * 9)) / 4)
    return int(kcal), int(p), int(f), int(c)

def calculate_water_target(weight):
    # Научная формула: 35 мл воды на 1 кг веса тела
    return int(weight * 35)

# ============================ ПРОВЕРКА ПОДПИСКИ ============================
async def check_subscription(message_or_call) -> bool:
    user_id = message_or_call.from_user.id
    if not CHANNEL_ID or CHANNEL_ID == "@твой_канал":
        return True
        
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
    except Exception as e:
        logging.error(f"Error checking sub for user {user_id}: {e}")
        return True

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🔗 Подписаться на канал", url=CHANNEL_URL),
        types.InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription_again")
    )
    
    text = (
        "⚠️ <b>Доступ временно ограничен!</b>\n\n"
        "Чтобы пользоваться AI Нутрициологом и всеми инструментами, пожалуйста, "
        "<b>подпишитесь</b> на наш официальный Telegram-канал.\n\n"
        "Это помогает нам развивать и поддерживать бота бесплатным для вас! 😊"
    )
    
    if isinstance(message_or_call, types.Message):
        await message_or_call.answer(text, reply_markup=kb)
    else:
        await message_or_call.message.answer(text, reply_markup=kb)
        await message_or_call.answer()
        
    return False

# ============================ ЗАПРОС К ИИ ============================
async def ask_groq_ai(payload: dict) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(GROQ_API_URL, json=payload, headers=headers) as response:
            if response.status == 200:
                result = await response.json()
                return result['choices'][0]['message']['content']
            else:
                error_data = await response.text()
                raise Exception(f"Ошибка {response.status}: {error_data}")

# ================================ КЛАВИАТУРЫ ================================
def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📝 Внести еду", "📊 Моё БЖУ", "🤖 AI Ассистент", "📔 Дневник питания", "💡 Совет дня", "💧 Вода +250мл", "📈 Прогресс")
    return kb

def get_activity_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add(
        "1.2 – Сидячий", 
        "1.3 – Небольшая активность", 
        "1.4 – Умеренная активность", 
        "1.5 – Высокая активность", 
        "1.6 – Очень высокая активность"
    )
    return kb

def get_goals_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add("Похудение (Сушка)", "Поддержание", "Набор массы")
    return kb

def get_ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("❌ Завершить диалог")
    return kb

# ================================= ОБРАБОТЧИКИ =================================

@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    if not await check_subscription(message):
        return

    profile = get_user_profile(message.from_user.id)
    if profile:
        await message.answer(f"👋 С возвращением, <b>{message.from_user.first_name}</b>!", reply_markup=get_main_keyboard())
    else:
        await ProfileStates.weight.set()
        await message.answer("👋 Привет! Я твой персональный AI-нутрициолог.\nДавай создадим твой профиль. Напиши свой <b>вес (в кг)</b>:")

# Проверка подписки
@dp.callback_query_handler(lambda c: c.data == "check_subscription_again", state='*')
async def check_sub_callback(callback_query: types.CallbackQuery, state: FSMContext):
    member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=callback_query.from_user.id)
    if member.status in ["member", "administrator", "creator"]:
        await callback_query.answer("Подписка подтверждена! 🎉")
        await callback_query.message.delete()
        profile = get_user_profile(callback_query.from_user.id)
        if profile:
            await callback_query.message.answer("Доступ открыт!", reply_markup=get_main_keyboard())
        else:
            await ProfileStates.weight.set()
            await callback_query.message.answer("Доступ открыт! Начнем регистрацию.\nНапиши свой <b>вес (в кг)</b>:")
    else:
        await callback_query.answer("Вы всё ещё не подписались на канал ❌", show_alert=True)

# --- Заполнение анкеты с нуля ---
@dp.message_handler(state=ProfileStates.weight)
async def p_w(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
        await state.update_data(weight=weight)
        await ProfileStates.height.set()
        await message.answer("Напиши свой <b>рост (в см)</b>:")
    except ValueError:
        await message.answer("Пожалуйста, введи число (например, 74.5):")

@dp.message_handler(state=ProfileStates.height)
async def p_h(message: types.Message, state: FSMContext):
    try:
        height = float(message.text.replace(',', '.'))
        await state.update_data(height=height)
        await ProfileStates.age.set()
        await message.answer("Напиши свой <b>возраст</b>:")
    except ValueError:
        await message.answer("Пожалуйста, введи число (например, 178):")

@dp.message_handler(state=ProfileStates.age)
async def p_a(message: types.Message, state: FSMContext):
    try:
        age = int(message.text)
        await state.update_data(age=age)
        await ProfileStates.gender.set()
        await message.answer("Выбери свой пол:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("Мужской", "Женский"))
    except ValueError:
        await message.answer("Пожалуйста, укажи возраст целым числом:")

@dp.message_handler(state=ProfileStates.gender)
async def p_g(message: types.Message, state: FSMContext):
    if message.text in ["Мужской", "Женский"]:
        await state.update_data(gender=message.text)
        await ProfileStates.activity.set()
        
        text = (
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
            "<b>Выберите один из вариантов ниже:</b>"
        )
        await message.answer(text, reply_markup=get_activity_keyboard())
    else:
        await message.answer("Используй кнопки для выбора пола!")

@dp.message_handler(state=ProfileStates.activity)
async def p_act(message: types.Message, state: FSMContext):
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
        await message.answer("Выбери цель из предложенных ниже:", reply_markup=get_goals_keyboard())
    else:
        await message.answer("Пожалуйста, выбери активность кнопкой!")

@dp.message_handler(state=ProfileStates.goal)
async def p_goal(message: types.Message, state: FSMContext):
    goal = message.text
    if goal not in ["Похудение (Сушка)", "Поддержание", "Набор массы"]:
        await message.answer("Используй кнопки для выбора цели!")
        return

    data = await state.get_data()
    await state.finish()
    
    w, h, a = float(data['weight']), float(data['height']), int(data['age'])
    kcal, p, f, c = calculate_macros(w, h, a, data['gender'], data['activity'], goal)
    
    save_user_profile(message.from_user.id, w, h, a, data['gender'], data['activity'], goal, kcal, p, f, c)
    await message.answer(
        f"🎉 <b>Профиль успешно создан!</b>\n\n"
        f"🎯 Твоя цель: <b>{goal}</b>\n"
        f"🔥 Твоя норма калорий: <b>{kcal} ккал</b>\n"
        f"🥩 Белков: {p} г | 🥑 Жиров: {f} г | 🌾 Углеводов: {c} г\n"
        f"💧 Норма воды: <b>{calculate_water_target(w)} мл</b>", 
        reply_markup=get_main_keyboard()
    )

# --- ИЗМЕНЕНИЕ ДАННЫХ (БЕЗ изменения цели) ---
@dp.callback_query_handler(lambda c: c.data == "change_profile_data", state='*')
async def cb_change_profile(call: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await UpdateProfileStates.weight.set()
    await call.message.answer("⚙️ Начинаем обновление данных профиля. Твоя текущая цель сохранится!\n\nНапиши актуальный <b>вес (в кг)</b>:")
    await call.message.delete()
    await call.answer()

@dp.message_handler(state=UpdateProfileStates.weight)
async def up_w(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
        await state.update_data(weight=weight)
        await UpdateProfileStates.height.set()
        await message.answer("Напиши актуальный <b>рост (в см)</b>:")
    except ValueError:
        await message.answer("Пожалуйста, укажи число (например, 80.4):")

@dp.message_handler(state=UpdateProfileStates.height)
async def up_h(message: types.Message, state: FSMContext):
    try:
        height = float(message.text.replace(',', '.'))
        await state.update_data(height=height)
        await UpdateProfileStates.age.set()
        await message.answer("Напиши актуальный <b>возраст</b>:")
    except ValueError:
        await message.answer("Пожалуйста, введи число:")

@dp.message_handler(state=UpdateProfileStates.age)
async def up_a(message: types.Message, state: FSMContext):
    try:
        age = int(message.text)
        await state.update_data(age=age)
        await UpdateProfileStates.gender.set()
        await message.answer("Выбери пол:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("Мужской", "Женский"))
    except ValueError:
        await message.answer("Пожалуйста, введи целое число:")

@dp.message_handler(state=UpdateProfileStates.gender)
async def up_g(message: types.Message, state: FSMContext):
    if message.text in ["Мужской", "Женский"]:
        await state.update_data(gender=message.text)
        await UpdateProfileStates.activity.set()
        await message.answer("Выбери коэффициент активности:", reply_markup=get_activity_keyboard())
    else:
        await message.answer("Выбери пол кнопкой!")

@dp.message_handler(state=UpdateProfileStates.activity)
async def up_act(message: types.Message, state: FSMContext):
    mapping = {
        "1.2 – Сидячий": 1.2, 
        "1.3 – Небольшая активность": 1.3, 
        "1.4 – Умеренная активность": 1.4, 
        "1.5 – Высокая активность": 1.5, 
        "1.6 – Очень высокая активность": 1.6
    }
    if message.text not in mapping:
        await message.answer("Выбери коэффициент кнопкой!")
        return

    data = await state.get_data()
    await state.finish()
    
    p_old = get_user_profile(message.from_user.id)
    current_goal = p_old[6] if p_old else "Поддержание"
    
    w, h, a = float(data['weight']), float(data['height']), int(data['age'])
    kcal, p, f, c = calculate_macros(w, h, a, data['gender'], mapping[message.text], current_goal)
    
    save_user_profile(message.from_user.id, w, h, a, data['gender'], mapping[message.text], current_goal, kcal, p, f, c)
    await message.answer(
        f"✅ <b>Параметры профиля обновлены!</b>\n"
        f"Цель сохранена: <b>{current_goal}</b>\n\n"
        f"🔥 Новые калории: <b>{kcal} ккал</b>\n"
        f"Б: {p}г | Ж: {f}г | У: {c}г\n"
        f"💧 Новая норма воды: <b>{calculate_water_target(w)} мл</b>", 
        reply_markup=get_main_keyboard()
    )

# --- ИЗМЕНЕНИЕ ЦЕЛИ ---
@dp.callback_query_handler(lambda c: c.data == "change_goal", state='*')
async def cb_change_goal(call: types.CallbackQuery):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("Похудение (Сушка)", callback_data="set_goal_Похудение (Сушка)"),
        types.InlineKeyboardButton("Поддержание", callback_data="set_goal_Поддержание"),
        types.InlineKeyboardButton("Набор массы", callback_data="set_goal_Набор массы")
    )
    await call.message.edit_text("🎯 Выбери новую цель:", reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("set_goal_"), state='*')
async def cb_set_goal(call: types.CallbackQuery):
    new_goal = call.data.replace("set_goal_", "")
    p = get_user_profile(call.from_user.id)
    if not p:
        await call.message.answer("Сначала заполни анкету!")
        return
    
    weight, height, age, gender, activity = p[1], p[2], p[3], p[4], p[5]
    kcal, b, j, u = calculate_macros(weight, height, age, gender, activity, new_goal)
    
    update_user_goal_db(call.from_user.id, new_goal, kcal, b, j, u)
    await call.message.answer(
        f"🎯 Цель успешно изменена на: <b>{new_goal}</b>!\n"
        f"Параметры КБЖУ пересчитаны:\n"
        f"🔥 <b>Калории:</b> {kcal} ккал\n"
        f"Б: {b}г | Ж: {j}г | У: {u}г", 
        reply_markup=get_main_keyboard()
    )
    await call.message.delete()
    await call.answer()

# --- ТРЕКЕР ВОДЫ ---
@dp.message_handler(lambda m: m.text == "💧 Вода +250мл", state='*')
async def add_water_handler(message: types.Message):
    if not await check_subscription(message):
        return
    p = get_user_profile(message.from_user.id)
    if not p:
        await message.answer("Пожалуйста, сначала заполни профиль через /start!")
        return
        
    add_water_to_log(message.from_user.id, 250)
    current = get_today_water(message.from_user.id)
    target = calculate_water_target(p[1])
    
    await message.answer(f"🥤 Добавлено 250 мл воды!\n💧 За сегодня: <b>{current} / {target} мл</b>")

# --- ПОЛЕЗНЫЕ СОВЕТЫ ---
@dp.message_handler(lambda m: m.text == "💡 Совет дня", state='*')
async def cmd_tip(message: types.Message):
    if not await check_subscription(message):
        return
    tip = random.choice(PP_TIPS)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔄 Другой совет", callback_data="get_next_tip"))
    await message.answer(f"💡 <b>Полезный ПП совет:</b>\n\n{tip}", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "get_next_tip", state='*')
async def cb_next_tip(call: types.CallbackQuery):
    tip = random.choice(PP_TIPS)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔄 Другой совет", callback_data="get_next_tip"))
    try:
        await call.message.edit_text(f"💡 <b>Полезный ПП совет:</b>\n\n{tip}", reply_markup=kb)
    except Exception:
        pass
    await call.answer()

# --- МОЁ БЖУ И ВОДА ---
@dp.message_handler(lambda m: m.text == "📊 Моё БЖУ", state='*')
async def my_macros(message: types.Message):
    if not await check_subscription(message):
        return
        
    p = get_user_profile(message.from_user.id)
    if not p: 
        await ProfileStates.weight.set()
        await message.answer("Сначала заполни профиль. Напиши свой <b>вес (в кг)</b>:")
        return
    
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(kcal), SUM(p), SUM(f), SUM(c) FROM food_log WHERE user_id=? AND date=?", 
                   (message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    res = cursor.fetchone()
    conn.close()
    
    sk, sp, sf, sc = (res[0] or 0), (res[1] or 0), (res[2] or 0), (res[3] or 0)
    current_water = get_today_water(message.from_user.id)
    target_water = calculate_water_target(p[1])
    
    text = (
        f"📊 <b>ПРОГРЕСС НА СЕГОДНЯ:</b>\n\n"
        f"🎯 <b>Цель:</b> {p[6]}\n\n"
        f"🔥 <b>Калории:</b> {int(sk)} / {p[7]} ккал\n"
        f"🥩 <b>Белки:</b> {int(sp)} / {p[8]} г\n"
        f"🥑 <b>Жиры:</b> {int(sf)} / {p[9]} г\n"
        f"🌾 <b>Углеводы:</b> {int(sc)} / {p[10]} г\n\n"
        f"💧 <b>Вода:</b> {current_water} / {target_water} мл"
    )
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🎯 Изменить цель", callback_data="change_goal"),
        types.InlineKeyboardButton("⚙️ Изменить данные", callback_data="change_profile_data")
    )
    
    await message.answer(text, reply_markup=kb)

# --- ГРАФИК ПРОГРЕССА (ЗА 7 ДНЕЙ) ---
@dp.message_handler(lambda m: m.text == "📈 Прогресс", state='*')
async def show_progress(message: types.Message):
    if not await check_subscription(message):
        return
        
    user_id = message.from_user.id
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    
    # Собираем данные за последние 7 дней (включая сегодня)
    dates = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    kcal_values = []
    
    for d in dates:
        cursor.execute("SELECT SUM(kcal) FROM food_log WHERE user_id=? AND date=?", (user_id, d))
        val = cursor.fetchone()[0] or 0
        kcal_values.append(val)
        
    conn.close()

    # Если библиотека Matplotlib доступна на сервере — строим изображение
    if HAS_MATPLOTLIB:
        plt.figure(figsize=(8, 4))
        # Переводим даты в формат ДД.ММ
        short_dates = [datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m") for d in dates]
        
        plt.bar(short_dates, kcal_values, color='#4CAF50', alpha=0.8, edgecolor='#388E3C', width=0.6)
        plt.title('Потребление калорий за последние 7 дней', fontsize=12, fontweight='bold', pad=15)
        plt.xlabel('Дата', fontsize=10, labelpad=10)
        plt.ylabel('ккал', fontsize=10, labelpad=10)
        plt.grid(axis='y', linestyle='--', alpha=0.5)
        
        # Настройка лимитов для красоты
        max_kcal = max(kcal_values) if max(kcal_values) > 0 else 1000
        plt.ylim(0, max_kcal * 1.2)
        
        for i, val in enumerate(kcal_values):
            if val > 0:
                plt.text(i, val + (max_kcal * 0.02), f"{int(val)}", ha='center', fontsize=9, fontweight='bold')

        plt.tight_layout()
        
        # Сохранение в буфер для отправки в Telegram без сохранения файлов на диске
        import io
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150)
        buf.seek(0)
        plt.close()
        
        await message.answer_photo(buf, caption="📈 Твой прогресс калорийности за последние 7 дней!")
    else:
        # Текстовый красивый график, если Matplotlib не установлен
        text = "📈 <b>ТВОЙ ПРОГРЕСС ЗА 7 ДНЕЙ:</b>\n\n"
        for d, val in zip(dates, kcal_values):
            d_formatted = datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m")
            bar = "🟩" * min(10, int(val / 250)) if val > 0 else "⬜️"
            text += f"📅 {d_formatted} | {bar} ({int(val)} ккал)\n"
        await message.answer(text)

# --- ДНЕВНИК ПИТАНИЯ И УДАЛЕНИЕ БЛЮД ---
@dp.message_handler(lambda m: m.text == "📔 Дневник питания", state='*')
async def cmd_diary(message: types.Message):
    if not await check_subscription(message):
        return

    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, food_name, kcal, p, f, c FROM food_log WHERE user_id=? AND date=?", 
                   (message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("Дневник на сегодня пуст. Внеси еду, чтобы наполнить его!")
        return

    text = "📔 <b>ТВОЙ ДНЕВНИК ПИТАНИЯ НА СЕГОДНЯ:</b>\n\n"
    total_kcal = 0
    kb = types.InlineKeyboardMarkup(row_width=1)
    
    for idx, meal in enumerate(rows, 1):
        meal_id, name, kcal, p, f, c = meal
        text += f"{idx}. <b>{name}</b>\n └ {kcal} ккал | Б: {p}г, Ж: {f}г, У: {c}г\n"
        total_kcal += kcal
        kb.add(types.InlineKeyboardButton(f"❌ Удалить {idx}. {name[:15]}...", callback_data=f"del_meal_{meal_id}"))

    text += f"\n<b>Итого за сегодня:</b> {total_kcal} ккал."
    await message.answer(text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("del_meal_"), state='*')
async def cb_delete_meal(call: types.CallbackQuery):
    meal_id = int(call.data.replace("del_meal_", ""))
    delete_food_from_log(meal_id, call.from_user.id)
    await call.answer("Блюдо успешно удалено из дневника!")
    
    # Обновляем дневник на экране
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, food_name, kcal, p, f, c FROM food_log WHERE user_id=? AND date=?", 
                   (call.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await call.message.edit_text("Дневник на сегодня пуст. Все блюда удалены!")
        return

    text = "📔 <b>ТВОЙ ДНЕВНИК ПИТАНИЯ НА СЕГОДНЯ:</b>\n\n"
    total_kcal = 0
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, meal in enumerate(rows, 1):
        m_id, name, kcal, p, f, c = meal
        text += f"{idx}. <b>{name}</b>\n └ {kcal} ккал | Б: {p}г, Ж: {f}г, У: {c}г\n"
        total_kcal += kcal
        kb.add(types.InlineKeyboardButton(f"❌ Удалить {idx}. {name[:15]}...", callback_data=f"del_meal_{m_id}"))

    text += f"\n<b>Итого за сегодня:</b> {total_kcal} ккал."
    await call.message.edit_text(text, reply_markup=kb)

# --- ВНЕСЕНИЕ ЕДЫ ТЕКСТОМ ---
@dp.message_handler(lambda m: m.text == "📝 Внести еду", state='*')
async def food_text_start(message: types.Message):
    if not await check_subscription(message):
        return
    await FoodStates.waiting_for_text.set()
    await message.answer("Напиши текстом, что и сколько ты съел (например: 'Гречка отварная 150г, котлета говяжья 100г'):")

@dp.message_handler(state=FoodStates.waiting_for_text)
async def process_food_text(message: types.Message, state: FSMContext):
    food_desc = message.text
    await message.answer("⏳ Рассчитываю КБЖУ...", reply_markup=get_main_keyboard())
    await state.finish()

    payload = {
        "model": TEXT_MODEL,
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
            js = json.loads(match.group(1))
            add_food_to_log(message.from_user.id, js['name'], js['kcal'], js['p'], js['f'], js['c'])
            clean_answer = answer.split("JSON_DATA")[0].strip()
            await message.answer(clean_answer + "\n\n✅ <b>Блюдо успешно добавлено в дневник!</b>", reply_markup=get_main_keyboard())
        else:
            await message.answer(answer, reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f"⚠️ Ошибка ИИ:\n<code>{str(e)}</code>", reply_markup=get_main_keyboard())

# --- AI АССИСТЕНТ (С ПАМЯТЬЮ) ---
@dp.message_handler(lambda m: m.text == "🤖 AI Ассистент", state='*')
async def ai_start(message: types.Message):
    if not await check_subscription(message):
        return
    await AssistantStates.waiting_for_question.set()
    await message.answer(
        "💬 <b>Режим AI-нутрициолога активен.</b>\n\n"
        "Я помню всю историю нашего диалога и учитываю твои физические параметры!\n"
        "Спрашивай меня о чём угодно. Для выхода нажми кнопку ниже 👇", 
        reply_markup=get_ai_keyboard()
    )

@dp.message_handler(state=AssistantStates.waiting_for_question)
async def ai_chat(message: types.Message, state: FSMContext):
    if message.text == "❌ Завершить диалог":
        await state.finish()
        await message.answer("Диалог завершен. Возвращаю тебя в главное меню.", reply_markup=get_main_keyboard())
        return
    
    msg = await message.answer("⏳ Думаю...")
    p = get_user_profile(message.from_user.id)
    if p:
        user_info = f"Контекст о пользователе: Пол: {p[4]}, Вес: {p[1]} кг, Рост: {p[2]} см, Возраст: {p[3]} лет, Цель: {p[6]}. Нормы КБЖУ: Калории {p[7]}ккал, Б: {p[8]}г, Ж: {p[9]}г, У: {p[10]}г."
    else:
        user_info = "Параметры пользователя еще не заполнены."

    messages = [
        {
            "role": "system", 
            "content": f"Ты высококлассный фитнес-тренер и спортивный диетолог. Отвечай экспертно, вежливо, на русском языке. {user_info}"
        }
    ]

    history = get_chat_history(message.from_user.id, limit=6)
    for role, content in history:
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": message.text})

    try:
        ans = await ask_groq_ai({"model": TEXT_MODEL, "messages": messages, "max_tokens": 1000})
        save_chat_msg(message.from_user.id, "user", message.text)
        save_chat_msg(message.from_user.id, "assistant", ans)
        await message.answer(ans, reply_markup=get_ai_keyboard())
    except Exception as e:
        await message.answer(f"⚠️ Ошибка сети или API:\n<code>{str(e)}</code>", reply_markup=get_ai_keyboard())
    finally:
        await bot.delete_message(message.chat.id, msg.message_id)

# --- Обработка неизвестных сообщений ---
@dp.message_handler(state='*')
async def default_handler(message: types.Message):
    if not await check_subscription(message):
        return
    await message.answer(
        "Я не понял команду. Пожалуйста, используй кнопки меню ниже:",
        reply_markup=get_main_keyboard()
    )

# ============================ ЕЖЕДНЕВНАЯ РАССЫЛКА (10:00) ============================
async def daily_scheduler():
    while True:
        now = datetime.now()
        if now.hour == 10 and now.minute == 0:
            logging.info("Starting daily broadcast to all users...")
            conn = sqlite3.connect("diet_bot.db")
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            users = cursor.fetchall()
            conn.close()
            
            for user in users:
                user_id = user[0]
                try:
                    await bot.send_message(
                        user_id, 
                        "☀️ <b>Доброе утро! Время позаботиться о себе!</b>\n\n"
                        "Не забудь внести свои сегодняшние приёмы пищи в дневник питания 📔, "
                        "а также не забывай пить воду 💧.\n\n"
                        "Стабильность — твой главный союзник на пути к идеальной форме! 💪",
                        reply_markup=get_main_keyboard()
                    )
                    await asyncio.sleep(0.05)  # Лимиты ТГ
                except Exception as e:
                    logging.error(f"Failed to send broadcast to {user_id}: {e}")
            await asyncio.sleep(65)
        await asyncio.sleep(30)

# ================================= ЗАПУСК =================================
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(daily_scheduler())
    executor.start_polling(dp, skip_updates=True)
