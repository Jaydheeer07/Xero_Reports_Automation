from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
from sqlalchemy import text, event
import structlog
import ssl

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# Supabase connection configuration
# The pooler (port 6543) uses PgBouncer which requires special handling for asyncpg
_connect_args = {}
_pool_class = None
_is_supabase = "supabase.co" in settings.database_url or "pooler.supabase.com" in settings.database_url

if _is_supabase:
    # Create SSL context that doesn't verify certificates (required for Supabase pooler)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    _connect_args = {
        "ssl": ssl_context,
        # Disable prepared statements for PgBouncer compatibility (transaction mode)
        "statement_cache_size": 0,
    }
    # Use NullPool to let Supabase's pooler handle connection pooling
    _pool_class = NullPool

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.log_level == "DEBUG",
    poolclass=_pool_class,
    connect_args=_connect_args,
    # Disable SQLAlchemy's prepared statement cache for PgBouncer compatibility
    query_cache_size=0 if _is_supabase else 500,
)

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Base class for models
Base = declarative_base()


async def init_db():
    """Initialize database connection."""
    try:
        async with engine.begin() as conn:
            # Test connection
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection successful")
    except Exception as e:
        logger.error("Database connection failed", error=str(e))
        raise


async def close_db():
    """Close database connection."""
    await engine.dispose()
    logger.info("Database connection disposed")


async def get_db() -> AsyncSession:
    """Dependency to get database session."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
