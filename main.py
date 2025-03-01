from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Allow Firebase frontend to access the API
origins = [
    "https://clinic-management-system-27d11.web.app",
    "http://localhost:3000",  # For local testing
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all headers
)

doctors = [
    {"id": 1, "username": "drsmith", "password": "securePass123", "name": "Dr. Smith", "specialization": "Cardiology"},
    {"id": 2, "username": "drjones", "password": "pass456", "name": "Dr. Jones", "specialization": "Neurology"},
]

@app.get("/")
def read_root():
    return {"message": "Python Backend Connected!"}
    
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()  # Get JSON data from request
    username = data.get("username")
    password = data.get("password")

    # Find doctor in the database
    doctor = next((doc for doc in doctors if doc["username"] == username and doc["password"] == password), None)

    if doctor:
        return jsonify({
            "id": doctor["id"],
            "name": doctor["name"],
            "specialization": doctor["specialization"]
        }), 200
    else:
        return jsonify({"error": "Invalid credentials"}), 401



@app.get("/queue/{appointment_id}")
def get_queue_status(appointment_id: int):
    queue_data = {
        1: {"position": 3, "estimated_wait_time": "15 min"},
        2: {"position": 1, "estimated_wait_time": "5 min"},
    }
    return queue_data.get(appointment_id, {"error": "Appointment not found"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
