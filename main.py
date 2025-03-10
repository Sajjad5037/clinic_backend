
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
        await websocket.accept()  # this allows communication between server and client
        self.active_connections.add(websocket)
        print(f"New client connected with token {session_token}! Total clients: {len(self.active_connections)}")
        # Optionally, store the session_token along with the connection if needed
        self.client_tokens[websocket] = session_token  # assuming you have this mapping

    def disconnect(self, websocket: WebSocket, session_token: str):
        # Remove the WebSocket from the session-specific connection set
        if session_token in self.active_connections:
            self.active_connections[session_token].discard(websocket)
            # Clean up the session if no connections remain
            if not self.active_connections[session_token]:
                del self.active_connections[session_token]
        
        # Calculate total active connections across all sessions for logging
        total_clients = sum(len(conns) for conns in self.active_connections.values())
        print(f"Client disconnected from session {session_token}! Total clients: {total_clients}")
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
            print(f"Connection is: {connection}")
            print(f"Token is: {token}")            
            print(f"Session token is: {session_token}\n\n")
            

            if token == session_token:
                try:
                    print(f"sending message where message: {message} \nconnection is {connection}\n token is {token}\n and session token is {session_token}\n")
                    await connection.send_text(json.dumps(message))
                except Exception as e:
                    print(f"Error sending message to session {session_token}: {e}")
class LogoutRequest(BaseModel):
    resetAverageInspectionTime: bool = True


# Instantiate the manager globally
manager = ConnectionManager()
# Server-side state for real-time features
class DashboardState:
    def __init__(self):
        self.patients = []  # List of patient names (simplified for this example)
        self.current_patient = None  # Patient being inspected
        self.inspection_times = []  # List of inspection durations in seconds
        self.start_time = None  # Start time of current inspection
        # Generate a unique public token for this dashboard
        self.public_token = str(uuid.uuid4())
        self.average_inspection_time = 60

    def add_patient(self, patient_name: str):
        self.patients.append(patient_name)
        if not self.current_patient:
            self.current_patient = patient_name
            self.start_time = time.time() #to store the starting time of the treatment which is used in mark_as_done to calculate the duration of the treatment

    def mark_as_done(self):
        if not self.current_patient:
            return
        # Calculate duration and store it
        duration = time.time() - self.start_time
        self.inspection_times.append(duration)
        # Shift to next patient
        self.patients.pop(0)
        self.current_patient = self.patients[0] if self.patients else None
        self.start_time = time.time() if self.current_patient else None

       
    def get_average_time(self):
        # Calculate average inspection time, default to 60s if none recorded
        return round(sum(self.inspection_times) / len(self.inspection_times)) if self.inspection_times else 300
    
    def get_public_state(self):
        # Return a read-only version of the state for public access
        return {
            "patients": self.patients,
            "currentPatient": self.current_patient,
            "averageInspectionTime": self.get_average_time()
        }
    def reset_all(self):
        """Reset all patient-related data to the initial state."""
        self.patients = []
        self.current_patient = None
        self.inspection_times = []  # Assuming this is used for calculating average time


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

    # Add WebSocket connection to the manager based on session token
    await manager.connect(websocket, session_token)

    try:
        # Send initial state to the new client
        initial_state = {
            "type": "update_state",
            "data": {
                "patients": state.patients,
                "currentPatient": state.current_patient,
                "averageInspectionTime": state.get_average_time(),
                "session_token": session_token
            }
        }
        await websocket.send_text(json.dumps(initial_state))

        # Broadcast to public clients (if applicable)
        if not session.is_authenticated:
            await public_manager.broadcast_to_session(session_token,initial_state)

        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            if message["type"] == "add_patient":
                state.add_patient(message["patient"])
                update = {
                    "type": "update_state",
                    "data": {
                        "patients": state.patients,
                        "currentPatient": state.current_patient,
                        "averageInspectionTime": state.get_average_time(),
                        "session_token":session_token
                    }
                }
                await manager.broadcast_to_session(session_token, update)
                await public_manager.broadcast_to_session(session_token,update)  # Update public clients

            elif message["type"] == "reset_all":
                print("Received reset_all message:", message)
                state.reset_all()
                update = {
                    "type": "update_state",
                    "data": {
                        "patients": state.patients,
                        "currentPatient": state.current_patient,
                        "averageInspectionTime": state.get_average_time(),
                        "session_token":session_token
                    }
                }
                await manager.broadcast_to_session(session_token, update)
                await public_manager.broadcast_to_session(session_token,update)  # Update public clients

            elif message["type"] == "close_connection":
                await websocket.close()
                print("Connection closing")
                
                # Remove WebSocket from active connections safely
                await manager.disconnect(websocket,session_token)
                await public_manager.disconnect(websocket,session_token)

                update = {
                    "type": "connection_closed",
                    "data": {
                        "message": "WebSocket connection has been closed."
                    }
                }
                await manager.broadcast_to_session(session_token, update)
                await public_manager.broadcast(update)

            elif message["type"] == "mark_done":
                state.mark_as_done()
                update = {
                    "type": "update_state",
                    "data": {
                        "patients": state.patients,
                        "currentPatient": state.current_patient,
                        "averageInspectionTime": state.get_average_time(),
                        "session_token":session_token
                    }
                }
                await manager.broadcast_to_session(session_token, update)
                await public_manager.broadcast_to_session(session_token,update)  # Update public clients

    except WebSocketDisconnect as e:
        # Log disconnection details
        print(f"Client disconnected: Code {e.code}, Reason: {str(e)}")
        await manager.disconnect(websocket,session_token)
    except Exception as e:
        # Log unexpected errors
        error_message = f"Unexpected error: {str(e)}\n{traceback.format_exc()}"
        print(error_message)        

# New public WebSocket endpoint for patients
@app.websocket("/ws/public/{token}")
async def public_websocket_endpoint(websocket: WebSocket, token: str):
    if token != state.public_token:
        await websocket.close(code=1008)  # Policy violation
        return

    await public_manager.connect(websocket,token)
    try:
        # Send initial state to the client
        await websocket.send_text(json.dumps({
            "type": "update_state",
            "data": state.get_public_state()
        }))

        # Keep listening for messages
        while True:
            try:
                message = await websocket.receive_text()  # Listen for messages
                print(f"Received from client: {message}")  # Debugging
            except WebSocketDisconnect as e:
                print(f"Client disconnected: Code {e.code}, Reason: {e.reason}")
                break  # Exit loop when disconnected
    except Exception as e:
        print(f"Unexpected WebSocket error: {e}")
    finally:
        public_manager.disconnect(websocket,token)
        print("Client removed from public manager.")

# HTTP endpoint to get the public token (for the doctor to share)
@app.get("/dashboard/public-token")
def get_public_token():
    return {"publicToken": state.public_token}
@app.get("/")
def read_root():
    return {"message": "Python Backend Connected!"}

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
# @app.post("/login")
# async def login(
#     login_request: LoginRequest,
#     db: Session = Depends(get_db)
# ):
#     doctor = db.query(Doctor).filter(Doctor.username == login_request.username).first()
#     if doctor and pwd_context.verify(login_request.password, doctor.password):
#         return JSONResponse(
#             content={
#                 "id": doctor.id,
#                 "name": doctor.name,
#                 "specialization": doctor.specialization
#             },
#             status_code=200
#         )
#     raise HTTPException(status_code=401, detail="Invalid credentials")
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
