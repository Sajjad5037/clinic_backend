import os
from fastapi import FastAPI

app = FastAPI()

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
