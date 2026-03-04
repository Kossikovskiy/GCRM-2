
import os
from sqlalchemy import create_engine
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

try:
    # Сначала удаляем сделки, так как они ссылаются на контакты
    num_deals_deleted = db_session.query(Deal).delete()
    print(f"Удалено {num_deals_deleted} записей из таблицы 'deals'.")

    # Затем удаляем контакты
    num_contacts_deleted = db_session.query(Contact).delete()
    print(f"Удалено {num_contacts_deleted} записей из таблицы 'contacts'.")

    # Подтверждаем транзакцию
    db_session.commit()
    print("--- Очистка успешно завершена. Изменения сохранены. ---")

except Exception as e:
    print(f"Произошла ошибка во время очистки: {e}")
    db_session.rollback()

finally:
    db_session.close()

