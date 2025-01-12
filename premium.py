# premium.py
import aiohttp
import aiofiles
from aiofiles.threadpool.binary import AsyncFileIO
from tqdm.asyncio import tqdm
from pathlib import Path
import logging
import os

logger = logging.getLogger(__name__)

async def download_file_from_premium_to(url: str, user_id: int, api_key: str, user_premium_id : str ,download_dir: str, update, context):
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
                            file_url_on_telegram = f"https://api.telegram.org/file/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/{file_path_on_telegram.file_path}"

                            # Inform the user with the link to the file
                            await update.message.reply_text(
                                f"Your file has been downloaded and is available here: {file_url_on_telegram}"
                            )
                            return file_url_on_telegram

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