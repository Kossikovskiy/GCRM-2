"""
Удаляет все сделки из базы данных.
Запуск: python scripts/clear_deals.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models.database import get_engine, get_session_factory, Deal, DealService

engine = get_engine()
Session = get_session_factory(engine)

with Session() as s:
    services_deleted = s.query(DealService).delete()
    deals_deleted = s.query(Deal).delete()
    s.commit()
    print(f"🗑️  Удалено сделок: {deals_deleted}")
    print(f"🗑️  Удалено строк услуг: {services_deleted}")
    print()
    print("✅ База очищена. Теперь запустите импорт:")
    print("   python scripts\\import_bitrix.py DEAL_....xls")
