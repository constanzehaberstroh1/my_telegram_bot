import aiohttp
import aiofiles
from aiofiles.threadpool.binary import AsyncFileIO
from tqdm.asyncio import tqdm
from pathlib import Path
import logging
import os
from telegram.error import BadRequest, TimedOut
import re

logger = logging.getLogger(__name__)

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
        The URL to the downloaded file on Telegram's server, or None if an error occurred.
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

                            file_path = user_dir / file_name
                            logger.info(f"file name is : {file_name}")
                            # Check file size and decide whether to send file directly or as a link
                            if total_size < 50 * 1024 * 1024:  # Less than 8 MB 
                                # Download the file
                                async with aiofiles.open(file_path, 'wb') as f:
                                    chunk_size = 4096
                                    downloaded_size = 0

                                    async for chunk in response.content.iter_chunked(chunk_size):
                                        if chunk:
                                            await f.write(chunk)
                                            downloaded_size += len(chunk)
                                            # Calculate progress percentage
                                            progress = int((downloaded_size / total_size) * 100) if total_size > 0 else 0
                                            # Send progress update to user (only if progress has changed by at least 5% and is not 100%)
                                            if progress >= last_progress_update + 5 and progress < 100:
                                                try:
                                                    await context.bot.edit_message_text(
                                                        chat_id=update.message.chat_id,
                                                        message_id=processing_message.message_id,
                                                        text=f"Downloading: {progress}%"
                                                    )
                                                    last_progress_update = progress  # Update the last progress update percentage
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
                                                # Send a final update message to indicate completion
                                                await context.bot.edit_message_text(
                                                    chat_id=update.message.chat_id,
                                                    message_id=processing_message.message_id,
                                                    text=f"Download complete!"
                                                )

                                # Send the downloaded file to the user using send_document
                                with open(file_path, 'rb') as f:
                                    try:
                                        file_doc = await context.bot.send_document(
                                            chat_id=update.message.chat_id,
                                            document=f,
                                            caption="Here is your file!",
                                            read_timeout=30,  # Increased timeout
                                            write_timeout=30,  # Increased timeout
                                            connect_timeout=30   # Increased timeout
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
                                return file_url_on_telegram

                            else:  # 10 MB or greater (Reduced to 8 MB)
                                # Download the file
                                async with aiofiles.open(file_path, 'wb') as f:
                                    chunk_size = 4096
                                    downloaded_size = 0

                                    async for chunk in response.content.iter_chunked(chunk_size):
                                        if chunk:
                                            await f.write(chunk)
                                            downloaded_size += len(chunk)
                                            # Calculate progress percentage
                                            progress = int((downloaded_size / total_size) * 100) if total_size > 0 else 0
                                            # Send progress update to user (only if progress has changed by at least 5% and is not 100%)
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
                                                # Send a final update message to indicate completion
                                                await context.bot.edit_message_text(
                                                    chat_id=update.message.chat_id,
                                                    message_id=processing_message.message_id,
                                                    text=f"Download complete!"
                                                )

                                # Send a link to the user
                                # Get the base URL for file hosting from the environment variable
                                file_host_base_url = os.getenv("FILE_HOST_BASE_URL")
                                if file_host_base_url:
                                    file_url = f"{file_host_base_url}/downloads/{user_id}/{file_name}"
                                    await update.message.reply_text(
                                        f"Your file has been downloaded and is available here: {file_url}"
                                    )
                                    logger.info(f"File link sent to user {user_id}")
                                    return file_url
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