# bot.py
import logging
import os
import re
from dotenv import load_dotenv
from telegram import ChatMember, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler, ChatMemberHandler
from pymongo.errors import OperationFailure, DuplicateKeyError
from db import connect_to_mongodb, get_file_info_by_hash, get_users_collection, get_log_collection, close_mongodb_connection, get_file_info_by_user, add_user_downloaded_file, update_file_thumbnail
from premium import download_file_from_premium_to, create_video_thumbnail_sheet_async
import mimetypes
import math
from pathlib import Path
import asyncio
from audio_processing import process_audio_message, load_words_from_file, gap_fillers, useless_words, conjunctions, generate_hashtags
from telegram.request import HTTPXRequest

# Constants
FILE_SIZE_LIMIT = 50 * 1024 * 1024  # 50 MB
PAGE_SIZE = 5  # Number of files per page

# Load environment variables
load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_KEY = os.getenv("API_KEY")
USER_ID = os.getenv("USER_ID")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR")
FILE_HOST_BASE_URL = os.getenv("FILE_HOST_BASE_URL")
IMAGES_DIR = os.getenv("IMAGES_DIR")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists files downloaded by the user through the Premium.to service."""
    user_id = update.effective_user.id
    logger.info(f"Received /premium command from user: {user_id}")

    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot list files.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    try:
        files_info = get_file_info_by_user(user_id)
        if not files_info:
            await update.message.reply_text("No files found.")
            return

        total_pages = math.ceil(len(files_info) / PAGE_SIZE)
        await send_premium_page(update, context, files_info, 0)  # Send the first page initially

    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to retrieve file list.")
    except Exception as e:
        logger.error(f"Error processing /premium command: {e}")
        await update.message.reply_text("Error: Failed to process your request.")

async def send_premium_page(update: Update, context: ContextTypes.DEFAULT_TYPE, files_info, page):
    """Sends a single page of the /premium command output."""
    user_id = update.effective_user.id
    start_index = page * PAGE_SIZE
    end_index = start_index + PAGE_SIZE
    page_files = files_info[start_index:end_index]
    total_pages = math.ceil(len(files_info) / PAGE_SIZE)

    for file_info in page_files:
        file_hash = file_info["file_hash"]
        original_filename = file_info["original_filename"]
        file_path = file_info["file_path"]
        thumbnail_path = file_info.get("thumbnail_path")

        download_url = f"{FILE_HOST_BASE_URL}/download/{file_hash}"

        try:
            file_size = os.path.getsize(file_path)
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            await update.message.reply_text(f"Error: File '{original_filename}' not found.")
            continue
        except Exception as e:
            logger.error(f"Error getting file size for {file_path}: {e}")
            await update.message.reply_text(f"Error: Could not retrieve file size for '{original_filename}'.")
            continue

        mime_type, _ = mimetypes.guess_type(file_path)

        message_text = (
            f"Filename: {original_filename}\n"
            f"Size: {file_size} bytes\n"
            f"Type: {mime_type or 'Unknown'}\n"
            f"URL: {download_url}"
        )

        keyboard = [[InlineKeyboardButton("Download", url=download_url)]]

        # Add pagination buttons
        if total_pages > 1:
            pagination_buttons = []
            if page > 0:
                pagination_buttons.append(InlineKeyboardButton("Previous", callback_data=f"premium_{page - 1}"))
            if page < total_pages - 1:
                pagination_buttons.append(InlineKeyboardButton("Next", callback_data=f"premium_{page + 1}"))
            keyboard.append(pagination_buttons)

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send message with thumbnail if available
        if thumbnail_path:
            try:
                with open(thumbnail_path, "rb") as thumb_file:
                    await update.message.reply_photo(photo=thumb_file, caption=message_text, reply_markup=reply_markup)
            except FileNotFoundError:
                logger.error(f"Thumbnail not found: {thumbnail_path}")
                await update.message.reply_text(message_text, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Error sending thumbnail: {e}")
                await update.message.reply_text(message_text, reply_markup=reply_markup)

        else:
            await update.message.reply_text(message_text, reply_markup=reply_markup)

async def handle_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles callbacks for the /premium command pagination."""
    query = update.callback_query
    await query.answer()

    callback_data = query.data
    user_id = update.effective_user.id

    if callback_data.startswith("premium_"):
        page = int(callback_data.split("_")[1])
        files_info = get_file_info_by_user(user_id)
        total_pages = math.ceil(len(files_info) / PAGE_SIZE)

        if 0 <= page < total_pages:
            await query.message.delete()
            await send_premium_page(update, context, files_info, page)
        else:
            logger.warning(f"Invalid page requested: {page}")
            await query.message.edit_text("Invalid page requested.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Starts the bot for the user and prompts for registration."""
    user_id = update.effective_user.id
    logger.info(f"Received /start command from user: {user_id}")

    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot start bot for user.")
        await update.message.reply_text("Error: Database connection not available.")
        return

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

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registers the user and saves info to MongoDB."""
    user = update.effective_user
    user_id = user.id
    logger.info(f"Received /register command from user: {user_id}")

    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot register user.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    try:
        existing_user = users_collection.find_one({"user_id": user_id})
        if existing_user and existing_user.get("deleted", False):
            # User is soft-deleted, update and welcome back
            users_collection.update_one({"user_id": user_id}, {"$set": {"deleted": False}})
            logger.info(f"User {user_id} re-registered (previously soft-deleted).")
            await update.message.reply_text(f"Welcome back, {existing_user['first_name']}! You have been re-registered.")
            return
        elif existing_user:
            await update.message.reply_text("You are already registered!")
            logger.info(f"User {user_id} is already registered.")
            return

        # Save user data
        user_data = {
            "user_id": user_id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "deleted": False,
            "started": True,
            "downloaded_files": [] # Initialize downloaded_files array
        }

        users_collection.insert_one(user_data)
        logger.info(f"Registered user: {user_id} in MongoDB.")

        # Welcome message
        await update.message.reply_text(
            f"Welcome, {user.first_name}! You have been successfully registered.\n"
            f"Your details:\n"
            f"ID: {user_id}\n"
            f"First Name: {user.first_name}\n"
            f"Last Name: {user.last_name}\n"
            f"Username: {user.username}"
        )
        logger.info(f"Sent registration confirmation to user: {user_id}")

    except DuplicateKeyError:
        logger.warning(f"User {user_id} tried to register again.")
        await update.message.reply_text("You are already registered!")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to register user.")

async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retrieves and sends the user's information."""
    user_id = update.effective_user.id
    logger.info(f"Received /me command from user: {user_id}")

    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot retrieve user info.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    try:
        user_data = users_collection.find_one({"user_id": user_id})
        if user_data:
            if user_data.get("deleted", False):
                await update.message.reply_text("You are not registered. Use /register to sign up.")
                logger.info(f"User {user_id} is soft-deleted.")
            else:
                await update.message.reply_text(
                    f"Your details:\n"
                    f"ID: {user_data['user_id']}\n"
                    f"First Name: {user_data['first_name']}\n"
                    f"Last Name: {user_data['last_name']}\n"
                    f"Username: {user_data['username']}"
                )
                logger.info(f"Sent user info to: {user_id}")
        else:
            await update.message.reply_text("You are not registered yet. Use /register to sign up.")
            logger.info(f"User {user_id} is not registered.")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to retrieve user information.")

async def unregister_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Soft-deletes the user from the database."""
    user_id = update.effective_user.id
    logger.info(f"Received /unregister command from user: {user_id}")

    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot unregister user.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    try:
        result = users_collection.update_one({"user_id": user_id}, {"$set": {"deleted": True}})
        if result.modified_count > 0:
            await update.message.reply_text("You have been successfully unregistered.")
            logger.info(f"User {user_id} soft-deleted (unregistered).")
        else:
            await update.message.reply_text("You are not registered yet.")
            logger.info(f"User {user_id} is not registered (cannot unregister).")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to unregister user.")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stops the bot for the user."""
    user_id = update.effective_user.id
    logger.info(f"Received /stop command from user: {user_id}")

    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot stop bot for user.")
        await update.message.reply_text("Error: Database connection not available.")
        return

    try:
        users_collection.update_one({"user_id": user_id}, {"$set": {"started": False}})
        logger.info(f"User {user_id} stopped the bot.")
        await update.message.reply_text("Bot stopped. Use /start to start the bot again.")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to stop bot for user.")

async def check_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if the user is registered and not soft-deleted."""
    user_id = update.effective_user.id

    # Bypass registration check for /start and /register commands
    if update.message and update.message.text and (update.message.text.startswith('/start') or update.message.text.startswith('/register')):
        return True

    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot check registration status.")
        await update.message.reply_text("Error: Database connection not available.")
        return False

    try:
        user_data = users_collection.find_one({"user_id": user_id, "deleted": False})
        if user_data:
            return True
        else:
            await update.message.reply_text("You are not registered. Use /register to sign up.")
            logger.info(f"User {user_id} is not registered or is soft-deleted.")
            return False
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to check registration status.")
        return False

async def check_user_started(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if the user has started the bot."""
    user_id = update.effective_user.id

    # Bypass the check for /start, /register, and /stop commands
    if update.message and update.message.text and (update.message.text.startswith('/start') or update.message.text.startswith('/register') or update.message.text.startswith('/stop')):
        return True

    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot check user status.")
        await update.message.reply_text("Error: Database connection not available.")
        return False

    try:
        user_data = users_collection.find_one({"user_id": user_id})
        if user_data and user_data.get("started", False):
            return True
        else:
            await update.message.reply_text("Please use /start to start the bot.")
            logger.info(f"User {user_id} has not started the bot.")
            return False
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        await update.message.reply_text("Error: Failed to check user status.")
        return False

async def log_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs user activity to MongoDB."""
    log_collection = get_log_collection()
    if log_collection is None:
        logger.error("MongoDB connection not established. Cannot log user activity.")
        return

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
        # Send initial processing message
        processing_message = await update.message.reply_text("Processing your Rapidgator link...")

        # Download the file (without waiting for thumbnail generation)
        result = await download_file_from_premium_to(
            message_text, user_id, API_KEY, USER_ID, DOWNLOAD_DIR, update, context, processing_message
        )

        # Log the outcome
        log_collection = get_log_collection()
        if result:
            file_hash = result["file_hash"]
            file_url = result["file_url"]

            # Add file to user's downloaded files in the database
            add_user_downloaded_file(user_id, file_hash)

            if file_url:
                log_event = "download_success_link"
                log_message = f"File link sent to user {user_id}"
            else:
                log_event = "download_success_direct"
                log_message = f"File downloaded and sent directly to user {user_id}"

            log_entry = {
                "user_id": user_id,
                "event": log_event,
                "url": message_text,
                "file_url": file_url,
                "file_hash": file_hash,
                "timestamp": update.message.date
            }
            try:
                log_collection.insert_one(log_entry)
                logger.info(log_message)
            except OperationFailure as e:
                logger.error(f"MongoDB operation failed: {e}")

            # Edit the processing message to show the download link
            await processing_message.edit_text(f"Your file has been downloaded: {file_url}")

            # Schedule thumbnail generation as a background task
            asyncio.create_task(generate_thumbnail_and_notify(file_hash, user_id, update))

        else:
            log_event = "download_failed"
            log_message = f"Failed to download file for user {user_id}"
            log_entry = {
                "user_id": user_id,
                "event": log_event,
                "url": message_text,
                "timestamp": update.message.date
            }
            try:
                log_collection.insert_one(log_entry)
                logger.info(log_message)
            except OperationFailure as e:
                logger.error(f"MongoDB operation failed: {e}")

            # Inform the user about the error
            await processing_message.edit_text("Failed to download the file. Please check the link and try again.")

    else:
        await update.message.reply_text("Please send a valid Rapidgator URL.")

async def generate_thumbnail_and_notify(file_hash, user_id, update: Update):
    """Generates a thumbnail for the given file and notifies the user."""
    try:
        file_info = get_file_info_by_hash(file_hash)
        if not file_info:
            logger.error(f"File info not found for hash: {file_hash}")
            return

        file_path = file_info["file_path"]
        images_dir = os.getenv("IMAGES_DIR")
        thumbnail_dir = Path(images_dir) / str(user_id)
        thumbnail_dir.mkdir(parents=True, exist_ok=True)
        thumbnail_path = thumbnail_dir / f"{file_hash}.jpg"

        await create_video_thumbnail_sheet_async(str(file_path), str(thumbnail_path))
        update_file_thumbnail(file_hash, str(thumbnail_path))
        logger.info(f"Thumbnail sheet created for {file_hash} at {thumbnail_path}")

        # Send thumbnail to user
        with open(thumbnail_path, "rb") as thumb_file:
            await update.message.reply_photo(photo=thumb_file, caption=f"Thumbnail for: {file_info['original_filename']}")

    except Exception as e:
        logger.error(f"Error creating or sending thumbnail for {file_hash}: {e}")
        await update.message.reply_text(f"Error generating thumbnail for file: {file_info['original_filename']}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

async def greet_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greets new chat members and sends a message when the bot is added to a group."""
    result = update.chat_member.new_chat_member
    if result.status == ChatMember.MEMBER and result.user.id == context.bot.id:
        # Bot was added to the group
        try:
            await update.message.reply_text(
                "Hello! I'm your new audio processing bot. Send /help to see available commands."
            )
        except:
            await update.effective_chat.send_message(
                "Hello! I'm your new audio processing bot. Send /help to see available commands."
            )
        logger.info(f"Bot added to group: {update.effective_chat.title} (ID: {update.effective_chat.id})")

async def group_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes audio files in the group and sends info to the admin."""
    if not update.effective_chat.type.endswith("group"):
        await update.message.reply_text("This command is only for groups.")
        return

    chat_id = update.effective_chat.id
    admin_user_id = int(ADMIN_USER_ID) # Convert to integer

    try:
        # Get the last 50 messages (adjust as needed)
        messages = await context.bot.get_chat_history(chat_id, limit=50)

        for message in messages:
            if message.audio or message.document:
                if message.audio:
                    mime_type = message.audio.mime_type
                    file_name = message.audio.file_name
                elif message.document:
                    mime_type = message.document.mime_type
                    file_name = message.document.file_name

                # Handle forwarded files without a filename
                if not file_name:
                    if message.caption:
                        caption_parts = message.caption.split()
                        for part in caption_parts:
                            if '.' in part:
                                file_name = part
                                break
                        if not file_name:
                            file_name = caption_parts[0] if caption_parts else "unknown_file"
                    else:
                        file_name = "unknown_file"

                # Check if audio file
                if mime_type and mime_type.startswith('audio/'):
                    hashtags = generate_hashtags(file_name)
                    response = f"Filename: {file_name}\nHashtags: {' '.join(hashtags)}"
                    await context.bot.send_message(chat_id=admin_user_id, text=response)

    except Exception as e:
        logger.error(f"Error processing /group_post: {e}")
        await context.bot.send_message(chat_id=admin_user_id, text="Error processing /group_post command.")

async def run_bot():
    """Set up and run the Telegram bot."""
    logger.info("Setting up the bot...")
    connect_to_mongodb()

    bot_app = Application.builder().token(BOT_TOKEN).connect_timeout(20).read_timeout(20).get_updates_request_timeout(20).pool_timeout(20).build()

    # Add command handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("register", register_command))
    bot_app.add_handler(CommandHandler("me", me_command))
    bot_app.add_handler(CommandHandler("unregister", unregister_command))
    bot_app.add_handler(CommandHandler("stop", stop_command))
    bot_app.add_handler(CommandHandler("premium", premium_command))
    bot_app.add_handler(CommandHandler("group_post", group_post_command))

    # Add the callback query handler for /premium pagination
    bot_app.add_handler(CallbackQueryHandler(handle_premium_callback, pattern="^premium_"))

    # Add middleware handlers
    bot_app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), check_user_started), group=-2)
    bot_app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), check_registration), group=-1)

    # Add message handler to log all user activity and handle Rapidgator URLs
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message), group=1)

    # Add message handler for audio files
    bot_app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.VIDEO | filters.Document.AUDIO | filters.Document.VIDEO | filters.VIDEO_NOTE, process_audio_message))
    
    # Add error handler
    bot_app.add_error_handler(error_handler)

    # Add handler for bot being added to a group
    bot_app.add_handler(ChatMemberHandler(greet_chat_members, ChatMemberHandler.MY_CHAT_MEMBER))

    # Start the bot
    logger.info("Initializing the bot...")
    await bot_app.initialize()
    logger.info("Starting the bot...")
    await bot_app.start()
    logger.info("Starting polling...")
    await bot_app.updater.start_polling(drop_pending_updates=True)
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