from dotenv import load_dotenv
load_dotenv()

import sys
import os
from datetime import datetime, date
from contextlib import asynccontextmanager
from typing import Optional, List
from functools import lru_cache

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date,
    DateTime, Boolean, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession
from fastapi.security import HTTPBearer
from jose import jwt, JWTError
import httpx

# 1. DATABASE SETUP
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("FATAL: DATABASE_URL is not set.")

Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(bind=engine)


# 2. DATABASE MODELS
class Stage(Base):
    __tablename__ = "stages"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    order = Column(Integer, default=0)
    type = Column(String(50), default="regular")   # "success" / "failed" / "regular"
    is_final = Column(Boolean, default=False)
    color = Column(String(20), default="#6B7280")
    deals = relationship("Deal", back_populates="stage")

class Contact(Base):
    __tablename__ = 'contacts'
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(50), unique=True, index=True, nullable=True)
    source = Column(String(100), nullable=True)
    deals = relationship("Deal", back_populates="contact")

class Deal(Base):
    __tablename__ = 'deals'
    id = Column(Integer, primary_key=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), nullable=True)
    stage_id = Column(Integer, ForeignKey("stages.id"), nullable=True)
    title = Column(String(200), nullable=False)
    total = Column(Float, default=0.0)
    notes = Column(Text, default='')
    created_at = Column(DateTime, default=datetime.utcnow)
    deal_date = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    is_repeat = Column(Boolean, default=False)

    contact = relationship("Contact", back_populates="deals")
    stage = relationship("Stage", back_populates="deals")

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    is_done = Column(Boolean, default=False)
    due_date = Column(Date, nullable=True)

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
    serial = Column(String(100), nullable=True)
    purchase_date = Column(Date, nullable=True)
    purchase_cost = Column(Float, default=0.0)
    engine_hours = Column(Float, default=0.0)
    status = Column(String(50), default='active')  # active / repair / retired
    notes = Column(Text, nullable=True)
    expenses = relationship("Expense", back_populates="equipment")

class Expense(Base):
    __tablename__ = 'expenses'
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, default=date.today)
    name = Column(String(300), nullable=False)
    amount = Column(Float, nullable=False)
    category_id = Column(Integer, ForeignKey('expense_categories.id'), nullable=True)
    equipment_id = Column(Integer, ForeignKey('equipment.id'), nullable=True)
    category = relationship("ExpenseCategory", back_populates="expenses")
    equipment = relationship("Equipment", back_populates="expenses")

class Consumable(Base):
    __tablename__ = 'consumables'
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    unit = Column(String(50), default='шт')
    stock_quantity = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)


# 3. DB INITIALIZATION & SEEDING
def init_and_seed_db():
    print("--- DB INIT START ---", flush=True)
    try:
        Base.metadata.create_all(engine)
        print("Tables created/verified OK", flush=True)

        with SessionFactory() as session:
            if session.query(Stage).count() == 0:
                print("Seeding stages...", flush=True)
                STAGES_DATA = [
                    {"name": "Согласовать", "order": 1, "color": "#3B82F6", "type": "regular"},
                    {"name": "Ожидание",    "order": 2, "color": "#F59E0B", "type": "regular"},
                    {"name": "В работе",    "order": 3, "color": "#EC4899", "type": "regular"},
                    {"name": "Успешно",     "order": 4, "color": "#10B981", "type": "success", "is_final": True},
                    {"name": "Провалена",   "order": 5, "color": "#EF4444", "type": "failed",  "is_final": True},
                ]
                for s in STAGES_DATA:
                    session.add(Stage(**s))
                session.commit()

            if session.query(ExpenseCategory).count() == 0:
                print("Seeding expense categories...", flush=True)
                for name in ["Техника", "Топливо", "Расходники", "Реклама", "Запчасти", "Прочее"]:
                    session.add(ExpenseCategory(name=name))
                session.commit()

        print("--- DB INIT DONE ---", flush=True)
    except Exception as e:
        print(f"---!! ERROR DURING DB INIT: {e} !!---", flush=True)


# 4. AUTHENTICATION
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE")
CLIENT_ID = os.getenv("AUTH0_CLIENT_ID")
# Используем APP_BASE_URL если AUTH0_CALLBACK_URL не задан явно
AUTH0_CALLBACK_URL = os.getenv("AUTH0_CALLBACK_URL") or (os.getenv("APP_BASE_URL", "").rstrip("/") + "/")

missing = [k for k, v in {
    "AUTH0_DOMAIN": AUTH0_DOMAIN,
    "AUTH0_AUDIENCE": AUTH0_AUDIENCE,
    "AUTH0_CLIENT_ID": CLIENT_ID,
}.items() if not v]

if missing:
    raise RuntimeError(f"FATAL: Missing env vars: {', '.join(missing)}")

bearer = HTTPBearer(auto_error=False)

@lru_cache(maxsize=1)
def get_jwks() -> dict:
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    with httpx.Client() as client:
        resp = client.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()

def get_current_user(token: Optional[str] = Depends(bearer)) -> dict:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Auth required.")
    try:
        header = jwt.get_unverified_header(token.credentials)
        jwks = get_jwks()
        key = next((k for k in jwks["keys"] if k["kid"] == header.get("kid")), None)
        if not key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signing key not found.")
        payload = jwt.decode(
            token.credentials, key, algorithms=["RS256"],
            audience=AUTH0_AUDIENCE, issuer=f"https://{AUTH0_DOMAIN}/"
        )
        return {"username": payload.get("sub", ""), "role": payload.get("https://grass-crm/role", "user")}
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}")


# 5. FASTAPI APPLICATION
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("App starting (v3.3)...", flush=True)
    init_and_seed_db()
    yield
    print("App shutting down.", flush=True)

app = FastAPI(title="GreenCRM API", version="3.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()


# 6. API ENDPOINTS

@app.get("/api/auth/login")
def login_redirect():
    """Редирект на Auth0 для входа."""
    auth_url = (
        f"https://{AUTH0_DOMAIN}/authorize"
        f"?response_type=token"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={AUTH0_CALLBACK_URL}"
        f"&scope=openid%20profile%20email"
        f"&audience={AUTH0_AUDIENCE}"
    )
    return RedirectResponse(url=auth_url)

@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

@app.get("/api/stages")
def get_stages(db: DBSession = Depends(get_db)):
    stages = db.query(Stage).order_by(Stage.order).all()
    return [
        {"id": s.id, "name": s.name, "order": s.order,
         "type": s.type, "is_final": s.is_final, "color": s.color}
        for s in stages
    ]

@app.get("/api/deals")
def get_deals(db: DBSession = Depends(get_db)):
    deals = (
        db.query(Deal)
        .outerjoin(Deal.contact)
        .outerjoin(Deal.stage)
        .order_by(Deal.created_at.desc())
        .all()
    )
    result = []
    for d in deals:
        result.append({
            "id": d.id,
            "title": d.title or "Без названия",
            "total": d.total or 0.0,
            "client": d.contact.name if d.contact else "Нет клиента",
            "stage": d.stage.name if d.stage else "Без статуса",
            "created_at": (d.created_at or datetime.utcnow()).isoformat(),
        })
    return {"deals": result}  # фронтенд ждёт {deals: [...]}

@app.get("/api/tasks")
def get_tasks(db: DBSession = Depends(get_db)):
    tasks = db.query(Task).order_by(Task.due_date.asc()).all()
    result = [
        {"id": t.id, "title": t.title,
         "status": "Выполнено" if t.is_done else "В работе",
         "due_date": t.due_date.isoformat() if t.due_date else None}
        for t in tasks
    ]
    return {"tasks": result}  # фронтенд ждёт {tasks: [...]}

@app.get("/api/expenses")
def get_expenses(db: DBSession = Depends(get_db)):
    expenses = (
        db.query(Expense)
        .outerjoin(Expense.category)
        .order_by(Expense.date.desc())
        .all()
    )
    result = [
        {"id": e.id, "name": e.name, "amount": e.amount,
         "category": e.category.name if e.category else "Без категории",
         "date": e.date.isoformat() if e.date else None}
        for e in expenses
    ]
    return {"expenses": result}  # фронтенд ждёт {expenses: [...]}

@app.get("/api/equipment")
def get_equipment(db: DBSession = Depends(get_db)):
    equipment = db.query(Equipment).order_by(Equipment.name).all()
    return [
        {"id": e.id, "name": e.name, "model": e.model or "",
         "status": e.status or "active"}
        for e in equipment
    ]

@app.get("/api/services")
def get_services(db: DBSession = Depends(get_db)):
    # Таблица services может не существовать — возвращаем пустой список
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1 FROM services LIMIT 1"))
    except Exception:
        return []
    # Если таблица есть — читаем напрямую
    from sqlalchemy import Table, MetaData, select
    meta = MetaData()
    meta.reflect(bind=engine, only=["services", "service_categories"])
    if "services" not in meta.tables:
        return []
    svc_table = meta.tables["services"]
    cat_table = meta.tables.get("service_categories")
    rows = db.execute(svc_table.select()).fetchall()
    result = []
    for r in rows:
        row_dict = dict(r._mapping)
        category = ""
        if cat_table is not None and row_dict.get("category_id"):
            cat_row = db.execute(
                cat_table.select().where(cat_table.c.id == row_dict["category_id"])
            ).fetchone()
            if cat_row:
                category = dict(cat_row._mapping).get("name", "")
        result.append({
            "id": row_dict.get("id"),
            "name": row_dict.get("name", ""),
            "category": category,
            "price": row_dict.get("price", 0),
            "unit": row_dict.get("unit", ""),
        })
    return result

@app.get("/api/consumables")
def get_consumables(db: DBSession = Depends(get_db)):
    consumables = db.query(Consumable).order_by(Consumable.name).all()
    return [
        {"id": c.id, "name": c.name,
         "stock_quantity": c.stock_quantity,
         "unit": c.unit}
        for c in consumables
    ]

@app.get("/api/contacts")
def get_contacts(db: DBSession = Depends(get_db)):
    contacts = db.query(Contact).order_by(Contact.name).all()
    return [
        {"id": c.id, "name": c.name, "phone": c.phone}
        for c in contacts
    ]


# 7. FRONTEND SERVING
@app.get("/{full_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend(full_path: str):
    path = os.path.join("./", full_path) if full_path else "./index.html"
    if os.path.exists(path) and os.path.isfile(path):
        return FileResponse(path)
    return FileResponse("./index.html")


print("main.py (v3.3) loaded successfully.", flush=True)
