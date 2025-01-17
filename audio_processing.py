# audio_processing.py
import logging
import re
from telegram import Update
from telegram.ext import ContextTypes
from db import get_file_info_by_hash
import os
import tempfile

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load the list of gap filler words, useless words, and conjunction words from files
def load_words_from_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        words = [line.strip().lower() for line in f]
    return words

# Define sets of words to be removed
gap_fillers = set(load_words_from_file('gap_fillers.txt'))
useless_words = set(load_words_from_file('useless_words.txt'))
conjunctions = set(load_words_from_file('conjunctions.txt'))

async def process_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles messages determined to be audio files."""
    message = update.message

    # Determine whether the message is a document or an audio
    if message.document:
        file_id = message.document.file_id
        mime_type = message.document.mime_type
        file_name = message.document.file_name or ""
    elif message.audio:
        file_id = message.audio.file_id
        mime_type = message.audio.mime_type
        file_name = message.audio.file_name or ""
    else:
        logger.warning("Message is neither a document nor an audio. Skipping.")
        return

    # Check if the mime type indicates an audio file
    if not mime_type or not mime_type.startswith('audio/'):
        logger.info(f"Skipping non-audio file: {file_name} (MIME type: {mime_type})")
        return

    logger.info(f"Processing audio file: {file_name} (MIME type: {mime_type})")

    try:
        # Download the file
        new_file = await context.bot.get_file(file_id)
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            await new_file.download_to_drive(temp_file.name)
            temp_file_path = temp_file.name

        # Extract hashtags from the temporary file's name
        hashtags = generate_hashtags(os.path.basename(temp_file_path))

        # Reply with hashtags
        if hashtags:
            await message.reply_text(f"Hashtags: {' '.join(hashtags)}", reply_to_message_id=message.message_id)
        else:
            await message.reply_text("Could not generate hashtags for this file.", reply_to_message_id=message.message_id)

    except Exception as e:
        logger.error(f"Error processing audio file: {e}")
        await message.reply_text("An error occurred while processing the audio file.", reply_to_message_id=message.message_id)

    finally:
        # Clean up: delete the temporary file
        if 'temp_file_path' in locals():
            os.unlink(temp_file_path)


def clean_filename(filename: str) -> str:
    """Cleans the filename by removing extension, special characters, and extra spaces."""
    # Remove file extension
    filename = os.path.splitext(filename)[0]
    # Remove special characters except hyphens and underscores
    filename = re.sub(r"[^\w\s-]", " ", filename)
    # Replace multiple spaces with a single space
    filename = re.sub(r"\s+", " ", filename).strip()
    return filename

def generate_hashtags(filename: str) -> list[str]:
    """Generates hashtags from a cleaned filename."""
    cleaned_filename = clean_filename(filename)
    words = cleaned_filename.lower().split()

    # Filter out the words to be removed
    filtered_words = [
        word for word in words
        if word not in gap_fillers and word not in useless_words and word not in conjunctions
    ]

    # Generate hashtags by adding '#' to EACH word
    hashtags = ["#" + word for word in filtered_words]

    return hashtags