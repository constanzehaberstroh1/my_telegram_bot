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

def connect_to_mongodb():
    """Establishes a connection to MongoDB."""
    global _client, _users_collection, _log_collection
    MONGO_URI = os.getenv("MONGO_URI")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME")
    MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME")
    MONGO_LOG_COLLECTION_NAME = os.getenv("MONGO_LOG_COLLECTION_NAME")

    if _client:
        return  # Already connected

    try:
        _client = MongoClient(MONGO_URI)
        db = _client[MONGO_DB_NAME]
        _users_collection = db[MONGO_COLLECTION_NAME]
        _log_collection = db[MONGO_LOG_COLLECTION_NAME]
        _client.admin.command('ping')  # Test the connection
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

def close_mongodb_connection():
    """Closes the MongoDB connection."""
    global _client
    if _client:
        _client.close()
        logger.info("MongoDB connection closed.")