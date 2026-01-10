# [[RARO]]/apps/agent-service/src/core/config.py
# Purpose: Centralized Configuration & Client Management
# Architecture: Core Layer providing singleton access to LLM and Cache clients.
# Dependencies: pydantic-settings, google-genai, redis

import os
import logging
from typing import Optional, Dict
from pydantic_settings import BaseSettings
from google import genai
import redis

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    Defaults are provided for local development.
    """
    GEMINI_API_KEY: Optional[str] = None
    E2B_API_KEY: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None

    REDIS_URL: str = "redis://localhost:6379"
    LOG_LEVEL: str = "INFO"

    # NEW: If set, we skip Gemini calls and send prompt dumps here
    DEBUG_PROBE_URL: Optional[str] = None
    
    # === MODEL AUTHORITY ===
    # Change specific versions here to propagate across the entire system.
    MODEL_FAST: str = "gemini-2.5-flash-lite"
    MODEL_REASONING: str = "gemini-2.5-flash-lite"
    MODEL_THINKING: str = "gemini-2.5-flash-lite"
    # THE MAPPING LAYER
    # The system sends keys (left), we use values (right).
    MODEL_ALIASES: Dict[str, str] = {
        "fast": MODEL_FAST,
        "reasoning": MODEL_REASONING,
        "thinking": MODEL_THINKING,
    }
    MODEL_CUSTOM: Optional[str] = None

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


def resolve_model(alias: str) -> str:
    """
    Resolves a semantic alias (e.g., 'fast') to a concrete model ID.
    If no alias is found, assumes the string is already a concrete ID (passthrough).
    """
    # Normalize input
    key = alias.lower().strip()
    
    if key in settings.MODEL_ALIASES:
        return settings.MODEL_ALIASES[key]
    
    # Allow custom passthrough (e.g. if user specifically requests 'gemini-1.5-pro')
    return alias

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