
import os
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from main import Base, Deal, Contact # Импортируем модели

print("--- Начало очистки таблиц --- ")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("FATAL: Переменная окружения DATABASE_URL не установлена.")
    exit(1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db_session = SessionLocal()
inspector = inspect(engine)

try:
    # Проверяем, существует ли таблица 'deal'
    if inspector.has_table("deal"):
        num_deals_deleted = db_session.query(Deal).delete()
        print(f"Удалено {num_deals_deleted} записей из таблицы 'deal'.")
    else:
        print("Таблица 'deal' не существует, пропущено.")

    # Проверяем, существует ли таблица 'contact'
    if inspector.has_table("contact"):
        num_contacts_deleted = db_session.query(Contact).delete()
        print(f"Удалено {num_contacts_deleted} записей из таблицы 'contact'.")
    else:
        print("Таблица 'contact' не существует, пропущено.")

    # Подтверждаем транзакцию
    db_session.commit()
    print("--- Очистка успешно завершена. Изменения сохранены. ---")

except Exception as e:
    print(f"Произошла ошибка во время очистки: {e}")
    db_session.rollback()

finally:
    db_session.close()

