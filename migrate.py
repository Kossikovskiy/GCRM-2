
import os
import sys
import urllib.request
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

# --- Важно! Импортируем модели и URL из основного приложения ---
# Это гарантирует, что мы работаем с одинаковыми структурами данных
from main import (
    Base, Deal, Stage, ServiceCategory, Service, Equipment, 
    ExpenseCategory, Expense, User, DATABASE_URL
)

# --- Настройки ---
# URL для скачивания файла crm.db из вашего GitHub репозитория
GIT_DB_URL = "https://raw.githubusercontent.com/Kossikovskiy/GCRM-2/main/crm.db"
LOCAL_DB_FILENAME = "crm_from_git.db" # Временное имя файла
SOURCE_DB_URL = f"sqlite:///{LOCAL_DB_FILENAME}"

def download_db_from_git():
    """Скачивает файл базы данных с GitHub."""
    print(f"Скачиваю файл базы данных из {GIT_DB_URL}...")
    try:
        urllib.request.urlretrieve(GIT_DB_URL, LOCAL_DB_FILENAME)
        print(f"Файл успешно сохранен как {LOCAL_DB_FILENAME}")
        return True
    except Exception as e:
        print(f"Ошибка при скачивании файла: {e}", file=sys.stderr)
        return False

def run_migration():
    """Выполняет миграцию данных из SQLite в PostgreSQL."""

    if not DATABASE_URL or not DATABASE_URL.startswith("postgres"):
        print("Ошибка: Переменная окружения DATABASE_URL для PostgreSQL не установлена.", file=sys.stderr)
        sys.exit(1)

    print("\n--- Начало миграции ---")
    if not download_db_from_git():
        sys.exit(1)

    print("Подключаюсь к базам данных...")
    source_engine = create_engine(SOURCE_DB_URL)
    target_engine = create_engine(DATABASE_URL)

    SourceSession = sessionmaker(bind=source_engine)
    TargetSession = sessionmaker(bind=target_engine)

    source_session = SourceSession()
    target_session = TargetSession()
    
    print("Проверяю/создаю таблицы в целевой базе данных (PostgreSQL)...")
    Base.metadata.create_all(target_engine)
    print("Таблицы успешно проверены/созданы.")

    try:
        # Очищаем таблицы в целевой БД перед миграцией, чтобы избежать дублей
        print("Очищаю таблицы в целевой БД перед переносом...")
        for table in reversed(Base.metadata.sorted_tables):
            target_session.execute(table.delete())
        target_session.commit()
        print("Таблицы очищены.")

        # --- Начало переноса данных ---
        print("\n--- Перенос Справочников ---")
        
        # Стадии
        stages_map = {obj.id: Stage(**{c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name != 'id'}) for obj in source_session.query(Stage).all()}
        target_session.add_all(stages_map.values())
        target_session.commit()
        print(f"Стадии: перенесено {len(stages_map)} записей.")

        # Категории услуг
        service_cat_map = {obj.id: ServiceCategory(**{c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name != 'id'}) for obj in source_session.query(ServiceCategory).all()}
        target_session.add_all(service_cat_map.values())
        target_session.commit()
        print(f"Категории услуг: перенесено {len(service_cat_map)} записей.")

        # Категории расходов
        expense_cat_map = {obj.id: ExpenseCategory(name=obj.name) for obj in source_session.query(ExpenseCategory).all()}
        target_session.add_all(expense_cat_map.values())
        target_session.commit()
        print(f"Категории расходов: перенесено {len(expense_cat_map)} записей.")

        # --- Перенос Зависимых данных ---
        print("\n--- Перенос Основных Данных ---")

        # Услуги (зависят от категорий)
        services = [Service(name=s.name, category_id=service_cat_map[s.category_id].id, unit=s.unit, price=s.price, min_volume=s.min_volume) for s in source_session.query(Service).all() if s.category_id in service_cat_map]
        target_session.bulk_save_objects(services)
        target_session.commit()
        print(f"Услуги: перенесено {len(services)} записей.")
        
        # Сделки (зависят от стадий)
        deals = [Deal(title=d.title, client=d.client, stage_id=stages_map[d.stage_id].id, created_at=d.created_at) for d in source_session.query(Deal).all() if d.stage_id in stages_map]
        target_session.bulk_save_objects(deals)
        target_session.commit()
        print(f"Сделки: перенесено {len(deals)} записей.")

        # Оборудование
        equipment = [Equipment(**{c.name: getattr(e, c.name) for c in e.__table__.columns if c.name != 'id'}) for e in source_session.query(Equipment).all()]
        target_session.bulk_save_objects(equipment)
        target_session.commit()
        print(f"Оборудование: перенесено {len(equipment)} записей.")
        
        # Расходы (зависят от категорий)
        expenses = [Expense(date=e.date, name=e.name, amount=e.amount, year=e.year, category_id=expense_cat_map[e.category_id].id) for e in source_session.query(Expense).all() if e.category_id in expense_cat_map]
        target_session.bulk_save_objects(expenses)
        target_session.commit()
        print(f"Расходы: перенесено {len(expenses)} записей.")

        print("\n✅✅✅ Миграция успешно завершена! ✅✅✅")

    except Exception as e:
        print(f"\n❌ Произошла ошибка во время миграции: {e}", file=sys.stderr)
        print("База данных может быть в несогласованном состоянии. Откатываю изменения...", file=sys.stderr)
        target_session.rollback()
    finally:
        source_session.close()
        target_session.close()
        print("Закрываю подключения к базам.")
        if os.path.exists(LOCAL_DB_FILENAME):
            os.remove(LOCAL_DB_FILENAME)
            print(f"Временный файл {LOCAL_DB_FILENAME} удален.")

if __name__ == "__main__":
    run_migration()
