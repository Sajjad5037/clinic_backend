# Use an official lightweight Python image
FROM python:3.9-slim

# Avoid Python buffering issues
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && \
    apt-get install -y gcc libpq-dev postgresql-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY . /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port the app will run on
EXPOSE 8000

# Run the FastAPI app with Uvicorn, using Railway's dynamic port
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${PORT}"]
