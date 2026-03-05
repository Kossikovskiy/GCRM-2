
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
    create_engine, Column, Integer, String, Float, Date, DateTime, 
    Boolean, ForeignKey, Text, text, MetaData, extract, Double, func
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession, joinedload
from pydantic import BaseModel, Field, ConfigDict
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
SessionFactory = sessionmaker(bind=engine, autoflush=False)

class User(Base): __tablename__ = "users"; id,username,name,email = Column(String, primary_key=True),Column(String),Column(String),Column(String)
class Service(Base): __tablename__ = "services"; id,name,price,unit = Column(Integer,primary_key=True),Column(String(200),nullable=False),Column(Float,default=0.0),Column(String(50),default="шт"); min_volume=Column(Float,default=1.0); notes=Column(Text)
class DealService(Base): __tablename__ = "deal_services"; id,deal_id,service_id,quantity,price_at_moment = Column(Integer,primary_key=True),Column(Integer,ForeignKey("deals.id",ondelete="CASCADE")),Column(Integer,ForeignKey("services.id",ondelete="RESTRICT")),Column(Float,default=1.0),Column(Float,nullable=False); service = relationship("Service")
class Stage(Base): __tablename__ = "stages"; id,name,order,type,is_final,color = Column(Integer,primary_key=True),Column(String(100),nullable=False,unique=True),Column(Integer,default=0),Column(String(50),default="regular"),Column(Boolean,default=False),Column(String(20),default="#6B7280"); deals = relationship("Deal", back_populates="stage")
class Contact(Base): __tablename__ = "contacts"; id,name,phone,source=Column(Integer,primary_key=True),Column(String(200),nullable=False),Column(String(50),unique=True,index=True),Column(String(100)); deals = relationship("Deal",back_populates="contact")
class Deal(Base): 
    __tablename__ = "deals"
    id=Column(Integer,primary_key=True)
    contact_id=Column(Integer,ForeignKey("contacts.id"),nullable=False)
    stage_id=Column(Integer,ForeignKey("stages.id"))
    title=Column(String(200),nullable=False)
    total=Column(Float,default=0.0)
    notes=Column(Text,default="")
    created_at=Column(DateTime,default=datetime.utcnow)
    deal_date=Column(DateTime)
    closed_at=Column(DateTime)
    is_repeat=Column(Boolean,default=False)
    manager=Column(String(200))
    address=Column(Text)
    tax_rate = Column(Float, default=4.0, nullable=False)
    tax_included = Column(Boolean, default=True, nullable=False)
    discount = Column(Float, default=0.0, nullable=False)

    contact=relationship("Contact",back_populates="deals")
    stage=relationship("Stage",back_populates="deals")
    services=relationship("DealService",cascade="all, delete-orphan",passive_deletes=True)

class Task(Base): __tablename__="tasks"; id,title,description,is_done=Column(Integer,primary_key=True),Column(String,nullable=False),Column(Text),Column(Boolean,default=False); due_date,assignee,priority,status=Column(Date),Column(String),Column(String,default="Обычный"),Column(String,default="Открыта")
class ExpenseCategory(Base): __tablename__="expense_categories"; id,name=Column(Integer,primary_key=True),Column(String(100),nullable=False,unique=True); expenses=relationship("Expense",back_populates="category")
class Consumable(Base): __tablename__="consumables"; id,name,unit=Column(Integer,primary_key=True),Column(String(200),nullable=False,unique=True),Column(String(50),default="шт"); stock_quantity,notes,price=Column(Float,default=0.0),Column(Text),Column(Float,default=0.0)
class MaintenanceConsumable(Base): __tablename__="maintenance_consumables"; id=Column(Integer,primary_key=True); maintenance_id=Column(Integer,ForeignKey("equipment_maintenance.id",ondelete="CASCADE"),nullable=False); consumable_id=Column(Integer,ForeignKey("consumables.id",ondelete="RESTRICT"),nullable=False); quantity=Column(Float,nullable=False); price_at_moment=Column(Float,nullable=False); consumable=relationship("Consumable"); maintenance_record=relationship("EquipmentMaintenance",back_populates="consumables_used")
class EquipmentMaintenance(Base): __tablename__ = "equipment_maintenance"; id=Column(Integer,primary_key=True); equipment_id=Column(Integer,ForeignKey("equipment.id",ondelete="CASCADE"),nullable=False); date=Column(Date,nullable=False); work_description=Column(Text,nullable=False); cost=Column(Float); notes=Column(Text); equipment = relationship("Equipment", back_populates="maintenance_records"); consumables_used = relationship("MaintenanceConsumable", back_populates="maintenance_record", cascade="all, delete-orphan")
class Equipment(Base): __tablename__="equipment"; id,name,model,serial=Column(Integer,primary_key=True),Column(String(200),nullable=False),Column(String(200),default=""),Column(String(100)); purchase_date,purchase_cost=Column(Date),Column(Double,default=0.0); status,notes=Column(String(50),default="active"),Column(Text); engine_hours=Column(Double,default=0.0); fuel_norm=Column(Double,default=0.0); last_maintenance_date=Column(Date); next_maintenance_date=Column(Date); expenses=relationship("Expense",back_populates="equipment"); maintenance_records = relationship("EquipmentMaintenance", back_populates="equipment", cascade="all, delete-orphan")
class Expense(Base): __tablename__="expenses"; id,date,name,amount=Column(Integer,primary_key=True),Column(Date,nullable=False,default=date.today),Column(String(300),nullable=False),Column(Float,nullable=False); category_id,equipment_id=Column(Integer,ForeignKey("expense_categories.id")),Column(Integer,ForeignKey("equipment.id")); category=relationship("ExpenseCategory",back_populates="expenses"); equipment=relationship("Equipment",back_populates="expenses")
class TaxPayment(Base): __tablename__ = "tax_payments"; id=Column(Integer,primary_key=True); amount=Column(Double,nullable=False); payment_date=Column(Date,nullable=False); created_at=Column(DateTime,default=datetime.utcnow)

def init_db_structure(): Base.metadata.create_all(engine)
def seed_initial_data(s: DBSession):
    if s.query(Stage).count()==0: s.add_all([Stage(**d) for d in [{"name":"Согласовать","order":1,"color":"#3B82F6"},{"name":"Ожидание","order":2,"color":"#F59E0B"},{"name":"В работе","order":3,"color":"#EC4899"},{"name":"Успешно","order":4,"color":"#10B981","is_final":True},{"name":"Провалена","order":5,"color":"#EF4444","is_final":True}]])
    if s.query(ExpenseCategory).count()==0: s.add_all([ExpenseCategory(name=n) for n in ["Техника","Топливо","Расходники","Реклама","Запчасти","Прочее"]])
    s.commit()

# ── 4. HELPERS ────────────────────────────────────────────────────────────────
def update_equipment_last_maintenance(db: DBSession, equipment_id: int):
    equipment_item = db.query(Equipment).filter(Equipment.id == equipment_id).first();
    if not equipment_item: return
    latest_maintenance_date = db.query(func.max(EquipmentMaintenance.date)).filter(EquipmentMaintenance.equipment_id == equipment_id).scalar()
    equipment_item.last_maintenance_date = latest_maintenance_date; db.commit()
    _cache.invalidate("equipment")

# ── 5. PYDANTIC MODELS ───────────────────────────────────────────────────────
class DealServiceItem(BaseModel): service_id: int; quantity: float
class DealCreate(BaseModel): 
    title:str; 
    stage_id:int; 
    contact_id:Optional[int]=None; 
    new_contact_name:Optional[str]=None; 
    manager:Optional[str]=None; 
    services:List[DealServiceItem]=[]
    tax_rate: Optional[float] = 4.0
    tax_included: Optional[bool] = True
    discount: Optional[float] = 0.0

class DealUpdate(BaseModel): 
    title:Optional[str]=None; 
    stage_id:Optional[int]=None; 
    contact_id:Optional[int]=None; 
    new_contact_name:Optional[str]=None; 
    manager:Optional[str]=None; 
    services:Optional[List[DealServiceItem]]=None
    tax_rate: Optional[float] = None
    tax_included: Optional[bool] = None
    discount: Optional[float] = None

class TaskCreate(BaseModel): title: str; description: Optional[str]=None; due_date: Optional[date]=None; priority: Optional[str]="Обычный"; status: Optional[str]="Открыта"; assignee: Optional[str]=None
class TaskUpdate(BaseModel): title: Optional[str]=None; description: Optional[str]=None; due_date: Optional[date]=None; priority: Optional[str]=None; status: Optional[str]=None; assignee: Optional[str]=None; is_done: Optional[bool]=None
class ContactCreate(BaseModel): name: str = Field(..., min_length=1); phone: Optional[str] = None; source: Optional[str] = None
class ContactUpdate(BaseModel): name: Optional[str] = Field(None, min_length=1); phone: Optional[str] = None; source: Optional[str] = None
class ServiceCreate(BaseModel): name: str = Field(...,min_length=1); price: float; unit: str; min_volume: Optional[float]=1.0; notes: Optional[str]=None
class ServiceUpdate(BaseModel): name: Optional[str]=Field(None,min_length=1); price: Optional[float]=None; unit: Optional[str]=None; min_volume: Optional[float]=None; notes: Optional[str]=None
class EquipmentCreate(BaseModel): name: str; model: Optional[str]=None; serial: Optional[str]=None; purchase_date: Optional[date]=None; purchase_cost: Optional[float]=None; status: Optional[str]='active'; notes: Optional[str]=None; engine_hours: Optional[float]=None; fuel_norm: Optional[float]=None; last_maintenance_date: Optional[date]=None; next_maintenance_date: Optional[date]=None
class EquipmentUpdate(BaseModel): name: Optional[str]=None; model: Optional[str]=None; serial: Optional[str]=None; purchase_date: Optional[date]=None; purchase_cost: Optional[float]=None; status: Optional[str]=None; notes: Optional[str]=None; engine_hours: Optional[float]=None; fuel_norm: Optional[float]=None; last_maintenance_date: Optional[date]=None; next_maintenance_date: Optional[date]=None
class ConsumableCreate(BaseModel): name: str; unit: Optional[str] = 'шт'; stock_quantity: Optional[float] = 0.0; price: Optional[float] = 0.0; notes: Optional[str] = None
class ConsumableUpdate(BaseModel): name: Optional[str] = None; unit: Optional[str] = None; stock_quantity: Optional[float] = None; price: Optional[float] = None; notes: Optional[str] = None
class MaintenanceConsumableItem(BaseModel): consumable_id: int; quantity: float
class MaintenanceCreate(BaseModel): equipment_id: int; date: date; work_description: str; notes: Optional[str]=None; consumables: List[MaintenanceConsumableItem] = []
class MaintenanceUpdate(BaseModel): date: Optional[date]=None; work_description: Optional[str]=None; notes: Optional[str]=None; consumables: Optional[List[MaintenanceConsumableItem]] = None
class ExpenseCreate(BaseModel): name: str; amount: float; date: date; category: str
class ExpenseUpdate(BaseModel): name: Optional[str] = None; amount: Optional[float] = None; date: Optional[date] = None; category: Optional[str] = None
class TaxPaymentCreate(BaseModel): amount: float; payment_date: date


# --- Response Models to prevent serialization cycles ---
class TaxPaymentResponse(BaseModel): id: int; amount: float; payment_date: date; created_at: datetime; model_config = ConfigDict(from_attributes=True)
class EquipmentForMaintResponse(BaseModel):
    id: int; name: str
    model_config = ConfigDict(from_attributes=True)

class MaintenanceForListResponse(BaseModel):
    id: int; date: date; work_description: str; cost: Optional[float]; equipment_id: int
    equipment: EquipmentForMaintResponse
    model_config = ConfigDict(from_attributes=True)

class ConsumableForMaintResponse(BaseModel):
    id: int; name: str; unit: Optional[str]
    model_config = ConfigDict(from_attributes=True)

class MaintConsumableForDetailResponse(BaseModel):
    quantity: float; price_at_moment: float
    consumable: ConsumableForMaintResponse
    model_config = ConfigDict(from_attributes=True)

class MaintenanceDetailResponse(BaseModel):
    id: int; equipment_id: int; date: date; work_description: str; notes: Optional[str]; cost: Optional[float]
    consumables_used: List[MaintConsumableForDetailResponse]
    model_config = ConfigDict(from_attributes=True)

class EquipmentResponse(BaseModel):
    id:int; name:str; model:Optional[str]; serial:Optional[str]; purchase_date:Optional[date]; purchase_cost:Optional[float]; status:Optional[str]; notes:Optional[str]; engine_hours:Optional[float]; fuel_norm:Optional[float]; last_maintenance_date:Optional[date]; next_maintenance_date:Optional[date]
    model_config = ConfigDict(from_attributes=True)

# ── 6. FASTAPI APP ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("App starting (v11.7)...",flush=True)
    init_db_structure()
    with SessionFactory() as db: seed_initial_data(db)
    yield
    print("App shutting down.",flush=True)

app = FastAPI(title="GreenCRM API", version="11.7.0", lifespan=lifespan)
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
@app.post("/api/cache/invalidate")
def invalidate_cache(_=Depends(get_current_user)): _cache.invalidate(); return {"status":"ok"}

# --- SERVICES ---
@app.get("/api/services")
def get_services(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(Service).order_by(Service.id).all()
@app.post("/api/services", status_code=201)
def create_service(data: ServiceCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    new_item = Service(**data.model_dump()); db.add(new_item); db.commit(); db.refresh(new_item)
    _cache.invalidate("services"); return new_item
@app.patch("/api/services/{service_id}")
def update_service(service_id: int, data: ServiceUpdate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    item = db.query(Service).filter(Service.id == service_id).first()
    if not item: raise HTTPException(404, "Услуга не найдена")
    for key, value in data.model_dump(exclude_unset=True).items(): setattr(item, key, value)
    db.commit(); db.refresh(item)
    _cache.invalidate(); return item
@app.delete("/api/services/{service_id}", status_code=204)
def delete_service(service_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    if db.query(DealService).filter(DealService.service_id == service_id).count() > 0:
        raise HTTPException(400, "Нельзя удалить услугу, которая используется в сделках.")
    item = db.query(Service).filter(Service.id == service_id).first()
    if item: db.delete(item); db.commit(); _cache.invalidate()
    return None

# --- DEALS ---
@app.get("/api/deals")
def get_deals(year:Optional[int]=None,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    q=db.query(Deal).options(joinedload(Deal.contact),joinedload(Deal.stage)).order_by(Deal.created_at.desc())
    if year: q = q.filter(extract("year", Deal.deal_date) == year)
    deals_list=[{"id":d.id,"title":d.title or "","total":d.total or 0.0,"client":d.contact.name if d.contact else "","stage":d.stage.name if d.stage else "","created_at":(d.created_at or datetime.utcnow()).isoformat()} for d in q.all()]
    return {"deals": deals_list}

@app.post("/api/deals", status_code=201)
def create_deal(deal_data: DealCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    contact_id = deal_data.contact_id
    if deal_data.new_contact_name:
        new_contact = Contact(name=deal_data.new_contact_name)
        db.add(new_contact)
        db.flush()
        db.refresh(new_contact)
        contact_id = new_contact.id
    if not contact_id:
        raise HTTPException(400, "Не указан клиент")

    subtotal = 0
    service_items_for_db = []
    for item in deal_data.services:
        service = db.query(Service).filter(Service.id == item.service_id).first()
        if not service: continue
        price = service.price or 0
        subtotal += price * item.quantity
        service_items_for_db.append(DealService(service_id=service.id, quantity=item.quantity, price_at_moment=price))

    discount_percent = deal_data.discount or 0
    tax_rate_percent = deal_data.tax_rate or 0
    
    discount_amount = subtotal * (discount_percent / 100.0)
    subtotal_after_discount = subtotal - discount_amount
    
    if deal_data.tax_included:
        final_total = subtotal_after_discount
    else:
        tax_amount = subtotal_after_discount * (tax_rate_percent / 100.0)
        final_total = subtotal_after_discount + tax_amount

    new_deal = Deal(
        title=deal_data.title,
        stage_id=deal_data.stage_id,
        contact_id=contact_id,
        deal_date=datetime.utcnow(),
        manager=deal_data.manager,
        services=service_items_for_db,
        total=round(final_total, 2),
        discount=discount_percent,
        tax_rate=tax_rate_percent,
        tax_included=deal_data.tax_included
    )
    db.add(new_deal)
    db.commit()
    _cache.invalidate()
    return {"status": "ok"}

@app.get("/api/deals/{deal_id}")
def get_deal_details(deal_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    deal = db.query(Deal).options(
        joinedload(Deal.contact), 
        joinedload(Deal.services).joinedload(DealService.service)
    ).filter(Deal.id == deal_id).first()

    if not deal:
        raise HTTPException(404, "Сделка не найдена")

    services_list = []
    for ds in deal.services:
        service_info = {"quantity": ds.quantity, "price_at_moment": ds.price_at_moment}
        if ds.service:
            service_info["service"] = {"id": ds.service.id, "name": ds.service.name, "price": ds.service.price, "unit": ds.service.unit}
        else:
            service_info["service"] = {"id": -1, "name": "[Удаленная услуга]", "price": ds.price_at_moment, "unit": "?"}
        services_list.append(service_info)
    
    return {
        "id": deal.id,
        "title": deal.title,
        "total": deal.total,
        "stage_id": deal.stage_id,
        "manager": deal.manager,
        "contact": {"id": deal.contact.id, "name": deal.contact.name} if deal.contact else None,
        "services": services_list,
        "discount": deal.discount,
        "tax_rate": deal.tax_rate,
        "tax_included": deal.tax_included,
    }

@app.patch("/api/deals/{deal_id}")
def update_deal(deal_id: int, deal_data: DealUpdate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal: raise HTTPException(404, "Сделка не найдена")

    update_data = deal_data.model_dump(exclude_unset=True)

    if "new_contact_name" in update_data:
        new_contact = Contact(name=update_data["new_contact_name"])
        db.add(new_contact)
        db.flush()
        db.refresh(new_contact)
        deal.contact_id = new_contact.id
    elif "contact_id" in update_data:
        deal.contact_id = update_data["contact_id"]

    recalculate = "services" in update_data or "discount" in update_data or "tax_rate" in update_data or "tax_included" in update_data

    if "services" in update_data:
        db.query(DealService).filter(DealService.deal_id == deal_id).delete(synchronize_session=False)
        for item_data in update_data["services"]:
            item = DealServiceItem(**item_data)
            service = db.query(Service).filter(Service.id == item.service_id).first()
            if service:
                db.add(DealService(deal_id=deal_id, service_id=service.id, quantity=item.quantity, price_at_moment=(service.price or 0)))
        db.flush()
    
    if "discount" in update_data: deal.discount = update_data["discount"]
    if "tax_rate" in update_data: deal.tax_rate = update_data["tax_rate"]
    if "tax_included" in update_data: deal.tax_included = update_data["tax_included"]
    
    if recalculate:
        subtotal = sum(ds.price_at_moment * ds.quantity for ds in deal.services)
        discount_percent = deal.discount or 0
        tax_rate_percent = deal.tax_rate or 0
        discount_amount = subtotal * (discount_percent / 100.0)
        subtotal_after_discount = subtotal - discount_amount
        
        if deal.tax_included:
            final_total = subtotal_after_discount
        else:
            tax_amount = subtotal_after_discount * (tax_rate_percent / 100.0)
            final_total = subtotal_after_discount + tax_amount
        
        deal.total = round(final_total, 2)

    if "title" in update_data: deal.title = update_data["title"]
    if "stage_id" in update_data: deal.stage_id = update_data["stage_id"]
    if "manager" in update_data: deal.manager = update_data["manager"]

    db.commit()
    _cache.invalidate()
    return {"status": "ok"}

@app.delete("/api/deals/{deal_id}", status_code=204)
def delete_deal(deal_id: int, db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    deal=db.query(Deal).filter(Deal.id==deal_id).first()
    if deal: db.delete(deal); db.commit(); _cache.invalidate()
    return None

# --- CONTACTS ---
@app.get("/api/contacts")
def get_contacts(db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    return db.query(Contact).order_by(Contact.name).all()

@app.post("/api/contacts", status_code=201)
def create_contact(contact_data: ContactCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    if contact_data.phone and contact_data.phone.strip():
        existing = db.query(Contact).filter(Contact.phone == contact_data.phone).first()
        if existing: raise HTTPException(status_code=409, detail="Контакт с таким телефоном уже существует")
    new_contact = Contact(**contact_data.model_dump()); db.add(new_contact); db.commit(); db.refresh(new_contact)
    _cache.invalidate("contacts"); return new_contact

@app.patch("/api/contacts/{contact_id}")
def update_contact(contact_id: int, contact_data: ContactUpdate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact: raise HTTPException(status_code=404, detail="Контакт не найден")
    
    update_data = contact_data.model_dump(exclude_unset=True)
    if "phone" in update_data and update_data["phone"] and update_data["phone"].strip():
        existing = db.query(Contact).filter(Contact.phone == update_data["phone"], Contact.id != contact_id).first()
        if existing: raise HTTPException(status_code=409, detail="Контакт с таким телефоном уже существует")

    for key, value in update_data.items(): setattr(contact, key, value)
    
    db.commit(); db.refresh(contact)
    _cache.invalidate(); return contact

@app.delete("/api/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact: return None
    if db.query(Deal).filter(Deal.contact_id == contact_id).count() > 0:
        raise HTTPException(status_code=400, detail="Нельзя удалить контакт, к которому привязаны сделки.")
    db.delete(contact); db.commit()
    _cache.invalidate(); return None

# --- EXPENSES ---
@app.get("/api/expense-categories")
def get_expense_categories(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return db.query(ExpenseCategory).order_by(ExpenseCategory.name).all()

@app.post("/api/expenses", status_code=201)
def create_expense(data: ExpenseCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    category_name = data.category.strip()
    category = None
    if category_name:
        category = db.query(ExpenseCategory).filter(func.lower(ExpenseCategory.name) == func.lower(category_name)).first()
        if not category:
            category = ExpenseCategory(name=category_name)
            db.add(category)
            db.flush()
            _cache.invalidate("expense_categories")

    new_expense = Expense(name=data.name, amount=data.amount, date=data.date, category_id=category.id if category else None)
    db.add(new_expense); db.commit(); db.refresh(new_expense)
    _cache.invalidate(); return new_expense

@app.patch("/api/expenses/{expense_id}")
def update_expense(expense_id: int, data: ExpenseUpdate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense: raise HTTPException(404, "Расход не найден")
    
    update_data = data.model_dump(exclude_unset=True)
    if "category" in update_data:
        category_name = update_data.pop("category").strip()
        category = None
        if category_name:
            category = db.query(ExpenseCategory).filter(func.lower(ExpenseCategory.name) == func.lower(category_name)).first()
            if not category:
                category = ExpenseCategory(name=category_name)
                db.add(category); db.flush()
                _cache.invalidate("expense_categories")
        expense.category_id = category.id if category else None

    for key, value in update_data.items(): setattr(expense, key, value)
    
    db.commit(); db.refresh(expense)
    _cache.invalidate(); return expense

@app.delete("/api/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if expense: db.delete(expense); db.commit(); _cache.invalidate()
    return None

@app.get("/api/expenses")
def get_expenses(year: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(Expense).options(joinedload(Expense.category)).filter(extract("year", Expense.date) == year).order_by(Expense.date.desc())
    return [{"id": e.id, "name": e.name, "amount": e.amount, "category": e.category.name if e.category else "", "date": e.date.isoformat() if e.date else None} for e in q.all()]

# --- TAXES ---
@app.get("/api/taxes/summary")
def get_tax_summary(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    successful_stage = db.query(Stage).filter(func.lower(Stage.name).like('%успешно%'), Stage.is_final == True).first()
    if not successful_stage:
        raise HTTPException(status_code=404, detail="Не найден успешный этап воронки. Проверьте настройки.")

    successful_deals = db.query(Deal).options(joinedload(Deal.services)).filter(Deal.stage_id == successful_stage.id).all()
    
    total_tax_from_deals = 0
    for deal in successful_deals:
        subtotal = sum(ds.price_at_moment * ds.quantity for ds in deal.services)
        discount_percent = deal.discount or 0
        tax_rate_percent = deal.tax_rate or 0

        discount_amount = subtotal * (discount_percent / 100.0)
        subtotal_after_discount = subtotal - discount_amount

        tax_amount = 0
        if deal.tax_included:
            tax_base = subtotal_after_discount / (1 + tax_rate_percent / 100.0)
            tax_amount = subtotal_after_discount - tax_base
        else:
            tax_amount = subtotal_after_discount * (tax_rate_percent / 100.0)
        
        total_tax_from_deals += tax_amount

    total_payments = db.query(func.sum(TaxPayment.amount)).scalar() or 0.0
    
    tax_due = total_tax_from_deals - total_payments
    
    return {
        "total_tax_from_deals": round(total_tax_from_deals, 2),
        "total_payments": round(total_payments, 2),
        "tax_due": round(tax_due, 2)
    }

@app.get("/api/taxes/payments", response_model=List[TaxPaymentResponse])
def get_tax_payments(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    return db.query(TaxPayment).order_by(TaxPayment.payment_date.desc()).all()

@app.post("/api/taxes/payments", status_code=201, response_model=TaxPaymentResponse)
def create_tax_payment(payment_data: TaxPaymentCreate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    new_payment = TaxPayment(**payment_data.model_dump())
    db.add(new_payment)
    db.commit()
    db.refresh(new_payment)
    _cache.invalidate()
    return new_payment

# --- EQUIPMENT & MAINTENANCE ---
@app.get("/api/equipment", response_model=List[EquipmentResponse])
def get_equipment(db: DBSession=Depends(get_db), _=Depends(get_current_user)):
    return db.query(Equipment).order_by(Equipment.name).all()

@app.post("/api/equipment", status_code=201, response_model=EquipmentResponse)
def create_equipment(data: EquipmentCreate, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    new_item = Equipment(**data.model_dump()); db.add(new_item); db.commit(); db.refresh(new_item)
    _cache.invalidate("equipment"); return new_item

@app.patch("/api/equipment/{eq_id}", response_model=EquipmentResponse)
def update_equipment(eq_id: int, data: EquipmentUpdate, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    item = db.query(Equipment).filter(Equipment.id == eq_id).first()
    if not item: raise HTTPException(404, "Техника не найдена")
    for key, value in data.model_dump(exclude_unset=True).items(): setattr(item, key, value)
    db.commit(); db.refresh(item)
    _cache.invalidate(); return item

@app.delete("/api/equipment/{eq_id}", status_code=204)
def delete_equipment(eq_id: int, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    item = db.query(Equipment).filter(Equipment.id == eq_id).first()
    if item: db.delete(item); db.commit(); _cache.invalidate()
    return None

@app.get("/api/maintenance", response_model=List[MaintenanceForListResponse])
def get_all_maintenance(year: Optional[int] = None, db: DBSession=Depends(get_db), _=Depends(get_current_user)):
    q = db.query(EquipmentMaintenance).options(joinedload(EquipmentMaintenance.equipment)).order_by(EquipmentMaintenance.date.desc())
    if year: q = q.filter(extract("year", EquipmentMaintenance.date) == year)
    return q.all()

@app.get("/api/maintenance/{m_id}", response_model=MaintenanceDetailResponse)
def get_maintenance_details(m_id: int, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    m_record = db.query(EquipmentMaintenance).options(joinedload(EquipmentMaintenance.consumables_used).joinedload(MaintenanceConsumable.consumable), joinedload(EquipmentMaintenance.equipment)).filter(EquipmentMaintenance.id == m_id).first()
    if not m_record: raise HTTPException(404, "Запись о ТО не найдена")
    return m_record

@app.post("/api/maintenance", status_code=201, response_model=MaintenanceDetailResponse)
def create_maintenance_record(data: MaintenanceCreate, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    total_cost = 0
    try:
        for item_data in data.consumables:
            consumable = db.query(Consumable).filter(Consumable.id == item_data.consumable_id).with_for_update().first()
            if not consumable or consumable.stock_quantity < item_data.quantity:
                raise HTTPException(400, f"Недостаточно '{consumable.name if consumable else 'ID:'+str(item_data.consumable_id)}' на складе.")
            consumable.stock_quantity -= item_data.quantity
            total_cost += (consumable.price or 0) * item_data.quantity
        
        new_item = EquipmentMaintenance(equipment_id=data.equipment_id, date=data.date, work_description=data.work_description, notes=data.notes, cost=total_cost)
        db.add(new_item)
        db.flush()

        for item_data in data.consumables:
            consumable = db.query(Consumable).filter(Consumable.id == item_data.consumable_id).first()
            db.add(MaintenanceConsumable(maintenance_id=new_item.id, consumable_id=item_data.consumable_id, quantity=item_data.quantity, price_at_moment=(consumable.price or 0)))
        
        db.commit()
        db.refresh(new_item)
        update_equipment_last_maintenance(db, data.equipment_id)
        _cache.invalidate()
        return new_item
    except:
        db.rollback()
        raise

@app.patch("/api/maintenance/{m_id}", response_model=MaintenanceDetailResponse)
def update_maintenance_record(m_id: int, data: MaintenanceUpdate, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    try:
        m_record = db.query(EquipmentMaintenance).options(joinedload(EquipmentMaintenance.consumables_used)).filter(EquipmentMaintenance.id == m_id).first()
        if not m_record: raise HTTPException(404, "Запись о ТО не найдена")

        if data.consumables is not None:
            for old_item in m_record.consumables_used:
                consumable = db.query(Consumable).filter(Consumable.id == old_item.consumable_id).with_for_update().first()
                if consumable: consumable.stock_quantity += old_item.quantity
            
            db.query(MaintenanceConsumable).filter(MaintenanceConsumable.maintenance_id == m_id).delete(synchronize_session=False)
            db.flush()

            total_cost = 0
            for item_data in data.consumables:
                consumable = db.query(Consumable).filter(Consumable.id == item_data.consumable_id).with_for_update().first()
                if not consumable or consumable.stock_quantity < item_data.quantity:
                    raise HTTPException(400, f"Недостаточно '{consumable.name if consumable else 'ID:'+str(item_data.consumable_id)}' на складе.")
                consumable.stock_quantity -= item_data.quantity
                total_cost += (consumable.price or 0) * item_data.quantity
                db.add(MaintenanceConsumable(maintenance_id=m_id, consumable_id=item_data.consumable_id, quantity=item_data.quantity, price_at_moment=(consumable.price or 0)))
            m_record.cost = total_cost

        update_data = data.model_dump(exclude_unset=True)
        if 'date' in update_data: m_record.date = update_data['date']
        if 'work_description' in update_data: m_record.work_description = update_data['work_description']
        if 'notes' in update_data: m_record.notes = update_data['notes']
        
        db.commit()
        db.refresh(m_record)
        update_equipment_last_maintenance(db, m_record.equipment_id)
        _cache.invalidate()
        return m_record
    except:
        db.rollback()
        raise

@app.delete("/api/maintenance/{m_id}", status_code=204)
def delete_maintenance_record(m_id: int, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    try:
        item = db.query(EquipmentMaintenance).options(joinedload(EquipmentMaintenance.consumables_used)).filter(EquipmentMaintenance.id == m_id).first()
        if item:
            equipment_id = item.equipment_id
            for used in item.consumables_used:
                consumable = db.query(Consumable).filter(Consumable.id == used.consumable_id).with_for_update().first()
                if consumable: consumable.stock_quantity += used.quantity
            
            db.delete(item)
            db.commit()
            update_equipment_last_maintenance(db, equipment_id)
            _cache.invalidate()
    except:
        db.rollback()
        raise
    return None

# --- CONSUMABLES ---
@app.get("/api/consumables")
def get_consumables(db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    return db.query(Consumable).order_by(Consumable.name).all()

@app.post("/api/consumables", status_code=201)
def create_consumable(data: ConsumableCreate, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    new_item = Consumable(**data.model_dump()); db.add(new_item); db.commit(); db.refresh(new_item)
    _cache.invalidate("consumables"); return new_item

@app.patch("/api/consumables/{c_id}")
def update_consumable(c_id: int, data: ConsumableUpdate, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    item = db.query(Consumable).filter(Consumable.id == c_id).first()
    if not item: raise HTTPException(404, "Расходник не найден")
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items(): setattr(item, key, value)
    db.commit(); db.refresh(item)
    _cache.invalidate(); return item

@app.delete("/api/consumables/{c_id}", status_code=204)
def delete_consumable(c_id: int, db:DBSession=Depends(get_db), _=Depends(get_current_user)):
    if db.query(MaintenanceConsumable).filter(MaintenanceConsumable.consumable_id == c_id).count() > 0:
        raise HTTPException(400, "Нельзя удалить расходник, который используется в записях о ТО.")
    item = db.query(Consumable).filter(Consumable.id == c_id).first()
    if item: db.delete(item); db.commit(); _cache.invalidate()

# --- OTHER ---
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
    tasks_with_names = []
    for t in q.all():
        task_dict = {c.name: getattr(t, c.name) for c in t.__table__.columns}
        task_dict["assignee_name"] = users.get(t.assignee, "Не назначен")
        tasks_with_names.append(task_dict)
    return tasks_with_names

@app.post("/api/tasks", status_code=201)
def create_task(task: TaskCreate, db: DBSession = Depends(get_db), user: dict = Depends(get_current_user)):
    new_task = Task(title=task.title, description=task.description, due_date=task.due_date or (date.today() + timedelta(days=1)), priority=task.priority, status=task.status, assignee=task.assignee or user['sub'])
    db.add(new_task); db.commit(); db.refresh(new_task)
    _cache.invalidate("tasks"); return new_task

@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, task_data: TaskUpdate, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task: raise HTTPException(404, "Task not found")
    for key, value in task_data.model_dump(exclude_unset=True).items(): setattr(task, key, value)
    if task_data.is_done: task.status = "Выполнена"
    elif task_data.status == "Выполнена": task.is_done = True
    db.commit(); _cache.invalidate("tasks"); return {"status": "ok"}

@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if task: db.delete(task); db.commit(); _cache.invalidate("tasks")

@app.get("/{full_path:path}", response_class=FileResponse)
async def serve_frontend(full_path: str):
    path = f"./{full_path.strip()}" if full_path else "./index.html"
    return FileResponse(path if os.path.isfile(path) else "./index.html")

print(f"main.py (v11.7) loaded.", flush=True)
