# Use official Python runtime as a parent image
FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the web app code into the container
COPY bot_acc_filler_app/ /app/bot_acc_filler_app/
COPY config.json /app/

# Set working directory to the web folder for FastAPI execution
WORKDIR /app/bot_acc_filler_app/web

# Make port 8000 available to the world outside this container
EXPOSE 8000

# Define environment variables (BOT Tokens should be passed at runtime)
ENV PYTHONUNBUFFERED=1

# Run uvicorn when the container launches
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
