# -*- coding: utf-8 -*-
"""
GreenCRM Telegram Bot
- Позволяет создавать сделки через интерактивный диалог с категориями услуг.
- Автоматически отправляет ежедневные отчеты.
- Корректно удаляет за собой сообщения, оставляя только итоговый результат.
"""

import asyncio
import html
import logging
import os
import sys
from datetime import date, timedelta, time
import pytz

from dotenv import load_dotenv
load_dotenv()

import requests
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, joinedload, contains_eager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from main import Deal, Stage, Contact, Task, DailyPhrase, Service, Expense, ExpenseCategory, DATABASE_URL
except ImportError as e:
    print(f"Critical Error: Cannot import models from main.py: {e}", file=sys.stderr)
    sys.exit(1)

# ════════════════════════════════════════
#  ⚙️  НАСТРОЙКИ
# ════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_BASE_URL = "http://127.0.0.1:8000/api"
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY")

if not INTERNAL_API_KEY:
    logger.critical("Переменная окружения INTERNAL_API_KEY не установлена!")
    sys.exit(1)
API_HEADERS = {"X-Internal-API-Key": INTERNAL_API_KEY}

# Состояния для диалога создания сделки
(GET_TITLE, GET_CLIENT, GET_DATE, GET_TIME, GET_ADDRESS, CHOOSE_CATEGORY, CHOOSE_SERVICE, GET_QUANTITY, ADD_MORE) = range(9)

# Состояния для диалога расхода
(EXP_CHOOSE_CATEGORY, EXP_GET_NAME, EXP_GET_AMOUNT) = range(9, 12)

SERVICE_CATEGORIES = {
    "🌿 Покос травы": list(range(1, 7)),
    "🧹 Уборка и вывоз": list(range(7, 11)),
    "🌱 Газон и почва": list(range(11, 18)),
    "🧪 Обработки": list(range(18, 21)),
    "🌳 Деревья и кустарники": list(range(21, 25)),
    "🌸 Посадка и уход за клумбами": list(range(25, 34)),
    "🍀 Удобрение и питание газона": list(range(34, 38)),
}

# ════════════════════════════════════════
#  🗑  УПРАВЛЕНИЕ СООБЩЕНИЯМИ (ИСПРАВЛЕНО)
# ════════════════════════════════════════

def add_message_to_cleanup(context: ContextTypes.DEFAULT_TYPE, message_id: int):
    if 'messages_to_delete' not in context.user_data:
        context.user_data['messages_to_delete'] = []
    context.user_data['messages_to_delete'].append(message_id)

async def cleanup_temp_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Удаляет все ВРЕМЕННЫЕ сообщения, собранные в ходе диалога."""
    message_ids = context.user_data.get('messages_to_delete', [])
    logger.info(f"Очистка {len(message_ids)} временных сообщений...")
    for message_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except BadRequest as e:
            if "Message to delete not found" not in e.message:
                logger.warning(f"Не удалось удалить сообщение {message_id}: {e}")
    context.user_data['messages_to_delete'] = []

# ════════════════════════════════════════
#  💬  ДИАЛОГ СОЗДАНИЯ СДЕЛКИ (ИСПРАВЛЕН)
# ════════════════════════════════════════

async def new_deal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data['deal_data'] = {'services': []}
    add_message_to_cleanup(context, update.message.message_id)
    
    sent_message = await update.message.reply_text(
        "Начинаем создание новой сделки...\n\n"
        "<b>Шаг 1/6:</b> Введите название сделки.",
        parse_mode='HTML'
    )
    # Это сообщение станет основным, его не удаляем, а запоминаем
    context.user_data['main_dialog_message_id'] = sent_message.message_id
    return GET_TITLE

async def get_deal_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    deal_title = update.message.text.strip()
    add_message_to_cleanup(context, update.message.message_id) # Удаляем сообщение пользователя

    if not deal_title:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['main_dialog_message_id'],
            text="Название сделки не может быть пустым. Попробуйте снова."
        )
        return GET_TITLE

    context.user_data['deal_data']['title'] = deal_title
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=context.user_data['main_dialog_message_id'],
        text=f"Название сделки: <b>{html.escape(deal_title)}</b>.\n\n"
             "<b>Шаг 2/6:</b> Теперь введите имя клиента.",
        parse_mode='HTML'
    )
    return GET_CLIENT

async def get_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    client_name = update.message.text.strip()
    add_message_to_cleanup(context, update.message.message_id)

    if not client_name:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['main_dialog_message_id'],
            text="Имя клиента не может быть пустым. Попробуйте снова."
        )
        return GET_CLIENT

    context.user_data['deal_data']['client_name'] = client_name

    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=context.user_data['main_dialog_message_id'],
        text=f"Клиент: <b>{html.escape(client_name)}</b>.\n\n"
             "<b>Шаг 3/8:</b> Введите дату выезда в формате <b>ДД.ММ.ГГГГ</b>\n"
             "или напишите <b>-</b> чтобы пропустить.",
        parse_mode='HTML'
    )
    return GET_DATE

async def get_deal_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    add_message_to_cleanup(context, update.message.message_id)
    if text != '-':
        from datetime import datetime as _dt
        try:
            context.user_data['deal_data']['work_date'] = _dt.strptime(text, "%d.%m.%Y").strftime("%Y-%m-%d")
        except ValueError:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['main_dialog_message_id'],
                text="Неверный формат. Введите дату как <b>ДД.ММ.ГГГГ</b> (например, 25.07.2025)\n"
                     "или <b>-</b> чтобы пропустить.",
                parse_mode='HTML'
            )
            return GET_DATE
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=context.user_data['main_dialog_message_id'],
        text="<b>Шаг 4/8:</b> Введите время выезда в формате <b>ЧЧ:ММ</b> (например, 09:00)\n"
             "или напишите <b>-</b> чтобы пропустить.",
        parse_mode='HTML'
    )
    return GET_TIME


async def get_deal_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    add_message_to_cleanup(context, update.message.message_id)
    if text != '-':
        import re
        if not re.match(r'^\d{1,2}:\d{2}$', text):
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['main_dialog_message_id'],
                text="Неверный формат. Введите время как <b>ЧЧ:ММ</b> (например, 09:00)\n"
                     "или <b>-</b> чтобы пропустить.",
                parse_mode='HTML'
            )
            return GET_TIME
        context.user_data['deal_data']['work_time'] = text
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=context.user_data['main_dialog_message_id'],
        text="<b>Шаг 5/8:</b> Введите адрес объекта\n"
             "или напишите <b>-</b> чтобы пропустить.",
        parse_mode='HTML'
    )
    return GET_ADDRESS


async def get_deal_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    add_message_to_cleanup(context, update.message.message_id)
    if text != '-':
        context.user_data['deal_data']['address'] = text
    return await show_category_keyboard(update, context)


async def fetch_services_if_needed(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if 'services_list' not in context.user_data:
        try:
            response = requests.get(f"{API_BASE_URL}/services", timeout=5, headers=API_HEADERS)
            response.raise_for_status()
            services = response.json()
            context.user_data['services_list'] = {s['id']: s for s in services}
            return True
        except requests.RequestException as e:
            logger.error(f"Ошибка при запросе услуг из API: {e}")
            return False
    return True

async def show_category_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await fetch_services_if_needed(context):
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=context.user_data['main_dialog_message_id'],
            text="Не удалось загрузить список услуг. Попробуйте отменить (/cancel) и начать заново."
        ) 
        return CHOOSE_CATEGORY

    keyboard = [[InlineKeyboardButton(name, callback_data=f"cat_{name}")] for name in SERVICE_CATEGORIES.keys()]
    # ДОБАВЛЕНА КНОПКА ОТМЕНЫ
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_deal")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = f"<b>Шаг 6/8:</b> Выберите категорию услуг:"

    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=context.user_data['main_dialog_message_id'],
        text=message_text, 
        reply_markup=reply_markup, 
        parse_mode='HTML'
    )
    return CHOOSE_CATEGORY

async def choose_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category_name = query.data.split("cat_", 1)[1]
    context.user_data['current_category'] = category_name
    return await show_services_keyboard(update, context)

async def show_services_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    category_name = context.user_data['current_category']
    all_services = context.user_data.get('services_list', {})
    service_ids = SERVICE_CATEGORIES.get(category_name, [])
    services_to_show = [s for s_id, s in all_services.items() if s_id in service_ids]

    if not services_to_show:
         await query.answer("В этой категории пока нет услуг.", show_alert=True)
         return CHOOSE_CATEGORY

    keyboard = [[InlineKeyboardButton(f"{s['name']} ({s['price']} ₽)", callback_data=f"service_{s['id']}")] for s in services_to_show]
    keyboard.append([InlineKeyboardButton("🔙 Назад к категориям", callback_data="back_to_cat")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = f"<b>Категория: {html.escape(category_name)}</b>\n\n<b>Шаг 7/8:</b> Выберите услугу:"
    
    await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
    return CHOOSE_SERVICE

async def back_to_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await show_category_keyboard(update, context)

async def choose_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    service_id = int(query.data.split('_')[1])
    context.user_data['current_service_id'] = service_id
    service_name = context.user_data.get('services_list', {}).get(service_id, {}).get('name', 'Unknown')
    context.user_data['current_service_name'] = service_name
    
    await query.edit_message_text(
        text=f"Выбрана услуга: <b>{html.escape(service_name)}</b>.\n\n<b>Шаг 7/8:</b> Введите количество (например: 1, 5, 1.5).",
        parse_mode='HTML',
        reply_markup=None
    )
    return GET_QUANTITY

async def get_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    add_message_to_cleanup(context, update.message.message_id)
    main_msg_id = context.user_data['main_dialog_message_id']
    
    try:
        quantity = float(update.message.text.strip().replace(',', '.'))
        if quantity <= 0: raise ValueError
    except ValueError:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=main_msg_id,
            text="Ошибка. Введите положительное число (например, 1 или 2.5)."
        )
        return GET_QUANTITY

    deal_data = context.user_data['deal_data']
    service_id = context.user_data['current_service_id']
    price = context.user_data.get('services_list', {}).get(service_id, {}).get('price', 0)

    deal_data['services'].append({
        'service_id': service_id, 'quantity': quantity,
        'name': context.user_data['current_service_name'], 'price': price
    })

    keyboard = [
        [InlineKeyboardButton("➕ Добавить еще услугу", callback_data='add_more')],
        [InlineKeyboardButton("✅ Завершить и создать сделку", callback_data='finish')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=main_msg_id,
        text=f"Услуга <b>'{html.escape(context.user_data['current_service_name'])}'</b> x{quantity} добавлена.\n\n<b>Шаг 8/8:</b> Добавить еще или завершить?",
        reply_markup=reply_markup, parse_mode='HTML'
    )
    return ADD_MORE

async def add_more_or_finish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == 'add_more':
        return await show_category_keyboard(update, context)
    elif query.data == 'finish':
        return await create_deal_in_api(update, context)

async def wrong_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_message_to_cleanup(context, update.message.message_id)
    temp_msg = await update.message.reply_text("Пожалуйста, используйте кнопки для выбора, а не вводите текст.")
    await asyncio.sleep(3)
    await temp_msg.delete()

async def create_deal_in_api(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=context.user_data['main_dialog_message_id'],
        text="⏳ Создаю сделку в CRM...",
        reply_markup=None
    )
    await cleanup_temp_messages(context, update.effective_chat.id)

    deal_data = context.user_data.get('deal_data')
    if not deal_data or not deal_data.get('services'):
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=context.user_data['main_dialog_message_id'], text="Нет данных для создания сделки. /newdeal для старта.")
        return ConversationHandler.END

    try:
        response = requests.get(f"{API_BASE_URL}/stages", timeout=5, headers=API_HEADERS)
        response.raise_for_status()
        stage_id = response.json()[0]['id']
    except (requests.RequestException, IndexError, KeyError) as e:
        logger.error(f"Не удалось получить ID начального этапа: {e}")
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=context.user_data['main_dialog_message_id'], text="Ошибка: не удалось определить начальный этап для сделки.")
        return ConversationHandler.END

    payload = {
        "title": deal_data['title'], "new_contact_name": deal_data['client_name'],
        "stage_id": stage_id, "manager": update.effective_user.full_name,
        "services": [{'service_id': s['service_id'], 'quantity': s['quantity']} for s in deal_data['services']],
        "work_date": deal_data.get('work_date'),
        "work_time": deal_data.get('work_time'),
        "address": deal_data.get('address'),
    }

    try:
        response = requests.post(f"{API_BASE_URL}/deals", json=payload, timeout=10, headers=API_HEADERS)
        response.raise_for_status()
        
        total_cost = sum(s.get('price', 0) * s.get('quantity', 0) for s in deal_data['services'])
        total_cost_str = f"{int(total_cost):,} ₽".replace(",", " ")
        services_str = "\n".join([f"- {html.escape(s['name'])} (x{s['quantity']})" for s in deal_data['services']])

        extra_lines = ""
        if deal_data.get('work_date'):
            from datetime import datetime as _dt
            dt_disp = _dt.strptime(deal_data['work_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
            time_str = deal_data.get('work_time', '')
            extra_lines += f"\n<b>Дата выезда:</b> {dt_disp}" + (f" в {time_str}" if time_str else "")
        if deal_data.get('address'):
            extra_lines += f"\n<b>Адрес:</b> {html.escape(deal_data['address'])}"

        final_text = (
            f"✅ <b>Сделка успешно создана!</b>\n\n"
            f"<b>Клиент:</b> {html.escape(deal_data['client_name'])}\n"
            f"<b>Название:</b> {html.escape(deal_data['title'])}"
            f"{extra_lines}\n"
            f"<b>Услуги:</b>\n{services_str}\n\n"
            f"<b>Итоговая стоимость: {total_cost_str}</b>"
        )
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=context.user_data['main_dialog_message_id'], text=final_text, parse_mode='HTML')
            
    except requests.RequestException as e:
        error_details = str(e)
        if e.response is not None: error_details = e.response.json().get('detail', e.response.text)
        logger.error(f"Ошибка API при создании сделки: {error_details}")
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=context.user_data['main_dialog_message_id'], text=f"❌ <b>Не удалось создать сделку.</b>\nОшибка сервера: {html.escape(error_details)}")
    finally:
        context.user_data.clear()
        return ConversationHandler.END

async def cancel_deal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await cleanup_temp_messages(context, query.message.chat_id)
    await context.bot.edit_message_text(
        chat_id=query.message.chat_id, 
        message_id=context.user_data['main_dialog_message_id'], 
        text="Действие отменено.",
        reply_markup=None
    )
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await cleanup_temp_messages(context, update.effective_chat.id)
    if 'main_dialog_message_id' in context.user_data:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=context.user_data['main_dialog_message_id'], 
            text="Действие отменено.",
            reply_markup=None
        )
    else:
        await update.message.reply_text("Действие отменено.")
    context.user_data.clear()
    return ConversationHandler.END


# ════════════════════════════════════════
#  📅  РАСПИСАНИЕ НА СЕГОДНЯ / ЗАВТРА
# ════════════════════════════════════════

async def schedule_for_day(target_date: date) -> str:
    engine_r = create_engine(DATABASE_URL)
    Session_r = sessionmaker(bind=engine_r)
    from sqlalchemy import func as sa_func
    from datetime import datetime as _dt

    with Session_r() as session:
        deals = (
            session.query(Deal)
            .options(joinedload(Deal.contact), joinedload(Deal.stage))
            .filter(
                Deal.deal_date != None,
                sa_func.date(Deal.deal_date) == target_date,
            )
            .order_by(Deal.deal_date)
            .all()
        )

    engine_r.dispose()

    label = "сегодня" if target_date == date.today() else "завтра"
    day_str = target_date.strftime("%d.%m.%Y")

    if not deals:
        return f"📅 Выездов на {label} ({day_str}) нет."

    lines = [f"📅 <b>Выезды на {label}, {day_str}:</b>\n"]
    for i, d in enumerate(deals, 1):
        client = d.contact.name if d.contact else "Без клиента"
        stage = d.stage.name if d.stage else "—"
        time_str = d.deal_date.strftime("%H:%M") if d.deal_date else "—"
        total = f"{int(d.total or 0):,}".replace(",", " ")
        lines.append(f"<b>{i}. {time_str}</b> — {html.escape(d.title)}")
        lines.append(f"   👤 {html.escape(client)}  |  {stage}  |  {total} руб.")
        if d.address:
            lines.append(f"   📍 {html.escape(d.address)}")
        lines.append("")

    return "\n".join(lines).strip()


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await schedule_for_day(date.today())
    await update.message.reply_text(msg, parse_mode='HTML')


async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await schedule_for_day(date.today() + timedelta(days=1))
    await update.message.reply_text(msg, parse_mode='HTML')


# ════════════════════════════════════════
#  💸  ДОБАВЛЕНИЕ РАСХОДА
# ════════════════════════════════════════

EXPENSE_CATEGORIES = ["Техника", "Топливо", "Расходники", "Реклама", "Запчасти", "Прочее"]


async def newexpense_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data['expense_data'] = {}
    add_message_to_cleanup(context, update.message.message_id)

    keyboard = [[InlineKeyboardButton(cat, callback_data=f"expcat_{cat}")] for cat in EXPENSE_CATEGORIES]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="expcancel")])
    sent = await update.message.reply_text(
        "<b>Новый расход</b>\n\n<b>Шаг 1/3:</b> Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    context.user_data['exp_main_msg_id'] = sent.message_id
    return EXP_CHOOSE_CATEGORY


async def exp_choose_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category = query.data.split("expcat_", 1)[1]
    context.user_data['expense_data']['category'] = category

    await query.edit_message_text(
        f"Категория: <b>{html.escape(category)}</b>\n\n"
        "<b>Шаг 2/3:</b> Введите название расхода\n"
        "<i>(например: Бензин АИ-95, Леска для триммера)</i>",
        parse_mode='HTML'
    )
    return EXP_GET_NAME


async def exp_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    add_message_to_cleanup(context, update.message.message_id)

    if not text:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['exp_main_msg_id'],
            text="Название не может быть пустым. Введите ещё раз:"
        )
        return EXP_GET_NAME

    context.user_data['expense_data']['name'] = text
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=context.user_data['exp_main_msg_id'],
        text=f"Название: <b>{html.escape(text)}</b>\n\n"
             "<b>Шаг 3/3:</b> Введите сумму в рублях (например: 500 или 1250.50):",
        parse_mode='HTML'
    )
    return EXP_GET_AMOUNT


async def exp_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(',', '.')
    add_message_to_cleanup(context, update.message.message_id)

    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['exp_main_msg_id'],
            text="Неверная сумма. Введите положительное число (например: 500 или 1250.50):"
        )
        return EXP_GET_AMOUNT

    expense = context.user_data['expense_data']
    expense['amount'] = amount

    payload = {
        "name": expense['name'],
        "amount": amount,
        "date": date.today().isoformat(),
        "category": expense['category'],
    }

    try:
        response = requests.post(f"{API_BASE_URL}/expenses", json=payload, timeout=10, headers=API_HEADERS)
        response.raise_for_status()
        amount_str = f"{amount:,.2f}".replace(",", " ").rstrip('0').rstrip('.')
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['exp_main_msg_id'],
            text=f"✅ <b>Расход записан!</b>\n\n"
                 f"📂 <b>Категория:</b> {html.escape(expense['category'])}\n"
                 f"📝 <b>Название:</b> {html.escape(expense['name'])}\n"
                 f"💸 <b>Сумма:</b> {amount_str} руб.\n"
                 f"📅 <b>Дата:</b> {date.today().strftime('%d.%m.%Y')}",
            parse_mode='HTML'
        )
    except requests.RequestException as e:
        error_details = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_details = e.response.json().get('detail', e.response.text)
            except Exception:
                error_details = e.response.text
        logger.error(f"Ошибка API при создании расхода: {error_details}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['exp_main_msg_id'],
            text=f"❌ <b>Не удалось записать расход.</b>\nОшибка: {html.escape(str(error_details))}",
            parse_mode='HTML'
        )

    context.user_data.clear()
    return ConversationHandler.END


async def exp_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Отменено.", reply_markup=None)
    context.user_data.clear()
    return ConversationHandler.END


# ════════════════════════════════════════
#  📊  ОТЧЁТ
# ════════════════════════════════════════

from sqlalchemy import text as sa_text

async def build_report_string() -> str:
    engine_r = create_engine(DATABASE_URL)
    Session_r = sessionmaker(bind=engine_r)
    today = date.today()
    lines = []

    with Session_r() as session:
        # ── Сделки в работе ──
        active_deals = (
            session.query(Deal)
            .join(Stage)
            .filter(Stage.is_final == False)
            .order_by(Stage.order, Deal.created_at.desc())
            .all()
        )

        lines.append(f"🌿 GrassCRM — отчёт за {today.strftime('%d.%m.%Y')}")
        lines.append("")

        if active_deals:
            lines.append("📋 Сделки в работе:")
            current_stage = None
            for deal in active_deals:
                stage_name = deal.stage.name if deal.stage else "Без стадии"
                if stage_name != current_stage:
                    current_stage = stage_name
                    lines.append(f"\n  ▸ {stage_name}")
                client = deal.contact.name if deal.contact else "Без клиента"
                total = f"{int(deal.total or 0):,}".replace(",", " ")
                lines.append(f"    · {deal.title} ({client}) — {total} руб.")
                if deal.deal_date:
                    lines.append(f"      📅 {deal.deal_date.strftime('%d.%m.%Y %H:%M')}")
                if deal.address:
                    lines.append(f"      📍 {deal.address}")
        else:
            lines.append("📋 Активных сделок нет.")

        lines.append("")

        # ── Задачи ──
        today_tasks = (
            session.query(Task)
            .filter(Task.is_done == False, Task.due_date == today)
            .all()
        )
        overdue_tasks = (
            session.query(Task)
            .filter(Task.is_done == False, Task.due_date < today)
            .order_by(Task.due_date)
            .all()
        )

        if today_tasks:
            lines.append("✅ Задачи на сегодня:")
            for t in today_tasks:
                lines.append(f"    · {t.title}")
            lines.append("")

        if overdue_tasks:
            lines.append(f"⚠️ Просрочено ({len(overdue_tasks)}):")
            for t in overdue_tasks[:5]:
                due = t.due_date.strftime("%d.%m") if t.due_date else "—"
                lines.append(f"    · {t.title} (до {due})")
            if len(overdue_tasks) > 5:
                lines.append(f"    ...и ещё {len(overdue_tasks) - 5}")
            lines.append("")

        if not today_tasks and not overdue_tasks:
            lines.append("✅ Задач на сегодня нет.")
            lines.append("")

        # ── Цитата из daily_phrases ──
        try:
            row = session.execute(
                sa_text("SELECT phrase FROM daily_phrases ORDER BY RANDOM() LIMIT 1")
            ).fetchone()
            if row:
                # Заменяем литеральный \n на реальный перенос строки
                phrase = row[0].replace("\\n", "\n")
                lines.append("· · · · · · · · · ·")
                lines.append(phrase)
        except Exception as e:
            logger.warning(f"Не удалось получить цитату: {e}")

    engine_r.dispose()
    return "\n".join(lines)


async def send_report_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Отправляю ежедневный отчёт...")
    try:
        msg = await build_report_string()
        await context.bot.send_message(chat_id=TG_CHAT_ID, text=msg)
        logger.info("Отчёт отправлен.")
    except Exception as e:
        logger.error(f"Ошибка при отправке отчёта: {e}")


async def send_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Формирую отчёт...")
    try:
        msg = await build_report_string()
        await context.bot.send_message(chat_id=TG_CHAT_ID, text=msg)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(f"Ошибка: {e}")


# ════════════════════════════════════════
#  🚀  ЗАПУСК БОТА
# ════════════════════════════════════════

def main() -> None:
    if not TG_TOKEN or not TG_CHAT_ID: sys.exit(1)

    application = Application.builder().token(TG_TOKEN).build()

    job_queue = application.job_queue
    report_time = time(hour=18, minute=0, tzinfo=pytz.timezone('Europe/Moscow'))
    job_queue.run_daily(send_report_job, time=report_time)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newdeal", new_deal_start)],
        states={
            GET_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deal_title)],
            GET_CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_client_name)],
            GET_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deal_date)],
            GET_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deal_time)],
            GET_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deal_address)],
            CHOOSE_CATEGORY: [
                CallbackQueryHandler(cancel_deal_callback, pattern="^cancel_deal$"),
                CallbackQueryHandler(choose_category_callback, pattern="^cat_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, wrong_input_handler)
            ],
            CHOOSE_SERVICE: [
                CallbackQueryHandler(back_to_category_callback, pattern="^back_to_cat$"),
                CallbackQueryHandler(choose_service_callback, pattern="^service_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, wrong_input_handler)
            ],
            GET_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_quantity)],
            ADD_MORE: [
                CallbackQueryHandler(add_more_or_finish_callback, pattern="^(add_more|finish)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, wrong_input_handler)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        conversation_timeout=900
    )

    application.add_handler(conv_handler)

    expense_handler = ConversationHandler(
        entry_points=[CommandHandler("newexpense", newexpense_start)],
        states={
            EXP_CHOOSE_CATEGORY: [
                CallbackQueryHandler(exp_cancel_callback, pattern="^expcancel$"),
                CallbackQueryHandler(exp_choose_category_callback, pattern="^expcat_"),
            ],
            EXP_GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, exp_get_name)],
            EXP_GET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, exp_get_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        conversation_timeout=300
    )
    application.add_handler(expense_handler)

    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text(
        "Привет! Я CRM-бот 🌿\n\n"
        "/newdeal — создать сделку\n"
        "/newexpense — записать расход\n"
        "/today — выезды на сегодня\n"
        "/tomorrow — выезды на завтра\n"
        "/sendreport — отправить отчёт"
    )))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("tomorrow", tomorrow_command))
    application.add_handler(CommandHandler("sendreport", send_report_command))

    logger.info("Бот запускается...")
    application.run_polling()

if __name__ == "__main__":
    main()
