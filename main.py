import asyncio
import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= КОНФІГУРАЦІЯ =================
# 1. Вставте ваш токен від BotFather у лапки нижче:
BOT_TOKEN = "9036019477:AAFW4m19YzOje2eX2MklpCeQGoHHxjclKGI"

# 2. Перелік Telegram ID керівників (через кому):
MANAGERS = [66491739]
# =================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- БАЗА ДАНИХ ---
def init_db():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            manager_id INTEGER,
            assignee_id INTEGER,
            deadline TEXT,
            status TEXT DEFAULT 'in_progress'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_task(text, manager_id, assignee_id, deadline):
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tasks (text, manager_id, assignee_id, deadline, status) VALUES (?, ?, ?, ?, 'in_progress')",
        (text, manager_id, assignee_id, deadline)
    )
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return task_id

def update_task_status(task_id, status):
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    conn.commit()
    conn.close()

def get_task(task_id):
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, manager_id, assignee_id, deadline, status FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    conn.close()
    return task

def get_active_tasks():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, manager_id, assignee_id, deadline FROM tasks WHERE status = 'in_progress'")
    tasks = cursor.fetchall()
    conn.close()
    return tasks

def get_stats_summary():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            assignee_id,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
            SUM(CASE WHEN status = 'overdue' THEN 1 ELSE 0 END) as overdue
        FROM tasks
        GROUP BY assignee_id
    """)
    data = cursor.fetchall()
    conn.close()
    return data

def get_employees():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, name FROM employees")
    employees = cursor.fetchall()
    conn.close()
    return employees

def add_employee_to_db(telegram_id, name):
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO employees (telegram_id, name) VALUES (?, ?)", (telegram_id, name))
    conn.commit()
    conn.close()

def get_employee_name(telegram_id):
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM employees WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else f"ID: {telegram_id}"

# --- FSM (СТАНИ) ---
class CreateTask(StatesGroup):
    waiting_for_text = State()
    waiting_for_assignee = State()
    waiting_for_deadline = State()

# --- ХЕНДЛЕРИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    text = f"👋 Вітаю! Твій Telegram ID: `{user_id}`\n\nПередай цей ID керівнику для додавання до бази."
    if user_id in MANAGERS:
        text += "\n\n👑 **Ви керівник:**\n• `/addemp ID Ім'я` — додати працівника\n• `/newtask` — створити завдання\n• `/stats` — переглянути статистику"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("addemp"))
async def cmd_add_emp(message: types.Message):
    if message.from_user.id not in MANAGERS:
        await message.answer("❌ Ця команда доступна тільки керівникам.")
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("⚠️ Використовуйте формат: `/addemp Telegram_ID Ім'я`", parse_mode="Markdown")
        return
    try:
        add_employee_to_db(int(args[1]), args[2])
        await message.answer(f"✅ Працівника **{args[2]}** додано до бази!", parse_mode="Markdown")
    except ValueError:
        await message.answer("⚠️ Telegram ID має складатися лише з цифр.")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in MANAGERS:
        await message.answer("❌ Ця команда доступна тільки керівникам.")
        return
    stats_data = get_stats_summary()
    if not stats_data:
        await message.answer("📊 База завдань порожня.")
        return
    text = "📊 **Статистика виконання:**\n\n"
    for assignee_id, completed, in_progress, overdue in stats_data:
        name = get_employee_name(assignee_id)
        text += f"👤 **{name}** (`{assignee_id}`):\n   • Виконано: ✅ {completed}\n   • В роботі: ⏳ {in_progress}\n   • Протерміновано: ⏰ {overdue}\n\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("newtask"))
async def cmd_newtask(message: types.Message, state: FSMContext):
    if message.from_user.id not in MANAGERS:
        await message.answer("❌ Ця команда доступна тільки керівникам.")
        return
    await message.answer("Введіть текст завдання:")
    await state.set_state(CreateTask.waiting_for_text)

@dp.message(CreateTask.waiting_for_text)
async def process_task_text(message: types.Message, state: FSMContext):
    await state.update_data(task_text=message.text)
    employees = get_employees()
    if not employees:
        await message.answer("⚠️ База працівників порожня! Додайте співробітника командою `/addemp ID Ім'я`.", parse_mode="Markdown")
        await state.clear()
        return
    kb = InlineKeyboardBuilder()
    for emp_id, emp_name in employees:
        kb.button(text=f"👤 {emp_name}", callback_data=f"select_emp_{emp_id}")
    kb.adjust(1)
    await message.answer("Виберіть працівника:",
