from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache


class Settings(BaseSettings):
    """Application configuration settings."""
    
    # Database
    database_url: str = "postgresql+asyncpg://xero_user:xero_password@postgres:5432/xero_automation"
    
    # Encryption
    encryption_key: str = "your-32-byte-fernet-key-here-change-me"
    
    # API Security
    api_key: str = "change-this-api-key-in-production"
    
    # Playwright
    playwright_timeout: int = 30000  # milliseconds
    headless: bool = True  # Set to False for manual auth setup

    # CORS - comma-separated list of allowed origins (override in .env for production)
    allowed_origins: str = "http://localhost:8000,http://localhost:3000,http://127.0.0.1:8000"
    
    # Directories
    download_dir: str = "/app/downloads"
    screenshot_dir: str = "/app/screenshots"
    session_dir: str = "/app/sessions"
    
    # Screenshot settings
    debug_screenshots: bool = False  # Set to True for development, False for production
    screenshot_retention_days: int = 7  # Auto-delete screenshots older than this
    
    # Logging
    log_level: str = "INFO"
    
    # Optional: n8n webhook for notifications
    n8n_webhook_url: str | None = None
    
    # Xero credentials for automated login
    xero_email: str | None = None
    xero_password: str | None = None
    
    # Xero security question answers (for MFA bypass)
    # Question 1: "As a child, what did you want to be when you grew up?"
    xero_security_answer_1: str | None = None
    # Question 2: "What is your most disliked holiday?"
    xero_security_answer_2: str | None = None
    # Question 3: "What is your dream job?"
    xero_security_answer_3: str | None = None
    
    @field_validator('encryption_key')
    @classmethod
    def validate_encryption_key(cls, v):
        if v == "your-32-byte-fernet-key-here-change-me":
            import warnings
            warnings.warn("WARNING: Using default encryption key! Set ENCRYPTION_KEY in .env for production.")
        return v
    
    @field_validator('api_key')
    @classmethod
    def validate_api_key(cls, v):
        if v == "change-this-api-key-in-production":
            import warnings
            warnings.warn("WARNING: Using default API key! Set API_KEY in .env for production.")
        return v
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
