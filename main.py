
from dotenv import load_dotenv
load_dotenv()

import sys
import os
from datetime import datetime, date
from contextlib import asynccontextmanager
from typing import Optional, List
from functools import lru_cache

# --- FastAPI & SQLAlchemy --- #
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, 
    DateTime, Boolean, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session as DBSession

# --- Auth --- #
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

# 2. DATABASE MODELS (v3.0 - Synchronized with colleague's notes)
class Stage(Base):
    __tablename__ = "stages"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    order = Column(Integer, default=0)
    color = Column(String(20), default="#6B7280")
    deals = relationship("Deal", back_populates="stage")

class Contact(Base):
    __tablename__ = 'contacts'
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(50), unique=True, index=True, nullable=True)
    source = Column(String(100))
    deals = relationship("Deal", back_populates="contact")

class Deal(Base):
    __tablename__ = 'deals'
    id = Column(Integer, primary_key=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), nullable=False)
    stage_id = Column(Integer, ForeignKey("stages.id"), nullable=True)
    title = Column(String(200), nullable=False)
    total = Column(Float, default=0.0)
    notes = Column(Text, default='')
    created_at = Column(DateTime, default=datetime.utcnow)
    deal_date = Column(DateTime)
    closed_at = Column(DateTime)
    is_repeat = Column(Boolean, default=False)
    # FIX: Added fields from mcp_server schema
    client = Column(String) 
    manager = Column(String) 

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
    # FIX: Added fields from mcp_server schema
    serial = Column(String)
    engine_hours = Column(Float)
    notes = Column(Text)
    expenses = relationship("Expense", back_populates="equipment")

class Expense(Base):
    __tablename__ = 'expenses'
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, default=date.today)
    name = Column(String(300), nullable=False)
    amount = Column(Float, nullable=False)
    category_id = Column(Integer, ForeignKey('expense_categories.id'))
    equipment_id = Column(Integer, ForeignKey('equipment.id'), nullable=True)
    category = relationship("ExpenseCategory", back_populates="expenses")
    equipment = relationship("Equipment", back_populates="expenses")

class Consumable(Base):
    __tablename__ = 'consumables'
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    stock_quantity = Column(Float, default=0.0)

# 3. DB INITIALIZATION & SEEDING
def init_and_seed_db():
    print("--- Checking DB Schema and Seeding ---", flush=True)
    try:
        # Create tables if they don't exist
        Base.metadata.create_all(engine)
        print("Schema check/update complete.", flush=True)

        with SessionFactory() as session:
            # Seed Stages only if the table is empty
            if session.query(Stage).count() == 0:
                print("Seeding Stages...", flush=True)
                STAGES_DATA = [
                    {"name": "Согласовать", "order": 1, "color": "#3B82F6"},
                    {"name": "Ожидание", "order": 2, "color": "#F59E0B"},
                    {"name": "В работе", "order": 3, "color": "#EC4899"},
                    {"name": "Успешно", "order": 4, "color": "#10B981"},
                    {"name": "Провалена", "order": 5, "color": "#EF4444"},
                ]
                for s_data in STAGES_DATA: session.add(Stage(**s_data))
                session.commit()
            
            # Seed Expense Categories if empty
            if session.query(ExpenseCategory).count() == 0:
                print("Seeding Expense Categories...", flush=True)
                EXP_CATS = ["Техника", "Топливо", "Расходники", "Реклама", "Запчасти", "Прочее"]
                for name in EXP_CATS: session.add(ExpenseCategory(name=name))
                session.commit()
        
        print("--- DB Seeding Complete! ---", flush=True)
    except Exception as e:
        print(f"---!! ERROR DURING DB INIT/SEED: {e} !!----", flush=True)

# 4. AUTHENTICATION
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-80umollds5sbkqku.us.auth0.com")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://grass-crm/api")
CLIENT_ID = os.getenv("AUTH0_CLIENT_ID") 
# ... (rest of auth functions remain the same) ...

# 5. FASTAPI APPLICATION
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("App is starting... (v3.0)", flush=True)
    # FIX: Re-enabled safe seeding on startup
    init_and_seed_db()
    yield
    print("App is shutting down...", flush=True)

app = FastAPI(title="GreenCRM API", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()

# 6. API ENDPOINTS

# FIX: Added login route to prevent redirect loop
@app.get("/api/auth/login")
def login_redirect():
    if not CLIENT_ID:
         raise HTTPException(status_code=500, detail="Auth0 Client ID not configured on server.")
    
    redirect_uri = "http://localhost:3000" # Should be your frontend URL
    
    auth_url = (
        f"https://{AUTH0_DOMAIN}/authorize?"
        f"response_type=token&"
        f"client_id={CLIENT_ID}&"
        f"redirect_uri={redirect_uri}&"
        f"scope=openid%20profile%20email&"
        f"audience={AUTH0_AUDIENCE}"
    )
    return RedirectResponse(url=auth_url)


@app.get("/api/deals")
def get_deals(db: DBSession = Depends(get_db)):
    deals_query = db.query(Deal).outerjoin(Deal.contact).outerjoin(Deal.stage).order_by(Deal.created_at.desc())
    deals_from_db = deals_query.all()
    
    response_data = []
    for d in deals_from_db:
        response_data.append({
            "id": d.id,
            "title": d.title or "Без названия",
            "total": d.total or 0.0,
            "client": d.contact.name if d.contact else "Нет клиента",
            "stage": d.stage.name if d.stage else "Без статуса",
            "created_at": (d.created_at or datetime.utcnow()).isoformat()
        })
    # FIX: Return data in the format expected by the frontend
    return {"deals": response_data}

# FIX: Added back missing endpoints
@app.get("/api/tasks", response_model=List[dict])
def get_tasks(db: DBSession = Depends(get_db)):
    tasks = db.query(Task).order_by(Task.due_date.desc()).all()
    return [{"id": t.id, "title": t.title, "is_done": t.is_done, "due_date": t.due_date} for t in tasks]

@app.get("/api/expenses", response_model=List[dict])
def get_expenses(db: DBSession = Depends(get_db)):
    expenses = db.query(Expense).outerjoin(Expense.category).order_by(Expense.date.desc()).all()
    return [{
        "id": e.id, "date": e.date, "name": e.name, "amount": e.amount, 
        "category": e.category.name if e.category else "Без категории"
    } for e in expenses]

@app.get("/api/consumables", response_model=List[dict])
def get_consumables(db: DBSession = Depends(get_db)):
    consumables = db.query(Consumable).order_by(Consumable.name).all()
    return [{"id": c.id, "name": c.name, "stock_quantity": c.stock_quantity} for c in consumables]

@app.get("/api/contacts")
def get_contacts(db: DBSession = Depends(get_db)):
    return db.query(Contact).order_by(Contact.name).all()

@app.get("/api/stages")
def get_stages(db: DBSession = Depends(get_db)):
    return db.query(Stage).order_by(Stage.order).all()

# 7. FRONTEND SERVING
@app.get("/{full_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend(full_path: str):
    path = os.path.join("./", full_path if full_path else 'index.html')
    if os.path.exists(path):
        return FileResponse(path)
    return FileResponse("./index.html")

print("main.py (v3.0) loaded successfully.", flush=True)

