
from dotenv import load_dotenv
load_dotenv()

import sys
import os
import urllib.request
import urllib.parse
import httpx
import secrets
from urllib.parse import urlencode
from datetime import datetime, date
from contextlib import asynccontextmanager
from typing import Optional, List
from functools import lru_cache

from fastapi import FastAPI, Depends, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, Boolean,
    ForeignKey, Text, create_engine, inspect
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession
from jose import jwt, JWTError

# 1. НАСТРОЙКА БАЗЫ ДАННЫХ
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("FATAL: DATABASE_URL is not set.", flush=True)
    sys.exit(1)

Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False)
SessionFactory = sessionmaker(bind=engine)


# 2. МОДЕЛИ БАЗЫ ДАННЫХ (СХЕМА 2.0)
class Stage(Base):
    __tablename__ = "stages"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    order = Column(Integer, default=0)
    type = Column(String(50), default="regular")
    is_final = Column(Boolean, default=False)
    color = Column(String(20), default="#6B7280")
    deals = relationship("Deal", back_populates="stage")

class ServiceCategory(Base):
    __tablename__ = "service_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    icon = Column(String(50), default="🌿")
    services = relationship("Service", back_populates="category")

class Service(Base):
    __tablename__ = "services"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    category_id = Column(Integer, ForeignKey("service_categories.id"))
    unit = Column(String(50), default="ед")
    price = Column(Float, nullable=False)
    min_volume = Column(Float, default=1.0)
    category = relationship("ServiceCategory", back_populates="services")
    deal_services = relationship("DealService", back_populates="service")

class Contact(Base):
    __tablename__ = 'contacts'
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(50), unique=True, index=True, nullable=True)
    source = Column(String(100))
    deals = relationship("Deal", back_populates="contact")

class Deal(Base):
    __tablename__ = 'deals'
    id = Column(Integer, primary_key=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), nullable=False)
    stage_id = Column(Integer, ForeignKey("stages.id"))
    
    title = Column(String(200), nullable=False)
    total = Column(Float, default=0.0)
    address = Column(Text, default='')
    notes = Column(Text, default='')
    
    deal_date = Column(DateTime, default=datetime.utcnow)
    is_repeat = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    
    contact = relationship("Contact", back_populates="deals")
    stage = relationship("Stage", back_populates="deals")
    deal_services = relationship("DealService", back_populates="deal", cascade="all, delete-orphan")

class DealService(Base):
    __tablename__ = 'deal_services'
    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey('deals.id'), nullable=False)
    service_id = Column(Integer, ForeignKey('services.id'), nullable=False)
    quantity = Column(Float, nullable=False)
    price_at_moment = Column(Float, nullable=False)
    deal = relationship("Deal", back_populates="deal_services")
    service = relationship("Service", back_populates="deal_services")

class ExpenseCategory(Base):
    __tablename__ = 'expense_categories'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    expenses = relationship("Expense", back_populates="category")

class Equipment(Base):
    __tablename__ = 'equipment'
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    model = Column(String(200), default="")
    purchase_date = Column(Date)
    purchase_cost = Column(Float, default=0.0)
    status = Column(String(50), default='active')
    maintenances = relationship("Maintenance", back_populates="equipment")
    expenses = relationship("Expense", back_populates="equipment")

class Expense(Base):
    __tablename__ = 'expenses'
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, default=date.today)
    name = Column(String(300), nullable=False)
    amount = Column(Float, nullable=False)
    category_id = Column(Integer, ForeignKey('expense_categories.id'))
    equipment_id = Column(Integer, ForeignKey('equipment.id'), nullable=True)
    category = relationship("ExpenseCategory", back_populates="expenses")
    equipment = relationship("Equipment", back_populates="expenses")

class Maintenance(Base):
    __tablename__ = 'maintenances'
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, default=date.today)
    description = Column(String(500), nullable=False)
    cost = Column(Float, default=0.0)
    equipment_id = Column(Integer, ForeignKey('equipment.id'), nullable=False)
    equipment = relationship("Equipment", back_populates="maintenances")

class Consumable(Base):
    __tablename__ = 'consumables'
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    unit = Column(String(50), default='шт')
    stock_quantity = Column(Float, default=0.0)
    notes = Column(Text, default='')

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    role = Column(String(20), default="user")

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    is_done = Column(Boolean, default=False)
    due_date = Column(Date, nullable=True)

# 3. ИНИЦИАЛИЗАЦИЯ И НАЧАЛЬНЫЕ ДАННЫЕ
def init_and_seed_db():
    print("--- STARTING DB INIT (SCHEMA 2.0) ---", flush=True)
    try:
        print("!!! DROPPING ALL EXISTING TABLES !!!", flush=True)
        Base.metadata.drop_all(engine)
        
        print("Creating all new tables...", flush=True)
        Base.metadata.create_all(engine)
        print("Tables created successfully.", flush=True)

        with SessionFactory() as session:
            # Seed Stages
            if session.query(Stage).count() == 0:
                print("Seeding Stages...", flush=True)
                STAGES_DATA = [
                    {"name": "Согласовать", "order": 1, "color": "#3B82F6"},
                    {"name": "Ожидание", "order": 2, "color": "#F59E0B"},
                    {"name": "В работе", "order": 3, "color": "#EC4899"},
                    {"name": "Успешно", "order": 4, "type": "success", "is_final": True, "color": "#10B981"},
                    {"name": "Провалена", "order": 5, "type": "failed", "is_final": True, "color": "#EF4444"},
                ]
                for s_data in STAGES_DATA: session.add(Stage(**s_data))
                session.commit()

            # Seed Service Categories & Services
            if session.query(ServiceCategory).count() == 0:
                print("Seeding Service Categories & Services...", flush=True)
                cat1 = ServiceCategory(name="Покос травы", icon="🌿")
                cat2 = ServiceCategory(name="Уборка и вывоз", icon="🧹")
                session.add_all([cat1, cat2])
                session.flush()
                session.add_all([
                    Service(name="Покос травы (до 20 см)", category_id=cat1.id, unit="сотка", price=350, min_volume=3),
                    Service(name="Покос травы (20-40 см)", category_id=cat1.id, unit="сотка", price=450, min_volume=3),
                ])
                session.commit()

            # Seed Expense Categories
            if session.query(ExpenseCategory).count() == 0:
                print("Seeding Expense Categories...", flush=True)
                EXP_CATS = ["Техника", "Топливо", "Расходники", "Реклама", "Запчасти", "Прочее"]
                for name in EXP_CATS: session.add(ExpenseCategory(name=name))
                session.commit()
                
            print("--- DB SEEDING COMPLETE! ---", flush=True)

    except Exception as e:
        print(f"---!! ERROR DURING DB INIT: {e} !!----", flush=True)
        # sys.exit(1) # Don't exit on error, allow app to run and show logs
    finally:
        print("--- FINISHED DB INIT ---", flush=True)

# 4. АВТОРИЗАЦИЯ (Без изменений)
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-80umollds5sbkqku.us.auth0.com")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://grass-crm/api")
ROLE_CLAIM = "https://grass-crm/role"
bearer = HTTPBearer(auto_error=False)

@lru_cache(maxsize=1)
def _fetch_jwks() -> dict:
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    try:
        resp = httpx.get(url, timeout=10); resp.raise_for_status(); return resp.json()
    except httpx.HTTPError as e: raise RuntimeError(f"Failed to fetch JWKS from Auth0: {e}") from e

def get_current_user(token: Optional[str] = Depends(bearer)) -> dict:
    if not token: raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Auth required.")
    unverified_header = jwt.get_unverified_header(token.credentials)
    kid = unverified_header.get("kid")
    jwks = _fetch_jwks()
    key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
    if not key: raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signing key not found.")
    try:
        payload = jwt.decode(token.credentials, key, algorithms=["RS256"], audience=AUTH0_AUDIENCE, issuer=f"https://{AUTH0_DOMAIN}/")
        return {"username": payload.get("sub", ""), "role": payload.get(ROLE_CLAIM, "user")}
    except JWTError as exc: raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}")


# 5. FASTAPI ПРИЛОЖЕНИЕ
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("App is starting...", flush=True)
    init_and_seed_db()
    yield
    print("App is shutting down...", flush=True)

app = FastAPI(title="GreenCRM API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionFactory();
    try: yield db
    finally: db.close()

# --- API Endpoints ---
@app.get("/api/me", tags=["Users"])
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

@app.get("/api/stages", tags=["Deals"])
def get_stages(db: DBSession = Depends(get_db)):
    return db.query(Stage).order_by(Stage.order).all()

@app.get("/api/deals", tags=["Deals"])
def get_deals(db: DBSession = Depends(get_db)):
    deals = db.query(Deal).join(Contact).join(Stage).order_by(Deal.created_at.desc()).all()
    return [{
        "id": d.id, "title": d.title, "total": d.total,
        "client": d.contact.name, "stage": d.stage.name,
        "created_at": d.created_at.isoformat()
    } for d in deals]

@app.get("/api/tasks", tags=["Tasks"])
def get_tasks(db: DBSession = Depends(get_db)):
    return db.query(Task).order_by(Task.due_date.desc()).all()

@app.get("/api/expenses", tags=["Finances"])
def get_expenses(db: DBSession = Depends(get_db)):
    expenses = db.query(Expense).join(ExpenseCategory).order_by(Expense.date.desc()).all()
    return [{"id": e.id, "date": e.date, "name": e.name, "amount": e.amount, "category": e.category.name} for e in expenses]

@app.get("/api/equipment", tags=["Operations"])
def get_equipment(db: DBSession = Depends(get_db)):
    return db.query(Equipment).order_by(Equipment.name).all()

@app.get("/api/maintenances", tags=["Operations"])
def get_maintenances(db: DBSession = Depends(get_db)):
    return db.query(Maintenance).order_by(Maintenance.date.desc()).all()

@app.get("/api/consumables", tags=["Operations"])
def get_consumables(db: DBSession = Depends(get_db)):
    return db.query(Consumable).order_by(Consumable.name).all()
    
@app.get("/api/contacts", tags=["Contacts"])
def get_contacts(db: DBSession = Depends(get_db)):
    return db.query(Contact).order_by(Contact.name).all()

@app.get("/api/services", tags=["Deals"])
def get_services(db: DBSession = Depends(get_db)):
    services = db.query(Service).join(ServiceCategory).order_by(ServiceCategory.name, Service.name).all()
    return [{"id": s.id, "name": s.name, "category": s.category.name, "unit": s.unit, "price": s.price} for s in services]

# --- Frontend Serving ---
@app.get("/{full_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend(full_path: str):
    path = f"./{full_path if full_path else 'index.html'}"
    if os.path.exists(path):
        return path
    return "./index.html"

print("main.py loaded successfully.", flush=True)

