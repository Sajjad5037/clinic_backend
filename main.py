from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import date
from starlette.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String,func,ForeignKey,Boolean,Text,text,Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.dialects.postgresql import UUID
import uvicorn
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from typing import List,Dict,Set,Optional
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect,Request,Body,Response,Query
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

from PIL import Image, ImageDraw, ImageFont



# Fetch the API key from environment variables
openai_api_key = os.getenv("OPENAI_API_KEY_S")

# Check if API key is set
if not openai_api_key:
    print("Error: OPENAI_API_KEY_S is not set in environment variables.")

# Initialize OpenAI client
client = OpenAI(api_key=openai_api_key)



secret_key = os.getenv("SESSION_SECRET_KEY", "fallback-secret-for-dev")



app = FastAPI()
session_states = {}
clients=[]
Base = declarative_base()
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

class ChatRequest(BaseModel):
    message: str
    user_id: int
# WebSocket connection manager to handle multiple clients... it is responsible for connecting, disconnecting and broadcasting messages
class ConnectionManager:
    def __init__(self):
        # List to store all active WebSocket connections
        self.active_connections: Set[WebSocket] = set()  # Change from List to Set
        # Dictionary to store the WebSocket connections with their session tokens
        self.client_tokens: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, session_token: str):
        # Accept the WebSocket connection and add it to the list
        await websocket.accept()
        self.active_connections.add(websocket)
        self.client_tokens[websocket] = session_token  # Add session token to this connection
        print(f"New client connected with token {session_token}! Total clients: {len(self.active_connections)}")
    def disconnect(self, websocket: WebSocket, session_token: str):
        # Remove the WebSocket from the session-specific connection set
        if session_token in self.active_connections:
            self.active_connections[session_token].discard(websocket)
            # Clean up the session if no connections remain
            if not self.active_connections[session_token]:
                del self.active_connections[session_token]
        
        
    async def broadcast(self, message: dict):
        print(f"Broadcasting message: {message}")
        if not self.active_connections:
            print("No active connections to broadcast to.")
            return
        print(f"Active Connections: {len(self.active_connections)}")
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                print(f"Error sending message to a client: {e}")
    async def broadcast_to_session(self, session_token: str, message: dict):
        # Broadcast message to all WebSocket connections for a specific session
        print(f"Broadcasting message to session {session_token}: {message}")
        
        if not self.active_connections:
            print("No active connections to broadcast to.")
            return

        # Iterate through active connections and send message if the session_token matches
        for connection, token in self.client_tokens.items():
            print(f"Connection: {connection}")
            print(f"Token associated with this connection: {token}")
            print(f"Session token provided: {session_token}\n")

            # Check if the session token matches the token for this connection
            if token == session_token:
                try:
                    print(f"Sending message to connection: {connection} with session token: {session_token}")
                    await connection.send_text(json.dumps(message))
                except Exception as e:
                    print(f"Error sending message to session {session_token}: {e}")
            else:
                print(f"Skipping connection {connection} as its token does not match the provided session token.")
class LogoutRequest(BaseModel):
    resetAverageInspectionTime: bool = True


# Instantiate the manager globally
manager = ConnectionManager()
# Server-side state for real-time features
class DashboardState:
    def __init__(self):
        self.sessions = {}  # Store session-specific state

    def get_session(self, session_token):
        if session_token not in self.sessions:
            # Create a new session if the token is not found
            self.sessions[session_token] = {
                "doctor_name": None,
                "patients": [],
                "current_patient": None,
                "inspection_times": [],
                "start_time": None,
                "average_inspection_time": 60,
                
                "notices": []
            }

        print(f"line 126: {self.sessions[session_token]}")
        return self.sessions[session_token]

    def add_patient(self, session_token, patient_name: str):
        session = self.get_session(session_token)
        session["patients"].append(patient_name)
        if not session["current_patient"]:
            session["current_patient"] = patient_name
            session["start_time"] = time.time()

    def mark_as_done(self, session_token):
        session = self.get_session(session_token)
        if not session["current_patient"]:
            return
        
        duration = time.time() - session["start_time"]
        session["inspection_times"].append(duration)
        
        session["patients"].pop(0)
        session["current_patient"] = session["patients"][0] if session["patients"] else None
        session["start_time"] = time.time() if session["current_patient"] else None

    def get_average_time(self, session_token):
        session = self.get_session(session_token)
        return round(sum(session["inspection_times"]) / len(session["inspection_times"])) if session["inspection_times"] else 300

    def get_public_state(self, session_token):
        session = self.get_session(session_token)
        return {
            "patients": session["patients"],
            "currentPatient": session["current_patient"],
            "averageInspectionTime": self.get_average_time(session_token)
        }

    def reset_all(self, session_token):
        session = self.get_session(session_token)
        if not session:
            return

        session["patients"] = []
        session["current_patient"] = None
        session["inspection_times"] = []
        session["start_time"] = None
        session["average_inspection_time"] = 60

    def add_notice(self, session_token, notice: str):
        """Adds a notice to the session's notice board."""
        session = self.get_session(session_token)
        session["notices"].append(notice)

    def get_notices(self, session_token):
        """Returns the list of notices for the session."""
        session = self.get_session(session_token)
        return session["notices"]
    
    def remove_notice(self, session_token, index: int):
        """Removes a notice from the session's notice board by index."""
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
                "notices": []
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

    def remove_notice(self, session_token, index: int):
        """Removes a notice from the notice board by index."""
        session = self.get_session(session_token)
        if 0 <= index < len(session["notices"]):
            session["notices"].pop(index)

# Global state instance
OrderManager_state=OrderManagerState()
state = DashboardState()
public_manager = ConnectionManager() # Add a separate manager for public connections
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

    id = Column(Integer, primary_key=True, server_default=text("nextval('doctors_id_seq')"))
    username = Column(String, unique=True, index=True, nullable=False)
    password = Column(Text, nullable=False)  # Store hashed passwords efficiently
    name = Column(String, nullable=False)
    specialization = Column(String, nullable=False)
    api_usage = relationship("APIUsageModel", back_populates="doctor")
    # Relationship for railway resource usage (ensure the correct name!)
    railway_resource_usage = relationship("RailwayResourceUsageModel", back_populates="doctor", cascade="all, delete")


    # Define relationship for cascading delete
    sessions = relationship("SessionModel", back_populates="doctor", cascade="all, delete")

class SessionModel(Base):  # Handles authentication sessions
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_token = Column(UUID(as_uuid=True), unique=True, index=True, default=uuid.uuid4)
    doctor_id = Column(Integer, ForeignKey("doctors.id", ondelete="CASCADE"))
    is_authenticated = Column(Boolean, default=False)
    public_token=Column(UUID(as_uuid=True), unique=True, index=True, default=uuid.uuid4)

    doctor = relationship("Doctor", back_populates="sessions")  # Establish relationship

class APIUsageModel(Base):  # Tracks API usage per doctor
    __tablename__ = "api_usage"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    request_type = Column(String(50), nullable=False)  # e.g., "chatbot"
    request_count = Column(Integer, default=1)
    date = Column(Date, default=func.current_date())

    doctor = relationship("Doctor", back_populates="api_usage")
class RailwayResourceUsageModel(Base):  # Tracks Railway resource usage per doctor
    __tablename__ = "railway_resource_usage"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    resource_type = Column(String(50), nullable=False)  # e.g., "database", "API requests"
    usage_count = Column(Integer, default=1)  # Tracks number of times a resource is used
    date = Column(Date, default=func.current_date())  # Logs daily usage


    doctor = relationship("Doctor", back_populates="railway_resource_usage")

    

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
DATABASE_URL = os.getenv("PostgreSQL_database")  # Use PostgreSQL instead of SQLite
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "sajjad")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "shuwafF2016")

# Initialize PostgreSQL database engine
engine = create_engine(DATABASE_URL)  # Removed SQLite-specific arguments

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_admin(db: Session):
    """Ensure an admin user exists in the database."""
    admin = db.query(Doctor).filter_by(username=ADMIN_USERNAME).first()

    if not admin:
        admin = Doctor(
            username=ADMIN_USERNAME,
            password=pwd_context.hash(ADMIN_PASSWORD),
            name="Sajjad Ali Noor",
            specialization="Administrator"
        )
        db.add(admin)
        db.commit()  # ✅ Commit to assign admin.id
        db.refresh(admin)

    else:
        admin.password = pwd_context.hash(ADMIN_PASSWORD)  # Ensure password updates if changed
        db.commit()

    print("✅ Admin account created/updated successfully.")
    
    # ✅ Reset the sequence correctly only if no insert failed
    db.execute(text(f"SELECT setval('doctors_id_seq', (SELECT MAX(id) FROM doctors), false);"))
    db.commit()

# Create tables before initializing admin
Base.metadata.create_all(bind=engine)

# Ensure admin user exists
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


@app.websocket("/ws/{session_token}")
async def websocket_endpoint(websocket: WebSocket, session_token: str):
    # Authenticate and verify session token
    #session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    session = db.query(SessionModel).filter(SessionModel.session_token == uuid.UUID(session_token)).first()

    if not session:
        await websocket.close(code=1008)  # Invalid token, close connection
        return

    # Ensure each session gets its own independent state
    if session_token not in session_states:
        session_states[session_token] = DashboardState()  # Create a new state for this session
    state = session_states[session_token]  # Get the state for this session
    session_data = state.get_session(session_token)  # Get the session data
    # Add WebSocket connection to the manager based on session token
    await manager.connect(websocket, session_token)

    try:
        # Send initial state to the new client
        initial_state = {
            "type": "update_state",
            "data": {
                "patients": session_data["patients"],
                "currentPatient": session_data["current_patient"],
                "averageInspectionTime": state.get_average_time(session_token),
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
        await manager.disconnect(websocket, session_token)

        # Remove session state after disconnect
        if session_token in session_states:
            del session_states[session_token]

    except Exception as e:
        error_message = f"Unexpected error: {str(e)}\n{traceback.format_exc()}"
        print(error_message)


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


@app.websocket("/ws/OrderManager/{session_token}")
async def websocket_endpoint(websocket: WebSocket, session_token: str):
    # Authenticate and verify session token
    #session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    session = db.query(SessionModel).filter(SessionModel.session_token == uuid.UUID(session_token)).first()

    if not session:
        await websocket.close(code=1008)  # Invalid token, close connection
        return

    # Ensure each session gets its own independent state
    if session_token not in session_states:
        session_states[session_token] = OrderManagerState()  # Create a new state for this session
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
                state.add_item(session_token_current, message.get("item", ""))
                session_data = state.get_session(session_token_current)
                update = {
                    "type": "update_state",
                    "data": {
                        "preparingList": session_data["preparingList"],
                        "servingList": session_data["servingList"],
                        "notices": session_data["notices"],
                        "session_token": session_token_current
                    }
                }
                await manager.broadcast_to_session(session_token_current, update)

            
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
        secure=False,  # Change to True if using HTTPS
        samesite="Lax",
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

    return JSONResponse(
        content=response_content,
        status_code=200
    )

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

@app.get("/generate-qr/{public_token}/{session_token}")
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
    
    # Resize QR code to 4x4 inches at 300 DPI (i.e., 1200x1200 pixels)
    try:
        qr = qr.resize((1200, 1200), Image.Resampling.LANCZOS)
    except Exception:
        return {"error": "Failed to resize QR code"}
    
    # Create a new image with extra space for text (150 pixels extra height)
    final_height = 1200 + 150  # 1200 for QR + 150 for text
    final_image = Image.new("RGB", (1200, final_height), "white")
    final_image.paste(qr, (0, 0))
    
    # Add text below the QR code
    draw = ImageDraw.Draw(final_image)
    
    # Try to load a robust TrueType font
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
    
    # Center text horizontally, and vertically center in the extra space
    text_x = (1200 - text_width) // 2
    text_y = 1200 + ((150 - text_height_calculated) // 2)
    
    try:
        draw.text((text_x, text_y), text, fill="black", font=font)
    except Exception:
        return {"error": "Failed to add text to image"}
    
    # Save the final image to a bytes buffer
    try:
        img_io = io.BytesIO()
        final_image.save(img_io, format="PNG")
        img_io.seek(0)
    except Exception:
        return {"error": "Failed to save final image"}
    
    return StreamingResponse(img_io, media_type="image/png")

@app.get("/get-doctor-id/{session_token}")
def get_doctor_id(session_token: str, db: Session = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    return {"doctor_id": session.doctor_id}

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
            system_message_content = (
                "You are a virtual assistant for Dr. Sarfraz, a specialist in Neurology. "
                "He did his Masters from America. He charges Rs.5000 per inspection."
            )
        elif user_id == 3:
            system_message_content = (
                "You are a virtual assistant for Chand, a hairstylist. "
                "He charges Rs.500 for a haircut and 1000 for a full body massage."
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

        # OpenAI API call
        try:
            chat_completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[system_message, user_message]
            )
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to fetch response from OpenAI")

        # Extract response
        bot_reply = chat_completion.choices[0].message.content

        # Log API usage
        today = date.today()
        api_usage_entry = db.query(APIUsageModel).filter_by(doctor_id=user_id, date=today).first()

        if api_usage_entry:
            api_usage_entry.request_count += 1  # Increment count
        else:
            api_usage_entry = APIUsageModel(doctor_id=user_id, request_type="chatbot", request_count=1, date=today)
            db.add(api_usage_entry)

        db.commit()  # Save changes

        return {"reply": bot_reply}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
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
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
