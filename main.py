from dotenv import load_dotenv
load_dotenv()

import os
import secrets
from datetime import datetime, date
from contextlib import asynccontextmanager
from typing import Optional
from functools import lru_cache

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date,
    DateTime, Boolean, ForeignKey, Text, text, MetaData
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession
import httpx
from jose import jwt, JWTError


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

for _var, _val in [
    ("DATABASE_URL",        DATABASE_URL),
    ("AUTH0_DOMAIN",        AUTH0_DOMAIN),
    ("AUTH0_AUDIENCE",      AUTH0_AUDIENCE),
    ("AUTH0_CLIENT_ID",     CLIENT_ID),
    ("AUTH0_CLIENT_SECRET", CLIENT_SECRET),
]:
    if not _val:
        raise RuntimeError(f"FATAL: {_var} is not set.")


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
    print("--- DB INIT START ---", flush=True)
    try:
        Base.metadata.create_all(engine)
        with SessionFactory() as s:
            if s.query(Stage).count() == 0:
                for d in [
                    {"name": "Согласовать", "order": 1, "color": "#3B82F6", "type": "regular"},
                    {"name": "Ожидание",    "order": 2, "color": "#F59E0B", "type": "regular"},
                    {"name": "В работе",    "order": 3, "color": "#EC4899", "type": "regular"},
                    {"name": "Успешно",     "order": 4, "color": "#10B981", "type": "success", "is_final": True},
                    {"name": "Провалена",   "order": 5, "color": "#EF4444", "type": "failed",  "is_final": True},
                ]:
                    s.add(Stage(**d))
                s.commit()
            if s.query(ExpenseCategory).count() == 0:
                for name in ["Техника", "Топливо", "Расходники", "Реклама", "Запчасти", "Прочее"]:
                    s.add(ExpenseCategory(name=name))
                s.commit()
        print("--- DB INIT DONE ---", flush=True)
    except Exception as e:
        print(f"---!! DB INIT ERROR: {e} !!---", flush=True)


# ── 4. АВТОРИЗАЦИЯ ────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_jwks() -> dict:
    with httpx.Client() as c:
        r = c.get(f"https://{AUTH0_DOMAIN}/.well-known/jwks.json", timeout=10)
        r.raise_for_status()
        return r.json()

def decode_access_token(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    key = next((k for k in get_jwks()["keys"] if k["kid"] == header.get("kid")), None)
    if not key:
        raise JWTError("Signing key not found")
    return jwt.decode(token, key, algorithms=["RS256"],
                      audience=AUTH0_AUDIENCE, issuer=f"https://{AUTH0_DOMAIN}/")

def get_current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


# ── 5. ПРИЛОЖЕНИЕ ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("App starting (v3.6)...", flush=True)
    init_and_seed_db()
    yield
    print("App shutting down.", flush=True)

app = FastAPI(title="GreenCRM API", version="3.6.0", lifespan=lifespan)

# FIX: Changed https_only to False to allow session cookies over HTTP
app.add_middleware(SessionMiddleware,
                   secret_key=SESSION_SECRET,
                   https_only=False, 
                   same_site="lax")
app.add_middleware(CORSMiddleware,
                   allow_origins=[APP_BASE_URL],
                   allow_credentials=True,
                   allow_methods=["*"],
                   allow_headers=["*"])

def get_db():
    db = SessionFactory()
    try:    yield db
    finally: db.close()


# ── 6. AUTH ЭНДПОИНТЫ ─────────────────────────────────────────────────────────

@app.get("/api/auth/login", include_in_schema=False)
def login(request: Request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(
        f"https://{AUTH0_DOMAIN}/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={CALLBACK_URL}"
        f"&scope=openid%20profile%20email"
        f"&audience={AUTH0_AUDIENCE}"
        f"&state={state}"
    )

@app.get("/api/auth/callback", include_in_schema=False)
def callback(request: Request, code: str = None, state: str = None, error: str = None):
    if error:
        return RedirectResponse(f"/?auth_error={error}")
    if not code:
        raise HTTPException(400, "No authorization code received")
    if state != request.session.pop("oauth_state", None):
        raise HTTPException(400, "Invalid OAuth state")

    with httpx.Client() as client:
        resp = client.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            json={
                "grant_type":    "authorization_code",
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code":          code,
                "redirect_uri":  CALLBACK_URL,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise HTTPException(500, f"Auth0 token exchange failed: {resp.text}")
        tokens = resp.json()

    access_token = tokens.get("access_token")
    if not access_token:
        raise HTTPException(500, "No access_token in Auth0 response")

    try:
        payload = decode_access_token(access_token)
    except JWTError as e:
        raise HTTPException(401, f"Token validation failed: {e}")

    request.session["user"] = {
        "sub":  payload.get("sub", ""),
        "role": payload.get(ROLE_CLAIM, "user"),
    }
    return RedirectResponse("/")

@app.get("/api/auth/logout", include_in_schema=False)
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(
        f"https://{AUTH0_DOMAIN}/v2/logout"
        f"?client_id={CLIENT_ID}"
        f"&returnTo={APP_BASE_URL}"
    )


# ── 7. DATA ЭНДПОИНТЫ ─────────────────────────────────────────────────────────

@app.get("/api/me")
def get_me(user: dict = Depends(get_current_user)):
    return {"username": user["sub"], "role": user["role"]}

@app.get("/api/stages")
def get_stages(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return [{"id": s.id, "name": s.name, "order": s.order,
             "type": s.type, "is_final": s.is_final, "color": s.color}
            for s in db.query(Stage).order_by(Stage.order).all()]

@app.get("/api/deals")
def get_deals(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    deals = (db.query(Deal)
               .outerjoin(Deal.contact)
               .outerjoin(Deal.stage)
               .order_by(Deal.created_at.desc())
               .all())
    result = []
    for d in deals:
        client_name = d.contact.name if d.contact else "Нет клиента" 
        result.append({
            "id":         d.id,
            "title":      d.title or "Без названия",
            "total":      d.total or 0.0,
            "client":     client_name,
            "stage":      d.stage.name if d.stage else "Без статуса",
            "created_at": (d.created_at or datetime.utcnow()).isoformat(),
        })
    return {"deals": result}

@app.get("/api/tasks")
def get_tasks(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return {"tasks": [{
        "id": t.id, "title": t.title,
        "status":   "Выполнено" if t.is_done else "В работе",
        "due_date": t.due_date.isoformat() if t.due_date else None,
    } for t in db.query(Task).order_by(Task.due_date.asc()).all()]}

@app.get("/api/expenses")
def get_expenses(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    rows = db.query(Expense).outerjoin(Expense.category).order_by(Expense.date.desc()).all()
    return {"expenses": [{
        "id": e.id, "name": e.name, "amount": e.amount,
        "category": e.category.name if e.category else "Без категории",
        "date": e.date.isoformat() if e.date else None,
    } for e in rows]}

@app.get("/api/equipment")
def get_equipment(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return [{"id": e.id, "name": e.name, "model": e.model or "", "status": e.status or "active"}
            for e in db.query(Equipment).order_by(Equipment.name).all()]

@app.get("/api/services")
def get_services(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    try:
        db.execute(text("SELECT 1 FROM services LIMIT 1"))
    except Exception:
        return []
    meta = MetaData()
    meta.reflect(bind=engine, only=["services", "service_categories"])
    if "services" not in meta.tables:
        return []
    svc = meta.tables["services"]
    cat = meta.tables.get("service_categories")
    result = []
    for r in db.execute(svc.select()).fetchall():
        row = dict(r._mapping)
        category = ""
        if cat is not None and row.get("category_id"):
            crow = db.execute(cat.select().where(cat.c.id == row["category_id"])).fetchone()
            if crow:
                category = dict(crow._mapping).get("name", "")
        result.append({"id": row.get("id"), "name": row.get("name", ""),
                        "category": category, "price": row.get("price", 0),
                        "unit": row.get("unit", "")})
    return result

@app.get("/api/consumables")
def get_consumables(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return [{"id": c.id, "name": c.name, "stock_quantity": c.stock_quantity, "unit": c.unit}
            for c in db.query(Consumable).order_by(Consumable.name).all()]

@app.get("/api/contacts")
def get_contacts(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    # Берём контакты из таблицы contacts
    db_contacts = {
        c.name.strip().lower(): {"id": c.id, "name": c.name, "phone": c.phone}
        for c in db.query(Contact).order_by(Contact.name).all()
    }

    result = list(db_contacts.values())
    result.sort(key=lambda c: c["name"])
    return result


# ── 8. ФРОНТЕНД ───────────────────────────────────────────────────────────────
@app.get("/{full_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend(full_path: str):
    path = f"./{full_path}" if full_path else "./index.html"
    if os.path.exists(path) and os.path.isfile(path):
        return FileResponse(path)
    return FileResponse("./index.html")


print("main.py (v3.6) loaded.", flush=True)
