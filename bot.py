#!/usr/bin/env python3
"""
Expense Tracker Bot для Telegram
ПойПро — Фиксишоу & Синий Трактор
"""

import os
import logging
import requests
from datetime import datetime
from typing import Dict, List, Optional
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from gspread.exceptions import WorksheetNotFound
from google.oauth2.service_account import Credentials
import json

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

(MENU, DIRECTION, SUBTYPE, SUBPROJECT, GASTROL_CHOOSE, GASTROL_DATE,
 GASTROL_YEAR, GASTROL_CITY, CATEGORY, AMOUNT, DESCRIPTION,
 RECEIPT, CONFIRM, EDIT_CHOICE) = range(14)

DIRECTIONS = ['СТ — Синий Трактор', 'ФШ — Фиксишоу']

SUBTYPES = {
    'СТ': ['Гастроль', 'Франшиза', 'Проект'],
    'ФШ': ['Гастроль', 'Проект'],
}

CATEGORIES = [
    'Чистка костюмов',
    'Изготовление/покупка костюмов',
    'Изготовление и ремонт реквизита',
    'Доставка',
    'Такси',
    'Аренда зала',
    'Другое',
]

CAT_CODES = {f'cat_{i}': cat for i, cat in enumerate(CATEGORIES)}

SHEET_HEADERS = [
    'Тип', 'Дата', 'Сумма', 'Гастроль/Проект',
    'Статья расхода', 'Описание расхода', 'Расход принят', 'Закрыто', 'Чек'
]

# Исправленные формулы баланса — L2=Выдано, L3=Принято, L4=В проверке, L5=Компенсировано
BALANCE_DATA = [
    ['Баланс', ''],
    ['Выдано', '=SUMPRODUCT((A2:A1000="Выдано")*(H2:H1000<>TRUE)*C2:C1000)'],
    ['Принято расходов', '=SUMPRODUCT((A2:A1000="Расход")*(G2:G1000=TRUE)*(H2:H1000<>TRUE)*C2:C1000)'],
    ['В проверке', '=SUMPRODUCT((A2:A1000="Расход")*(G2:G1000<>TRUE)*(H2:H1000<>TRUE)*C2:C1000)'],
    ['Компенсировано', '=SUMPRODUCT((A2:A1000="Компенсация")*(H2:H1000<>TRUE)*C2:C1000)'],
    ['Остаток', '=L2-L3-L5'],
    ['Реально на руках', '=L2-L3-L4-L5'],
]

PHOTO_ROW_HEIGHT = 150  # высота строки с фото в пикселях


def parse_date(text: str) -> Optional[tuple]:
    """Принимает ДД.ММ или ДД/ММ, проверяет корректность месяца"""
    text = text.strip().replace('/', '.')
    parts = text.split('.')
    if len(parts) != 2:
        return None
    try:
        day, month = int(parts[0]), int(parts[1])
        if not (1 <= month <= 12):
            return None
        if not (1 <= day <= 31):
            return None
        return day, month
    except:
        return None


def upload_to_yandex_disk(file_bytes: bytes, filename: str, user_name: str) -> Optional[str]:
    token = os.getenv('YANDEX_DISK_TOKEN')
    if not token:
        return None
    try:
        now = datetime.now()
        folder = f"Чеки/{user_name}/{now.year}/{now.month:02d}"
        for path in ["Чеки", f"Чеки/{user_name}",
                     f"Чеки/{user_name}/{now.year}", folder]:
            requests.put(
                "https://cloud-api.yandex.net/v1/disk/resources",
                headers={"Authorization": f"OAuth {token}"},
                params={"path": path},
                timeout=10
            )
        disk_path = f"{folder}/{filename}"
        resp = requests.get(
            "https://cloud-api.yandex.net/v1/disk/resources/upload",
            headers={"Authorization": f"OAuth {token}"},
            params={"path": disk_path, "overwrite": "true"},
            timeout=10
        )
        upload_url = resp.json().get("href")
        if not upload_url:
            return None
        requests.put(upload_url, data=file_bytes, timeout=30)
        requests.put(
            "https://cloud-api.yandex.net/v1/disk/resources/publish",
            headers={"Authorization": f"OAuth {token}"},
            params={"path": disk_path},
            timeout=10
        )
        download_resp = requests.get(
            "https://cloud-api.yandex.net/v1/disk/resources/download",
            headers={"Authorization": f"OAuth {token}"},
            params={"path": disk_path},
            timeout=10
        )
        return download_resp.json().get("href")
    except Exception as e:
        logger.error(f"Яндекс Диск ошибка: {e}")
        return None


class GoogleSheetsManager:
    def __init__(self):
        self.spreadsheet = None
        self._connect()

    def _connect(self):
        try:
            creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
            sheets_id = os.getenv('GOOGLE_SHEETS_ID')
            if not creds_json or not sheets_id:
                return
            creds_data = json.loads(creds_json)
            creds = Credentials.from_service_account_info(
                creds_data,
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
            client = gspread.authorize(creds)
            self.spreadsheet = client.open_by_key(sheets_id)
            logger.info("Google Sheets подключен")
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")

    def get_or_create_employee_sheet(self, user_name: str):
        try:
            try:
                return self.spreadsheet.worksheet(user_name)
            except WorksheetNotFound:
                sheet = self.spreadsheet.add_worksheet(
                    title=user_name, rows=1000, cols=15
                )
                # Заголовки A1:I1
                sheet.update('A1:I1', [SHEET_HEADERS])
                sheet.format('A1:I1', {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                })

                # Флажки в G2:H1000
                self.spreadsheet.batch_update({
                    "requests": [
                        {
                            "repeatCell": {
                                "range": {
                                    "sheetId": sheet.id,
                                    "startRowIndex": 1,
                                    "endRowIndex": 1000,
                                    "startColumnIndex": 6,
                                    "endColumnIndex": 8
                                },
                                "cell": {
                                    "dataValidation": {
                                        "condition": {"type": "BOOLEAN"},
                                        "strict": True
                                    }
                                },
                                "fields": "dataValidation"
                            }
                        },
                        # Высота строк для фото — 150px по умолчанию
                        {
                            "updateDimensionProperties": {
                                "range": {
                                    "sheetId": sheet.id,
                                    "dimension": "ROWS",
                                    "startIndex": 1,
                                    "endIndex": 1000
                                },
                                "properties": {"pixelSize": PHOTO_ROW_HEIGHT},
                                "fields": "pixelSize"
                            }
                        },
                        # Ширина столбца I (Чек) — 200px
                        {
                            "updateDimensionProperties": {
                                "range": {
                                    "sheetId": sheet.id,
                                    "dimension": "COLUMNS",
                                    "startIndex": 8,
                                    "endIndex": 9
                                },
                                "properties": {"pixelSize": 200},
                                "fields": "pixelSize"
                            }
                        }
                    ]
                })

                # Баланс K1:L7 с исправленными формулами
                sheet.update('K1:L7', BALANCE_DATA, value_input_option='USER_ENTERED')
                sheet.format('K1:L1', {'textFormat': {'bold': True}})

                logger.info(f"Создан лист для {user_name}")
                return sheet
        except Exception as e:
            logger.error(f"Ошибка листа: {e}")
            return None

    def _next_empty_row(self, sheet) -> int:
        col_a = sheet.col_values(1)
        return len(col_a) + 1

    def get_subprojects(self, direction: str, ptype: str) -> List[str]:
        try:
            sheet = self.spreadsheet.worksheet('Справочники')
            rows = sheet.get_all_values()
            return [
                row[2].strip() for row in rows[1:]
                if len(row) >= 3 and row[0].strip() == direction
                and row[1].strip() == ptype
            ]
        except Exception as e:
            logger.error(f"Справочник ошибка: {e}")
            return []

    def add_expense(self, user_name: str, data: Dict) -> bool:
        try:
            sheet = self.get_or_create_employee_sheet(user_name)
            if not sheet:
                return False

            receipt_url = data.get('receipt_url', '')
            if receipt_url and 'yandex' in receipt_url:
                # IMAGE с размером 2 = растянуть по ячейке
                receipt_cell = f'=IMAGE("{receipt_url}",2)'
            else:
                receipt_cell = receipt_url

            row_data = [
                'Расход',
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                data.get('amount', ''),
                data.get('subproject', ''),
                data.get('category', ''),
                data.get('description', ''),
                False,
                False,
                receipt_cell,
            ]

            next_row = self._next_empty_row(sheet)
            sheet.update(
                f'A{next_row}:I{next_row}',
                [row_data],
                value_input_option='USER_ENTERED'
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления: {e}")
            return False

    def check_duplicate(self, user_name: str, subproject: str,
                        category: str, amount: str) -> bool:
        try:
            sheet = self.get_or_create_employee_sheet(user_name)
            if not sheet:
                return False
            rows = sheet.get_all_values()
            for row in rows[1:]:
                if len(row) >= 6:
                    if (row[3] == subproject and row[4] == category
                            and str(row[2]) == amount):
                        return True
            return False
        except:
            return False

    def get_balance(self, user_name: str) -> Dict:
        try:
            sheet = self.get_or_create_employee_sheet(user_name)
            if not sheet:
                return self._empty_balance()
            rows = sheet.get_all_values()

            issued = accepted = pending = compensated = 0.0

            for row in rows[1:]:
                if len(row) < 8 or not row[0]:
                    continue
                try:
                    amount = float(str(row[2]).replace(' ', '').replace(',', '.'))
                except:
                    continue

                closed = str(row[7]).upper() in ('TRUE', 'ИСТИНА', '1')
                if closed:
                    continue

                accepted_flag = str(row[6]).upper() in ('TRUE', 'ИСТИНА', '1')
                rtype = row[0].strip()

                if rtype == 'Выдано':
                    issued += amount
                elif rtype == 'Расход':
                    if accepted_flag:
                        accepted += amount
                    else:
                        pending += amount
                elif rtype == 'Компенсация':
                    compensated += amount

            return {
                'issued': issued, 'accepted': accepted,
                'pending': pending, 'compensated': compensated,
                'balance': issued - accepted - compensated,
                'real_balance': issued - accepted - pending - compensated,
            }
        except Exception as e:
            logger.error(f"Баланс ошибка: {e}")
            return self._empty_balance()

    def _empty_balance(self):
        return {'issued': 0, 'accepted': 0, 'pending': 0,
                'compensated': 0, 'balance': 0, 'real_balance': 0}

    def get_my_expenses(self, user_name: str) -> List[Dict]:
        try:
            sheet = self.get_or_create_employee_sheet(user_name)
            if not sheet:
                return []
            rows = sheet.get_all_values()
            result = []
            for row in rows[1:]:
                if len(row) >= 5 and row[0] == 'Расход':
                    accepted = str(row[6]).upper() in ('TRUE', 'ИСТИНА', '1') if len(row) > 6 else False
                    result.append({
                        'date': row[1],
                        'amount': row[2],
                        'category': row[4],
                        'status': '✅ Принят' if accepted else '🔍 В проверке',
                    })
            return result[-20:]
        except:
            return []


def make_keyboard(items: List[str], cols: int = 2) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(item, callback_data=item) for item in items]
    rows = [buttons[i:i + cols] for i in range(0, len(buttons), cols)]
    return InlineKeyboardMarkup(rows)


def make_category_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(cat, callback_data=f'cat_{i}')
        for i, cat in enumerate(CATEGORIES)
    ]
    return InlineKeyboardMarkup([[b] for b in buttons])


def full_name(user) -> str:
    name = user.first_name or ''
    if user.last_name:
        name += f' {user.last_name}'
    return name.strip() or user.username or 'Неизвестно'


def format_amount(text: str) -> Optional[str]:
    try:
        cleaned = text.strip().replace(' ', '').replace('.', ',')
        value = float(cleaned.replace(',', '.'))
        if value <= 0:
            return None
        if value == int(value):
            return str(int(value))
        return f"{value:.2f}".replace('.', ',')
    except:
        return None


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить расход", callback_data="menu_add")],
        [InlineKeyboardButton("💰 Остаток", callback_data="menu_balance")],
        [InlineKeyboardButton("📋 Мои расходы", callback_data="menu_myexpenses")],
    ])


def format_balance(b: Dict) -> str:
    text = "💰 <b>Ваш баланс</b>\n\n"
    text += f"Выдано: <b>{b['issued']:,.2f} ₽</b>\n"
    text += f"\n✅ Принято расходов: <b>{b['accepted']:,.2f} ₽</b>\n"
    text += f"🔍 В проверке: <b>{b['pending']:,.2f} ₽</b>\n"
    text += f"💸 Компенсировано: <b>{b['compensated']:,.2f} ₽</b>\n"
    text += f"─────────────────\n"
    text += f"Остаток (принятые): <b>{b['balance']:,.2f} ₽</b>\n"
    text += f"Реально на руках: <b>{b['real_balance']:,.2f} ₽</b>"
    return text


def get_confirm_text(d: Dict) -> str:
    receipt_status = "✅ Чек на Яндекс Диске" if d.get('receipt_on_yandex') else "✅ Чек загружен"
    return (
        f"📋 <b>Проверьте данные:</b>\n\n"
        f"🏗 Направление: <b>{d.get('direction_name', '')}</b>\n"
        f"📌 Гастроль/Проект: <b>{d.get('subproject', '')}</b>\n"
        f"🏷 Статья расхода: <b>{d.get('category', '')}</b>\n"
        f"💰 Сумма: <b>{d.get('amount', '')} ₽</b>\n"
        f"📝 Описание расхода: <b>{d.get('description', '')}</b>\n"
        f"{receipt_status}"
    )


def confirm_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_yes"),
            InlineKeyboardButton("✏️ Изменить", callback_data="confirm_edit"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="confirm_no")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = full_name(update.effective_user)
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\nВыберите действие:",
        reply_markup=main_menu_keyboard()
    )
    return MENU


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "menu_add":
        context.user_data.clear()
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
            format_balance(b),
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
                lines.append(f"{e['status']} {e['date'][:10]} | {e['amount']} ₽ | {e['category']}")
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
        await query.edit_message_text("Выберите действие:", reply_markup=main_menu_keyboard())
        return MENU


async def direction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    code = query.data.split(' — ')[0]
    context.user_data.clear()
    context.user_data['direction_code'] = code
    context.user_data['direction_name'] = 'Синий Трактор' if code == 'СТ' else 'Фиксишоу'

    await query.edit_message_text(
        f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n\n"
        "📌 Выберите тип:",
        reply_markup=make_keyboard(SUBTYPES[code], cols=1),
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
        gs = GoogleSheetsManager()
        gastrol_list = gs.get_subprojects(code, 'Гастроль')
        if gastrol_list:
            items = gastrol_list + ['➕ Другая дата']
            await query.edit_message_text(
                f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n"
                f"✅ Тип: <b>Гастроль</b>\n\n"
                "📅 Выберите гастроль:",
                reply_markup=make_keyboard(items, cols=1),
                parse_mode=ParseMode.HTML
            )
            return GASTROL_CHOOSE
        else:
            await query.edit_message_text(
                f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n"
                f"✅ Тип: <b>Гастроль</b>\n\n"
                "📅 Введите дату в формате <b>ДД.ММ</b> или <b>ДД/ММ</b>:",
                parse_mode=ParseMode.HTML
            )
            return GASTROL_DATE

    elif subtype == 'Франшиза':
        context.user_data['subproject'] = 'Франшиза'
        await query.edit_message_text(
            f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n"
            f"✅ Гастроль/Проект: <b>Франшиза</b>\n\n"
            "🏷 Выберите статью расхода:",
            reply_markup=make_category_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return CATEGORY

    else:
        gs = GoogleSheetsManager()
        projects = gs.get_subprojects(code, 'Проект')
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


async def gastrol_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == '➕ Другая дата':
        await query.edit_message_text(
            "📅 Введите дату в формате <b>ДД.ММ</b> или <b>ДД/ММ</b>:",
            parse_mode=ParseMode.HTML
        )
        return GASTROL_DATE
    else:
        context.user_data['subproject'] = f"Гастроль — {query.data}"
        await query.edit_message_text(
            f"✅ Гастроль/Проект: <b>{context.user_data['subproject']}</b>\n\n"
            "🏷 Выберите статью расхода:",
            reply_markup=make_category_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return CATEGORY


async def gastrol_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    parsed = parse_date(text)

    if not parsed:
        await update.message.reply_text(
            "❌ Неверный формат или месяц. Введите дату <b>ДД.ММ</b> или <b>ДД/ММ</b>, например <b>15.06</b>:",
            parse_mode=ParseMode.HTML
        )
        return GASTROL_DATE

    day, month = parsed
    date_str = f"{day:02d}.{month:02d}"
    context.user_data['gastrol_date'] = date_str
    current_month = datetime.now().month

    if current_month >= 10:
        current_year = datetime.now().year
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(str(current_year), callback_data=f"year_{current_year}"),
            InlineKeyboardButton(str(current_year + 1), callback_data=f"year_{current_year + 1}"),
        ]])
        await update.message.reply_text(
            f"✅ Дата: <b>{date_str}</b>\n\n📅 Выберите год:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        return GASTROL_YEAR
    else:
        context.user_data['gastrol_year'] = datetime.now().year
        await update.message.reply_text(
            f"✅ Дата: <b>{date_str}.{datetime.now().year}</b>\n\n"
            "🏙 Введите город (или <b>-</b> чтобы пропустить):",
            parse_mode=ParseMode.HTML
        )
        return GASTROL_CITY


async def gastrol_year_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    year = int(query.data.split('_')[1])
    context.user_data['gastrol_year'] = year
    date = context.user_data['gastrol_date']
    await query.edit_message_text(
        f"✅ Дата: <b>{date}.{year}</b>\n\n"
        "🏙 Введите город (или <b>-</b> чтобы пропустить):",
        parse_mode=ParseMode.HTML
    )
    return GASTROL_CITY


async def gastrol_city_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    date = context.user_data['gastrol_date']
    year = context.user_data.get('gastrol_year', datetime.now().year)

    if text == '-' or not text:
        context.user_data['subproject'] = f"Гастроль — {date}.{year}"
    else:
        context.user_data['subproject'] = f"Гастроль — {date}.{year}, {text}"

    if context.user_data.get('editing'):
        context.user_data.pop('editing')
        return await show_confirm_msg(update.message, context)

    await update.message.reply_text(
        f"✅ Гастроль/Проект: <b>{context.user_data['subproject']}</b>\n\n"
        "🏷 Выберите статью расхода:",
        reply_markup=make_category_keyboard(),
        parse_mode=ParseMode.HTML
    )
    return CATEGORY


async def subproject_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['subproject'] = query.data

    if context.user_data.get('editing'):
        context.user_data.pop('editing')
        return await show_confirm_edit(query, context)

    await query.edit_message_text(
        f"✅ Гастроль/Проект: <b>{query.data}</b>\n\n"
        "🏷 Выберите статью расхода:",
        reply_markup=make_category_keyboard(),
        parse_mode=ParseMode.HTML
    )
    return CATEGORY


async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cat_name = CAT_CODES.get(query.data, query.data)
    context.user_data['category'] = cat_name

    if context.user_data.get('editing'):
        context.user_data.pop('editing')
        return await show_confirm_edit(query, context)

    await query.edit_message_text(
        f"✅ Статья расхода: <b>{cat_name}</b>\n\n"
        "💰 Введите сумму (например <b>1883,75</b> или <b>1883</b>):",
        parse_mode=ParseMode.HTML
    )
    return AMOUNT


async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount_str = format_amount(update.message.text)
    if not amount_str:
        await update.message.reply_text(
            "❌ Неверный формат. Введите сумму, например <b>1883,75</b> или <b>1883</b>:",
            parse_mode=ParseMode.HTML
        )
        return AMOUNT

    context.user_data['amount'] = amount_str

    if context.user_data.get('editing'):
        context.user_data.pop('editing')
        return await show_confirm_msg(update.message, context)

    hint = "\n\n⚠️ Категория «Другое» — опишите подробно." if context.user_data.get('category') == 'Другое' else ""
    await update.message.reply_text(
        f"✅ Сумма: <b>{amount_str} ₽</b>\n\n"
        f"📝 Напишите полное описание расхода:{hint}",
        parse_mode=ParseMode.HTML
    )
    return DESCRIPTION


async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Описание обязательно. Напишите полное описание расхода:")
        return DESCRIPTION
    context.user_data['description'] = text

    if context.user_data.get('editing'):
        context.user_data.pop('editing')
        return await show_confirm_msg(update.message, context)

    await update.message.reply_text(
        f"✅ Описание расхода: <b>{text}</b>\n\n📸 Отправьте фото чека:",
        parse_mode=ParseMode.HTML
    )
    return RECEIPT


async def receipt_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("❌ Нужно отправить фото чека:")
        return RECEIPT

    # Сразу сообщаем что фото получено
    await update.message.reply_text("⏳ Загружаю чек на Яндекс Диск...")

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    user_name = full_name(update.effective_user)

    file_bytes = await file.download_as_bytearray()
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{photo.file_id[-8:]}.jpg"
    yandex_url = upload_to_yandex_disk(bytes(file_bytes), filename, user_name)

    context.user_data['receipt_url'] = yandex_url or file.file_path
    context.user_data['receipt_file_id'] = photo.file_id
    context.user_data['receipt_on_yandex'] = yandex_url is not None

    return await show_confirm_msg(update.message, context)


async def show_confirm_msg(message, context):
    d = context.user_data
    gs = GoogleSheetsManager()
    is_dup = gs.check_duplicate(
        d.get('user_name', ''), d.get('subproject', ''),
        d.get('category', ''), d.get('amount', '')
    )
    dup_warning = "\n\n⚠️ <b>Похожий расход уже существует!</b>" if is_dup else ""
    await message.reply_text(
        get_confirm_text(d) + dup_warning,
        reply_markup=confirm_keyboard(),
        parse_mode=ParseMode.HTML
    )
    return CONFIRM


async def show_confirm_edit(query, context):
    await query.edit_message_text(
        get_confirm_text(context.user_data),
        reply_markup=confirm_keyboard(),
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
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📌 Гастроль/Проект", callback_data="edit_subproject")],
            [InlineKeyboardButton("🏷 Статья расхода", callback_data="edit_category")],
            [InlineKeyboardButton("💰 Сумма", callback_data="edit_amount")],
            [InlineKeyboardButton("📝 Описание расхода", callback_data="edit_description")],
            [InlineKeyboardButton("📸 Чек", callback_data="edit_receipt")],
        ])
        await query.edit_message_text("✏️ Что хотите изменить?", reply_markup=keyboard)
        return EDIT_CHOICE

    if query.data == 'confirm_yes':
        d = context.user_data
        user_name = full_name(update.effective_user)

        gs = GoogleSheetsManager()
        ok = gs.add_expense(user_name, {
            'subproject': d['subproject'],
            'category': d['category'],
            'amount': d['amount'],
            'description': d['description'],
            'receipt_url': d.get('receipt_url', ''),
        })

        if ok:
            await query.edit_message_text(
                f"✅ <b>Расход сохранён!</b>\n\n"
                f"Гастроль/Проект: {d['subproject']}\n"
                f"Сумма: {d['amount']} ₽\n"
                f"Статус: 🔍 В проверке",
                parse_mode=ParseMode.HTML
            )
            admin_id = int(os.getenv('ADMIN_CHAT_ID', 0))
            if admin_id:
                try:
                    caption = (
                        f"📊 <b>Новый расход</b>\n\n"
                        f"👤 {user_name}\n"
                        f"🏗 {d.get('direction_name')} / {d['subproject']}\n"
                        f"🏷 {d['category']}\n"
                        f"💰 {d['amount']} ₽\n"
                        f"📝 {d['description']}\n"
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
                    logger.error(f"Уведомление ошибка: {e}")
        else:
            await query.edit_message_text("❌ Ошибка при сохранении. Попробуйте ещё раз.")

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Выберите действие:",
            reply_markup=main_menu_keyboard()
        )
        return MENU


async def edit_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['editing'] = True

    if query.data == 'edit_category':
        await query.edit_message_text("🏷 Выберите новую статью расхода:", reply_markup=make_category_keyboard())
        return CATEGORY
    elif query.data == 'edit_amount':
        await query.edit_message_text("💰 Введите новую сумму:")
        return AMOUNT
    elif query.data == 'edit_description':
        await query.edit_message_text("📝 Напишите полное описание расхода:")
        return DESCRIPTION
    elif query.data == 'edit_receipt':
        await query.edit_message_text("📸 Отправьте новое фото чека:")
        return RECEIPT
    elif query.data == 'edit_subproject':
        code = context.user_data.get('direction_code', 'СТ')
        subtype = context.user_data.get('subtype', 'Проект')
        if subtype == 'Гастроль':
            gs = GoogleSheetsManager()
            gastrol_list = gs.get_subprojects(code, 'Гастроль')
            if gastrol_list:
                items = gastrol_list + ['➕ Другая дата']
                await query.edit_message_text(
                    "📅 Выберите гастроль:",
                    reply_markup=make_keyboard(items, cols=1)
                )
                return GASTROL_CHOOSE
            else:
                await query.edit_message_text(
                    "📅 Введите новую дату гастроли (<b>ДД.ММ</b> или <b>ДД/ММ</b>):",
                    parse_mode=ParseMode.HTML
                )
                return GASTROL_DATE
        else:
            gs = GoogleSheetsManager()
            projects = gs.get_subprojects(code, 'Проект')
            await query.edit_message_text(
                "📌 Выберите новый проект:",
                reply_markup=make_keyboard(projects or ['Нет данных'], cols=1)
            )
            return SUBPROJECT


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = full_name(update.effective_user)
    gs = GoogleSheetsManager()
    b = gs.get_balance(user_name)
    await update.message.reply_text(
        format_balance(b),
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
            lines.append(f"{e['status']} {e['date'][:10]} | {e['amount']} ₽ | {e['category']}")
        text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Отменено.", reply_markup=main_menu_keyboard())
    return MENU


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Справка</b>\n\n"
        "/start — главное меню\n"
        "/balance — мой баланс\n"
        "/myexpenses — мои расходы\n"
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
        entry_points=[CommandHandler('start', start)],
        states={
            MENU: [CallbackQueryHandler(menu_handler, pattern='^(menu_|back_menu)')],
            DIRECTION: [CallbackQueryHandler(direction_selected)],
            SUBTYPE: [CallbackQueryHandler(subtype_selected)],
            SUBPROJECT: [CallbackQueryHandler(subproject_selected)],
            GASTROL_CHOOSE: [CallbackQueryHandler(gastrol_choose)],
            GASTROL_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, gastrol_date_received)],
            GASTROL_YEAR: [CallbackQueryHandler(gastrol_year_selected, pattern='^year_')],
            GASTROL_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, gastrol_city_received)],
            CATEGORY: [CallbackQueryHandler(category_selected, pattern='^cat_')],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_received)],
            RECEIPT: [
                MessageHandler(filters.PHOTO, receipt_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_received),
            ],
            CONFIRM: [CallbackQueryHandler(confirm_handler, pattern='^confirm_')],
            EDIT_CHOICE: [CallbackQueryHandler(edit_choice_handler, pattern='^edit_')],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler('balance', balance_command))
    app.add_handler(CommandHandler('myexpenses', myexpenses_command))
    app.add_handler(CommandHandler('help', help_command))

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == '__main__':
    main()
