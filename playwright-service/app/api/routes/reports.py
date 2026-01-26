from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import structlog
import os

from app.db.connection import get_db
from app.db.models import DownloadLog, Client
from app.config import get_settings
from app.services.browser_manager import BrowserManager
from app.services.xero_automation import XeroAutomation
from app.services.xero_session import XeroSessionService
from app.services.xero_auth import XeroAuthService
from sqlalchemy import select

router = APIRouter()
logger = structlog.get_logger()
settings = get_settings()


class ReportRequest(BaseModel):
    """Request model for report downloads."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    period: Optional[str] = Field(None, description="Report period")
    find_unfiled: bool = Field(True, description="Find unfiled/draft statements")


class PayrollReportRequest(BaseModel):
    """Request model for payroll report downloads."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    month: Optional[int] = Field(None, ge=1, le=12, description="Month (1-12)")
    year: Optional[int] = Field(None, ge=2020, le=2100, description="Year")


class BatchDownloadRequest(BaseModel):
    """Request model for batch report downloads."""
    tenant_ids: Optional[List[str]] = Field(None, description="List of tenant IDs to process (if None, process all active)")
    reports: List[str] = Field(
        default=["activity_statement", "payroll_summary"],
        description="List of report types to download"
    )


async def _ensure_authenticated(db: AsyncSession) -> tuple[bool, dict]:
    """Ensure browser is authenticated with Xero."""
    browser_manager = await BrowserManager.get_instance()
    session_service = XeroSessionService(db)
    
    # Check if browser is initialized
    if not browser_manager.is_initialized:
        # Try to restore session
        session_data = await session_service.get_session()
        if not session_data:
            return False, {"error": "No session found. Please run /api/auth/setup first."}
        
        auth_service = XeroAuthService(browser_manager)
        restore_result = await auth_service.restore_session(session_data.get("cookies", []))
        
        if not restore_result.get("success"):
            return False, {"error": "Failed to restore session. Please re-authenticate."}
    
    return True, {}


async def _log_download(
    db: AsyncSession,
    client_id: Optional[int],
    report_type: str,
    result: dict
) -> None:
    """Log a download attempt to the database."""
    log = DownloadLog(
        client_id=client_id,
        report_type=report_type,
        status="success" if result.get("success") else "failed",
        file_path=result.get("file_path"),
        file_name=result.get("file_name"),
        error_message=result.get("error"),
        screenshot_path=result.get("screenshot"),
        completed_at=datetime.utcnow() if result.get("success") else None
    )
    db.add(log)
    await db.commit()


@router.post("/activity-statement")
async def download_activity_statement(
    request: ReportRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Download Activity Statement (BAS Report) for a tenant.
    
    This endpoint:
    1. Ensures browser is authenticated
    2. Switches to the specified tenant
    3. Navigates to Activity Statement
    4. Downloads the draft/unfiled statement as Excel
    5. Returns the file path
    """
    logger.info(
        "Activity statement download requested",
        tenant_id=request.tenant_id,
        tenant_name=request.tenant_name
    )
    
    # Ensure authenticated
    is_auth, auth_error = await _ensure_authenticated(db)
    if not is_auth:
        return {"success": False, **auth_error}
    
    # Get browser and automation service
    browser_manager = await BrowserManager.get_instance()
    automation = XeroAutomation(browser_manager)
    
    # Switch tenant and download report
    switch_result = await automation.switch_tenant(request.tenant_name)
    if not switch_result.get("success"):
        await _log_download(db, None, "activity_statement", switch_result)
        return switch_result
    
    # Download the report
    result = await automation.download_activity_statement(
        tenant_name=request.tenant_name,
        find_unfiled=request.find_unfiled
    )
    
    # Log the download
    # Try to find client ID
    client_result = await db.execute(
        select(Client).where(Client.tenant_id == request.tenant_id)
    )
    client = client_result.scalar_one_or_none()
    await _log_download(db, client.id if client else None, "activity_statement", result)
    
    return result


@router.post("/payroll-activity-summary")
async def download_payroll_summary(
    request: PayrollReportRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Download Payroll Activity Summary for a tenant.
    
    This endpoint:
    1. Ensures browser is authenticated
    2. Switches to the specified tenant
    3. Navigates to Payroll Activity Summary
    4. Sets date range to last month (or specified period)
    5. Downloads the report as Excel
    6. Returns the file path
    """
    logger.info(
        "Payroll summary download requested",
        tenant_id=request.tenant_id,
        tenant_name=request.tenant_name,
        month=request.month,
        year=request.year
    )
    
    # Ensure authenticated
    is_auth, auth_error = await _ensure_authenticated(db)
    if not is_auth:
        return {"success": False, **auth_error}
    
    # Get browser and automation service
    browser_manager = await BrowserManager.get_instance()
    automation = XeroAutomation(browser_manager)
    
    # Switch tenant
    switch_result = await automation.switch_tenant(request.tenant_name)
    if not switch_result.get("success"):
        await _log_download(db, None, "payroll_summary", switch_result)
        return switch_result
    
    # Download the report
    result = await automation.download_payroll_activity_summary(
        tenant_name=request.tenant_name,
        month=request.month,
        year=request.year
    )
    
    # Log the download
    client_result = await db.execute(
        select(Client).where(Client.tenant_id == request.tenant_id)
    )
    client = client_result.scalar_one_or_none()
    await _log_download(db, client.id if client else None, "payroll_summary", result)
    
    return result


@router.post("/batch")
async def batch_download(
    request: BatchDownloadRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Download reports for multiple tenants.
    
    If tenant_ids is not specified, processes all active clients in the database.
    """
    logger.info("Batch download requested", reports=request.reports)
    
    # Ensure authenticated
    is_auth, auth_error = await _ensure_authenticated(db)
    if not is_auth:
        return {"success": False, **auth_error}
    
    # Get clients to process
    if request.tenant_ids:
        query = select(Client).where(Client.tenant_id.in_(request.tenant_ids))
    else:
        query = select(Client).where(Client.is_active == True)
    
    result = await db.execute(query)
    clients = result.scalars().all()
    
    if not clients:
        return {
            "success": False,
            "error": "No clients found to process"
        }
    
    # Get automation service
    browser_manager = await BrowserManager.get_instance()
    automation = XeroAutomation(browser_manager)
    
    # Process each client
    results = {
        "total": len(clients),
        "completed": 0,
        "failed": 0,
        "results": []
    }
    
    for client in clients:
        client_result = await automation.download_reports_for_tenant(
            tenant_id=client.tenant_id,
            tenant_name=client.tenant_name,
            reports=request.reports
        )
        
        results["results"].append(client_result)
        
        if client_result.get("success"):
            results["completed"] += 1
        else:
            results["failed"] += 1
        
        # Log each report download
        for report_type, report_result in client_result.get("reports", {}).items():
            await _log_download(db, client.id, report_type, report_result)
    
    results["success"] = results["failed"] == 0
    
    return results


@router.get("/download/{filename}")
async def download_file(filename: str):
    """
    Download a previously generated report file.
    """
    file_path = os.path.join(settings.download_dir, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.get("/files")
async def list_downloaded_files():
    """
    List all downloaded report files.
    """
    from app.services.file_manager import get_file_manager
    
    file_manager = get_file_manager()
    files = file_manager.list_downloads()
    
    return {
        "success": True,
        "count": len(files),
        "files": files
    }


@router.get("/logs")
async def get_download_logs(
    limit: int = 50,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Get download logs.
    """
    query = select(DownloadLog).order_by(DownloadLog.started_at.desc()).limit(limit)
    
    if status:
        query = query.where(DownloadLog.status == status)
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    return {
        "success": True,
        "count": len(logs),
        "logs": [
            {
                "id": log.id,
                "client_id": log.client_id,
                "report_type": log.report_type,
                "status": log.status,
                "file_name": log.file_name,
                "error_message": log.error_message,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            }
            for log in logs
        ]
    }
