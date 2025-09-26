# Use an official Python image as the base image
FROM python:3.9-slim

# Set environment variables to avoid the Python startup warning
ENV PYTHONUNBUFFERED 1

# Install required dependencies: gcc, PostgreSQL client, etc.
RUN apt-get update && \
    apt-get install -y gcc libpq-dev postgresql-client && \
    apt-get clean

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app/

# Install the Python dependencies from the requirements.txt file
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port your app runs on (adjust as needed)
EXPOSE 8000

# Command to run the application (adjust the command to your app)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${PORT}"]




