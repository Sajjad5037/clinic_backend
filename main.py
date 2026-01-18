from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import DateTime, Float
from sqlalchemy.dialects.postgresql import ARRAY
from fastapi import Form
from PyPDF2 import PdfReader
from sklearn.metrics.pairwise import cosine_similarity



import hashlib
import numpy as np
from datetime import date
from starlette.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String,func,ForeignKey,Boolean,Text,text,Date,Sequence
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.dialects.postgresql import UUID,JSONB
import uvicorn
import openai
import xmlrpc.client
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
from typing import List,Dict,Set,Optional
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect,Request,Body,Response,Query,UploadFile,File
import json
import time
import uuid  # For generating unique tokens
import os
from starlette.middleware.sessions import SessionMiddleware
from datetime import datetime, timedelta,timezone
import traceback
import logging
from uuid import uuid4
from typing import Set
from openai import OpenAI
from sqlalchemy.orm import relationship
from fastapi.responses import StreamingResponse
import qrcode
import io
import shutil
import pytesseract
from datetime import datetime, timedelta
import psycopg2



from PIL import Image, ImageDraw, ImageFont
import boto3
#google cloud credentials
# Google Cloud Storage configuration


# Fetch the API key from environment variables
openai.api_key = os.getenv("OPENAI_API_KEY_S")# for rafis kitchen
openai_api_key = os.getenv("OPENAI_API_KEY_S")
# In-memory system prompt cache (per user)
system_prompt_cache = {}

# Define how long a session lasts
SESSION_DURATION = timedelta(minutes=30)
# Check if API key is set
if not openai_api_key:
    print("Error: OPENAI_API_KEY_S is not set in environment variables.")

# Initialize OpenAI client
client = OpenAI(api_key=openai_api_key)
secret_key = os.getenv("SESSION_SECRET_KEY", "fallback-secret-for-dev")
vector_stores = {}

# AWS S3 Configuration (Replace with your credentials)
AWS_ACCESS_KEY_ID = "your-access-key-id"
AWS_SECRET_ACCESS_KEY = "your-secret-access-key"
AWS_REGION = "your-region"  # e.g., "us-east-1"
BUCKET_NAME = "your-bucket-name"

# Allowed file extensions
ALLOWED_EXTENSIONS = {"pdf"}

# Initialize the S3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
)



app = FastAPI()

session_states = {}
clients=[]
Base = declarative_base()
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

class WhatsAppDocument(Base):
    __tablename__ = "whatsapp_documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    title = Column(String)
    filename = Column(String)

    # NEW
    pdf_name = Column(String)  

    # REQUIRED FIELD
    content = Column(Text, nullable=False)   # <-- ADD THIS

class WhatsAppDocumentEmbedding(Base):
    __tablename__ = "whatsapp_document_embeddings"  # ✅ singular "embedding"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("whatsapp_documents.id"), index=True)
    chunk_index = Column(Integer)
    content = Column(Text)
    embedding = Column(ARRAY(Float))  # already present

    # NEW metadata fields:
    page_number = Column(Integer)     # which page of the PDF this chunk came from
    pdf_name = Column(String)         # helpful for source display
    
class ChatRequest_new(BaseModel):
    message: str
    public_token: str
    chat_access_token: str

class KnowledgeBase(Base):
    __tablename__ = "knowledgebases"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)  # associate with doctor
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WhatsAppKnowledgeBase(Base):
    __tablename__ = "WhatsAppknowledgebases"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)        # associate with doctor
    phone_number = Column(String(15), nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    

class ChatRequestRK(BaseModel): # for Rafis Kitchen
    message: str
class UserData(BaseModel):
    name: str
    email: str
    login: Optional[str] = None  # Make it optional
    role: str
    clinic_name: str


class PDFFile(Base):
    __tablename__ = "pdfs"
    id = Column(Integer, primary_key=True, index=True)
    pdf_url = Column(String, unique=True)

class ChatRequest(BaseModel):
    message: str
    user_id: int
# WebSocket connection manager to handle multiple clients... it is responsible for connecting, disconnecting and broadcasting messages
class ConnectionManager:
    def __init__(self):
        # Map session_token → set of websocket connections
        self.sessions: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_token: str):
        await websocket.accept()

        if session_token not in self.sessions:
            self.sessions[session_token] = set()

        self.sessions[session_token].add(websocket)

        print(f"[CONNECT] Session {session_token}: {len(self.sessions[session_token])} clients connected.")

    def disconnect(self, websocket: WebSocket, session_token: str):
        if session_token in self.sessions:
            session_set = self.sessions[session_token]

            if websocket in session_set:
                session_set.remove(websocket)

            if not session_set:
                del self.sessions[session_token]

        print(f"[DISCONNECT] Session {session_token}: {len(self.sessions.get(session_token, []))} clients remaining.")

    async def broadcast_to_session(self, session_token: str, message: dict):
        # If no session connections → nothing to send
        if session_token not in self.sessions:
            print(f"[BROADCAST] No clients for session {session_token}")
            return

        serialized = json.dumps(message)
        dead_connections = []

        # Send message to all clients in this session
        for ws in self.sessions[session_token]:
            try:
                await ws.send_text(serialized)
            except Exception as e:
                print(f"[ERROR] Failed to send to a client: {e}")
                dead_connections.append(ws)

        # Clean dead connections
        for ws in dead_connections:
            self.sessions[session_token].remove(ws)

        print(f"[BROADCAST] Sent to {len(self.sessions.get(session_token, []))} clients in session {session_token}")

    async def broadcast(self, message: dict):
        # Send to every client in every session
        serialized = json.dumps(message)

        for session_token, ws_set in self.sessions.items():
            for ws in ws_set:
                try:
                    await ws.send_text(serialized)
                except:
                    pass

class LogoutRequest(BaseModel):
    resetAverageInspectionTime: bool = True


# Instantiate the manager globally
manager = ConnectionManager()
# Server-side state for real-time features
class DashboardState:
    def __init__(self):
        self.sessions = {}  # Store state per session_token

    # ------------------------------------
    # GET OR CREATE SESSION
    # ------------------------------------
    def get_session(self, session_token):
        if session_token not in self.sessions:
            self.sessions[session_token] = {
                "doctor_name": None,
                "patients": [],
                "current_patient": None,
                "inspection_times": [],  # stores finished inspection durations
                "start_time": None,      # when doctor started inspecting current patient
                "notices": [],
                "added_times": []        # timestamp when each patient was added to queue
            }
        return self.sessions[session_token]

    # ------------------------------------
    # GET TIMESTAMP WHEN PATIENT WAS ADDED
    # ------------------------------------
    def get_patient_timestamp(self, session_token, index):
        session = self.get_session(session_token)
        added_times = session.get("added_times", [])
        if index < len(added_times):
            return added_times[index]
        return time.time()

    # ------------------------------------
    # ADD NEW PATIENT
    # ------------------------------------
    def add_patient(self, session_token, patient_name: str):
        session = self.get_session(session_token)

        session["patients"].append(patient_name)
        session["added_times"].append(time.time())

        # If this is the first patient, doctor begins inspection now
        if not session.get("current_patient"):
            session["current_patient"] = patient_name
            session["start_time"] = time.time()

    # ------------------------------------
    # MARK PATIENT AS DONE
    # ------------------------------------
    def mark_as_done(self, session_token):
        session = self.get_session(session_token)
    
        current = session["current_patient"]
        if not current:
            return
    
        now = time.time()
        start_time = session.get("start_time")
    
        # ------------------------------------------
        # Record inspection duration WITH BASELINE
        # ------------------------------------------
        if start_time:
            duration = now - start_time
    
            # If this is the first inspection, mix with baseline 300 sec
            if not session["inspection_times"]:
                session["inspection_times"] = [300]   # baseline for averaging
    
            session["inspection_times"].append(duration)
            session["inspection_times"] = session["inspection_times"][-10:]  # keep last 10
    
            # Recalculate average inspection time
            avg = sum(session["inspection_times"]) / len(session["inspection_times"])
            session["average_inspection_time"] = max(avg, 60)
        else:
            # No inspection happened (possible on reset)
            pass
    
        # ------------------------------------------
        # Remove current patient + their timestamp
        # ------------------------------------------
        if session["patients"]:
            session["patients"].pop(0)
    
        if session["added_times"]:
            session["added_times"].pop(0)
    
        # ------------------------------------------
        # Move to next patient
        # ------------------------------------------
        if session["patients"]:
            session["current_patient"] = session["patients"][0]
            session["start_time"] = time.time()  # doctor starts next inspection
        else:
            session["current_patient"] = None
            session["start_time"] = None

    # ------------------------------------
    # COMPUTE AVERAGE INSPECTION TIME
    # ------------------------------------
    def get_average_time(self, session_token):
        session = self.get_session(session_token)
        times = session["inspection_times"]

        if not times:
            return 300  # default = 5 minutes

        avg = sum(times) / len(times)

        # Ensure minimum realistic inspection time (avoid 0 or unrealistic spikes)
        return max(int(avg), 60)

    # ------------------------------------
    # PUBLIC STATE (FOR PATIENT VIEW)
    # ------------------------------------
    def get_public_state(self, session_token):
        session = self.get_session(session_token)
        return {
            "patients": session["patients"],
            "currentPatient": session["current_patient"],
            "averageInspectionTime": self.get_average_time(session_token),
        }

    # ------------------------------------
    # RESET SESSION
    # ------------------------------------
    def reset_all(self, session_token):
        session = self.get_session(session_token)
        session["patients"] = []
        session["current_patient"] = None
        session["inspection_times"] = []
        session["start_time"] = None
        session["added_times"] = []
        # notices remain so the doctor doesn’t lose them

    # ------------------------------------
    # NOTICES
    # ------------------------------------
    def add_notice(self, session_token, notice: str):
        session = self.get_session(session_token)
        session["notices"].append(notice)

    def get_notices(self, session_token):
        session = self.get_session(session_token)
        return session["notices"]

    def remove_notice(self, session_token, index: int):
        session = self.get_session(session_token)
        if 0 <= index < len(session["notices"]):
            del session["notices"][index]



class OrderManagerState:
    def __init__(self):
        self.sessions = {}  # Store session-specific state

    def get_session(self, session_token):
        if session_token not in self.sessions:
            # Create a new session if the token is not found
            self.sessions[session_token] = {
                "preparingList": [],
                "servingList": [],
                "notices": [],
                "orderList": [],
                "cartList": []
            }
        return self.sessions[session_token]
    def add_item(self, session_token, item: str):
        """Adds a trimmed item to the preparing list if it's not empty."""
        session = self.get_session(session_token)
        trimmed_item = item.strip()
        if trimmed_item:
            session["preparingList"].append(trimmed_item)

    def mark_done(self, session_token, selected_index: int):
        """
        Moves an item from the preparing list to the serving list using the provided index.
        This is similar to handleDone in your JavaScript code.
        """
        session = self.get_session(session_token)
        if selected_index is None or not (0 <= selected_index < len(session["preparingList"])):
            return
        
        # Move the selected item from preparingList to servingList.
        item = session["preparingList"].pop(selected_index)
        session["servingList"].append(item)

    def mark_served(self, session_token, selected_index: int):
        """
        Removes an item from the serving list using the provided index.
        This is similar to handleServed in your JavaScript code.
        """
        session = self.get_session(session_token)
        if selected_index is None or not (0 <= selected_index < len(session["servingList"])):
            return
        
        session["servingList"].pop(selected_index)

    def add_notice(self, session_token, notice: str):
        """Adds a trimmed notice to the notice board if it's not empty."""
        session = self.get_session(session_token)
        trimmed_notice = notice.strip()
        if trimmed_notice:
            session["notices"].append(trimmed_notice)
    
    def get_public_state(self, session_token):
        session = self.get_session(session_token)
        return {
            "preparingList": session["preparingList"],
            "servingList": session["servingList"],
            "notices": session["notices"]
        }

    def remove_notice(self, session_token, index: int):
        """Removes a notice from the notice board by index."""
        session = self.get_session(session_token)
        if 0 <= index < len(session["notices"]):
            session["notices"].pop(index)

class SchoolState:
    def __init__(self):
        self.sessions = {}  # Store session-specific state

    def get_session(self, session_token):
        if session_token not in self.sessions:
            # Create a new session if the token is not found
            self.sessions[session_token] = { 
                "notices": []
            }
        return self.sessions[session_token]

    
    def add_notice(self, session_token, notice: str):
        """Adds a trimmed notice to the notice board if it's not empty."""
        session = self.get_session(session_token)
        if notice:  # Ensure it's not an empty string
            session["notices"].append(notice)
    def get_public_state(self, session_token):
        session = self.get_session(session_token)
        return {            
            "notices": session["notices"]
        }

    def remove_notice(self, session_token, index: int):
        """Removes a notice from the notice board by index."""
        session = self.get_session(session_token)
        if 0 <= index < len(session["notices"]):
            session["notices"].pop(index)

class RealEstateState:
    def __init__(self):
        self.sessions = {}  # Store session-specific state

    def get_session(self, session_token):
        if session_token not in self.sessions:
            # Create a new session if the token is not found
            self.sessions[session_token] = { 
                "notices": []
            }
        return self.sessions[session_token]

    
    def add_notice(self, session_token, notice: str):
        """Adds a trimmed notice to the notice board if it's not empty."""
        session = self.get_session(session_token)
        trimmed_notice = notice.strip()
        if trimmed_notice:
            session["notices"].append(trimmed_notice)
    
    def get_public_state(self, session_token):
        session = self.get_session(session_token)
        return {            
            "notices": session["notices"]
        }

    def remove_notice(self, session_token, index: int):
        """Removes a notice from the notice board by index."""
        session = self.get_session(session_token)
        if 0 <= index < len(session["notices"]):
            session["notices"].pop(index)


# Global state instance
OrderManager_state=OrderManagerState()
state = DashboardState()
public_manager = ConnectionManager() # Add a separate manager for public connections
School_state=SchoolState()
RealEstate_state=RealEstateState()
"""
class Patient(Base): #base is the object that contains the meta data of the database models. this helps map the class to a table in the database
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

class PatientCreate(BaseModel):
    name: str

class PatientResponse(PatientCreate):
    id: int
"""
class Doctor(Base):
    __tablename__ = "doctors"

    # Manually creating the sequence to control when the id is auto-generated
    id = Column(Integer, Sequence('doctor_id_seq'), primary_key=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password = Column(Text, nullable=False)  # Store hashed passwords efficiently
    name = Column(String, nullable=False)
    specialization = Column(String, nullable=False)
    
    # Define relationships
    
    railway_resource_usage = relationship("RailwayResourceUsageModel", back_populates="doctor", cascade="all, delete")
    sessions = relationship("SessionModel", back_populates="doctor", cascade="all, delete")
    

class SessionModel(Base):  # Handles authentication sessions
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)

    session_token = Column(UUID(as_uuid=True), unique=True, index=True, default=uuid.uuid4)
    public_token = Column(UUID(as_uuid=True), unique=True, index=True, default=uuid.uuid4)

    doctor_id = Column(Integer, ForeignKey("doctors.id", ondelete="CASCADE"))
    is_authenticated = Column(Boolean, default=False)

    # 🔐 NEW — Password protection for the public chatbot link
    require_password = Column(Boolean, default=False)
    access_password = Column(String, nullable=True)  # hashed password

    doctor = relationship("Doctor", back_populates="sessions")


class APIUsageModel(Base):
    __tablename__ = "api_usage"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, nullable=False)

    # Which API they used
    request_type = Column(String(50), nullable=False)      # rag_chat, pdf_upload, embeddings

    # How much OpenAI cost was consumed
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)

    # When it happened
    timestamp = Column(DateTime, default=datetime.utcnow)
    
class RailwayResourceUsageModel(Base):  # Tracks Railway resource usage per doctor
    __tablename__ = "railway_resource_usage"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    resource_type = Column(String(50), nullable=False)  # e.g., "database", "API requests"
    usage_count = Column(Integer, default=1)  # Tracks number of times a resource is used
    date = Column(Date, default=func.current_date())  # Logs daily usage


    doctor = relationship("Doctor", back_populates="railway_resource_usage")

class NoticesModel(Base):
    __tablename__ = "new_notices"

    session_token = Column(UUID(as_uuid=True), ForeignKey("sessions.session_token"), primary_key=True)
    notices = Column(JSONB, nullable=False)  # Store notices as JSON    

# Pydantic models for API validation
class DoctorCreate(BaseModel):  # Used to create doctors
    
    username: str
    password: str
    name: str
    specialization: str

class DoctorUpdate(BaseModel):  # Used to update doctor details
    username: str
    password: Optional[str] = None  # Optional password update
    name: str
    specialization: str

class DoctorResponse(BaseModel):  # Used for API responses
    id: int
    username: str
    name: str
    specialization: str

    class Config:
        orm_mode = True  # Enables ORM compatibility
"""
class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    name = Column(String)
    specialization = Column(String)

class SessionModel(Base):#used for creating database tables and performing CURD (create,update,read and delete) operations
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_token = Column(String, unique=True, index=True)
    session_token = Column(UUID(as_uuid=True), unique=True, index=True, default=uuid.uuid4)
    doctor_id = Column(Integer, ForeignKey("doctors.id"))
    is_authenticated = Column(Boolean, default=False)

class DoctorCreate(BaseModel): # for validating API request data befor storing in the database
    id: int
    username: str
    password: str
    name: str
    specialization: str

class DoctorUpdate(BaseModel):
    username: str
    password: str | None = None  # Optional password update
    name: str
    specialization: str

class DoctorResponse(BaseModel):
    id: int
    username: str
    name: str
    specialization: str
"""

# Allow Firebase frontend and local development
origins = [
    "https://clinic-management-system-27d11.web.app",
    "http://localhost:3000",
    "https://chat-for-me-ai-login.vercel.app",
    "https://class-management-system-new.web.app",
    "https://sajjadalinoor.vercel.app",
    "https://hospital-management-sys-pk.web.app",
    "https://rafis-kitchen.vercel.app",
    "https://ai-social-campaign.vercel.app",
    "https://ibne-sina.vercel.app",
    "https://a-level-exam-preparation.vercel.app",
    "https://anz-way.vercel.app",          # newly added frontend
    "https://krish-chat-bot.vercel.app",   # added URL
    "https://chat-for-me-ai.web.app",      # newly added Firebase hosting
    "https://chat-for-me-ai.vercel.app",   # newly added
    "https://chat-for-me-ai-login.vercel.app"  # newly added login frontend
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Allow specific origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all headers
)

app.add_middleware(SessionMiddleware, secret_key=secret_key)

# Load environment variables
#DATABASE_URL = os.getenv("PostgreSQL_database")  # Use PostgreSQL instead of SQLite
DATABASE_URL = os.getenv("DATABASE_URL")


ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "sajjad")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "shuwafF2016")

# Initialize PostgreSQL database engine
engine = create_engine(DATABASE_URL)  # Removed SQLite-specific arguments

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")



def get_cached_system_prompt(user_id):
    now = datetime.utcnow()
    entry = system_prompt_cache.get(user_id)

    if entry:
        timestamp, prompt = entry
        if now - timestamp < SESSION_DURATION:
            return prompt

    return None


def set_cached_system_prompt(user_id, prompt):
    system_prompt_cache[user_id] = (datetime.utcnow(), prompt)

def create_admin(db: Session):
    """Ensure an admin user exists in the database."""

    # ✅ Ensure the sequence exists
    try:
        result = db.execute(text("SELECT 1 FROM pg_class WHERE relkind = 'S' AND relname = 'doctors_id_seq';"))
        if result.scalar() is None:
            print("🔧 Creating missing sequence: doctors_id_seq")
            db.execute(text("CREATE SEQUENCE doctors_id_seq START 1;"))
            db.commit()
    except ProgrammingError as e:
        print(f"❌ Failed checking/creating sequence: {e}")
        db.rollback()

    # ✅ Create or update admin
    admin = db.query(Doctor).filter_by(username=ADMIN_USERNAME).first()

    if not admin:
        admin = Doctor(
            username=ADMIN_USERNAME,
            password=pwd_context.hash(ADMIN_PASSWORD),
            name="Sajjad Ali Noor",
            specialization="Administrator"
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    else:
        admin.password = pwd_context.hash(ADMIN_PASSWORD)
        db.commit()

    print("✅ Admin account created/updated successfully.")

    # ✅ Reset sequence
    db.execute(text("SELECT setval('doctors_id_seq', (SELECT MAX(id) FROM doctors), false);"))
    db.commit()

# ✅ Run admin creation after defining everything
with SessionLocal() as db:
    create_admin(db)

# Pydantic model for login requests
class LoginRequest(BaseModel):
    username: str
    password: str
    

# Dependency to get DB session
def get_db():
    """Yield a database session, ensuring it's closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
"""
def create_admin(db: Session):
    admin_username = "sajjad"
    admin = db.query(Doctor).filter(Doctor.username == admin_username).first()
    intended_password = "shuwaf123"
    
    if not admin:
        admin = Doctor(
            username=admin_username,
            password=pwd_context.hash(intended_password),
            name="Sajjad Ali Noor",
            specialization="Administrator"
        )
        db.add(admin)
    else:
        admin.password = pwd_context.hash(intended_password)
    db.commit()
    db.refresh(admin)
    print("Admin account created/updated successfully.")
    print(f"Stored hash: {repr(admin.password)}")


# Database setup
DATABASE_URL = "sqlite:///./clinic.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()
create_admin(db)
db.close()




# Create database tables
Base.metadata.create_all(bind=engine)

# Ensure Admin exists


# Pydantic model for login requests
class LoginRequest(BaseModel):
    username: str
    password: str

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
"""


def allowed_file(filename: str) -> bool:
    """Check if the file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def chunk_text(text, chunk_size=500, overlap=50):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def embed_texts(texts):
    """Return list of embeddings for a list of texts using OpenAI embeddings"""
    response = client.embeddings.create(
        model="text-embedding-3-large",   # MUST MATCH UPLOAD ENDPOINT
        input=texts
    )
    return [np.array(e.embedding) for e in response.data]

@app.get("/api/whatsapp-knowledge-base/list")
def list_documents(user_id: int, db: Session = Depends(get_db)):
    print("\n========== LIST DOCUMENTS ==========")
    print("User ID:", user_id)

    docs = db.query(WhatsAppDocument).filter(
        WhatsAppDocument.user_id == user_id
    ).order_by(WhatsAppDocument.id.desc()).all()

    results = [
        {
            "id": doc.id,
            "title": doc.title or f"Document {doc.id}",
            "filename": doc.filename
        }
        for doc in docs
    ]

    print(f"[DEBUG] Found {len(results)} document(s) for user {user_id}")

    return {"documents": results}

def register_api_usage(db, doctor_id: int, request_type: str):
    new_row = APIUsageModel(
        doctor_id=doctor_id,
        request_type=request_type,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost_usd=0.0
    )

    db.add(new_row)
    db.commit()

def register_detailed_usage(
    db, doctor_id, request_type,
    prompt_tokens, completion_tokens,
    total_tokens, cost_usd
):
    # Apply 10× markup (your charge)
    platform_cost = cost_usd * 10

    row = APIUsageModel(
        doctor_id=doctor_id,
        request_type=request_type,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=platform_cost,   # save your marked-up cost
    )

    db.add(row)
    db.commit()
    
@app.post("/api/rag-chat")
def rag_chat(request: ChatRequest_new, db: Session = Depends(get_db)):
    print("\n========== /api/rag-chat CALLED ==========")
    print("User Message:", request.message)
    print("Public Token:", request.public_token)

    # ------------------ 1. VALIDATE SESSION ------------------
    session_row = (
        db.query(SessionModel)
        .filter(SessionModel.public_token == request.public_token)
        .first()
    )

    if not session_row:
        raise HTTPException(status_code=404, detail="Invalid public token")

    doctor_id = session_row.doctor_id
    print("✅ Session OK → doctor_id:", doctor_id)

    # ------------------ 2. LOAD EMBEDDINGS ------------------
    stored_embeddings = (
        db.query(WhatsAppDocumentEmbedding)
        .join(WhatsAppDocument, WhatsAppDocumentEmbedding.document_id == WhatsAppDocument.id)
        .filter(WhatsAppDocument.user_id == doctor_id)
        .all()
    )

    if not stored_embeddings:
        return {"reply": "No knowledge base uploaded for this doctor."}

    print(f"📊 Loaded {len(stored_embeddings)} embedding chunks")

    # ------------------ 3. EMBED USER QUERY ------------------
    query_embedding = embed_texts([request.message])[0]
    print(f"✨ Query embedding dim = {len(query_embedding)}")

    # ------------------ 4. ROBUST COSINE SIMILARITY ------------------
    def cosine_sim(a, b):
        a = np.array(a)
        b = np.array(b)

        if a.shape[0] != b.shape[0]:
            print(f"❌ Dimension mismatch: query={a.shape[0]}, stored={b.shape[0]}")
            return None

        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom == 0:
            return None

        return float(np.dot(a, b) / denom)

    scored = []
    for emb in stored_embeddings:
        sim = cosine_sim(query_embedding, emb.embedding)
        if sim is None:
            continue
        scored.append({
            "sim": sim,
            "content": emb.content,
            "pdf_name": emb.pdf_name,
            "page_number": emb.page_number,
        })

    if not scored:
        return {"reply": "Embedding mismatch detected. Please re-upload your documents to refresh embeddings."}

    scored.sort(key=lambda x: x["sim"], reverse=True)
    top_chunks = scored[:5]

    # ------------------ 5. BUILD CONTEXT ------------------
    context_parts = []
    for c in top_chunks:
        citation = f"[Source: {c['pdf_name']} | Page {c['page_number']}]"
        context_parts.append(f"{citation}\n{c['content']}")

    context = "\n\n".join(context_parts)

    # ------------------ 6. GPT PROMPT ------------------
    prompt = f"""
You are a strict RAG assistant.

RULES:
- Only use the provided context.
- Cite PDF and page number.
- No guessing or hallucination.

CONTEXT:
{context}

USER QUESTION:
{request.message}
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    usage = completion.usage
    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    total_tokens = usage.total_tokens

    prompt_cost = prompt_tokens * 0.000000150
    completion_cost = completion_tokens * 0.000000600
    total_cost = prompt_cost + completion_cost

    register_detailed_usage(
        db=db,
        doctor_id=doctor_id,
        request_type="rag_chat",
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cost_usd=total_cost
    )

    reply = completion.choices[0].message.content

    return {
        "reply": reply,
        "sources": [
            {
                "pdf_name": c["pdf_name"],
                "page_number": c["page_number"],
                "similarity": c["sim"]
            }
            for c in top_chunks
        ],
    }



# ------------------ FALLBACK GPT ------------------
def fallback_gpt(message: str):
    print("⚠️ Fallback GPT used")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": message}]
    )
    return response.choices[0].message["content"]

@app.post("/api/knowledge-base/upload")
async def upload_pdf(
    user_id: int = Form(...),  # Read doctor/user ID from frontend
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    print(f"[DEBUG] Received upload request: user_id={user_id}, filename={file.filename}, content_type={file.content_type}")
    
    # Extract text from PDF
    try:
        file_bytes = await file.read()
        print(f"[DEBUG] Read {len(file_bytes)} bytes from uploaded file")
        reader = PdfReader(io.BytesIO(file_bytes))
        text = ""
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            print(f"[DEBUG] Page {i+1}: extracted {len(page_text)} characters")
            text += page_text
    except Exception as e:
        print(f"[ERROR] Failed to read PDF: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to read PDF: {e}")

    if not text.strip():
        print("[WARNING] PDF contains no readable text")
        raise HTTPException(status_code=400, detail="PDF contains no readable text")

    # Overwrite existing KB for the user if it exists
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.user_id == user_id).first()
    if kb:
        print(f"[DEBUG] Overwriting existing knowledge base for user_id={user_id}, kb_id={kb.id}")
        kb.content = text
    else:
        kb = KnowledgeBase(user_id=user_id, content=text)
        db.add(kb)

    db.commit()
    db.refresh(kb)
    print(f"[DEBUG] Knowledge base saved: id={kb.id}, user_id={kb.user_id}, content_length={len(text)}")

    # ----- Recreate temporary vector store -----
    if user_id in vector_stores:
        print(f"[DEBUG] Deleting existing temporary vector store for user_id={user_id}")
        del vector_stores[user_id]

    chunks = chunk_text(kb.content, chunk_size=500, overlap=50)
    embeddings = embed_texts(chunks)
    vector_stores[user_id] = {"chunks": chunks, "embeddings": np.array(embeddings)}
    print(f"[DEBUG] New vector store created for user_id={user_id} with {len(chunks)} chunks")

    return {"knowledge_base_id": kb.id, "message": "PDF content saved successfully and vector store rebuilt."}

"""
@app.post("/api/whatsapp-knowledge-base/upload")
async def upload_pdf(
    user_id: int = Form(...),            # Required (sent by frontend)
    file: UploadFile = File(...),        # Required (sent by frontend)
    db: Session = Depends(get_db)
):
    print(f"[DEBUG] Upload request: user_id={user_id}, filename={file.filename}")

    # ----- Extract PDF text -----
    try:
        file_bytes = await file.read()
        print(f"[DEBUG] Read {len(file_bytes)} bytes from file")

        reader = PdfReader(io.BytesIO(file_bytes))
        text = ""

        for i, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            print(f"[DEBUG] Page {i+1}: extracted {len(page_text)} characters")
            text += page_text

    except Exception as e:
        print(f"[ERROR] PDF parsing error: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to read PDF: {e}")

    if not text.strip():
        print("[WARNING] PDF has no readable content")
        raise HTTPException(status_code=400, detail="Uploaded PDF contains no readable text")

    print(f"[DEBUG] Final extracted text length: {len(text)} characters")

    # ----- Find existing KB or create new -----
    kb = db.query(WhatsAppKnowledgeBase).filter(
        WhatsAppKnowledgeBase.user_id == user_id
    ).first()

    if kb:
        print(f"[DEBUG] Updating existing KB (id={kb.id})")
        kb.content = text
    else:
        print("[DEBUG] Creating new KB entry")
        kb = WhatsAppKnowledgeBase(
            user_id=user_id,
            content=text
        )
        db.add(kb)

    db.commit()
    db.refresh(kb)

    print(f"[DEBUG] KB saved: id={kb.id}, user_id={kb.user_id}")

    return {
        "knowledge_base_id": kb.id,
        "message": "PDF knowledge base uploaded successfully."
    }
"""

@app.post("/api/whatsapp-knowledge-base/upload")
async def upload_pdf(
    user_id: int = Form(...),
    mode: str = Form(...),                   # "new" or "replace"
    file: UploadFile = File(...),
    document_id: int = Form(None),           # required when mode="replace"
    document_title: str = Form(None),        # optional
    db: Session = Depends(get_db),
):
    print("\n========== PDF Upload Requested ==========")
    print(f"[DEBUG] user_id={user_id}, mode={mode}, document_id={document_id}, filename={file.filename}")

    # ------------------- Validate mode -------------------
    if mode not in ["new", "replace"]:
        raise HTTPException(400, "Mode must be 'new' or 'replace'")

    if mode == "replace" and not document_id:
        raise HTTPException(400, "document_id is required when mode='replace'")

    # ------------------- Read PDF -------------------
    try:
        pdf_bytes = await file.read()
        print(f"[DEBUG] PDF bytes read: {len(pdf_bytes)}")

        reader = PdfReader(io.BytesIO(pdf_bytes))

        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            print(f"[DEBUG] Page {i+1}: {len(text)} chars extracted")
            pages.append({"page_number": i + 1, "text": text})

    except Exception as e:
        print(f"[ERROR] PDF parsing failed: {e}")
        raise HTTPException(400, f"Failed to read PDF: {e}")

    if not any(p["text"].strip() for p in pages):
        raise HTTPException(400, "PDF contains no readable text")

    pdf_name = file.filename

    # ------------------- NEW DOCUMENT -------------------
    if mode == "new":
        print("[DEBUG] Creating new document entry")

        full_text = "\n\n".join([p["text"] for p in pages])  # rebuild whole PDF text

        doc = WhatsAppDocument(
            user_id=user_id,
            title=document_title or pdf_name,
            filename=pdf_name,
            pdf_name=pdf_name,
            content=full_text,   # ✅ REQUIRED FIX
        )

        db.add(doc)
        db.commit()
        db.refresh(doc)

        print(f"[DEBUG] New document created: id={doc.id}")

    # ------------------- REPLACE EXISTING DOCUMENT -------------------
    else:
        print(f"[DEBUG] Replacing existing document: id={document_id}")

        doc = (
            db.query(WhatsAppDocument)
            .filter(
                WhatsAppDocument.id == document_id,
                WhatsAppDocument.user_id == user_id,
            )
            .first()
        )

        if not doc:
            raise HTTPException(404, f"Document {document_id} not found")

        full_text = "\n\n".join([p["text"] for p in pages])

        doc.content = full_text  # ✅ REQUIRED FIX
        doc.filename = pdf_name
        doc.pdf_name = pdf_name
        if document_title:
            doc.title = document_title


        # Delete old embeddings
        print("[DEBUG] Removing old embeddings...")
        db.query(WhatsAppDocumentEmbedding).filter(
            WhatsAppDocumentEmbedding.document_id == document_id
        ).delete()

        db.commit()
        db.refresh(doc)

        print(f"[DEBUG] Document updated: id={doc.id}")

    # ------------------- Chunk + Embed Text -------------------
    print("[DEBUG] Beginning chunking + embedding…")
    chunk_counter = 0

    for page in pages:
        page_num = page["page_number"]
        text = page["text"]

        chunks = chunk_text(text)  # your existing function
        print(f"[DEBUG] Page {page_num} → {len(chunks)} chunks")

        for chunk in chunks:
            print(f"[DEBUG] Embedding chunk #{chunk_counter}, page {page_num}")

            try:
                embedding = (
                    client.embeddings.create(
                        model="text-embedding-3-large",
                        input=chunk,
                    ).data[0].embedding
                )
            except Exception as e:
                print(f"[ERROR] Embedding failed: {e}")
                raise HTTPException(500, f"Embedding failed: {e}")

            embed_record = WhatsAppDocumentEmbedding(
                document_id=doc.id,
                chunk_index=chunk_counter,
                page_number=page_num,  # ✅ NEW
                pdf_name=pdf_name,     # ✅ NEW
                content=chunk,
                embedding=embedding,
            )
            db.add(embed_record)
            chunk_counter += 1

    db.commit()

    print("\n========== Upload Complete ==========\n")

    return {
        "document_id": doc.id,
        "total_chunks": chunk_counter,
        "message": "Document uploaded and embeddings stored with page numbers and PDF name.",
    }



# ------------------ REQUEST MODEL ------------------



# ------------------ RAG CHATBOT ENDPOINT ------------------
@app.post("/api/chatbot/rag")
def chatbot_rag(request: ChatRequest, db: Session = Depends(get_db)):
    print("\n========== /api/chatbot/rag CALLED ==========")
    print("Incoming message:", request.message)
    print("public_token:", request.public_token)

    # ------------------ VERIFY BOT SESSION ------------------
    session = db.query(SessionModel).filter(
        SessionModel.public_token == request.public_token
    ).first()

    if not session:
        print("❌ No session found for public_token")
        raise HTTPException(status_code=404, detail="Chatbot not found")

    print("Session found for doctor_id:", session.doctor_id)

    # ------------------ ENFORCE PASSWORD PROTECTION ------------------
    if session.require_password:
        if not request.chat_access_token:
            print("❌ Missing access token on password-protected bot")
            raise HTTPException(status_code=401, detail="Missing access token")

        print("Password-protected access authorized")
    else:
        print("Chatbot is public → no password required")

    # ------------------ FETCH USER DOCUMENTS ------------------
    docs = db.query(WhatsAppDocument).filter(
        WhatsAppDocument.user_id == session.doctor_id
    ).all()

    if not docs:
        print("⚠️ No documents found → fallback GPT")
        return {"reply": simple_gpt_reply(request.message)}

    print(f"Loaded {len(docs)} documents")

    # ------------------ FETCH ALL EMBEDDINGS ------------------
    embeddings = db.query(WhatsAppDocumentEmbedding).join(
        WhatsAppDocument,
        WhatsAppDocumentEmbedding.document_id == WhatsAppDocument.id
    ).filter(
        WhatsAppDocument.user_id == session.doctor_id
    ).all()

    if not embeddings:
        print("⚠️ No embeddings found → fallback GPT")
        return {"reply": simple_gpt_reply(request.message)}

    print(f"Total embeddings loaded: {len(embeddings)}")

    # ------------------ EMBED USER QUERY ------------------
    query_embedding = (
        client.embeddings.create(
            model="text-embedding-3-large",
            input=request.message
        ).data[0].embedding
    )

    print("Query embedding generated")

    # ------------------ COSINE SIMILARITY SEARCH ------------------
    def cosine_similarity(a, b):
        a = np.array(a)
        b = np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    scored_chunks = []

    for emb in embeddings:
        sim = cosine_similarity(query_embedding, emb.embedding)
        scored_chunks.append((sim, emb.content))

    scored_chunks.sort(key=lambda x: x[0], reverse=True)

    top_chunks = [chunk for _, chunk in scored_chunks[:5]]

    print("\nTop matching chunks:")
    for i, chunk in enumerate(top_chunks):
        print(f"Chunk #{i+1}: {chunk[:200]}...\n")

    # ------------------ GENERATE GPT RESPONSE ------------------
    context = "\n\n".join(top_chunks)

    prompt = f"""
You are an AI assistant that answers strictly using the provided context.

CONTEXT:
{context}

USER QUESTION:
{request.message}

RULES:
- If the answer is NOT in the context, reply:
  "I’m sorry, I couldn’t find information about that."
- Do NOT fabricate information.
"""

    print("Sending prompt to GPT…")

    reply = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    ).choices[0].message["content"]

    print("GPT reply generated")

    print("========== /api/chatbot/rag END ==========\n")
    return {"reply": reply}


# ------------------ FALLBACK GPT ------------------
def simple_gpt_reply(message: str):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": message}]
    )
    return response.choices[0].message["content"]



@app.post("/api/knowledge-base/upload")
async def upload_pdf(
    user_id: int = Form(...),  # Read doctor/user ID from frontend
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    print(f"[DEBUG] Received upload request: user_id={user_id}, filename={file.filename}, content_type={file.content_type}")
    
    # Extract text from PDF
    try:
        file_bytes = await file.read()
        print(f"[DEBUG] Read {len(file_bytes)} bytes from uploaded file")
        reader = PdfReader(io.BytesIO(file_bytes))
        text = ""
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            print(f"[DEBUG] Page {i+1}: extracted {len(page_text)} characters")
            text += page_text
    except Exception as e:
        print(f"[ERROR] Failed to read PDF: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to read PDF: {e}")

    if not text.strip():
        print("[WARNING] PDF contains no readable text")
        raise HTTPException(status_code=400, detail="PDF contains no readable text")

    # Overwrite existing KB for the user if it exists
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.user_id == user_id).first()
    if kb:
        print(f"[DEBUG] Overwriting existing knowledge base for user_id={user_id}, kb_id={kb.id}")
        kb.content = text
    else:
        kb = KnowledgeBase(user_id=user_id, content=text)
        db.add(kb)

    db.commit()
    db.refresh(kb)
    print(f"[DEBUG] Knowledge base saved: id={kb.id}, user_id={kb.user_id}, content_length={len(text)}")

    # ----- Recreate temporary vector store -----
    if user_id in vector_stores:
        print(f"[DEBUG] Deleting existing temporary vector store for user_id={user_id}")
        del vector_stores[user_id]

    chunks = chunk_text(kb.content, chunk_size=500, overlap=50)
    embeddings = embed_texts(chunks)
    vector_stores[user_id] = {"chunks": chunks, "embeddings": np.array(embeddings)}
    print(f"[DEBUG] New vector store created for user_id={user_id} with {len(chunks)} chunks")

    return {"knowledge_base_id": kb.id, "message": "PDF content saved successfully and vector store rebuilt."}



@app.post("/chatbot/validate-password")
def validate_password(payload: dict, db: Session = Depends(get_db)):
    print("\n========== DEBUG: /chatbot/validate-password CALLED ==========")
    print("Incoming payload:", payload)

    public_token = payload.get("public_token")
    entered_password = payload.get("password")

    print("Parsed values:")
    print(" public_token =", public_token)
    print(" entered_password =", entered_password)

    # IMPORTANT: Replace ChatbotLink with SessionModel
    print("\n--- Querying sessions table by public_token ---")
    bot = db.query(SessionModel).filter(
        SessionModel.public_token == public_token
    ).first()

    print("DB session fetched:", bot)

    if not bot:
        print("❌ ERROR: No session found with this public_token")
        print("========== /chatbot/validate-password END ==========\n")
        raise HTTPException(status_code=404, detail="Chatbot session not found")

    print("\n--- SESSION SECURITY SETTINGS ---")
    print(" require_password =", bot.require_password)
    print(" stored hashed password =", bot.access_password)

    # If NOT password protected → automatically allow
    if not bot.require_password:
        print("\nChatbot does NOT require password → auto-allow access")
        token = str(uuid4())
        print("Generated chatAccessToken:", token)
        print("========== /chatbot/validate-password END ==========\n")
        return {"valid": True, "chatAccessToken": token}

    # Password IS required → verify
    if not entered_password:
        print("\n❌ ERROR: No password entered but one is required")
        print("========== /chatbot/validate-password END ==========\n")
        return {"valid": False}

    print("\nVerifying entered password...")
    is_valid = pwd_context.verify(entered_password, bot.access_password)

    print("Password match result =", is_valid)

    if not is_valid:
        print("❌ ERROR: Password incorrect")
        print("========== /chatbot/validate-password END ==========\n")
        return {"valid": False}

    # Correct password → generate chat access token
    chat_token = str(uuid4())
    print("\nPassword CORRECT → Access granted")
    print("Generated chatAccessToken:", chat_token)

    print("========== /chatbot/validate-password END ==========\n")
    return {"valid": True, "chatAccessToken": chat_token}



def generate_pdf_url(bucket_name: str, filename: str) -> str:
    """Generate a public URL for the uploaded PDF from S3."""
    return f"https://{bucket_name}.s3.{AWS_REGION}.amazonaws.com/{filename}"



@app.get("/chatbot/init/{public_token}")
def chatbot_init(public_token: str, db: Session = Depends(get_db)):
    print("\n========== DEBUG: /chatbot/init CALLED ==========")
    print("Incoming public_token:", public_token)

    # --------------------------------------------------------
    # Fetch session record using the public_token
    # --------------------------------------------------------
    session_record = db.query(SessionModel).filter(
        SessionModel.public_token == public_token
    ).first()

    print("DEBUG: Session lookup result:", session_record)

    if not session_record:
        print("ERROR: No session found with this public_token")
        raise HTTPException(status_code=404, detail="Chatbot not found")

    # --------------------------------------------------------
    # Read password protection flags
    # --------------------------------------------------------
    requires_password = getattr(session_record, "require_password", False)
    print("DEBUG: require_password =", requires_password)

    if requires_password:
        print("Chatbot IS password protected — requiresPassword=True")
        print("========== /chatbot/init END ==========\n")
        return {
            "requiresPassword": True,
            "publicToken": public_token
        }

    # Chatbot is public access
    print("Chatbot is NOT password protected — requiresPassword=False")
    print("========== /chatbot/init END ==========\n")

    return {
        "requiresPassword": False,
        "publicToken": public_token
    }
@app.post("/uploadPdf")
async def upload_pdf(pdf: UploadFile = File(...)):
    # Check if the uploaded file is a PDF
    if pdf.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF files are allowed.")

    if not allowed_file(pdf.filename):
        raise HTTPException(status_code=400, detail="Invalid file extension. Only PDF files are allowed.")

    # Create a unique filename
    file_extension = pdf.filename.rsplit(".", 1)[1].lower()
    unique_filename = f"{uuid4().hex}.{file_extension}"

    try:
        # Upload the file to Amazon S3
        s3_client.upload_fileobj(pdf.file, BUCKET_NAME, unique_filename, ExtraArgs={"ContentType": pdf.content_type, "ACL": "public-read"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error uploading file to Amazon S3: {e}")

    pdf_url = generate_pdf_url(BUCKET_NAME, unique_filename)
    return JSONResponse(status_code=200, content={"message": "Upload successful", "pdfUrl": pdf_url})

@app.websocket("/ws/public/{session_token}/{public_token}")
async def public_websocket_endpoint(websocket: WebSocket, session_token: str, public_token: str):
    try:
        # Debug: Print the received tokens
        print(f"WebSocket connection attempt with session_token: {session_token}, public_token: {public_token}")
        
        # Retrieve session data using the session token
        session_data = state.get_session(session_token)  

        # Ensure the public token matches the one in session data
        if session_data.get("public_token") != public_token:
            print(f"Token mismatch: {public_token} != {session_data.get('public_token')}")
            await websocket.close(code=1008)  # Policy violation: close connection
            return
        
        # Add WebSocket connection to the public manager
        await public_manager.connect(websocket, session_token)
        print(f"WebSocket connected for session_token: {session_token}")

        # Send initial state to the client
        initial_state = {
            "type": "update_state",
            "data": state.get_public_state(session_token)  # Use session_token for state retrieval
        }
        print(f"Sending initial state to client: {initial_state}")
        await websocket.send_text(json.dumps(initial_state))

        # Keep listening for messages
        while True:
            try:
                message = await websocket.receive_text()
                print(f"Received from client: {message}")

            except WebSocketDisconnect as e:
                print(f"Client disconnected: Code {e.code}, Reason: {e.reason}")
                break  

            except Exception as e:
                print(f"Error receiving message: {e}")

    except Exception as e:
        print(f"Unexpected WebSocket error: {e}")
    finally:
        await public_manager.disconnect(websocket, session_token)
        print(f"Client removed from public manager for session_token: {session_token}")


@app.post("/chatbot/settings")
def update_chatbot_settings(payload: dict, db: Session = Depends(get_db)):
    print("\n\n====================== /chatbot/settings CALLED ======================")
    print("RAW Incoming payload:", payload)

    # Extract fields
    session_token = payload.get("session_token")
    public_token = payload.get("public_token")
    require_password = payload.get("require_password")
    raw_password = payload.get("password")

    print("\n--- PARSED PAYLOAD VALUES ---")
    print(" session_token      =", session_token)
    print(" public_token       =", public_token)
    print(" require_password   =", require_password)
    print(" password (raw)     =", raw_password)

    # ---------------- FETCH SESSION ----------------
    print("\n--- QUERYING DATABASE FOR SESSION ---")
    session = (
        db.query(SessionModel)
        .filter(
            SessionModel.session_token == session_token,
            SessionModel.public_token == public_token
        )
        .first()
    )

    print("Database returned session:", session)

    if not session:
        print("❌ ERROR: No session found matching BOTH session_token + public_token")
        raise HTTPException(status_code=401, detail="Invalid session or chatbot token")

    print("\n--- SESSION BEFORE UPDATE ---")
    print(" require_password (old) =", session.require_password)
    print(" access_password (old)  =", session.access_password)

    # ---------------- UPDATE PASSWORD SETTINGS ----------------
    print("\n--- APPLYING UPDATES TO SESSION ---")
    session.require_password = require_password
    print(" require_password updated →", require_password)

    if require_password:
        print(" Password protection ENABLED")

        if not raw_password:
            print("❌ ERROR: require_password=True but no password sent")
            raise HTTPException(
                status_code=400,
                detail="Password required when require_password is true"
            )

        hashed_pw = pwd_context.hash(raw_password)
        print(" Generated hashed password =", hashed_pw)
        session.access_password = hashed_pw

    else:
        print(" Password protection DISABLED → clearing access_password")
        session.access_password = None

    print("\n--- SESSION AFTER UPDATE (before commit) ---")
    print(" require_password (new) =", session.require_password)
    print(" access_password (new)  =", session.access_password)

    # ---------------- COMMIT CHANGES ----------------
    print("\n--- COMMITTING CHANGES TO DATABASE ---")
    try:
        db.commit()
        print("✔️ DB commit successful")
    except Exception as e:
        print("❌ DB commit failed:", str(e))
        db.rollback()
        raise

    print("\n====================== /chatbot/settings END ======================\n")

    return {"message": "Chatbot access settings updated successfully"}

async def timer_loop(session_token, state, manager, public_manager):
    while session_token in session_states:
        session_data = state.get_session(session_token)
        patients = session_data.get("patients", [])
        avg = state.get_average_time(session_token)

        # Debug session info
        print("\n--- TIMER LOOP DEBUG ---")
        print("Session Token:", session_token)
        print("Patients:", patients)
        print("Added_times:", session_data.get("added_times"))
        print("Average Inspection Time:", avg)

        timers = {}
        now = time.time()

        for index, patient in enumerate(patients):
            added = state.get_patient_timestamp(session_token, index)

            wait_offset = avg * index
            elapsed = now - added
            remaining = max(wait_offset - elapsed, 0)

            timers[index] = int(remaining)

            # Debug each patient timer computation
            print(f"\nPatient #{index}: {patient}")
            print("  Added Time:", added)
            print("  Wait Offset:", wait_offset)
            print("  Elapsed:", elapsed)
            print("  Remaining:", remaining)

        print("Computed Timers:", timers)
        print("--- END DEBUG ---\n")

        update_msg = {
            "type": "update_timers",
            "timers": timers
        }

        # Broadcast the timers to doctor and public dashboard
        await manager.broadcast_to_session(session_token, update_msg)
        await public_manager.broadcast_to_session(session_token, update_msg)

        await asyncio.sleep(1)


@app.websocket("/ws/{session_token}")
async def websocket_endpoint(websocket: WebSocket, session_token: str):
    # Authenticate and verify session token
    #session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    session = db.query(SessionModel).filter(SessionModel.session_token == uuid.UUID(session_token)).first()

    if not session:
        await websocket.close(code=1008)  # Invalid token, close connection
        return

    # Ensure each session gets its own independent state
    if session_token not in session_states or not isinstance(session_states[session_token], DashboardState):
        session_states[session_token] = DashboardState()

    state = session_states[session_token]  # Get the state for this session
    session_data = state.get_session(session_token)  # Get the session data
    # Add WebSocket connection to the manager based on session token
    await manager.connect(websocket, session_token)
    # Start the backend timer loop for this session
    asyncio.create_task(timer_loop(session_token, state, manager, public_manager))


    try:
        # Send initial state to the new client
        initial_state = {
            "type": "update_state",
            "data": {
                "patients": session_data.get("patients", []),
                "currentPatient": session_data.get("current_patient", None),
                "averageInspectionTime": state.get_average_time(session_token),
                "session_token": session_token,
                "notices": session_data.get("notices", [])
            }
        }
        await websocket.send_text(json.dumps(initial_state))

        # Broadcast to public clients (if applicable)
        if not session.is_authenticated:
            await public_manager.broadcast_to_session(session_token, initial_state)

        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "get_doctor_id":
                doctor_id = session.doctor_id  # Extract doctor_id
                print(session.doctor_id)
                response = {
                    
                    "doctor_id": doctor_id
                }
                await websocket.send_text(json.dumps(response)) 
                 

            elif message["type"] == "add_patient":
                session_token_current = message.get("session_token")                
                patient_name = message.get("patient")

                # Add patient to the state
                state.add_patient(session_token_current, patient_name)

                # Prepare the update message
                update = {
                    "type": "update_state",
                    "data": {
                        "patients": session_data["patients"],
                        "currentPatient": session_data["current_patient"],
                        "averageInspectionTime": state.get_average_time(session_token),
                        "session_token": session_token
                    }
                }

                # Broadcast updates
                await manager.broadcast_to_session(session_token_current, update)
                await public_manager.broadcast_to_session(session_token_current, update)  # Update public clients

                # Log railway resource usage
                today = func.current_date()

                try:
                    # Check session entry
                    session_entry = db.query(SessionModel).filter_by(session_token=session_token_current).first()
                    
                    if session_entry:
                        doctor_id = session_entry.doctor_id  # Ensure doctor_id exists

                        # Check railway usage entry
                        railway_usage_entry = db.query(RailwayResourceUsageModel).filter_by(doctor_id=doctor_id, date=today).first()

                        if railway_usage_entry:
                            railway_usage_entry.usage_count += 1  # Increment count
                        else:
                            railway_usage_entry = RailwayResourceUsageModel(
                                doctor_id=doctor_id, 
                                resource_type="add_patient", 
                                usage_count=1, 
                                date=today
                            )
                            db.add(railway_usage_entry)

                        db.commit()  # Save changes
                except Exception as e:
                    pass  # Handle exceptions silently or log them if needed
            elif message["type"] == "reset_all":
                print("Received reset_all message:", message)
                session_token_current = message.get("session_token") 
                state.reset_all(session_token_current)
                update = {
                    "type": "update_state",
                    "data": {
                        "patients": session_data["patients"],
                        "currentPatient": session_data["current_patient"],
                        "averageInspectionTime": state.get_average_time(session_token),
                        "session_token": session_token
                    }
                }
                await manager.broadcast_to_session(session_token_current, update)
                await public_manager.broadcast_to_session(session_token_current, update)  # Update public clients

            elif message["type"] == "close_connection":
                session_token_current = message.get("session_token") 

                # Close the WebSocket connection
                await websocket.close()
                print("Connection closing")

                # Safely remove WebSocket from active connections
                await manager.disconnect(websocket, session_token_current)
                await public_manager.disconnect(websocket, session_token_current)

                # Remove session state when closing
                if session_token_current in session_states:
                    del session_states[session_token_current]

                # Broadcast update to notify clients about the connection closure
                update = {
                    "type": "connection_closed",
                    "data": {
                        "message": "WebSocket connection has been closed.",
                        "session_token": session_token_current
                    }
                }
                await manager.broadcast_to_session(session_token_current, update)
                await public_manager.broadcast_to_session(session_token_current, update)

            elif message["type"] == "mark_done":
                
                session_token_current = message.get("session_token") 
                state.mark_as_done(session_token_current)
                update = {
                    "type": "update_state",
                    "data": {
                        "patients": session_data["patients"],
                        "currentPatient": session_data["current_patient"],
                        "averageInspectionTime": state.get_average_time(session_token_current),
                        "session_token": session_token
                    }
                }
                await manager.broadcast_to_session(session_token_current, update)
                await public_manager.broadcast_to_session(session_token_current, update)  # Update public clients
            elif message["type"] == "add_notice":
                session_token_current = message.get("session_token")                
                notice_text = message.get("notice")
                
                state.add_notice(session_token_current, notice_text)
                
                session_data = state.get_session(session_token_current)  # Retrieve updated session data

                update = {
                    "type": "update_notices",
                    "data": {
                        "notices": session_data.get("notices", []),
                        "session_token": session_token_current
                    }
                }
                
                await manager.broadcast_to_session(session_token_current, update)
                await public_manager.broadcast_to_session(session_token_current, update)  # Update public clients
            elif message["type"] == "remove_notice":
                session_token_current = message.get("session_token")
                notice_index = message.get("index")

                session_data = state.get_session(session_token_current)
                
                if 0 <= notice_index < len(session_data["notices"]):
                    session_data["notices"].pop(notice_index)  # Remove the notice

                # Prepare update message
                update = {
                    "type": "update_notices",
                    "data": {
                        "notices": session_data["notices"]
                    }
                }

                # Broadcast the updated notices to all clients
                await manager.broadcast_to_session(session_token_current, update)
                await public_manager.broadcast_to_session(session_token_current, update)  # Update public clients

    except WebSocketDisconnect as e:
        print(f"Client disconnected: Code {e.code}, Reason: {str(e)}")
        manager.disconnect(websocket, session_token)  # No await here

        # Remove session state after disconnect
        if session_token in session_states:
            del session_states[session_token]

    except Exception as e:
        error_message = f"Unexpected error: {str(e)}\n{traceback.format_exc()}"
        print(error_message)


@app.post("/add-user")
async def add_user(request: Request):
    try:
        # 📥 Step 1: Get raw JSON data from the frontend
        data = await request.json()
        print("🟡 Raw incoming data:", data)

        # ✅ Step 2: Validate and parse into UserData model
        try:
            user_data = UserData(**data)
            print("✅ Parsed UserData:", user_data)
        except Exception as e:
            print("🔴 Failed to parse UserData:", str(e))
            raise HTTPException(status_code=422, detail=f"Validation error: {str(e)}")

        # 🛠️ Step 3: Prepare data for Odoo
        odoo_data = {
            "name": user_data.name,
            "email": user_data.email,
            "login": user_data.email,  # Set login as email
            "role": user_data.role,    # This will be used to fetch the group ID
        }

        # Debugging the login field
        if not odoo_data["login"]:
            raise HTTPException(status_code=400, detail="Login field is missing or empty.")

        print("📤 Sending to Odoo:", odoo_data)

        # 🌐 Step 4: Odoo credentials and server setup
        url = "https://odoo-custom-production.up.railway.app"
        db = "odoo_database"
        username = "proactive1@live.com"  # Correct username for XML-RPC
        password = "rubabB121024"  # Correct password for XML-RPC

        # 🔐 Authenticate
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, username, password, {})
        print("🔑 Authenticated UID:", uid)

        # 📦 Send data to Odoo
        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        # Step 4.1: Search for the company (clinic) by clinic name
        company_ids = models.execute_kw(
            db, uid, password,
            'res.company', 'search',
            [[['name', '=', user_data.clinic_name]]],
            {'limit': 1}
        )

        if company_ids:
            company_id = company_ids[0]
            print(f"✅ Company '{user_data.clinic_name}' found in Odoo with ID: {company_id}")
        else:
            company_id = models.execute_kw(
                db, uid, password,
                'res.company', 'create',
                [{'name': user_data.clinic_name}]
            )
            print(f"✅ Company '{user_data.clinic_name}' created in Odoo with ID: {company_id}")

        # Step 4.2: Fetch the group ID for the specified role (e.g., "Internal User")
        group_ids = models.execute_kw(
            db, uid, password,
            'res.groups', 'search',
            [[['name', '=', user_data.role]]],  # Search for group by role name (e.g., "Internal User")
        )

        if not group_ids:
            print(f"🔴 Role '{user_data.role}' not found in Odoo.")
            raise HTTPException(status_code=404, detail=f"Role '{user_data.role}' not found in Odoo")

        print(f"✅ Role '{user_data.role}' found in Odoo with ID(s): {group_ids}")

        # Step 4.3: Create the user in 'res.users' for login
        user_id = models.execute_kw(
            db, uid, password,
            'res.users', 'create',
            [{
                'name': user_data.name,
                'email': user_data.email,
                'login': user_data.email,  # Ensure the login is being passed correctly
                'groups_id': [[6, 0, group_ids]],  # Assign the role using the group ID(s)
                'company_id': company_id,  # Associate the user with the company (clinic)
            }]
        )
        print(f"✅ User created in Odoo with ID: {user_id}")

        # Step 5: Create the employee in 'hr.employee' and link it to the user and company
        employee_data = {
            'name': user_data.name,
            'user_id': user_id,  # Link the employee to the created user
            'company_id': company_id,  # Link employee to the company (clinic)
        }

        staff_id = models.execute_kw(
            db, uid, password,
            'hr.employee', 'create', [employee_data]
        )

        print("✅ Staff created in Odoo with ID:", staff_id)
        return {"message": "User created successfully", "staff_id": staff_id}

    except Exception as e:
        print("🚨 Exception occurred:", str(e))
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/api/usage")
def get_api_usage(doctorId: int, db: Session = Depends(get_db)):

    usage_rows = (
        db.query(APIUsageModel)
        .filter(APIUsageModel.doctor_id == doctorId)
        .order_by(APIUsageModel.timestamp.desc())
        .all()
    )

    if not usage_rows:
        return []

    return [
        {
            "date": str(row.timestamp.date()),  # extract date only
            "request_type": row.request_type,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "total_tokens": row.total_tokens,
            "cost_usd": row.cost_usd,
            "timestamp": row.timestamp.isoformat(),
        }
        for row in usage_rows
    ]
                    
@app.post("/extractText")
async def extract_text(image: UploadFile = File(...)):
    # Ensure the uploaded file is an image
    print("DEBUG: Received file with content type:", image.content_type)
    if not image.content_type.startswith("image/"):
        print("DEBUG: File is not an image.")
        raise HTTPException(status_code=400, detail="Invalid file type. Only image files are allowed.")
    
    try:
        # Read the uploaded file into memory
        contents = await image.read()
        print("DEBUG: Read file contents successfully. Size:", len(contents), "bytes")
        
        # Open the image using Pillow
        img = Image.open(io.BytesIO(contents))
        print("DEBUG: Image opened successfully. Format:", img.format)
        
        # Use pytesseract to extract text from the image
        extracted_text = pytesseract.image_to_string(img)
        print("DEBUG: Extracted text:", extracted_text)
    except Exception as e:
        print("DEBUG: Exception occurred during processing:", e)
        raise HTTPException(status_code=500, detail=f"Error processing image: {e}")

    return JSONResponse(status_code=200, content={"extractedText": extracted_text})

@app.websocket("/ws/public/{session_token}/{public_token}")
async def public_websocket_endpoint(
    websocket: WebSocket,
    session_token: str,
    public_token: str,
    db: Session = Depends(get_db),
):
    try:
        print(f"\n🔹 [DEBUG] New WebSocket connection attempt")
        print(f"   - Received session_token: {session_token}")
        print(f"   - Received public_token: {public_token}")

        # ✅ Convert session_token to UUID
        try:
            session_uuid = uuid.UUID(session_token)
        except ValueError:
            print(f"❌ Invalid session_token format: {session_token}")
            await websocket.close(code=1008)  # Policy violation
            return

        # ✅ Retrieve session from database
        session = db.query(SessionModel).filter(SessionModel.session_token == session_uuid).first()

        if not session:
            print(f"❌ Invalid session_token: {session_token} (No matching session found in DB)")
            await websocket.close(code=1008)  # Close connection if session doesn't exist
            return

        # ✅ Convert db_public_token to string for comparison
        db_public_token = str(session.public_token)
        print(f"   - DB public_token: {db_public_token}")

        # ✅ Check if the provided public_token matches the stored one
        if db_public_token != public_token:
            print(f"❌ Token mismatch: {public_token} != {db_public_token} (Expected)")
            await websocket.close(code=1008)  # Policy violation: close connection
            return
        # ✅ Add a small delay before sending the initial state
        await asyncio.sleep(0.5)  # Adjust delay if needed
        # ✅ Add WebSocket connection to the public manager
        await public_manager.connect(websocket, session_token)

        # ✅ Send initial state to the client
        initial_state = {
            "type": "update_state",
            "data": state.get_public_state(session_token),  # Ensure `state` is properly defined
        }
        print(f"📤 Sending initial state: {json.dumps(initial_state, indent=2)}")
        await websocket.send_text(json.dumps(initial_state))

        # ✅ Listen for messages
        while True:
            try:
                message = await websocket.receive_text()
                print(f"📩 Received from client: {message}")

            except WebSocketDisconnect:
                print(f"🔻 Client disconnected for session_token: {session_token}")
                break  

            except Exception as e:
                print(f"⚠️ Error receiving message: {e}")

    except Exception as e:
        print(f"⚠️ Unexpected WebSocket error: {e}")

    finally:
        await public_manager.disconnect(websocket, session_token)
        print(f"🔻 Removed from WebSocket manager: session_token={session_token}\n")
         
    


# HTTP endpoint to get the public token (for the doctor to share)
@app.get("/dashboard/public-token")
def get_public_token(session_token: str = Query(...)):  # Required query param
    print(f"Session Token Requested: {session_token}")

    # Retrieve session from the database
    session = db.query(SessionModel).filter(SessionModel.session_token == uuid.UUID(session_token)).first()

    # Check if session exists
    if not session:
        print("Session Not Found")
        return {"error": "Session not found", "sessionToken": session_token}

    # Extract public token from session
    public_token = session.public_token
    print(f"Stored Public Token: {public_token}")

    return {
        "sessionToken": session_token,
        "publicToken": public_token
    }

@app.get("/get-doctor-id/{session_token}")
def get_doctor_id(session_token: str, db: Session = Depends(get_db)):
    """
    Given a session token, returns the associated doctor_id.
    """
    session_entry = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    
    if not session_entry:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"doctor_id": session_entry.doctor_id}

@app.websocket("/ws/OrderManager/{session_token}")
async def websocket_endpoint(websocket: WebSocket, session_token: str):
    # Authenticate and verify session token
    #session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    session = db.query(SessionModel).filter(SessionModel.session_token == uuid.UUID(session_token)).first()

    if not session:
        await websocket.close(code=1008)  # Invalid token, close connection
        return

    # Ensure each session gets its own independent state
    if session_token not in session_states or not isinstance(session_states[session_token], OrderManagerState):
        session_states[session_token] = OrderManagerState()

    OrderManager_state = session_states[session_token]  # Get the state for this session
    session_data = OrderManager_state.get_session(session_token)  # Get the session data
    # Add WebSocket connection to the manager based on session token
    await manager.connect(websocket, session_token)

    try:
        # Send initial state to the new client
        initial_state = {
            "type": "update_state",
            "data": {
                "preparingList": session_data["preparingList"],
                "servingList": session_data["servingList"],
                "notices": session_data["notices"],
                "session_token": session_token
            }
        }
        await websocket.send_text(json.dumps(initial_state))

        # Broadcast to public clients (if applicable)
        if not session.is_authenticated:
            await public_manager.broadcast_to_session(session_token, initial_state)
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "close_connection":
                session_token_current = message.get("session_token") 

                # Close the WebSocket connection
                await websocket.close()
                print("Connection closing")

                # Safely remove WebSocket from active connections
                await manager.disconnect(websocket, session_token_current)  
                # Remove session state when closing
                if session_token_current in session_states:
                    del session_states[session_token_current]

                # Broadcast update to notify clients about the connection closure
                update = {
                    "type": "connection_closed",
                    "data": {
                        "message": "WebSocket connection has been closed.",
                        "session_token": session_token_current
                    }
                }
                await manager.broadcast_to_session(session_token_current, update)
            elif message["type"] == "add_item":
                print("Received add_item message:", message)
                session_token_current = message.get("session_token")

                # Add the item using the LiveUpdateState method
                OrderManager_state.add_item(session_token_current, message.get("item", ""))
                
                # Safely get session data
                session_data = OrderManager_state.get_session(session_token_current)

                update = {
                    "type": "update_state",
                    "data": {
                        "preparingList": session_data.get("preparingList", []),
                        "servingList": session_data.get("servingList", []),
                        "notices": session_data.get("notices", []),
                        "session_token": session_token_current
                    }
                }
                await manager.broadcast_to_session(session_token_current, update)
                await public_manager.broadcast_to_session(session_token_current, update)
                
            elif message["type"] == "mark_done":
                session_token_current = message.get("session_token")
                selected_index = message.get("index")

                if selected_index is not None:
                    OrderManager_state.mark_done(session_token_current, selected_index)
                    print(OrderManager_state.get_session(session_token_current)["preparingList"],)

                    update = {
                        "type": "update_state",
                        "data": {
                            "preparingList": OrderManager_state.get_session(session_token_current)["preparingList"],
                            "servingList": OrderManager_state.get_session(session_token_current)["servingList"],
                            "notices": OrderManager_state.get_session(session_token_current)["notices"],
                            "session_token": session_token_current
                        }
                    }

                    await manager.broadcast_to_session(session_token_current, update)      
                    await public_manager.broadcast_to_session(session_token_current, update) 
            elif message["type"] == "add_notice":
                session_token_current = message.get("session_token")
                new_notice = message.get("notice")

                if new_notice:
                    OrderManager_state.add_notice(session_token_current, new_notice)
                    print(OrderManager_state.get_session(session_token_current)["notices"])

                    update = {
                        "type": "update_state",
                        "data": {
                            "preparingList": OrderManager_state.get_session(session_token_current)["preparingList"],
                            "servingList": OrderManager_state.get_session(session_token_current)["servingList"],
                            "notices": OrderManager_state.get_session(session_token_current)["notices"],
                            "session_token": session_token_current
                        }
                    }

                    await manager.broadcast_to_session(session_token_current, update)
                    await public_manager.broadcast_to_session(session_token_current, update)
            elif message["type"] == "remove_notice":
                    session_token_current = message.get("session_token")
                    notice_index = message.get("index")

                    if notice_index is not None:
                        OrderManager_state.remove_notice(session_token_current, notice_index)
                        print(OrderManager_state.get_session(session_token_current)["notices"])

                        update = {
                            "type": "update_state",
                            "data": {
                                "preparingList": OrderManager_state.get_session(session_token_current)["preparingList"],
                                "servingList": OrderManager_state.get_session(session_token_current)["servingList"],
                                "notices": OrderManager_state.get_session(session_token_current)["notices"],
                                "session_token": session_token_current
                            }
                        }

                        await manager.broadcast_to_session(session_token_current, update)
                        await public_manager.broadcast_to_session(session_token_current, update)

            elif message["type"] == "mark_served":
                session_token_current = message.get("session_token")
                selected_index = message.get("index")

                if selected_index is not None:
                    OrderManager_state.mark_served(session_token_current, selected_index)
                    print(OrderManager_state.get_session(session_token_current)["servingList"])

                    update = {
                        "type": "update_state",
                        "data": {
                            "preparingList": OrderManager_state.get_session(session_token_current)["preparingList"],
                            "servingList": OrderManager_state.get_session(session_token_current)["servingList"],
                            "notices": OrderManager_state.get_session(session_token_current)["notices"],
                            "session_token": session_token_current
                        }
                    }

                    await manager.broadcast_to_session(session_token_current, update)
                    await public_manager.broadcast_to_session(session_token_current, update)
            elif message["type"] == "add_item_cart":
                    session_token_current = message.get("session_token")
                    item_to_add = message.get("item")  # Assuming the message contains an "item" key

                    if item_to_add is not None:
                        # Add item to the cartList in the session
                        OrderManager_state.get_session(session_token_current)["cartList"].append(item_to_add)
                        

                        update = {
                            "type": "add_item_cart",
                            "data": {                                
                                "cartList": OrderManager_state.get_session(session_token_current)["cartList"],
                                "session_token": session_token_current
                            }
                        }

                        await manager.broadcast_to_session(session_token_current, update)
                        await public_manager.broadcast_to_session(session_token_current, update)

            elif message["type"] == "place_order":
                        session_token_current = message.get("session_token")

                        # Get the session
                        session = OrderManager_state.get_session(session_token_current)

                        if session["cartList"]:  # Only proceed if cart is not empty
                            # Move items from cartList to orderList
                            session["orderList"].extend(session["cartList"])                                    
                            session["cartList"].clear()

                            update = {
                                "type": "place_order",
                                "data": {
                                    "orderList": session["orderList"],
                                    "cartList": session["cartList"],
                                    "session_token": session_token_current
                                }
                            }

                            # Send update to the client who placed the order
                            await manager.broadcast_to_session(session_token_current, update)

                            # Send update to ALL public (management/admin) clients
                            await public_manager.broadcast(update)

    except WebSocketDisconnect as e:
        print(f"Client disconnected: Code {e.code}, Reason: {str(e)}")
        await manager.disconnect(websocket, session_token)

        # Remove session state after disconnect
        if session_token in session_states:
            del session_states[session_token]

    except Exception as e:
        error_message = f"Unexpected error: {str(e)}\n{traceback.format_exc()}"
        print(error_message)
@app.websocket("/ws/School/{session_token}")
async def websocket_endpoint(websocket: WebSocket, session_token: str):
    # Authenticate and verify session token
    #session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    session = db.query(SessionModel).filter(SessionModel.session_token == uuid.UUID(session_token)).first()
    

    if not session:
        await websocket.close(code=1008)  # Invalid token, close connection
        return

    # Ensure each session gets its own independent state
    #here
    if session_token not in session_states:
        session_states[session_token] = OrderManagerState()  # Create a new state for this session
    School_state = session_states[session_token]  # Get the state for this session
    session_data = School_state.get_session(session_token)  # Get the session data
    # Add WebSocket connection to the manager based on session token
    await manager.connect(websocket, session_token)

    try:
        # Send initial state to the new client
        initial_state = {
            "type": "update_state",
            "data": {                
                "notices": session_data["notices"]
                }
        }
        await websocket.send_text(json.dumps(initial_state))

        # Broadcast to public clients (if applicable)
        if not session.is_authenticated:
            await public_manager.broadcast_to_session(session_token, initial_state)
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "close_connection":
                session_token_current = message.get("session_token") 

                # Close the WebSocket connection
                await websocket.close()
                print("Connection closing")

                # Safely remove WebSocket from active connections
                await manager.disconnect(websocket, session_token_current)  
                # Remove session state when closing
                if session_token_current in session_states:
                    del session_states[session_token_current]

                # Broadcast update to notify clients about the connection closure
                update = {
                    "type": "connection_closed",
                    "data": {
                        "message": "WebSocket connection has been closed.",
                        "session_token": session_token_current
                    }
                }
                await manager.broadcast_to_session(session_token_current, update)
            
            elif message["type"] == "add_notice":
                session_token_current = message.get("session_token")
                new_notice = message.get("notice")

                if new_notice:
                    School_state.add_notice(session_token_current, new_notice)
                    

                    update = {
                        "type": "update_state",
                        "data": {
                            
                            "notices": School_state.get_session(session_token_current)["notices"],
                            "session_token": session_token_current
                        }
                    }

                    await manager.broadcast_to_session(session_token_current, update)
                    await public_manager.broadcast_to_session(session_token_current, update)
            elif message["type"] == "remove_notice":
                    session_token_current = message.get("session_token")
                    notice_index = message.get("index")

                    if notice_index is not None:
                        School_state.remove_notice(session_token_current, notice_index)
                        print(School_state.get_session(session_token_current)["notices"])
                        update = {
                            "type": "update_state",
                            "data": {
                                
                                "notices": School_state.get_session(session_token_current)["notices"],
                                "session_token": session_token_current
                            }
                        }
                        await manager.broadcast_to_session(session_token_current, update)
                        await public_manager.broadcast_to_session(session_token_current, update)    
            elif message["type"] == "restore_notice":
                session_token_current = message.get("session_token")

                if session:  # Ensure session is valid
                    try:
                        # Fetch the saved notices from the database
                        notice_entry = db.query(NoticesModel).filter(NoticesModel.session_token == session_token_current).first()

                        if notice_entry:
                            restored_notices = notice_entry.notices  # Retrieve saved notices
                        else:
                            restored_notices = []  # No notices found, return empty list

                        # Send the restored notices back to the frontend
                        response = {
                            "type": "notices_restored",
                            "data": {
                                "message": "Notices successfully restored",
                                "notices": restored_notices,
                                "session_token": session_token_current
                            }
                        }
                        await websocket.send_text(json.dumps(response))

                    except Exception as e:
                        error_response = {
                            "type": "error",
                            "data": {
                                "message": "Failed to restore notices.",
                                "error": str(e),
                                "session_token": session_token_current
                            }
                        }
                        await websocket.send_text(json.dumps(error_response))
                else:
                    # Send an error response if session is invalid
                    error_response = {
                        "type": "error",
                        "data": {
                            "message": "Invalid session. Cannot restore notices.",
                            "session_token": session_token_current
                        }
                    }
                    await websocket.send_text(json.dumps(error_response))     
            elif message["type"] == "save_notice":
                session_token_current = message.get("session_token")
                complete_notices = message.get("complete_notices")

                if complete_notices is not None:
                    # No need to fetch session again, just check if it's valid
                    if session:
                        try:
                            # Delete existing notices for this session_token
                            db.query(NoticesModel).filter(NoticesModel.session_token == session_token).delete()

                            # Add the new notice entry
                            new_notice = NoticesModel(session_token=session_token, notices=complete_notices)
                            db.add(new_notice)

                            # Commit the changes to the database
                            db.commit()

                            # Send a success response back to the frontend
                            update = {
                                "type": "notice_saved",
                                "data": {
                                    "message": "Notices successfully saved",
                                    "notices": School_state.get_session(session_token)["notices"],
                                    "session_token": session_token
                                }
                            }
                            await manager.broadcast_to_session(session_token, update)
                        
                        except Exception as e:
                            db.rollback()  # Roll back the transaction if something goes wrong
                            error_response = {
                                "type": "error",
                                "data": {
                                    "message": "Failed to save notices.",
                                    "error": str(e),
                                    "session_token": session_token
                                }
                            }
                            await websocket.send_text(json.dumps(error_response))
                    else:
                        # Send an error response if session is invalid
                        error_response = {
                            "type": "error",
                            "data": {
                                "message": "Invalid session. Cannot save notices.",
                                "session_token": session_token
                            }
                        }
                        await websocket.send_text(json.dumps(error_response))             
            
    except WebSocketDisconnect as e:
        print(f"Client disconnected: Code {e.code}, Reason: {str(e)}")
        await manager.disconnect(websocket, session_token)

        # Remove session state after disconnect
        if session_token in session_states:
            del session_states[session_token]

    except Exception as e:
        error_message = f"Unexpected error: {str(e)}\n{traceback.format_exc()}"
        print(error_message)

@app.websocket("/ws/RealEstate/{session_token}")
async def websocket_endpoint(websocket: WebSocket, session_token: str):
    # Authenticate and verify session token
    #session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    session = db.query(SessionModel).filter(SessionModel.session_token == uuid.UUID(session_token)).first()

    if not session:
        await websocket.close(code=1008)  # Invalid token, close connection
        return

    # Ensure each session gets its own independent state
    #here
    if session_token not in session_states:
        session_states[session_token] = OrderManagerState()  # Create a new state for this session
    RealEstate_state = session_states[session_token]  # Get the state for this session
    session_data = RealEstate_state.get_session(session_token)  # Get the session data
    # Add WebSocket connection to the manager based on session token
    await manager.connect(websocket, session_token)

    try:
        # Send initial state to the new client
        initial_state = {
            "type": "update_state",
            "data": {                
                "notices": session_data["notices"]
                }
        }
        await websocket.send_text(json.dumps(initial_state))

        # Broadcast to public clients (if applicable)
        if not session.is_authenticated:
            await public_manager.broadcast_to_session(session_token, initial_state)
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "close_connection":
                session_token_current = message.get("session_token") 

                # Close the WebSocket connection
                await websocket.close()
                print("Connection closing")

                # Safely remove WebSocket from active connections
                await manager.disconnect(websocket, session_token_current)  
                # Remove session state when closing
                if session_token_current in session_states:
                    del session_states[session_token_current]

                # Broadcast update to notify clients about the connection closure
                update = {
                    "type": "connection_closed",
                    "data": {
                        "message": "WebSocket connection has been closed.",
                        "session_token": session_token_current
                    }
                }
                await manager.broadcast_to_session(session_token_current, update)
            
            elif message["type"] == "add_notice":
                session_token_current = message.get("session_token")
                new_notice = message.get("notice")

                if new_notice:
                    RealEstate_state.add_notice(session_token_current, new_notice)
                    print(RealEstate_state.get_session(session_token_current)["notices"])

                    update = {
                        "type": "update_state",
                        "data": {
                            "notices": RealEstate_state.get_session(session_token_current)["notices"],
                            "session_token": session_token_current
                        }
                    }

                    await manager.broadcast_to_session(session_token_current, update)
                    await public_manager.broadcast_to_session(session_token_current, update)
            elif message["type"] == "remove_notice":
                    session_token_current = message.get("session_token")
                    notice_index = message.get("index")

                    if notice_index is not None:
                        RealEstate_state.remove_notice(session_token_current, notice_index)
                        print(RealEstate_state.get_session(session_token_current)["notices"])
                        update = {
                            "type": "update_state",
                            "data": {
                                
                                "notices": RealEstate_state.get_session(session_token_current)["notices"],
                                "session_token": session_token_current
                            }
                        }
                        await manager.broadcast_to_session(session_token_current, update)
                        await public_manager.broadcast_to_session(session_token_current, update)           
            
    except WebSocketDisconnect as e:
        print(f"Client disconnected: Code {e.code}, Reason: {str(e)}")
        await manager.disconnect(websocket, session_token)

        # Remove session state after disconnect
        if session_token in session_states:
            del session_states[session_token]

    except Exception as e:
        error_message = f"Unexpected error: {str(e)}\n{traceback.format_exc()}"
        print(error_message)

"""
@app.websocket("/ws/public/OrderManager/{session_token}/{public_token}")
async def public_websocket_endpoint(
    websocket: WebSocket,
    session_token: str,
    public_token: str,
    db: Session = Depends(get_db),
):
    try:
        print("\n🔹 [DEBUG] New WebSocket connection attempt")
        print(f"   - Received session_token: {session_token}")
        print(f"   - Received public_token: {public_token}")

        # ✅ Validate session_token as UUID
        try:
            session_uuid = uuid.UUID(session_token)
        except ValueError:
            print(f"❌ [ERROR] Invalid session_token format: {session_token}")
            await websocket.close(code=1008)  # Policy violation
            return

        # ✅ Retrieve session from the database
        session = db.query(SessionModel).filter(SessionModel.session_token == session_uuid).first()

        if not session:
            print(f"❌ [ERROR] No session found in DB for session_token: {session_token}")
            await websocket.close(code=1008)  # Close connection if session doesn't exist
            return

        # ✅ Check if public_token matches
        db_public_token = str(session.public_token)
        print(f"   - DB public_token: {db_public_token}")

        if db_public_token != public_token:
            print(f"❌ [ERROR] Token mismatch: received {public_token}, expected {db_public_token}")
            await websocket.close(code=1008)
            return

        # ✅ Debug public_manager state before using it
        if public_manager is None:
            print("❌ [ERROR] public_manager is None!")
            await websocket.close(code=1011)  # Internal Server Error
            return

        # ✅ Add WebSocket connection to the manager
        print(f"🔹 Connecting WebSocket for session_token={session_token}...")
        connection_result = await public_manager.connect(websocket, session_token)
        print(f"✅ Connected to WebSocket Manager: {connection_result}")
        await asyncio.sleep(0.5)
        print("waiting a bit before sending the initial state")
        # ✅ Fetch initial state safely
        try:
            initial_state = {
                "type": "update_state",
                "data": OrderManager_state.get_public_state(session_token),
            }
            print(f"📤 Sending initial state: {json.dumps(initial_state, indent=2)}")
            await websocket.send_text(json.dumps(initial_state))
        except Exception as e:
            print(f"⚠️ [ERROR] Could not fetch or send initial state: {e}")

        # ✅ Listen for messages from the client
        while True:
            try:
                message = await websocket.receive_text()
                print(f"📩 Received from client: {message}")
                

            except WebSocketDisconnect as e:
                print(f"🔻 [DISCONNECT] Client disconnected for session_token={session_token}, reason={e}")
                break  

            except Exception as e:
                print(f"⚠️ [ERROR] Unexpected issue receiving message: {e}")

    except Exception as e:
        print(f"⚠️ [ERROR] Unexpected WebSocket error: {e}")

    finally:
        try:
            print(f"🔹 Disconnecting WebSocket for session_token={session_token}...")
            disconnect_result = await public_manager.disconnect(websocket, session_token)
            print(f"✅ WebSocket disconnected: {disconnect_result}")
        except Exception as e:
            print(f"⚠️ [ERROR] Failed to disconnect WebSocket: {e}")

        print(f"🔻 Removed from WebSocket manager: session_token={session_token}\n")

"""
"""
# HTTP endpoint to get the public token (for the doctor to share)
@app.get("/dashboard/public-token")
def get_public_token(session_token: str = Query(...)):  # Required query param
    print(f"Session Token Requested: {session_token}") 
    session_data = state.get_session(session_token)

    if not session_data:
        print("Session Not Found")
        return {"error": "Session not found", "sessionToken": session_token}

    public_token = session_data.get("public_token")
    print(f"Stored Public Token: {public_token}")

    return {
        "sessionToken": session_token,
        "publicToken": public_token
    }
"""
@app.get("/")
def read_root():
    return {"message": "Python Backend Connected!"}

@app.post("/login")
async def login(
    login_request: LoginRequest, 
    response: Response, 
    db: Session = Depends(get_db)
):
    # Debug: log entire login_request data
    print("DEBUG: Received login request data:", login_request.dict())

    print(f"DEBUG: Received login request for username: {login_request.username}")

    # Fetch doctor from the database
    doctor = db.query(Doctor).filter(Doctor.username == login_request.username).first()
    print("DEBUG: Doctor fetched from DB:", doctor)
    
    if not doctor:
        print("DEBUG: No doctor found with that username.")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Debug: verify password presence and compare
    print("DEBUG: Verifying password...")
    if not pwd_context.verify(login_request.password, doctor.password):
        print("DEBUG: Invalid password provided. Expected:", doctor.password, "Received:", login_request.password)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    print("DEBUG: Password verified successfully.")

    # Check if an active session already exists for the doctor
    existing_session = db.query(SessionModel).filter(SessionModel.doctor_id == doctor.id).first()
    print("DEBUG: Existing session fetched:", existing_session)
    
    if existing_session:
        session_token = existing_session.session_token
        public_token = existing_session.public_token
        print(f"DEBUG: Existing session found for doctor {doctor.id}: {session_token}")
    else:
        # Generate a new session token
        session_token = str(uuid4())
        public_token = str(uuid4())
        print(f"DEBUG: Generated new session token: {session_token}")
        print(f"DEBUG: Generated new public token: {public_token}")

        # Store session in the database
        new_session = SessionModel(
            session_token=session_token, 
            doctor_id=doctor.id,
            public_token=public_token
        )
        db.add(new_session)
        db.commit()
        print("DEBUG: New session stored in the database.")

    # Set session cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=True,          # ✅ REQUIRED
        samesite="none",      # ✅ REQUIRED (lowercase!)
        max_age=3600
    )

    print("DEBUG: Session cookie set successfully.")

    # Debug: log final response content
    response_content = {
        "message": "Login successful",
        "id": doctor.id,
        "name": doctor.name,
        "specialization": doctor.specialization,
        "session_token": str(session_token),
        "public_token": str(public_token)
    }
    print("DEBUG: Returning response content:", response_content)

    return {
        "message": "Login successful",
        "id": doctor.id,
        "name": doctor.name,
        "specialization": doctor.specialization,
        "session_token": str(session_token),
        "public_token": str(public_token)
    }


"""
@app.post("/login")
async def login(login_request: LoginRequest, response: Response, db: Session = Depends(get_db)):
    print(f"Received login request for username: {login_request.username}")
    doctor = db.query(Doctor).filter(Doctor.username == login_request.username).first()
    
    if not doctor:
        print("No doctor found with that username.")
    
    if doctor and pwd_context.verify(login_request.password, doctor.password):
        print("Password verified successfully.")
        session_token = str(uuid4())  # Generate unique session ID
        print(f"Generated session token: {session_token}")
        
        # Store session in the database
        db.add(SessionModel(session_token=session_token, doctor_id=doctor.id))
        db.commit()
        print("Session stored in the database.")
        
        # Set session cookie
        response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,
            secure=False,  
            samesite="Lax",
            max_age=3600
        )
        print("Session cookie set successfully.")
        
        return JSONResponse(
    content={
        "message": "Login successful",
        "id": doctor.id,
        "name": doctor.name,
        "session_token": session_token  # Add the session token here
    },
    status_code=200
)

    
    print("Invalid credentials provided.")
    raise HTTPException(status_code=401, detail="Invalid credentials") 
"""

@app.post("/logout")
async def logout(req: Request, logout_request: LogoutRequest = Body(...)):
    # Optionally, if requested, reset the averageInspectionTime (or ignore if not needed)
    if logout_request.resetAverageInspectionTime:
        req.session["averageInspectionTime"] = 60  # Reset the value if needed

    # Clear the session
    req.session.clear()

    # Create a response and explicitly delete the session cookie
    response = JSONResponse(
        content={"message": "Logged out and session reset."},
        status_code=200
    )

    # Delete the session cookie (the default cookie name is "session_token")
    response.delete_cookie(key="session_token", path="/")

    # Alternatively, set the cookie to expire in the past (effectively deletes the cookie)
    response.set_cookie(
        key="session_token",  # Make sure this matches the cookie name used in login
        value="",
        expires=(datetime.now(timezone.utc) - timedelta(days=1)).strftime("%a, %d-%b-%Y %H:%M:%S GMT"),
        path="/"
    )

    return response
# @app.post("/logout")
# async def logout(req: Request, logout_request: LogoutRequest = Body(...)):
#     # Optionally, if requested, reset the averageInspectionTime (or ignore if not needed)
#     if logout_request.resetAverageInspectionTime:
#         req.session["averageInspectionTime"] = 60

#     # Clear the session
#     req.session.clear()

#     # Create a response and explicitly delete the session cookie.
#     response = JSONResponse(
#         content={"message": "Logged out and session reset."},
#         status_code=200
#     )
#     # Delete the session cookie (the default cookie name is "session")
#     response.delete_cookie(key="session", path="/")
#     # Alternatively, set the cookie to expire in the past.
#     response.set_cookie(
#         key="session",
#         value="",
#         expires=(datetime.now(timezone.utc) - timedelta(days=1)).strftime("%a, %d-%b-%Y %H:%M:%S GMT"),
#         path="/"
#     )
#     return response

@app.get("/get_next_doctor_id")
def get_next_doctor_id(db: Session = Depends(get_db)):
    max_id = db.query(func.max(Doctor.id)).scalar()
    next_id = 1 if max_id is None else max_id + 1
    return next_id
"""
@app.get("/get_next_doctor_id")
def get_next_doctor_id(db: Session = Depends(get_db)):
    max_id = db.query(func.max(Doctor.id)).scalar()
    next_id = 1 if max_id is None else max_id + 1
    return next_id
"""
@app.post("/add_doctor")
def add_doctor(doctor: DoctorCreate, db: Session = Depends(get_db)):
    existing_doctor = db.query(Doctor).filter(Doctor.username == doctor.username).first()
    if existing_doctor:
        raise HTTPException(status_code=400, detail="Username already exists")

    hashed_password = pwd_context.hash(doctor.password)

    new_doctor = Doctor(
        username=doctor.username,
        password=hashed_password,
        name=doctor.name,
        specialization=doctor.specialization
    )

    db.add(new_doctor)
    db.commit()  # ✅ PostgreSQL will auto-assign an ID here
    db.refresh(new_doctor)  # ✅ Ensures new_doctor has the correct ID

    return {"message": "Doctor added successfully", "id": new_doctor.id}

@app.put("/edit_doctor/{doctor_id}")
def update_doctor(doctor_id: int, doctor_data: DoctorUpdate, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    doctor.name = doctor_data.name
    doctor.specialization = doctor_data.specialization
    doctor.username = doctor_data.username

    if doctor_data.password:
        hashed_password = pwd_context.hash(doctor_data.password)
        doctor.password = hashed_password
    
    db.commit()
    db.refresh(doctor)

    return {"message": "Doctor updated successfully", "doctor": doctor}


@app.get("/view_doctors")
def view_doctors(db: Session = Depends(get_db)):
    doctors = db.query(Doctor).all()
    if not doctors:
        raise HTTPException(status_code=404, detail="No doctors found")
    
    return {"doctors": doctors}


@app.get("/view_doctors/{doctor_id}", response_model=DoctorResponse)

def get_doctor_by_id(doctor_id: int, db: Session = Depends(get_db)):
    print(f"Fetching doctor with ID: {doctor_id}")  # Debugging
    
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    
    if not doctor:
        print("Doctor not found in database")  # Debugging
        raise HTTPException(status_code=404, detail="Doctor not found")
    
    print(f"Doctor found: {doctor}")  # Debugging
    return doctor

@app.delete("/delete_doctor/{doctor_id}")
def delete_doctor(doctor_id: int, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    
    # Delete related session records if they exist
    db.query(SessionModel).filter(SessionModel.doctor_id == doctor_id).delete()

    db.delete(doctor)
    db.commit()
    return {"message": "Doctor deleted successfully"}

@app.get("/generate-qr-old/{public_token}/{session_token}")
def generate_qr(public_token: str, session_token: str, db: Session = Depends(get_db)):
    # Validate session token
    session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    if not session:
        return {"error": "Invalid session token"}
    
    # Generate shareable URL
    shareable_url = f"https://clinic-management-system-27d11.web.app/dashboard?publicToken={public_token}&sessionToken={session_token}"
    
    # Generate QR Code
    try:
        font_path = os.path.join(os.path.dirname(__file__), "arial.ttf")
        qr = qrcode.make(shareable_url)
    except Exception:
        return {"error": "Failed to generate QR code"}
    
    # Resize QR code
    try:
        qr = qr.resize((1200, 1200), Image.Resampling.LANCZOS)
    except Exception:
        return {"error": "Failed to resize QR code"}
    
    final_height = 1200 + 150
    final_image = Image.new("RGB", (1200, final_height), "white")
    final_image.paste(qr, (0, 0))
    
    draw = ImageDraw.Draw(final_image)
    
    try:
        if not os.path.exists(font_path):
            raise FileNotFoundError(f"Font file not found at {font_path}")
        font = ImageFont.truetype(font_path, 100)
    except Exception:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 100)
        except Exception:
            font = ImageFont.load_default()
    
    text = "Scan for Live Updates"
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height_calculated = text_bbox[3] - text_bbox[1]
    
    text_x = (1200 - text_width) // 2
    text_y = 1200 + ((150 - text_height_calculated) // 2)
    
    try:
        draw.text((text_x, text_y), text, fill="black", font=font)
    except Exception:
        return {"error": "Failed to add text to image"}
    
    try:
        img_io = io.BytesIO()
        final_image.save(img_io, format="PNG")
        img_io.seek(0)
    except Exception:
        return {"error": "Failed to save final image"}
    
    return StreamingResponse(img_io, media_type="image/png")


@app.get("/generate-qr/{public_token}")
def generate_qr(public_token: str):
    print("\n========== DEBUG: /generate-qr CALLED ==========")
    print("Incoming public_token:", public_token)

    # Build public chatbot URL
    shareable_url = f"https://chat-for-me-ai-login.vercel.app/chatbot?publicToken={public_token}"
    print("Generated shareable_url:", shareable_url)

    # Try generating QR code
    try:
        qr = qrcode.make(shareable_url)
        qr = qr.resize((1200, 1200), Image.Resampling.LANCZOS)
        print("QR code generated successfully.")
    except Exception as e:
        print("ERROR: Failed to create QR code:", e)
        raise HTTPException(status_code=500, detail="Failed to generate QR code")

    # Create final image with text
    final_height = 1200 + 150
    final_image = Image.new("RGB", (1200, final_height), "white")
    final_image.paste(qr, (0, 0))

    draw = ImageDraw.Draw(final_image)

    # Load font safely
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 100)
    except:
        font = ImageFont.load_default()

    text = "Scan to Open Chatbot"
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]

    text_x = (1200 - text_width) // 2
    text_y = 1220

    draw.text((text_x, text_y), text, fill="black", font=font)
    print("Text added under QR code.")

    # Return PNG as StreamingResponse
    try:
        img_buffer = io.BytesIO()
        final_image.save(img_buffer, format="PNG")
        img_buffer.seek(0)
    except Exception as e:
        print("ERROR: Failed to save image:", e)
        raise HTTPException(status_code=500, detail="Failed to save image")

    print("========== /generate-qr END ==========\n")
    return StreamingResponse(img_buffer, media_type="image/png")


@app.get("/get-doctor-id")
def get_doctor_id(username: str = Query(...), db: Session = Depends(get_db)):
    """
    Returns the doctor's ID associated with a given username.
    """
    # Look up the doctor by username
    doctor = db.query(Doctor).filter(Doctor.username == username).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    return {"doctor_id": doctor.id}




@app.get("/doctors/{doctor_id}", response_model=DoctorResponse)
    
def get_doctor_by_id(doctor_id: int, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    
    return doctor
    
@app.get("/get-doctor-name/{doctor_id}")
def get_doctor_name(doctor_id: int, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    return {"doctor_name": doctor.name}


"""
# Endpoint to get a doctor by ID
@app.post("/add_doctor")
def add_doctor(doctor: DoctorCreate, db: Session = Depends(get_db)):
    # Check if the username already exists
    existing_doctor = db.query(Doctor).filter(Doctor.username == doctor.username).first()
    if existing_doctor:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Check if the ID is already taken
    id_exists = db.query(Doctor).filter(Doctor.id == doctor.id).first()
    if id_exists:
        raise HTTPException(status_code=400, detail="Doctor ID already exists")

    # Hash the password
    hashed_password = pwd_context.hash(doctor.password)

    # Create new doctor record
    new_doctor = Doctor(
        id=doctor.id,
        username=doctor.username,
        password=hashed_password,
        name=doctor.name,
        specialization=doctor.specialization
    )

    # Add to database
    db.add(new_doctor)
    db.commit()
    db.refresh(new_doctor)
    return {"message": "Doctor added successfully"}

# Endpoint to update a doctor
@app.put("/edit_doctor/{doctor_id}")
def update_doctor(doctor_id: int, doctor_data: DoctorUpdate, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # Update doctor details
    doctor.name = doctor_data.name
    doctor.specialization = doctor_data.specialization
    doctor.username = doctor_data.username

    # Hash the password if a new one is provided
    if doctor_data.password:
        hashed_password = pwd_context.hash(doctor_data.password)
        doctor.password = hashed_password
    db.commit()
    db.refresh(doctor)

    return {"message": "Doctor updated successfully", "doctor": doctor}

@app.get("/view_doctors")
def view_doctors(db: Session = Depends(get_db)):
    doctors = db.query(Doctor).all()
    if not doctors:
        raise HTTPException(status_code=404, detail="No doctors found")
    
    return {"doctors": doctors}

@app.get("/view_doctors/{doctor_id}")
async def get_doctor(doctor_id: int, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if doctor:
        return {"doctor": doctor}
    raise HTTPException(status_code=404, detail="Doctor not found")

@app.delete("/delete_doctor/{doctor_id}")
def delete_doctor(doctor_id: int, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    
    db.delete(doctor)
    db.commit()
    return {"message": "Doctor deleted successfully"}

@app.get("/doctors/{doctor_id}", response_model=DoctorResponse)
def get_doctor_by_id(doctor_id: int, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    
    return doctor

@app.get("/get-doctor-id/{session_token}")
def get_doctor_id(session_token: str, db: Session = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    if session and session.doctor_id:
        return {"doctor_id": session.doctor_id}
    return {"error": "Invalid session or doctor not found"}
"""
def is_relevant_to_neurology(user_input):
    relevant_keywords = [
        "neurology", "brain", "nervous system", "epilepsy", "migraine",
        "stroke", "parkinson", "multiple sclerosis","sarfraz", "alzheimers",
        "headache", "nerve pain", "neurologist", "doctor", "appointment"
    ]
    
    irrelevant_keywords = [
        "recipe", "cook", "food", "biryani", "weather", "movie",
        "cricket", "sports", "joke", "entertainment", "news", "politics"
    ]
    
    user_input = user_input.lower()
    
    if any(word in user_input for word in irrelevant_keywords):
        return False  # Block input
    if any(word in user_input for word in relevant_keywords):
        return True  # Allow input
    return False  # Default to blocking ambiguous inputs
#chatapi for Rafis Kitchen
@app.post("/api/chatRK")
async def chat(request: ChatRequestRK):
    message = request.message
    if not message:
        print("Error: Missing message in request body.")
        return {"error": "Missing message in request body."}, 400

    try:
        # Debugging: Print the received message
        print(f"Received message: {message}")

        # Create system message for the assistant
        system_message = {
            'role': 'system',
            'content': (
                "You are a virtual assistant for a restaurant named Rafis Kitchen. "
                "Your role is strictly limited to answering questions about:\n"
                "- The restaurant is located in America.\n"
                "If the user asks about *anything else* (e.g., cooking, entertainment, religion, technology, etc.), "
                "you MUST respond exactly with:\n"
                "'I'm sorry, but I can only assist with restaurant-related questions or information related to Rafis Kitchen.'\n"
                "Do NOT provide any other information outside of this scope."
            )
        }

        # Create the user message object
        user_message = {
            'role': 'user',
            'content': message
        }

        # Sending request to OpenAI API using the updated method for new API versions
        print("Sending request to OpenAI API...")

        try:
            chat_completion = openai.ChatCompletion.create(
                model="gpt-4",  # Replace with your desired model (adjust version if needed)
                temperature=0.2,
                messages=[system_message, user_message]  # Correct parameter for chat models
            )
        except Exception as api_error:
            print(f"OpenAI API error: {api_error}")
            raise HTTPException(status_code=500, detail="Failed to fetch response from OpenAI")

        # Extract the reply from the response
        bot_reply = chat_completion['choices'][0]['message']['content']

        # Debugging: Print the bot's reply
        print(f"Bot's reply: {bot_reply}")

        # Returning the response
        return {"reply": bot_reply}

    except Exception as e:
        # Handle any unexpected errors
        print(f"Unexpected error: {e}")
        return {"error": "Oops, something went wrong on our end."}, 500

@app.post("/api/chat-new")
def chat(message: str = Body(...), user_id: int = Body(...), db: Session = Depends(get_db)):
    print("==============================================")
    print("[DEBUG] Entered /api/chat endpoint")
    print(f"[DEBUG] user_id={user_id}, message='{message}'")
    print(f"[DEBUG] db injected: {db}")
    print(f"[DEBUG] db type: {type(db)}")

    if db is None:
        print("[ERROR] Database session is None! FastAPI did not inject a session.")
        raise HTTPException(status_code=500, detail="Database session is None")

    # --- Test DB connection properly ---
    try:
        _ = db.execute(text("SELECT 1")).fetchone()  # wrap raw SQL in text()
        print("[DEBUG] Database connection test: OK ✅")
    except Exception as e:
        print(f"[ERROR] Database connection test failed: {e}")
        raise HTTPException(status_code=500, detail="Database session invalid or closed")

    # --- Fetch KB for this doctor ---
    try:
        print("[DEBUG] Querying KnowledgeBase for user_id:", user_id)
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.user_id == user_id).first()
        print("[DEBUG] Query executed successfully.")
    except Exception as e:
        print(f"[ERROR] Query to KnowledgeBase failed: {e}")
        raise HTTPException(status_code=500, detail=f"Database query failed: {e}")

    if not kb:
        print(f"[WARNING] No knowledge base found for user_id={user_id}")
        return {"reply": "Sorry, I have no knowledge to answer this yet."}

    print(f"[DEBUG] Knowledge base retrieved: id={kb.id}, content_length={len(kb.content)}")

    # --- Build prompt using doctor's KB ---
    prompt = f"You are Dr. {user_id}. Answer the question concisely based on the knowledge below.\n\nKnowledge:\n{kb.content}\n\nUser: {message}\n\nInstructions: Provide a brief summary in 2-3 sentences. Avoid long paragraphs."

    print(f"[DEBUG] Prompt length: {len(prompt)} characters")

    # --- Call OpenAI API ---
    try:
        print("[DEBUG] Sending prompt to OpenAI API...")
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500
        )

        bot_reply = response.choices[0].message.content
        print(f"[DEBUG] Bot reply length: {len(bot_reply)} characters")

        print("[DEBUG] Returning successful response ✅")
        return {"reply": bot_reply}

    except Exception as e:
        print(f"[ERROR] OpenAI API call failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate reply from OpenAI")

                                
@app.post("/api/chat")
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    try:
        if not request.message:
            raise HTTPException(status_code=400, detail="Message is required")
        if request.user_id is None:
            raise HTTPException(status_code=400, detail="User ID is required")

        user_id = request.user_id

        # Set system message
        if user_id == 2:
            if not is_relevant_to_neurology(request.message):
                return {"reply": "I'm sorry, but I can only assist with Neurology-related questions or information related to Dr. Sarfraz."}
            system_message_content = (
                "You are a virtual assistant for Dr. Sarfraz, a neurologist. "
                "Your role is strictly limited to answering questions about:\n"
                "- Neurological health issues\n"
                "- Dr. Sarfraz's qualifications and services\n"
                "- Appointments and clinic information\n"
                "\n"
                "If the user asks about *anything else* (e.g., cooking, entertainment, religion, technology, etc.), you MUST respond exactly with:\n"
                "'I'm sorry, but I can only assist with Neurology-related questions or information related to Dr. Sarfraz.'\n"
                "Do NOT provide any other information outside of this scope."
            )

        elif user_id == 3:
            system_message_content = (
                "You are a virtual assistant for Chand, a hairstylist. "
                "He charges Rs.500 for a haircut and 1000 for a full body massage."
            )

        elif user_id == 15:
            system_message_content = (
                "You are a virtual assistant for Kips, an educational institute. "
                "If you are asked how Farhan is doing then ask for the password and if the password given is 123 then tell them that Farhan is doing well in all subjects except math in which he needs to work hard to improve."
            )

        else:
            system_message_content = (
                "You are my virtual assistant, trained to assist clients with any questions or tasks they may have. "
                "I have expertise in Python, having studied Automate the Boring Stuff with Python and Master Python for Data Science. "
                "When interacting with clients, provide insightful responses that highlight my skills and experience. "
                "Only accept projects that align with my expertise, ensuring that I can deliver high-quality results. "
                "If the client wishes to communicate further, provide my email address: proactive1.san@gmail.com. "
                "Your goal is to help attract relevant projects that match my background in Python programming and data science."
            )

        cached_prompt = get_cached_system_prompt(user_id)
        if cached_prompt:
            system_message_content = cached_prompt
        else:
            # build your system prompt based on user_id...
            set_cached_system_prompt(user_id, system_message_content)

        system_message = {"role": "system", "content": system_message_content}
        user_message = {"role": "user", "content": request.message}

        # API usage tracking
        current_month = date.today().replace(day=1)
        api_usage_entry = db.query(APIUsageModel).filter_by(doctor_id=user_id, date=current_month).first()

        if api_usage_entry:
            if api_usage_entry.request_count >= 1000:
                raise HTTPException(status_code=429, detail="Daily request limit reached (1000 requests). Please try again tomorrow.")
        else:
            api_usage_entry = APIUsageModel(
                doctor_id=user_id,
                request_type="chatbot",
                request_count=0,
                date=current_month
            )
            db.add(api_usage_entry)
            db.commit()

        # OpenAI call
        try:
            # Use the updated client method for the new API version (openai>=1.0.0)
            chat_completion = client.chat.completions.create(
                model="gpt-4",
                temperature=0.2,
                messages=[system_message, user_message]
            )
            bot_reply = chat_completion.choices[0].message.content
        except Exception as api_error:
            print(f"OpenAI API error: {api_error}")
            raise HTTPException(status_code=500, detail="Failed to fetch response from OpenAI")

        # Extract and return the response from the OpenAI API
        bot_reply = chat_completion['choices'][0]['message']['content']  # Updated to match the new response structure
        api_usage_entry.request_count += 1
        db.commit()

        return {"reply": bot_reply}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@app.get("/get-doctor-username")
def get_doctor_username(session_token: str = Query(...), db: Session = Depends(get_db)):
    """
    Returns the doctor's username associated with a given session token.
    """
    # Look up the session using the session_token
    session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Look up the doctor using doctor_id from session
    doctor = db.query(Doctor).filter(Doctor.id == session.doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # Return doctor username
    return {"username": doctor.username}

@app.post("/api/chat-whatsapp")
def chat_whatsapp(
    payload: dict = Body(...),
    db: Session = Depends(get_db)
):
    print("\n==================== DEBUG: /api/chat-whatsapp CALLED ====================")
    print("Incoming payload:", payload)

    # Extract incoming fields
    message = payload.get("message")
    public_token = payload.get("public_token")
    chat_access_token = payload.get("chat_access_token")

    print(f"Parsed parameters:\n message='{message}'\n public_token={public_token}\n chat_access_token={chat_access_token}")

    # -----------------------------------------------------------
    # Validate required fields
    # -----------------------------------------------------------
    if not message or not public_token:
        print("[ERROR] Missing message or public_token")
        raise HTTPException(status_code=400, detail="message and public_token are required")

    # -----------------------------------------------------------
    # STEP 1 — Lookup session by public_token
    # -----------------------------------------------------------
    session_record = db.query(SessionModel).filter(
        SessionModel.public_token == public_token
    ).first()

    print("DEBUG: Session lookup result:", session_record)

    if not session_record:
        print(f"[ERROR] No session found for public_token = {public_token}")
        raise HTTPException(status_code=404, detail="Invalid chatbot link")

    doctor_id = session_record.doctor_id
    print("DEBUG: Session belongs to doctor_id =", doctor_id)

    # -----------------------------------------------------------
    # STEP 2 — Validate password if required
    # -----------------------------------------------------------
    requires_password = session_record.require_password
    print("DEBUG: require_password =", requires_password)

    if requires_password:
        print("Password protection is enabled")

        if not chat_access_token:
            print("[ERROR] chat_access_token missing")
            raise HTTPException(status_code=401, detail="Password required")

        print("chat_access_token provided → access allowed")
    else:
        print("Chatbot is public → no password needed")

    # -----------------------------------------------------------
    # STEP 3 — Fetch WhatsAppKnowledgeBase using doctor_id
    # WhatsAppKnowledgeBase.user_id actually stores doctor_id
    # -----------------------------------------------------------
    kb = db.query(WhatsAppKnowledgeBase).filter(
        WhatsAppKnowledgeBase.user_id == doctor_id
    ).first()

    if not kb:
        print(f"[WARNING] No KB found for doctor_id = {doctor_id}")
        return {"reply": "Sorry, no knowledge base is available yet."}

    print("KB fetched successfully. KB size:", len(kb.content), "characters")

    # Compute KB hash for caching
    kb_hash = hashlib.md5(kb.content.encode("utf-8")).hexdigest()
    print("KB hash:", kb_hash)

    # -----------------------------------------------------------
    # STEP 4 — Build or reuse vector store
    # -----------------------------------------------------------
    if (doctor_id not in vector_stores) or (vector_stores[doctor_id]["kb_hash"] != kb_hash):
        print("Vector store missing or outdated → rebuilding...")

        chunks = chunk_text(kb.content, chunk_size=500, overlap=50)
        embeddings = embed_texts(chunks)

        vector_stores[doctor_id] = {
            "chunks": chunks,
            "embeddings": np.array(embeddings),
            "kb_hash": kb_hash
        }

        print(f"[DEBUG] Vector store rebuilt with {len(chunks)} chunks")
    else:
        print("[DEBUG] Using cached vector store")

    store = vector_stores[doctor_id]

    # -----------------------------------------------------------
    # STEP 5 — Embed user question and compute similarity
    # -----------------------------------------------------------
    query_embedding = np.array(embed_texts([message])[0])
    print("Query embedding shape:", query_embedding.shape)

    sims = cosine_similarity([query_embedding], store["embeddings"])[0]
    top_idx = sims.argmax()
    print(f"[DEBUG] Most relevant chunk index = {top_idx}, similarity = {sims[top_idx]:.4f}")

    relevant_chunk = store["chunks"][top_idx]

    # -----------------------------------------------------------
    # STEP 6 — Build prompt
    # -----------------------------------------------------------
    prompt = f"""
You are an AI assistant for doctor ID {doctor_id}.
Answer concisely (1–2 sentences) using ONLY the knowledge below.

Knowledge:
{relevant_chunk}

User question: {message}
    """.strip()

    print(f"[DEBUG] Prompt length = {len(prompt)} characters")

    # -----------------------------------------------------------
    # STEP 7 — OpenAI API call
    # -----------------------------------------------------------
    try:
        print("[DEBUG] Sending prompt to OpenAI...")

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300
        )

        bot_reply = response.choices[0].message.content
        print("[DEBUG] OpenAI response length:", len(bot_reply))

        print("==================== /api/chat-whatsapp END ====================\n")
        return {"reply": bot_reply}

    except Exception as e:
        print("[ERROR] OpenAI API failed:", e)
        print("==================== /api/chat-whatsapp ERROR END ====================\n")
        raise HTTPException(status_code=500, detail="Failed to generate AI reply")





"""
@app.post("/api/chat")
async def chat(request: ChatRequest, db: Session = Depends(get_db)):  # Inject DB session
    try:
        if not request.message:
            raise HTTPException(status_code=400, detail="Message is required")
        if request.user_id is None:
            raise HTTPException(status_code=400, detail="User ID is required")

        # Identify user
        user_id = request.user_id
         # Set system message based on user_id
        if user_id == 10:
           if not is_relevant_to_neurology(request.message):
                return {"reply": "I'm sorry, but I can only assist with Neurology-related questions or information related to Dr. Sarfraz."}
           system_message_content = (
        "You are a virtual assistant for Dr. Sarfraz, a neurologist. "
        "Your role is strictly limited to answering questions about:\n"
        "- Neurological health issues\n"
        "- Dr. Sarfraz's qualifications and services\n"
        "- Appointments and clinic information\n"
        "\n"
        "If the user asks about *anything else* (e.g., cooking, entertainment, religion, technology, etc.), you MUST respond exactly with:\n"
        "'I'm sorry, but I can only assist with Neurology-related questions or information related to Dr. Sarfraz.'\n"
        "Do NOT provide any other information outside of this scope."
    )
        elif user_id == 3:
            system_message_content = (
                "You are a virtual assistant for Chand, a hairstylist. "
                "He charges Rs.500 for a haircut and 1000 for a full body massage."
            )
        elif user_id == 15:
            system_message_content = (
                "You are a virtual assistant for Kips, an educational institute. "
                "if you are asked how Farhan is doing then ask for the password and if the password given is 123 then tell them that Farhan is doing well in all subjects except math in which he needs to work hard to improve"
            )
        else:
            system_message_content = (
                "You are my virtual assistant, trained to assist clients with any questions or tasks they may have. "
                "I have expertise in Python, having studied Automate the Boring Stuff with Python and Master Python for Data Science. "
                "When interacting with clients, provide insightful responses that highlight my skills and experience."
                " Only accept projects that align with my expertise, ensuring that I can deliver high-quality results."
                " If the client wishes to communicate further, provide my email address: proactive1.san@gmail.com."
                " Your goal is to help attract relevant projects that match my background in Python programming and data science."
            )

        system_message = {"role": "system", "content": system_message_content}
        user_message = {"role": "user", "content": request.message}

        # Log API usage
        current_month = date.today().replace(day=1)  # Ensures a full date object (e.g., 2024-03-01)
        api_usage_entry = db.query(APIUsageModel).filter_by(doctor_id=user_id, date=current_month).first()

        if api_usage_entry:
            if api_usage_entry.request_count >= 1000:
                
                raise HTTPException(status_code=429, detail="Daily request limit reached (1000 requests). Please try again tomorrow.")
        else:
            api_usage_entry = APIUsageModel(doctor_id=user_id, request_type="chatbot", request_count=0, date=current_month)
            db.add(api_usage_entry)
            db.commit()
        # OpenAI API call
        try:
            chat_completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,  # Lower = more predictable
            messages=[system_message, user_message]
        )        
        except Exception as api_error:
            print(f"Error: OpenAI API call failed: {api_error}")
            raise HTTPException(status_code=500, detail="Failed to fetch response from OpenAI")

        # Extract response
        bot_reply = chat_completion.choices[0].message.content   
        # Update API usage
        api_usage_entry.request_count += 1
        db.commit()  # Save changes
        return {"reply": bot_reply}

    except Exception as e:        
        raise HTTPException(status_code=500, detail=str(e))
    """

"""
# GET /patients - Fetch all patients
@app.get("/patients", response_model=List[PatientResponse])
def get_patients():
    db = SessionLocal()
    patients = db.query(Patient).all()
    db.close()
    return patients

# POST /patients - Add a new patient
@app.post("/patients", response_model=PatientResponse)
def add_patient(patient: PatientCreate):
    db = SessionLocal()
    new_patient = Patient(name=patient.name)
    db.add(new_patient)
    db.commit()
    db.refresh(new_patient)
    db.close()
    return new_patient



# DELETE /patients/{id} - Remove a patient
@app.delete("/patients/{id}")
def delete_patient(id: int):
    db = SessionLocal()
    patient = db.query(Patient).filter(Patient.id == id).first()
    if not patient:
        db.close()
        raise HTTPException(status_code=404, detail="Patient not found")
    
    db.delete(patient)
    db.commit()
    db.close()
    return {"message": "Patient deleted successfully"}
    """
