# [[RARO]]/apps/agent-service/src/core/config.py
# Purpose: Centralized Configuration & Client Management
# Architecture: Core Layer providing singleton access to LLM and Cache clients.
# Dependencies: pydantic-settings, google-genai, redis

import os
import logging
from typing import Optional
from pydantic_settings import BaseSettings
from google import genai
import redis

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    Defaults are provided for local development.
    """
    GEMINI_API_KEY: Optional[str] = None 
    REDIS_URL: str = "redis://localhost:6379"
    LOG_LEVEL: str = "INFO"
    
    # Model defaults for different reasoning profiles
    MODEL_FAST: str = "gemini-2.0-flash"
    MODEL_REASONING: str = "gemini-2.0-flash-lite"
    MODEL_THINKING: str = "gemini-2.0-flash-thinking-exp"

    class Config:
        env_file = ".env"
        case_sensitive = True

# Initialize Settings
settings = Settings()

# Configure Logging
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("raro.agent")

def get_gemini_client() -> Optional[genai.Client]:
    """
    Initializes and returns the Google GenAI client.
    Returns None if the API key is missing to allow for graceful failure in non-LLM paths.
    """
    if not settings.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is missing. LLM features will be disabled.")
        return None
    try:
        return genai.Client(api_key=settings.GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Gemini Client: {e}")
        return None

def get_redis_client() -> Optional[redis.Redis]:
    """
    Initializes and validates the Redis connection.
    """
    try:
        r = redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.ping()
        logger.info(f"Connected to Redis at {settings.REDIS_URL}")
        return r
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. State persistence will be disabled.")
        return None

# Global instances for service-wide import
gemini_client = get_gemini_client()
redis_client = get_redis_client()

# Integration: Imported by src/main.py, src/core/llm.py, and src/intelligence/architect.py.
# Notes: Ensure .env file contains a valid GEMINI_API_KEY for full functionality.