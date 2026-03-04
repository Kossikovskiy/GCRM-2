
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
from fastapi.responses import FileResponse
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


# 2. DATABASE MODELS
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
    contact = relationship("Contact", back_populates="deals")
    stage = relationship("Stage", back_populates="deals")


# 3. AUTHENTICATION
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-80umollds5sbkqku.us.auth0.com")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://grass-crm/api")
ROLE_CLAIM = "https://grass-crm/role"
bearer = HTTPBearer(auto_error=False)

@lru_cache(maxsize=1)
def get_jwks() -> dict:
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    try:
        with httpx.Client() as client:
            resp = client.get(url, timeout=10)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Failed to fetch JWKS from Auth0: {e}") from e

def get_current_user(token: Optional[str] = Depends(bearer)) -> dict:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Auth required.")
    try:
        unverified_header = jwt.get_unverified_header(token.credentials)
        kid = unverified_header.get("kid")
        jwks = get_jwks()
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if not key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signing key not found.")
        
        payload = jwt.decode(
            token.credentials, key, algorithms=["RS256"], 
            audience=AUTH0_AUDIENCE, issuer=f"https://{AUTH0_DOMAIN}/"
        )
        return {"username": payload.get("sub", ""), "role": payload.get(ROLE_CLAIM, "user")}
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}")


# 4. FASTAPI APPLICATION
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("App is starting... (v2.0.3)", flush=True)
    # DB seeding is disabled to preserve migrated data.
    yield
    print("App is shutting down...", flush=True)

app = FastAPI(title="GreenCRM API", version="2.0.3", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

def get_db():
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()


# 5. API ENDPOINTS
@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

@app.get("/api/stages")
def get_stages(db: DBSession = Depends(get_db)):
    return db.query(Stage).order_by(Stage.order).all()

@app.get("/api/contacts")
def get_contacts(db: DBSession = Depends(get_db)):
    return db.query(Contact).order_by(Contact.name).all()

@app.get("/api/deals")
def get_deals(db: DBSession = Depends(get_db)):
    # Use outerjoin to prevent crash if a stage or contact is missing.
    deals_query = db.query(Deal).outerjoin(Deal.contact).outerjoin(Deal.stage).order_by(Deal.created_at.desc())
    deals_from_db = deals_query.all()
    
    response = []
    for d in deals_from_db:
        response.append({
            "id": d.id,
            "title": d.title or "Без названия",
            "total": d.total or 0.0,
            "client": d.contact.name if d.contact else "Нет клиента",
            "stage": d.stage.name if d.stage else "Без статуса",
            "created_at": (d.created_at or datetime.utcnow()).isoformat()
        })
    return response

# 6. FRONTEND SERVING
@app.get("/{full_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend(full_path: str):
    path = os.path.join("./", full_path if full_path else 'index.html')
    if os.path.exists(path):
        return FileResponse(path)
    return FileResponse("./index.html")

print("main.py (v2.0.3) loaded successfully.", flush=True)
