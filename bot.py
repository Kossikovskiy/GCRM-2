# -*- coding: utf-8 -*-
"""
Telegram-бот для вечерних уведомлений GreenCRM
Запуск вручную:  python bot.py
"""

import sys
import os
import asyncio
import html
from datetime import date, timedelta

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, joinedload, contains_eager
from telegram import Bot

# --- ВАЖНО: Импортируем модели из main.py ---
from main import Deal, Stage, Equipment, Contact, Task, DailyPhrase, DATABASE_URL

# ════════════════════════════════════════
#  ⚙️  НАСТРОЙКИ
# ════════════════════════════════════════

TG_TOKEN = "8620281491:AAFhrxrs5TzMCAl5NCEStaADv9MOX_4PsbE"
TG_CHAT = "-4993820220"

# ════════════════════════════════════════

async def send_evening_report():
    """Собирает вечерний отчёт из БД и отправляет его в Telegram."""

    if not TG_TOKEN or not TG_CHAT or "ВАШ_ТОКЕН" in TG_TOKEN:
        print("❌ Ошибка: Укажите корректные TG_TOKEN и TG_CHAT в файле bot.py")
        return

    print("🔌 Подключаюсь к базе данных...")
    if not DATABASE_URL:
        print("❌ Ошибка: Переменная окружения DATABASE_URL не установлена.")
        return

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    final_message = ""

    try:
        with Session() as session:
            print("🔍 Собираю данные для отчета...")

            today = date.today()
            tomorrow = today + timedelta(days=1)

            # 1. Задачи на завтра
            tasks_for_tomorrow = session.query(Task).filter(
                Task.due_date == tomorrow,
                Task.is_done == False
            ).order_by(Task.priority).all()

            # 2. Сделки, которые не в финальных стадиях
            active_stage_ids_tuples = session.query(Stage.id).filter(Stage.is_final == False).all()
            active_stage_ids = [s_id[0] for s_id in active_stage_ids_tuples]

            active_deals = []
            if active_stage_ids:
                active_deals = session.query(Deal).join(Deal.stage).options(
                    contains_eager(Deal.stage),
                    joinedload(Deal.contact)
                ).filter(
                    Deal.stage_id.in_(active_stage_ids)
                ).order_by(Stage.order, Deal.created_at).all()

            # 3. Техника в ремонте или требующая ТО
            in_repair = session.query(Equipment).filter(Equipment.status == "repair").all()
            equip_attention = session.query(Equipment).filter(
                Equipment.next_maintenance_date <= tomorrow + timedelta(days=7),
                Equipment.status == "active"
            ).all()

            # 4. Случайная фраза
            random_phrase = session.query(DailyPhrase).order_by(func.random()).first()

            # --- Формируем текст сообщения (ВНУТРИ СЕССИИ!) ---
            print("✍️ Формирую текст сообщения...")
            report_lines = []
            weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            tomorrow_str = f"{tomorrow.strftime('%d.%m.%Y')}, {weekdays[tomorrow.weekday()]}"

            report_lines.append(f"🌿 <b>GreenCRM — Добрый вечер!</b>")
            report_lines.append(f"📅 План на завтра: {tomorrow_str}")
            report_lines.append("")
            
            # Задачи
            if tasks_for_tomorrow:
                report_lines.append(f"📝 <b>Задачи на завтра: {len(tasks_for_tomorrow)}</b>")
                for task in tasks_for_tomorrow:
                    priority_emoji = {"Высокий": "🔥", "Средний": "🔸", "Низкий": "🔹"}.get(task.priority, "")
                    report_lines.append(f"  • {priority_emoji} {task.title}")
                report_lines.append("")
            else:
                report_lines.append("✅ <b>Задач на завтра нет.</b> Отличная работа!\n")

            # Сделки
            if active_deals:
                report_lines.append(f"📋 <b>Активные сделки: {len(active_deals)}</b>")
                current_stage_name = ""
                for deal in active_deals:
                    if deal.stage.name != current_stage_name:
                        current_stage_name = deal.stage.name
                        report_lines.append(f"\n<b>{current_stage_name}</b>")
                    client_name = deal.contact.name if deal.contact else "Клиент не указан"
                    total_str = f"{int(deal.total or 0):,} ₽".replace(",", " ")
                    report_lines.append(f"  • <b>{client_name}</b> ({total_str}) – <i>{deal.title[:40]}</i>")
            else:
                report_lines.append("✅ <b>Активных сделок нет.</b>")

            report_lines.append("\n")

            # Техника
            if equip_attention or in_repair:
                 report_lines.append("🛠️ <b>Техника</b>")
            if equip_attention:
                report_lines.append("  <u>Требует внимания (ТО):</u>")
                for eq in equip_attention:
                    if not eq.next_maintenance_date: continue
                    days_left = (eq.next_maintenance_date - today).days
                    when = "сегодня!" if days_left <= 0 else f"через {days_left} дн."
                    report_lines.append(f"  ⚠️ {eq.name} — {when}")
            if in_repair:
                report_lines.append("  <u>В ремонте:</u>")
                for eq in in_repair:
                    report_lines.append(f"  🔴 {eq.name} ({eq.model or ''})")
            if not equip_attention and not in_repair:
                report_lines.append("👍 <b>Техника:</b> всё в порядке.")

            report_lines.append("\n━━━━━━━━━━━━━━━━━━━━")
            
            if random_phrase:
                report_lines.append(f"<i>{random_phrase.phrase}</i>")

            report_lines.append("\nХорошего вечера!")
            
            final_message = "\n".join(report_lines)

        # --- Отправка сообщения (после закрытия сессии, когда строка уже готова) ---
        bot = Bot(token=TG_TOKEN)
        print(f"📤 Отправляю сообщение в чат {TG_CHAT}...")
        await bot.send_message(chat_id=TG_CHAT, text=final_message, parse_mode='HTML')
        print("✅ Сообщение успешно отправлено!")

    except Exception as e:
        print(f"❌ Произошла ошибка: {e}")
        try:
            error_bot = Bot(token=TG_TOKEN)
            sanitized_error = html.escape(str(e), quote=True)
            await error_bot.send_message(
                chat_id=TG_CHAT,
                text=f"<b>Ошибка в работе Telegram-бота!</b>\n<pre>{sanitized_error}</pre>",
                parse_mode='HTML'
            )
        except Exception as e_send:
            print(f"❌ Не удалось отправить сообщение об ошибке: {e_send}")

if __name__ == "__main__":
    print("🌿 GreenCRM — запуск отправки вечернего отчёта...")
    try:
        import sqlalchemy
        import telegram
    except ImportError:
        print("\n❌ Ошибка: Не найдены необходимые библиотеки.")
        print(f"   Пожалуйста, активируйте виртуальное окружение: `source /var/www/crm/venv/bin/activate`")
        sys.exit(1)

    asyncio.run(send_evening_report())
