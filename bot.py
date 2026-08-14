#!/usr/bin/env python3
"""
Expense Tracker Bot для Telegram
ПойПро — Фиксишоу & Синий Трактор
"""

import os
import logging
import json
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

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

(MENU, DIRECTION, SUBTYPE, SUBPROJECT, GASTROL_CHOOSE, GASTROL_DATE,
 GASTROL_YEAR, GASTROL_CITY, CATEGORY, AMOUNT, DESCRIPTION,
 RECEIPT, CONFIRM, EDIT_CHOICE, INCOME_SOURCE, INCOME_AMOUNT, INCOME_DESC) = range(17)

DIRECTIONS = ['СТ — Синий Трактор', 'ФШ — Фиксишоу']

INCOME_SOURCES = ['Возврат депозита', 'Продажа в магазине', 'Другой доход налом']

SUBTYPES = {
    'СТ': ['Гастроль', 'Франшиза', 'Проект', 'Склад'],
    'ФШ': ['Гастроль', 'Проект', 'Склад'],
}

CATEGORIES = [
    'Чистка костюмов',
    'Изготовление/покупка костюмов',
    'Изготовление и ремонт реквизита',
    'Доставка',
    'Такси',
    'Бензин',
    'Залог',
    'Аренда зала',
    'Другое',
]

CAT_CODES = {f'cat_{i}': cat for i, cat in enumerate(CATEGORIES)}

SHEET_HEADERS = [
    'Тип', 'Дата', 'Сумма', 'Гастроль/Проект',
    'Статья расхода', 'Описание расхода', 'Расход принят', 'Закрыто',
    'Чек 1', 'Чек 2', 'Чек 3'
]
MAX_PHOTOS = 3

PHOTO_ROW_HEIGHT = 375

GOOGLE_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


# ─── Форматирование чисел ────────────────────────────────────────────────────

def fmt_money(value: float) -> str:
    """1234567.5 → '1 234 567,50 ₽'"""
    parts = f"{value:,.2f}".split(".")
    integer = parts[0].replace(",", " ")
    decimal = parts[1]
    return f"{integer},{decimal} ₽"


def fmt_amount_str(value: float) -> str:
    """Для хранения в таблице: 1234.5 → '1 234,50', целое → '1 234'"""
    if value == int(value):
        return f"{int(value):,}".replace(",", " ")
    parts = f"{value:,.2f}".split(".")
    return f"{parts[0].replace(',', ' ')},{parts[1]}"


# ─── Утилиты ─────────────────────────────────────────────────────────────────

def parse_date(text: str) -> Optional[tuple]:
    text = text.strip().replace('/', '.')
    parts = text.split('.')
    if len(parts) != 2:
        return None
    try:
        day, month = int(parts[0]), int(parts[1])
        if not (1 <= month <= 12) or not (1 <= day <= 31):
            return None
        return day, month
    except:
        return None


def format_amount(text: str) -> Optional[float]:
    """Парсит введённую пользователем сумму, возвращает float или None"""
    try:
        cleaned = text.strip().replace(' ', '').replace(',', '.')
        value = float(cleaned)
        return value if value > 0 else None
    except:
        return None


def full_name(user) -> str:
    name = user.first_name or ''
    if user.last_name:
        name += f' {user.last_name}'
    return name.strip() or user.username or 'Неизвестно'


def get_google_creds() -> Optional[Credentials]:
    creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if not creds_json:
        return None
    return Credentials.from_service_account_info(
        json.loads(creds_json), scopes=GOOGLE_SCOPES
    )


# ─── Google Drive ─────────────────────────────────────────────────────────────

async def upload_receipt(bot, file_id: str, filename: str, user_name: str) -> Optional[str]:
    """
    Загружает фото:
    - на ImgBB (для =IMAGE() в Google Sheets) — возвращает URL
    - на Яндекс Диск (для архива) — параллельно, молча
    """
    import base64
    import requests as req

    file = await bot.get_file(file_id)
    file_bytes = bytes(await file.download_as_bytearray())

    # ── ImgBB ────────────────────────────────────────────────────────────────
    imgbb_url = None
    try:
        b64 = base64.b64encode(file_bytes).decode('utf-8')
        resp = req.post(
            "https://api.imgbb.com/1/upload",
            data={"key": "5bc37cf490fddb21bb4721de6072bec7", "image": b64},
            timeout=30
        )
        data = resp.json()
        if data.get("success"):
            imgbb_url = data["data"]["url"]
            logger.info(f"ImgBB: загружено — {imgbb_url}")
        else:
            logger.error(f"ImgBB ошибка: {data}")
    except Exception as e:
        logger.error(f"ImgBB ошибка: {e}")

    # ── Яндекс Диск (архив) ──────────────────────────────────────────────────
    try:
        token = os.getenv('YANDEX_DISK_TOKEN')
        if token:
            now = datetime.now()
            folder = f"Чеки/{user_name}/{now.year}/{now.month:02d}"
            for path in ["Чеки", f"Чеки/{user_name}",
                         f"Чеки/{user_name}/{now.year}", folder]:
                req.put(
                    "https://cloud-api.yandex.net/v1/disk/resources",
                    headers={"Authorization": f"OAuth {token}"},
                    params={"path": path}, timeout=10
                )
            disk_path = f"{folder}/{filename}"
            upload_resp = req.get(
                "https://cloud-api.yandex.net/v1/disk/resources/upload",
                headers={"Authorization": f"OAuth {token}"},
                params={"path": disk_path, "overwrite": "true"}, timeout=10
            )
            upload_url = upload_resp.json().get("href")
            if upload_url:
                req.put(upload_url, data=file_bytes, timeout=30)
                logger.info(f"Яндекс Диск: архивировано {disk_path}")
    except Exception as e:
        logger.error(f"Яндекс Диск ошибка: {e}")

    return imgbb_url


# ─── Google Sheets ────────────────────────────────────────────────────────────

class GoogleSheetsManager:
    def __init__(self):
        self.spreadsheet = None
        self._connect()

    def _connect(self):
        try:
            sheets_id = os.getenv('GOOGLE_SHEETS_ID')
            if not sheets_id:
                return
            creds = get_google_creds()
            if not creds:
                return
            client = gspread.authorize(creds)
            self.spreadsheet = client.open_by_key(sheets_id)
            logger.info("Google Sheets подключен")
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")

    # ── Главная ──────────────────────────────────────────────────────────────

    DASHBOARD_HEADERS = [
        'Сотрудник', 'Выдано', 'Принято расходов', 'В проверке', 'Взносы', 'Остаток', 'Реально на руках'
    ]

    def get_or_create_balance_sheet(self):
        try:
            sheet = self.spreadsheet.worksheet('Главная')
            # Самолечение: если лист создан до появления колонки "Взносы" — доводим шапку до актуальной
            header = sheet.row_values(1)
            if header[:len(self.DASHBOARD_HEADERS)] != self.DASHBOARD_HEADERS:
                sheet.update('A1:G1', [self.DASHBOARD_HEADERS], value_input_option='USER_ENTERED')
                sheet.format('A1:G1', {
                    'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                    'backgroundColor': {'red': 0.2, 'green': 0.47, 'blue': 0.78},
                    'horizontalAlignment': 'CENTER',
                })
            return sheet
        except WorksheetNotFound:
            sheet = self.spreadsheet.add_worksheet(title='Главная', rows=200, cols=7)
            try:
                all_sheets = self.spreadsheet.worksheets()
                self.spreadsheet.reorder_worksheets(
                    [sheet] + [ws for ws in all_sheets if ws.id != sheet.id]
                )
            except Exception:
                pass

            sheet.update('A1:G1', [self.DASHBOARD_HEADERS], value_input_option='USER_ENTERED')
            sheet.format('A1:G1', {
                'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                'backgroundColor': {'red': 0.2, 'green': 0.47, 'blue': 0.78},
                'horizontalAlignment': 'CENTER',
            })
            self.spreadsheet.batch_update({"requests": [
                {"updateDimensionProperties": {
                    "range": {"sheetId": sheet.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                    "properties": {"pixelSize": 180}, "fields": "pixelSize"
                }},
                {"updateDimensionProperties": {
                    "range": {"sheetId": sheet.id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 7},
                    "properties": {"pixelSize": 150}, "fields": "pixelSize"
                }},
            ]})
            logger.info("Создан лист Главная")
            return sheet

    def update_balance_sheet(self, user_name: str):
        """Строка сотрудника на Главной с SUMPRODUCT-формулами из его листа"""
        try:
            sheet = self.get_or_create_balance_sheet()
            all_names = sheet.col_values(1)

            row = all_names.index(user_name) + 1 if user_name in all_names else max(len(all_names) + 1, 2)
            s = user_name.replace("'", "''")

            issued   = f"=SUMPRODUCT((LOWER('{s}'!A2:A1000)=\"выдано\")*(('{s}'!H2:H1000)<>TRUE)*('{s}'!C2:C1000))"
            accepted = f"=SUMPRODUCT((LOWER('{s}'!A2:A1000)=\"расход\")*('{s}'!G2:G1000=TRUE)*(('{s}'!H2:H1000)<>TRUE)*('{s}'!C2:C1000))"
            pending  = f"=SUMPRODUCT((LOWER('{s}'!A2:A1000)=\"расход\")*('{s}'!G2:G1000<>TRUE)*(('{s}'!H2:H1000)<>TRUE)*('{s}'!C2:C1000))"
            income   = f"=SUMPRODUCT((LOWER('{s}'!A2:A1000)=\"взнос\")*(('{s}'!H2:H1000)<>TRUE)*('{s}'!C2:C1000))"
            balance  = f"=B{row}-C{row}+E{row}"
            real     = f"=B{row}-C{row}-D{row}+E{row}"

            sheet.update(f'A{row}:G{row}', [[user_name, issued, accepted, pending, income, balance, real]],
                         value_input_option='USER_ENTERED')

            bg = {'red': 0.9, 'green': 0.94, 'blue': 1.0} if row % 2 == 0 else {'red': 1, 'green': 1, 'blue': 1}
            sheet.format(f'A{row}:G{row}', {'backgroundColor': bg})
        except Exception as e:
            logger.error(f"Ошибка дашборда: {e}")

    # ── Лист сотрудника ───────────────────────────────────────────────────────

    def get_or_create_employee_sheet(self, user_name: str):
        try:
            try:
                return self.spreadsheet.worksheet(user_name)
            except WorksheetNotFound:
                sheet = self.spreadsheet.add_worksheet(title=user_name, rows=1000, cols=12)
                sheet.update('A1:K1', [SHEET_HEADERS])
                sheet.format('A1:I1', {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                })
                self.spreadsheet.batch_update({"requests": [
                    {   # Флажки G-H
                        "repeatCell": {
                            "range": {"sheetId": sheet.id,
                                      "startRowIndex": 1, "endRowIndex": 1000,
                                      "startColumnIndex": 6, "endColumnIndex": 8},
                            "cell": {"dataValidation": {"condition": {"type": "BOOLEAN"}, "strict": True}},
                            "fields": "dataValidation"
                        }
                    },
                    {   # Высота строк для фото
                        "updateDimensionProperties": {
                            "range": {"sheetId": sheet.id, "dimension": "ROWS",
                                      "startIndex": 1, "endIndex": 1000},
                            "properties": {"pixelSize": PHOTO_ROW_HEIGHT}, "fields": "pixelSize"
                        }
                    },
                    {   # Ширина столбца Чек
                        "updateDimensionProperties": {
                            "range": {"sheetId": sheet.id, "dimension": "COLUMNS",
                                      "startIndex": 8, "endIndex": 11},
                            "properties": {"pixelSize": 500}, "fields": "pixelSize"
                        }
                    },
                ]})
                logger.info(f"Создан лист для {user_name}")
                return sheet
        except Exception as e:
            logger.error(f"Ошибка листа: {e}")
            return None

    def _next_empty_row(self, sheet) -> int:
        return len(sheet.col_values(1)) + 1

    def get_subprojects(self, direction: str, ptype: str) -> List[str]:
        try:
            sheet = self.spreadsheet.worksheet('Справочники')
            rows = sheet.get_all_values()
            return [
                row[2].strip() for row in rows[1:]
                if len(row) >= 3 and row[0].strip() == direction and row[1].strip() == ptype
            ]
        except Exception as e:
            logger.error(f"Справочник ошибка: {e}")
            return []

    def add_expense(self, user_name: str, data: Dict) -> bool:
        try:
            sheet = self.get_or_create_employee_sheet(user_name)
            if not sheet:
                return False

            # До 3 чеков в отдельных столбцах
            receipt_urls = data.get('receipt_urls', [])
            receipt_cells = [f'=IMAGE("{url}";2)' if url else '' for url in receipt_urls[:3]]
            while len(receipt_cells) < 3:
                receipt_cells.append('')

            amount_val = data.get('amount', 0)

            row_data = [
                'Расход',
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                amount_val,
                data.get('subproject', ''),
                data.get('category', ''),
                data.get('description', ''),
                False,
                False,
            ] + receipt_cells  # Чек 1, Чек 2, Чек 3

            next_row = self._next_empty_row(sheet)
            sheet.update(f'A{next_row}:K{next_row}',
                         [row_data], value_input_option='USER_ENTERED')
            self.update_balance_sheet(user_name)
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления: {e}")
            return False

    def add_income(self, user_name: str, data: Dict) -> bool:
        """Взнос: возврат депозита, выручка с продаж, другой доход налом — увеличивает остаток"""
        try:
            sheet = self.get_or_create_employee_sheet(user_name)
            if not sheet:
                return False

            row_data = [
                'Взнос',
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                data.get('amount', 0),
                '',
                data.get('source', ''),
                data.get('description', ''),
                False,
                False,
                '', '', '',
            ]

            next_row = self._next_empty_row(sheet)
            sheet.update(f'A{next_row}:K{next_row}',
                         [row_data], value_input_option='USER_ENTERED')
            self.update_balance_sheet(user_name)
            return True
        except Exception as e:
            logger.error(f"Ошибка взноса: {e}")
            return False

    def check_duplicate(self, user_name: str, subproject: str, category: str, amount: float) -> bool:
        try:
            sheet = self.get_or_create_employee_sheet(user_name)
            if not sheet:
                return False
            for row in sheet.get_all_values()[1:]:
                if len(row) >= 6 and row[3] == subproject and row[4] == category:
                    try:
                        if float(str(row[2]).replace(' ', '').replace(',', '.')) == amount:
                            return True
                    except:
                        pass
            return False
        except:
            return False

    def get_balance(self, user_name: str) -> Dict:
        try:
            sheet = self.get_or_create_employee_sheet(user_name)
            if not sheet:
                return self._empty_balance()

            issued = accepted = pending = income = 0.0

            for row in sheet.get_all_values()[1:]:
                if len(row) < 8 or not row[0]:
                    continue
                try:
                    val = row[2]
                    if isinstance(val, (int, float)):
                        amount = float(val)
                    else:
                        amount = float(str(val).replace(' ', '').replace(',', '.'))
                except:
                    continue

                if str(row[7]).upper() in ('TRUE', 'ИСТИНА', '1'):
                    continue  # закрыто — не считаем

                rtype = row[0].strip().lower()
                accepted_flag = str(row[6]).upper() in ('TRUE', 'ИСТИНА', '1')

                if rtype == 'выдано':
                    issued += amount
                elif rtype == 'расход':
                    if accepted_flag:
                        accepted += amount
                    else:
                        pending += amount
                elif rtype == 'взнос':
                    income += amount

            return {
                'issued': issued,
                'accepted': accepted,
                'pending': pending,
                'income': income,
                'balance': issued - accepted + income,
                'real_balance': issued - accepted - pending + income,
            }
        except Exception as e:
            logger.error(f"Баланс ошибка: {e}")
            return self._empty_balance()

    def _empty_balance(self):
        return {'issued': 0, 'accepted': 0, 'pending': 0, 'income': 0, 'balance': 0, 'real_balance': 0}

    def get_my_expenses(self, user_name: str) -> List[Dict]:
        try:
            sheet = self.get_or_create_employee_sheet(user_name)
            if not sheet:
                return []
            result = []
            for row in sheet.get_all_values()[1:]:
                if len(row) >= 5 and row[0].strip().lower() in ('расход', 'взнос'):
                    rtype = row[0].strip().lower()
                    if rtype == 'расход':
                        accepted = str(row[6]).upper() in ('TRUE', 'ИСТИНА', '1') if len(row) > 6 else False
                        status = '✅ Принят' if accepted else '🔍 В проверке'
                    else:
                        status = '💵 Взнос'
                    result.append({
                        'date': row[1],
                        'amount': row[2],
                        'category': row[4],
                        'status': status,
                    })
            return result[-20:]
        except:
            return []


# ─── UI helpers ───────────────────────────────────────────────────────────────

def make_keyboard(items: List[str], cols: int = 2) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(item, callback_data=item) for item in items]
    return InlineKeyboardMarkup([buttons[i:i + cols] for i in range(0, len(buttons), cols)])


def make_category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(cat, callback_data=f'cat_{i}')]
        for i, cat in enumerate(CATEGORIES)
    ])


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить расход", callback_data="menu_add")],
        [InlineKeyboardButton("💵 Взнос", callback_data="menu_income")],
        [InlineKeyboardButton("💰 Остаток", callback_data="menu_balance")],
        [InlineKeyboardButton("📋 Мои расходы", callback_data="menu_myexpenses")],
    ])


def format_balance(b: Dict) -> str:
    return (
        f"💰 <b>Ваш баланс</b>\n\n"
        f"Выдано: <b>{fmt_money(b['issued'])}</b>\n\n"
        f"✅ Принято расходов: <b>{fmt_money(b['accepted'])}</b>\n"
        f"🔍 В проверке: <b>{fmt_money(b['pending'])}</b>\n"
        f"💵 Взносы: <b>{fmt_money(b.get('income', 0))}</b>\n"
        f"─────────────────\n"
        f"Остаток: <b>{fmt_money(b['balance'])}</b>\n"
        f"Реально на руках: <b>{fmt_money(b['real_balance'])}</b>"
    )


def get_confirm_text(d: Dict) -> str:
    amount_val = d.get('amount', 0)
    amount_display = fmt_money(amount_val) if isinstance(amount_val, (int, float)) else amount_val
    urls = d.get('receipt_urls', [])
    count = len([u for u in urls if u])
    receipt_status = f"✅ Чеков загружено: {count}" if count else "📸 Чек не загружен"
    return (
        f"📋 <b>Проверьте данные:</b>\n\n"
        f"🏗 Направление: <b>{d.get('direction_name', '')}</b>\n"
        f"📌 Гастроль/Проект: <b>{d.get('subproject', '')}</b>\n"
        f"🏷 Статья расхода: <b>{d.get('category', '')}</b>\n"
        f"💰 Сумма: <b>{amount_display}</b>\n"
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


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 Привет, {full_name(update.effective_user)}!\n\nВыберите действие:",
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

    elif query.data == "menu_income":
        context.user_data.clear()
        await query.edit_message_text(
            "💵 <b>Взнос</b>\n\nВыберите источник:",
            reply_markup=make_keyboard(INCOME_SOURCES, cols=1),
            parse_mode=ParseMode.HTML
        )
        return INCOME_SOURCE

    elif query.data == "menu_balance":
        gs = GoogleSheetsManager()
        b = gs.get_balance(full_name(update.effective_user))
        await query.edit_message_text(
            format_balance(b),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Меню", callback_data="back_menu")]]),
            parse_mode=ParseMode.HTML
        )
        return MENU

    elif query.data == "menu_myexpenses":
        gs = GoogleSheetsManager()
        expenses = gs.get_my_expenses(full_name(update.effective_user))
        if not expenses:
            text = "📋 <b>Мои расходы</b>\n\nРасходов пока нет."
        else:
            lines = ["📋 <b>Мои расходы (последние 20)</b>\n"]
            for e in expenses:
                lines.append(f"{e['status']} {e['date'][:10]} | {e['amount']} ₽ | {e['category']}")
            text = "\n".join(lines)
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Меню", callback_data="back_menu")]]),
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
        f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n\n📌 Выберите тип:",
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
            await query.edit_message_text(
                f"✅ Направление: <b>{context.user_data['direction_name']}</b>\n"
                f"✅ Тип: <b>Гастроль</b>\n\n📅 Выберите гастроль:",
                reply_markup=make_keyboard(gastrol_list + ['➕ Другая дата'], cols=1),
                parse_mode=ParseMode.HTML
            )
            return GASTROL_CHOOSE
        else:
            await query.edit_message_text(
                f"✅ Тип: <b>Гастроль</b>\n\n📅 Введите дату <b>ДД.ММ</b>:",
                parse_mode=ParseMode.HTML
            )
            return GASTROL_DATE

    elif subtype == 'Франшиза':
        context.user_data['subproject'] = 'Франшиза'
        await query.edit_message_text(
            f"✅ Гастроль/Проект: <b>Франшиза</b>\n\n🏷 Выберите статью расхода:",
            reply_markup=make_category_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return CATEGORY

    elif subtype == 'Склад':
        context.user_data['subproject'] = 'Склад'
        await query.edit_message_text(
            f"✅ Гастроль/Проект: <b>Склад</b>\n\n🏷 Выберите статью расхода:",
            reply_markup=make_category_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return CATEGORY

    else:
        gs = GoogleSheetsManager()
        projects = gs.get_subprojects(code, 'Проект') or ['Нет данных в справочнике']
        await query.edit_message_text(
            f"✅ Тип: <b>{subtype}</b>\n\n📌 Выберите проект:",
            reply_markup=make_keyboard(projects, cols=1),
            parse_mode=ParseMode.HTML
        )
        return SUBPROJECT


async def gastrol_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == '➕ Другая дата':
        await query.edit_message_text("📅 Введите дату <b>ДД.ММ</b> или <b>ДД/ММ</b>:", parse_mode=ParseMode.HTML)
        return GASTROL_DATE
    else:
        context.user_data['subproject'] = f"Гастроль — {query.data}"
        await query.edit_message_text(
            f"✅ Гастроль/Проект: <b>{context.user_data['subproject']}</b>\n\n🏷 Выберите статью расхода:",
            reply_markup=make_category_keyboard(), parse_mode=ParseMode.HTML
        )
        return CATEGORY


async def gastrol_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    parsed = parse_date(update.message.text)
    if not parsed:
        await update.message.reply_text("❌ Неверный формат. Введите <b>ДД.ММ</b>, например <b>15.06</b>:", parse_mode=ParseMode.HTML)
        return GASTROL_DATE
    day, month = parsed
    date_str = f"{day:02d}.{month:02d}"
    context.user_data['gastrol_date'] = date_str
    if datetime.now().month >= 10:
        cur = datetime.now().year
        await update.message.reply_text(
            f"✅ Дата: <b>{date_str}</b>\n\n📅 Выберите год:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(str(cur), callback_data=f"year_{cur}"),
                InlineKeyboardButton(str(cur + 1), callback_data=f"year_{cur + 1}"),
            ]]),
            parse_mode=ParseMode.HTML
        )
        return GASTROL_YEAR
    else:
        context.user_data['gastrol_year'] = datetime.now().year
        await update.message.reply_text(
            f"✅ Дата: <b>{date_str}.{datetime.now().year}</b>\n\n🏙 Введите город (или <b>-</b>):",
            parse_mode=ParseMode.HTML
        )
        return GASTROL_CITY


async def gastrol_year_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    year = int(query.data.split('_')[1])
    context.user_data['gastrol_year'] = year
    await query.edit_message_text(
        f"✅ Дата: <b>{context.user_data['gastrol_date']}.{year}</b>\n\n🏙 Введите город (или <b>-</b>):",
        parse_mode=ParseMode.HTML
    )
    return GASTROL_CITY


async def gastrol_city_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    date = context.user_data['gastrol_date']
    year = context.user_data.get('gastrol_year', datetime.now().year)
    context.user_data['subproject'] = (
        f"Гастроль — {date}.{year}" if text == '-' or not text
        else f"Гастроль — {date}.{year}, {text}"
    )
    if context.user_data.get('editing'):
        context.user_data.pop('editing')
        return await show_confirm_msg(update.message, context)
    await update.message.reply_text(
        f"✅ Гастроль/Проект: <b>{context.user_data['subproject']}</b>\n\n🏷 Выберите статью расхода:",
        reply_markup=make_category_keyboard(), parse_mode=ParseMode.HTML
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
        f"✅ Гастроль/Проект: <b>{query.data}</b>\n\n🏷 Выберите статью расхода:",
        reply_markup=make_category_keyboard(), parse_mode=ParseMode.HTML
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
        f"✅ Статья расхода: <b>{cat_name}</b>\n\n💰 Введите сумму (например <b>1 883,75</b> или <b>1883</b>):",
        parse_mode=ParseMode.HTML
    )
    return AMOUNT


async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount_val = format_amount(update.message.text)
    if not amount_val:
        await update.message.reply_text(
            "❌ Неверный формат. Введите сумму, например <b>1 883,75</b> или <b>1883</b>:",
            parse_mode=ParseMode.HTML
        )
        return AMOUNT
    context.user_data['amount'] = amount_val  # храним как float
    if context.user_data.get('editing'):
        context.user_data.pop('editing')
        return await show_confirm_msg(update.message, context)
    hint = "\n\n⚠️ Категория «Другое» — опишите подробно." if context.user_data.get('category') == 'Другое' else ""
    await update.message.reply_text(
        f"✅ Сумма: <b>{fmt_money(amount_val)}</b>\n\n📝 Напишите полное описание расхода:{hint}",
        parse_mode=ParseMode.HTML
    )
    return DESCRIPTION


async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Описание обязательно:")
        return DESCRIPTION
    context.user_data['description'] = text
    if context.user_data.get('editing'):
        context.user_data.pop('editing')
        return await show_confirm_msg(update.message, context)
    context.user_data['receipt_urls'] = []  # инициализируем список чеков
    context.user_data.pop('receipt_file_id', None)
    await update.message.reply_text(
        f"✅ Описание: <b>{text}</b>\n\n📸 Отправьте фото чека (до 3 штук, по одному):",
        parse_mode=ParseMode.HTML
    )
    return RECEIPT


async def income_source_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['income_source'] = query.data
    await query.edit_message_text(
        f"✅ Источник: <b>{query.data}</b>\n\n💰 Введите сумму (например <b>3000</b>):",
        parse_mode=ParseMode.HTML
    )
    return INCOME_AMOUNT


async def income_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount_val = format_amount(update.message.text)
    if not amount_val:
        await update.message.reply_text(
            "❌ Неверный формат. Введите сумму, например <b>3000</b>:",
            parse_mode=ParseMode.HTML
        )
        return INCOME_AMOUNT
    context.user_data['income_amount'] = amount_val
    await update.message.reply_text(
        "📝 Короткий комментарий (например «Баллон газа» или «Продажа футболок на площадке»):"
    )
    return INCOME_DESC


async def income_desc_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Напишите комментарий:")
        return INCOME_DESC

    user_name = full_name(update.effective_user)
    source = context.user_data.get('income_source', '')
    amount_val = context.user_data.get('income_amount', 0)

    gs = GoogleSheetsManager()
    ok = gs.add_income(user_name, {
        'source': source,
        'amount': amount_val,
        'description': text,
    })

    if ok:
        await update.message.reply_text(
            f"✅ <b>Взнос сохранён!</b>\n\n"
            f"Источник: {source}\n"
            f"Сумма: {fmt_money(amount_val)}\n"
            f"📝 {text}",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
        admin_id = int(os.getenv('ADMIN_CHAT_ID', 0))
        if admin_id:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"💵 <b>Новый взнос</b>\n\n"
                        f"👤 {user_name}\n"
                        f"📌 {source}\n"
                        f"💰 {fmt_money(amount_val)}\n"
                        f"📝 {text}\n"
                        f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа: {e}")
    else:
        await update.message.reply_text(
            "❌ Ошибка сохранения. Попробуйте ещё раз.",
            reply_markup=main_menu_keyboard()
        )
    return MENU


async def receipt_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Кнопка Готово
    if update.message.text and update.message.text.strip() in ('Готово', 'готово', '✅ Готово'):
        if not context.user_data.get('receipt_urls'):
            await update.message.reply_text("❌ Нужно отправить хотя бы одно фото чека:")
            return RECEIPT
        return await show_confirm_msg(update.message, context)

    if not update.message.photo:
        await update.message.reply_text("❌ Нужно отправить фото чека:")
        return RECEIPT

    receipt_urls = context.user_data.get('receipt_urls', [])
    if len(receipt_urls) >= 3:
        await update.message.reply_text("⚠️ Максимум 3 фото. Нажмите ✅ Готово.")
        return RECEIPT

    await update.message.reply_text("⏳ Загружаю чек...")
    photo = update.message.photo[-1]
    user_name = full_name(update.effective_user)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{photo.file_id[-8:]}.jpg"

    img_url = await upload_receipt(context.bot, photo.file_id, filename, user_name)

    if img_url:
        receipt_urls.append(img_url)
        context.user_data['receipt_urls'] = receipt_urls
        # Сохраняем первый file_id для уведомления админу
        if 'receipt_file_id' not in context.user_data:
            context.user_data['receipt_file_id'] = photo.file_id

    count = len(receipt_urls)
    if count < 3:
        await update.message.reply_text(
            f"✅ Чек {count} загружен.\n\nОтправьте ещё фото или нажмите ✅ Готово.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Готово", callback_data="receipts_done")
            ]])
        )
        return RECEIPT
    else:
        await update.message.reply_text("✅ Все 3 чека загружены.")
        return await show_confirm_msg(update.message, context)


async def receipts_done_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if not context.user_data.get('receipt_urls'):
        await query.edit_message_text("❌ Нужно отправить хотя бы одно фото чека:")
        return RECEIPT
    return await show_confirm_msg(query.message, context)


async def show_confirm_msg(message, context):
    d = context.user_data
    gs = GoogleSheetsManager()
    is_dup = gs.check_duplicate(
        d.get('user_name', ''), d.get('subproject', ''),
        d.get('category', ''), d.get('amount', 0)
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
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Выберите действие:", reply_markup=main_menu_keyboard())
        return MENU

    if query.data == 'confirm_edit':
        await query.edit_message_text("✏️ Что хотите изменить?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📌 Гастроль/Проект", callback_data="edit_subproject")],
            [InlineKeyboardButton("🏷 Статья расхода", callback_data="edit_category")],
            [InlineKeyboardButton("💰 Сумма", callback_data="edit_amount")],
            [InlineKeyboardButton("📝 Описание расхода", callback_data="edit_description")],
            [InlineKeyboardButton("📸 Чек", callback_data="edit_receipt")],
        ]))
        return EDIT_CHOICE

    if query.data == 'confirm_yes':
        d = context.user_data
        user_name = full_name(update.effective_user)
        gs = GoogleSheetsManager()
        ok = gs.add_expense(user_name, {
            'subproject': d['subproject'],
            'category': d['category'],
            'amount': d.get('amount', 0),
            'description': d['description'],
            'receipt_urls': d.get('receipt_urls', []),
        })

        if ok:
            await query.edit_message_text(
                f"✅ <b>Расход сохранён!</b>\n\n"
                f"Гастроль/Проект: {d['subproject']}\n"
                f"Сумма: {fmt_money(d.get('amount', 0))}\n"
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
                        f"💰 {fmt_money(d.get('amount', 0))}\n"
                        f"📝 {d['description']}\n"
                        f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                    )
                    if d.get('receipt_file_id'):
                        await context.bot.send_photo(chat_id=admin_id, photo=d['receipt_file_id'], caption=caption, parse_mode=ParseMode.HTML)
                    else:
                        await context.bot.send_message(chat_id=admin_id, text=caption, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.error(f"Уведомление ошибка: {e}")
        else:
            await query.edit_message_text("❌ Ошибка при сохранении. Попробуйте ещё раз.")

        await context.bot.send_message(chat_id=update.effective_chat.id, text="Выберите действие:", reply_markup=main_menu_keyboard())
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
        gs = GoogleSheetsManager()
        if subtype == 'Гастроль':
            gastrol_list = gs.get_subprojects(code, 'Гастроль')
            if gastrol_list:
                await query.edit_message_text("📅 Выберите гастроль:", reply_markup=make_keyboard(gastrol_list + ['➕ Другая дата'], cols=1))
                return GASTROL_CHOOSE
            else:
                await query.edit_message_text("📅 Введите новую дату (<b>ДД.ММ</b>):", parse_mode=ParseMode.HTML)
                return GASTROL_DATE
        else:
            projects = gs.get_subprojects(code, 'Проект')
            await query.edit_message_text("📌 Выберите новый проект:", reply_markup=make_keyboard(projects or ['Нет данных'], cols=1))
            return SUBPROJECT


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gs = GoogleSheetsManager()
    b = gs.get_balance(full_name(update.effective_user))
    await update.message.reply_text(format_balance(b), parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())


async def myexpenses_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gs = GoogleSheetsManager()
    expenses = gs.get_my_expenses(full_name(update.effective_user))
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
        "📖 <b>Справка</b>\n\n/start — главное меню\n/balance — мой баланс\n"
        "/myexpenses — мои расходы\n/cancel — отменить\n\n"
        "<b>Направления:</b>\n🚜 СТ — Синий Трактор\n🎭 ФШ — Фиксишоу",
        parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
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
                CallbackQueryHandler(receipts_done_handler, pattern='^receipts_done$'),
                MessageHandler(filters.PHOTO, receipt_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_received),
            ],
            CONFIRM: [CallbackQueryHandler(confirm_handler, pattern='^confirm_')],
            EDIT_CHOICE: [CallbackQueryHandler(edit_choice_handler, pattern='^edit_')],
            INCOME_SOURCE: [CallbackQueryHandler(income_source_selected)],
            INCOME_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, income_amount_received)],
            INCOME_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, income_desc_received)],
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
