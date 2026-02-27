from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog
import logging
import os
import time
from datetime import datetime, timedelta

from app.config import get_settings
from app.db.connection import init_db, close_db
from app.api.routes import health, auth, reports, clients
from app.services.browser_manager import BrowserManager

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger()
settings = get_settings()


def _cleanup_old_files(directory: str, max_age_days: int) -> int:
    """
    Remove files older than max_age_days from a directory.

    Args:
        directory: Path to clean up
        max_age_days: Maximum age of files in days

    Returns:
        Number of files removed
    """
    if not os.path.exists(directory):
        return 0

    removed = 0
    cutoff = time.time() - (max_age_days * 86400)

    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        if os.path.isfile(filepath):
            try:
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    removed += 1
            except OSError:
                pass  # File may have been removed by another process

    return removed


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Playwright Service", log_level=settings.log_level)
    await init_db()
    logger.info("Database connection initialized")

    # Cleanup old screenshots on startup
    removed_screenshots = _cleanup_old_files(
        settings.screenshot_dir,
        settings.screenshot_retention_days
    )
    if removed_screenshots > 0:
        logger.info(
            "Cleaned up old screenshots",
            removed=removed_screenshots,
            retention_days=settings.screenshot_retention_days
        )

    # Cleanup old downloads (keep for 30 days)
    removed_downloads = _cleanup_old_files(settings.download_dir, 30)
    if removed_downloads > 0:
        logger.info("Cleaned up old downloads", removed=removed_downloads)

    yield

    # Shutdown
    logger.info("Shutting down Playwright Service")

    # Close browser if running
    try:
        browser_manager = await BrowserManager.get_instance()
        if browser_manager.is_initialized:
            await browser_manager.close()
            logger.info("Browser closed")
    except Exception as e:
        logger.warning("Error closing browser during shutdown", error=str(e))

    await close_db()
    logger.info("Database connection closed")


app = FastAPI(
    title="Xero Reports Automation Service",
    description="FastAPI service for automating Xero report downloads using Playwright",
    version="1.1.0",
    lifespan=lifespan
)

# CORS middleware
# Configurable via ALLOWED_ORIGINS env var (comma-separated)
# This is an internal API service (called by n8n), so CORS is restrictive by default.
_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(reports.router, prefix="/api/reports", tags=["Reports"])
app.include_router(clients.router, prefix="/api/clients", tags=["Clients"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Xero Reports Automation",
        "version": "1.1.0",
        "docs": "/docs"
    }
