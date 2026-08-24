from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # --- App ---
    APP_NAME: str = "AetherCode"
    ENV: str = "development"  # development | production

    # --- Supabase (Database + pgvector + Auth) ---
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    # --- LLM Provider (free tier) ---
    # Set BOTH keys for automatic Groq → Gemini fallback when Groq
    # hits rate limits or runs out of tokens. If only one is set,
    # that provider is used exclusively.
    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    OLLAMA_BASE_URL: str = ""

    # --- GitHub (Repository Search, PR Agent) ---
    GITHUB_TOKEN: str = ""
    GITHUB_REPO_OWNER: str = ""
    GITHUB_REPO_NAME: str = ""

    # --- Safety limits ---
    MAX_DEBUG_ATTEMPTS: int = 3  # cap for the self-correction loop

    # Extra CORS origins for production (comma-separated URLs)
    CORS_ORIGINS: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings loader so we don't re-read the environment
    on every import. Import `settings` below wherever needed.
    """
    return Settings()


settings = get_settings()