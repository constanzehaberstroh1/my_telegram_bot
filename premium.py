# premium.py
import math
import aiohttp
import aiofiles
from aiofiles.threadpool.binary import AsyncFileIO
from tqdm.asyncio import tqdm
from pathlib import Path
import logging
import os
from telegram.error import BadRequest, TimedOut
import re
import hashlib
from db import add_file_info, update_file_thumbnail
import ffmpeg
import magic  # Import python-magic

logger = logging.getLogger(__name__)

def guess_mime_type_from_header(file_path):
    """Guesses the MIME type of a file based on its header (magic number)."""
    try:
        mime = magic.Magic(mime=True)
        mime_type = mime.from_file(file_path)
        return mime_type
    except magic.MagicException as e:
        logger.error(f"Error guessing MIME type from header: {e}")
        return None
    
def guess_mime_type_from_header(file_path):
    """Guesses the MIME type of a file based on its header (magic number)."""
    try:
        mime = magic.Magic(mime=True)
        mime_type = mime.from_file(file_path)
        return mime_type
    except magic.MagicException as e:
        logger.error(f"Error guessing MIME type from header: {e}")
        return None

def create_video_thumbnail_sheet(video_path, thumbnail_path, num_frames=12):
    """
    Creates a thumbnail sheet from a video file using ffmpeg.
    It now extracts frames from across the entire video duration.
    """
    try:
        # Get video duration
        probe = ffmpeg.probe(video_path)
        duration = float(probe['format']['duration'])

        # Calculate interval between frames
        interval = duration / num_frames

        # Determine tile layout
        if num_frames <= 4:
            cols = num_frames
            rows = 1
        elif num_frames <= 9:
            cols = 3
            rows = math.ceil(num_frames / cols)
        else:
            cols = 4
            rows = math.ceil(num_frames / cols)

        # Generate thumbnail sheet using a filter_complex expression
        # This approach is more efficient as it directly selects frames at specific timestamps
        select_expr = "+".join([f"eq(t,{i * interval})" for i in range(num_frames)])
        (
            ffmpeg
            .input(video_path)
            .filter('select', select_expr)
            .filter('scale', 640, -1)
            .filter('tile', f'{cols}x{rows}')
            .output(thumbnail_path, vframes=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        # Log thumbnail sheet dimensions
        probe = ffmpeg.probe(thumbnail_path)
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream:
            width = int(video_stream['width'])
            height = int(video_stream['height'])
            logger.info(f"Thumbnail sheet dimensions: {width} x {height}")
        else:
            logger.warning("Could not determine thumbnail sheet dimensions.")

        logger.info(f"Thumbnail sheet created for {video_path} at {thumbnail_path}")

    except ffmpeg.Error as e:
        logger.error(f"Error creating thumbnail sheet for {video_path}: {e.stderr.decode()}")

async def download_file_from_premium_to(url: str, user_id: int, api_key: str, user_premium_id: str, download_dir: str, update, context):
    """
    Downloads a file from a given URL using the Premium.to API.

    Args:
        url: The Rapidgator URL of the file to download.
        user_id: The Telegram user ID.
        api_key: Your Premium.to API key.
        user_premium_id: Your Premium.to user ID.
        download_dir: The directory to save downloaded files.
        update: The Telegram Update object.
        context: The Telegram ContextTypes object.

    Returns:
        A dictionary containing the file hash and the URL to the downloaded file, or None if an error occurred.
    """
    # Send initial "Processing..." message
    processing_message = await update.message.reply_text("Processing your Rapidgator link...")
    last_progress_update = 0

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('http://api.premium.to/api/2/getfile.php', params={
                'userid': user_premium_id,
                'apikey': api_key,
                'link': url
            }) as response:
                # Handle API response based on status code
                if response.status == 200:
                    # Check content type
                    if response.headers.get('Content-Type') == 'application/octet-stream':
                        # Download the file and send it to the user
                        try:
                            total_size = int(response.headers.get('Content-Length', 0))

                            # Create user directory if it doesn't exist
                            user_dir = Path(download_dir) / str(user_id)
                            user_dir.mkdir(parents=True, exist_ok=True)

                            # Get filename from Content-Disposition header
                            content_disposition = response.headers.get('Content-Disposition', '')
                            match = re.search(r"filename\*=UTF-8''(.+)", content_disposition)
                            if match:
                                file_name = match.group(1)
                            else:
                                # Fallback to URL if Content-Disposition is not present or doesn't match
                                file_name = os.path.basename(url)

                            # Decode the filename if necessary
                            try:
                                file_name = file_name.encode('latin1').decode('utf-8')
                            except UnicodeEncodeError:
                                pass  # If encoding fails, keep the original filename

                            # Generate file hash
                            file_hash = hashlib.sha256()
                            async with aiofiles.open(user_dir / "tempfile", 'wb') as temp_file:
                                chunk_size = 4096
                                downloaded_size = 0
                                async for chunk in response.content.iter_chunked(chunk_size):
                                    if chunk:
                                        await temp_file.write(chunk)
                                        file_hash.update(chunk)
                                        downloaded_size += len(chunk)
                                        progress = int((downloaded_size / total_size) * 100) if total_size > 0 else 0
                                        if progress >= last_progress_update + 5 and progress < 100:
                                            try:
                                                await context.bot.edit_message_text(
                                                    chat_id=update.message.chat_id,
                                                    message_id=processing_message.message_id,
                                                    text=f"Downloading: {progress}%"
                                                )
                                                last_progress_update = progress
                                            except BadRequest as e:
                                                if "Message is not modified" in str(e):
                                                    logger.info(f"Message {processing_message.message_id} not modified - progress likely the same.")
                                                elif "Message can't be edited" in str(e):
                                                    logger.warning(f"Could not edit message {processing_message.message_id} - likely too old or deleted.")
                                                else:
                                                    logger.error(f"Error editing message: {e}")
                                            except Exception as e:
                                                logger.error(f"An unexpected error occurred while editing message: {e}")
                                        elif progress == 100:
                                            await context.bot.edit_message_text(
                                                chat_id=update.message.chat_id,
                                                message_id=processing_message.message_id,
                                                text=f"Download complete!"
                                            )

                            # Finalize hash and rename file
                            file_hash_str = file_hash.hexdigest()
                            final_file_path = user_dir / file_hash_str
                            os.rename(user_dir / "tempfile", final_file_path)

                            # Add file info to the database
                            add_file_info(file_hash_str, str(final_file_path), file_name)

                            # Check if the file is a video and create a thumbnail sheet
                            mime_type = guess_mime_type_from_header(str(final_file_path))
                            logger.info(f"File: {final_file_path}, MIME type (from header): {mime_type}")

                            if (mime_type and mime_type.startswith('video/')) or str(final_file_path).lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
                                images_dir = os.getenv("IMAGES_DIR")
                                if images_dir:
                                    thumbnail_dir = Path(images_dir) / str(user_id)
                                    thumbnail_dir.mkdir(parents=True, exist_ok=True)
                                    thumbnail_path = thumbnail_dir / f"{file_hash_str}.jpg"

                                    try:
                                        create_video_thumbnail_sheet(str(final_file_path), str(thumbnail_path)) # Use new function
                                        update_file_thumbnail(file_hash_str, str(thumbnail_path))
                                        logger.info(f"Thumbnail sheet created for {file_hash_str} at {thumbnail_path}")
                                    except Exception as e:
                                        logger.error(f"Error creating thumbnail sheet for {file_hash_str}: {e}")
                                else:
                                    logger.error("IMAGES_DIR environment variable not set. Cannot create thumbnails.")

                            # Check file size and decide whether to send file directly or as a link
                            if total_size < 50 * 1024 * 1024:  # Less than 50 MB
                                # Send the downloaded file to the user using send_document
                                with open(final_file_path, 'rb') as f:
                                    try:
                                        file_doc = await context.bot.send_document(
                                            chat_id=update.message.chat_id,
                                            document=f,
                                            caption="Here is your file!",
                                            filename= file_name,
                                            read_timeout=30,
                                            write_timeout=30,
                                            connect_timeout=30
                                        )
                                    except TimedOut as e:
                                        logger.error(f"Telegram API timed out while sending document: {e}")
                                        await update.message.reply_text("Error: Telegram API timed out while sending the file. Please try again later.")
                                        return None

                                # Get the file ID from the sent document
                                file_id = file_doc.document.file_id

                                # Get the file path on Telegram's server
                                file_path_on_telegram = await context.bot.get_file(file_id)

                                # Construct the URL to the file on Telegram's server
                                file_url_on_telegram = f"https://api.telegram.org/file/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/{file_path_on_telegram.file_path}"

                                # Inform the user with the link to the file
                                await update.message.reply_text(
                                    f"Your file has been downloaded and is available here: {file_url_on_telegram}"
                                )
                                logger.info(f"File sent directly to user {user_id}")
                                return {
                                    "file_hash": file_hash_str,
                                    "file_url": file_url_on_telegram
                                }

                            else:  # 50 MB or greater
                                # Send a link to the user
                                file_host_base_url = os.getenv("FILE_HOST_BASE_URL")
                                if file_host_base_url:
                                    file_url = f"{file_host_base_url}/download/{file_hash_str}"
                                    await update.message.reply_text(
                                        f"Your file has been downloaded and is available here: {file_url}"
                                    )
                                    logger.info(f"File link sent to user {user_id}")
                                    return {
                                        "file_hash": file_hash_str,
                                        "file_url": file_url
                                    }
                                else:
                                    error_msg = "Error: FILE_HOST_BASE_URL environment variable not set."
                                    await update.message.reply_text(error_msg)
                                    logger.error(error_msg)
                                    return None

                        except Exception as e:
                            error_msg = f"Error during download or sending file: {e}"
                            await update.message.reply_text(error_msg)
                            logger.error(error_msg)
                            return None
                    else:
                        # Handle API error codes (JSON response)
                        try:
                            api_response = await response.json()
                            if "code" in api_response:
                                error_code = api_response["code"]
                                error_message = api_response.get("message", "Unknown error")

                                # Map error codes to messages
                                error_messages = {
                                    400: "Invalid parameters",
                                    401: "Invalid API authentication",
                                    402: "Filehost is not supported",
                                    403: "Not enough traffic",
                                    404: "File not found",
                                    429: "Too many open connections",
                                    500: "Currently no available premium account for this filehost",
                                }
                                error_msg = error_messages.get(error_code, f"Unknown error (code {error_code})")

                                await update.message.reply_text(f"Error: {error_msg}")
                                logger.error(f"Premium.to API error: {error_msg}")
                                return None
                        except aiohttp.ContentTypeError:
                            logger.error(f"Failed to decode JSON response: {await response.text()}")
                            await update.message.reply_text("Error: Invalid response from Premium.to API.")
                            return None

                elif response.status == 302:
                    redirect_url = response.headers.get('Location')
                    logger.info(f"Received redirect to: {redirect_url}")
                    await update.message.reply_text(f"Download redirected to: {redirect_url}")
                    return None
                else:
                    error_msg = f"Error: Premium.to API returned status code {response.status}"
                    await update.message.reply_text(error_msg)
                    logger.error(error_msg)
                    return None

    except Exception as e:
        error_msg = f"Error calling Premium.to API: {e}"
        await update.message.reply_text(error_msg)
        logger.error(error_msg)
        return None