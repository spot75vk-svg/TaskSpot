import asyncio
import os
import sqlite3
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= КОНФІГУРАЦІЯ =================
# 1. Вставте ваш  токен від BotFather замість тексту у лапках
BOT_TOKEN = "9036019477:AAFW4m19YzOje2eX2MklpCeQGoHHxjclKGI"

# 2. Вставте Telegram ID керівників у список (можна додати ще, через кому)
MANAGERS = [66491739] 
# =================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- БАЗА ДАНИХ ---
def init_db():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    # Таблиця завдань
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
    # Таблиця працівників
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

# --- FSM (СТАНИ ДЛЯ СТВОРЕННЯ ЗАДАЧІ) ---
class CreateTask(StatesGroup):
    waiting_for_text = State()
    waiting_for_assignee = State()
    waiting_for_deadline = State()

# --- ХЕНДЛЕРИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    text = (
        f"👋 Вітаю! Твій Telegram ID: `{user_id}`\n\n"
        "Передай цей ID керівнику, щоб він міг додати тебе до бази працівників.\n"
    )
    if user_id in MANAGERS:
        text += (
            "\n👑 **Ви визначені як керівник.**\n"
            "• Додати працівника: `/addemp ID Ім'я`\n"
            "• Створити задачу: `/newtask`\n"
            "• Статистика: `/stats`"
        )
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("addemp"))
async def cmd_add_emp(message: types.Message):
    if message.from_user.id not in MANAGERS:
        await message.answer("❌ Ця команда доступна тільки керівникам.")
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("⚠️ Невірний формат!\nВикористовуйте: `/addemp Telegram_ID Ім'я`\nПриклад: `/addemp 123456789 Іван`", parse_mode="Markdown")
        return
    
    try:
        emp_id = int(args[1])
        emp_name = args[2]
        add_employee_to_db(emp_id, emp_name)
        await message.answer(f"✅ Працівника **{emp_name}** (`{emp_id}`) успішно додано до бази!", parse_mode="Markdown")
    except ValueError:
        await message.answer("⚠️ Telegram ID має складатися лише з цифр.")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in MANAGERS:
        await message.answer("❌ Ця команда доступна тільки керівникам.")
        return

    stats_data = get_stats_summary()
    if not stats_data:
        await message.answer("📊 Наразі немає даних про задачі в системі.")
        return

    text = "📊 **Статистика виконання завдань співробітниками:**\n\n"
    for assignee_id, completed, in_progress, overdue in stats_data:
        emp_name = get_employee_name(assignee_id)
        text += f"👤 **Працівник:** {emp_name} (`{assignee_id}`)\n"
        text += f"   • Виконано: ✅ **{completed}**\n"
        text += f"   • В роботі: ⏳ **{in_progress}**\n"
        text += f"   • Протерміновано: ⏰ **{overdue}**\n\n"

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
        await message.answer("⚠️ База працівників порожня! Спочатку додайте працівника командою `/addemp ID Ім'я`.", parse_mode="Markdown")
        await state.clear()
        return

    kb = InlineKeyboardBuilder()
    for emp_id, emp_name in employees:
        kb.button(text=f"👤 {emp_name}", callback_data=f"select_emp_{emp_id}")
    kb.adjust(1)

    await message.answer("Виберіть працівника, якому призначається завдання:", reply_markup=kb.as_markup())
    await state.set_state(CreateTask.waiting_for_assignee)

@dp.callback_query(F.data.startswith("select_emp_"), CreateTask.waiting_for_assignee)
async def process_employee_selection(callback: types.CallbackQuery, state: FSMContext):
    assignee_id = int(callback.data.split("_")[2])
    await state.update_data(assignee_id=assignee_id)

    await callback.message.edit_text("Введіть дедлайн виконання у форматі `ДД.ММ.РРРР ГГ:ХХ`\n(Наприклад: `15.11.2026 18:00`):", parse_mode="Markdown")
    await state.set_state(CreateTask.waiting_for_deadline)
    await callback.answer()

@dp.message(CreateTask.waiting_for_deadline)
async def process_deadline(message: types.Message, state: FSMContext):
    deadline_str = message.text.strip()
    
    try:
        datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")
    except ValueError:
        await message.answer("⚠️ Невірний формат дати! Використовуйте формат `ДД.ММ.РРРР ГГ:ХХ` (наприклад: `15.11.2026 18:00`):", parse_mode="Markdown")
        return

    data = await state.get_data()
    task_text = data['task_text']
    assignee_id = data['assignee_id']
    manager_id = message.from_user.id

    task_id = add_task(task_text, manager_id, assignee_id, deadline_str)
    await state.clear()

    emp_name = get_employee_name(assignee_id)
    await message.answer(f"✅ Задачу #{task_id} успішно створено та відправлено працівнику **{emp_name}**!", parse_mode="Markdown")

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Відмітити як виконану", callback_data=f"done_{task_id}")

    try:
        await bot.send_message(
            chat_id=assignee_id,
            text=f"📋 **Нове завдання #{task_id}:**\n\n{task_text}\n\n⏳ **Дедлайн:** {deadline_str}",
            reply_markup=kb.as_markup(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"⚠️ Задачу створено, але не вдалося надіслати сповіщення працівнику. Помилка: {e}")

@dp.callback_query(F.data.startswith("done_"))
async def process_task_done(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    task = get_task(task_id)

    if not task:
        await callback.answer("Завдання не знайдено.", show_alert=True)
        return

    manager_id = task[2]

    kb = InlineKeyboardBuilder()
    kb.button(text="👍 Підтвердити", callback_data=f"approve_{task_id}")
    kb.button(text="🔄 На доопрацювання", callback_data=f"reject_{task_id}")
    kb.adjust(2)

    await bot.send_message(
        chat_id=manager_id,
        text=f"🔔 **Працівник відмітив задачу #{task_id} як виконану!**\n\n**Текст задачі:** {task[1]}",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )

    await callback.message.edit_text(f"⏳ **Завдання #{task_id} відправлено на перевірку керівнику.**\n\n{task[1]}", parse_mode="Markdown")
    await callback.answer("Відправлено на підтвердження!")

@dp.callback_query(F.data.startswith("approve_"))
async def approve_task(callback: types.CallbackQuery):
    if callback.from_user.id not in MANAGERS:
        await callback.answer("Тільки керівники можуть підтверджувати виконання!", show_alert=True)
        return

    task_id = int(callback.data.split("_")[1])
    task = get_task(task_id)

    update_task_status(task_id, "completed")

    await callback.message.edit_text(f"✅ **Завдання #{task_id} підтверджено та закрито!**\n\n{task[1]}", parse_mode="Markdown")

    await bot.send_message(
        chat_id=task[3],
        text=f"🎉 **Керівник підтвердив виконання завдання #{task_id}!**\n\n{task[1]}",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_task(callback: types.CallbackQuery):
    if callback.from_user.id not in MANAGERS:
        await callback.answer("Тільки керівники можуть повертати задачі!", show_alert=True)
        return

    task_id = int(callback.data.split("_")[1])
    task = get_task(task_id)

    update_task_status(task_id, "in_progress")

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Відмітити як виконану", callback_data=f"done_{task_id}")

    await callback.message.edit_text(f"🔄 **Завдання #{task_id} повернуто працівнику на доопрацювання.**\n\n{task[1]}", parse_mode="Markdown")

    await bot.send_message(
        chat_id=task[3],
        text=f"⚠️ **Завдання #{task_id} повернуто на доопрацювання!**\n\n{task[1]}",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()

async def check_deadlines():
    while True:
        await asyncio.sleep(600)  # Перевірка кожні 10 хвилин
        try:
            tasks = get_active_tasks()
            now = datetime.now()
            for task in tasks:
                task_id, text, manager_id, assignee_id, deadline_str = task
                deadline_dt = datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")
                
                if now > deadline_dt:
                    update_task_status(task_id, "overdue")
                    
                    await bot.send_message(
                        chat_id=assignee_id,
                        text=f"⏰ **УВАГА! Дедлайн завдання #{task_id} вичерпано!**\n\n{text}",
                        parse_mode="Markdown"
                    )
                    await bot.send_message(
                        chat_id=manager_id,
                        text=f"⚠️ **Дедлайн завдання #{task_id} протерміновано виконавцем!**\n\n{text}",
                        parse_mode="Markdown"
                    )
        except Exception as e:
            print(f"Помилка у фоновій перевірці дедлайнів: {e}")

# --- ВЕБСЕРВЕР ТА ГОЛОВНИЙ ЗАПУСК ДЛЯ RENDER ---
async def handle(request):
    return web.Response(text="Bot is running!")

async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    
    print("Бот запущений і готовий до роботи...")
    
    # Запускаємо фонову перевірку дедлайнів та polling бота
    asyncio.create_task(check_deadlines())
    
    # Створюємо додаток aiohttp для вебсерверу Render
    app = web.Application()
    app.router.add_get("/", handle)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render передає порт через змінну середовища PORT
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    # Запускаємо безперервний поллінг бота в основному потоці
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
