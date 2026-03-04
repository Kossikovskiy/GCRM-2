
import os
import sys
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# Загружаем переменные окружения (например, DATABASE_URL)
load_dotenv()

# Импортируем модели из main.py
# Это немного "грязный" способ, но для одноразового скрипта он подходит
from main import (
    Base, Contact, Deal, DealService, Service, Stage, 
    Expense, ExpenseCategory, Equipment, Maintenance, Consumable, User
)

# --- 1. НАСТРОЙКА СОЕДИНЕНИЯ С БД ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("FATAL: Переменная окружения DATABASE_URL не установлена.", flush=True)
    sys.exit(1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- 2. ПУТИ К ФАЙЛАМ ---
DEALS_FILE_PATH = 'docs/DEAL_20260225_80749126_699ec1e196167.xls'
BUSINESS_FILE_PATH = 'docs/Покос Управление Бизнесом.xlsx'

# --- 3. ФУНКЦИИ-ПОМОЩНИКИ ---

def get_or_create_contact(session, name, phone, source):
    """Находит контакт по телефону или создает нового, если не найден."""
    if not name or pd.isna(name):
        name = "Имя не указано" # Имя по умолчанию, если оно пустое

    # Пытаемся найти контакт по номеру телефона, если он есть
    contact = None
    if phone and not pd.isna(phone):
        # Приводим телефон к стандартному формату (только цифры)
        normalized_phone = ''.join(filter(str.isdigit, str(phone)))
        if normalized_phone:
            contact = session.query(Contact).filter(Contact.phone.like(f'%{normalized_phone}%')).first()

    # Если контакт не найден по телефону, ищем по имени
    if not contact:
        contact = session.query(Contact).filter(Contact.name == name).first()
        
    # Если контакт все еще не найден, создаем новый
    if not contact:
        print(f"Создание нового контакта: {name}, Телефон: {phone}")
        contact = Contact(
            name=str(name),
            phone=str(phone) if phone and not pd.isna(phone) else None,
            source=str(source) if source and not pd.isna(source) else 'Импорт'
        )
        session.add(contact)
        session.flush() # Получаем ID для нового контакта
    
    return contact

# --- 4. ОСНОВНАЯ ЛОГИКА МИГРАЦИИ ---

def migrate_deals(session):
    """Миграция данных о сделках и контактах."""
    print(f"\n--- Начало миграции сделок из файла: {DEALS_FILE_PATH} ---")
    
    try:
        df = pd.read_excel(DEALS_FILE_PATH)
        print(f"Успешно прочитано {len(df)} строк из файла сделок.")
    except FileNotFoundError:
        print(f"ОШИБКА: Файл {DEALS_FILE_PATH} не найден.")
        return

    # Получаем ID стадий "Успешно" и "Провалена" для автоматического назначения
    success_stage = session.query(Stage).filter_by(type='success').first()
    
    if not success_stage:
        print("ОШИБКА: Не найдена стадия 'Успешно' в базе данных. Прерывание.")
        return

    for index, row in df.iterrows():
        try:
            # Пропускаем строки, где нет названия сделки
            if pd.isna(row.get('Сделка')):
                continue

            # 1. Получаем или создаем контакт
            contact = get_or_create_contact(
                session=session,
                name=row.get('Контакт'),
                phone=row.get('Телефон'),
                source=row.get('Источник')
            )

            # 2. Создаем сделку
            # Преобразуем дату, обрабатывая возможные ошибки
            try:
                deal_date = pd.to_datetime(row.get('Дата'), errors='coerce')
                if pd.isna(deal_date):
                    deal_date = datetime.now()
            except Exception:
                deal_date = datetime.now()

            new_deal = Deal(
                title=row.get('Сделка'),
                total=float(row.get('Сумма', 0)),
                address=str(row.get('Адрес', '')),
                deal_date=deal_date,
                is_repeat=bool(row.get('Повторная сделка', False)),
                created_at=deal_date, # Используем дату сделки как дату создания
                closed_at=deal_date,  # Считаем сделку закрытой в ту же дату
                contact_id=contact.id,
                stage_id=success_stage.id # Все импортированные сделки считаем успешными
            )
            session.add(new_deal)
            
            print(f"  -> Обработана сделка: '{new_deal.title}' для контакта '{contact.name}'")

        except Exception as e:
            print(f"!! Ошибка при обработке строки {index+2}: {row.to_dict()}")
            print(f"!! Исключение: {e}")
            session.rollback() # Откатываем изменения для этой строки
            continue

    print("--- Миграция сделок завершена. Сохранение изменений... ---")
    session.commit()
    print("--- Изменения успешно сохранены в базе данных. ---")


# --- ГЛАВНАЯ ФУНКЦИЯ ---
if __name__ == "__main__":
    db_session = SessionLocal()
    try:
        # Пока вызываем только миграцию сделок
        migrate_deals(db_session)
        # migrate_business_data(db_session) # Эту функцию добавим позже
    finally:
        db_session.close()

