# Use an official Python runtime as the base image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Install ffmpeg and other dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libgl1-mesa-glx libglib2.0-0 libmagic1

# Copy the requirements file into the container
COPY requirements.txt .

# Install the required Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . .

# Set environment variables (if any are needed)
# ENV TELEGRAM_BOT_TOKEN=your_bot_token
# ENV API_KEY=your_api_key
# ENV USER_ID=your_user_id
# ENV DOWNLOAD_DIR=/app/downloads
# ENV MONGO_URI=your_mongodb_uri
# ENV MONGO_DB_NAME=your_mongodb_database_name
# ENV MONGO_COLLECTION_NAME=your_user_collection_name
# ENV MONGO_LOG_COLLECTION_NAME=your_log_collection_name
# ENV MONGO_FILES_COLLECTION_NAME=files
# ENV ADMIN_USERNAME=your_admin_username
# ENV ADMIN_PASSWORD=your_admin_password
# ENV FILE_HOST_BASE_URL=https://your-server.com
# ENV IMAGES_DIR=/app/images

# Create the download and images directories with appropriate permissions
RUN mkdir -p /app/downloads && chmod -R 777 /app/downloads
RUN mkdir -p /app/images && chmod -R 777 /app/images

# Expose the port that FastAPI runs on (default is 8000)
EXPOSE 8080

# Command to run the application using Uvicorn
CMD ["python", "main.py"]