import logging
import os
import re
from dotenv import load_dotenv
from telegram import Update, ForceReply, constants, File
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from pymongo.errors import OperationFailure
import asyncio
from db import connect_to_mongodb, get_users_collection, get_log_collection, close_mongodb_connection
import aiohttp
import aiofiles
from aiofiles.threadpool.binary import AsyncFileIO
from tqdm.asyncio import tqdm
from pathlib import Path

# Load environment variables
load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_KEY = os.getenv("API_KEY")
USER_ID = os.getenv("USER_ID")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR")

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# /start command handler
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Starts the bot for the user and prompts for registration."""
    user_id = update.effective_user.id
    logger.info(f"Received /start command from user: {user_id}")

    users_collection = get_users_collection()
    # Check if MongoDB is connected
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot start bot for user.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    # Update user status to started
    try:
        users_collection.update_one({"user_id": user_id}, {"$set": {"started": True}}, upsert=True)
        logger.info(f"User {user_id} started the bot.")
        await update.message.reply_html(
            rf"Hi {update.effective_user.mention_html()}! Welcome to the bot. "
            rf"Please use the /register command to sign up."
        )
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to start bot for user.")

# /register command handler
async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registers the user and saves info to MongoDB."""
    logger.info(f"Received /register command from user: {update.effective_user.id}")
    user = update.effective_user

    users_collection = get_users_collection()
    # Check if MongoDB is connected
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot register user.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    # Check if user is already registered (deleted users can re-register)
    try:
        existing_user = users_collection.find_one({"user_id": user.id})
        if existing_user and existing_user.get("deleted", False):
            # User is soft-deleted, so update deleted status and welcome back
            users_collection.update_one({"user_id": user.id}, {"$set": {"deleted": False}})
            logger.info(f"User {update.effective_user.id} re-registered (previously soft-deleted).")
            await update.message.reply_text(
                f"Welcome back, {existing_user['first_name']}! You have been re-registered."
            )
            return
        elif existing_user:
            await update.message.reply_text("You are already registered!")
            logger.info(f"User {update.effective_user.id} is already registered.")
            return
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to check for existing user.")
        return

    # Save user data to MongoDB
    user_data = {
        "user_id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "deleted": False,  # Add a "deleted" field for soft deletion
        "started": True  # User is started
    }

    try:
        users_collection.insert_one(user_data)
        logger.info(f"Registered user: {update.effective_user.id} in MongoDB.")

        # Welcome message with user info
        await update.message.reply_text(
            f"Welcome, {user.first_name}! You have been successfully registered.\n"
            f"Your details:\n"
            f"ID: {user.id}\n"
            f"First Name: {user.first_name}\n"
            f"Last Name: {user.last_name}\n"
            f"Username: {user.username}"
        )
        logger.info(f"Sent registration confirmation to user: {update.effective_user.id}")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to register user.")
        return

# /me command handler
async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retrieves and sends the user's information."""
    logger.info(f"Received /me command from user: {update.effective_user.id}")
    user = update.effective_user

    users_collection = get_users_collection()
    # Check if MongoDB is connected
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot retrieve user info.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    try:
        user_data = users_collection.find_one({"user_id": user.id})
        if user_data:
            if user_data.get("deleted", False):  # Check if the user is soft-deleted
                await update.message.reply_text("You are not registered. Use /register to sign up.")
                logger.info(f"User {update.effective_user.id} is soft-deleted.")
            else:
                # Send user information
                await update.message.reply_text(
                    f"Your details:\n"
                    f"ID: {user_data['user_id']}\n"
                    f"First Name: {user_data['first_name']}\n"
                    f"Last Name: {user_data['last_name']}\n"
                    f"Username: {user_data['username']}"
                )
                logger.info(f"Sent user info to: {update.effective_user.id}")
        else:
            await update.message.reply_text("You are not registered yet. Use /register to sign up.")
            logger.info(f"User {update.effective_user.id} is not registered.")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to retrieve user information.")
        return

# /unregister command handler
async def unregister_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Soft-deletes the user from the database."""
    logger.info(f"Received /unregister command from user: {update.effective_user.id}")
    user = update.effective_user

    users_collection = get_users_collection()
    # Check if MongoDB is connected
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot unregister user.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    try:
        result = users_collection.update_one({"user_id": user.id}, {"$set": {"deleted": True}})
        if result.modified_count > 0:
            await update.message.reply_text("You have been successfully unregistered.")
            logger.info(f"User {update.effective_user.id} soft-deleted (unregistered).")
        else:
            await update.message.reply_text("You are not registered yet.")
            logger.info(f"User {update.effective_user.id} is not registered (cannot unregister).")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to unregister user.")
        return

# /stop command handler
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stops the bot for the user."""
    user_id = update.effective_user.id
    logger.info(f"Received /stop command from user: {user_id}")

    users_collection = get_users_collection()
    # Check if MongoDB is connected
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot stop bot for user.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    # Update user status to stopped
    try:
        users_collection.update_one({"user_id": user_id}, {"$set": {"started": False}})
        logger.info(f"User {user_id} stopped the bot.")
        await update.message.reply_text("Bot stopped. Use /start to start the bot again.")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to stop bot for user.")

# Middleware to check user registration status
async def check_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if the user is registered and not soft-deleted."""
    user = update.effective_user

    users_collection = get_users_collection()
    # Bypass registration check for /start and /register commands
    if update.message and update.message.text and (update.message.text.startswith('/start') or update.message.text.startswith('/register')):
        return True

    # Check if MongoDB is connected
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot check registration status.")
        await update.message.reply_text("Error: Database connection not available.")
        return False

    try:
        user_data = users_collection.find_one({"user_id": user.id, "deleted": False})
        if user_data:
            return True  # User is registered and not soft-deleted
        else:
            await update.message.reply_text("You are not registered. Use /register to sign up.")
            logger.info(f"User {update.effective_user.id} is not registered or is soft-deleted.")
            return False
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to check registration status.")
        return False

async def check_user_started(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if the user has started the bot."""
    user_id = update.effective_user.id

    users_collection = get_users_collection()
    # Bypass the check for /start, /register, and /stop commands
    if update.message and update.message.text and (update.message.text.startswith('/start') or update.message.text.startswith('/register') or update.message.text.startswith('/stop')):
        return True

    # Check if MongoDB is connected
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot check user status.")
        await update.message.reply_text("Error: Database connection not available.")
        return False

    try:
        user_data = users_collection.find_one({"user_id": user_id})
        if user_data and user_data.get("started", False):
            return True  # User has started the bot
        else:
            await update.message.reply_text("Please use /start to start the bot.")
            logger.info(f"User {user_id} has not started the bot.")
            return False
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to check user status.")
        return False

# Log user activity
async def log_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs user activity to MongoDB."""
    log_collection = get_log_collection()
    if log_collection is None:
        logger.error("MongoDB connection not established. Cannot log user activity.")
        return

    # Check if the user has started the bot before logging activity
    if not await check_user_started(update, context):
        return

    user = update.effective_user
    message = update.effective_message

    log_entry = {
        "user_id": user.id if user else None,
        "username": user.username if user else None,
        "first_name": user.first_name if user else None,
        "last_name": user.last_name if user else None,
        "message_id": message.message_id if message else None,
        "message_text": message.text if message else None,
        "timestamp": message.date if message else None,
    }

    try:
        log_collection.insert_one(log_entry)
        logger.info(f"Logged activity for user: {user.id if user else 'N/A'}")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")

async def download_and_send_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    """Downloads a file from a given URL and sends it to the user."""
    user_id = update.effective_user.id
    
    log_collection = get_log_collection()
    # Log the start of the download process
    log_entry = {
        "user_id": user_id,
        "event": "download_start",
        "url": url,
        "timestamp": update.message.date
    }
    try:
        log_collection.insert_one(log_entry)
        logger.info(f"Starting download for user {user_id} from URL: {url}")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")

    # Call Premium.to API to get the file
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('http://api.premium.to/api/2/getfile.php', params={
                'userid': USER_ID,
                'apikey': API_KEY,
                'link': url
            }) as response:
                if response.status != 200:
                    error_msg = f"Error: Premium.to API returned status code {response.status}"
                    await update.message.reply_text(error_msg)
                    # Log the error
                    log_collection.insert_one({
                        "user_id": user_id,
                        "event": "error",
                        "message": error_msg,
                        "timestamp": update.message.date
                    })
                    return
                try:
                    api_response = await response.json()
                except aiohttp.ContentTypeError as e:
                    logger.error(f"Failed to decode JSON response: {e}")
                    await update.message.reply_text("Error: Invalid response from Premium.to API.")
                    return
                
                if 'download' not in api_response:
                    error_msg = f"Error: Invalid response from Premium.to API. {api_response.get('message', 'Unknown error')}"
                    await update.message.reply_text(error_msg)
                    # Log the error
                    log_collection.insert_one({
                        "user_id": user_id,
                        "event": "error",
                        "message": error_msg,
                        "timestamp": update.message.date
                    })
                    return

                download_url = api_response['download']
    except Exception as e:
        error_msg = f"Error calling Premium.to API: {e}"
        await update.message.reply_text(error_msg)
        # Log the error
        log_collection.insert_one({
            "user_id": user_id,
            "event": "error",
            "message": error_msg,
            "timestamp": update.message.date
        })
        return

    # Inform the user that the download is starting
    await update.message.reply_text("Download starting...")

    # Download the file and send it to the user
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                total_size = int(response.headers.get('Content-Length', 0))

                # Create user directory if it doesn't exist
                user_dir = Path(DOWNLOAD_DIR) / str(user_id)
                user_dir.mkdir(parents=True, exist_ok=True)

                file_name = os.path.basename(url)
                file_path = user_dir / file_name

                async with aiofiles.open(file_path, 'wb') as f:
                    chunk_size = 4096
                    downloaded_size = 0

                    async for chunk in response.content.iter_chunked(chunk_size):
                        if chunk:
                            await f.write(chunk)
                            downloaded_size += len(chunk)
                            # Calculate progress percentage
                            progress = int((downloaded_size / total_size) * 100) if total_size > 0 else 0
                            # Log the progress
                            log_collection.insert_one({
                                "user_id": user_id,
                                "event": "download_progress",
                                "progress": progress,
                                "downloaded": downloaded_size,
                                "total": total_size,
                                "timestamp": update.message.date
                            })
                            # Send progress update to user (optional)
                            await context.bot.edit_message_text(
                                chat_id=update.message.chat_id,
                                message_id=update.message.message_id + 1,  # Assumes this is the next message
                                text=f"Downloading: {progress}%"
                            )

        # Send the downloaded file to the user
        file_doc = await context.bot.send_document(
            chat_id=update.message.chat_id,
            document=open(file_path, 'rb'),
            caption="Here is your file!"
        )

        # Get the file ID from the sent document
        file_id = file_doc.document.file_id

        # Get the file path on Telegram's server
        file_path_on_telegram = await context.bot.get_file(file_id)

        # Construct the URL to the file on Telegram's server
        # Replace `BOT_TOKEN` with your actual bot token
        file_url_on_telegram = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path_on_telegram.file_path}"

        # Log the successful download
        log_collection.insert_one({
            "user_id": user_id,
            "event": "download_complete",
            "file_url": file_url_on_telegram,
            "timestamp": update.message.date
        })

        # Inform the user with the link to the file
        await update.message.reply_text(
            f"Your file has been downloaded and is available here: {file_url_on_telegram}"
        )

    except Exception as e:
        error_msg = f"Error during download or sending file: {e}"
        await update.message.reply_text(error_msg)
        # Log the error
        log_collection.insert_one({
            "user_id": user_id,
            "event": "error",
            "message": error_msg,
            "timestamp": update.message.date
        })

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming messages and processes Rapidgator URLs."""
    message_text = update.message.text
    user_id = update.effective_user.id

    # Log the received message
    log_collection = get_log_collection()
    log_entry = {
        "user_id": user_id,
        "event": "message_received",
        "message": message_text,
        "timestamp": update.message.date
    }
    try:
        log_collection.insert_one(log_entry)
        logger.info(f"User {user_id} sent a message: {message_text}")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")

    # Check if the message is a Rapidgator URL
    if re.match(r"https?://(www\.)?rapidgator\.net", message_text):
        await download_and_send_file(update, context, message_text)
    else:
        await update.message.reply_text("Please send a valid Rapidgator URL.")

# Error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

async def run_bot():
    """Set up and run the Telegram bot."""
    logger.info("Setting up the bot...")
    connect_to_mongodb()  # Connect to MongoDB at bot startup

    bot_app = Application.builder().token(BOT_TOKEN).build()

    # Add command handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("register", register_command))
    bot_app.add_handler(CommandHandler("me", me_command))
    bot_app.add_handler(CommandHandler("unregister", unregister_command))
    bot_app.add_handler(CommandHandler("stop", stop_command))

    # Add middleware handlers
    bot_app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), check_user_started), group=-2)
    bot_app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), check_registration), group=-1)

    # Add message handler to log all user activity and handle Rapidgator URLs
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message), group=1)

    # Add error handler
    bot_app.add_error_handler(error_handler)

    # Start the bot
    logger.info("Initializing the bot...")
    await bot_app.initialize()
    logger.info("Starting the bot...")
    await bot_app.start()
    logger.info("Starting polling...")
    await bot_app.updater.start_polling()
    logger.info("Bot started successfully.")
    return bot_app

async def stop_bot(bot_app):
    """Gracefully stop the Telegram bot."""
    logger.info("Stopping the bot...")
    if bot_app:
        if bot_app.updater:
            logger.info("Stopping the updater...")
            await bot_app.updater.stop()
        logger.info("Stopping the bot application...")
        await bot_app.stop()
    logger.info("Bot stopped successfully.")