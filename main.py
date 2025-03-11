
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String,func,ForeignKey,Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import uvicorn
from passlib.context import CryptContext
from typing import List,Dict,Set
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect,Request,Body,Response
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



secret_key = os.getenv("SESSION_SECRET_KEY", "fallback-secret-for-dev")



app = FastAPI()
session_states = {}
clients=[]
Base = declarative_base()
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

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

    def get_session(self, session_token=None):
        if session_token is None:
            # If no session_token is provided, generate a new one
            session_token = str(uuid.uuid4())
            self.sessions[session_token] = {
                "patients": [],
                "current_patient": None,
                "inspection_times": [],
                "start_time": None,
                "average_inspection_time": 60,
                "public_token": str(uuid.uuid4())
            }
        else:
            # If session_token is provided, check if it exists, otherwise create it
            if session_token not in self.sessions:
                self.sessions[session_token] = {
                    "patients": [],
                    "current_patient": None,
                    "inspection_times": [],
                    "start_time": None,
                    "average_inspection_time": 60,
                    "public_token": str(uuid.uuid4())
                }
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
        if session_token in self.sessions:
            self.sessions[session_token] = {
                "patients": [],
                "current_patient": None,
                "inspection_times": [],
                "start_time": None,
                "average_inspection_time": 60,
                "public_token": str(uuid.uuid4())
            }
# Global state instance
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

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    name = Column(String)
    specialization = Column(String)

class SessionModel(Base):#used for creating database tables and performing CURD (create,update,read and delete) operations
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_token = Column(String, unique=True, index=True)
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

@app.websocket("/ws/{session_token}")
async def websocket_endpoint(websocket: WebSocket, session_token: str):
    # Authenticate and verify session token
    session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
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

            if message["type"] == "add_patient":
                session_token_current = message.get("session_token")                
                patient_name = message.get("patient")
                
                state.add_patient(session_token_current, patient_name)
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

            elif message["type"] == "reset_all":
                print("Received reset_all message:", message)
                session_token_current = message.get("session_token") 
                state.reset_all()
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
                await websocket.close()
                print("Connection closing")
                
                # Remove WebSocket from active connections safely
                await manager.disconnect(websocket, session_token_current)
                await public_manager.disconnect(websocket, session_token_current)

                update = {
                    "type": "connection_closed",
                    "data": {
                        "message": "WebSocket connection has been closed."
                    }
                }
                await manager.broadcast_to_session(session_token_current, update)
                await public_manager.broadcast_to_session(session_token_current,update)

                # Remove state when session ends
                if session_token in session_states:
                    del session_states[session_token]  

            elif message["type"] == "mark_done":
                state.mark_as_done()
                session_token_current = message.get("session_token") 
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

    except WebSocketDisconnect as e:
        print(f"Client disconnected: Code {e.code}, Reason: {str(e)}")
        await manager.disconnect(websocket, session_token)

        # Remove session state after disconnect
        if session_token in session_states:
            del session_states[session_token]

    except Exception as e:
        error_message = f"Unexpected error: {str(e)}\n{traceback.format_exc()}"
        print(error_message)

@app.websocket("/ws/public/{token}")
async def public_websocket_endpoint(websocket: WebSocket, token: str):
    try:
        # Debug: Print the received token from the URL
        print(f"WebSocket connection attempt with token: {token}")
        
        # Retrieve session data using the provided token
        session_data = state.get_session(token)  # Retrieve session data for the token
        
        # Debug: Print the session data for the provided token
        if session_data:
            print(f"Session data for token {token}: {session_data}")
        else:
            print(f"No session data found for token {token}")
        
        # Check if the token matches the public token
        if token != session_data.get("public_token", None):
            print(f"Token mismatch: {token} != {session_data.get('public_token')}")
            await websocket.close(code=1008)  # Policy violation: close connection
            return

        # Add WebSocket connection to the public manager
        await public_manager.connect(websocket, token)
        print(f"WebSocket connected for token: {token}")

        # Send initial state to the client
        initial_state = {
            "type": "update_state",
            "data": state.get_public_state(token)  # Get the public state using session token
        }
        print(f"Sending initial state to client: {initial_state}")
        await websocket.send_text(json.dumps(initial_state))

        # Keep listening for messages
        while True:
            try:
                # Listen for incoming messages from the client
                message = await websocket.receive_text()
                print(f"Received from client: {message}")  # Debugging: Print received message
                
                # Add logic to handle different message types (optional)
                # Example: if message["type"] == "some_action": process_message(message)

            except WebSocketDisconnect as e:
                # Log the disconnection event with the reason and code
                print(f"Client disconnected: Code {e.code}, Reason: {e.reason}")
                break  # Exit loop when disconnected

            except Exception as e:
                # Log any unexpected errors during message reception
                print(f"Error receiving message: {e}")

    except Exception as e:
        # Log any errors that occur during WebSocket connection
        print(f"Unexpected WebSocket error: {e}")
    finally:
        # Remove the client from the public manager upon disconnection or error
        await public_manager.disconnect(websocket, token)
        print(f"Client removed from public manager for token: {token}")

# HTTP endpoint to get the public token (for the doctor to share)
@app.get("/dashboard/public-token")
def get_public_token(session_token: str = None):
    if not session_token:
        session_token = str(uuid.uuid4())

    print(f"Session Token Requested: {session_token}") 
    session_data = state.get_session(session_token)

    if not session_data:
        print("Session Not Found")
        return {"error": "Session not found", "sessionToken": session_token}

    print(f"Stored Public Token: {session_data.get('public_token')}")
    return {"publicToken": session_data.get("public_token")}

@app.get("/")
def read_root():
    return {"message": "Python Backend Connected!"}
@app.post("/login")
async def login(
    login_request: LoginRequest, 
    response: Response, 
    db: Session = Depends(get_db)
):
    print(f"Received login request for username: {login_request.username}")

    # Fetch doctor from the database
    doctor = db.query(Doctor).filter(Doctor.username == login_request.username).first()
    
    if not doctor:
        print("No doctor found with that username.")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not pwd_context.verify(login_request.password, doctor.password):
        print("Invalid password provided.")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    print("Password verified successfully.")

    # Check if an active session already exists for the doctor
    existing_session = db.query(SessionModel).filter(SessionModel.doctor_id == doctor.id).first()

    if existing_session:
        session_token = existing_session.session_token
        print(f"Existing session found for doctor {doctor.id}: {session_token}")
    else:
        # Generate a new session token
        session_token = str(uuid4())
        print(f"Generated new session token: {session_token}")

        # Store session in the database
        new_session = SessionModel(session_token=session_token, doctor_id=doctor.id)
        db.add(new_session)
        db.commit()
        print("New session stored in the database.")

    # Set session cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=False,  # Change to True if using HTTPS
        samesite="Lax",
        max_age=3600
    )
    print("Session cookie set successfully.")

    return JSONResponse(
        content={
            "message": "Login successful",
            "id": doctor.id,
            "name": doctor.name,
            "session_token": session_token  # Send session token for frontend use
        },
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
