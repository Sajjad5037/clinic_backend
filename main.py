from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import JSONResponse

app = FastAPI()

# Allow Firebase frontend and local development
origins = [
    "https://clinic-management-system-27d11.web.app",
    "http://localhost:3000",  # For local testing
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Allowed origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)

# Hypothetical database of doctors
doctors = [
    {"id": 1, "username": "drsmith", "password": "securePass123", "name": "Dr. Smith", "specialization": "Cardiology"},
    {"id": 2, "username": "drjones", "password": "pass456", "name": "Dr. Jones", "specialization": "Neurology"},
]

# Pydantic model for login requests
class LoginRequest(BaseModel):
    username: str
    password: str

@app.get("/")
def read_root():
    return {"message": "Python Backend Connected!"}


@app.post("/login")
async def login(request: LoginRequest):
    username = request.username
    password = request.password

    # Find doctor in the database
    doctor = next((doc for doc in doctors if doc["username"] == username and doc["password"] == password), None)

    if doctor:
        return JSONResponse(content={
            "id": doctor["id"],
            "name": doctor["name"],
            "specialization": doctor["specialization"]
        }, status_code=200)
    
    raise HTTPException(status_code=401, detail="Invalid credentials")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
