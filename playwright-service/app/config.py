from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application configuration settings."""
    
    # Database
    database_url: str = "postgresql+asyncpg://xero_user:xero_password@postgres:5432/xero_automation"
    
    # Encryption
    encryption_key: str = "your-32-byte-fernet-key-here-change-me"
    
    # Playwright
    playwright_timeout: int = 30000  # milliseconds
    headless: bool = True  # Set to False for manual auth setup
    
    # Directories
    download_dir: str = "/app/downloads"
    screenshot_dir: str = "/app/screenshots"
    session_dir: str = "/app/sessions"
    
    # Logging
    log_level: str = "INFO"
    
    # Optional: n8n webhook for notifications
    n8n_webhook_url: str | None = None
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
