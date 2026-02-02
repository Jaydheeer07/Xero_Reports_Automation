"""Pydantic request models for API endpoints."""

from pydantic import BaseModel, Field
from typing import Optional, List


# =============================================================================
# Auth Models
# =============================================================================

class SwitchTenantRequest(BaseModel):
    """Request model for tenant switching."""
    tenant_name: str = Field(..., description="Name of the Xero tenant/organisation to switch to")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode for URL-based switching (e.g., 'mkK34'). If provided, uses faster URL method.")


# =============================================================================
# Report Models
# =============================================================================

class ReportRequest(BaseModel):
    """Request model for report downloads."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode for URL-based switching")
    period: str = Field(..., description="Report period (e.g., 'October 2025') - REQUIRED")
    find_unfiled: bool = Field(True, description="Find unfiled/draft statements")


class PayrollReportRequest(BaseModel):
    """Request model for payroll report downloads."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode for URL-based switching")
    month: Optional[int] = Field(None, ge=1, le=12, description="Month (1-12)")
    year: Optional[int] = Field(None, ge=2020, le=2100, description="Year")


class ConsolidatedReportRequest(BaseModel):
    """Request model for consolidated report download (Activity Statement + Payroll Summary)."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode for URL-based switching")
    month: int = Field(..., ge=1, le=12, description="Month (1-12)")
    year: int = Field(..., ge=2020, le=2100, description="Year")
    period: Optional[str] = Field(None, description="Activity Statement period (e.g., 'October 2025'). If not provided, derived from month/year")
    find_unfiled: bool = Field(False, description="Find unfiled/draft activity statements")


class BatchDownloadRequest(BaseModel):
    """Request model for batch report downloads."""
    tenant_ids: Optional[List[str]] = Field(None, description="List of tenant IDs to process (if None, process all active)")
    reports: List[str] = Field(
        default=["activity_statement", "payroll_summary"],
        description="List of report types to download"
    )


# =============================================================================
# Client Models
# =============================================================================

class ClientCreate(BaseModel):
    """Request model for creating a client."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode for URL-based switching (e.g., 'mkK34')")
    onedrive_folder: Optional[str] = Field(None, description="OneDrive folder path")
    is_active: bool = Field(True, description="Whether client is active")


class ClientUpdate(BaseModel):
    """Request model for updating a client."""
    tenant_name: Optional[str] = None
    tenant_shortcode: Optional[str] = None
    onedrive_folder: Optional[str] = None
    is_active: Optional[bool] = None
