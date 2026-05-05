#!/usr/bin/env python3
"""
Expense Tracker Bot для Telegram
ПойПро — Фиксишоу & Синий Трактор
"""

import os
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from telegram.constants import ParseMode
import gspread
from google.oauth2.service_account import Credentials
import json

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния диалога
(MENU, DIRECTION, SUBTYPE, SUBPROJECT, GASTROL_DATE, GASTROL_CITY,
 CATEGORY, AMOUNT, DESCRIPTION, RECEIPT, CONFIRM, EDIT_CHOICE) = range(12)

DIRECTIONS = ['СТ — Синий Трактор', 'ФШ — Фиксишоу']

SUBTYPES = {
    'СТ': ['Гастроль', 'Франшиза', 'Проект'],
    'ФШ': ['Гастроль', 'Проект'],
}

CATEGORIES = [
    'Чистка костюмов',
    'Изготовление/покупка костюмов',
    'Изготовление, покупка и ремонт реквизита',
    'ЗП налом',
    'Доставка',
    'Такси',
    'Аренда зала',
    'Другое',
]


class GoogleSheetsManager:
    def __init__(self):
        self.spreadsheet = None
        self._connect()

    def _connect(self):
        try:
            creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
            sheets_id = os.getenv('GOOGLE_SHEETS_ID')
            if not creds_json or not sheets_id:
                logger.warning("Не заданы GOOGLE_CREDENTIALS_JSON или GOOGLE_SHEETS_ID")
                return
            creds_data = json.loads(creds_json)
            creds = Credentials.from_service_account_info(
                creds_data,
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
            client = gspread.authorize(creds)
            self.spreadsheet = client.open_by_key(sheets_id)
            logger.info("Google Sheets подключен успешно")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")

    def get_sheet(self, name: str):
        try:
            return self.spreadsheet.worksheet(name)
        except Exception as e:
            logger.error(f"Ошибка получения листа {name}: {e}")
            return None

    def get_projects(self, direction: str, ptype: str) -> List[str]:
        """Получить список проектов из справочника"""
        try:
            sheet = self.get_sheet('Справочники')
            if not sheet:
                return []
            rows = sheet.get_all_values()
            result = []
            for row in rows[1:]:
                if len(row) >= 3 and row[0].strip() == direction and row[1].strip() == ptype:
                    result.append(row[2].strip())
            return result
        except Exception as e:
            logger.error(f"Ошибка получения справочника: {e}")
            return []

    def add_expense(self, data: Dict) -> bool:
        try:
            sheet = self.get_sheet('Расходы')
            if not sheet:
                return False
            row = [
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                data.get('user_name', ''),
                data.get('project', ''),
                data.get('subproject', ''),
                data.get('category', ''),
                data.get('amount', ''),
                data.get('description', ''),
                data.get('receipt_url', ''),
                'Не принят',
                'Нет',
            ]
            sheet.append_row(row)
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении расхода: {e}")
            return False

    def check_duplicate(self, user_name: str, project: str, subproject: str,
                        category: str, amount: str) -> bool:
        """Проверить наличие дубликата за последние 24 часа"""
        try:
            sheet = self.get_sheet('Расходы')
            if not sheet:
                return False
            rows = sheet.get_all_values()
            for row in rows[1:]:
                if len(row) >= 6:
                    if (row[1] == user_name and row[2] == project and
                            row[3] == subproject and row[4] == category and
                            row[5] == amount):
                        return True
            return False
        except Exception as e:
            logger.error(f"Ошибка проверки дубликата: {e}")
            return False

    def get_balance(self, user_name: str) -> Dict:
        """Получить баланс сотрудника"""
        try:
            # Выдано
            issued = 0.0
            sheet_v = self.get_sheet('Выдано')
            if sheet_v:
                rows = sheet_v.get_all_values()
                for row in rows[1:]:
                    if len(row) >= 4 and row[1] == user_name and row[3] == 'Нет':
                        try:
                            issued += float(str(row[2]).replace(',', '.'))
                        except:
                            pass

            # Принятые расходы
            accepted = 0.0
            sheet_r = self.get_sheet('Расходы')
            if sheet_r:
                rows = sheet_r.get_all_values()
                for row in rows[1:]:
                    if (len(row) >= 10 and row[1] == user_name and
                            row[8] == 'Принят' and row[9] == 'Нет'):
                        try:
                            accepted += float(str(row[5]).replace(',', '.'))
                        except:
                            pass

            # Компенсации
            compensated = 0.0
            sheet_k = self.get_sheet('Компенсации')
            if sheet_k:
                rows = sheet_k.get_all_values()
                for row in rows[1:]:
                    if len(row) >= 5 and row[1] == user_name and row[4] == 'Нет':
                        try:
                            compensated += float(str(row[2]).replace(',', '.'))
                        except:
                            pass

            balance = issued - accepted - compensated
            return {
                'issued': issued,
                'accepted': accepted,
                'compensated': compensated,
                'balance': balance,
            }
        except Exception as e:
            logger.error(f"Ошибка расчёта баланса: {e}")
            return {'issued': 0, 'accepted': 0, 'compensated': 0, 'balance': 0}

    def get_my_expenses(self, user_name: str) -> List[Dict]:
        """Получить расходы сотрудника"""
        try:
            sheet = self.get_sheet('Расходы')
            if not sheet:
                return []
            rows = sheet.get_all_values()
            result = []
            for row in rows[1:]:
                if len(row) >= 9 and row[1] == user_name:
                    result.append({
                        'date': row[0],
                        'amount': row[5],
                        'category': row[4],
                        'status': row[8],
                    })
            return result[-20:]  # последние 20
        except Exception as e:
            logger.error(f"Ошибка получения расходов: {e}")
            return []


def make_keyboard(items: List[str], cols: int = 2) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(item, callback_data=item) for item in items]
    rows = [buttons[i:i + cols] for i in range(0, len(buttons), cols)]
    return InlineKeyboardMarkup(rows)


def full_name(user) -> str:
    name = user.first_name or ''
    if user.last_name:
        name += f' {user.last_name}'
    return name.strip() or user.username or 'Неизвестно'


def format_amount(text: str) -> Optional[str]:
    """Конвертировать сумму: принять точку или запятую, вернуть с запятой"""
    try:
        cleaned = text.strip().replace(' ', '').replace('.', ',')
        value = float(cleaned.replace(',', '.'))
        if value <= 0:
            return None
        # Форматируем: если целое — без дробей, иначе с двумя знаками
        if value == int(value):
            return str(int(value))
        else:
            return f"{value:.2f}".replace('.', ',')
    except:
        return None


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить расход", callback_data="menu_add")],
        [InlineKeyboardButton("💰 Остаток", callback_data="menu_balance")],
        [InlineKeyboardButton("📋 Мои расходы", callback_data="menu_myexpenses")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = full_name(update.effective_user)
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        "Выберите действие:",
        reply_markup=main_menu_keyboard()
    )
    return MENU


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "menu_add":
        await query.edit_message_text(
            "🏗 <b>Выберите направление:</b>",
            reply_markup=make_keyboard(DIRECTIONS, cols=1),
            parse_mode=ParseMode.HTML
        )
        return DIRECTION

    elif query.data == "menu_balance":
        user_name = full_name(update.effective_user)
        gs = GoogleSheetsManager()
        b = gs.get_balance(user_name)
        await query.edit_message_text(
            f"💰 <b>Ваш баланс</b>\n\n"
            f"Выдано: <b>{b['issued']:,.2f} ₽</b>\n"
            f"Принято расходов: <b>{b['accepted']:,.2f} ₽</b>\n"
            f"Компенсировано: <b>{b['compensated']:,.2f} ₽</b>\n"
            f"─────────────────\n"
            f"Остаток: <b>{b['balance']:,.2f} ₽</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Меню", callback_data="back_menu")
            ]]),
            parse_mode=ParseMode.HTML
        )
        return MENU

    elif query.data == "menu_myexpenses":
        user_name = full_name(update.effective_user)
        gs = GoogleSheetsManager()
        expenses = gs.get_my_expenses(user_name)
        if not expenses:
            text = "📋 <b>Мои расходы</b>\n\nРасходов пока нет."
        else:
            lines = ["📋 <b>Мои расходы (последние 20)</b>\n"]
            for e in expenses:
                status_emoji = "✅" if e['status'] == 'Принят' else "⏳"
                lines.append(f"{status_emoji} {e['date'][:10]} | {e['amount']} ₽ | {e['category']}")
            text = "\n".join(lines)
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Меню", callback_data="back_menu")
            ]]),
            parse_mode=ParseMode.HTML
        )
        return MENU

    elif query.data == "back_menu":
        await query.edit_message_text(
            "Выберите действие:",
            reply_markup=main_menu_keyboard()
        )
        return MENU


async def direction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    code = query.data.split(' — ')[0]
    context.user_data.clear()
    context.user_data['direction_code'] = code
    context.user_data['direction_name'] = 'Синий Трактор' if code == 'СТ' else 'Фиксишоу'

    subtypes = SUBTYPES[code]
    await query.edit_message_text(
        f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n\n"
        "📌 Выберите тип:",
        reply_markup=make_keyboard(subtypes, cols=1),
        parse_mode=ParseMode.HTML
    )
    return SUBTYPE


async def subtype_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    subtype = query.data
    context.user_data['subtype'] = subtype
    code = context.user_data['direction_code']

    if subtype == 'Гастроль':
        await query.edit_message_text(
            f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n"
            f"✅ Тип: <b>Гастроль</b>\n\n"
            "📅 Введите дату гастроли в формате <b>ДД.ММ</b>:",
            parse_mode=ParseMode.HTML
        )
        return GASTROL_DATE

    elif subtype in ('Франшиза',):
        # Нет подпроекта — сразу категория
        context.user_data['subproject'] = subtype
        return await _ask_category_msg_edit(query, context)

    else:
        # Проект — читаем из справочника
        gs = GoogleSheetsManager()
        projects = gs.get_projects(code, 'Проект')
        if not projects:
            projects = ['Нет данных в справочнике']
        await query.edit_message_text(
            f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n"
            f"✅ Тип: <b>{subtype}</b>\n\n"
            "📌 Выберите проект:",
            reply_markup=make_keyboard(projects, cols=1),
            parse_mode=ParseMode.HTML
        )
        return SUBPROJECT


async def gastrol_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    # Валидация формата ДД.ММ
    parts = text.split('.')
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await update.message.reply_text(
            "❌ Неверный формат. Введите дату в формате <b>ДД.ММ</b>, например <b>15.06</b>:",
            parse_mode=ParseMode.HTML
        )
        return GASTROL_DATE

    context.user_data['gastrol_date'] = text
    await update.message.reply_text(
        f"✅ Дата: <b>{text}</b>\n\n"
        "🏙 Введите город или площадку (или напишите <b>-</b> чтобы пропустить):",
        parse_mode=ParseMode.HTML
    )
    return GASTROL_CITY


async def gastrol_city_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    date = context.user_data['gastrol_date']
    if text == '-' or not text:
        context.user_data['subproject'] = f"Гастроль — {date}"
    else:
        context.user_data['subproject'] = f"Гастроль — {date}, {text}"
    return await _ask_category_msg(update.message, context)


async def subproject_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['subproject'] = query.data
    return await _ask_category_msg_edit(query, context)


async def _ask_category_msg_edit(query, context):
    keyboard = make_keyboard(CATEGORIES, cols=1)
    await query.edit_message_text(
        f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n"
        f"✅ Подпроект: <b>{context.user_data['subproject']}</b>\n\n"
        "🏷 Выберите категорию:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return CATEGORY


async def _ask_category_msg(message, context):
    keyboard = make_keyboard(CATEGORIES, cols=1)
    await message.reply_text(
        f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n"
        f"✅ Подпроект: <b>{context.user_data['subproject']}</b>\n\n"
        "🏷 Выберите категорию:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return CATEGORY


async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['category'] = query.data
    await query.edit_message_text(
        f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n"
        f"✅ Подпроект: <b>{context.user_data['subproject']}</b>\n"
        f"✅ Категория: <b>{query.data}</b>\n\n"
        "💰 Введите сумму (например <b>1883,75</b> или <b>1883</b>):",
        parse_mode=ParseMode.HTML
    )
    return AMOUNT


async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount_str = format_amount(update.message.text)
    if not amount_str:
        await update.message.reply_text(
            "❌ Неверный формат. Введите сумму числом, например <b>1883,75</b> или <b>1883</b>:",
            parse_mode=ParseMode.HTML
        )
        return AMOUNT

    context.user_data['amount'] = amount_str
    hint = ""
    if context.user_data.get('category') == 'Другое':
        hint = "\n\n⚠️ Категория «Другое» — опишите подробно что именно оплачено."
    await update.message.reply_text(
        f"✅ Сумма: <b>{amount_str} ₽</b>\n\n"
        f"📝 Введите описание:{hint}",
        parse_mode=ParseMode.HTML
    )
    return DESCRIPTION


async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Описание обязательно. Введите описание:")
        return DESCRIPTION
    context.user_data['description'] = text
    await update.message.reply_text(
        f"✅ Описание: <b>{text}</b>\n\n"
        "📸 Отправьте фото чека:",
        parse_mode=ParseMode.HTML
    )
    return RECEIPT


async def receipt_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text(
            "❌ Нужно отправить фото чека. Сфотографируйте чек и отправьте:"
        )
        return RECEIPT

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    context.user_data['receipt_url'] = file.file_path
    context.user_data['receipt_file_id'] = photo.file_id

    # Проверка дубликата
    gs = GoogleSheetsManager()
    user_name = full_name(update.effective_user)
    is_dup = gs.check_duplicate(
        user_name,
        context.user_data['direction_name'],
        context.user_data['subproject'],
        context.user_data['category'],
        context.user_data['amount']
    )
    dup_warning = "\n\n⚠️ <b>Внимание: похожий расход уже существует!</b>" if is_dup else ""

    d = context.user_data
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_yes"),
            InlineKeyboardButton("✏️ Изменить", callback_data="confirm_edit"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="confirm_no")],
    ])
    await update.message.reply_text(
        f"📋 <b>Проверьте данные:</b>{dup_warning}\n\n"
        f"🏗 Направление: <b>{d['direction_name']}</b>\n"
        f"📌 Подпроект: <b>{d['subproject']}</b>\n"
        f"🏷 Категория: <b>{d['category']}</b>\n"
        f"💰 Сумма: <b>{d['amount']} ₽</b>\n"
        f"📝 Описание: <b>{d['description']}</b>\n"
        f"✅ Чек загружен",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return CONFIRM


async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == 'confirm_no':
        await query.edit_message_text("❌ Отменено.")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Выберите действие:",
            reply_markup=main_menu_keyboard()
        )
        return MENU

    if query.data == 'confirm_edit':
        d = context.user_data
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📌 Подпроект", callback_data="edit_subproject")],
            [InlineKeyboardButton("🏷 Категория", callback_data="edit_category")],
            [InlineKeyboardButton("💰 Сумма", callback_data="edit_amount")],
            [InlineKeyboardButton("📝 Описание", callback_data="edit_description")],
            [InlineKeyboardButton("📸 Чек", callback_data="edit_receipt")],
        ])
        await query.edit_message_text(
            "✏️ Что хотите изменить?",
            reply_markup=keyboard
        )
        return EDIT_CHOICE

    if query.data == 'confirm_yes':
        d = context.user_data
        user = update.effective_user
        expense = {
            'user_name': full_name(user),
            'project': d['direction_name'],
            'subproject': d['subproject'],
            'category': d['category'],
            'amount': d['amount'],
            'description': d['description'],
            'receipt_url': d.get('receipt_url', ''),
        }
        gs = GoogleSheetsManager()
        ok = gs.add_expense(expense)

        if ok:
            await query.edit_message_text(
                f"✅ <b>Расход сохранён!</b>\n\n"
                f"Проект: {expense['project']} / {expense['subproject']}\n"
                f"Сумма: {expense['amount']} ₽\n"
                f"Статус: Не принят",
                parse_mode=ParseMode.HTML
            )
            # Уведомление администратору
            admin_id = int(os.getenv('ADMIN_CHAT_ID', 0))
            if admin_id:
                try:
                    caption = (
                        f"📊 <b>Новый расход</b>\n\n"
                        f"👤 {expense['user_name']}\n"
                        f"🏗 {expense['project']} / {expense['subproject']}\n"
                        f"🏷 {expense['category']}\n"
                        f"💰 {expense['amount']} ₽\n"
                        f"📝 {expense['description']}\n"
                        f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                    )
                    if d.get('receipt_file_id'):
                        await context.bot.send_photo(
                            chat_id=admin_id,
                            photo=d['receipt_file_id'],
                            caption=caption,
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=caption,
                            parse_mode=ParseMode.HTML
                        )
                except Exception as e:
                    logger.error(f"Ошибка уведомления: {e}")
        else:
            await query.edit_message_text(
                "❌ Ошибка при сохранении. Попробуйте ещё раз или обратитесь к Ануш."
            )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Выберите действие:",
            reply_markup=main_menu_keyboard()
        )
        return MENU


async def edit_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    field = query.data

    if field == 'edit_category':
        await query.edit_message_text(
            "🏷 Выберите новую категорию:",
            reply_markup=make_keyboard(CATEGORIES, cols=1)
        )
        context.user_data['editing'] = 'category'
        return CATEGORY

    elif field == 'edit_amount':
        await query.edit_message_text("💰 Введите новую сумму:")
        context.user_data['editing'] = 'amount'
        return AMOUNT

    elif field == 'edit_description':
        await query.edit_message_text("📝 Введите новое описание:")
        context.user_data['editing'] = 'description'
        return DESCRIPTION

    elif field == 'edit_receipt':
        await query.edit_message_text("📸 Отправьте новое фото чека:")
        context.user_data['editing'] = 'receipt'
        return RECEIPT

    elif field == 'edit_subproject':
        code = context.user_data.get('direction_code', 'СТ')
        subtype = context.user_data.get('subtype', 'Проект')
        if subtype == 'Гастроль':
            await query.edit_message_text(
                "📅 Введите новую дату гастроли (ДД.ММ):"
            )
            return GASTROL_DATE
        else:
            gs = GoogleSheetsManager()
            projects = gs.get_projects(code, 'Проект')
            if not projects:
                projects = ['Нет данных']
            await query.edit_message_text(
                "📌 Выберите новый проект:",
                reply_markup=make_keyboard(projects, cols=1)
            )
            return SUBPROJECT


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = full_name(update.effective_user)
    gs = GoogleSheetsManager()
    b = gs.get_balance(user_name)
    await update.message.reply_text(
        f"💰 <b>Ваш баланс</b>\n\n"
        f"Выдано: <b>{b['issued']:,.2f} ₽</b>\n"
        f"Принято расходов: <b>{b['accepted']:,.2f} ₽</b>\n"
        f"Компенсировано: <b>{b['compensated']:,.2f} ₽</b>\n"
        f"─────────────────\n"
        f"Остаток: <b>{b['balance']:,.2f} ₽</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )


async def myexpenses_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = full_name(update.effective_user)
    gs = GoogleSheetsManager()
    expenses = gs.get_my_expenses(user_name)
    if not expenses:
        text = "📋 <b>Мои расходы</b>\n\nРасходов пока нет."
    else:
        lines = ["📋 <b>Мои расходы (последние 20)</b>\n"]
        for e in expenses:
            status_emoji = "✅" if e['status'] == 'Принят' else "⏳"
            lines.append(f"{status_emoji} {e['date'][:10]} | {e['amount']} ₽ | {e['category']}")
        text = "\n".join(lines)
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Все", callback_data="export_all")],
        [InlineKeyboardButton("Принятые", callback_data="export_accepted")],
        [InlineKeyboardButton("Не принятые", callback_data="export_rejected")],
    ])
    await update.message.reply_text(
        "📤 <b>Выгрузка расходов</b>\n\nВыберите фильтр:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )


async def export_filter_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_name = full_name(update.effective_user)
    gs = GoogleSheetsManager()

    filter_map = {
        'export_all': None,
        'export_accepted': 'Принят',
        'export_rejected': 'Не принят',
    }
    status_filter = filter_map.get(query.data)

    try:
        sheet = gs.get_sheet('Расходы')
        rows = sheet.get_all_values()
        result = []
        for row in rows[1:]:
            if len(row) >= 9 and row[1] == user_name:
                if status_filter is None or row[8] == status_filter:
                    result.append(row)

        if not result:
            await query.edit_message_text("📤 Расходов по данному фильтру нет.")
            return

        lines = [f"📤 <b>Выгрузка расходов</b>\n"]
        for row in result[-30:]:
            status_emoji = "✅" if row[8] == 'Принят' else "⏳"
            lines.append(
                f"{status_emoji} {row[0][:10]} | {row[3]} | {row[4]} | {row[5]} ₽"
            )
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка выгрузки: {e}")
        await query.edit_message_text("❌ Ошибка при выгрузке.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❌ Отменено.",
        reply_markup=main_menu_keyboard()
    )
    return MENU


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Справка</b>\n\n"
        "/start — главное меню\n"
        "/add — добавить расход\n"
        "/balance — мой баланс\n"
        "/myexpenses — мои расходы\n"
        "/export — выгрузка\n"
        "/cancel — отменить\n\n"
        "<b>Направления:</b>\n"
        "🚜 СТ — Синий Трактор\n"
        "🎭 ФШ — Фиксишоу",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )


def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан!")

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('add', lambda u, c: u.message.reply_text(
                "🏗 <b>Выберите направление:</b>",
                reply_markup=make_keyboard(DIRECTIONS, cols=1),
                parse_mode=ParseMode.HTML
            )),
        ],
        states={
            MENU: [CallbackQueryHandler(menu_handler)],
            DIRECTION: [CallbackQueryHandler(direction_selected)],
            SUBTYPE: [CallbackQueryHandler(subtype_selected)],
            SUBPROJECT: [CallbackQueryHandler(subproject_selected)],
            GASTROL_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, gastrol_date_received)],
            GASTROL_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, gastrol_city_received)],
            CATEGORY: [CallbackQueryHandler(category_selected)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_received)],
            RECEIPT: [
                MessageHandler(filters.PHOTO, receipt_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_received),
            ],
            CONFIRM: [CallbackQueryHandler(confirm_handler)],
            EDIT_CHOICE: [CallbackQueryHandler(edit_choice_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler('balance', balance_command))
    app.add_handler(CommandHandler('myexpenses', myexpenses_command))
    app.add_handler(CommandHandler('export', export_command))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CallbackQueryHandler(export_filter_handler, pattern='^export_'))

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == '__main__':
    main()
