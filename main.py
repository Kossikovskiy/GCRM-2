
from dotenv import load_dotenv
load_dotenv()

import sys
import os
import urllib.request
import urllib.parse
import httpx
import secrets
from urllib.parse import urlencode
import io
import csv
from functools import lru_cache
from datetime import datetime, date, timedelta
from contextlib import asynccontextmanager
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, Boolean,
    ForeignKey, Text, create_engine, inspect, text, extract, func
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession
from jose import jwt, JWTError

# --------------------------------------------------------------------------
# 1. НАСТРОЙКА БАЗЫ ДАННЫХ
# --------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("Ошибка: Переменная окружения DATABASE_URL не установлена.")
    print("Приложение не может запуститься без подключения к базе данных PostgreSQL.")
    sys.exit(1)

Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False)
SessionFactory = sessionmaker(bind=engine)


# --------------------------------------------------------------------------
# 2. МОДЕЛИ БАЗЫ ДАННЫХ
# --------------------------------------------------------------------------
class Stage(Base):
    __tablename__ = "stages"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    order = Column(Integer, default=0)
    type = Column(String(50), default="regular") # regular, success, failed
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

class Deal(Base):
    __tablename__ = "deals"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    client = Column(String(200), nullable=False)
    stage_id = Column(Integer, ForeignKey("stages.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    
    address = Column(String(300), default="")
    notes = Column(Text, default="")
    total = Column(Float, default=0.0)
    vat_rate = Column(String(20), default="no_vat")

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

class Equipment(Base):
    __tablename__ = "equipment"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    model = Column(String(200), default="")
    purchase_date = Column(Date, nullable=True)
    purchase_cost = Column(Float, default=0.0)
    status = Column(String(50), default="active") # active, repair, retired

class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    name = Column(String(300), nullable=False)
    category_id = Column(Integer, ForeignKey("expense_categories.id"))
    amount = Column(Float, nullable=False)
    year = Column(Integer, nullable=False)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(120), unique=True, nullable=True)
    full_name = Column(String(100), default="")
    role = Column(String(20), default="user") # user, admin
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, default='')
    due_date = Column(Date)
    is_done = Column(Boolean, default=False)
    status = Column(String(50), default='open') # open, in_progress, done, canceled
    priority = Column(String(50), default='normal') # low, normal, high
    manager = Column(String(100), default='')


# --------------------------------------------------------------------------
# 3. ИНИЦИАЛИЗАЦИЯ И НАЧАЛЬНЫЕ ДАННЫЕ
# --------------------------------------------------------------------------
STAGES_DATA = [
    {"name": "Согласовать", "order": 1, "type": "regular", "is_final": False, "color": "#3B82F6"},
    {"name": "Ожидание", "order": 2, "type": "regular", "is_final": False, "color": "#F59E0B"},
    {"name": "Запланировано", "order": 3, "type": "regular", "is_final": False, "color": "#8B5CF6"},
    {"name": "В работе", "order": 4, "type": "regular", "is_final": False, "color": "#EC4899"},
    {"name": "Успешно", "order": 5, "type": "success", "is_final": True, "color": "#10B981"},
    {"name": "Провалена", "order": 6, "type": "failed", "is_final": True, "color": "#EF4444"},
]
SERVICE_CATEGORIES_DATA = [
    {"name": "Покос травы", "icon": "🌿"},
    {"name": "Уборка и вывоз", "icon": "🧹"},
]
SERVICES_DATA = [
    ("Покос травы (до 20 см)", "Покос травы", "сотка", 350, 3),
    ("Покос травы (20-40 см)", "Покос травы", "сотка", 450, 3),
]
EQUIPMENT_DATA = [
    {"name": "Газонокосилка #1", "model": "Nocord", "purchase_date": "2024-06-01", "purchase_cost": 26481, "status": "active"},
]
EXPENSE_CATEGORIES_DATA = ["Техника", "Топливо"]

def init_and_seed_db():
    print("--- STARTING DB INIT ---", flush=True)
    try:
        print("Creating all tables (if they don't exist)...", flush=True)
        Base.metadata.create_all(engine)
        print("Tables creation command finished.", flush=True)

        with SessionFactory() as session:
            if session.query(Stage).count() == 0:
                print("Seeding Stages...", flush=True)
                for s_data in STAGES_DATA:
                    session.add(Stage(**s_data))
                session.commit()

            if session.query(ServiceCategory).count() == 0:
                print("Seeding Service Categories...", flush=True)
                for sc_data in SERVICE_CATEGORIES_DATA:
                    session.add(ServiceCategory(**sc_data))
                session.commit()

            if session.query(Service).count() == 0:
                print("Seeding Services...", flush=True)
                for name, cat_name, unit, price, min_vol in SERVICES_DATA:
                    cat = session.query(ServiceCategory).filter_by(name=cat_name).first()
                    if cat:
                        session.add(Service(name=name, category_id=cat.id, unit=unit, price=price, min_volume=min_vol))
                session.commit()

            if session.query(Equipment).count() == 0:
                print("Seeding Equipment...", flush=True)
                for eq_data in EQUIPMENT_DATA:
                    eq_data_copy = eq_data.copy()
                    eq_data_copy["purchase_date"] = datetime.strptime(eq_data_copy["purchase_date"], "%Y-%m-%d").date()
                    session.add(Equipment(**eq_data_copy))
                session.commit()
            
            if session.query(ExpenseCategory).count() == 0:
                print("Seeding Expense Categories...", flush=True)
                for name in EXPENSE_CATEGORIES_DATA:
                    session.add(ExpenseCategory(name=name))
                session.commit()
                
            print("--- DB SEEDING COMPLETE! ---", flush=True)

    except Exception as e:
        print(f"---!! ERROR DURING DB INIT: {e} !!----", flush=True)
    finally:
        print("--- FINISHED DB INIT ---", flush=True)

# --------------------------------------------------------------------------
# 4. АВТОРИЗАЦИЯ
# --------------------------------------------------------------------------
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-80umollds5sbkqku.us.auth0.com")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://grass-crm/api")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "tWfznxnflmcDEitZfkzlesHJ9YjZAZkN")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET")
AUTH0_CALLBACK_URL = os.getenv("AUTH0_CALLBACK_URL", "https://crmpokos.ru/api/auth/callback")

ROLE_CLAIM = "https://grass-crm/role"
bearer = HTTPBearer(auto_error=False)

@lru_cache(maxsize=1)
def _fetch_jwks() -> dict:
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Не удалось загрузить JWKS с Auth0: {e}") from e

def _get_signing_key(token: str) -> dict:
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")
    for attempt in range(2):
        jwks = _fetch_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key
        if attempt == 0:
            _fetch_jwks.cache_clear()
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Подходящий ключ не найден.")

def verify_token(token: str) -> dict:
    signing_key = _get_signing_key(token)
    try:
        return jwt.decode(token, signing_key, algorithms=["RS256"], audience=AUTH0_AUDIENCE, issuer=f"https://{AUTH0_DOMAIN}/")
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Токен недействителен: {exc}")

def get_current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> dict:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Необходима авторизация.")
    payload = verify_token(creds.credentials)
    return {"username": payload.get("sub", ""), "email": payload.get("email", ""), "role": payload.get(ROLE_CLAIM, "user")}

def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Требуется роль admin.")
    return current_user


# --------------------------------------------------------------------------
# 5. FASTAPI ПРИЛОЖЕНИЕ
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Приложение запускается...", flush=True)
    init_and_seed_db()
    yield
    print("Приложение останавливается...", flush=True)

app = FastAPI(title="Grass CRM API", version="2.3.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", secrets.token_hex(32)))
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

HTML_PATH = "./index.html"
@app.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_frontend():
    if os.path.exists(HTML_PATH):
        return HTML_PATH
    return "Frontend file not found."

@app.get("/health", status_code=status.HTTP_200_OK, tags=["System"])
async def health_check():
    return {"status": "ok"}
    
def get_db():
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()

# --- Auth0 Endpoints ---
@app.get("/api/auth/login")
async def login(request: Request):
    return RedirectResponse(f"https://{AUTH0_DOMAIN}/authorize?" + urlencode({
        "response_type": "code", "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": AUTH0_CALLBACK_URL, "scope": "openid profile email",
        "audience": AUTH0_AUDIENCE,
    }))

@app.get("/api/auth/callback")
async def callback(request: Request, code: str = None, error: str = None, error_description: str = None):
    if not AUTH0_CLIENT_SECRET:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Секретный ключ Auth0 не настроен.")
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{error}: {error_description}")
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing authorization code")

    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"
    payload = {
        "grant_type": "authorization_code", "client_id": AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET, "code": code, "redirect_uri": AUTH0_CALLBACK_URL,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(token_url, data=payload)
            response.raise_for_status()
            token_data = response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Error from Auth0: {e.response.json().get('error_description', e.response.text)}")
    
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Could not get access token from Auth0.")
        
    return RedirectResponse(url=f"/?access_token={access_token}")

@app.get("/api/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(f"https://{AUTH0_DOMAIN}/v2/logout?" + urlencode({
        "returnTo": str(request.base_url), "client_id": AUTH0_CLIENT_ID,
    }, quote_via=urllib.parse.quote))


# --- Pydantic Schemas ---
class DealCreateOrUpdate(BaseModel):
    title: str
    client: str
    notes: Optional[str] = ""
    address: Optional[str] = ""
    vat_rate: Optional[str] = "no_vat"
    services: Optional[str] = "" 
    total: Optional[float] = 0

class StageUpdate(BaseModel):
    stage_name: str

class ExpenseCreate(BaseModel):
    date: str
    name: str
    category: str
    amount: float

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ''
    due_date: Optional[date] = None
    manager: Optional[str] = ''
    priority: Optional[str] = 'normal'
    status: Optional[str] = 'open'

# --- API Endpoints ---
@app.get("/api/me", tags=["Users"])
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

@app.get("/api/stages", tags=["Deals"])
def get_stages(db: DBSession = Depends(get_db)):
    return db.query(Stage).order_by(Stage.order).all()

@app.get("/api/deals", tags=["Deals"])
def get_deals(db: DBSession = Depends(get_db)):
    deals_data = db.query(Deal).order_by(Deal.created_at.desc()).all()
    
    deals_list = []
    for d in deals_data:
        services_str = ",".join([f"{ds.service.name}:{ds.quantity}" for ds in d.deal_services])
        deals_list.append({
            "id": d.id,
            "title": d.title,
            "client": d.client,
            "stage": d.stage.name if d.stage else None,
            "address": d.address,
            "notes": d.notes,
            "total": d.total,
            "vat_rate": d.vat_rate,
            "services": services_str,
            "created_at": d.created_at.isoformat()
        })
    return {"deals": deals_list}

@app.post("/api/deals", status_code=201, tags=["Deals"])
def create_deal(body: DealCreateOrUpdate, db: DBSession = Depends(get_db)):
    first_stage = db.query(Stage).order_by(Stage.order).first()
    if not first_stage:
        raise HTTPException(status_code=500, detail="В системе нет ни одной стадии для создания сделки.")

    deal = Deal(
        title=body.title, client=body.client, stage_id=first_stage.id,
        address=body.address, notes=body.notes, vat_rate=body.vat_rate
    )
    db.add(deal)
    db.flush()

    calculated_total = 0.0
    if body.services:
        for pair in body.services.split(','):
            parts = pair.split(':')
            if len(parts) != 2: continue
            
            service_name, quantity_str = parts[0].strip(), parts[1].strip()
            service = db.query(Service).filter(Service.name == service_name).first()
            if service and quantity_str:
                quantity = float(quantity_str)
                db.add(DealService(
                    deal_id=deal.id, service_id=service.id,
                    quantity=quantity, price_at_moment=service.price
                ))
                calculated_total += quantity * service.price

    deal.total = calculated_total
    db.commit()
    db.refresh(deal)

    return {
        "id": deal.id, "stage": first_stage.name, "title": deal.title,
        "client": deal.client, "total": deal.total
    }

@app.patch("/api/deals/{deal_id}/stage", status_code=200, tags=["Deals"])
def update_deal_stage(deal_id: int, body: StageUpdate, db: DBSession = Depends(get_db)):
    deal = db.query(Deal).get(deal_id)
    if not deal:
        raise HTTPException(404, "Сделка не найдена")
    
    stage = db.query(Stage).filter(Stage.name == body.stage_name).first()
    if not stage:
        raise HTTPException(404, f"Стадия '{body.stage_name}' не найдена")
        
    deal.stage_id = stage.id
    deal.updated_at = datetime.utcnow()
    if stage.is_final and not deal.closed_at:
        deal.closed_at = datetime.utcnow()
        
    db.commit()
    return {"id": deal.id, "new_stage": stage.name}

@app.get("/api/tasks", tags=["Tasks"])
def get_tasks(db: DBSession = Depends(get_db)):
    tasks = db.query(Task).order_by(Task.due_date.desc()).all()
    return {"tasks": tasks}

# Fallback for other endpoints to avoid crashes, returning empty data
@app.get("/api/expenses")
async def get_expenses_mock(): return {"expenses": []}

@app.get("/api/equipment")
async def get_equipment_mock(): return []

@app.get("/api/maintenances")
async def get_maintenances_mock(): return []

@app.get("/api/consumables")
async def get_consumables_mock(): return []

@app.get("/api/services")
def get_services(db: DBSession = Depends(get_db)):
    services = db.query(Service).join(ServiceCategory).order_by(ServiceCategory.name, Service.name).all()
    return [{
        "id": s.id, "name": s.name, "category": s.category.name,
        "unit": s.unit, "price": s.price, "min_volume": s.min_volume
    } for s in services]

print("Главный модуль main.py успешно загружен и готов к работе с PostgreSQL.")
