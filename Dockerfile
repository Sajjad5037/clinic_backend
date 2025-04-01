FROM python:3.9-slim

# Install Tesseract OCR and any other system-level dependencies
RUN apt-get update && apt-get install -y tesseract-ocr

# Set the working directory inside the container
WORKDIR /app

# Copy your project files into the container
COPY . /app

# Install Python dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Run your application using Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
