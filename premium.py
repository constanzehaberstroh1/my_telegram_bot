# premium.py
import aiohttp
import aiofiles
from aiofiles.threadpool.binary import AsyncFileIO
from tqdm.asyncio import tqdm
from pathlib import Path
import logging
import os
from telegram.error import BadRequest, TimedOut
from telegram import Update, Message
from telegram.ext import ContextTypes
import re
import hashlib
from db import add_file_info, update_file_thumbnail
import ffmpeg
import magic
import math
import asyncio

logger = logging.getLogger(__name__)

FILE_SIZE_LIMIT = 50 * 1024 * 1024  # 50 MB

def guess_mime_type_from_header(file_path):
    """Guesses the MIME type of a file based on its header (magic number)."""
    try:
        mime = magic.Magic(mime=True)
        mime_type = mime.from_file(file_path)
        return mime_type
    except magic.MagicException as e:
        logger.error(f"Error guessing MIME type from header: {e}")
        return None

async def create_video_thumbnail_sheet_async(video_path, thumbnail_path, num_frames=12):
    """
    Creates a thumbnail sheet from a video file using ffmpeg asynchronously.
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
        select_expr = "+".join([f"eq(t,{i * interval})" for i in range(num_frames)])

        # Convert thumbnail_path to an absolute path
        thumbnail_path_absolute = os.path.abspath(thumbnail_path)
        logger.info(f"Creating thumbnail sheet at: {thumbnail_path_absolute}")

        # Run FFmpeg asynchronously
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", video_path,
            "-filter_complex", f"[0:v]select='{select_expr}',scale=320:-1,tile={cols}x{rows}[out]",
            "-map", "[out]",
            "-vframes", "1",
            thumbnail_path_absolute,
            "-y",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error(f"Error creating thumbnail sheet for {video_path}:")
            logger.error(f"  FFmpeg stderr: {stderr.decode()}")
            raise ffmpeg.Error(
                'ffmpeg',
                stdout,
                stderr
            )

        # Log thumbnail sheet dimensions
        probe = ffmpeg.probe(thumbnail_path_absolute)
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream:
            width = int(video_stream['width'])
            height = int(video_stream['height'])
            logger.info(f"Thumbnail sheet dimensions: {width} x {height}")
        else:
            logger.warning("Could not determine thumbnail sheet dimensions.")

        logger.info(f"Thumbnail sheet created for {video_path} at {thumbnail_path_absolute}")

    except ffmpeg.Error as e:
        logger.error(f"Error creating thumbnail sheet for {video_path}: {e.stderr.decode()}")
        raise

async def download_file_from_premium_to(url: str, user_id: int, api_key: str, user_premium_id: str, download_dir: str, update: Update, context: ContextTypes.DEFAULT_TYPE, processing_message: Message):
    """Downloads a file from a given URL using the Premium.to API."""
    last_progress_update = 0

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('http://api.premium.to/api/2/getfile.php', params={
                'userid': user_premium_id,
                'apikey': api_key,
                'link': url
            }) as response:
                if response.status == 200:
                    if response.headers.get('Content-Type') == 'application/octet-stream':
                        try:
                            total_size = int(response.headers.get('Content-Length', 0))
                            user_dir = Path(download_dir) / str(user_id)
                            user_dir.mkdir(parents=True, exist_ok=True)

                            content_disposition = response.headers.get('Content-Disposition', '')
                            match = re.search(r"filename\*=UTF-8''(.+)", content_disposition)
                            if match:
                                file_name = match.group(1)
                            else:
                                file_name = os.path.basename(url)

                            try:
                                file_name = file_name.encode('latin1').decode('utf-8')
                            except UnicodeEncodeError:
                                pass

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
                                                    chat_id=processing_message.chat_id,
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
                                                chat_id=processing_message.chat_id,
                                                message_id=processing_message.message_id,
                                                text=f"Download complete!"
                                            )

                            file_hash_str = file_hash.hexdigest()
                            final_file_path = user_dir / file_hash_str
                            os.rename(user_dir / "tempfile", final_file_path)

                            add_file_info(file_hash_str, str(final_file_path), file_name)

                            if total_size < FILE_SIZE_LIMIT:
                                with open(final_file_path, 'rb') as f:
                                    try:
                                        file_doc = await context.bot.send_document(
                                            chat_id=processing_message.chat_id,
                                            document=f,
                                            caption="Here is your file!",
                                            filename=file_name,
                                            read_timeout=30,
                                            write_timeout=30,
                                            connect_timeout=30
                                        )
                                    except TimedOut as e:
                                        logger.error(f"Telegram API timed out while sending document: {e}")
                                        return None

                                file_id = file_doc.document.file_id
                                file_path_on_telegram = await context.bot.get_file(file_id)
                                file_url_on_telegram = f"https://api.telegram.org/file/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/{file_path_on_telegram.file_path}"

                                logger.info(f"File sent directly to user {user_id}")
                                return {
                                    "file_hash": file_hash_str,
                                    "file_url": file_url_on_telegram
                                }
                            else:
                                file_host_base_url = os.getenv("FILE_HOST_BASE_URL")
                                if file_host_base_url:
                                    file_url = f"{file_host_base_url}/download/{file_hash_str}"
                                    logger.info(f"File link sent to user {user_id}")
                                    return {
                                        "file_hash": file_hash_str,
                                        "file_url": file_url
                                    }
                                else:
                                    logger.error("Error: FILE_HOST_BASE_URL environment variable not set.")
                                    return None
                        except Exception as e:
                            logger.error(f"Error during download or sending file: {e}")
                            return None
                    else:
                        try:
                            api_response = await response.json()
                            if "code" in api_response:
                                error_code = api_response["code"]
                                error_message = api_response.get("message", "Unknown error")
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
                                await processing_message.edit_text(f"Error: {error_msg}")
                                logger.error(f"Premium.to API error: {error_msg}")
                                return None
                        except aiohttp.ContentTypeError:
                            logger.error(f"Failed to decode JSON response: {await response.text()}")
                            await processing_message.edit_text("Error: Invalid response from Premium.to API.")
                            return None
                elif response.status == 302:
                    redirect_url = response.headers.get('Location')
                    logger.info(f"Received redirect to: {redirect_url}")
                    await processing_message.edit_text(f"Download redirected to: {redirect_url}")
                    return None
                else:
                    error_msg = f"Error: Premium.to API returned status code {response.status}"
                    await processing_message.edit_text(error_msg)
                    logger.error(error_msg)
                    return None

    except Exception as e:
        error_msg = f"Error calling Premium.to API: {e}"
        await processing_message.edit_text(error_msg)
        logger.error(error_msg)
        return None