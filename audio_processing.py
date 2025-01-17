# audio_processing.py
import logging
import re
from telegram import Update
from telegram.ext import ContextTypes
import os

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load the list of gap filler words, useless words, and conjunction words from files
def load_words_from_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            words = [line.strip().lower() for line in f]
        return words
    except FileNotFoundError:
        logger.error(f"Error: Word list file not found at {filepath}")
        return []
    except Exception as e:
        logger.error(f"Error loading words from {filepath}: {e}")
        return []

# Define sets of words to be removed
gap_fillers = set(load_words_from_file('gap_fillers.txt'))
useless_words = set(load_words_from_file('useless_words.txt'))
conjunctions = set(load_words_from_file('conjunctions.txt'))

async def process_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles messages determined to be audio files."""
    message = update.message
    file_name = None

    # Determine file type and potential filename
    if message.document:
        mime_type = message.document.mime_type
        file_name = message.document.file_name  # Try to get from document
    elif message.audio:
        mime_type = message.audio.mime_type
        file_name = message.audio.file_name  # Try to get from audio

    # Handle forwarded files without a filename
    if not file_name:
        if message.caption:
            # Extract potential filename from caption
            caption_parts = message.caption.split()
            for part in caption_parts:
                if '.' in part:  # Simple check for file extension
                    file_name = part
                    logger.info(f"Using potential filename from caption: {file_name}")
                    break
            if not file_name:
                file_name = caption_parts[0] if caption_parts else None  # Use first word or None
                logger.info(f"Using first word of caption as filename: {file_name}")
        else:
            logger.warning("No filename found in forwarded message.")

    # Skip if no filename could be determined
    if not file_name:
        await message.reply_text("Could not determine the filename.", reply_to_message_id=message.message_id)
        return

    # Check if the mime type indicates an audio file
    if not mime_type or not mime_type.startswith('audio/'):
        logger.info(f"Skipping non-audio file: {file_name} (MIME type: {mime_type})")
        return

    logger.info(f"Processing audio file: {file_name} (MIME type: {mime_type})")

    # Extract hashtags from the filename
    hashtags = generate_hashtags(file_name)

    # Reply with hashtags
    if hashtags:
        await message.reply_text(f"Hashtags: {' '.join(hashtags)}", reply_to_message_id=message.message_id)
    else:
        await message.reply_text("Could not generate hashtags for this file.", reply_to_message_id=message.message_id)


def clean_filename(filename: str) -> str:
    """Cleans the filename by removing extension, special characters, and extra spaces."""
    try:
        # Remove file extension
        filename = os.path.splitext(filename)[0]

        # Replace underscores with spaces
        filename = filename.replace("_", " ")

        # Remove special characters except hyphens
        filename = re.sub(r"[^\w\s-]", " ", filename)

        # Replace multiple spaces with a single space
        filename = re.sub(r"\s+", " ", filename).strip()

        return filename
    except Exception as e:
        logger.error(f"Error cleaning filename '{filename}': {e}")
        return ""  # Return an empty string on error

def generate_hashtags(filename: str) -> list[str]:
    """Generates hashtags from a cleaned filename."""
    try:
        cleaned_filename = clean_filename(filename)
        if not cleaned_filename:
            return []

        words = cleaned_filename.lower().split()

        # Filter out the words to be removed
        filtered_words = [
            word for word in words
            if word not in gap_fillers and word not in useless_words and word not in conjunctions
        ]

        # Generate hashtags by adding '#' to EACH word
        hashtags = ["#" + word for word in filtered_words]
        return hashtags
    except Exception as e:
        logger.error(f"Error generating hashtags for '{filename}': {e}")
        return []  # Return an empty list on error