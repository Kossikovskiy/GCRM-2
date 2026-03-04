
import os
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from main import Base, Deal, Contact  # Импортируем модели
import numpy as np # Для обработки nan

# --- Константы и Настройки ---
DEALS_FILE_PATH = 'docs/DEAL_20260225_80749126_699ec1e196167.xls'
DATABASE_URL = os.getenv("DATABASE_URL")

def get_first_phone(row):
    """Возвращает первый доступный телефон из нескольких колонок."""
    mobile = row.get('Контакт: Мобильный телефон')
    if pd.notna(mobile) and mobile:
        return str(mobile)
    work = row.get('Контакт: Рабочий телефон')
    if pd.notna(work) and work:
        return str(work)
    return None

print("--- Начало финальной миграции ---")

# --- 1. Настройка БД ---
if not DATABASE_URL:
    print("FATAL: Переменная окружения DATABASE_URL не установлена.")
    exit(1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db_session = SessionLocal()

# --- 2. Создание таблиц, если их нет ---
print("Проверка и создание таблиц 'contact' и 'deal'...")
Base.metadata.create_all(bind=engine)
print("Таблицы готовы.")

# --- 3. Чтение и обработка данных ---
try:
    print(f"Чтение данных из файла: {DEALS_FILE_PATH}")
    tables = pd.read_html(DEALS_FILE_PATH, header=0)
    df = tables[0]
    print(f"Успешно прочитано {len(df)} строк.")

    # Заменяем все NaN на None для корректной работы с базой
    df = df.replace({np.nan: None})

    for index, row in df.iterrows():
        # --- Извлечение данных из строки ---
        contact_name = row.get('Контакт: Имя') or row.get('Контакт') # Резервное имя
        contact_phone = get_first_phone(row)
        deal_title = row.get('Название сделки')
        deal_amount = row.get('Сумма')

        # Пропускаем, если нет ключевых данных
        if not contact_name or not deal_title:
            print(f"Пропущена строка {index+2}: нет имени контакта или названия сделки.")
            continue

        # --- Работа с контактом ---
        contact = None
        if contact_phone:
            contact = db_session.query(Contact).filter(Contact.phone == contact_phone).first()
        
        if not contact:
            contact = Contact(
                name=str(contact_name),
                phone=contact_phone,
                source=row.get('Источник')
            )
            db_session.add(contact)
            db_session.flush() # Получаем ID для нового контакта
            print(f"Создан новый контакт: {contact.name} (Телефон: {contact.phone})")
        else:
            print(f"Найден существующий контакт: {contact.name}")

        # --- Работа со сделкой ---
        deal = Deal(
            title=str(deal_title),
            value=float(deal_amount) if deal_amount else 0.0,
            contact_id=contact.id
        )
        db_session.add(deal)
        print(f" -> Добавлена сделка '{deal.title}' на сумму {deal.value}")

    # --- 4. Сохранение в БД ---
    print("\n--- Сохранение всех изменений в базе данных... ---")
    db_session.commit()
    print("--- Миграция успешно завершена! Все данные сохранены. ---")

except FileNotFoundError:
    print(f"ОШИБКА: Файл {DEALS_FILE_PATH} не найден.")
except Exception as e:
    print(f"Произошла критическая ошибка: {e}")
    db_session.rollback()

finally:
    db_session.close()
