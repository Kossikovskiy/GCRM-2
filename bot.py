# -*- coding: utf-8 -*-
"""
Telegram-бот для утренних уведомлений GreenCRM
Запуск вручную:  python bot.py
"""

import sys
import os
import asyncio
import html
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload
from telegram import Bot

from main import Deal, Stage, Equipment, Contact, DATABASE_URL

# ════════════════════════════════════════
#  ⚙️  НАСТРОЙКИ
# ════════════════════════════════════════

TG_TOKEN = "8620281491:AAFhrxrs5TzMCAl5NCEStaADv9MOX_4PsbE"
TG_CHAT = "-4993820220"

# ════════════════════════════════════════

async def send_morning_report():
    """Собирает утренний отчёт из БД и отправляет его в Telegram."""

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

            # 1. Сделки, которые не в финальных стадиях
            active_stage_ids_tuples = session.query(Stage.id).filter(Stage.is_final == False).all()
            active_stage_ids = [s_id[0] for s_id in active_stage_ids_tuples]

            active_deals = []
            if active_stage_ids:
                active_deals = session.query(Deal).options(
                    joinedload(Deal.stage),
                    joinedload(Deal.contact)
                ).filter(
                    Deal.stage_id.in_(active_stage_ids)
                ).join(Stage).order_by(Stage.order, Deal.created_at).all()

            # 2. Техника, требующая ТО на этой неделе
            equip_attention = session.query(Equipment).filter(
                Equipment.next_maintenance_date <= date.today() + timedelta(days=7),
                Equipment.next_maintenance_date >= date.today(),
                Equipment.status == "active"
            ).all()

            # 3. Техника в ремонте
            in_repair = session.query(Equipment).filter(Equipment.status == "repair").all()

            # --- Формируем текст сообщения (ВНУТРИ СЕССИИ!) ---
            print("✍️ Формирую текст сообщения...")
            report_lines = []
            today = date.today()
            weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            today_str = f"{today.strftime('%d.%m.%Y')}, {weekdays[today.weekday()]}"

            report_lines.append(f"🌿 <b>GreenCRM — Доброе утро!</b>")
            report_lines.append(f"📅 {today_str}")
            report_lines.append("")

            if active_deals:
                report_lines.append(f"📋 <b>Активных сделок: {len(active_deals)}</b>\n")
                current_stage_name = ""
                for deal in active_deals:
                    if deal.stage.name != current_stage_name:
                        current_stage_name = deal.stage.name
                        report_lines.append(f"<b>{current_stage_name}</b>")
                    client_name = deal.contact.name if deal.contact else "Клиент не указан"
                    total_str = f"{int(deal.total or 0):,} ₽".replace(",", " ")
                    report_lines.append(f"  • <b>{client_name}</b> ({total_str}) – <i>{deal.title[:40]}</i>")
            else:
                report_lines.append("✅ <b>Активных сделок нет.</b> Время создавать новые!")

            report_lines.append("\n")

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
            report_lines.append("Желаю продуктивного дня!")
            
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
    print("🌿 GreenCRM — запуск отправки утреннего отчёта...")
    try:
        import sqlalchemy
        import telegram
    except ImportError:
        print("\n❌ Ошибка: Не найдены необходимые библиотеки.")
        print(f"   Пожалуйста, активируйте виртуальное окружение: `source /var/www/crm/venv/bin/activate`")
        sys.exit(1)

    asyncio.run(send_morning_report())
