
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String,func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import uvicorn
from passlib.context import CryptContext
from typing import List
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect,Request,Body
import json
import time
import uuid  # For generating unique tokens
import os
from starlette.middleware.sessions import SessionMiddleware
from datetime import datetime, timedelta,timezone

import logging




secret_key = os.getenv("SESSION_SECRET_KEY", "fallback-secret-for-dev")


app = FastAPI()
clients=[]
Base = declarative_base()
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# WebSocket connection manager to handle multiple clients... it is responsible for connecting, disconnecting and broadcasting messages
class ConnectionManager:
    def __init__(self):
        # List to store all active WebSocket connections
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        # Accept the WebSocket connection and add it to the list
        await websocket.accept() #this allows communication between server and client
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        # Remove a WebSocket from the list when it disconnects
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        logging.info(f"Broadcasting message: {message}")
        if not self.active_connections:
            logging.warning("No active connections to broadcast to.")
            return

        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                logging.error(f"Error sending message to a client: {e}")


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

    def reset_averageInspectionTime(self):
        """Reset the average inspection time to 60 seconds."""
        self.average_inspection_time = 60       
       
    def get_average_time(self):
        # Calculate average inspection time, default to 60s if none recorded
        return round(sum(self.inspection_times) / len(self.inspection_times)) if self.inspection_times else 60
    
    def get_public_state(self):
        # Return a read-only version of the state for public access
        return {
            "patients": self.patients,
            "currentPatient": self.current_patient,
            "averageInspectionTime": self.get_average_time()
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

class DoctorCreate(BaseModel):
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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Connect the client
    await manager.connect(websocket)
    try:
        # Send initial state to the new client
        await websocket.send_text(json.dumps({
            "type": "update_state",
            "data": {
                "patients": state.patients,
                "currentPatient": state.current_patient,
                "averageInspectionTime": state.get_average_time()
            }
        }))
        while True:
            # Listen for messages from the client
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Handle different message types
            if message["type"] == "add_patient":
                state.add_patient(message["patient"])
                await manager.broadcast({
                    "type": "update_state",
                    "data": {
                        "patients": state.patients,
                        "currentPatient": state.current_patient,
                        "averageInspectionTime": state.get_average_time()
                    }
                })
            elif message["type"] == "mark_done":
                state.mark_as_done()
                await manager.broadcast({
                    "type": "update_state",
                    "data": {
                        "patients": state.patients,
                        "currentPatient": state.current_patient,
                        "averageInspectionTime": state.get_average_time()
                    }
                })
            elif message["type"] == "reset_averageInspectionTime":
                state.reset_averageInspectionTime()
                await manager.broadcast({
                    "type": "reset_averageInspectionTime",
                    "data": {                        
                        "averageInspectionTime": state.average_inspection_time
                    }
                })
            
            
    except WebSocketDisconnect:
        # Handle client disconnection
        manager.disconnect(websocket)
async def broadcast_state():
    state_data = {
        "type": "update_state",
        "data": state.get_public_state()
    }
    await manager.broadcast(state_data)  # To authenticated clients
    await public_manager.broadcast(state_data)  # To public clients

# New public WebSocket endpoint for patients
@app.websocket("/ws/public/{token}")
async def public_websocket_endpoint(websocket: WebSocket, token: str):
    # Verify the token matches the dashboard's public token
    if token != state.public_token:
        await websocket.close(code=1008)  # Policy violation
        return
    
    # Connect the public client
    await public_manager.connect(websocket)
    try:
        # Send initial state to the public client
        await websocket.send_text(json.dumps({
            "type": "update_state",
            "data": state.get_public_state()
        }))
        # Keep the connection open for updates (no actions allowed)
        while True:
            await websocket.receive_text()  # Keep alive, but ignore messages
    except WebSocketDisconnect:
        public_manager.disconnect(websocket)

# HTTP endpoint to get the public token (for the doctor to share)
@app.get("/dashboard/public-token")
def get_public_token():
    return {"publicToken": state.public_token}
@app.get("/")
def read_root():
    return {"message": "Python Backend Connected!"}

    
@app.post("/login")
async def login(
    login_request: LoginRequest,
    db: Session = Depends(get_db)
):
    doctor = db.query(Doctor).filter(Doctor.username == login_request.username).first()
    if doctor and pwd_context.verify(login_request.password, doctor.password):
        return JSONResponse(
            content={
                "id": doctor.id,
                "name": doctor.name,
                "specialization": doctor.specialization
            },
            status_code=200
        )
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.post("/logout")
async def logout(req: Request, logout_request: LogoutRequest = Body(...)):
    # Optionally, if requested, reset the averageInspectionTime (or ignore if not needed)
    if logout_request.resetAverageInspectionTime:
        req.session["averageInspectionTime"] = 60

    # Clear the session
    req.session.clear()

    # Create a response and explicitly delete the session cookie.
    response = JSONResponse(
        content={"message": "Logged out and session reset."},
        status_code=200
    )
    # Delete the session cookie (the default cookie name is "session")
    response.delete_cookie(key="session", path="/")
     # Alternatively, set the cookie to expire in the past.
    response.set_cookie(
    key="session",
    value="",
    expires=(datetime.now(timezone.utc) - timedelta(days=1)).strftime("%a, %d-%b-%Y %H:%M:%S GMT"),
    path="/"
    )
    return response
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
