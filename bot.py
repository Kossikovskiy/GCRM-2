# -*- coding: utf-8 -*-
"""
Telegram-бот для утренних уведомлений GreenCRM
Запуск вручную:  python bot.py
"""

import sys
import os
import asyncio
from datetime import date, timedelta

# Добавляем корневую папку проекта в sys.path, чтобы можно было импортировать main
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram import Bot

# --- ВАЖНО: Импортируем модели из main.py ---
# Это гарантирует, что мы работаем с той же структурой базы данных
from main import Deal, Stage, Equipment, DATABASE_URL

# ════════════════════════════════════════
#  ⚙️  НАСТРОЙКИ 
# ════════════════════════════════════════

# Токен, полученный от @BotFather
TG_TOKEN = "8620281491:AAFhrxrs5TzMCAl5NCEStaADv9MOX_4PsbE"
# ID чата, куда бот будет отправлять сообщения
TG_CHAT = "-4993820220"

# ════════════════════════════════════════

async def send_morning_report():
    """Собирает утренний отчёт из БД и отправляет его в Telegram."""

    if not TG_TOKEN or not TG_CHAT or TG_TOKEN == "ВСТАВЬТЕ_ВАШ_ТОКЕН_СЮДА":
        print("❌ Ошибка: Укажите корректные TG_TOKEN и TG_CHAT в файле bot.py")
        return

    print("🔌 Подключаюсь к базе данных...")
    if not DATABASE_URL:
        print("❌ Ошибка: Переменная окружения DATABASE_URL не установлена.")
        sys.exit(1)

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)

    today = date.today()
    in_week = today + timedelta(days=7)

    report_lines = []

    try:
        with Session() as session:
            print("🔍 Собираю данные для отчета...")

            # ── 1. Сделки, которые не в финальных стадиях ──
            active_stages = session.query(Stage).filter(Stage.is_final == False).all()
            active_stage_ids = [s.id for s in active_stages]

            active_deals = session.query(Deal).filter(
                Deal.stage_id.in_(active_stage_ids)
            ).order_by(Deal.stage_id, Deal.created_at).all() if active_stage_ids else []

            # ── 2. Техника, требующая ТО на этой неделе ──
            equip_attention = session.query(Equipment).filter(
                Equipment.next_maintenance_date <= in_week,
                Equipment.next_maintenance_date >= today,
                Equipment.status == "active"
            ).all()

            # ── 3. Техника в ремонте ──
            in_repair = session.query(Equipment).filter(
                Equipment.status == "repair"
            ).all()

        # ── Формируем текст сообщения ──────────────────────────
        print("✍️ Формирую текст сообщения...")

        weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
        today_str = f"{today.strftime('%d.%m.%Y')}, {weekdays[today.weekday()]}"

        report_lines.append(f"🌿 <b>GreenCRM — Доброе утро!</b>")
        report_lines.append(f"📅 {today_str}")
        report_lines.append("")

        # Активные сделки
        if active_deals:
            report_lines.append(f"📋 <b>Активных сделок: {len(active_deals)}</b>")
            
            deals_by_stage = {}
            for stage in active_stages:
                deals_by_stage[stage.name] = []
            
            for deal in active_deals:
                if deal.stage:
                    deals_by_stage[deal.stage.name].append(deal)
            
            for stage_name, deals in deals_by_stage.items():
                if deals:
                    report_lines.append(f"\n<b>{stage_name}</b>")
                    for deal in deals:
                        client_name = deal.contact.name if deal.contact else "Клиент не указан"
                        total_str = f"{int(deal.total or 0):,}₽".replace(",", " ")
                        report_lines.append(f"  • <b>{client_name}</b> ({total_str}) – <i>{deal.title[:40]}</i>")
        else:
            report_lines.append("✅ <b>Активных сделок нет.</b> Время создавать новые!")

        report_lines.append("")

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

        report_lines.append("")
        report_lines.append("━━━━━━━━━━━━━━━━━━━━")
        report_lines.append("Желаю продуктивного дня!")

        final_message = "\n".join(report_lines)
        
        # Инициализируем бота и отправляем сообщение
        bot = Bot(token=TG_TOKEN)
        
        print(f"📤 Отправляю сообщение в чат {TG_CHAT}...")
        await bot.send_message(
            chat_id=TG_CHAT,
            text=final_message,
            parse_mode='HTML'
        )
        print("✅ Сообщение успешно отправлено!")

    except Exception as e:
        print(f"❌ Произошла ошибка: {e}")
        try:
            error_bot = Bot(token=TG_TOKEN)
            await error_bot.send_message(
                chat_id=TG_CHAT,
                text=f"<b>Ошибка в работе Telegram-бота!</b>\n<pre>{e}</pre>",
                parse_mode='HTML'
            )
        except Exception as e_send:
            print(f"❌ Не удалось отправить сообщение об ошибке: {e_send}")

if __name__ == "__main__":
    print("🌿 GreenCRM — запуск отправки утреннего отчёта...")
    asyncio.run(send_morning_report())
