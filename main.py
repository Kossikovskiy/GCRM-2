from dotenv import load_dotenv
load_dotenv()

import os
import secrets
from datetime import datetime, date
from contextlib import asynccontextmanager
from typing import Optional
from functools import lru_cache, wraps

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date,
    DateTime, Boolean, ForeignKey, Text, text, MetaData, extract
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession
import httpx
from jose import jwt, JWTError
from cachetools import TTLCache
from cachetools.keys import hashkey

# ── 0. КЭШ ───────────────────────────────────────────────────────────────────
cache = TTLCache(maxsize=256, ttl=600)

def cache_key_generator(*args, **kwargs):
    # Игнорируем db и user в ключе кэша, они меняются при каждом запросе
    # Мы заботимся только о фактических параметрах, таких как 'year'
    key_kwargs = {k: v for k, v in kwargs.items() if k not in ('db', '_')}
    return hashkey(*args, **key_kwargs)

# ── 1. КОНФИГ ─────────────────────────────────────────────────────────────────
DATABASE_URL   = os.getenv("DATABASE_URL")
AUTH0_DOMAIN   = os.getenv("AUTH0_DOMAIN")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE")
CLIENT_ID      = os.getenv("AUTH0_CLIENT_ID")
CLIENT_SECRET  = os.getenv("AUTH0_CLIENT_SECRET")
APP_BASE_URL   = os.getenv("APP_BASE_URL", "https://crmpokos.ru").rstrip("/")
CALLBACK_URL   = f"{APP_BASE_URL}/api/auth/callback"
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_hex(32))
ROLE_CLAIM     = "https://grass-crm/role"


# ── 2. БАЗА ДАННЫХ ────────────────────────────────────────────────────────────
Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(bind=engine)


class Stage(Base):
    __tablename__ = "stages"
    id       = Column(Integer, primary_key=True)
    name     = Column(String(100), nullable=False, unique=True)
    order    = Column(Integer, default=0)
    type     = Column(String(50), default="regular")
    is_final = Column(Boolean, default=False)
    color    = Column(String(20), default="#6B7280")
    deals    = relationship("Deal", back_populates="stage")

class Contact(Base):
    __tablename__ = "contacts"
    id     = Column(Integer, primary_key=True)
    name   = Column(String(200), nullable=False)
    phone  = Column(String(50), unique=True, index=True, nullable=True)
    source = Column(String(100), nullable=True)
    deals  = relationship("Deal", back_populates="contact")

class Deal(Base):
    __tablename__ = "deals"
    id         = Column(Integer, primary_key=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True)
    stage_id   = Column(Integer, ForeignKey("stages.id"),   nullable=True)
    title      = Column(String(200), nullable=False)
    total      = Column(Float, default=0.0)
    notes      = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    deal_date  = Column(DateTime, nullable=True)
    closed_at  = Column(DateTime, nullable=True)
    is_repeat  = Column(Boolean, default=False)
    manager    = Column(String(200), nullable=True)
    address    = Column(Text, nullable=True)

    contact    = relationship("Contact", back_populates="deals")
    stage      = relationship("Stage",   back_populates="deals")

class Task(Base):
    __tablename__ = "tasks"
    id       = Column(Integer, primary_key=True)
    title    = Column(String, nullable=False)
    is_done  = Column(Boolean, default=False)
    due_date = Column(Date, nullable=True)

class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id       = Column(Integer, primary_key=True)
    name     = Column(String(100), nullable=False, unique=True)
    expenses = relationship("Expense", back_populates="category")

class Equipment(Base):
    __tablename__ = "equipment"
    id            = Column(Integer, primary_key=True)
    name          = Column(String(200), nullable=False)
    model         = Column(String(200), default="")
    serial        = Column(String(100), nullable=True)
    purchase_date = Column(Date, nullable=True)
    purchase_cost = Column(Float, default=0.0)
    engine_hours  = Column(Float, default=0.0)
    status        = Column(String(50), default="active")
    notes         = Column(Text, nullable=True)
    expenses      = relationship("Expense", back_populates="equipment")

class Expense(Base):
    __tablename__ = "expenses"
    id           = Column(Integer, primary_key=True)
    date         = Column(Date, nullable=False, default=date.today)
    name         = Column(String(300), nullable=False)
    amount       = Column(Float, nullable=False)
    category_id  = Column(Integer, ForeignKey("expense_categories.id"), nullable=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"),          nullable=True)
    category     = relationship("ExpenseCategory", back_populates="expenses")
    equipment    = relationship("Equipment",        back_populates="expenses")

class Consumable(Base):
    __tablename__ = "consumables"
    id             = Column(Integer, primary_key=True)
    name           = Column(String(200), nullable=False, unique=True)
    unit           = Column(String(50), default="шт")
    stock_quantity = Column(Float, default=0.0)
    notes          = Column(Text, nullable=True)


# ── 3. ИНИЦИАЛИЗАЦИЯ БД ───────────────────────────────────────────────────────
def init_and_seed_db():
    try:
        Base.metadata.create_all(engine)
        with SessionFactory() as s:
            if s.query(Stage).count() == 0:
                # ... seeding logic
                pass
            if s.query(ExpenseCategory).count() == 0:
                # ... seeding logic
                pass
    except Exception:
        pass


# ── 4. АВТОРИЗАЦИЯ ────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_jwks() -> dict:
    # ... implementation
    pass

def decode_access_token(token: str) -> dict:
    # ... implementation
    pass

def get_current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user

# ── 5. ПРИЛОЖЕНИЕ ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("App starting (v4.0 - fixed caching)...", flush=True)
    init_and_seed_db()
    yield
    print("App shutting down.", flush=True)

app = FastAPI(title="GreenCRM API", version="4.0.0", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False, same_site="lax")
app.add_middleware(CORSMiddleware, allow_origins=[APP_BASE_URL], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionFactory()
    try:    yield db
    finally: db.close()

# Декоратор для кэширования
def cached(cache, key=cache_key_generator):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            k = key(*args, **kwargs)
            try:
                return cache[k]
            except KeyError:
                pass  # Fall through

            res = func(*args, **kwargs)
            cache[k] = res
            return res
        return wrapper
    return decorator

# ── 6. AUTH ЭНДПОИНТЫ ─────────────────────────────────────────────────────────
# ... auth endpoints

# ── 7. DATA ЭНДПОИНТЫ ─────────────────────────────────────────────────────────

@app.get("/api/years")
@cached(cache)
def get_years(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    # ... implementation
    pass

@app.get("/api/me")
def get_me(user: dict = Depends(get_current_user)):
    return {"username": user["sub"], "role": user["role"]}

@app.get("/api/stages")
@cached(cache)
def get_stages(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    # ... implementation
    pass

@app.get("/api/deals")
@cached(cache, key=cache_key_generator)
def get_deals(db: DBSession = Depends(get_db), year: Optional[int] = None, _=Depends(get_current_user)):
    # ... implementation
    pass

@app.get("/api/tasks")
@cached(cache, key=cache_key_generator)
def get_tasks(db: DBSession = Depends(get_db), year: Optional[int] = None, _=Depends(get_current_user)):
    # ... implementation
    pass

@app.get("/api/expenses")
@cached(cache, key=cache_key_generator)
def get_expenses(db: DBSession = Depends(get_db), year: Optional[int] = None, _=Depends(get_current_user)):
    # ... implementation
    pass

@app.get("/api/equipment")
@cached(cache)
def get_equipment(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    # ... implementation
    pass

@app.get("/api/services")
@cached(cache)
def get_services(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    # ... implementation
    pass

@app.get("/api/consumables")
@cached(cache)
def get_consumables(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    # ... implementation
    pass

@app.get("/api/contacts")
@cached(cache)
def get_contacts(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    # ... implementation
    pass

# ── 8. ФРОНТЕНД ───────────────────────────────────────────────────────────────
@app.get("/{full_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend(full_path: str):
    path = f"./{full_path}" if full_path else "./index.html"
    if os.path.exists(path) and os.path.isfile(path):
        return FileResponse(path)
    return FileResponse("./index.html")
