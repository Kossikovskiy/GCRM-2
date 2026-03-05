from dotenv import load_dotenv
load_dotenv()

import os
import secrets
import threading
import time as _time
from datetime import datetime, date, timedelta
from contextlib import asynccontextmanager
from typing import Optional, List
from functools import lru_cache

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date,
    DateTime, Boolean, ForeignKey, Text, text, MetaData, extract
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession
from pydantic import BaseModel
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
CACHE_TTL      = 300

for _var, _val in [
    ("DATABASE_URL",        DATABASE_URL),
    ("AUTH0_DOMAIN",        AUTH0_DOMAIN),
    ("AUTH0_AUDIENCE",      AUTH0_AUDIENCE),
    ("AUTH0_CLIENT_ID",     CLIENT_ID),
    ("AUTH0_CLIENT_SECRET", CLIENT_SECRET),
]:
    if not _val:
        raise RuntimeError(f"FATAL: {_var} is not set.")


# ── 2. КЭШ В ПАМЯТИ ──────────────────────────────────────────────────────────
class _Cache:
    def __init__(self, ttl: int):
        self._ttl, self._data, self._ts, self._lock = ttl, {}, {}, threading.Lock()

    def get(self, key: str):
        with self._lock:
            if key not in self._data or _time.monotonic() - self._ts[key] > self._ttl:
                return None
            return self._data[key]

    def set(self, key: str, value):
        with self._lock:
            self._data[key], self._ts[key] = value, _time.monotonic()

    def invalidate(self, *keys):
        with self._lock:
            if not keys:
                self._data.clear(); self._ts.clear()
                return
            for k in keys:
                to_remove = [ek for ek in self._data if ek == k or ek.startswith(f"{k}:")]
                for ek in to_remove:
                    if ek in self._data: del self._data[ek]
                    if ek in self._ts:   del self._ts[ek]

_cache = _Cache(ttl=CACHE_TTL)


# ── 3. БАЗА ДАННЫХ ────────────────────────────────────────────────────────────
Base = declarative_base()
# Явно указываем кодировку utf8 для корректной работы с кириллицей
engine = create_engine(DATABASE_URL, client_encoding='utf8')
SessionFactory = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id       = Column(String, primary_key=True)
    username = Column(String) # Legacy
    name     = Column(String)
    email    = Column(String)

class Stage(Base):
    __tablename__ = "stages"
    id, name, order, type, is_final, color = Column(Integer, primary_key=True), Column(String(100), nullable=False, unique=True), Column(Integer, default=0), Column(String(50), default="regular"), Column(Boolean, default=False), Column(String(20), default="#6B7280")
    deals = relationship("Deal", back_populates="stage")

class Contact(Base):
    __tablename__ = "contacts"
    id, name, phone, source = Column(Integer, primary_key=True), Column(String(200), nullable=False), Column(String(50), unique=True, index=True, nullable=True), Column(String(100), nullable=True)
    deals = relationship("Deal", back_populates="contact")

class Deal(Base):
    __tablename__ = "deals"
    id, contact_id, stage_id, title = Column(Integer, primary_key=True), Column(Integer, ForeignKey("contacts.id"), nullable=True), Column(Integer, ForeignKey("stages.id"), nullable=True), Column(String(200), nullable=False)
    total, notes, created_at, deal_date = Column(Float, default=0.0), Column(Text, default=""), Column(DateTime, default=datetime.utcnow), Column(DateTime, nullable=True)
    closed_at, is_repeat, manager, address = Column(DateTime, nullable=True), Column(Boolean, default=False), Column(String(200), nullable=True), Column(Text, nullable=True)
    contact, stage = relationship("Contact", back_populates="deals"), relationship("Stage", back_populates="deals")

class Task(Base):
    __tablename__ = "tasks"
    id, title, description, is_done = Column(Integer, primary_key=True), Column(String, nullable=False), Column(Text, nullable=True), Column(Boolean, default=False)
    due_date, assignee, priority, status = Column(Date, nullable=True), Column(String, nullable=True), Column(String, default="Обычный"), Column(String, default="Открыта")

class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id, name = Column(Integer, primary_key=True), Column(String(100), nullable=False, unique=True)
    expenses = relationship("Expense", back_populates="category")

class Equipment(Base):
    __tablename__ = "equipment"
    id, name, model, serial = Column(Integer, primary_key=True), Column(String(200), nullable=False), Column(String(200), default=""), Column(String(100), nullable=True)
    purchase_date, purchase_cost, engine_hours = Column(Date, nullable=True), Column(Float, default=0.0), Column(Float, default=0.0)
    status, notes = Column(String(50), default="active"), Column(Text, nullable=True)
    expenses = relationship("Expense", back_populates="equipment")

class Expense(Base):
    __tablename__ = "expenses"
    id, date, name, amount = Column(Integer, primary_key=True), Column(Date, nullable=False, default=date.today), Column(String(300), nullable=False), Column(Float, nullable=False)
    category_id, equipment_id = Column(Integer, ForeignKey("expense_categories.id"), nullable=True), Column(Integer, ForeignKey("equipment.id"), nullable=True)
    category, equipment = relationship("ExpenseCategory", back_populates="expenses"), relationship("Equipment", back_populates="expenses")

class Consumable(Base):
    __tablename__ = "consumables"
    id, name, unit, stock_quantity, notes = Column(Integer, primary_key=True), Column(String(200), nullable=False, unique=True), Column(String(50), default="шт"), Column(Float, default=0.0), Column(Text, nullable=True)

# ── 4. ИНИЦИАЛИЗАЦИЯ БД ───────────────────────────────────────────────────────
def init_and_seed_db():
    try:
        Base.metadata.create_all(engine)
        with SessionFactory() as s:
            if s.query(Stage).count() == 0:
                s.add_all([Stage(**d) for d in [
                    {"name": "Согласовать", "order": 1, "color": "#3B82F6"},
                    {"name": "Ожидание", "order": 2, "color": "#F59E0B"},
                    {"name": "В работе", "order": 3, "color": "#EC4899"},
                    {"name": "Успешно", "order": 4, "color": "#10B981", "is_final": True},
                    {"name": "Провалена", "order": 5, "color": "#EF4444", "is_final": True}]])
            if s.query(ExpenseCategory).count() == 0:
                s.add_all([ExpenseCategory(name=n) for n in ["Техника", "Топливо", "Расходники", "Реклама", "Запчасти", "Прочее"]])
            s.commit()
    except Exception as e:
        print(f"---!! DB INIT ERROR: {e} !!---", flush=True)

# ── 5. АВТОРИЗАЦИЯ ────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_jwks() -> dict:
    with httpx.Client() as c:
        return c.get(f"https://{AUTH0_DOMAIN}/.well-known/jwks.json", timeout=10).raise_for_status().json()

def get_current_user(request: Request) -> dict:
    if not (user := request.session.get("user")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user

# ── 6. FASTAPI APP ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"App starting (v4.2)...", flush=True)
    init_and_seed_db()
    yield
    print("App shutting down.", flush=True)

app = FastAPI(title="GreenCRM API", version="4.2.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=True, same_site="lax")
app.add_middleware(CORSMiddleware, allow_origins=[APP_BASE_URL], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionFactory()
    try: yield db
    finally: db.close()

# ── 7. AUTH ЭНДПОИНТЫ ─────────────────────────────────────────────────────────
@app.get("/api/auth/login", include_in_schema=False)
def login(request: Request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(f"https://{AUTH0_DOMAIN}/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={CALLBACK_URL}&scope=openid%20profile%20email&audience={AUTH0_AUDIENCE}&state={state}")

@app.get("/api/auth/callback", include_in_schema=False)
def callback(request: Request, code: str = None, state: str = None, error: str = None):
    if error: return RedirectResponse(f"/?auth_error={error}")
    if not code: raise HTTPException(400, "No authorization code received")
    if state != request.session.pop("oauth_state", None): raise HTTPException(400, "Invalid OAuth state")

    with httpx.Client() as client:
        token_resp = client.post(f"https://{AUTH0_DOMAIN}/oauth/token", json={"grant_type": "authorization_code", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "code": code, "redirect_uri": CALLBACK_URL}, timeout=15)
        if token_resp.status_code != 200: raise HTTPException(500, f"Auth0 token exchange failed: {token_resp.text}")
        access_token = token_resp.json().get("access_token")
        if not access_token: raise HTTPException(500, "No access_token in Auth0 response")

        profile_resp = client.get(f"https://{AUTH0_DOMAIN}/userinfo", headers={"Authorization": f"Bearer {access_token}"})
        if profile_resp.status_code != 200: raise HTTPException(500, f"Failed to get user profile: {profile_resp.text}")
        user_profile = profile_resp.json()

    user_id = user_profile.get("sub")
    if not user_id: raise HTTPException(500, "User ID (sub) not found in profile")

    user_name = user_profile.get("name") or user_profile.get("nickname") or user_id
    user_email = user_profile.get("email")

    with SessionFactory() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            db.add(User(id=user_id, name=user_name, email=user_email))
        else:
            user.name, user.email = user_name, user_email
        db.commit()

    try:
        header = jwt.get_unverified_header(access_token)
        key = next((k for k in get_jwks()["keys"] if k["kid"] == header.get("kid")), None)
        payload = jwt.decode(access_token, key, algorithms=["RS256"], audience=AUTH0_AUDIENCE, issuer=f"https://{AUTH0_DOMAIN}/") if key else {}
        user_role = payload.get(ROLE_CLAIM, "user")
    except JWTError: user_role = "user"

    request.session["user"] = {"sub": user_id, "name": user_name, "role": user_role}
    return RedirectResponse("/")

@app.get("/api/auth/logout", include_in_schema=False)
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(f"https://{AUTH0_DOMAIN}/v2/logout?client_id={CLIENT_ID}&returnTo={APP_BASE_URL}")

# ── 8. DATA ЭНДПОИНТЫ ─────────────────────────────────────────────────────────
@app.get("/api/me")
def get_me(user: dict = Depends(get_current_user)): return user

@app.get("/api/users")
def get_users(db: DBSession = Depends(get_db), _=Depends(get_current_user)): return db.query(User).all()

# ... (остальной код без изменений) ...

@app.get("/api/years")
def get_years(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    if (cached := _cache.get("years")) is not None: return cached
    deal_years = {r[0] for r in db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM deal_date)::int FROM deals WHERE deal_date IS NOT NULL")).fetchall() if r[0]}
    exp_years = {r[0] for r in db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM date)::int FROM expenses WHERE date IS NOT NULL")).fetchall() if r[0]}
    years = sorted(deal_years | exp_years, reverse=True) or [datetime.utcnow().year]
    _cache.set("years", years); return years

@app.get("/api/stages")
def get_stages(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    if (cached := _cache.get("stages")) is not None: return cached
    result = db.query(Stage).order_by(Stage.order).all()
    _cache.set("stages", result); return result

@app.get("/api/deals")
def get_deals(year: Optional[int] = None, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    cache_key = f"deals:{year}"
    if (cached := _cache.get(cache_key)) is not None: return cached
    q = db.query(Deal).outerjoin(Deal.contact).outerjoin(Deal.stage).order_by(Deal.created_at.desc())
    if year: q = q.filter(extract("year", Deal.deal_date) == year)
    result = [{"id": d.id, "title": d.title or "", "total": d.total or 0.0, "client": d.contact.name if d.contact else "", "stage": d.stage.name if d.stage else "", "created_at": (d.created_at or datetime.utcnow()).isoformat()} for d in q.all()]
    _cache.set(cache_key, {"deals": result}); return {"deals": result}

class TaskCreate(BaseModel): title: str; description: Optional[str] = None; due_date: Optional[date] = None; priority: Optional[str] = "Обычный"; status: Optional[str] = "Открыта"; assignee: Optional[str] = None
class TaskUpdate(BaseModel): title: Optional[str] = None; description: Optional[str] = None; due_date: Optional[date] = None; priority: Optional[str] = None; status: Optional[str] = None; assignee: Optional[str] = None; is_done: Optional[bool] = None

@app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
def create_task(task: TaskCreate, db: DBSession = Depends(get_db), user: dict = Depends(get_current_user)):
    new_task = Task(title=task.title, description=task.description, due_date=task.due_date or (date.today() + timedelta(days=1)), priority=task.priority, status=task.status, assignee=task.assignee or user["sub"])
    db.add(new_task); db.commit(); db.refresh(new_task)
    _cache.invalidate("tasks"); return new_task

@app.get("/api/tasks")
def get_tasks(is_done: Optional[bool] = None, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    cache_key = f"tasks:{is_done}"
    if (cached := _cache.get(cache_key)) is not None: return cached
    q = db.query(Task).order_by(Task.due_date.asc())
    if is_done is not None: q = q.filter(Task.is_done == is_done)
    users = {u.id: u.name for u in db.query(User).all()}
    tasks = [{"id": t.id, "title": t.title, "description": t.description, "status": t.status, "is_done": t.is_done, "due_date": t.due_date.isoformat() if t.due_date else None, "assignee": t.assignee, "assignee_name": users.get(t.assignee, ""), "priority": t.priority} for t in q.all()]
    _cache.set(cache_key, tasks); return tasks

@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, task_data: TaskUpdate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task: raise HTTPException(404, "Task not found")
    for key, value in task_data.dict(exclude_unset=True).items(): setattr(task, key, value)
    if task.status == "Выполнена": task.is_done = True
    elif task_data.is_done is True: task.status = "Выполнена"
    db.commit(); _cache.invalidate("tasks"); return {"status": "ok"}

@app.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if task: db.delete(task); db.commit(); _cache.invalidate("tasks")

@app.get("/api/expenses")
def get_expenses(year: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    cache_key = f"expenses:{year}"
    if (cached := _cache.get(cache_key)) is not None: return cached
    q = db.query(Expense).outerjoin(Expense.category).filter(extract("year", Expense.date) == year).order_by(Expense.date.desc())
    result = [{"id": e.id, "name": e.name, "amount": e.amount, "category": e.category.name if e.category else "", "date": e.date.isoformat() if e.date else None} for e in q.all()]
    _cache.set(cache_key, result); return result

@app.get("/api/equipment")
def get_equipment(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    if (cached := _cache.get("equipment")) is not None: return cached
    result = db.query(Equipment).order_by(Equipment.name).all()
    _cache.set("equipment", result); return result

@app.get("/api/consumables")
def get_consumables(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    if (cached := _cache.get("consumables")) is not None: return cached
    result = db.query(Consumable).order_by(Consumable.name).all()
    _cache.set("consumables", result); return result

@app.get("/api/contacts")
def get_contacts(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    if (cached := _cache.get("contacts")) is not None: return cached
    result = db.query(Contact).order_by(Contact.name).all()
    _cache.set("contacts", result); return result

@app.post("/api/cache/invalidate")
def invalidate_cache(_=Depends(get_current_user)):
    _cache.invalidate(); return {"status": "ok", "message": "Кэш сброшен."}

@app.get("/{full_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend(full_path: str):
    path = f"./{full_path.strip()}" if full_path else "./index.html"
    if os.path.exists(path) and os.path.isfile(path): return FileResponse(path)
    return FileResponse("./index.html")

print(f"main.py (v4.2) loaded.", flush=True)
