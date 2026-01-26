"""Pydantic request models for API endpoints."""

from pydantic import BaseModel, Field
from typing import Optional


class ReportRequest(BaseModel):
    """Request model for report downloads."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    period: Optional[str] = Field(None, description="Report period (e.g., 'Q4_2025')")
    find_unfiled: bool = Field(True, description="Find unfiled/draft statements")


class PayrollReportRequest(BaseModel):
    """Request model for payroll report downloads."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    month: Optional[int] = Field(None, ge=1, le=12, description="Month (1-12)")
    year: Optional[int] = Field(None, ge=2020, le=2100, description="Year")


class BatchDownloadRequest(BaseModel):
    """Request model for batch report downloads."""
    tenants: list[dict] = Field(..., description="List of tenant objects with tenant_id and tenant_name")
    reports: list[str] = Field(
        default=["activity_statement", "payroll_summary"],
        description="List of report types to download"
    )
    month: Optional[int] = Field(None, description="Month for payroll reports")
    year: Optional[int] = Field(None, description="Year for payroll reports")


class ClientCreate(BaseModel):
    """Request model for creating a client."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    onedrive_folder: Optional[str] = Field(None, description="OneDrive folder path")
    is_active: bool = Field(True, description="Whether client is active")


class ClientUpdate(BaseModel):
    """Request model for updating a client."""
    tenant_name: Optional[str] = None
    onedrive_folder: Optional[str] = None
    is_active: Optional[bool] = None
