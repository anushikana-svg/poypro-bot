#!/usr/bin/env python3
"""
Expense Tracker Bot для Telegram
ПойПро — Фиксишоу & Синий Трактор
"""

import os
import logging
from datetime import datetime
from typing import Dict, List
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
from google.oauth2.service_account import Credentials
import json

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

PROJECT, SUBPROJECT, GASTROL_DATE, CATEGORY, AMOUNT, DESCRIPTION, RECEIPT, CONFIRM = range(8)

PROJECTS = {
    'СТ': 'Синий Трактор',
    'ФШ': 'Фиксишоу',
}

SUBPROJECTS = {
    'ФШ': ['Гастроль', 'Египет', 'Турция', 'Дубай', '➕ Добавить новый'],
    'СТ': ['Гастроль', 'Франшиза', 'МиниСТ', '➕ Добавить новый'],
}

CATEGORIES = [
    'Чистка костюмов',
    'Изготовление/покупка костюмов',
    'Изготовление и ремонт реквизита',
    'ЗП налом',
    'Доставка',
    'Такси',
    'Аренда зала',
    'Прочее',
]

class GoogleSheetsManager:
    def __init__(self):
        self.sheet = None
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
            spreadsheet = client.open_by_key(sheets_id)
            try:
                self.sheet = spreadsheet.worksheet('Расходы')
            except:
                self.sheet = spreadsheet.sheet1
            logger.info("Google Sheets подключен успешно")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")

    def add_expense(self, data: Dict) -> bool:
        try:
            if not self.sheet:
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
            ]
            self.sheet.append_row(row)
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении расхода: {e}")
            return False

def make_keyboard(items: List[str], cols: int = 2) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(item, callback_data=item) for item in items]
    rows = [buttons[i:i+cols] for i in range(0, len(buttons), cols)]
    return InlineKeyboardMarkup(rows)

def full_name(user) -> str:
    name = user.first_name or ''
    if user.last_name:
        name += f' {user.last_name}'
    return name.strip() or user.username or 'Неизвестно'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = full_name(update.effective_user)
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        "Я помогу добавить расход в таблицу.\n\n"
        "/add — добавить расход\n"
        "/help — справка"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Справка</b>\n\n"
        "<b>Проекты:</b>\n"
        "🚜 Синий Трактор (СТ)\n"
        "🎭 Фиксишоу (ФШ)\n\n"
        "<b>Команды:</b>\n"
        "/add — добавить расход\n"
        "/cancel — отменить",
        parse_mode=ParseMode.HTML
    )

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    keyboard = make_keyboard([f"{code} — {name}" for code, name in PROJECTS.items()], cols=1)
    name = full_name(update.effective_user)
    await update.message.reply_text(
        f"👋 {name}, добавляем расход!\n\n"
        "🏗 <b>Выберите проект:</b>",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return PROJECT

async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    code = query.data.split(' — ')[0]
    context.user_data['project_code'] = code
    context.user_data['project_name'] = PROJECTS[code]
    keyboard = make_keyboard(SUBPROJECTS[code], cols=1)
    await query.edit_message_text(
        f"✅ Проект: <b>{PROJECTS[code]}</b>\n\n"
        "📌 Выберите подпроект:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return SUBPROJECT

async def subproject_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    sub = query.data
    context.user_data['subproject'] = sub

    if sub == '➕ Добавить новый':
        await query.edit_message_text(
            f"✅ Проект: <b>{context.user_data['project_name']}</b>\n\n"
            "✏️ Введите название нового подпроекта:",
            parse_mode=ParseMode.HTML
        )
        return SUBPROJECT

    if sub == 'Гастроль':
        await query.edit_message_text(
            f"✅ Проект: <b>{context.user_data['project_name']}</b>\n"
            f"✅ Подпроект: <b>Гастроль</b>\n\n"
            "📅 Введите дату и место в формате:\n<b>15.05, город или площадка</b>",
            parse_mode=ParseMode.HTML
        )
        return GASTROL_DATE

    return await _ask_category(query, context)

async def subproject_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if context.user_data.get('subproject') == 'Гастроль':
        context.user_data['subproject'] = f"Гастроль — {text}"
    else:
        context.user_data['subproject'] = text
    return await _ask_category_msg(update.message, context)

async def gastrol_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['subproject'] = f"Гастроль — {update.message.text.strip()}"
    return await _ask_category_msg(update.message, context)

async def _ask_category(query, context):
    keyboard = make_keyboard(CATEGORIES, cols=1)
    await query.edit_message_text(
        f"✅ Проект: <b>{context.user_data['project_name']}</b>\n"
        f"✅ Подпроект: <b>{context.user_data['subproject']}</b>\n\n"
        "🏷 Выберите категорию:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return CATEGORY

async def _ask_category_msg(message, context):
    keyboard = make_keyboard(CATEGORIES, cols=1)
    await message.reply_text(
        f"✅ Проект: <b>{context.user_data['project_name']}</b>\n"
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
        f"✅ Проект: <b>{context.user_data['project_name']}</b>\n"
        f"✅ Подпроект: <b>{context.user_data['subproject']}</b>\n"
        f"✅ Категория: <b>{query.data}</b>\n\n"
        "💰 Введите сумму в формате <b>1883,75</b>:",
        parse_mode=ParseMode.HTML
    )
    return AMOUNT

async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount_text = update.message.text.strip()
        amount = float(amount_text.replace(',', '.'))
        context.user_data['amount'] = amount
        context.user_data['amount_text'] = amount_text
        await update.message.reply_text(
            f"✅ Сумма: <b>{amount_text} ₽</b>\n\n"
            "📝 Введите описание (что именно оплачено):",
            parse_mode=ParseMode.HTML
        )
        return DESCRIPTION
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Введите сумму в формате <b>1883,75</b>:",
            parse_mode=ParseMode.HTML
        )
        return AMOUNT

async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
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

    d = context.user_data
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Сохранить", callback_data="yes"),
        InlineKeyboardButton("❌ Отмена", callback_data="no"),
    ]])
    await update.message.reply_text(
        f"📋 <b>Проверьте данные:</b>\n\n"
        f"🏗 Проект: <b>{d['project_name']}</b>\n"
        f"📌 Подпроект: <b>{d['subproject']}</b>\n"
        f"🏷 Категория: <b>{d['category']}</b>\n"
        f"💰 Сумма: <b>{d.get('amount_text', d['amount'])} ₽</b>\n"
        f"📝 Описание: <b>{d.get('description')}</b>\n"
        f"✅ Чек загружен\n\n"
        "Всё верно?",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == 'no':
        await query.edit_message_text("❌ Отменено. Напишите /add чтобы начать заново.")
        return ConversationHandler.END

    d = context.user_data
    user = update.effective_user
    expense = {
        'user_name': full_name(user),
        'project': d['project_name'],
        'subproject': d['subproject'],
        'category': d['category'],
        'amount': d.get('amount_text', d['amount']),
        'description': d.get('description', ''),
        'receipt_url': d.get('receipt_url', ''),
    }

    gs = GoogleSheetsManager()
    ok = gs.add_expense(expense)

    if ok:
        await query.edit_message_text(
            f"✅ <b>Расход сохранён!</b>\n\n"
            f"Проект: {expense['project']}\n"
            f"Сумма: {expense['amount']} ₽\n\n"
            f"Нажмите /add чтобы добавить ещё один расход.",
            parse_mode=ParseMode.HTML
        )
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
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Отменено. Напишите /add чтобы начать заново.")
    return ConversationHandler.END

def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан!")

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler('add', add_start)],
        states={
            PROJECT: [CallbackQueryHandler(project_selected)],
            SUBPROJECT: [
                CallbackQueryHandler(subproject_selected),
                MessageHandler(filters.TEXT & ~filters.COMMAND, subproject_text),
            ],
            GASTROL_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, gastrol_date)],
            CATEGORY: [CallbackQueryHandler(category_selected)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_received)],
            RECEIPT: [
                MessageHandler(filters.PHOTO, receipt_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_received),
            ],
            CONFIRM: [CallbackQueryHandler(confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(conv)

    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == '__main__':
    main()
