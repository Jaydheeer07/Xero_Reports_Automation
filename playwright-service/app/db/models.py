from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.db.connection import Base


class Client(Base):
    """Xero client/tenant model."""
    __tablename__ = "clients"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(255), unique=True, nullable=False, index=True)
    tenant_name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, index=True)
    onedrive_folder = Column(String(500))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class XeroSession(Base):
    """Xero session storage (encrypted cookies)."""
    __tablename__ = "xero_sessions"
    
    id = Column(Integer, primary_key=True, default=1)
    cookies = Column(Text, nullable=False)  # Encrypted JSON
    oauth_tokens = Column(Text)  # Optional: encrypted OAuth tokens
    expires_at = Column(DateTime)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class DownloadLog(Base):
    """Download audit log."""
    __tablename__ = "download_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    report_type = Column(String(50), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)
    file_path = Column(String(500))
    file_name = Column(String(255))
    file_size = Column(Integer)
    error_message = Column(Text)
    screenshot_path = Column(String(500))
    started_at = Column(DateTime, server_default=func.now(), index=True)
    completed_at = Column(DateTime)
    uploaded_to_onedrive = Column(Boolean, default=False)
    onedrive_path = Column(String(500))
