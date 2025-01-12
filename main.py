import asyncio
import uvicorn
from fastapi import FastAPI, HTTPException, Depends, Request, Header
from threading import Thread
from bot import run_bot, stop_bot
from db import get_log_collection, close_mongodb_connection, connect_to_mongodb
import logging
import time
from typing import List
from pymongo import DESCENDING
from pymongo.errors import OperationFailure
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_401_UNAUTHORIZED
from starlette.responses import JSONResponse, FileResponse
import secrets
import os

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

app = FastAPI()

# Global variables to track the bot application and thread
bot_app = None
bot_thread = None

# HTTP Basic Authentication
security = HTTPBasic()

def start_bot_in_thread():
    global bot_app
    logger.info("Starting bot in a new thread...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run_and_get_app():
        global bot_app
        bot_app = await run_bot()

    loop.create_task(run_and_get_app())
    loop.run_forever()

# Authentication function using HTTP Basic Auth
async def authenticate_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

    if not credentials or not credentials.username or not credentials.password:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# Apply authentication to all routes
@app.middleware("http")
async def apply_authentication(request: Request, call_next):
    # Exclude authentication for the root path ("/") and the /download path
    if request.url.path == "/" or request.url.path.startswith("/download/"):
        return await call_next(request)

    # Check for authentication for all other paths
    try:
        # Get the Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Authorization header missing",
                headers={"WWW-Authenticate": "Basic"},
            )

        # Check authentication using the dependency
        credentials = await security(request)
        username = await authenticate_admin(credentials)
        request.state.username = username
    except HTTPException as e:
        # If authentication fails, return the error response
        return JSONResponse(
            status_code=e.status_code,
            content={"detail": e.detail},
            headers=e.headers,
        )

    # If authentication succeeds, proceed with the request
    response = await call_next(request)
    return response

@app.get("/")
def read_root():
    logger.info("Received request at /")
    return {"message": "Welcome to the API"}

@app.get("/botstart")
async def start_bot_endpoint(username: str = Depends(authenticate_admin)):
    """Starts the Telegram bot if it's not already running."""
    global bot_thread
    logger.info(f"Received request at /botstart from {username}")

    if bot_thread is None or not bot_thread.is_alive():
        bot_thread = Thread(target=start_bot_in_thread)
        bot_thread.start()
        logger.info("Bot start initiated.")

        # Wait for a few seconds to ensure the bot and MongoDB connection are ready
        time.sleep(5)

        return {"message": "Bot started successfully"}
    else:
        logger.info("Bot is already running.")
        return {"message": "Bot is already running"}

@app.get("/botstop")
async def stop_bot_endpoint(username: str = Depends(authenticate_admin)):
    """Stops the Telegram bot if it's running."""
    global bot_app
    global bot_thread
    logger.info(f"Received request at /botstop from {username}")

    if bot_app and bot_thread and bot_thread.is_alive():
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(stop_bot(bot_app))

            # Close MongoDB connection
            close_mongodb_connection()

            bot_thread.join()
            bot_app = None
            bot_thread = None
            logger.info("Bot stop initiated.")
            return {"message": "Bot stopped successfully"}
        except Exception as e:
            logger.error(f"Error stopping bot: {e}")
            raise HTTPException(status_code=500, detail=f"Error stopping bot: {e}")
    else:
        logger.info("Bot is not running.")
        return {"message": "Bot is not running"}

# API endpoint to get user activity logs (admin only)
@app.get("/logs", response_model=List[dict])
async def get_logs(username: str = Depends(authenticate_admin)):
    """Retrieves user activity logs from MongoDB (admin only)."""
    logger.info(f"Admin {username} requested logs at /logs")

    log_collection = get_log_collection()
    if log_collection is None:
        logger.error("MongoDB connection not established. Cannot retrieve logs.")
        raise HTTPException(status_code=500, detail="Database connection not available")

    try:
        logs = list(log_collection.find().sort("timestamp", DESCENDING))  # Sort by timestamp in descending order
        # Convert ObjectId to string for JSON serialization
        for log in logs:
            log["_id"] = str(log["_id"])
        return logs
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve logs")

@app.get("/download/{user_id}/{file_name}")
async def download_file(user_id: str, file_name: str):
    """Serve files from the user's download directory."""
    file_path = os.path.join(os.getenv("DOWNLOAD_DIR"), user_id, file_name)
    if not os.path.isfile(file_path):
        logger.error(f"File not found: {file_path}")
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path)

if __name__ == "__main__":
    logger.info("Starting FastAPI application...")
    uvicorn.run(app, host="0.0.0.0", port=8000)