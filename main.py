
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
# 1. МОДЕЛИ БАЗЫ ДАННЫХ (объединенные)
# --------------------------------------------------------------------------
Base = declarative_base()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./crm.db")

class Stage(Base):
    __tablename__ = "stages"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    order = Column(Integer, default=0)
    type = Column(String(50), default="regular")
    is_final = Column(Boolean, default=False)
    color = Column(String(20), default="#6B7280")
    deals = relationship("Deal", back_populates="stage")

class ServiceCategory(Base):
    __tablename__ = "service_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    icon = Column(String(50), default="🌿")
    services = relationship("Service", back_populates="category")

class Service(Base):
    __tablename__ = "services"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    category_id = Column(Integer, ForeignKey("service_categories.id"))
    unit = Column(String(50), default="ед")
    price = Column(Float, nullable=False)
    min_volume = Column(Float, default=1.0)
    category = relationship("ServiceCategory", back_populates="services")

class Deal(Base):
    __tablename__ = "deals"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    client = Column(String(200), nullable=False)
    stage_id = Column(Integer, ForeignKey("stages.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    stage = relationship("Stage", back_populates="deals")

class Equipment(Base):
    __tablename__ = "equipment"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    model = Column(String(200), default="")
    purchase_date = Column(Date, nullable=True)
    purchase_cost = Column(Float, default=0.0)
    status = Column(String(50), default="active")

class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)

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
    role = Column(String(20), default="user")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

def get_engine(url: str = DATABASE_URL):
    return create_engine(url, echo=False)

def get_session_factory(engine):
    return sessionmaker(bind=engine)

def init_db(engine):
    Base.metadata.create_all(engine)

# --------------------------------------------------------------------------
# 2. НАЧАЛЬНЫЕ ДАННЫЕ И ИНИЦИАЛИЗАЦИЯ
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

def seed_database(session):
    if session.query(Stage).count() == 0:
        for s_data in STAGES_DATA:
            session.add(Stage(**s_data))
    if session.query(ServiceCategory).count() == 0:
        for sc_data in SERVICE_CATEGORIES_DATA:
            session.add(ServiceCategory(**sc_data))
    session.commit()
    if session.query(Service).count() == 0:
        for name, cat_name, unit, price, min_vol in SERVICES_DATA:
            cat = session.query(ServiceCategory).filter_by(name=cat_name).first()
            if cat:
                session.add(Service(name=name, category_id=cat.id, unit=unit, price=price, min_volume=min_vol))
    if session.query(Equipment).count() == 0:
        for eq_data in EQUIPMENT_DATA:
            eq_data["purchase_date"] = datetime.strptime(eq_data["purchase_date"], "%Y-%m-%d").date()
            session.add(Equipment(**eq_data))
    if session.query(ExpenseCategory).count() == 0:
        for name in EXPENSE_CATEGORIES_DATA:
            session.add(ExpenseCategory(name=name))
    session.commit()
    print("🎉 База данных успешно инициализирована!")

DB_FILE_PATH = "./crm.db"
if not os.path.exists(DB_FILE_PATH):
    print("База данных не найдена. Создаю новую...")
    try:
        engine = get_engine()
        init_db(engine)
        Session = get_session_factory(engine)
        with Session() as session:
            seed_database(session)
    except Exception as e:
        print(f"ОШИБКА при инициализации БД: {e}")
        sys.exit(1)

engine = get_engine()
SessionFactory = get_session_factory(engine)

# --------------------------------------------------------------------------
# 3. АВТОРИЗАЦИЯ
# --------------------------------------------------------------------------
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-80umollds5sbkqku.us.auth0.com")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://grass-crm/api")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "tWfznxnflmcDEitZfkzlesHJ9YjZAZkN")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
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
# 4. FASTAPI ПРИЛОЖЕНИЕ
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Приложение запускается...")
    yield
    print("Приложение останавливается...")

app = FastAPI(title="Grass CRM API", version="2.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "a-very-secret-key"))
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

HTML_PATH = "./index.html"
@app.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_frontend():
    if os.path.exists(HTML_PATH):
        return HTML_PATH
    return "Frontend file not found."

def get_db():
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()


@app.get("/api/auth/login")
async def login(request: Request):
    redirect_uri = AUTH0_CALLBACK_URL
    return RedirectResponse(
        f"https://{AUTH0_DOMAIN}/authorize?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": AUTH0_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "scope": "openid profile email",
                "audience": AUTH0_AUDIENCE,
            }
        )
    )


@app.get("/api/auth/callback")
async def callback(request: Request, code: str = None, error: str = None, error_description: str = None):
    if AUTH0_CLIENT_SECRET == "YOUR_CLIENT_SECRET" or not AUTH0_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Секретный ключ Auth0 не настроен на сервере. Установите переменную окружения AUTH0_CLIENT_SECRET."
        )
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{error}: {error_description}")
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing authorization code")

    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET,
        "code": code,
        "redirect_uri": AUTH0_CALLBACK_URL,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(token_url, data=payload)
            response.raise_for_status()
            token_data = response.json()
        except httpx.HTTPStatusError as e:
            err_data = e.response.json()
            raise HTTPException(status_code=e.response.status_code, detail=f"Error from Auth0: {err_data.get('error_description', e.response.text)}")
    
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Could not get access token from Auth0.")
        
    return RedirectResponse(url=f"/?access_token={access_token}")


@app.get("/api/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(
        f"https://{AUTH0_DOMAIN}/v2/logout?"
        + urlencode(
            {
                "returnTo": str(request.base_url),
                "client_id": AUTH0_CLIENT_ID,
            },
            quote_via=urllib.parse.quote,
        )
    )


# --- Pydantic Schemas ---
class DealCreate(BaseModel):
    title: str
    client: str

# --- Endpoints ---
@app.get("/api/deals")
def get_deals(db: DBSession = Depends(get_db)):
    deals = db.query(Deal).order_by(Deal.created_at.desc()).all()
    return [{ "id": d.id, "title": d.title, "client": d.client, "stage": d.stage.name if d.stage else None } for d in deals]

@app.post("/api/deals", status_code=201)
def create_deal(body: DealCreate, db: DBSession = Depends(get_db)):
    stage = db.query(Stage).order_by(Stage.order).first()
    deal = Deal(title=body.title, client=body.client, stage_id=stage.id if stage else None)
    db.add(deal)
    db.commit()
    return {"id": deal.id}

@app.get("/api/stages")
def get_stages(db: DBSession = Depends(get_db)):
    return db.query(Stage).order_by(Stage.order).all()

@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

print("Главный модуль main.py успешно загружен.")
