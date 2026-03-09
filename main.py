import asyncio
import sqlite3
import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"  # Замените на токен от @BotFather
ADMIN_IDS = [123456789]  # Замените на ваш Telegram ID

# Создаем папки
Path("data").mkdir(exist_ok=True)
Path("data/photos").mkdir(exist_ok=True)


# ==================== МОДЕЛИ ДАННЫХ ====================
class UserRole(Enum):
    EMPLOYEE = "employee"
    FOREMAN = "foreman"
    DIRECTOR = "director"
    ADMIN = "admin"


@dataclass
class User:
    id: int
    telegram_id: int
    username: str
    full_name: str
    role: UserRole
    workshop_id: int = None
    foreman_id: int = None
    is_active: bool = True


@dataclass
class WorkRecord:
    user_id: int
    product_id: int
    operation_type_id: int
    start_time: datetime
    end_time: datetime = None
    duration: float = 0
    comment: str = ""
    photos: List[str] = None
    status: str = "in_progress"


# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self, db_path="data/database.db"):
        self.db_path = db_path
        self.user_cache: Dict[int, User] = {}
        self._init_db()

    def _init_db(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                workshop_id INTEGER,
                foreman_id INTEGER,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица цехов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS workshops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                foreman_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица изделий
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_number TEXT UNIQUE NOT NULL,
                name TEXT,
                workshop_id INTEGER NOT NULL,
                status TEXT DEFAULT 'in_progress',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица типов операций
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS operation_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT NOT NULL,
                workshop_id INTEGER NOT NULL,
                standard_time REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица учета времени
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS time_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date DATE NOT NULL,check_in DATETIME,
                check_out DATETIME,
                total_hours REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица рабочих записей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS work_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                operation_type_id INTEGER NOT NULL,
                start_time DATETIME NOT NULL,
                end_time DATETIME,
                duration REAL DEFAULT 0,
                comment TEXT,
                photos TEXT,
                status TEXT DEFAULT 'in_progress',
                reviewed_by INTEGER,
                review_comment TEXT,
                hidden_comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

        # Создаем тестовые данные
        self._create_sample_data()

    def _create_sample_data(self):
        """Создание тестовых данных"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Проверяем, есть ли уже данные
            cursor.execute("SELECT COUNT(*) FROM workshops")
            if cursor.fetchone()[0] == 0:
                # Цеха
                workshops = [
                    ("Цех 1 - Сборка", "Сборка каркасов"),
                    ("Цех 2 - Отделка", "Внутренняя отделка"),
                    ("Цех 3 - Покраска", "Покраска")
                ]
                for name, desc in workshops:
                    cursor.execute("INSERT INTO workshops (name, description) VALUES (?, ?)", (name, desc))

                # Изделия
                products = [
                    ("Баня-001", "Баня 'Русская'", 1),
                    ("Баня-002", "Купель 'Лесная'", 2),
                    ("Баня-003", "Баня 'Модерн'", 1)
                ]
                for num, name, workshop in products:
                    cursor.execute(
                        "INSERT INTO products (product_number, name, workshop_id) VALUES (?, ?, ?)",
                        (num, name, workshop)
                    )

                # Операции
                operations = [
                    ("Сборка каркаса", "ASSEMBLY", 1, 4.0),
                    ("Обшивка стен", "WALLS", 1, 6.0),
                    ("Шлифовка", "SANDING", 2, 5.0),
                    ("Покраска", "PAINTING", 3, 4.0),
                ]
                for name, code, workshop, time in operations:
                    cursor.execute(
                        "INSERT INTO operation_types (name, code, workshop_id, standard_time) VALUES (?, ?, ?, ?)",
                        (name, code, workshop, time)
                    )

                conn.commit()
        finally:
            conn.close()

    def get_connection(self):
        """Получить соединение с базой"""
        return sqlite3.connect(self.db_path)

    def get_user(self, telegram_id: int) -> User | None:
        """Получить пользователя"""
        if telegram_id in self.user_cache:
            return self.user_cache[telegram_id]

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, telegram_id, username, full_name, role, workshop_id, foreman_id, is_active FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            user = User(
                id=row[0],
                telegram_id=row[1],
                username=row[2],
                full_name=row[3],
                role=UserRole(row[4]),
                workshop_id=row[5],
                foreman_id=row[6],
                is_active=bool(row[7])
            )
            self.user_cache[telegram_id] = user
            return user
        return None
        def add_user(username: str, full_name: str, role: str = "employee") -> bool:
        """Добавить пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, full_name, role) VALUES (?, ?, ?, ?)",
                (telegram_id, username, full_name, role)
            )
            conn.commit()

            # Очищаем кэш
            if telegram_id in self.user_cache:
                del self.user_cache[telegram_id]
        except Exception as e:
            print(f"❌ Ошибка добавления пользователя: {e}")
            conn.close()
            return False

        conn.close()
        return True


    def save_time_record(self, user_id: int, check_in: datetime, check_out: datetime = None) -> bool:
        """Сохранить запись времени"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            today = date.today()

            if check_out:
                total_hours = (check_out - check_in).total_seconds() / 3600
                cursor.execute(
                    "INSERT INTO time_records (user_id, date, check_in, check_out, total_hours) VALUES (?, ?, ?, ?, ?)",
                    (user_id, today, check_in, check_out, total_hours)
                )
            else:
                cursor.execute(
                    "INSERT INTO time_records (user_id, date, check_in) VALUES (?, ?, ?)",
                    (user_id, today, check_in)
                )

            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Ошибка сохранения времени: {e}")
            return False
        finally:
            conn.close()

    def save_work_record(self, work_record: WorkRecord) -> Optional[int]:
        """Сохранить рабочую запись и вернуть её ID"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            duration = (work_record.end_time - work_record.start_time).total_seconds() / 3600
            photos_json = json.dumps(work_record.photos) if work_record.photos else None

            cursor.execute('''
                INSERT INTO work_records 
                (user_id, product_id, operation_type_id, start_time, end_time, 
                 duration, comment, photos, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                work_record.user_id,
                work_record.product_id,
                work_record.operation_type_id,
                work_record.start_time,
                work_record.end_time,
                duration,
                work_record.comment,
                photos_json,
                "pending_review"
            ))

            record_id = cursor.lastrowid
            conn.commit()
            return record_id
        except Exception as e:
            print(f"❌ Ошибка сохранения работы: {e}")
            return None
        finally:
            conn.close()

    def get_workshop_id_for_user(self, user_id: int) -> Optional[int]:
        """Получить workshop_id для пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT workshop_id FROM users WHERE id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

    def add_user(self, telegram_id, username, full_name, role):
        pass


# ==================== СОСТОЯНИЯ БОТА ====================
class Form(StatesGroup):
    waiting_for_comment = State()
    waiting_for_photos = State()
    review_comment = State()

# ==================== ОСНОВНОЙ БОТ ====================
async def _show_main_menu(message: Message, user: User):
    """Показать главное меню"""
    if user.role.value in ["foreman", "director", "admin"]:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="👥 Мои сотрудники"), KeyboardButton(text="✓ Приемка работ")],
                [KeyboardButton(text="📈 Статистика цеха")],
                [KeyboardButton(text="👤 Сотрудник")]
            ],
            resize_keyboard=True
        )
        await message.answer("👑 Меню администратора:", reply_markup=keyboard)
    else:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Пришел на смену"), KeyboardButton(text="🏁 Ушел со смены")],
                [KeyboardButton(text="🔨 Начать операцию"), KeyboardButton(text="📸 Завершить операцию")],
                [KeyboardButton(text="📊 Мой табель"), KeyboardButton(text="👤 Личный кабинет")]
            ],
            resize_keyboard=True
        )
        await message.answer("Главное меню сотрудника:", reply_markup=keyboard)


class BathProductionBot:
    def __init__(self, token: str):
        self.bot = Bot(token=token)
        self.storage = MemoryStorage()
        self.dp = Dispatcher(storage=self.storage)
        self.db = Database()
        self.active_operations: Dict[int, Dict] = {}  # user_id -> {work_record, photo_count}

        self._register_handlers()

    def _register_handlers(self):
        """Регистрация обработчиков"""

        # /start
        @self.dp.message(CommandStart())
        async def cmd_start(message: Message):
            user = self.db.get_user(message.from_user.id)

            if not user:
                username = message.from_user.username or ""
                full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
                role = "director" if message.from_user.id in ADMIN_IDS else "employee"

                if self.db.add_user(
                        telegram_id=message.from_user.id,username=username, full_name=full_name,role=role
                ):
                    user = self.db.get_user(message.from_user.id)
                else:
                    await message.answer("❌ Ошибка регистрации")
                    return

            await _show_main_menu(message, user)

        # Пришел на смену
        @self.dp.message(F.text == "✅ Пришел на смену")
        async def check_in(message: Message):
            user = self.db.get_user(message.from_user.id)
            if not user:
                await message.answer("❌ Сначала /start")
                return

            if self.db.save_time_record(user.id, datetime.now()):
                await message.answer(f"✅ Приход: {datetime.now().strftime('%H:%M:%S')}")
            else:
                await message.answer("❌ Ошибка сохранения")

        # Ушел со смены
        @self.dp.message(F.text == "🏁 Ушел со смены")
        async def check_out(message: Message):
            user = self.db.get_user(message.from_user.id)
            if not user:
                await message.answer("❌ Сначала /start")
                return

            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, check_in FROM time_records WHERE user_id = ? AND date = date('now') AND check_out IS NULL",
                (user.id,)
            )
            record = cursor.fetchone()

            if not record:
                await message.answer("❌ Сначала отметьте приход!")
                conn.close()
                return

            record_id, check_in_str = record
            check_in_time = datetime.fromisoformat(check_in_str)
            check_out_time = datetime.now()
            total_hours = (check_out_time - check_in_time).total_seconds() / 3600

            cursor.execute(
                "UPDATE time_records SET check_out = ?, total_hours = ? WHERE id = ?",
                (check_out_time, total_hours, record_id)
            )
            conn.commit()
            conn.close()

            await message.answer(f"🏁 Уход: {check_out_time.strftime('%H:%M:%S')}\n⏱️ Отработано: {total_hours:.1f} ч")

        # Начать операцию
        @self.dp.message(F.text == "🔨 Начать операцию")
        async def start_operation(message: Message):
            user = self.db.get_user(message.from_user.id)
            if not user:
                await message.answer("❌ Сначала /start")
                return

            if user.id in self.active_operations:
                await message.answer("❌ Завершите текущую операцию!")
                return

            workshop_id = user.workshop_id or 1

            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, name FROM operation_types WHERE workshop_id = ?",
                (workshop_id,)
            )
            operations = cursor.fetchall()

            if not operations:
                await message.answer("❌ Нет операций для вашего цеха")
                conn.close()
                return

            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            for op_id, op_name in operations:
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(text=op_name, callback_data=f"op_{op_id}")
                ])

            conn.close()
            await message.answer("Выберите операцию:", reply_markup=keyboard)

        # Выбор операции
        @self.dp.callback_query(F.data.startswith("op_"))
        async def select_operation(callback: CallbackQuery):
            op_id = int(callback.data.split("_")[1])
            user = self.db.get_user(callback.from_user.id)

            conn = self.db.get_connection()
            cursor = conn.cursor()

            # Получаем изделия
            workshop_id = user.workshop_id or 1
            cursor.execute(
                "SELECT id, product_number FROM products WHERE workshop_id = ? AND status = 'in_progress'",
                (workshop_id,)
            )
            products = cursor.fetchall()

            if not products:
                await callback.message.answer("❌ Нет изделий в работе")
                conn.close()
                await callback.answer()
                return

            # Сохраняем операцию во временные данные
            self.active_operations[user.id] = {
                'operation_id': op_id,
                'product_id': None,
                'start_time': datetime.now(),
                'photos': [],
                'comment': ""
            }

            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            for prod_id, prod_num in products:
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(text=prod_num, callback_data=f"prod_{prod_id}")
                ])

            conn.close()
            await callback.message.answer("Выберите изделие:", reply_markup=keyboard)
            await callback.answer()

        # Выбор изделия
        @self.dp.callback_query(F.data.startswith("prod_"))
        async def select_product(callback: CallbackQuery):
            prod_id = int(callback.data.split("_")[1])
            user = self.db.get_user(callback.from_user.id)

            if user.id not in self.active_operations:
                await callback.message.answer("❌ Операция не начата")
                await callback.answer()
                return

            # Обновляем данные операции
            self.active_operations[user.id]['product_id'] = prod_id

            conn = self.db.get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT name FROM operation_types WHERE id = ?",
                         (self.active_operations[user.id]['operation_id'],))
            op_name = cursor.fetchone()[0]

            cursor.execute("SELECT product_number FROM products WHERE id = ?", (prod_id,))
            prod_num = cursor.fetchone()[0]

            conn.close()

            await callback.message.answer(f"✅ Операция начата!\n"
                f"🛠️ {op_name}\n"
                f"🏠 {prod_num}\n"
                f"⏰ {self.active_operations[user.id]['start_time'].strftime('%H:%M:%S')}\n\n"
                f"Для завершения нажмите '📸 Завершить операцию'"
            )
            await callback.answer()

        # Завершить операцию
        @self.dp.message(F.text == "📸 Завершить операцию")
        async def finish_operation(message: Message, state: FSMContext):
            user = self.db.get_user(message.from_user.id)

            if user.id not in self.active_operations:
                await message.answer("❌ Нет активной операции")
                return

            op_data = self.active_operations[user.id]
            if not op_data.get('product_id'):
                await message.answer("❌ Вы не выбрали изделие!")
                return

            op_data['end_time'] = datetime.now()
            duration = (op_data['end_time'] - op_data['start_time']).total_seconds() / 3600

            await state.set_state(Form.waiting_for_comment)
            await state.update_data(user_id=user.id, duration=duration)

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Без комментария", callback_data="no_comment")
            ]])

            await message.answer(
                f"⏱️ Длительность: {duration:.1f} часов\n"
                f"Напишите комментарий:",
                reply_markup=keyboard
            )

        # Комментарий
        @self.dp.message(Form.waiting_for_comment)
        async def get_comment(message: Message, state: FSMContext):
            comment = message.text
            data = await state.get_data()
            user_id = data['user_id']

            if user_id in self.active_operations:
                self.active_operations[user_id]['comment'] = comment

            await state.set_state(Form.waiting_for_photos)
            await message.answer("📸 Отправьте фото (1-5 штук). После 5 фото операция сохранится автоматически.")

        # Без комментария
        @self.dp.callback_query(F.data == "no_comment")
        async def no_comment(callback: CallbackQuery, state: FSMContext):
            data = await state.get_data()
            user_id = data['user_id']

            if user_id in self.active_operations:
                self.active_operations[user_id]['comment'] = "Без комментария"

            await state.set_state(Form.waiting_for_photos)
            await callback.message.answer("📸 Отправьте фото (1-5 штук). После 5 фото операция сохранится автоматически.")
            await callback.answer()

        # Фото
        @self.dp.message(Form.waiting_for_photos, F.photo)
        async def get_photos(message: Message, state: FSMContext):
            user = self.db.get_user(message.from_user.id)

            if user.id not in self.active_operations:
                await state.clear()
                await message.answer("❌ Нет активной операции")
                return

            op_data = self.active_operations[user.id]

            # Сохраняем фото
            photo = message.photo[-1]
            file_id = photo.file_id

            file = await self.bot.get_file(file_id)
            file_path = f"data/photos/{file_id}.jpg"
            await self.bot.download_file(file.file_path, file_path)

            op_data['photos'].append(file_path)

            if len(op_data['photos']) >= 5:
                # Сохраняем операцию
                work_record = WorkRecord(
                    user_id=user.id,
                    product_id=op_data['product_id'],
                    operation_type_id=op_data['operation_id'],
                    start_time=op_data['start_time'],
                    end_time=op_data['end_time'],
                    duration=op_data.get('duration') if 'duration' in locals() else 0,comment=op_data['comment'],
                    photos=op_data['photos'],
                    status="pending_review"
                )

                record_id = self.db.save_work_record(work_record)

                if record_id:
                    # Уведомляем бригадира
                    await self._notify_foreman(record_id, work_record)

                    await message.answer(
                        f"✅ Операция сохранена!\n"
                        f"📸 Фото: {len(op_data['photos'])} шт.\n"
                        f"⏱️ Время: {work_record.duration:.1f} ч"
                    )

                    # Удаляем из активных
                    del self.active_operations[user.id]
                    await state.clear()
                else:
                    await message.answer("❌ Ошибка сохранения")
            else:
                await message.answer(f"✅ Фото {len(op_data['photos'])}/5 принято")

        # Мой табель
        @self.dp.message(F.text == "📊 Мой табель")
        async def my_time_sheet(message: Message):
            user = self.db.get_user(message.from_user.id)
            if not user:
                await message.answer("❌ Сначала /start")
                return

            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, check_in, check_out, total_hours 
                FROM time_records 
                WHERE user_id = ? 
                ORDER BY date DESC 
                LIMIT 5
            ''', (user.id,))

            records = cursor.fetchall()
            conn.close()

            if not records:
                await message.answer("📊 Нет записей")
                return

            report = "📊 Ваш табель:\n\n"
            total = 0

            for date_str, check_in, check_out, hours in records:
                check_in_fmt = datetime.fromisoformat(check_in).strftime('%H:%M') if check_in else "—"
                check_out_fmt = datetime.fromisoformat(check_out).strftime('%H:%M') if check_out else "—"

                report += f"📅 {date_str}:\n"
                report += f"  ➕ {check_in_fmt}\n"
                report += f"  ➖ {check_out_fmt}\n"
                report += f"  ⏱️ {hours or 0:.1f} ч\n\n"

                total += hours or 0

            report += f"📈 Всего: {total:.1f} часов"
            await message.answer(report)

        # Личный кабинет
        @self.dp.message(F.text == "👤 Личный кабинет")
        async def personal_cabinet(message: Message):
            user = self.db.get_user(message.from_user.id)
            if not user:
                await message.answer("❌ Сначала /start")
                return

            info = f"👤 {user.full_name}\n"
            info += f"🎭 {user.role.value}\n"

            if user.workshop_id:
                conn = self.db.get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM workshops WHERE id = ?", (user.workshop_id,))
                workshop = cursor.fetchone()
                conn.close()
                if workshop:
                    info += f"🏭 {workshop[0]}"

            await message.answer(info)

        # Переключение на сотрудника
        @self.dp.message(F.text == "👤 Сотрудник")
        async def switch_to_employee(message: Message):
            user = self.db.get_user(message.from_user.id)
            await _show_main_menu(message, user)

        # Статистика цеха
        @self.dp.message(F.text == "📈 Статистика цеха")
        async def workshop_stats(message: Message):
            user = self.db.get_user(message.from_user.id)
            if not user or user.role.value not in ["foreman", "director", "admin"]:
                await message.answer("❌ Нет прав")
                await message.answer("📊 Статистика будет здесь (в разработке)")
                return

        # Приемка работ
        @self.dp.message(F.text == "✓ Приемка работ")
        async def review_works(message: Message):
            user = self.db.get_user(message.from_user.id)
            if not user or user.role.value not in ["foreman", "director", "admin"]:
                await message.answer("❌ Нет прав")
                return

            await message.answer("✓ Приемка работ (в разработке)")

    async def _notify_foreman(self, record_id: int, work_record: WorkRecord):
        """Уведомить бригадира"""
        workshop_id = self.db.get_workshop_id_for_user(work_record.user_id)
        if not workshop_id:
            return

        conn = self.db.get_connection()
        cursor = conn.cursor()

        # Информация об операции
        cursor.execute('''
            SELECT u.full_name, ot.name, p.product_number
            FROM users u, operation_types ot, products p
            WHERE u.id = ? AND ot.id = ? AND p.id = ?
        ''', (work_record.user_id, work_record.operation_type_id, work_record.product_id))

        result = cursor.fetchone()
        if not result:
            conn.close()
            return

        emp_name, op_name, prod_num = result

        # Находим бригадиров
        cursor.execute(
            "SELECT telegram_id FROM users WHERE workshop_id = ? AND role IN ('foreman', 'director')",
            (workshop_id,)
        )
        foremen = cursor.fetchall()
        conn.close()

        for foreman_id, in foremen:
            try:
                await self.bot.send_message(
                    foreman_id,
                    f"📋 Новая операция #{record_id}\n"
                    f"👤 {emp_name}\n"
                    f"🛠️ {op_name}\n"
                    f"🏠 {prod_num}\n"
                    f"⏱️ {work_record.duration:.1f} ч\n"
                    f"💬 {work_record.comment[:50]}..."
                )
            except:
                pass

    async def run(self):
        """Запуск бота"""
        print("🤖 Бот запускается...")
        await self.dp.start_polling(self.bot, skip_updates=True)

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    TOKEN = BOT_TOKEN

    if TOKEN == "ВАШ_ТОКЕН_БОТА":
        print("❌ ЗАМЕНИТЕ BOT_TOKEN НА ВАШ ТОКЕН!")
        print("1. @BotFather в Telegram")
        print("2. /newbot")
        print("3. Скопируйте токен")
        print("4. Вставьте в BOT_TOKEN = 'ваш_токен'")
    else:
        bot = BathProductionBot(TOKEN)

        try:
            print("✅ База данных инициализирована")
            print("✅ Бот запущен")
            print("✅ Откройте Telegram и найдите бота")
            print("✅ Напишите /start")
            asyncio.run(bot.run())
        except KeyboardInterrupt:
            print("\n🛑 Бот остановлен")