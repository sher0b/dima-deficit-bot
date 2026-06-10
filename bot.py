
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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ТВОЙ_ТОКЕН_ОТ_BOTFATHER")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "ТВОЙ_КЛЮЧ_GROQ")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

# ================================ БАЗА ДАННЫХ ===============================
def init_db():
    conn = sqlite3.connect("bot_diet.db")
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, weight REAL, height REAL, age INTEGER, gender TEXT, activity REAL, goal TEXT, target_kcal INTEGER, target_p INTEGER, target_f INTEGER, target_c INTEGER)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS food_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, food_name TEXT, kcal INTEGER, p INTEGER, f INTEGER, c INTEGER)""")
    conn.commit()
    conn.close()

init_db()

# ================================ КЛАВИАТУРЫ ================================
def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📸 Фото еды", "📝 Внести еду", "📊 Моё БЖУ", "🤖 AI Ассистент", "📔 Дневник питания", "🔄 Заполнить данные заново")
    return kb

def get_activity_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add("1.2 – Сидячий", "1.3 – Небольшая", "1.4 – Умеренная", "1.5 – Высокая", "1.6 – Очень высокая", "🏠 Меню")
    return kb

def get_ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("❌ Завершить диалог")
    return kb

# ================================ ЛОГИКА ================================
async def ask_groq_ai(payload: dict) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(GROQ_API_URL, json=payload, headers=headers) as resp:
            data = await resp.json()
            return data['choices'][0]['message']['content']

# ================================= ХЕНДЛЕРЫ =================================
@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("👋 Привет! Я твой AI-нутрициолог. Давай настроим профиль.", reply_markup=get_main_keyboard())
    await ProfileStates.weight.set()
    await message.answer("Шаг 1: Напиши свой вес (кг):")

@dp.message_handler(state=ProfileStates.activity)
async def process_activity(message: types.Message, state: FSMContext):
    mapping = {"1.2 – Сидячий": 1.2, "1.3 – Небольшая": 1.375, "1.4 – Умеренная": 1.55, "1.5 – Высокая": 1.725, "1.6 – Очень высокая": 1.9}
    if message.text in mapping:
        await state.update_data(activity=mapping[message.text])
        await ProfileStates.goal.set()
        await message.answer("Отлично! Выбери цель:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("Дефицит калорий 📉", "Поддержание веса ⚖️", "Набор массы 📈", "Сушка организма ⚡"))
    else:
        await message.answer("Выбери вариант из списка кнопок.")

@dp.message_handler(lambda m: m.text == "🤖 AI Ассистент", state='*')
async def ai_start(message: types.Message, state: FSMContext):
    await AssistantStates.waiting_for_question.set()
    await message.answer("💬 Режим консультации. Задавай вопросы!", reply_markup=get_ai_keyboard())

@dp.message_handler(state=AssistantStates.waiting_for_question)
async def ai_chat(message: types.Message, state: FSMContext):
    if message.text == "❌ Завершить диалог":
        await state.finish()
        await message.answer("Диалог завершен.", reply_markup=get_main_keyboard())
        return
    
    msg = await message.answer("⏳ Думаю...")
    try:
        ans = await ask_groq_ai({"model": "llama-3.2-11b-vision-preview", "messages": [{"role": "user", "content": message.text}]})
        await message.answer(ans)
        await bot.delete_message(message.chat.id, msg.message_id)
    except:
        await message.answer("Ошибка связи.")

# --- Состояния ---
class ProfileStates(StatesGroup):
    weight, height, age, gender, activity, goal = State(), State(), State(), State(), State(), State()

class AssistantStates(StatesGroup):
    waiting_for_question = State()

# (Остальные хендлеры: веса, роста, еды и т.д. сохраняются из прошлого кода)
# ...

@dp.message_handler(state=ProfileStates.weight)
async def p_w(message: types.Message, state: FSMContext):
    await state.update_data(weight=message.text)
    await ProfileStates.next()
    await message.answer("Шаг 2: Рост (см):")

@dp.message_handler(state=ProfileStates.height)
async def p_h(message: types.Message, state: FSMContext):
    await state.update_data(height=message.text)
    await ProfileStates.next()
    await message.answer("Шаг 3: Возраст:")

@dp.message_handler(state=ProfileStates.age)
async def p_a(message: types.Message, state: FSMContext):
    await state.update_data(age=message.text)
    await ProfileStates.next()
    await message.answer("Шаг 4: Пол:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("Мужской", "Женский"))

@dp.message_handler(state=ProfileStates.gender)
async def p_g(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text)
    await ProfileStates.next()
    await message.answer(
        "<b>Пожалуйста, выберите коэффициент активности:</b>\n\n"
        "• 1.2 – сидячий: Минимум движений, офис, <5к шагов.\n"
        "• 1.3 – небольшая: 5–8к шагов, легкие тренировки.\n"
        "• 1.4 – умеренная: 8–12к шагов, 2-3 тренировки.\n"
        "• 1.5 – высокая: 12к+ шагов, 3-5 тренировок.\n"
        "• 1.6 – очень высокая: Тяжелый труд + ежедневный спорт.",
        reply_markup=get_activity_keyboard()
    )

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
