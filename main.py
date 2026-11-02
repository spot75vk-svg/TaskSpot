import asyncio
import io
import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

# ================= КОНФІГУРАЦІЯ =================
BOT_TOKEN = "9071631760:AAFFOcT4-jgBe4PSqQ3VYwveIpGmjZZXck0"
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
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            alerted_30min INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT
        )
    """)
    # Безпечне додавання нових колонок для існуючих баз даних
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN status TEXT DEFAULT 'pending'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN created_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN alerted_30min INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def add_task(text, manager_id, assignee_id, deadline):
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tasks (text, manager_id, assignee_id, deadline, status, created_at, alerted_30min) VALUES (?, ?, ?, ?, 'pending', ?, 0)",
        (text, manager_id, assignee_id, deadline, now_str)
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
    cursor.execute("SELECT id, text, manager_id, assignee_id, deadline, status, created_at, alerted_30min FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    conn.close()
    return task

def get_pending_unaccepted_tasks():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, manager_id, assignee_id, created_at FROM tasks WHERE status = 'pending' AND alerted_30min = 0")
    tasks = cursor.fetchall()
    conn.close()
    return tasks

def mark_task_alerted(task_id):
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET alerted_30min = 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()

def get_active_tasks():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, manager_id, assignee_id, deadline FROM tasks WHERE status IN ('pending', 'in_progress')")
    tasks = cursor.fetchall()
    conn.close()
    return tasks

def get_employee_active_tasks(assignee_id):
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, deadline FROM tasks WHERE assignee_id = ? AND status IN ('pending', 'in_progress')", (assignee_id,))
    tasks = cursor.fetchall()
    conn.close()
    return tasks

def get_all_tasks_for_export():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.id, t.text, e.name, t.assignee_id, t.deadline, t.status 
        FROM tasks t
        LEFT JOIN employees e ON t.assignee_id = e.telegram_id
        ORDER BY t.id DESC
    """)
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
            SUM(CASE WHEN status IN ('pending', 'in_progress') THEN 1 ELSE 0 END) as in_progress,
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

# --- КЛАВІАТУРИ (МЕНЮ) ---
def get_manager_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text="➕ Створити задачу")
    kb.button(text="👤 Додати працівника")
    kb.button(text="📊 Експорт в Excel")
    kb.button(text="📈 Статистика")
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)

def get_employee_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text="❓ Уточнення по задачі")
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

# --- ГЕНЕРАЦІЯ EXCEL-ЗВІТУ ---
def generate_excel_report():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Завдання"

    headers = ["ID Задачі", "Текст завдання", "Виконавець", "ID Виконавця", "Дедлайн", "Статус"]
    ws.append(headers)

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    status_map = {
        "pending": "⏳ Очікує прийняття",
        "in_progress": "🛠 В роботі",
        "completed": "✅ Виконано",
        "overdue": "⏰ Протерміновано"
    }

    tasks = get_all_tasks_for_export()
    for t in tasks:
        task_id, text, emp_name, assignee_id, deadline, status = t
        emp_display = emp_name if emp_name else f"ID: {assignee_id}"
        status_display = status_map.get(status, status)
        ws.append([task_id, text, emp_display, assignee_id, deadline, status_display])

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)
    return file_stream

# --- FSM СТАНИ ---
class CreateTask(StatesGroup):
    waiting_for_text = State()
    waiting_for_assignee = State()
    waiting_for_deadline = State()

class AddEmployee(StatesGroup):
    waiting_for_data = State()

class TaskClarification(StatesGroup):
    waiting_for_question = State()

class ManagerReply(StatesGroup):
    waiting_for_answer = State()

# --- ХЕНДЛЕРИ КОМАНД ТА КНОПОК ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    if user_id in MANAGERS:
        text = "👑 **Вітаю, пане керівник!**\nВикористовуйте кнопки знизу для керування завданнями:"
        await message.answer(text, reply_markup=get_manager_keyboard(), parse_mode="Markdown")
    else:
        text = (
            f"👋 Вітаю! Твій Telegram ID: `{user_id}`\n\n"
            "Передай цей ID своєму керівнику, щоб він додав тебе до бази працівників."
        )
        await message.answer(text, reply_markup=get_employee_keyboard(), parse_mode="Markdown")

# Додавання працівника
@dp.message(F.text == "👤 Додати працівника")
@dp.message(Command("addemp"))
async def cmd_add_emp(message: types.Message, state: FSMContext):
    if message.from_user.id not in MANAGERS:
        await message.answer("❌ Ця функція доступна тільки керівникам.")
        return
    await message.answer("Введіть дані працівника у форматі:\n`ID Ім'я`\n(наприклад: `123456789 Олександр`)", parse_mode="Markdown")
    await state.set_state(AddEmployee.waiting_for_data)

@dp.message(AddEmployee.waiting_for_data)
async def process_employee_data(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Невірний формат. Введіть у форматі: `ID Ім'я`", parse_mode="Markdown")
        return
    try:
        emp_id = int(args[0])
        emp_name = args[1]
        add_employee_to_db(emp_id, emp_name)
        await state.clear()
        await message.answer(f"✅ Працівника **{emp_name}** (`{emp_id}`) успішно додано до бази!", parse_mode="Markdown", reply_markup=get_manager_keyboard())
    except ValueError:
        await message.answer("⚠️ ID повинен бути числом. Спробуйте ще раз у форматі `ID Ім'я`:")

# Експорт в Excel
@dp.message(F.text == "📊 Експорт в Excel")
@dp.message(Command("export"))
async def btn_export(message: types.Message):
    if message.from_user.id not in MANAGERS:
        await message.answer("❌ Ця функція доступна тільки керівникам.")
        return
    
    excel_file = generate_excel_report()
    doc_file = types.BufferedInputFile(excel_file.read(), filename="tasks_report.xlsx")
    await message.answer_document(doc_file, caption="📊 **Актуальний звіт із завданнями в Excel**", parse_mode="Markdown")

# Статистика
@dp.message(F.text == "📈 Статистика")
@dp.message(Command("stats"))
async def btn_stats(message: types.Message):
    if message.from_user.id not in MANAGERS:
        await message.answer("❌ Ця функція доступна тільки керівникам.")
        return
    stats_data = get_stats_summary()
    if not stats_data:
        await message.answer("📊 База завдань порожня.")
        return
    text = "📊 **Статистика виконання:**\n\n"
    for assignee_id, completed, in_progress, overdue in stats_data:
        name = get_employee_name(assignee_id)
        text += f"👤 **{name}** (`{assignee_id}`):\n   • Виконано: ✅ {completed}\n   • В роботі: 🛠 {in_progress}\n   • Протерміновано: ⏰ {overdue}\n\n"
    await message.answer(text, parse_mode="Markdown")

# --- ЛОГІКА УТОЧНЕНЬ ВІД ПРАЦІВНИКА ---
@dp.message(F.text == "❓ Уточнення по задачі")
async def employee_ask_clarification(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    tasks = get_employee_active_tasks(user_id)
    if not tasks:
        await message.answer("У вас немає активних завдань, по яких потрібні уточнення.")
        return

    kb = InlineKeyboardBuilder()
    for task_id, text, deadline in tasks:
        short_text = (text[:25] + '..') if len(text) > 25 else text
        kb.button(text=f"#{task_id}: {short_text}", callback_data=f"ask_clar_{task_id}")
    kb.adjust(1)

    await message.answer("Оберіть завдання, по якому хочете зробити уточнення:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("ask_clar_"))
async def process_clarification_task_choice(callback: types.CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])
    await state.update_data(clar_task_id=task_id)
    await callback.message.edit_text(f"Введіть ваше запитання / уточнення для завдання #{task_id}:", parse_mode="Markdown")
    await state.set_state(TaskClarification.waiting_for_question)
    await callback.answer()

@dp.message(TaskClarification.waiting_for_question)
async def send_clarification_to_manager(message: types.Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get('clar_task_id')
    await state.clear()

    task = get_task(task_id)
    if not task:
        await message.answer("Помилка: завдання не знайдено.")
        return

    manager_id = task[2]
    emp_name = get_employee_name(message.from_user.id)

    kb = InlineKeyboardBuilder()
    kb.button(text="💬 Відповісти на уточнення", callback_data=f"reply_clar_{task_id}_{message.from_user.id}")
    kb.adjust(1)

    try:
        await bot.send_message(
            chat_id=manager_id,
            text=f"❓ **Уточнення від працівника ({emp_name}) по задачі #{task_id}:**\n\n{message.text}",
            reply_markup=kb.as_markup(),
            parse_mode="Markdown"
        )
        await message.answer("✅ Ваше запитання успішно надіслано керівнику!", reply_markup=get_employee_keyboard())
    except Exception as e:
        await message.answer(f"⚠️ Не вдалося надіслати повідомлення керівнику: {e}")

@dp.callback_query(F.data.startswith("reply_clar_"))
async def manager_start_reply(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in MANAGERS:
        await callback.answer("Ви не керівник!", show_alert=True)
        return

    parts = callback.data.split("_")
    task_id = int(parts[2])
    employee_id = int(parts[3])

    await state.update_data(reply_task_id=task_id, reply_employee_id=employee_id)
    await callback.message.answer(f"Введіть відповідь на уточнення для завдання #{task_id}:", parse_mode="Markdown")
    await state.set_state(ManagerReply.waiting_for_answer)
    await callback.answer()

@dp.message(ManagerReply.waiting_for_answer)
async def send_manager_answer_to_employee(message: types.Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get('reply_task_id')
    employee_id = data.get('reply_employee_id')
    await state.clear()

    try:
        await bot.send_message(
            chat_id=employee_id,
            text=f"💡 **Відповідь керівника на уточнення по задачі #{task_id}:**\n\n{message.text}",
            parse_mode="Markdown"
        )
        await message.answer(f"✅ Відповідь успішно надіслано працівнику!", reply_markup=get_manager_keyboard())
    except Exception as e:
        await message.answer(f"⚠️ Помилка надсилання відповіді працівнику: {e}")

# --- СТВОРЕННЯ ЗАДАЧІ ---
@dp.message(F.text == "➕ Створити задачу")
@dp.message(Command("newtask"))
async def btn_new_task(message: types.Message, state: FSMContext):
    if message.from_user.id not in MANAGERS:
        await message.answer("❌ Ця функція доступна тільки керівникам.")
        return
    await message.answer("Введіть текст завдання:")
    await state.set_state(CreateTask.waiting_for_text)

@dp.message(CreateTask.waiting_for_text)
async def process_task_text(message: types.Message, state: FSMContext):
    await state.update_data(task_text=message.text)
    employees = get_employees()
    if not employees:
        await message.answer("⚠️ База працівників порожня! Додайте співробітника за допомогою кнопки «👤 Додати працівника».", parse_mode="Markdown")
        await state.clear()
        return

    kb = InlineKeyboardBuilder()
    for emp_id, emp_name in employees:
        kb.button(text=f"👤 {emp_name}", callback_data=f"select_emp_{emp_id}")
    kb.adjust(1)

    await message.answer("Оберіть працівника, якому призначається задача:", reply_markup=kb.as_markup())
    await state.set_state(CreateTask.waiting_for_assignee)

@dp.callback_query(F.data.startswith("select_emp_"), CreateTask.waiting_for_assignee)
async def process_employee_selection(callback: types.CallbackQuery, state: FSMContext):
    assignee_id = int(callback.data.split("_")[2])
    await state.update_data(assignee_id=assignee_id)
    emp_name = get_employee_name(assignee_id)

    await callback.message.edit_text(f"Обрано виконавця: **{emp_name}**\n\nТепер введіть дедлайн у форматі `ДД.ММ.РРРР ГГ:ХХ` (наприклад: `15.11.2026 18:00`):", parse_mode="Markdown")
    await state.set_state(CreateTask.waiting_for_deadline)
    await callback.answer()

@dp.message(CreateTask.waiting_for_deadline)
async def process_deadline(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        await message.answer("⚠️ Невірний формат! Введіть дату у форматі `ДД.ММ.РРРР ГГ:ХХ`:", parse_mode="Markdown")
        return

    data = await state.get_data()
    task_id = add_task(data['task_text'], message.from_user.id, data['assignee_id'], message.text.strip())
    await state.clear()

    emp_name = get_employee_name(data['assignee_id'])
    await message.answer(f"✅ Задачу #{task_id} відправлено працівнику **{emp_name}**!", parse_mode="Markdown", reply_markup=get_manager_keyboard())

    # Кнопка прийняття в роботу для працівника
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Прийняти в роботу", callback_data=f"accept_{task_id}")
    try:
        await bot.send_message(
            chat_id=data['assignee_id'],
            text=f"📋 **Нове завдання #{task_id}:**\n\n{data['task_text']}\n\n⏳ **Дедлайн:** {message.text.strip()}",
            reply_markup=kb.as_markup(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"⚠️ Помилка надсилання сповіщення працівнику: {e}")

# --- ПРИЙНЯТТЯ ЗАДАЧІ В РОБОТУ ---
@dp.callback_query(F.data.startswith("accept_"))
async def process_task_accept(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    task = get_task(task_id)
    if not task:
        await callback.answer("Завдання не знайдено.")
        return
    
    if task[5] != 'pending':
        await callback.answer("Завдання вже прийняте або закрите.")
        return

    update_task_status(task_id, 'in_progress')
    emp_name = get_employee_name(callback.from_user.id)

    # Заміна кнопки на виконання
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Відмітити як виконану", callback_data=f"done_{task_id}")

    await callback.message.edit_text(
        f"📋 **Завдання #{task_id} (В роботі):**\n\n{task[1]}\n\n⏳ **Дедлайн:** {task[4]}",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )

    # Сповіщення керівнику про прийняття
    try:
        await bot.send_message(
            chat_id=task[2],
            text=f"👤 **Працівник {emp_name} прийняв в роботу завдання #{task_id}!**",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Помилка сповіщення керівника: {e}")

    await callback.answer("Завдання прийнято в роботу!")

# --- ВИКОНАННЯ ТА ЗАКРИТТЯ ЗАДАЧІ ---
@dp.callback_query(F.data.startswith("done_"))
async def process_task_done(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    task = get_task(task_id)
    if not task:
        await callback.answer("Завдання не знайдено.")
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="👍 Підтвердити", callback_data=f"approve_{task_id}")
    kb.button(text="🔄 На доопрацювання", callback_data=f"reject_{task_id}")
    kb.adjust(2)

    await bot.send_message(
        chat_id=task[2],
        text=f"🔔 **Працівник відмітив задачу #{task_id} як виконану!**\n\n{task[1]}",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )
    await callback.message.edit_text(f"⏳ **Завдання #{task_id} на перевірці у керівника.**", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_task(callback: types.CallbackQuery):
    if callback.from_user.id not in MANAGERS:
        await callback.answer("Ви не керівник!", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    task = get_task(task_id)
    update_task_status(task_id, "completed")
    await callback.message.edit_text(f"✅ **Завдання #{task_id} підтверджено та закрито!**", parse_mode="Markdown")
    await bot.send_message(task[3], f"🎉 **Керівник підтвердив виконання завдання #{task_id}!**", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_task(callback: types.CallbackQuery):
    if callback.from_user.id not in MANAGERS:
        await callback.answer("Ви не керівник!", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    task = get_task(task_id)
    update_task_status(task_id, "in_progress")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Відмітити як виконану", callback_data=f"done_{task_id}")
    await callback.message.edit_text(f"🔄 **Завдання #{task_id} повернуто на доопрацювання.**", parse_mode="Markdown")
    await bot.send_message(task[3], f"⚠️ **Завдання #{task_id} повернуто на доопрацювання!**", reply_markup=kb.as_markup(), parse_mode="Markdown")
    await callback.answer()

# --- ФОНОВИЙ ПЕРЕВІРНИК (ДЕЛАЙНИ ТА 30 ХВИЛИН НА ПРИЙНЯТТЯ) ---
async def check_deadlines():
    while True:
        await asyncio.sleep(300) # Перевірка кожні 5 хвилин
        try:
            now = datetime.now()

            # 1. Перевірка 30 хвилин на прийняття задачі
            pending_tasks = get_pending_unaccepted_tasks()
            for task in pending_tasks:
                task_id, text, manager_id, assignee_id, created_str = task
                if created_str:
                    try:
                        created_dt = datetime.strptime(created_str, "%d.%m.%Y %H:%M")
                        if (now - created_dt).total_seconds() > 1800: # 1800 секунд = 30 хвилин
                            mark_task_alerted(task_id)
                            emp_name = get_employee_name(assignee_id)
                            await bot.send_message(
                                chat_id=manager_id,
                                text=f"⏰ **УВАГА! Працівник {emp_name} не прийняв в роботу завдання #{task_id} протягом 30 хвилин!**\n\n{text}",
                                parse_mode="Markdown"
                            )
                    except Exception as e:
                        print(f"Помилка обробки часу створення задачі #{task_id}: {e}")

            # 2. Перевірка протермінованих дедлайнів
            for task in get_active_tasks():
                task_id, text, manager_id, assignee_id, deadline_str = task
                try:
                    deadline_dt = datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")
                    if now > deadline_dt:
                        update_task_status(task_id, "overdue")
                        emp_name = get_employee_name(assignee_id)

                        try:
                            await bot.send_message(
                                chat_id=assignee_id,
                                text=f"⏰ **УВАГА! Протерміновано дедлайн за завданням #{task_id}!**\n\n{text}\n\n⏳ **Дедлайн був:** {deadline_str}",
                                parse_mode="Markdown"
                            )
                        except Exception as e:
                            print(f"Помилка сповіщення працівника: {e}")

                        try:
                            await bot.send_message(
                                chat_id=manager_id,
                                text=f"⚠️ **УВАГА! Недотримано дедлайн!**\n\n👤 **Виконавець:** {emp_name}\n📋 **Завдання #{task_id}:** {text}\n⏳ **Дедлайн був:** {deadline_str}",
                                parse_mode="Markdown"
                            )
                        except Exception as e:
                            print(f"Помилка сповіщення керівника: {e}")
                except Exception as e:
                    print(f"Помилка перевірки дедлайну задачі #{task_id}: {e}")

        except Exception as e:
            print(f"Помилка фонового процесу: {e}")

async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(check_deadlines())
    print("Бот успішно запущений...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
