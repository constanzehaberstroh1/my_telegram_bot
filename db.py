# db.py
import logging
import os
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_client = None
_users_collection = None
_log_collection = None
_files_collection = None

def connect_to_mongodb():
    """Establishes a connection to MongoDB."""
    global _client, _users_collection, _log_collection, _files_collection
    MONGO_URI = os.getenv("MONGO_URI")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME")
    MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME")
    MONGO_LOG_COLLECTION_NAME = os.getenv("MONGO_LOG_COLLECTION_NAME")
    MONGO_FILES_COLLECTION_NAME = os.getenv("MONGO_FILES_COLLECTION_NAME")

    if _client:
        return

    try:
        _client = MongoClient(MONGO_URI)
        db = _client[MONGO_DB_NAME]
        _users_collection = db[MONGO_COLLECTION_NAME]
        _log_collection = db[MONGO_LOG_COLLECTION_NAME]
        _files_collection = db[MONGO_FILES_COLLECTION_NAME]
        _client.admin.command('ping')
        logger.info("Successfully connected to MongoDB!")
    except ConnectionFailure as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")

def get_users_collection():
    """Returns the users collection."""
    global _users_collection
    if _users_collection is None:
        connect_to_mongodb()
    return _users_collection

def get_log_collection():
    """Returns the log collection."""
    global _log_collection
    if _log_collection is None:
        connect_to_mongodb()
    return _log_collection

def get_files_collection():
    """Returns the files collection."""
    global _files_collection
    if _files_collection is None:
        connect_to_mongodb()
    return _files_collection

def add_file_info(file_hash, file_path, original_filename):
    """Adds file information to the database."""
    files_collection = get_files_collection()
    if files_collection is None:
        logger.error("MongoDB connection not established. Cannot add file info.")
        return

    file_data = {
        "file_hash": file_hash,
        "file_path": file_path,
        "original_filename": original_filename,
        "thumbnail_path": None
    }

    try:
        files_collection.insert_one(file_data)
        logger.info(f"Added file info to MongoDB: {file_hash} -> {original_filename}")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")

def get_file_info_by_hash(file_hash):
    """Retrieves file information from the database based on file hash."""
    files_collection = get_files_collection()
    if files_collection is None:
        logger.error("MongoDB connection not established. Cannot retrieve file info.")
        return None

    try:
        file_info = files_collection.find_one({"file_hash": file_hash})
        if file_info:
            logger.info(f"Retrieved file info from MongoDB for hash: {file_hash}")
            return file_info
        else:
            logger.warning(f"File info not found for hash: {file_hash}")
            return None
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        return None
    
def add_user_downloaded_file(user_id, file_hash):
    """Adds a downloaded file to the user's record in the database."""
    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot add downloaded file to user.")
        return

    try:
        users_collection.update_one(
            {"user_id": user_id},
            {"$addToSet": {"downloaded_files": file_hash}}
        )
        logger.info(f"Added file {file_hash} to user {user_id}'s downloaded files.")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")

def get_file_info_by_user(user_id):
    """Retrieves file information for a specific user."""
    users_collection = get_users_collection()
    if users_collection is None:
        logger.error("MongoDB connection not established. Cannot retrieve user info.")
        return None

    try:
        user_files = []
        user_data = users_collection.find_one({"user_id": user_id})
        if user_data and "downloaded_files" in user_data:
            files_collection = get_files_collection()
            for file_hash in user_data["downloaded_files"]:
                file_info = files_collection.find_one({"file_hash": file_hash})
                if file_info:
                    user_files.append(file_info)
        return user_files
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")
        return None
        
def update_file_thumbnail(file_hash, thumbnail_path):
    """Updates the thumbnail path for a specific file."""
    files_collection = get_files_collection()
    if files_collection is None:
        logger.error("MongoDB connection not established. Cannot update file thumbnail.")
        return

    try:
        result = files_collection.update_one(
            {"file_hash": file_hash},
            {"$set": {"thumbnail_path": thumbnail_path}}
        )
        if result.modified_count > 0:
            logger.info(f"Updated thumbnail path for file {file_hash}")
        else:
            logger.warning(f"Failed to update thumbnail path for file {file_hash}")
    except OperationFailure as e:
        logger.error(f"MongoDB operation failed: {e}")

def close_mongodb_connection():
    """Closes the MongoDB connection."""
    global _client
    if _client:
        _client.close()
        logger.info("MongoDB connection closed.")