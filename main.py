from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import uvicorn
from passlib.context import CryptContext

app = FastAPI()

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


# Database setup
DATABASE_URL = "sqlite:///./clinic.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Password hashing setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Define Doctor model
class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    name = Column(String)
    specialization = Column(String)

# Create database tables
Base.metadata.create_all(bind=engine)

# Ensure Admin exists
def create_admin(db: Session):
    """Ensure the admin user exists, create if it doesn't."""
    admin_username = "sajjad"

    # Check if the admin already exists
    admin = db.query(Doctor).filter(Doctor.username == admin_username).first()
    
    if not admin:  # If no admin exists, create one
        admin = Doctor(
            username=admin_username,
            password=pwd_context.hash("admin123"),  # Hashed password
            name="Sajjad Ali Noor",
            specialization="Administrator"
        )
        db.add(admin)
        db.commit()
        print("Admin account created successfully.")
    else:
        print("Admin already exists.")

db = SessionLocal()
create_admin(db)
db.close()

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

@app.get("/")
def read_root():
    return {"message": "Python Backend Connected!"}
"""
@app.post("/add_doctor")

    def add_doctor(doctor: DoctorCreate, db: Session = Depends(get_db)):
    existing_doctor = db.query(Doctor).filter(Doctor.username == doctor.username).first()
    if existing_doctor:
        raise HTTPException(status_code=400, detail="Username already exists")

    new_doctor = Doctor(
        id=doctor.id,
        username=doctor.username,
        password=pwd_context.hash(doctor.password),  # Hash password
        name=doctor.name,
        specialization=doctor.specialization
    )
    db.add(new_doctor)
    db.commit()
    return {"message": "Doctor added successfully"}
"""
@app.post("/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.username == request.username).first()

    if doctor and pwd_context.verify(request.password, doctor.password):
        return JSONResponse(content={
            "id": doctor.id,
            "name": doctor.name,
            "specialization": doctor.specialization
        }, status_code=200)
    
    raise HTTPException(status_code=401, detail="Invalid credentials")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
