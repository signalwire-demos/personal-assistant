"""
Configuration management for Personal Assistant AI Agent
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base paths
BASE_DIR = Path(__file__).resolve().parent
KNOWLEDGE_DIR = BASE_DIR / "knowledge"

# SignalWire Configuration
SIGNALWIRE_SPACE_NAME = os.getenv("SIGNALWIRE_SPACE_NAME", "")
SIGNALWIRE_PROJECT_ID = os.getenv("SIGNALWIRE_PROJECT_ID", "")
SIGNALWIRE_TOKEN = os.getenv("SIGNALWIRE_TOKEN", "")

# Server Configuration
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "3000"))
SWML_PROXY_URL_BASE = os.getenv("SWML_PROXY_URL_BASE", "")
SWML_BASIC_AUTH_USER = os.getenv("SWML_BASIC_AUTH_USER", "")
SWML_BASIC_AUTH_PASSWORD = os.getenv("SWML_BASIC_AUTH_PASSWORD", "")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/ethan.db")

# Google OAuth
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")

# Admin Panel
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key")

# Business Configuration - generic fallbacks only (all real config comes from database)
# These are NOT loaded from .env - database is the only source of truth
DEFAULT_BUSINESS_NAME = "Business"
DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_OWNER_EMAIL = ""
DEFAULT_OWNER_PHONE = ""

# Knowledge Base Configuration
KNOWLEDGE_INDEX_PATH = Path(os.getenv("KNOWLEDGE_INDEX_PATH", str(KNOWLEDGE_DIR / "indexes")))
KNOWLEDGE_DOCS_PATH = Path(os.getenv("KNOWLEDGE_DOCS_PATH", str(KNOWLEDGE_DIR / "docs")))
KNOWLEDGE_SIMILARITY_THRESHOLD = float(os.getenv("KNOWLEDGE_SIMILARITY_THRESHOLD", "0.3"))
KNOWLEDGE_RESULTS_COUNT = int(os.getenv("KNOWLEDGE_RESULTS_COUNT", "5"))
KNOWLEDGE_DB_URL = os.getenv("KNOWLEDGE_DB_URL", "")
KNOWLEDGE_BACKEND = os.getenv("KNOWLEDGE_BACKEND", "sqlite")

# Agent Configuration
AGENT_NAME = "Assistant"
AGENT_VOICE = "elevenlabs.josh"
AGENT_LANGUAGE = "en-US"
AI_MODEL = os.getenv("AI_MODEL", "gpt-oss-120b")

# Speech Settings
END_OF_SPEECH_TIMEOUT = 1700  # ms
ATTENTION_TIMEOUT = 15000    # ms
INACTIVITY_TIMEOUT = 30000   # ms


def get_basic_auth():
    """Get basic auth tuple if configured"""
    if SWML_BASIC_AUTH_USER and SWML_BASIC_AUTH_PASSWORD:
        return (SWML_BASIC_AUTH_USER, SWML_BASIC_AUTH_PASSWORD)
    return None
