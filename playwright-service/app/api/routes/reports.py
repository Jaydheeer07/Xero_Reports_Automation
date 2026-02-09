from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
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
from app.api.dependencies import verify_api_key
from app.models import (
    ReportRequest,
    PayrollReportRequest,
    ConsolidatedReportRequest,
    BatchDownloadRequest,
)
from sqlalchemy import select

router = APIRouter()
logger = structlog.get_logger()
settings = get_settings()


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
    api_key: str = Depends(verify_api_key),
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
    
    # Skip tenant switching - the session is already authenticated to the correct tenant
    # Tenant switching is unreliable due to dynamic page titles and org_switcher detection issues
    # TODO: Implement reliable tenant switching in the future
    logger.info(f"Proceeding with download for tenant: {request.tenant_name} (tenant switching disabled)")
    
    # Download the report - period is required, no hardcoded fallback
    if not request.period:
        return {
            "success": False,
            "error": "Period is required (e.g., 'October 2025')"
        }
    
    result = await automation.download_activity_statement(
        tenant_name=request.tenant_name,
        find_unfiled=request.find_unfiled,
        period=request.period
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
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Download Payroll Activity Summary for a tenant.
    
    This endpoint:
    1. Ensures browser is authenticated
    2. Navigates to Reporting > All Reports > Payroll Activity Summary
    3. Sets date range to specified month/year (or defaults to last month)
    4. Downloads the report as Excel
    5. Returns the file path
    
    The date range is automatically calculated based on the month/year:
    - Start date: 1st of the month (e.g., "1 October 2025")
    - End date: Last day of the month (e.g., "31 October 2025")
    
    The system handles months with different day counts (28, 29, 30, 31) correctly.
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
    
    # Skip tenant switching - the session is already authenticated to the correct tenant
    # Tenant switching is unreliable due to dynamic page titles and org_switcher detection issues
    # TODO: Implement reliable tenant switching in the future
    logger.info(f"Proceeding with download for tenant: {request.tenant_name} (tenant switching disabled)")
    
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
    await _log_download(db, client.id if client else None, "payroll_activity_summary", result)
    
    return result


@router.post("/consolidated")
async def download_consolidated_report(
    request: ConsolidatedReportRequest,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Download both Activity Statement and Payroll Activity Summary, then consolidate into a single Excel file.
    
    Workflow:
    1. Switches to the specified tenant (if shortcode provided)
    2. Downloads Activity Statement for the specified period
    3. Downloads Payroll Activity Summary for the specified month/year
    4. Consolidates both reports into a single Excel file with multiple sheets
    5. Returns the consolidated file path
    
    This is the main endpoint for n8n integration.
    """
    import calendar
    from app.services.file_manager import get_file_manager
    
    period = request.period or f"{calendar.month_name[request.month]} {request.year}"
    
    logger.info(
        "Consolidated report download requested",
        tenant_id=request.tenant_id,
        tenant_name=request.tenant_name,
        tenant_shortcode=request.tenant_shortcode,
        month=request.month,
        year=request.year,
        period=period
    )
    
    is_auth, auth_error = await _ensure_authenticated(db)
    if not is_auth:
        return {"success": False, **auth_error}
    
    browser_manager = await BrowserManager.get_instance()
    automation = XeroAutomation(browser_manager)
    file_manager = get_file_manager()
    
    # Step 0: Switch tenant if shortcode provided
    if request.tenant_shortcode:
        logger.info(f"Switching to tenant: {request.tenant_name} (shortcode: {request.tenant_shortcode})")
        switch_result = await automation.switch_tenant(request.tenant_name, request.tenant_shortcode)
        if not switch_result.get("success"):
            return {
                "success": False,
                "error": f"Failed to switch tenant: {switch_result.get('error')}",
                "screenshot": switch_result.get("screenshot")
            }
    
    results = {
        "success": False,
        "tenant_name": request.tenant_name,
        "period": period,
        "activity_statement": None,
        "payroll_summary": None,
        "consolidated_file": None,
        "errors": []
    }
    
    downloaded_files = []
    sheet_names = []
    
    # Step 1: Download Activity Statement
    logger.info(f"Step 1/3: Downloading Activity Statement... tenant_shortcode={request.tenant_shortcode}")
    activity_result = await automation.download_activity_statement(
        tenant_name=request.tenant_name,
        find_unfiled=request.find_unfiled,
        period=period,
        tenant_shortcode=request.tenant_shortcode,
        month=request.month,
        year=request.year
    )
    
    results["activity_statement"] = activity_result
    
    if activity_result.get("success"):
        downloaded_files.append(activity_result["file_path"])
        sheet_names.append("Activity_Statement")
        logger.info("Activity Statement downloaded successfully")
    else:
        results["errors"].append(f"Activity Statement failed: {activity_result.get('error')}")
        logger.error("Activity Statement download failed", error=activity_result.get("error"))
    
    client_result = await db.execute(
        select(Client).where(Client.tenant_id == request.tenant_id)
    )
    client = client_result.scalar_one_or_none()
    await _log_download(db, client.id if client else None, "activity_statement", activity_result)
    
    # Step 2: Download Payroll Activity Summary
    logger.info("Step 2/3: Downloading Payroll Activity Summary...")
    payroll_result = await automation.download_payroll_activity_summary(
        tenant_name=request.tenant_name,
        month=request.month,
        year=request.year,
        tenant_shortcode=request.tenant_shortcode
    )
    
    results["payroll_summary"] = payroll_result
    
    if payroll_result.get("success"):
        downloaded_files.append(payroll_result["file_path"])
        sheet_names.append("Payroll_Summary")
        logger.info("Payroll Activity Summary downloaded successfully")
    else:
        results["errors"].append(f"Payroll Summary failed: {payroll_result.get('error')}")
        logger.error("Payroll Summary download failed", error=payroll_result.get("error"))
    
    await _log_download(db, client.id if client else None, "payroll_activity_summary", payroll_result)
    
    # Step 3: Consolidate files
    if downloaded_files:
        logger.info("Step 3/3: Consolidating reports...")
        try:
            # Format: Consolidated_BAS_Report_{Month_Year}.xlsx
            month_year = f"{calendar.month_name[request.month]}_{request.year}"
            consolidated_filename = f"Consolidated_BAS_Report_{month_year}.xlsx"
            
            consolidated_path = file_manager.consolidate_excel_files(
                file_paths=downloaded_files,
                output_filename=consolidated_filename,
                sheet_names=sheet_names
            )
            
            results["consolidated_file"] = {
                "file_path": consolidated_path,
                "file_name": consolidated_filename,
                "sheets_count": len(downloaded_files)
            }
            
            results["success"] = True
            logger.info("Consolidation complete", file=consolidated_filename)
            
        except Exception as e:
            results["errors"].append(f"Consolidation failed: {str(e)}")
            logger.error("Consolidation failed", error=str(e))
            results["success"] = len(downloaded_files) > 0
    else:
        results["errors"].append("No files were downloaded successfully")
    
    consolidated_log_result = {
        "success": results["success"],
        "file_path": results["consolidated_file"]["file_path"] if results["consolidated_file"] else None,
        "file_name": results["consolidated_file"]["file_name"] if results["consolidated_file"] else None,
        "error": "; ".join(results["errors"]) if results["errors"] else None
    }
    await _log_download(db, client.id if client else None, "consolidated_report", consolidated_log_result)
    
    return results


@router.post("/batch")
async def batch_download(
    request: BatchDownloadRequest,
    api_key: str = Depends(verify_api_key),
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
async def download_file(filename: str, api_key: str = Depends(verify_api_key)):
    """
    Download a previously generated report file.
    """
    # Sanitize filename to prevent path traversal attacks
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(settings.download_dir, safe_filename)
    
    # Verify path is within download directory
    if not os.path.abspath(file_path).startswith(os.path.abspath(settings.download_dir)):
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.get("/files")
async def list_downloaded_files(api_key: str = Depends(verify_api_key)):
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
    api_key: str = Depends(verify_api_key),
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
