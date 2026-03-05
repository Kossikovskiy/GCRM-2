
from dotenv import load_dotenv
load_dotenv()

import os
import secrets
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
    DateTime, Boolean, ForeignKey, Text, text, extract
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession
from pydantic import BaseModel, ConfigDict
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
TAX_RATE       = float(os.getenv("TAX_RATE", "0.06"))

# ── 2. БАЗА ДАННЫХ И МОДЕЛИ ORM ───────────────────────────────────────────────
Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(bind=engine)

class Stage(Base): __tablename__ = "stages"; id=Column(Integer,primary_key=True); name=Column(String,unique=True); order=Column(Integer,default=0); type=Column(String,default="regular"); is_final=Column(Boolean,default=False); color=Column(String,default="#6B7280"); deals=relationship("Deal",back_populates="stage")
class Contact(Base): __tablename__ = "contacts"; id=Column(Integer,primary_key=True); name=Column(String,nullable=False); phone=Column(String,unique=True); source=Column(String); deals=relationship("Deal",back_populates="contact")
class Service(Base): __tablename__ = "services"; id=Column(Integer,primary_key=True); name=Column(String); price=Column(Float); unit=Column(String); min_volume=Column(Float); notes=Column(Text)
class DealService(Base): __tablename__="deal_services"; deal_id=Column(Integer,ForeignKey("deals.id",ondelete="CASCADE"),primary_key=True); service_id=Column(Integer,ForeignKey("services.id"),primary_key=True); quantity=Column(Float); price_at_moment=Column(Float); deal=relationship("Deal",back_populates="service_links"); service=relationship("Service")
class Deal(Base): __tablename__="deals"; id=Column(Integer,primary_key=True); contact_id=Column(Integer,ForeignKey("contacts.id")); stage_id=Column(Integer,ForeignKey("stages.id")); title=Column(String); total=Column(Float,default=0.0); created_at=Column(DateTime,default=datetime.utcnow); deal_date=Column(DateTime,default=datetime.utcnow); manager=Column(String); discount=Column(Float,default=0.0); tax_rate=Column(Float,default=0.0); tax_included=Column(Boolean,default=False); contact=relationship("Contact",back_populates="deals"); stage=relationship("Stage",back_populates="deals"); service_links=relationship("DealService",back_populates="deal",cascade="all, delete-orphan")
class Task(Base): __tablename__="tasks"; id=Column(Integer,primary_key=True); title=Column(String); description=Column(Text); is_done=Column(Boolean,default=False); due_date=Column(Date); priority=Column(String,default='Обычный'); status=Column(String,default='Открыта'); assignee=Column(String)
class ExpenseCategory(Base): __tablename__="expense_categories"; id=Column(Integer,primary_key=True); name=Column(String,unique=True); expenses=relationship("Expense",back_populates="category")
class Equipment(Base): __tablename__="equipment"; id=Column(Integer,primary_key=True); name=Column(String); model=Column(String); serial=Column(String); purchase_date=Column(Date); purchase_cost=Column(Float); engine_hours=Column(Float); status=Column(String,default="active"); notes=Column(Text); fuel_norm=Column(Float); last_maintenance_date=Column(Date); next_maintenance_date=Column(Date); expenses=relationship("Expense",back_populates="equipment")
class Expense(Base): __tablename__="expenses"; id=Column(Integer,primary_key=True); date=Column(Date,default=date.today); name=Column(String); amount=Column(Float); category_id=Column(Integer,ForeignKey("expense_categories.id")); equipment_id=Column(Integer,ForeignKey("equipment.id")); category=relationship("ExpenseCategory",back_populates="expenses"); equipment=relationship("Equipment",back_populates="expenses")
class Consumable(Base): __tablename__="consumables"; id=Column(Integer,primary_key=True); name=Column(String,unique=True); unit=Column(String,default="шт"); stock_quantity=Column(Float,default=0.0); price=Column(Float,default=0.0); notes=Column(Text)
class TaxPayment(Base): __tablename__="tax_payments"; id=Column(Integer,primary_key=True); amount=Column(Float); date=Column(Date,default=date.today); note=Column(String); year=Column(Integer)

# ── 3. PYDANTIC-СХЕМЫ ──────────────────────────────────────────────────────────
# ** КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: ДОБАВЛЯЕМ `ConfigDict(from_attributes=True)` В КАЖДУЮ СХЕМУ **
class OrmSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class StageSchema(OrmSchema): id: int; name: str; order: int; type: str; is_final: bool; color: str
class ContactSchema(OrmSchema): id: int; name: str; phone: Optional[str]; source: Optional[str]
class ServiceSchema(OrmSchema): id: int; name: str; price: float; unit: str; min_volume: float; notes: Optional[str]
class EquipmentSchema(OrmSchema): id: int; name: str; model: Optional[str]; status: str; purchase_date: Optional[date]; purchase_cost: Optional[float]; engine_hours: Optional[float]; fuel_norm: Optional[float]; last_maintenance_date: Optional[date]; next_maintenance_date: Optional[date]
class ConsumableSchema(OrmSchema): id: int; name: str; unit: str; stock_quantity: float; price: float; notes: Optional[str]
class ExpenseCategorySchema(OrmSchema): id: int; name: str
class ExpenseSchema(OrmSchema): id: int; name: str; amount: float; date: date; category_name: Optional[str] = None
class TaskSchema(OrmSchema): id: int; title: str; description: Optional[str]; is_done: bool; due_date: Optional[date]; priority: str; status: str; assignee: Optional[str]; assignee_name: Optional[str]
class TaxPaymentSchema(OrmSchema): id: int; amount: float; date: date; note: Optional[str]; year: int

# ── 4. ИНИЦИАЛИЗАЦИЯ БД ───────────────────────────────────────────────────────
def init_and_seed_db():
    Base.metadata.create_all(engine)
    with SessionFactory() as s:
        if s.query(Stage).count() == 0: s.add_all([Stage(**d) for d in [{"name":"Согласовать","order":1,"color":"#3B82F6"},{"name":"Ожидание","order":2,"color":"#F59E0B"},{"name":"В работе","order":3,"color":"#EC4899"},{"name":"Успешно","order":4,"color":"#10B981","type":"success","is_final":True},{"name":"Провалена","order":5,"color":"#EF4444","type":"failed","is_final":True}]]); s.commit()
        if s.query(ExpenseCategory).count() == 0: s.add_all([ExpenseCategory(name=n) for n in ["Техника","Топливо","Расходники","Реклама","Запчасти","Прочее"]]); s.commit()

# ── 5. АВТОРИЗАЦИЯ ────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_jwks(): return httpx.get(f"https://{AUTH0_DOMAIN}/.well-known/jwks.json").json()
def decode_access_token(token:str): header=jwt.get_unverified_header(token); key=next((k for k in get_jwks()["keys"] if k["kid"]==header.get("kid")),None); return jwt.decode(token,key,algorithms=["RS256"],audience=AUTH0_AUDIENCE,issuer=f"https://{AUTH0_DOMAIN}/")
def get_current_user(req:Request): user=req.session.get("user"); 
    if not user: raise HTTPException(401)
    return user

# ── 6. FASTAPI APP ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app:FastAPI): print("App start"); init_and_seed_db(); yield; print("App down")
app=FastAPI(title="GreenCRM",version="4.0.0",lifespan=lifespan)
app.add_middleware(SessionMiddleware,secret_key=SESSION_SECRET,https_only=not (os.getenv("DEV_MODE")=="1"),same_site="lax")
app.add_middleware(CORSMiddleware,allow_origins=[APP_BASE_URL],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
def get_db(): db=SessionFactory(); 
    try: yield db
    finally: db.close()

# ── 7. AUTH ЭНДПОИНТЫ ─────────────────────────────────────────────────────────
@app.get("/api/auth/login")
def login(req:Request): state=secrets.token_urlsafe(16); req.session["oauth_state"]=state; return RedirectResponse(f"https://{AUTH0_DOMAIN}/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={CALLBACK_URL}&scope=openid%20profile%20email&audience={AUTH0_AUDIENCE}&state={state}")
@app.get("/api/auth/callback")
def callback(req:Request,code:str=None,state:str=None,error:str=None):
    if error or not code or state!=req.session.pop("oauth_state",None): return RedirectResponse(f"/?auth_error={error or 'invalid_state'}")
    with httpx.Client() as client: tokens=client.post(f"https://{AUTH0_DOMAIN}/oauth/token",json={"grant_type":"authorization_code","client_id":CLIENT_ID,"client_secret":CLIENT_SECRET,"code":code,"redirect_uri":CALLBACK_URL}).json()
    try: payload=decode_access_token(tokens["access_token"]); req.session["user"]={"sub":payload.get("sub"),"role":payload.get(ROLE_CLAIM,"user")}; return RedirectResponse("/")
    except(JWTError,KeyError) as e: raise HTTPException(401,f"Token error:{e}")
@app.get("/api/auth/logout")
def logout(req:Request): req.session.clear(); return RedirectResponse(f"https://{AUTH0_DOMAIN}/v2/logout?client_id={CLIENT_ID}&returnTo={APP_BASE_URL}")

# ── 8. CRUD ЭНДПОИНТЫ ──────────────────────────────────────────────────────────

class User(BaseModel):id:str; name:str
@app.get("/api/users",response_model=List[User])
def get_users(_=Depends(get_current_user)): return [{"id":"google-oauth2|111132204803657388744","name":"Сергей"}]
@app.get("/api/me")
def get_me(user:dict=Depends(get_current_user)): return {"username":user["sub"],"role":user["role"]}

@app.get("/api/years", response_model=List[int])
def get_years(db: DBSession = Depends(get_db), _=Depends(get_current_user)):
    years = {r[0] for r in db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM deal_date)::int FROM deals WHERE deal_date IS NOT NULL")) if r[0]}
    years.update(r[0] for r in db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM date)::int FROM expenses WHERE date IS NOT NULL")) if r[0])
    res = sorted(list(years), reverse=True)
    if not res: res = [datetime.utcnow().year]
    return res

@app.get("/api/stages",response_model=List[StageSchema])
def get_stages(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(Stage).order_by(Stage.order).all()

# СДЕЛКИ
class DealServiceSchema(BaseModel):service_id:int; quantity:float
class DealCreateUpdate(BaseModel): title:str; contact_id:Optional[int]=None; new_contact_name:Optional[str]=None; stage_id:int; manager:Optional[str]=None; services:List[DealServiceSchema]=[]; discount:float=0; tax_rate:float=0; tax_included:bool=False

@app.post("/api/deals")
def create_deal(body:DealCreateUpdate,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    contact_id=body.contact_id
    if not contact_id and body.new_contact_name: contact=db.query(Contact).filter_by(name=body.new_contact_name).first() or Contact(name=body.new_contact_name); db.add(contact); db.commit(); contact_id=contact.id
    deal_data=body.dict(exclude={"services","new_contact_name"}); deal=Deal(**deal_data,deal_date=datetime.utcnow(),contact_id=contact_id)
    subtotal=sum(db.query(Service).get(s.service_id).price*s.quantity for s in body.services); discounted=subtotal*(1-deal.discount/100); deal.total=round(discounted if deal.tax_included else discounted*(1+deal.tax_rate/100),2)
    for s in body.services: deal.service_links.append(DealService(service_id=s.service_id,quantity=s.quantity,price_at_moment=db.query(Service).get(s.service_id).price))
    db.add(deal); db.commit(); return {"id":deal.id}

@app.patch("/api/deals/{deal_id}")
def update_deal(deal_id:int,body:DealCreateUpdate,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    deal=db.query(Deal).get(deal_id); 
    if not deal: raise HTTPException(404)
    for k,v in body.dict(exclude={"services","new_contact_name"}).items(): setattr(deal,k,v)
    subtotal=sum(db.query(Service).get(s.service_id).price*s.quantity for s in body.services); discounted=subtotal*(1-deal.discount/100); deal.total=round(discounted if deal.tax_included else discounted*(1+deal.tax_rate/100),2)
    deal.service_links.clear()
    for s in body.services: deal.service_links.append(DealService(service_id=s.service_id,quantity=s.quantity,price_at_moment=db.query(Service).get(s.service_id).price))
    db.commit(); return {"ok":True}

@app.get("/api/deals")
def get_deals(year:Optional[int]=None,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    q=db.query(Deal).outerjoin(Deal.contact).outerjoin(Deal.stage).order_by(Deal.created_at.desc())
    if year: q=q.filter(extract("year",Deal.deal_date)==year)
    return {"deals":[{"id":d.id,"title":d.title,"total":d.total,"client":d.contact.name if d.contact else "","stage":d.stage.name if d.stage else "","created_at":d.created_at.isoformat()} for d in q.all()]}

@app.get("/api/deals/{deal_id}")
def get_deal_details(deal_id:int,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    deal=db.query(Deal).get(deal_id); 
    if not deal: raise HTTPException(404)
    return {"id":deal.id,"title":deal.title,"total":deal.total,"stage_id":deal.stage_id,"contact":{"id":deal.contact.id,"name":deal.contact.name} if deal.contact else None,"manager":deal.manager,"discount":deal.discount,"tax_rate":deal.tax_rate,"tax_included":deal.tax_included,"services":[{"service":sl.service.__dict__,"quantity":sl.quantity,"price_at_moment":sl.price_at_moment} for sl in deal.service_links]}

@app.delete("/api/deals/{deal_id}",status_code=204)
def delete_deal(deal_id:int,db:DBSession=Depends(get_db),_=Depends(get_current_user)): deal=db.query(Deal).get(deal_id); 
    if deal: db.delete(deal); db.commit()
@app.patch("/api/deals/{deal_id}/stage")
def update_deal_stage(deal_id:int,body:dict,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    deal,stage=db.query(Deal).get(deal_id),db.query(Stage).get(body.get("stage_id")); 
    if not deal or not stage: raise HTTPException(404)
    deal.stage_id=stage.id; db.commit(); return {"ok":True}

# ОСТАЛЬНЫЕ CRUD
@app.get("/api/tasks",response_model=List[TaskSchema])
def get_tasks(year:Optional[int]=None,is_done:Optional[bool]=None,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    q=db.query(Task).order_by(Task.due_date.asc()); users={u['id']:u['name'] for u in get_users()}
    if year: q=q.filter(extract("year",Task.due_date)==year)
    if is_done is not None: q=q.filter(Task.is_done==is_done)
    return [TaskSchema.model_validate(dict(t.__dict__, assignee_name=users.get(t.assignee))) for t in q.all()]

@app.get("/api/expenses",response_model=List[ExpenseSchema])
def get_expenses(year:Optional[int]=None,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    q=db.query(Expense).outerjoin(Expense.category).order_by(Expense.date.desc())
    if year: q=q.filter(extract("year",Expense.date)==year)
    return [ExpenseSchema(id=e.id,name=e.name,amount=e.amount,date=e.date,category_name=e.category.name if e.category else None) for e in q.all()]

@app.get("/api/expense-categories",response_model=List[ExpenseCategorySchema])
def get_expense_categories(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(ExpenseCategory).all()

@app.get("/api/equipment",response_model=List[EquipmentSchema])
def get_equipment(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(Equipment).order_by(Equipment.name).all()

@app.get("/api/services",response_model=List[ServiceSchema])
def get_services(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(Service).order_by(Service.name).all()

@app.get("/api/consumables",response_model=List[ConsumableSchema])
def get_consumables(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(Consumable).order_by(Consumable.name).all()

@app.get("/api/contacts",response_model=List[ContactSchema])
def get_contacts(db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(Contact).order_by(Contact.name).all()

class TaxPaymentCreate(BaseModel):amount:float; date:date; note:Optional[str]=None; year:int
@app.get("/api/taxes/summary")
def get_tax_summary(year:int,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    success_stage=db.query(Stage).filter(Stage.type=="success").first()
    if not success_stage: return {"balance":0,"tax_accrued":0,"paid":0}
    revenue=sum(d.total or 0 for d in db.query(Deal).filter(Deal.stage_id==success_stage.id,extract("year",Deal.deal_date)==year).all())
    tax_accrued=round(revenue*TAX_RATE,2); paid=sum(p.amount for p in db.query(TaxPayment).filter(TaxPayment.year==year).all())
    return {"revenue":round(revenue,2),"tax_accrued":tax_accrued,"paid":round(paid,2),"balance":round(tax_accrued-paid,2)}

@app.get("/api/taxes/payments",response_model=List[TaxPaymentSchema])
def get_tax_payments(year:int,db:DBSession=Depends(get_db),_=Depends(get_current_user)): return db.query(TaxPayment).filter(TaxPayment.year==year).order_by(TaxPayment.date.desc()).all()

@app.post("/api/taxes/payments",status_code=201,response_model=TaxPaymentSchema)
def create_tax_payment(body:TaxPaymentCreate,db:DBSession=Depends(get_db),_=Depends(get_current_user)):
    if body.amount<=0: raise HTTPException(400,"Сумма должна быть положительной")
    payment=TaxPayment(**body.dict()); db.add(payment); db.commit(); return payment

# ── 9. ФРОНТЕНД ───────────────────────────────────────────────────────────────
@app.get("/{full_path:path}")
async def serve_frontend(full_path:str): path=f"./{full_path.strip()}" if full_path else "./index.html"; return FileResponse(path if os.path.exists(path) and os.path.isfile(path) else "./index.html")

print("main.py (v4.0) loaded.", flush=True)
