from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Allow frontend to access the backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with your frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Python Backend Connected!"}

@app.get("/queue/{appointment_id}")
def get_queue_status(appointment_id: int):
    queue_data = {
        1: {"position": 3, "estimated_wait_time": "15 min"},
        2: {"position": 1, "estimated_wait_time": "5 min"},
    }
    return queue_data.get(appointment_id, {"error": "Appointment not found"})
