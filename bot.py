# -*- coding: utf-8 -*-
"""
GreenCRM Telegram Bot
- Позволяет создавать сделки через интерактивный диалог.
- Автоматически отправляет ежедневные отчеты.
"""

import asyncio
import html
import logging
import os
import sys
from datetime import date, timedelta, time
import pytz

# --- ДОБАВЛЕНО: Загрузка переменных окружения из .env файла ---
from dotenv import load_dotenv
load_dotenv()
# ------------------------------------------------------------

import requests
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, joinedload, contains_eager

# Telegram
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# --- МОДЕЛИ ИЗ main.py ---
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from main import Deal, Stage, Equipment, Contact, Task, DailyPhrase, Service, DATABASE_URL
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

# --- НОВЫЕ ЗАГОЛОВКИ ДЛЯ API ---
if not INTERNAL_API_KEY:
    logger.critical("Переменная окружения INTERNAL_API_KEY не установлена!")
    sys.exit(1)
API_HEADERS = {"X-Internal-API-Key": INTERNAL_API_KEY}
# ---------------------------------

# Состояния для диалога
(GET_CLIENT, GET_TITLE, CHOOSE_SERVICE, GET_QUANTITY, ADD_MORE) = range(5)

# ════════════════════════════════════════
#  💬  ДИАЛОГ СОЗДАНИЯ СДЕЛКИ
# ════════════════════════════════════════

async def new_deal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['deal_data'] = {'services': []}
    await update.message.reply_text(
        "Начинаем создание новой сделки. \n\n"
        "<b>Шаг 1/5:</b> Введите имя клиента (или название компании).",
        parse_mode='HTML'
    )
    return GET_CLIENT

async def get_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    client_name = update.message.text.strip()
    if not client_name:
        await update.message.reply_text("Имя клиента не может быть пустым. Попробуйте снова.")
        return GET_CLIENT

    context.user_data['deal_data']['client_name'] = client_name
    await update.message.reply_text(
        f"Отлично, клиент: <b>{html.escape(client_name)}</b>.\n\n"
        "<b>Шаг 2/5:</b> Теперь введите название сделки (например, 'Покос газона').",
        parse_mode='HTML'
    )
    return GET_TITLE

async def get_deal_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    deal_title = update.message.text.strip()
    if not deal_title:
        await update.message.reply_text("Название сделки не может быть пустым. Попробуйте снова.")
        return GET_TITLE

    context.user_data['deal_data']['title'] = deal_title
    await update.message.reply_text(
        f"Название сделки: <b>{html.escape(deal_title)}</b>.",
        parse_mode='HTML'
    )
    logger.info("Название сделки получено. Показываю клавиатуру выбора услуг...")
    return await show_services_keyboard(update, context)

async def show_services_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info(f"Запрос услуг из API: {API_BASE_URL}/services")
    try:
        response = requests.get(f"{API_BASE_URL}/services", timeout=5, headers=API_HEADERS)
        response.raise_for_status()
        services = response.json()
        context.user_data['services_list'] = services
        logger.info(f"Получено {len(services)} услуг.")
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе услуг из API: {e}")
        msg = update.callback_query.message if update.callback_query else update.message
        await msg.reply_text("Не удалось загрузить список услуг. Попробуйте отменить (/cancel) и начать заново.")
        return CHOOSE_SERVICE

    if not services:
        await update.message.reply_text("В базе нет ни одной услуги. Добавьте их в CRM и попробуйте снова.")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(f"{s['name']} ({s['price']} ₽)", callback_data=f"service_{s['id']}")] for s in services]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = "<b>Шаг 3/5:</b> Выберите услугу из списка:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
    
    logger.info("Отправлена клавиатура выбора услуг. Возвращаю состояние CHOOSE_SERVICE.")
    return CHOOSE_SERVICE

async def choose_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    service_id = int(query.data.split('_')[1])
    logger.info(f"Нажата кнопка выбора услуги: ID {service_id}")
    context.user_data['current_service_id'] = service_id
    
    service_name = "Unknown"
    for s in context.user_data.get('services_list', []):
        if s['id'] == service_id:
            service_name = s['name']
            break

    context.user_data['current_service_name'] = service_name
    await query.edit_message_text(
        text=f"Выбрана услуга: <b>{html.escape(service_name)}</b>.\n\n"
             "<b>Шаг 4/5:</b> Введите количество (например: 1, 5, 1.5).",
        parse_mode='HTML'
    )
    return GET_QUANTITY

async def get_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        quantity = float(update.message.text.strip().replace(',', '.'))
        if quantity <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("Ошибка. Введите положительное число (например, 1 или 2.5).")
        return GET_QUANTITY

    deal_data = context.user_data['deal_data']
    deal_data['services'].append({
        'service_id': context.user_data['current_service_id'],
        'quantity': quantity,
        'name': context.user_data['current_service_name']
    })

    keyboard = [
        [InlineKeyboardButton("➕ Добавить еще услугу", callback_data='add_more')],
        [InlineKeyboardButton("✅ Завершить и создать сделку", callback_data='finish')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Услуга <b>'{html.escape(context.user_data['current_service_name'])}'</b> x{quantity} добавлена.\n\n"
        "<b>Шаг 5/5:</b> Добавить еще или завершить?",
        reply_markup=reply_markup, parse_mode='HTML'
    )
    return ADD_MORE

# --- НОВАЯ ФУНКЦИЯ ДЛЯ ОТЛАДКИ ---
async def wrong_input_in_service_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.warning(f"Получено текстовое сообщение в состоянии CHOOSE_SERVICE: '{update.message.text}'. Пользователь должен был нажать кнопку.")
    await update.message.reply_text("Пожалуйста, используйте кнопки для выбора услуги, а не вводите текст.")
    return CHOOSE_SERVICE
# ------------------------------------

async def add_more_or_finish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == 'add_more':
        return await show_services_keyboard(update, context)
    elif query.data == 'finish':
        await query.edit_message_text(text="⏳ Создаю сделку в CRM...", parse_mode='HTML')
        return await create_deal_in_api(update.callback_query.message, context)

async def create_deal_in_api(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    deal_data = context.user_data.get('deal_data')
    if not deal_data or not deal_data.get('services'):
        await message.reply_text("Нет данных для создания сделки. /newdeal для старта.")
        return ConversationHandler.END
    
    try:
        response = requests.get(f"{API_BASE_URL}/stages", timeout=5, headers=API_HEADERS)
        response.raise_for_status()
        stage_id = response.json()[0]['id']
    except (requests.RequestException, IndexError, KeyError) as e:
        logger.error(f"Не удалось получить ID начального этапа: {e}")
        await message.reply_text("Ошибка: не удалось определить начальный этап для сделки.")
        return ConversationHandler.END

    payload = {
        "title": deal_data['title'],
        "new_contact_name": deal_data['client_name'],
        "stage_id": stage_id,
        "services": [{'service_id': s['service_id'], 'quantity': s['quantity']} for s in deal_data['services']],
        "manager": message.chat.full_name,
    }

    try:
        response = requests.post(f"{API_BASE_URL}/deals", json=payload, timeout=10, headers=API_HEADERS)
        response.raise_for_status()
        
        services_str = "\n".join([f"- {html.escape(s['name'])} (x{s['quantity']})" for s in deal_data['services']])
        final_text = (
            f"✅ <b>Сделка успешно создана!</b>\n\n"
            f"<b>Клиент:</b> {html.escape(deal_data['client_name'])}\n"
            f"<b>Название:</b> {html.escape(deal_data['title'])}
"
            f"<b>Услуги:</b>\n{services_str}"
        )
        await message.reply_text(final_text, parse_mode='HTML')
            
    except requests.RequestException as e:
        error_details = str(e)
        if e.response is not None:
            try: error_details = e.response.json().get('detail', e.response.text)
            except: pass
        logger.error(f"Ошибка API при создании сделки: {error_details} | Payload: {payload}")
        await message.reply_text(f"❌ <b>Не удалось создать сделку.</b>\nОшибка сервера: {html.escape(error_details)}")
    finally:
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Действие отменено.")
    context.user_data.clear()
    return ConversationHandler.END

# ════════════════════════════════════════
#  📈  ЕЖЕДНЕВНЫЙ ОТЧЕТ
# ════════════════════════════════════════

async def build_report_string() -> str:
    if not DATABASE_URL: raise ConnectionError("DATABASE_URL не установлена.")

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        today = date.today()
        tomorrow = today + timedelta(days=1)
        
        tasks = session.query(Task).filter(Task.due_date == tomorrow, Task.is_done == False).order_by(Task.priority).all()
        
        active_stage_ids = [s[0] for s in session.query(Stage.id).filter(Stage.is_final == False).all()]
        active_deals = []
        if active_stage_ids:
            active_deals = session.query(Deal).join(Deal.stage).options(contains_eager(Deal.stage), joinedload(Deal.contact)).filter(Deal.stage_id.in_(active_stage_ids)).order_by(Stage.order, Deal.created_at).all()
        
        equip_attention = session.query(Equipment).filter(Equipment.next_maintenance_date <= tomorrow + timedelta(days=7), Equipment.status == "active").all()
        in_repair = session.query(Equipment).filter(Equipment.status == "repair").all()
        random_phrase = session.query(DailyPhrase).order_by(func.random()).first()

        weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        tomorrow_str = f"{tomorrow.strftime('%d.%m.%Y')}, {weekdays[tomorrow.weekday()]}"

        lines = [f"🌿 <b>GreenCRM — План на завтра: {tomorrow_str}</b>", ""]
        
        if tasks:
            lines.append(f"📝 <b>Задачи на завтра: {len(tasks)}</b>")
            for t in tasks:
                lines.append(f"  • {t.title}")
            lines.append("")
        else:
            lines.append("✅ <b>Задач на завтра нет.</b>\n")

        if active_deals:
            lines.append(f"📋 <b>Активные сделки: {len(active_deals)}</b>")
            current_stage = ""
            for deal in active_deals:
                if deal.stage.name != current_stage:
                    current_stage = deal.stage.name
                    lines.append(f"\n<b>Этап: {current_stage}</b>")
                client = deal.contact.name if deal.contact else "-no client-"
                total = f"{int(deal.total or 0):,} ₽".replace(",", " ")
                lines.append(f"  • <b>{client}</b> ({total}) – <i>{deal.title[:40]}</i>")
        else:
            lines.append("✅ <b>Активных сделок нет.</b>")
        lines.append("\n")

        if equip_attention or in_repair:
             lines.append("🛠️ <b>Техника</b>")
        if equip_attention:
            lines.append("  <u>Требует внимания (ТО):</u>")
            for eq in equip_attention:
                days = (eq.next_maintenance_date - today).days if eq.next_maintenance_date else 0
                when = "сегодня!" if days <= 0 else f"через {days} дн."
                lines.append(f"  ⚠️ {eq.name} — {when}")
        if in_repair:
            lines.append("  <u>В ремонте:</u>")
            for eq in in_repair:
                lines.append(f"  🔴 {eq.name}")

        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        if random_phrase: lines.append(f"<i>{random_phrase.phrase}</i>")
        
        return "\n".join(lines)

async def send_report_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Запуск автоматической отправки отчета...")
    try:
        report_text = await build_report_string()
        await context.bot.send_message(chat_id=TG_CHAT_ID, text=report_text, parse_mode='HTML')
        logger.info(f"Отчет успешно отправлен в чат {TG_CHAT_ID}")
    except Exception as e:
        logger.error(f"Ошибка при отправке отчета: {e}", exc_info=True)
        await context.bot.send_message(chat_id=TG_CHAT_ID, text=f"🔴 Не удалось создать отчет: {e}")

async def send_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Формирую отчет, это может занять до 30 секунд...")
    try:
        report_text = await build_report_string()
        await update.message.reply_text(report_text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Ошибка при ручной отправке отчета: {e}", exc_info=True)
        await update.message.reply_text(f"🔴 Не удалось создать отчет: {e}")

# ════════════════════════════════════════
#  🚀  ЗАПУСК БОТА
# ════════════════════════════════════════

def main() -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        logger.critical("Переменные окружения TG_TOKEN и TG_CHAT_ID должны быть установлены!")
        sys.exit(1)

    application = Application.builder().token(TG_TOKEN).build()

    job_queue = application.job_queue
    report_time = time(hour=18, minute=0, second=0, tzinfo=pytz.timezone('Europe/Moscow'))
    job_queue.run_daily(send_report_job, time=report_time, days=tuple(range(7)))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newdeal", new_deal_start)],
        states={
            GET_CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_client_name)],
            GET_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deal_title)],
            CHOOSE_SERVICE: [
                CallbackQueryHandler(choose_service_callback, pattern="^service_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, wrong_input_in_service_selection)
            ],
            GET_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_quantity)],
            ADD_MORE: [CallbackQueryHandler(add_more_or_finish_callback, pattern="^(add_more|finish)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=600
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text('Привет! Я CRM-бот. /newdeal для создания сделки, /sendreport для отчета.')))
    application.add_handler(CommandHandler("sendreport", send_report_command))

    logger.info(f"Бот запускается... Отчеты в {TG_CHAT_ID} запланированы на {report_time.strftime('%H:%M')} MSK.")
    application.run_polling()

if __name__ == "__main__":
    main()
