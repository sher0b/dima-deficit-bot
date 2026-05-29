
import os
import asyncio
import logging
import aiohttp
import random
import sqlite3
from datetime import datetime, date

try:
    import psycopg2
except ImportError:
    psycopg2 = None

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from aiogram.utils.exceptions import MessageNotModified
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# 🔑 ТОКЕНЫ И КОНФИГУРАЦИЯ (УМНАЯ)
# =========================
# Получаем токен из переменных или ставим заглушку
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ТВОЙ_ТОКЕН_ЗДЕСЬ")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "ТВОЙ_КЛЮЧ_ЗДЕСЬ")

# Безопасное получение ADMIN_ID
admin_id_env = os.getenv("ADMIN_ID")
if admin_id_env and admin_id_env.isdigit():
    ADMIN_ID = int(admin_id_env)
else:
    ADMIN_ID = 0  # Если в Railway пусто, ставим 0, чтобы не было ошибки

CHANNEL_ID = "@dimadeficit"
CHANNEL_URL = "https://t.me/dimadeficit"

bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

DATABASE_URL = os.getenv("DATABASE_URL")

# =========================
# 🗄 РАБОТА С БАЗОЙ ДАННЫХ
# =========================
class Database:
    def __init__(self):
        self.is_postgres = DATABASE_URL is not None and DATABASE_URL.startswith("postgres")
        self.init_db()

    def get_connection(self):
        if self.is_postgres:
            if psycopg2 is None:
                raise ImportError("Установите psycopg2-binary для работы с PostgreSQL!")
            url = DATABASE_URL
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            return psycopg2.connect(url)
        else:
            return sqlite3.connect("bot.db")

    def execute(self, query, params=(), fetch=False, fetchone=False):
        """Универсальный метод выполнения запросов для Postgres и SQLite"""
        conn = self.get_connection()
        if not self.is_postgres:
            # Превращаем плейсхолдеры %s от Postgres в ? для SQLite
            query = query.replace("%s", "?")
        
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            if fetch:
                res = cursor.fetchall()
            elif fetchone:
                res = cursor.fetchone()
            else:
                res = None
            conn.commit()
            return res
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка БД: {e} | Запрос: {query}")
            raise e
        finally:
            cursor.close()
            conn.close()

    def init_db(self):
        # Таблица пользователей
        self.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                gender TEXT,
                age INTEGER,
                height REAL,
                weight REAL,
                goal TEXT,
                is_pro BOOLEAN DEFAULT FALSE,
                ai_requests_left INTEGER DEFAULT 10,
                is_interested BOOLEAN DEFAULT FALSE
            )
        """)
        # Таблица взвешиваний
        self.execute("""
            CREATE TABLE IF NOT EXISTS weights (
                user_id BIGINT,
                weight REAL,
                log_date TEXT
            )
        """)
        # Таблица калорий
        self.execute("""
            CREATE TABLE IF NOT EXISTS calories (
                user_id BIGINT,
                log_date TEXT,
                calories INTEGER,
                PRIMARY KEY (user_id, log_date)
            )
        """)

    def get_user(self, user_id):
        row = self.execute(
            "SELECT user_id, gender, age, height, weight, goal, is_pro, ai_requests_left, is_interested FROM users WHERE user_id = %s",
            (user_id,), fetchone=True
        )
        if row:
            return {
                "user_id": row[0],
                "gender": row[1],
                "age": row[2],
                "height": row[3],
                "weight": row[4],
                "goal": row[5],
                "is_pro": bool(row[6]),
                "ai_requests_left": row[7],
                "is_interested": bool(row[8])
            }
        return None

    def save_user_profile(self, user_id, gender, age, height, weight, goal):
        user = self.get_user(user_id)
        if user:
            self.execute(
                "UPDATE users SET gender = %s, age = %s, height = %s, weight = %s, goal = %s WHERE user_id = %s",
                (gender, age, height, weight, goal, user_id)
            )
        else:
            self.execute(
                "INSERT INTO users (user_id, gender, age, height, weight, goal) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, gender, age, height, weight, goal)
            )

    def update_user_weight(self, user_id, weight):
        self.execute("UPDATE users SET weight = %s WHERE user_id = %s", (weight, user_id))

    def update_user_pro(self, user_id, is_pro, ai_requests_left):
        user = self.get_user(user_id)
        if user:
            self.execute("UPDATE users SET is_pro = %s, ai_requests_left = %s WHERE user_id = %s", (is_pro, ai_requests_left, user_id))
        else:
            self.execute("INSERT INTO users (user_id, is_pro, ai_requests_left) VALUES (%s, %s, %s)", (user_id, is_pro, ai_requests_left))

    def update_user_interest(self, user_id, is_interested):
        user = self.get_user(user_id)
        if user:
            self.execute("UPDATE users SET is_interested = %s WHERE user_id = %s", (is_interested, user_id))
        else:
            self.execute("INSERT INTO users (user_id, is_interested) VALUES (%s, %s)", (user_id, is_interested))

    def add_weight(self, user_id, weight, log_date_str):
        self.execute("INSERT INTO weights (user_id, weight, log_date) VALUES (%s, %s, %s)", (user_id, weight, log_date_str))

    def get_weights(self, user_id):
        rows = self.execute("SELECT weight, log_date FROM weights WHERE user_id = %s ORDER BY log_date ASC", (user_id,), fetch=True)
        result = []
        for r in rows:
            if isinstance(r[1], str):
                d = datetime.strptime(r[1], "%Y-%m-%d").date()
            else:
                d = r[1]
            result.append((r[0], d))
        return result

    def add_calories(self, user_id, log_date_str, calories):
        row = self.execute("SELECT calories FROM calories WHERE user_id = %s AND log_date = %s", (user_id, log_date_str), fetchone=True)
        if row:
            new_cals = row[0] + calories
            self.execute("UPDATE calories SET calories = %s WHERE user_id = %s AND log_date = %s", (new_cals, user_id, log_date_str))
        else:
            self.execute("INSERT INTO calories (user_id, log_date, calories) VALUES (%s, %s, %s)", (user_id, log_date_str, calories))

    def get_calories(self, user_id, log_date_str):
        row = self.execute("SELECT calories FROM calories WHERE user_id = %s AND log_date = %s", (user_id, log_date_str), fetchone=True)
        return row[0] if row else 0

    def reset_calories(self, user_id, log_date_str):
        self.execute("DELETE FROM calories WHERE user_id = %s AND log_date = %s", (user_id, log_date_str))

    def get_active_users(self):
        rows = self.execute("SELECT user_id FROM users", fetch=True)
        return [r[0] for r in rows]

    def get_interested_users(self):
        # В Postgres логические значения TRUE/FALSE, в SQLite это 1/0
        rows = self.execute("SELECT user_id FROM users WHERE is_interested = %s OR is_interested = 1", (True,), fetch=True)
        return [r[0] for r in rows]

    def count_pro_users(self):
        row = self.execute("SELECT COUNT(*) FROM users WHERE is_pro = %s OR is_pro = 1", (True,), fetchone=True)
        return row[0] if row else 0

    def reset_user_data(self, user_id):
        self.execute("UPDATE users SET gender=NULL, age=NULL, height=NULL, weight=NULL, goal=NULL WHERE user_id = %s", (user_id,))
        self.execute("DELETE FROM weights WHERE user_id = %s", (user_id,))
        self.execute("DELETE FROM calories WHERE user_id = %s", (user_id,))

db = Database()

# Локальная память
user_memory = {}       # Память ИИ
sub_cache = {}         # Кэш подписок

GOALS = {
    "cut": "🍎 Сушка (Дефицит)",
    "maintain": "🍽 Поддержание",
    "bulk": "💪🏻 Набор массы"
}

GENDERS = {
    "male": "👨 Мужской",
    "female": "👩 Женский"
}

DAILY_TIPS = [
    "❤️ *Челлендж дня:* Сделай сегодня на 2000 шагов больше обычного. Это сожжет около 100 ккал!",
    "🍎 *Инсайт дня:* Белок насыщает лучше всего. Добавь к приему пищи 100г куриного филе.",
    "💧 *Микро-правило:* Выпивай стакан чистой воды за 15 минут до еды."
]

# =========================
# 📝 СОСТОЯНИЯ (FSM)
# =========================
class RegistrationStates(StatesGroup):
    gender, age, height, weight, goal = State(), State(), State(), State(), State()

class MenuStates(StatesGroup):
    updating_weight, logging_calories, ai_chat = State(), State(), State()

# =========================
# ⚡️ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================
async def is_subscribed(user_id: int) -> bool:
    uid = str(user_id)
    now = datetime.now().timestamp()
    if uid in sub_cache and (now - sub_cache[uid]) < 300: return True 
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status != 'left':
            sub_cache[uid] = now
            return True
        return False
    except Exception: return True 

def sub_keyboard():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("📢 Подписаться", url=CHANNEL_URL),
        InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription")
    )

def navigation_bar():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("⏮ Назад"), KeyboardButton("🏠 В главное меню"))
    kb.row(KeyboardButton("⚙️ Заполнить заново"))
    return kb

def main_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("❤️ Мой Профиль", callback_data="menu_profile"),
        InlineKeyboardButton("🍎 Дневник калорий", callback_data="menu_calories"),
        InlineKeyboardButton("💪🏻 Записать вес", callback_data="menu_weight"),
        InlineKeyboardButton("🍽 Расчёт КБЖУ", callback_data="menu_food"),
        InlineKeyboardButton("💬 AI Ассистент", callback_data="menu_ai"),
        InlineKeyboardButton("😇 Польза дня", callback_data="menu_benefit"),
        InlineKeyboardButton("💳 PRO-система", callback_data="menu_pro_info"),
    )
    return kb

# =========================
# 🛡 АДМИН-ПАНЕЛЬ
# =========================
@dp.message_handler(commands=["admin"])
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return 

    total = len(db.get_active_users())
    interested = len(db.get_interested_users())
    pro_count = db.count_pro_users()
    
    report = (
        "📊 *СТАТИСТИКА БОТА:*\n\n"
        f"👤 Всего пользователей в БД: {total}\n"
        f"🔥 Проявили интерес к PRO: {interested}\n"
        f"💳 Действующих PRO: {pro_count}\n\n"
        f"ID заинтересованных: `{db.get_interested_users()}`"
    )
    await message.answer(report, parse_mode="Markdown")

# =========================
# 🧭 БЕЗОПАСНЫЙ СБРОС ДАННЫХ
# =========================
async def safe_reset_user_profile(user_id: int, state: FSMContext):
    db.reset_user_data(int(user_id))
    user_memory.pop(str(user_id), None)
    await state.finish()

# =========================
# 🛠 КОМАНДЫ
# =========================
@dp.message_handler(commands=["sherobthebest"], state="*")
async def cmd_secret_grant_pro(message: types.Message):
    uid = int(message.from_user.id)
    db.update_user_pro(uid, True, 999999)
    await message.answer("🤫 PRO активирован навсегда (секретный код).")

@dp.message_handler(commands=["tankthebest"], state="*")
async def cmd_secret_revoke_pro(message: types.Message):
    uid = int(message.from_user.id)
    db.update_user_pro(uid, False, 10)
    await message.answer("🤫 PRO удален (секретный код).")

@dp.message_handler(commands=["reset"], state="*")
async def cmd_reset(message: types.Message, state: FSMContext):
    await safe_reset_user_profile(message.from_user.id, state)
    await message.answer("🔄 Анкета сброшена.", reply_markup=navigation_bar())
    await message.answer("Выбери пол 👇", reply_markup=gender_kb())
    await RegistrationStates.gender.set()

@dp.message_handler(commands=["start"], state="*")
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    uid = int(message.from_user.id)
    
    if not await is_subscribed(uid):
        return await message.answer(f"Подпишись на {CHANNEL_ID}", reply_markup=sub_keyboard())

    u = db.get_user(uid)
    is_complete = u is not None and all(u.get(k) is not None for k in ["gender", "age", "height", "weight", "goal"])
    
    if is_complete:
        await message.answer("🏠 Главное меню:", reply_markup=main_menu_kb())
    else:
        if not u:
            db.update_user_pro(uid, False, 10)
        await message.answer("Давай настроим профиль. Твой пол:", reply_markup=navigation_bar())
        await message.answer("Выбери пол 👇", reply_markup=gender_kb())
        await RegistrationStates.gender.set()

# =========================
# 🧭 ГЛОБАЛЬНАЯ НАВИГАЦИЯ
# =========================
@dp.message_handler(lambda m: m.text in ["⏮ Назад", "🏠 В главное меню", "⚙️ Заполнить заново"], state="*")
async def global_navigation(message: types.Message, state: FSMContext):
    uid = int(message.from_user.id)
    if message.text == "⚙️ Заполнить заново":
        await safe_reset_user_profile(uid, state)
        await message.answer("Выбери пол:", reply_markup=gender_kb())
        await RegistrationStates.gender.set()
        return
    
    await state.finish()
    await message.answer("🏠 Главное меню:", reply_markup=main_menu_kb())

# =========================
# 📋 РЕГИСТРАЦИЯ
# =========================
def gender_kb():
    return InlineKeyboardMarkup().add(
        InlineKeyboardButton("👨 Мужской", callback_data="reg_gender_male"),
        InlineKeyboardButton("👩 Женский", callback_data="reg_gender_female")
    )

@dp.callback_query_handler(lambda c: c.data.startswith("reg_gender_"), state=RegistrationStates.gender)
async def reg_gender(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    gender = call.data.split("_")[2]
    await state.update_data(gender=gender)
    await call.message.edit_text("🔢 Шаг 2 из 5:\nУкажи свой возраст:")
    await RegistrationStates.age.set()

@dp.message_handler(state=RegistrationStates.age)
async def reg_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠️ Введи возраст цифрами:")
    await state.update_data(age=int(message.text))
    await message.answer("📏 Шаг 3 из 5:\nВведи рост (см):")
    await RegistrationStates.height.set()

@dp.message_handler(state=RegistrationStates.height)
async def reg_height(message: types.Message, state: FSMContext):
    try:
        h = float(message.text.replace(",", "."))
        await state.update_data(height=h)
        await message.answer("⚖️ Шаг 4 из 5:\nВведи текущий вес (кг):")
        await RegistrationStates.weight.set()
    except ValueError:
        await message.answer("⚠️ Введи рост числом:")

@dp.message_handler(state=RegistrationStates.weight)
async def reg_weight(message: types.Message, state: FSMContext):
    try:
        w = float(message.text.replace(",", "."))
        await state.update_data(weight=w)
        
        kb = InlineKeyboardMarkup(row_width=1)
        for k, v in GOALS.items(): 
            kb.add(InlineKeyboardButton(v, callback_data=f"reg_goal_{k}"))
        await message.answer("🎯 Шаг 5 из 5:\nВыбери цель:", reply_markup=kb)
        await RegistrationStates.goal.set()
    except ValueError:
        await message.answer("⚠️ Введи вес числом:")

@dp.callback_query_handler(lambda c: c.data.startswith("reg_goal_"), state=RegistrationStates.goal)
async def reg_goal(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    goal = call.data.split("_")[2]
    uid = int(call.from_user.id)
    
    data = await state.get_data()
    gender = data.get("gender")
    age = data.get("age")
    height = data.get("height")
    weight = data.get("weight")
    
    # Сохраняем все данные в базу!
    db.save_user_profile(uid, gender, age, height, weight, goal)
    db.add_weight(uid, weight, date.today().strftime("%Y-%m-%d"))
    
    await state.finish()
    await call.message.delete()
    await call.message.answer("🎉 Профиль успешно создан и интегрирован в систему!", reply_markup=main_menu_kb())

# =========================
# 📊 МЕНЮ И ЛОГИКА
# =========================
def calc_kcal(w, h, a, g, goal):
    bmr = 10 * w + 6.25 * h - 5 * a + (5 if g == "male" else -161)
    tdee = bmr * 1.375
    if goal == "cut": return int(tdee - 400)
    if goal == "bulk": return int(tdee + 300)
    return int(tdee)

@dp.callback_query_handler(state="*")
async def process_menu_clicks(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    uid = int(call.from_user.id)
    code = call.data
    today_str = date.today().strftime("%Y-%m-%d")

    u = db.get_user(uid)
    if not u:
        db.update_user_pro(uid, False, 10)
        await call.message.answer("⚠️ Давай пройдем регистрацию!", reply_markup=navigation_bar())
        await call.message.answer("Выбери пол 👇", reply_markup=gender_kb())
        await RegistrationStates.gender.set()
        return

    try:
        if code == "menu_pro_info":
            if u.get("is_pro"):
                await call.message.edit_text("❤️ У тебя уже есть PRO-доступ!", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu")))
            else:
                kb = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔥 Нажми, если хочешь купить", callback_data="register_interest")
                ).add(InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu"))
                
                await call.message.edit_text(
                    "💳 *PRO-СИСТЕМА РЕЗУЛЬТАТА*\n\n"
                    "Сейчас идёт закрытый тест системы. Ранний доступ с дневником калорий, контролем веса и безлимитным AI откроется совсем скоро.\n\n"
                    "💰 Предварительная цена: *100 руб (навсегда)*\n\n"
                    "Если ты хочешь получить доступ первым — нажми кнопку ниже 👇",
                    parse_mode="Markdown", reply_markup=kb
                )

        elif code == "register_interest":
            db.update_user_interest(uid, True)
            await call.message.edit_text("🎉 Ты в списке ожидания! Мы сообщим тебе лично, как только PRO станет доступен.", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu")))

        elif code == "menu_profile":
            kcal = calc_kcal(u["weight"], u["height"], u["age"], u["gender"], u["goal"])
            status = "💳 PRO-Система" if u.get("is_pro") else f"🆓 Лимит AI: {u.get('ai_requests_left')} запр."
            
            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton("🎯 Сменить цель", callback_data="action_change_goal"),
                InlineKeyboardButton("🏠 В главное меню", callback_data="back_to_menu")
            )
            await call.message.edit_text(
                f"❤️ ТВОЙ ПРОФИЛЬ:\n\n"
                f"👤 Пол: {GENDERS[u['gender']]}\n"
                f"🎂 Возраст: {u['age']} лет\n"
                f"📏 Рост: {u['height']} см\n"
                f"⚖️ Вес: {u['weight']} кг\n\n"
                f"🎯 Цель: {GOALS[u['goal']]}\n"
                f"🍎 Норма калорий: {kcal} ккал/день\n"
                f"💳 Статус: {status}", reply_markup=kb
            )

        elif code == "action_change_goal":
            kb = InlineKeyboardMarkup(row_width=1)
            for k, v in GOALS.items():
                kb.add(InlineKeyboardButton(v, callback_data=f"edit_goal_{k}"))
            await call.message.edit_text("🎯 Выбери новую цель:", reply_markup=kb)

        elif code.startswith("edit_goal_"):
            new_g = code.split("_")[2]
            db.save_user_profile(uid, u["gender"], u["age"], u["height"], u["weight"], new_g)
            await call.message.answer("✅ Цель изменена!")
            call.data = "menu_profile"
            await process_menu_clicks(call, state)

        elif code == "menu_food":
            kcal = calc_kcal(u["weight"], u["height"], u["age"], u["gender"], u["goal"])
            p = int(u["weight"] * (2.2 if u["goal"] == "cut" else 1.8 if u["goal"] == "bulk" else 2.0))
            f = int(u["weight"] * (0.9 if u["goal"] == "cut" else 1.1 if u["goal"] == "bulk" else 1.0))
            c = int(max(0, (kcal - (p * 4 + f * 9)) / 4))
            
            kb = InlineKeyboardMarkup().add(InlineKeyboardButton("🏠 В главное меню", callback_data="back_to_menu"))
            await call.message.edit_text(
                f"🍽 РАСЧЕТ КБЖУ:\n\n"
                f"🎯 Цель: {GOALS[u['goal']]}\n"
                f"🍎 Калории: {kcal} ккал\n"
                f"🧬 Белки: {p} г\n"
                f"🧈 Жиры: {f} г\n"
                f"🌾 Углеводы: {c} г", reply_markup=kb
            )

        elif code == "menu_calories":
            if not u.get("is_pro"):
                kb = InlineKeyboardMarkup(row_width=1).add(
                    InlineKeyboardButton("💳 Подключить PRO", callback_data="menu_pro_info"),
                    InlineKeyboardButton("🏠 В главное меню", callback_data="back_to_menu")
                )
                await call.message.edit_text(
                    "🔒 *ДНЕВНИК КАЛОРИЙ И КОНТРОЛЬ*\n\n"
                    "Эта функция доступна только участникам **PRO-системы**.\n\n"
                    "Без ежедневного контроля съеденного невозможно увидеть реальный прогресс 😔",
                    parse_mode="Markdown", reply_markup=kb
                )
                return
            
            target_kcal = calc_kcal(u["weight"], u["height"], u["age"], u["gender"], u["goal"])
            eaten_kcal = db.get_calories(uid, today_str)
            remaining = target_kcal - eaten_kcal
            
            feedback = ""
            if eaten_kcal == 0:
                feedback = "🍎 Дневник пуст. Запиши свой первый прием пищи за сегодня."
            elif remaining > 0:
                feedback = f"❤️ Отлично! Ты остаешься в дефиците на *{remaining} ккал*."
            else:
                feedback = f"😔 *Внимание!*\nТы переел норму дефицита на *{abs(remaining)} ккал*."

            kb = InlineKeyboardMarkup(row_width=1).add(
                InlineKeyboardButton("🍎 Добавить калории", callback_data="add_cal_calories"),
                InlineKeyboardButton("🔄 Сбросить сегодняшний день", callback_data="reset_cal_today"),
                InlineKeyboardButton("🏠 В главное меню", callback_data="back_to_menu")
            )
            
            await call.message.edit_text(
                f"🍎 *ДНЕВНИК КОНТРОЛЯ ПИТАНИЯ:*\n\n"
                f"📅 Дата: {today_str}\n"
                f"🎯 Твоя норма: *{target_kcal} ккал*\n"
                f"📥 Уже съедено: *{eaten_kcal} ккал*\n\n"
                f"{feedback}", parse_mode="Markdown", reply_markup=kb
            )

        elif code == "add_cal_calories":
            await call.message.edit_text("🍎 Напиши количество калорий, которое хочешь прибавить (например: 350):")
            await MenuStates.logging_calories.set()

        elif code == "reset_cal_today":
            db.reset_calories(uid, today_str)
            await call.answer("Дневник очищен")
            call.data = "menu_calories"
            await process_menu_clicks(call, state)

        elif code == "menu_weight":
            if not u.get("is_pro"):
                kb = InlineKeyboardMarkup(row_width=1).add(
                    InlineKeyboardButton("💳 Подключить PRO", callback_data="menu_pro_info"),
                    InlineKeyboardButton("🏠 В главное меню", callback_data="back_to_menu")
                )
                await call.message.edit_text(
                    "🔒 *КОНТРОЛЬ И ДИНАМИКА ВЕСА*\n\n"
                    "Ввод текущего веса и отслеживание прогресса доступны только в **PRO-системе**.",
                    parse_mode="Markdown", reply_markup=kb
                )
                return
            await call.message.edit_text("💪🏻 Напиши свой новый вес в кг (например: 71.4):")
            await MenuStates.updating_weight.set()

        elif code == "menu_ai":
            is_pro = u.get("is_pro")
            left_requests = u.get("ai_requests_left", 0)
            
            if not is_pro and left_requests <= 0:
                await call.message.answer("⚠️ Твои бесплатные запросы закончились. Подключи PRO!")
                call.data = "menu_pro_info"
                await process_menu_clicks(call, state)
                return
                
            status_chat = "💳 Безлимит PRO" if is_pro else f"🆓 Осталось запросов: {left_requests}"
            await call.message.edit_text(
                f"💬 ЧАТ С AI-ТРЕНЕРОМ\n"
                f"Статус: {status_chat}\n\n"
                f"Задай любой вопрос о продуктах или тренировках."
            )
            await MenuStates.ai_chat.set()

        elif code == "menu_benefit":
            tip = random.choice(DAILY_TIPS)
            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔄 Другой совет", callback_data="menu_benefit"),
                InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu")
            )
            await call.message.edit_text(
                f"😇 *ПОЛЬЗА ДНЯ:*\n\n{tip}", parse_mode="Markdown", reply_markup=kb
            )

        elif code == "back_to_menu":
            await call.message.edit_text("🏠 Меню:", reply_markup=main_menu_kb())

    except MessageNotModified:
        pass

# =========================
# ✏️ ЛОГИРОВАНИЕ КАЛОРИЙ
# =========================
@dp.message_handler(state=MenuStates.logging_calories)
async def process_calorie_addition(message: types.Message, state: FSMContext):
    uid = int(message.from_user.id)
    today_str = date.today().strftime("%Y-%m-%d")
    
    try:
        added_cals = int(message.text)
        if added_cals <= 0:
            return await message.answer("⚠️ Введи положительное число:")
            
        db.add_calories(uid, today_str, added_cals)
        u = db.get_user(uid)
        
        target = calc_kcal(u["weight"], u["height"], u["age"], u["gender"], u["goal"])
        total = db.get_calories(uid, today_str)
        remaining = target - total
        
        if remaining >= 0:
            msg = f"❤️ Добавлено: +{added_cals} ккал.\nСегодня съедено: {total} ккал. Остаток: *{remaining} ккал*."
        else:
            msg = f"⚠️ *Превышение нормы!*\nСъедено: {total} ккал (норма {target}). Ты переел на *{abs(remaining)} ккал*!"
            
        await state.finish()
        await message.answer(msg, parse_mode="Markdown", reply_markup=main_menu_kb())
    except ValueError:
        await message.answer("⚠️ Введи число цифрами:")

# =========================
# 💪🏻 ВВОД И АНАЛИЗ ВЕСА (ДИНАМИКА С БД!)
# =========================
@dp.message_handler(state=MenuStates.updating_weight)
async def process_weight_input(message: types.Message, state: FSMContext):
    uid = int(message.from_user.id)
    try:
        w = float(message.text.replace(",", "."))
        if w <= 0 or w > 300:
            return await message.answer("⚠️ Укажи корректный вес:")
            
        db.update_user_weight(uid, w)
        db.add_weight(uid, w, date.today().strftime("%Y-%m-%d"))
        await state.finish()
        
        u = db.get_user(uid)
        goal = u.get("goal", "maintain")
        history = db.get_weights(uid)
        
        if len(history) > 1:
            start_w, _ = history[0]
            prev_w, _ = history[-2]
            
            diff_start = w - start_w
            diff_prev = w - prev_w
            
            msg = f"✅ *Новый вес зафиксирован: {w} кг!*\n\n"
            
            if goal == "cut":
                if diff_prev < 0:
                    msg += f"🔥 *Ты молодец!* Это отличный результат! С прошлого раза ушло еще *{abs(diff_prev):.1f} кг*! 💪🏻\n"
                elif diff_prev > 0:
                    msg += f"⚖️ Вес колеблется на *+{diff_prev:.1f} кг*, но это нормально. Вода, гликоген задерживают вес. Не вешай нос! 😎\n"
                else:
                    msg += f"👌 Вес стабилен (без изменений с прошлого раза). ❤️\n"
                
                if diff_start < 0:
                    msg += f"\n🏆 *Твой общий прогресс:* ты скинул уже *{abs(diff_start):.1f} кг* со старта! Твое тело меняется! ❤️"
                    
            elif goal == "bulk":
                if diff_prev > 0:
                    msg += f"🔥 *Мощная работа!* Прибавка *+{diff_prev:.1f} кг* с прошлого раза. Мышцы растут! 💪🏻\n"
                elif diff_prev < 0:
                    msg += f"⚖️ Вес просел на *-{abs(diff_prev):.1f} кг*. Добавь немного углеводов и белка! 🍎\n"
                else:
                    msg += f"👌 Вес на месте. Чтобы расти дальше, немного увеличь калорийность! 💪🏻\n"
                    
                if diff_start > 0:
                    msg += f"\n🏆 *Твой общий прогресс:* плюс *{diff_start:.1f} кг* качественной массы со старта! 😎"
            else:
                msg += f"🎯 Ты отлично держишь форму! Разница со стартом: *{diff_start:+.1f} кг*. Баланс под контролем! 😇"
                
            await message.answer(msg, parse_mode="Markdown", reply_markup=main_menu_kb())
        else:
            await message.answer(f"✅ Вес успешно сохранен: {w} кг!\n\nЭто твоя отправная точка. Записывай вес регулярно!", reply_markup=main_menu_kb())
    except ValueError:
        await message.answer("⚠️ Введи вес цифрами:")

# =========================
# 💬 ЛОГИКА AI ТРЕНЕРА
# =========================
@dp.message_handler(state=MenuStates.ai_chat)
async def process_ai_chat_message(message: types.Message, state: FSMContext):
    uid = int(message.from_user.id)
    u = db.get_user(uid)
    
    is_pro = u.get("is_pro")
    left_requests = u.get("ai_requests_left", 0)
    
    if not is_pro and left_requests <= 0:
        await state.finish()
        await message.answer("⚠️ Твои бесплатные запросы закончились.")
        return
        
    await bot.send_chat_action(message.chat.id, action=types.ChatActions.TYPING)
    wait_msg = await message.answer("💬 Обдумываю ответ...")
    
    reply = await request_groq_ai(uid, message.text, u)
    await wait_msg.delete()
    
    if not is_pro:
        new_left = max(0, left_requests - 1)
        db.update_user_pro(uid, False, new_left)
        reply += f"\n\n*⚠️ Осталось бесплатных запросов: {new_left}*"

    await message.answer(reply)

async def request_groq_ai(uid, user_text, u):
    uid_str = str(uid)
    if uid_str not in user_memory: user_memory[uid_str] = []
    user_memory[uid_str].append({"role": "user", "content": user_text})
    user_memory[uid_str] = user_memory[uid_str][-10:]
    
    system_prompt = (
        f"Ты — дружелюбный фитнес-консультант. Твой клиент: "
        f"пол {GENDERS.get(u.get('gender'))}, возраст {u.get('age')}, рост {u.get('height')}, вес {u.get('weight')}, цель {GOALS.get(u.get('goal'))}. "
        f"Отвечай по-человечески тепло, кратко, мотивируй соблюдать дисциплину и вести дневник калорий."
    )
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "system", "content": system_prompt}] + user_memory[uid_str],
        "temperature": 0.7
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=20) as r:
                if r.status == 200:
                    data = await r.json()
                    ai_reply = data["choices"][0]["message"]["content"]
                    user_memory[uid_str].append({"role": "assistant", "content": ai_reply})
                    return ai_reply
                return "😔 Ошибка связи с сервером."
    except Exception:
        return "😔 Не удалось получить ответ."

# =========================
# 🚀 ЗАПУСК БОТА
# =========================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
 
