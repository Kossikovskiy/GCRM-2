
import os
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from main import Base, Deal, Contact, Stage  # Импортируем все нужные модели
import numpy as np
from datetime import datetime

# --- Константы и Настройки ---
DEALS_FILE_PATH = 'docs/DEAL_20260225_80749126_699ec1e196167.xls'
DATABASE_URL = os.getenv("DATABASE_URL")

def parse_date(date_str):
    if not date_str or pd.isna(date_str):
        return None
    try:
        # Попытка разбора формата '25.02.2025 21:03:00'
        return datetime.strptime(str(date_str), '%d.%m.%Y %H:%M:%S')
    except ValueError:
        return None # Если формат неверный, возвращаем None

print("--- НАЧАЛО ФИНАЛЬНОЙ МИГРАЦИИ (v3) ---")

# --- 1. Настройка БД ---
if not DATABASE_URL:
    print("FATAL: Переменная окружения DATABASE_URL не установлена.")
    exit(1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db_session = SessionLocal()

# --- 2. Создание таблиц, если их нет ---
print("Проверка и создание таблиц...")
Base.metadata.create_all(bind=engine)
print("Таблицы готовы.")

# --- 3. Загрузка справочников из БД ---
stage_map = {stage.name: stage.id for stage in db_session.query(Stage).all()}
if not stage_map:
    print("КРИТИЧЕСКАЯ ОШИБКА: Не удалось загрузить стадии сделок из базы данных.")
    db_session.close()
    exit(1)
print(f"Загружены стадии: {list(stage_map.keys())}")
default_stage_id = next(iter(stage_map.values())) # Берем ID первой попавшейся стадии как по-умолчанию

# --- 4. Чтение и обработка данных ---
try:
    print(f"Чтение данных из файла: {DEALS_FILE_PATH}")
    df = pd.read_html(DEALS_FILE_PATH, header=0)[0]
    df = df.replace({np.nan: None}) # Замена NaN на None
    print(f"Успешно прочитано {len(df)} строк.")

    for index, row in df.iterrows():
        # --- А. Извлечение и сборка данных Контакта ---
        first_name = row.get('Контакт: Имя', '')
        last_name = row.get('Контакт: Фамилия', '')
        # Собираем полное имя, отсекая лишние пробелы
        contact_name = " ".join(filter(None, [last_name, first_name])).strip()
        if not contact_name: # Если имя все равно пустое, пробуем резервное поле
            contact_name = row.get('Контакт')
        
        if not contact_name:
            print(f"ПРОПУСК строки {index+2}: Не удалось определить имя контакта.")
            continue

        contact_source = row.get('Контакт: Источник') or row.get('Источник')
        # Используем функцию для получения телефона из прошлой версии
        mobile = row.get('Контакт: Мобильный телефон')
        phone = str(mobile) if pd.notna(mobile) and mobile else None

        # --- Б. Поиск или создание Контакта ---
        contact = db_session.query(Contact).filter(Contact.name == contact_name).first()
        if not contact:
            contact = Contact(name=contact_name, phone=phone, source=contact_source)
            db_session.add(contact)
            db_session.flush() # Нужен ID для сделки
            print(f"Создан контакт: {contact.name}")

        # --- В. Извлечение и сборка данных Сделки ---
        deal_title = row.get('Название сделки')
        if not deal_title:
            print(f"ПРОПУСК строки {index+2} для контакта {contact_name}: Отсутствует название сделки.")
            continue
            
        # Стадия сделки
        stage_name = row.get('Стадия сделки')
        stage_id = stage_map.get(stage_name, default_stage_id)

        # Заметки: собираем все доп. поля в одну строку
        notes_parts = []
        if row.get('Ответственный'): notes_parts.append(f"Ответственный: {row.get('Ответственный')}")
        if row.get('Товар'): notes_parts.append(f"Товар: {row.get('Товар')} (Кол-во: {row.get('Количество', 'N/A')}, Цена: {row.get('Цена', 'N/A')})")
        if row.get('Причина отказа'): notes_parts.append(f"Причина отказа: {row.get('Причина отказа')}")
        notes = ". ".join(notes_parts)
        
        # --- Г. Создание объекта Сделки ---
        deal = Deal(
            title=str(deal_title),
            total=float(row.get('Сумма', 0.0) or 0.0),
            contact_id=contact.id,
            stage_id=stage_id, 
            deal_date=parse_date(row.get('Дата начала')),
            closed_at=parse_date(row.get('Сделка закрыта')),
            is_repeat=(row.get('Повторная сделка') == 'Да'),
            notes=notes
        )
        db_session.add(deal)
        print(f" -> Подготовлена сделка '{deal.title}' для {contact.name}")

    # --- 5. Сохранение в БД ---
    print("\n--- Сохранение всех изменений в базе данных... ---")
    db_session.commit()
    print("--- МИГРАЦИЯ УСПЕШНО ЗАВЕРШЕНА! Данные сохранены. ---")

except Exception as e:
    print(f"\n!!! Произошла КРИТИЧЕСКАЯ ОШИБКА: {e}")
    print("--- Откат изменений... ---")
    db_session.rollback()

finally:
    db_session.close()
    print("--- Завершение работы ---")
