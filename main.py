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
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession, joinedload
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

# ── 2. КЭШ И УТИЛИТЫ ──────────────────────────────────────────────────────────
class _Cache:
    def __init__(self, ttl: int):
        self._ttl, self._data, self._ts, self._lock = ttl, {}, {}, threading.Lock()
    def get(self, key: str):
        with self._lock:
            if key not in self._data or _time.monotonic() - self._ts[key] > self._ttl: return None
            return self._data[key]
    def set(self, key: str, value):
        with self._lock: self._data[key], self._ts[key] = value, _time.monotonic()
    def invalidate(self, *keys):
        with self._lock:
            if not keys: self._data.clear(); self._ts.clear(); return
            for k in keys:
                to_remove = [ek for ek in self._data if ek == k or ek.startswith(f"{k}:")]
                for ek in to_remove: 
                    if ek in self._data: del self._data[ek]
                    if ek in self._ts: del self._ts[ek]
_cache = _Cache(ttl=CACHE_TTL)

# ── 3. БАЗА ДАННЫХ ────────────────────────────────────────────────────────────
Base = declarative_base()
engine = create_engine(DATABASE_URL, client_encoding='utf8')
SessionFactory = sessionmaker(bind=engine)

class User(Base): __tablename__ = "users"; id,username,name,email = Column(String, primary_key=True),Column(String),Column(String),Column(String)
class Service(Base): __tablename__ = "services"; id,name,price,unit = Column(Integer,primary_key=True),Column(String(200),nullable=False),Column(Float,default=0.0),Column(String(50),default="шт")
class DealService(Base): __tablename__ = "deal_services"; id,deal_id,service_id,quantity,price_at_moment = Column(Integer,primary_key=True),Column(Integer,ForeignKey("deals.id",ondelete="CASCADE")),Column(Integer,ForeignKey("services.id")),Column(Float,default=1.0),Column(Float,nullable=False); service = relationship("Service")
class Stage(Base): __tablename__ = "stages"; id,name,order,type,is_final,color = Column(Integer,primary_key=True),Column(String(100),nullable=False,unique=True),Column(Integer,default=0),Column(String(50),default="regular"),Column(Boolean,default=False),Column(String(20),default="#6B7280"); deals = relationship("Deal", back_populates="stage")
class Contact(Base): __tablename__ = "contacts"; id,name,phone,source=Column(Integer,primary_key=True),Column(String(200),nullable=False),Column(String(50),unique=True,index=True),Column(String(100)); deals = relationship("Deal",back_populates="contact")
class Deal(Base): __tablename__ = "deals"; id,contact_id,stage_id,title=Column(Integer,primary_key=True),Column(Integer,ForeignKey("contacts.id"),nullable=False),Column(Integer,ForeignKey("stages.id")),Column(String(200),nullable=False); total,notes,created_at,deal_date=Column(Float,default=0.0),Column(Text,default=""),Column(DateTime,default=datetime.utcnow),Column(DateTime); closed_at,is_repeat,manager,address=Column(DateTime),Column(Boolean,default=False),Column(String(200)),Column(Text); contact=relationship("Contact",back_populates="deals"); stage=relationship("Stage",back_populates="deals"); services=relationship("DealService",cascade="all, delete-orphan",passive_deletes=True)
class Task(Base): __tablename__="tasks"; id,title,description,is_done=Column(Integer,primary_key=True),Column(String,nullable=False),Column(Text),Column(Boolean,default=False); due_date,assignee,priority,status=Column(Date),Column(String),Column(String,default="Обычный"),Column(String,default="Открыта")
class ExpenseCategory(Base): __tablename__="expense_categories"; id,name=Column(Integer,primary_key=True),Column(String(100),nullable=False,unique=True); expenses=relationship("Expense",back_populates="category")
class Equipment(Base): __tablename__="equipment"; id,name,model,serial=Column(Integer,primary_key=True),Column(String(200),nullable=False),Column(String(200),default=""),Column(String(100)); purchase_date,purchase_cost,engine_hours=Column(Date),Column(Float,default=0.0),Column(Float,default=0.0); status,notes=Column(String(50),default="active"),Column(Text); expenses=relationship("Expense",back_populates="equipment")
class Expense(Base): __tablename__="expenses"; id,date,name,amount=Column(Integer,primary_key=True),Column(Date,nullable=False,default=date.today),Column(String(300),nullable=False),Column(Float,nullable=False); category_id,equipment_id=Column(Integer,ForeignKey("expense_categories.id")),Column(Integer,ForeignKey("equipment.id")); category=relationship("ExpenseCategory",back_populates="expenses"); equipment=relationship("Equipment",back_populates="expenses")
class Consumable(Base): __tablename__="consumables"; id,name,unit,stock_quantity,notes=Column(Integer,primary_key=True),Column(String(200),nullable=False,unique=True),Column(String(50),default="шт"),Column(Float,default=0.0),Column(Text)

def init_and_seed_db():
    try:
        Base.metadata.create_all(engine)
        with SessionFactory() as s:
            if s.query(Stage).count()==0: s.add_all([Stage(**d) for d in [{"name":"Согласовать","order":1,"color":"#3B82F6"},{"name":"Ожидание","order":2,"color":"#F59E0B"},{"name":"В работе","order":3,"color":"#EC4899"},{"name":"Успешно","order":4,"color":"#10B981","is_final":True},{"name":"Провалена","order":5,"color":"#EF4444","is_final":True}]])
            if s.query(ExpenseCategory).count()==0: s.add_all([ExpenseCategory(name=n) for n in ["Техника","Топливо","Расходники","Реклама","Запчасти","Прочее"]])
            if s.query(Service).count()==0: s.add_all([Service(**d) for d in [{"name":"Покос бурьяна/высокой травы","price":1500,"unit":"сотка"},{"name":"Покос газона","price":800,"unit":"сотка"},{"name":"Стрижка кустарника","price":500,"unit":"м.п."},{"name":"Вспашка земли мотоблоком","price":2000,"unit":"сотка"},{"name":"Спил дерева","price":1500,"unit":"шт"},{"name":"Вывоз мусора","price":3000,"unit":"рейс"}]])
            s.commit()
    except Exception as e: print(f"---!! DB INIT ERROR: {e} !!---", flush=True)

# ── 5. МОДЕЛИ PYDANTIC ────────────────────────────────────────────────────────
class DealServiceItem(BaseModel): service_id: int; quantity: float
class DealCreate(BaseModel): title:str; stage_id:int; contact_id:Optional[int]=None; new_contact_name:Optional[str]=None; manager:Optional[str]=None; services:List[DealServiceItem]=[]
class DealUpdate(BaseModel): title:Optional[str]=None; stage_id:Optional[int]=None; contact_id:Optional[int]=None; new_contact_name:Optional[str]=None; manager:Optional[str]=None; services:Optional[List[DealServiceItem]]=None
class TaskCreate(BaseModel): title: str; description: Optional[str]=None; due_date: Optional[date]=None; priority: Optional[str]="Обычный"; status: Optional[str]="Открыта"; assignee: Optional[str]=None
class TaskUpdate(BaseModel): title: Optional[str]=None; description: Optional[str]=None; due_date: Optional[date]=None; priority: Optional[str]=None; status: Optional[str]=None; assignee: Optional[str]=None; is_done: Optional[bool]=None

# ── 6. FASTAPI APP ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI): print("App starting (v6.0-final)...",flush=True); init_and_seed_db(); yield; print("App shutting down.",flush=True)

app = FastAPI(title="GreenCRM API", version="6.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=True, same_site="lax")
app.add_middleware(CORSMiddleware, allow_origins=[APP_BASE_URL], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@lru_cache(maxsize=1)
def get_jwks(): return httpx.get(f"https://{AUTH0_DOMAIN}/.well-known/jwks.json", timeout=10).raise_for_status().json()
def get_db(): db = SessionFactory(); yield db; db.close()
def get_current_user(req: Request): 
    if not (user := req.session.get("user")): raise HTTPException(401, "Not authenticated")
    return user

# ── 7. ЭНДПОИНТЫ AUTH ─────────────────────────────────────────────────────────
@app.get("/api/auth/login")
def login(req:Request): state=secrets.token_urlsafe(16); req.session["oauth_state"]=state; return RedirectResponse(f"https://{AUTH0_DOMAIN}/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={CALLBACK_URL}&scope=openid%20profile%20email&audience={AUTH0_AUDIENCE}&state={state}")
@app.get("/api/auth/callback")
def callback(req:Request, code:str=None, state:str=None, error:str=None):
    if error: return RedirectResponse(f"/?auth_error={error}")
    if not code or state!=req.session.pop("oauth_state",None): raise HTTPException(400,"Invalid state or no code")
    with httpx.Client() as c:
        tokens=c.post(f"https://{AUTH0_DOMAIN}/oauth/token",json={"grant_type":"authorization_code","client_id":CLIENT_ID,"client_secret":CLIENT_SECRET,"code":code,"redirect_uri":CALLBACK_URL}).raise_for_status().json()
        profile=c.get(f"https://{AUTH0_DOMAIN}/userinfo",headers={"Authorization":f"Bearer {tokens['access_token']}"}).raise_for_status().json()
    user_id=profile.get("sub"); user_name=profile.get("name") or profile.get("nickname") or user_id
    with SessionFactory() as db:
        user=db.query(User).filter(User.id==user_id).first()
        if not user: db.add(User(id=user_id,name=user_name,email=profile.get("email")))
        else: user.name,user.email = user_name,profile.get("email")
        db.commit()
    try:
        header=jwt.get_unverified_header(tokens['access_token']); key=next((k for k in get_jwks()["keys"] if k["kid"]==header.get("kid")),None)
        payload=jwt.decode(tokens['access_token'],key,algorithms=["RS256"],audience=AUTH0_AUDIENCE,issuer=f"https://{AUTH0_DOMAIN}/")
        user_role=payload.get(ROLE_CLAIM, "user")
    except JWTError: user_role="user"
    req.session["user"]={"sub":user_id,"name":user_name,"role":user_role}; return RedirectResponse("/")
@app.get("/api/auth/logout")
def logout(req:Request): req.session.clear(); return RedirectResponse(f"https://{AUTH0_DOMAIN}/v2/logout?client_id={CLIENT_ID}&returnTo={APP_BASE_URL}")

# ── 8. ЭНДПОИНТЫ API ──────────────────────────────────────────────────────────
@app.get("/api/me")
def get_me(user:dict=Depends(get_current_user)): return user
@app.get("/api/users")
def get_users(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(User).all()
@app.get("/api/stages")
def get_stages(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(Stage).order_by(Stage.order).all()
@app.get("/api/services")
def get_services(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(Service).order_by(Service.name).all()
@app.get("/api/contacts")
def get_contacts(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(Contact).order_by(Contact.name).all()
@app.post("/api/cache/invalidate")
def invalidate_cache(_=Depends(get_current_user)): _cache.invalidate(); return {"status":"ok"}

@app.get("/api/deals")
def get_deals(year:Optional[int]=None,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    q=db.query(Deal).options(joinedload(Deal.contact),joinedload(Deal.stage)).order_by(Deal.created_at.desc())
    if year: q = q.filter(extract("year", Deal.deal_date) == year)
    deals_list=[{"id":d.id,"title":d.title or "","total":d.total or 0.0,"client":d.contact.name if d.contact else "","stage":d.stage.name if d.stage else "","created_at":(d.created_at or datetime.utcnow()).isoformat()} for d in q.all()]
    return {"deals": deals_list}

@app.post("/api/deals", status_code=201)
def create_deal(deal_data:DealCreate,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    contact_id=deal_data.contact_id
    if deal_data.new_contact_name:
        new_contact=Contact(name=deal_data.new_contact_name); db.add(new_contact); db.flush(); db.refresh(new_contact)
        contact_id=new_contact.id
    if not contact_id: raise HTTPException(400, "Не указан клиент")

    total=0; service_items=[]
    for item in deal_data.services:
        service=db.query(Service).filter(Service.id==item.service_id).first()
        if not service: continue
        price=service.price; total+=price*item.quantity
        service_items.append(DealService(service_id=service.id,quantity=item.quantity,price_at_moment=price))
    new_deal=Deal(title=deal_data.title,total=total,stage_id=deal_data.stage_id,contact_id=contact_id,deal_date=datetime.utcnow(),manager=deal_data.manager,services=service_items)
    db.add(new_deal); db.commit(); _cache.invalidate("deals","years"); return {"status":"ok"}

@app.get("/api/deals/{deal_id}")
def get_deal_details(deal_id:int,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    deal=db.query(Deal).options(joinedload(Deal.contact),joinedload(Deal.services).joinedload(DealService.service)).filter(Deal.id==deal_id).first()
    if not deal: raise HTTPException(404, "Сделка не найдена")
    
    services_list = []
    for ds in deal.services:
        service_info = {"quantity": ds.quantity, "price_at_moment": ds.price_at_moment}
        if ds.service:
            service_info["service"] = {"id": ds.service.id, "name": ds.service.name, "price": ds.service.price, "unit": ds.service.unit}
        else:
            service_info["service"] = {"id": -1, "name": "[Удаленная услуга]", "price": ds.price_at_moment, "unit": "?"}
        services_list.append(service_info)
    
    return {"id":deal.id, "title":deal.title, "total":deal.total, "stage_id":deal.stage_id, "manager":deal.manager, "contact": {"id":deal.contact.id, "name":deal.contact.name} if deal.contact else None, "services": services_list}

@app.patch("/api/deals/{deal_id}")
def update_deal(deal_id: int, deal_data: DealUpdate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal: raise HTTPException(404, "Сделка не найдена")

    update_data = deal_data.dict(exclude_unset=True)

    if "new_contact_name" in update_data:
        new_contact = Contact(name=update_data["new_contact_name"]); db.add(new_contact); db.flush(); db.refresh(new_contact)
        deal.contact_id = new_contact.id
    elif "contact_id" in update_data:
        deal.contact_id = update_data["contact_id"]

    if "services" in update_data:
        db.query(DealService).filter(DealService.deal_id == deal_id).delete(synchronize_session=False)
        total = 0
        for item_data in update_data["services"]:
            item = DealServiceItem(**item_data)
            service = db.query(Service).filter(Service.id == item.service_id).first()
            if service:
                price = service.price; total += price * item.quantity
                db.add(DealService(deal_id=deal_id, service_id=service.id, quantity=item.quantity, price_at_moment=price))
        deal.total = total

    if "title" in update_data: deal.title = update_data["title"]
    if "stage_id" in update_data: deal.stage_id = update_data["stage_id"]
    if "manager" in update_data: deal.manager = update_data["manager"]

    db.commit()
    _cache.invalidate("deals", "years")
    return {"status": "ok"}


@app.delete("/api/deals/{deal_id}", status_code=204)
def delete_deal(deal_id: int, db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    deal=db.query(Deal).filter(Deal.id==deal_id).first()
    if deal: db.delete(deal); db.commit(); _cache.invalidate("deals","years")
    return None

@app.get("/api/years")
def get_years(db: DBSession=Depends(get_db),_=Depends(get_current_user)):
    if (cached := _cache.get("years")) is not None: return cached
    years=sorted(set([r[0] for r in db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM deal_date)::int FROM deals WHERE deal_date IS NOT NULL")).fetchall() if r[0]] + [r[0] for r in db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM date)::int FROM expenses WHERE date IS NOT NULL")).fetchall() if r[0]]), reverse=True) or [datetime.utcnow().year]
    _cache.set("years", years); return years

@app.get("/api/tasks")
def get_tasks(year: Optional[int] = None, is_done: Optional[bool] = None, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(Task).order_by(Task.due_date.asc())
    if year: q = q.filter(extract("year", Task.due_date) == year)
    if is_done is not None: q = q.filter(Task.is_done == is_done)
    users = {u.id: u.name for u in db.query(User).all()}
    return [{"id": t.id, "title": t.title, "description": t.description, "status": t.status, "is_done": t.is_done, "due_date": t.due_date.isoformat() if t.due_date else None, "assignee": t.assignee, "assignee_name": users.get(t.assignee, "Не назначен"), "priority": t.priority} for t in q.all()]
@app.post("/api/tasks", status_code=201)
def create_task(task: TaskCreate, db: DBSession = Depends(get_db), user: dict = Depends(get_current_user)):
    new_task = Task(title=task.title, description=task.description, due_date=task.due_date or (date.today() + timedelta(days=1)), priority=task.priority, status=task.status, assignee=task.assignee or user["sub"])
    db.add(new_task); db.commit(); db.refresh(new_task)
    _cache.invalidate("tasks"); return new_task
@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, task_data: TaskUpdate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task: raise HTTPException(404, "Task not found")
    for key, value in task_data.dict(exclude_unset=True).items(): setattr(task, key, value)
    if task_data.is_done: task.status = "Выполнена"
    elif task_data.status == "Выполнена": task.is_done = True
    db.commit(); _cache.invalidate("tasks"); return {"status": "ok"}
@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if task: db.delete(task); db.commit(); _cache.invalidate("tasks")
@app.get("/api/expenses")
def get_expenses(year: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)): 
    q = db.query(Expense).outerjoin(Expense.category).filter(extract("year", Expense.date) == year).order_by(Expense.date.desc())
    return [{"id": e.id, "name": e.name, "amount": e.amount, "category": e.category.name if e.category else "", "date": e.date.isoformat() if e.date else None} for e in q.all()]
@app.get("/api/equipment")
def get_equipment(db: DBSession = Depends(get_db), _=Depends(get_current_user)): return db.query(Equipment).order_by(Equipment.name).all()
@app.get("/api/consumables")
def get_consumables(db: DBSession = Depends(get_db), _=Depends(get_current_user)): return db.query(Consumable).order_by(Consumable.name).all()

@app.get("/{full_path:path}", response_class=FileResponse)
async def serve_frontend(full_path: str):
    path = f"./{full_path.strip()}" if full_path else "./index.html"
    return FileResponse(path if os.path.isfile(path) else "./index.html")

print(f"main.py (v6.0-final) loaded.", flush=True)
