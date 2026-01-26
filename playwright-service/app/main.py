from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog
import logging

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Playwright Service", log_level=settings.log_level)
    await init_db()
    logger.info("Database connection initialized")
    
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
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware (adjust origins for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
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
        "version": "1.0.0",
        "docs": "/docs"
    }
