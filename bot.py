
import os
import logging
import sqlite3
import re
import json
import asyncio
import random
from datetime import datetime, timedelta
import aiohttp
import io

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# Попытка импорта matplotlib для графиков
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# ================================ КОНФИГУРАЦИЯ =================================
TELEGRAM_TOKEN = "ВСТАВЬ_ТОКЕН_BOTFATHER"
GROQ_API_KEY = "ВСТАВЬ_КЛЮЧ_GROQ"
CHANNEL_ID = "@твой_канал"
CHANNEL_URL = "https://t.me/твой_канал"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
TEXT_MODEL = "llama-3.3-70b-versatile" 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ================================ СОСТОЯНИЯ (FSM) ===============================
class ProfileStates(StatesGroup):
    weight = State(); height = State(); age = State(); gender = State(); activity = State(); goal = State()

class UpdateProfileStates(StatesGroup):
    weight = State(); height = State(); age = State(); gender = State(); activity = State()

class UpdateWeightState(StatesGroup):
    new_weight = State()

class FoodStates(StatesGroup):
    waiting_for_text = State()

class AssistantStates(StatesGroup):
    waiting_for_question = State()

# ================================ БАЗА ДАННЫХ ===============================
def init_db():
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, weight REAL, height REAL, age INTEGER, gender TEXT, activity REAL, goal TEXT, target_kcal INTEGER, target_p INTEGER, target_f INTEGER, target_c INTEGER)")
    cursor.execute("CREATE TABLE IF NOT EXISTS food_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, food_name TEXT, kcal INTEGER, p INTEGER, f INTEGER, c INTEGER)")
    cursor.execute("CREATE TABLE IF NOT EXISTS water_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, amount INTEGER)")
    cursor.execute("CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, timestamp TEXT)")
    conn.commit(); conn.close()

def save_user_profile(user_id, weight, height, age, gender, activity, goal, kcal, p, f, c):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?)", (user_id, weight, height, age, gender, activity, goal, kcal, p, f, c))
    conn.commit(); conn.close()

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
    cursor.execute("INSERT INTO food_log (user_id, date, food_name, kcal, p, f, c) VALUES (?,?,?,?,?,?,?)", (user_id, datetime.now().strftime("%Y-%m-%d"), food_name, kcal, p, f, c))
    conn.commit(); conn.close()

def add_water_to_log(user_id, amount):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO water_log (user_id, date, amount) VALUES (?,?,?)", (user_id, datetime.now().strftime("%Y-%m-%d"), amount))
    conn.commit(); conn.close()

def get_today_water(user_id):
    conn = sqlite3.connect("diet_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(amount) FROM water_log WHERE user_id = ? AND date = ?", (user_id, datetime.now().strftime("%Y-%m-%d")))
    res = cursor.fetchone()[0]
    conn.close()
    return res or 0

init_db()

# ============================ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============================
def calculate_macros(weight, height, age, gender, activity, goal):
    bmr = (10 * weight + 6.25 * height - 5 * age + 5) if gender == "Мужской" else (10 * weight + 6.25 * height - 5 * age - 161)
    tdee = bmr * activity
    if goal == "Похудение (Сушка)": kcal = tdee * 0.82; p = weight * 2.0; f = weight * 0.8
    elif goal == "Набор массы": kcal = tdee * 1.15; p = weight * 1.7; f = weight * 1.1
    else: kcal = tdee; p = weight * 1.6; f = weight * 1.0
    c = max(0, (kcal - (p * 4 + f * 9)) / 4)
    return int(kcal), int(p), int(f), int(c)

def calculate_water_target(weight): return int(weight * 35)

def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📝 Внести еду", "📊 Моё БЖУ", "👤 Мой профиль", "📔 Дневник питания", "💡 Совет дня", "💧 Вода +250мл", "📈 Прогресс", "🤖 AI Ассистент")
    return kb

# ============================ ОБРАБОТЧИКИ ============================
@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    if get_user_profile(message.from_user.id):
        await message.answer("С возвращением!", reply_markup=get_main_keyboard())
    else:
        await ProfileStates.weight.set()
        await message.answer("Привет! Давай создадим профиль. Введи свой вес (кг):")

@dp.message_handler(lambda m: m.text == "👤 Мой профиль", state='*')
async def show_profile(message: types.Message):
    p = get_user_profile(message.from_user.id)
    text = (f"👤 <b>ТВОЙ ПРОФИЛЬ:</b>\n\n⚖️ Вес: {p[1]} кг\n📏 Рост: {p[2]} см\n🎂 Возраст: {p[3]} лет\n🎯 Цель: {p[6]}\n💧 Норма воды: {calculate_water_target(p[1])} мл\n🔥 Калории: {p[7]} ккал")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⚖️ Обновить вес", callback_data="update_weight_start"), types.InlineKeyboardButton("⚙️ Изменить все данные", callback_data="change_profile_data"))
    await message.answer(text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "update_weight_start", state='*')
async def update_weight_start(call: types.CallbackQuery):
    await UpdateWeightState.new_weight.set()
    await call.message.answer("Введи новый вес (кг):")

@dp.message_handler(state=UpdateWeightState.new_weight)
async def process_new_weight(message: types.Message, state: FSMContext):
    new_w = float(message.text.replace(',', '.'))
    p = get_user_profile(message.from_user.id)
    k, pr, f, c = calculate_macros(new_w, p[2], p[3], p[4], p[5], p[6])
    save_user_profile(message.from_user.id, new_w, p[2], p[3], p[4], p[5], p[6], k, pr, f, c)
    await state.finish()
    await message.answer(f"✅ Вес обновлен! Новые нормы: {k} ккал и {calculate_water_target(new_w)} мл воды.", reply_markup=get_main_keyboard())

# --- Остальные хэндлеры (БЖУ, еда, вода, прогресс) логика остается прежней из прошлого кода ---
# ... (код для остальных кнопок: water, diary, assistant, tips) ...

@dp.message_handler(lambda m: m.text == "📈 Прогресс", state='*')
async def show_progress(message: types.Message):
    # Генерация графика
    conn = sqlite3.connect("diet_bot.db")
    dates = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    vals = [conn.cursor().execute("SELECT SUM(kcal) FROM food_log WHERE user_id=? AND date=?", (message.from_user.id, d)).fetchone()[0] or 0 for d in dates]
    conn.close()
    
    if HAS_MATPLOTLIB:
        plt.figure(figsize=(8, 4))
        plt.bar([d[5:] for d in dates], vals, color='#4CAF50')
        plt.title('Калории за 7 дней')
        buf = io.BytesIO()
        plt.savefig(buf, format='png'); buf.seek(0)
        await message.answer_photo(buf)
    else:
        await message.answer("График: " + ", ".join([str(v) for v in vals]))

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
