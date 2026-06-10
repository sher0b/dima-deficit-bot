
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
# Вставь свои токены сюда или укажи их в переменных окружения:
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "СЮДА_ВСТАВЬ_ТОКЕН_ОТ_BOTFATHER")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "СЮДА_ВСТАВЬ_КЛЮЧ_GROQ")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# АКТУАЛЬНЫЕ МОДЕЛИ GROQ (РАБОЧИЕ И СТАБИЛЬНЫЕ)
TEXT_MODEL = "llama-3.3-70b-versatile" 
VISION_MODEL = "llama-3.2-11b-vision-preview"

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

class FoodStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

class AssistantStates(StatesGroup):
    waiting_for_question = State()

# ================================ БАЗА ДАННЫХ ===============================
def init_db():
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, weight REAL, height REAL, age INTEGER, 
            gender TEXT, activity REAL, goal TEXT, target_kcal INTEGER, 
            target_p INTEGER, target_f INTEGER, target_c INTEGER
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
            date TEXT, food_name TEXT, kcal INTEGER, p INTEGER, f INTEGER, c INTEGER
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

def add_food_to_log(user_id, food_name, kcal, p, f, c):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("INSERT INTO food_log (user_id, date, food_name, kcal, p, f, c) VALUES (?,?,?,?,?,?,?)",
                   (user_id, today, food_name, kcal, p, f, c))
    conn.commit()
    conn.close()

init_db()

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
    kb.add("📸 Фото еды", "📝 Внести еду", "📊 Моё БЖУ", "🤖 AI Ассистент", "📔 Дневник питания", "🔄 Заполнить данные заново")
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

def get_ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("❌ Завершить диалог")
    return kb

# ================================= ОБРАБОТЧИКИ =================================

@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    profile = get_user_profile(message.from_user.id)
    if profile:
        await message.answer(f"👋 С возвращением, <b>{message.from_user.first_name}</b>!", reply_markup=get_main_keyboard())
    else:
        await ProfileStates.weight.set()
        await message.answer("Привет! Давай рассчитаем твой персональный план. Напиши свой <b>вес (в кг)</b>:")

# --- Процесс заполнения анкеты ---
@dp.message_handler(state=ProfileStates.weight)
async def p_w(message: types.Message, state: FSMContext):
    await state.update_data(weight=message.text.replace(',', '.'))
    await ProfileStates.height.set()
    await message.answer("Напиши свой <b>рост (в см)</b>:")

@dp.message_handler(state=ProfileStates.height)
async def p_h(message: types.Message, state: FSMContext):
    await state.update_data(height=message.text)
    await ProfileStates.age.set()
    await message.answer("Напиши свой <b>возраст</b>:")

@dp.message_handler(state=ProfileStates.age)
async def p_a(message: types.Message, state: FSMContext):
    await state.update_data(age=message.text)
    await ProfileStates.gender.set()
    await message.answer("Выбери свой пол:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("Мужской", "Женский"))

@dp.message_handler(state=ProfileStates.gender)
async def p_g(message: types.Message, state: FSMContext):
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
        await message.answer("Выбери свою фитнес-цель:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("Дефицит калорий 📉", "Поддержание веса ⚖️", "Набор массы 📈", "Сушка организма ⚡"))
    else:
        await message.answer("Пожалуйста, воспользуйся кнопками выбора активности!")

@dp.message_handler(state=ProfileStates.goal)
async def p_goal(message: types.Message, state: FSMContext):
    goal = message.text
    if goal not in ["Дефицит калорий 📉", "Поддержание веса ⚖️", "Набор массы 📈", "Сушка организма ⚡"]:
        await message.answer("Используй кнопки для выбора цели!")
        return

    data = await state.get_data()
    await state.finish()
    
    # Расчет КБЖУ (Формула Миффлина-Сан Жеора)
    w, h, a = float(data['weight']), float(data['height']), int(data['age'])
    bmr = (10 * w + 6.25 * h - 5 * a + 5) if data['gender'] == "Мужской" else (10 * w + 6.25 * h - 5 * a - 161)
    tdee = bmr * data['activity']
    
    if "Дефицит" in goal: 
        kcal = tdee * 0.85; p = w * 1.8; f = w * 0.9
    elif "Сушка" in goal: 
        kcal = tdee * 0.8; p = w * 2.2; f = w * 0.8
    elif "Набор" in goal: 
        kcal = tdee * 1.15; p = w * 1.6; f = w * 1.1
    else: 
        kcal = tdee; p = w * 1.6; f = w * 1.0
        
    c = (kcal - (p * 4 + f * 9)) / 4
    
    save_user_profile(message.from_user.id, w, h, a, data['gender'], data['activity'], goal, int(kcal), int(p), int(f), int(c))
    await message.answer(
        f"🎉 <b>Персональный план создан!</b>\n\n"
        f"🎯 Цель: {goal}\n"
        f"🔥 Калории: {int(kcal)} ккал\n"
        f"🥩 Белков: {int(p)} г | 🥑 Жиров: {int(f)} г | 🌾 Углеводов: {int(c)} г", 
        reply_markup=get_main_keyboard()
    )

# --- Режим AI Ассистента ---
@dp.message_handler(lambda m: m.text == "🤖 AI Ассистент", state='*')
async def ai_start(message: types.Message):
    await AssistantStates.waiting_for_question.set()
    await message.answer(
        "💬 <b>Режим AI-нутрициолога активен.</b>\n\n"
        "Задай мне любой вопрос о твоём питании или тренировках.\n"
        "Для выхода нажми кнопку ниже 👇", 
        reply_markup=get_ai_keyboard()
    )

@dp.message_handler(state=AssistantStates.waiting_for_question)
async def ai_chat(message: types.Message, state: FSMContext):
    # ПРОВЕРКА КНОПКИ ВЫХОДА В ПЕРВУЮ ОЧЕРЕДЬ
    if message.text == "❌ Завершить диалог":
        await state.finish()
        await message.answer("Диалог завершен. Возвращаю тебя в главное меню.", reply_markup=get_main_keyboard())
        return
    
    msg = await message.answer("⏳ Составляю ответ...")
    try:
        ans = await ask_groq_ai({"model": TEXT_MODEL, "messages": [{"role": "user", "content": message.text}]})
        await message.answer(ans, reply_markup=get_ai_keyboard())
    except Exception as e:
        await message.answer(f"⚠️ Ошибка сети или API:\n<code>{str(e)}</code>", reply_markup=get_ai_keyboard())
    finally:
        await bot.delete_message(message.chat.id, msg.message_id)

# --- Внесение еды текстом ---
@dp.message_handler(lambda m: m.text == "📝 Внести еду", state='*')
async def food_text_start(message: types.Message):
    await FoodStates.waiting_for_text.set()
    await message.answer("Напиши текстом, что и сколько ты съел (например: 'Овсянка на молоке 200г, банан'):")

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

# --- Фото еды ---
@dp.message_handler(lambda m: m.text == "📸 Фото еды", state='*')
async def food_photo_start(message: types.Message):
    await FoodStates.waiting_for_photo.set()
    await message.answer("Отправь мне фото своего блюда:")

@dp.message_handler(state=FoodStates.waiting_for_photo, content_types=['photo'])
async def process_photo(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("⏳ Анализирую снимок блюда...", reply_markup=get_main_keyboard())
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        photo_bytes = await bot.download_file(file.file_path)
        base64_img = base64.b64encode(photo_bytes.read()).decode('utf-8')
        
        payload = {
            "model": VISION_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Распознай еду на фото, рассчитай КБЖУ. Ответь по-русски. В конце обязательно выведи JSON_DATA: {\"name\": \"название блюда\", \"kcal\": 250, \"p\": 15, \"f\": 8, \"c\": 30}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                ]
            }]
        }
        ans = await ask_groq_ai(payload)
        match = re.search(r'JSON_DATA:\s*(\{.*\})', ans, re.DOTALL)
        if match:
            js = json.loads(match.group(1))
            add_food_to_log(message.from_user.id, js['name'], js['kcal'], js['p'], js['f'], js['c'])
            clean_ans = ans.split("JSON_DATA")[0].strip()
            await message.answer(clean_ans + "\n\n✅ <b>Блюдо успешно распознано и добавлено в дневник!</b>", reply_markup=get_main_keyboard())
        else:
            await message.answer(ans, reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f"⚠️ Ошибка обработки фото:\n<code>{str(e)}</code>", reply_markup=get_main_keyboard())

# --- Дневник питания ---
@dp.message_handler(lambda m: m.text == "📔 Дневник питания", state='*')
async def cmd_diary(message: types.Message):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT food_name, kcal, p, f, c FROM food_log WHERE user_id=? AND date=?", 
                   (message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("Дневник на сегодня пуст. Внеси еду, чтобы наполнить его!")
        return

    text = "📔 <b>ТВОЙ ДНЕВНИК ПИТАНИЯ НА СЕГОДНЯ:</b>\n\n"
    total_kcal = 0
    for idx, meal in enumerate(rows, 1):
        name, kcal, p, f, c = meal
        text += f"{idx}. <b>{name}</b>\n └ {kcal} ккал | Б: {p}г, Ж: {f}г, У: {c}г\n"
        total_kcal += kcal

    text += f"\n<b>Итого за сегодня:</b> {total_kcal} ккал."
    await message.answer(text, reply_markup=get_main_keyboard())

# --- Моё БЖУ ---
@dp.message_handler(lambda m: m.text == "📊 Моё БЖУ", state='*')
async def my_macros(message: types.Message):
    p = get_user_profile(message.from_user.id)
    if not p: 
        await message.answer("Сначала заполни анкету, нажав '🔄 Заполнить данные заново'!")
        return
    
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(kcal), SUM(p), SUM(f), SUM(c) FROM food_log WHERE user_id=? AND date=?", 
                   (message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    res = cursor.fetchone()
    conn.close()
    
    sk, sp, sf, sc = (res[0] or 0), (res[1] or 0), (res[2] or 0), (res[3] or 0)
    
    await message.answer(
        f"📊 <b>ПРОГРЕСС НА СЕГОДНЯ:</b>\n\n"
        f"🎯 <b>Цель:</b> {p[6]}\n\n"
        f"🔥 <b>Калории:</b> {int(sk)} / {p[7]} ккал\n"
        f"🥩 <b>Белки:</b> {int(sp)} / {p[8]} г\n"
        f"🥑 <b>Жиры:</b> {int(sf)} / {p[9]} г\n"
        f"🌾 <b>Углеводы:</b> {int(sc)} / {p[10]} г", 
        reply_markup=get_main_keyboard()
    )

# --- Перезапуск данных ---
@dp.message_handler(lambda m: m.text == "🔄 Заполнить данные заново", state='*')
async def reset_data(message: types.Message, state: FSMContext):
    await state.finish()
    await ProfileStates.weight.set()
    await message.answer("Окей, давай обновим твои данные. Напиши свой <b>вес (в кг)</b>:")

# --- Эхо на неизвестные сообщения ---
@dp.message_handler(state='*')
async def default_handler(message: types.Message):
    await message.answer(
        "Я не понял команду. Пожалуйста, выбери один из пунктов меню на клавиатуре ниже:",
        reply_markup=get_main_keyboard()
    )

# ================================= ЗАПУСК =================================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
