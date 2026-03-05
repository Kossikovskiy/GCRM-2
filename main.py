
from dotenv import load_dotenv
load_dotenv()

import os
import secrets
import threading
import time as _time
from datetime import datetime, date
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
CACHE_TTL      = 300  # 5 минут

TAX_RATE       = float(os.getenv("TAX_RATE", "0.06"))  # 6% УСН по умолчанию

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
        self._ttl  = ttl
        self._data: dict = {}
        self._ts:   dict = {}
        self._lock  = threading.Lock()

    def get(self, key: str):
        with self._lock:
            if key not in self._data:
                return None
            if _time.monotonic() - self._ts[key] > self._ttl:
                del self._data[key], self._ts[key]
                return None
            return self._data[key]

    def set(self, key: str, value):
        with self._lock:
            self._data[key] = value
            self._ts[key]   = _time.monotonic()

    def invalidate(self, *keys):
        with self._lock:
            if not keys or "all" in keys:
                self._data.clear()
                self._ts.clear()
            else:
                for k in keys:
                    prefix_keys = [pk for pk in self._data if pk.startswith(k)]
                    for pk in prefix_keys:
                         self._data.pop(pk, None)
                         self._ts.pop(pk, None)


_cache = _Cache(ttl=CACHE_TTL)


# ── 3. БАЗА ДАННЫХ ────────────────────────────────────────────────────────────
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

class Service(Base):
    __tablename__ = "services"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    price = Column(Float, default=0.0)
    unit = Column(String(50), default="шт")
    min_volume = Column(Float, default=1.0)
    notes = Column(Text)

class DealService(Base):
    __tablename__ = "deal_services"
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="CASCADE"), primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"), primary_key=True)
    quantity = Column(Float, nullable=False)
    price_at_moment = Column(Float, nullable=False)
    deal = relationship("Deal", back_populates="service_links")
    service = relationship("Service")

class Deal(Base):
    __tablename__ = "deals"
    id         = Column(Integer, primary_key=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True)
    stage_id   = Column(Integer, ForeignKey("stages.id"),   nullable=True)
    title      = Column(String(200), nullable=False)
    total      = Column(Float, default=0.0)
    notes      = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    deal_date  = Column(DateTime, nullable=True, default=datetime.utcnow)
    closed_at  = Column(DateTime, nullable=True)
    is_repeat  = Column(Boolean, default=False)
    manager    = Column(String(200), nullable=True)
    address    = Column(Text, nullable=True)
    discount     = Column(Float, default=0.0)
    tax_rate     = Column(Float, default=0.0)
    tax_included = Column(Boolean, default=False)
    contact    = relationship("Contact", back_populates="deals")
    stage      = relationship("Stage",   back_populates="deals")
    service_links = relationship("DealService", back_populates="deal", cascade="all, delete-orphan")

class Task(Base):
    __tablename__ = "tasks"
    id          = Column(Integer, primary_key=True)
    title       = Column(String, nullable=False)
    description = Column(Text)
    is_done     = Column(Boolean, default=False)
    due_date    = Column(Date, nullable=True)
    priority    = Column(String(50), default='Обычный')
    status      = Column(String(50), default='Открыта')
    assignee    = Column(String, nullable=True)

class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id       = Column(Integer, primary_key=True)
    name     = Column(String(100), nullable=False, unique=True)
    expenses = relationship("Expense", back_populates="category")

class Equipment(Base):
    __tablename__ = "equipment"
    id           = Column(Integer, primary_key=True)
    name         = Column(String(200), nullable=False)
    model        = Column(String(200), default="")
    serial       = Column(String(100), nullable=True)
    purchase_date = Column(Date, nullable=True)
    purchase_cost = Column(Float, default=0.0)
    engine_hours = Column(Float, default=0.0)
    status       = Column(String(50), default="active")
    notes        = Column(Text, nullable=True)
    expenses     = relationship("Expense", back_populates="equipment")
    fuel_norm    = Column(Float, nullable=True)
    last_maintenance_date = Column(Date, nullable=True)
    next_maintenance_date = Column(Date, nullable=True)

class Expense(Base):
    __tablename__ = "expenses"
    id           = Column(Integer, primary_key=True)
    date         = Column(Date, nullable=False, default=date.today)
    name         = Column(String(300), nullable=False)
    amount       = Column(Float, nullable=False)
    category_id  = Column(Integer, ForeignKey("expense_categories.id"), nullable=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=True)
    category     = relationship("ExpenseCategory", back_populates="expenses")
    equipment    = relationship("Equipment", back_populates="expenses")

class Consumable(Base):
    __tablename__ = "consumables"
    id             = Column(Integer, primary_key=True)
    name           = Column(String(200), nullable=False, unique=True)
    unit           = Column(String(50), default="шт")
    stock_quantity = Column(Float, default=0.0)
    price          = Column(Float, default=0.0)
    notes          = Column(Text, nullable=True)

class TaxPayment(Base):
    __tablename__ = "tax_payments"
    id      = Column(Integer, primary_key=True)
    amount  = Column(Float, nullable=False)
    date    = Column(Date, nullable=False, default=date.today)
    note    = Column(String(300), nullable=True)
    year    = Column(Integer, nullable=False)


# ── 4. ИНИЦИАЛИЗАЦИЯ БД ───────────────────────────────────────────────────────
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


# ── 5. АВТОРИЗАЦИЯ ────────────────────────────────────────────────────────────
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


# ── 6. FASTAPI APP ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("App starting (v3.8)...", flush=True)
    init_and_seed_db()
    yield
    print("App shutting down.", flush=True)

app = FastAPI(title="GreenCRM API", version="3.8.0", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=not (os.getenv("DEV_MODE") == "1"), same_site="lax")
app.add_middleware(CORSMiddleware, allow_origins=[APP_BASE_URL], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionFactory()
    try:    yield db
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
    if not code or state != request.session.pop("oauth_state", None):
        raise HTTPException(400, "Invalid state or no code")
    with httpx.Client() as client:
        resp = client.post(f"https://{AUTH0_DOMAIN}/oauth/token", json={"grant_type": "authorization_code", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "code": code, "redirect_uri": CALLBACK_URL}, timeout=15)
        if resp.status_code != 200: raise HTTPException(500, f"Auth0 error: {resp.text}")
        tokens = resp.json()
    try: payload = decode_access_token(tokens["access_token"])
    except (JWTError, KeyError) as e: raise HTTPException(401, f"Token error: {e}")
    request.session["user"] = {"sub": payload.get("sub", ""), "role": payload.get(ROLE_CLAIM, "user")}
    return RedirectResponse("/")

@app.get("/api/auth/logout", include_in_schema=False)
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(f"https://{AUTH0_DOMAIN}/v2/logout?client_id={CLIENT_ID}&returnTo={APP_BASE_URL}")


# ── 8. DATA ЭНДПОИНТЫ ─────────────────────────────────────────────────────────

class User(BaseModel):
    id: str
    name: str

@app.get("/api/users", response_model=List[User])
def get_users(_=Depends(get_current_user)):
    return [{"id": "google-oauth2|111132204803657388744", "name": "Сергей"}]

@app.get("/api/me")
def get_me(user: dict = Depends(get_current_user)):
    return {"username": user["sub"], "role": user["role"]}

@app.get("/api/years")
def get_years(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    deal_years = db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM deal_date)::int FROM deals WHERE deal_date IS NOT NULL")).fetchall()
    exp_years = db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM date)::int FROM expenses WHERE date IS NOT NULL")).fetchall()
    years = sorted({r[0] for r in deal_years if r[0]} | {r[0] for r in exp_years if r[0]}, reverse=True)
    if not years: years = [datetime.utcnow().year]
    return years

@app.get("/api/stages", response_model=List[dict])
def get_stages(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return [s.__dict__ for s in db.query(Stage).order_by(Stage.order).all()]

# --- СДЕЛКИ (DEALS) --- 

class ServiceInDealSchema(BaseModel):
    service_id: int
    quantity: float

class DealCreateUpdateSchema(BaseModel):
    title: str
    contact_id: Optional[int] = None
    new_contact_name: Optional[str] = None
    stage_id: int
    manager: Optional[str] = None
    services: List[ServiceInDealSchema] = []
    discount: float = 0
    tax_rate: float = 0
    tax_included: bool = False

def _calculate_total(db: DBSession, services: List[ServiceInDealSchema], discount: float, tax_rate: float, tax_included: bool) -> float:
    subtotal = 0
    for item in services:
        service_db = db.query(Service).get(item.service_id)
        if not service_db: raise HTTPException(404, f"Service {item.service_id} not found")
        subtotal += service_db.price * item.quantity
    
    discounted_total = subtotal * (1 - discount / 100)
    if tax_included:
        return round(discounted_total, 2)
    else:
        return round(discounted_total * (1 + tax_rate / 100), 2)

@app.post("/api/deals", status_code=201)
def create_deal(body: DealCreateUpdateSchema, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    contact_id = body.contact_id
    if not contact_id and body.new_contact_name:
        contact = db.query(Contact).filter_by(name=body.new_contact_name).first()
        if not contact:
            contact = Contact(name=body.new_contact_name)
            db.add(contact); db.commit(); db.refresh(contact)
        contact_id = contact.id

    deal = Deal(
        title=body.title, contact_id=contact_id, stage_id=body.stage_id, manager=body.manager,
        discount=body.discount, tax_rate=body.tax_rate, tax_included=body.tax_included,
        deal_date=datetime.utcnow(),
        total = _calculate_total(db, body.services, body.discount, body.tax_rate, body.tax_included)
    )

    for s_in in body.services:
        service_db = db.query(Service).get(s_in.service_id)
        deal.service_links.append(DealService(service_id=s_in.service_id, quantity=s_in.quantity, price_at_moment=service_db.price))
    
    db.add(deal); db.commit(); db.refresh(deal)
    _cache.invalidate("all")
    return {"id": deal.id}

@app.patch("/api/deals/{deal_id}")
def update_deal(deal_id: int, body: DealCreateUpdateSchema, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    deal = db.query(Deal).get(deal_id)
    if not deal: raise HTTPException(404, "Deal not found")

    for key, value in body.dict(exclude={'services', 'new_contact_name'}).items():
        setattr(deal, key, value)

    deal.total = _calculate_total(db, body.services, body.discount, body.tax_rate, body.tax_included)
    deal.service_links.clear()
    for s_in in body.services:
        service_db = db.query(Service).get(s_in.service_id)
        if service_db:
            deal.service_links.append(DealService(service_id=s_in.service_id, quantity=s_in.quantity, price_at_moment=service_db.price))

    db.commit()
    _cache.invalidate("all")
    return {"ok": True}

@app.get("/api/deals")
def get_deals(year: Optional[int] = None, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(Deal).outerjoin(Deal.contact).outerjoin(Deal.stage).order_by(Deal.created_at.desc())
    if year: q = q.filter(extract("year", Deal.deal_date) == year)
    deals_data = []
    for d in q.all():
        deals_data.append({
            "id": d.id, "title": d.title, "total": d.total or 0.0,
            "client": d.contact.name if d.contact else "",
            "stage": d.stage.name if d.stage else "",
            "created_at": d.created_at.isoformat(),
        })
    return {"deals": deals_data}

@app.get("/api/deals/{deal_id}")
def get_deal_details(deal_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    deal = db.query(Deal).get(deal_id)
    if not deal: raise HTTPException(404, "Deal not found")
    return {
        "id": deal.id, "title": deal.title, "total": deal.total, "stage_id": deal.stage_id,
        "contact": {"id": deal.contact.id, "name": deal.contact.name} if deal.contact else None,
        "manager": deal.manager, "discount": deal.discount, "tax_rate": deal.tax_rate, "tax_included": deal.tax_included,
        "services": [
            {"service": {"id": sl.service.id, "name": sl.service.name, "unit": sl.service.unit, "min_volume": sl.service.min_volume}, "quantity": sl.quantity, "price_at_moment": sl.price_at_moment}
            for sl in deal.service_links
        ]
    }

@app.delete("/api/deals/{deal_id}", status_code=204)
def delete_deal(deal_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    deal = db.query(Deal).get(deal_id)
    if deal: db.delete(deal); db.commit(); _cache.invalidate("all")
    return

@app.patch("/api/deals/{deal_id}/stage")
def update_deal_stage(deal_id: int, body: dict, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    deal = db.query(Deal).get(deal_id)
    if not deal: raise HTTPException(404, "Deal not found")
    stage = db.query(Stage).get(body.get("stage_id"))
    if not stage: raise HTTPException(404, "Stage not found")
    deal.stage_id = stage.id
    db.commit()
    _cache.invalidate("all")
    return {"ok": True}


# --- ОСТАЛЬНЫЕ CRUD --- 

@app.get("/api/tasks")
def get_tasks(year: Optional[int] = None, is_done: Optional[bool] = None, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(Task).order_by(Task.due_date.asc())
    if year: q = q.filter(extract("year", Task.due_date) == year)
    if is_done is not None: q = q.filter(Task.is_done == is_done)
    users = {u['id']: u['name'] for u in get_users()}
    return [dict(t.__dict__, assignee_name=users.get(t.assignee, '-')) for t in q.all()]

class TaskCreate(BaseModel):
    title: str; description: Optional[str]=None; due_date: Optional[date]=None; is_done: bool=False; priority:str='Обычный'; assignee:Optional[str]=None; status:str='Открыта'

@app.post("/api/tasks", status_code=201)
def create_task(body: TaskCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    task = Task(**body.dict()); db.add(task); db.commit(); db.refresh(task); _cache.invalidate("tasks")
    return task

@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(Task).get(task_id); 
    if not task: raise HTTPException(404)
    for k, v in body.dict(exclude_unset=True).items(): setattr(task, k, v)
    db.commit(); _cache.invalidate("tasks")
    return task

@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(Task).get(task_id)
    if task: db.delete(task); db.commit(); _cache.invalidate("tasks")
    return

@app.get("/api/expenses")
def get_expenses(year: Optional[int] = None, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(Expense).outerjoin(Expense.category).order_by(Expense.date.desc())
    if year: q = q.filter(extract("year", Expense.date) == year)
    return [dict(e.__dict__, category=e.category.name if e.category else None) for e in q.all()]

class ExpenseCreate(BaseModel):
    name: str; amount: float; date: date; category: Optional[str] = None

@app.post("/api/expenses", status_code=201)
def create_expense(body: ExpenseCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    cat_id = None
    if body.category: 
        cat = db.query(ExpenseCategory).filter_by(name=body.category).first()
        if not cat: cat = ExpenseCategory(name=body.category); db.add(cat); db.commit(); db.refresh(cat)
        cat_id = cat.id
    exp = Expense(name=body.name, amount=body.amount, date=body.date, category_id=cat_id)
    db.add(exp); db.commit(); db.refresh(exp); _cache.invalidate("all")
    return exp

@app.patch("/api/expenses/{expense_id}")
def update_expense(expense_id: int, body: ExpenseCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    exp = db.query(Expense).get(expense_id); 
    if not exp: raise HTTPException(404)
    cat_id = None
    if body.category:
        cat = db.query(ExpenseCategory).filter_by(name=body.category).first()
        if not cat: cat = ExpenseCategory(name=body.category); db.add(cat); db.commit(); db.refresh(cat)
        cat_id = cat.id
    exp.name=body.name; exp.amount=body.amount; exp.date=body.date; exp.category_id=cat_id
    db.commit(); _cache.invalidate("all")
    return exp

@app.delete("/api/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    exp = db.query(Expense).get(expense_id)
    if exp: db.delete(exp); db.commit(); _cache.invalidate("all")
    return

@app.get("/api/expense-categories")
def get_expense_categories(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return db.query(ExpenseCategory).all()

@app.get("/api/equipment", response_model=List[dict])
def get_equipment(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return [e.__dict__ for e in db.query(Equipment).order_by(Equipment.name).all()]

@app.get("/api/services", response_model=List[dict])
def get_services(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return [s.__dict__ for s in db.query(Service).order_by(Service.name).all()]

@app.get("/api/consumables", response_model=List[dict])
def get_consumables(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return [c.__dict__ for c in db.query(Consumable).order_by(Consumable.name).all()]

@app.get("/api/contacts", response_model=List[dict])
def get_contacts(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return [c.__dict__ for c in db.query(Contact).order_by(Contact.name).all()]

@app.post("/api/contacts", status_code=201)
def create_contact(body: dict, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    c = Contact(name=body['name'], phone=body.get('phone'), source=body.get('source'))
    db.add(c); db.commit(); db.refresh(c); _cache.invalidate("all")
    return c

# ... and so on for other models ...

# ── 9. НАЛОГИ ─────────────────────────────────────────────────────────────────

@app.get("/api/taxes/summary")
def get_tax_summary(year: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    success_stage = db.query(Stage).filter(Stage.type == "success").first()
    if not success_stage: return {"balance": 0, "tax_accrued": 0, "paid": 0} # Or raise error
    revenue = sum(d.total or 0 for d in db.query(Deal).filter(Deal.stage_id == success_stage.id, extract("year", Deal.deal_date) == year).all())
    tax_accrued = round(revenue * TAX_RATE, 2)
    paid = sum(p.amount for p in db.query(TaxPayment).filter(TaxPayment.year == year).all())
    return {"year": year, "tax_rate": TAX_RATE, "revenue": round(revenue, 2), "tax_accrued": tax_accrued, "paid": round(paid, 2), "balance": round(tax_accrued - paid, 2)}

@app.get("/api/taxes/payments")
def get_tax_payments(year: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return {"payments": db.query(TaxPayment).filter(TaxPayment.year == year).order_by(TaxPayment.date.desc()).all()}

class TaxPaymentCreate(BaseModel):
    amount: float; date: date; note: Optional[str] = None; year: int

@app.post("/api/taxes/payments", status_code=201)
def create_tax_payment(body: TaxPaymentCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    if body.amount <= 0: raise HTTPException(400, "Сумма должна быть положительной")
    payment = TaxPayment(**body.dict()); db.add(payment); db.commit(); db.refresh(payment)
    _cache.invalidate(f"taxes_summary:{body.year}", f"taxes_payments:{body.year}")
    return payment


# ── 10. СБРОС КЭША И ФРОНТЕНД ──────────────────────────────────────────────────

@app.post("/api/cache/invalidate")
def invalidate_cache(_=Depends(get_current_user)):
    _cache.invalidate("all"); return {"status": "ok"}

@app.get("/{full_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend(full_path: str):
    path = f"./{full_path.strip()}" if full_path else "./index.html"
    if os.path.exists(path) and os.path.isfile(path): return FileResponse(path)
    return FileResponse("./index.html")

print("main.py (v3.8) loaded.", flush=True)
