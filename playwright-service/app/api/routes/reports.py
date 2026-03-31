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

import uuid
from datetime import datetime, timedelta


def _get_australian_fy_year() -> int:
    """Return the current Australian fiscal year (e.g. 2026 for July 2025 – June 2026)."""
    now = datetime.now()
    return now.year + 1 if now.month >= 7 else now.year


# --- In-memory background job registry ---
# Stores job state during execution. Jobs auto-expire after 1 hour.
# Supabase download_logs is the permanent record; this dict is only for polling.
_jobs: dict[str, dict] = {}
_JOB_TTL_HOURS = 1


def _create_job() -> str:
    """Create a new job entry and return its job_id. Also purges expired jobs."""
    # Lazy cleanup: remove jobs older than TTL
    cutoff = datetime.utcnow() - timedelta(hours=_JOB_TTL_HOURS)
    expired = [jid for jid, j in _jobs.items() if j.get("created_at", datetime.utcnow()) < cutoff]
    for jid in expired:
        del _jobs[jid]

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "running",
        "message": "Starting...",
        "steps": [],
        "result": None,
        "created_at": datetime.utcnow(),
    }
    return job_id


def _update_job(job_id: str, message: str) -> None:
    """Append a step message to the job and update current message."""
    if job_id in _jobs:
        _jobs[job_id]["message"] = message
        _jobs[job_id]["steps"].append(message)


def _finish_job(job_id: str, success: bool, result: dict) -> None:
    """Mark a job as complete."""
    if job_id in _jobs:
        _jobs[job_id]["status"] = "success" if success else "failed"
        _jobs[job_id]["message"] = "Complete" if success else result.get("error", "Failed")
        _jobs[job_id]["result"] = result


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

    Uses request_lock to prevent concurrent browser access.
    """
    logger.info(
        "Activity statement download requested",
        tenant_id=request.tenant_id,
        tenant_name=request.tenant_name
    )

    # Download the report - period is required, no hardcoded fallback
    if not request.period:
        return {
            "success": False,
            "error": "Period is required (e.g., 'October 2025')"
        }

    browser_manager = await BrowserManager.get_instance()

    # Acquire browser lock to prevent concurrent access
    async with browser_manager.request_lock:
        # Ensure authenticated
        is_auth, auth_error = await _ensure_authenticated(db)
        if not is_auth:
            return {"success": False, **auth_error}

        automation = XeroAutomation(browser_manager)

        logger.info(f"Proceeding with download for tenant: {request.tenant_name}")

        result = await automation.download_activity_statement(
            tenant_name=request.tenant_name,
            find_unfiled=request.find_unfiled,
            period=request.period
        )

    # Log the download (outside lock - doesn't need browser)
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

    Uses request_lock to prevent concurrent browser access.
    """
    logger.info(
        "Payroll summary download requested",
        tenant_id=request.tenant_id,
        tenant_name=request.tenant_name,
        month=request.month,
        year=request.year
    )

    browser_manager = await BrowserManager.get_instance()

    # Acquire browser lock to prevent concurrent access
    async with browser_manager.request_lock:
        # Ensure authenticated
        is_auth, auth_error = await _ensure_authenticated(db)
        if not is_auth:
            return {"success": False, **auth_error}

        automation = XeroAutomation(browser_manager)

        logger.info(f"Proceeding with download for tenant: {request.tenant_name}")

        # Download the report
        result = await automation.download_payroll_activity_summary(
            tenant_name=request.tenant_name,
            month=request.month,
            year=request.year
        )

    # Log the download (outside lock)
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
    
    browser_manager = await BrowserManager.get_instance()

    # Acquire browser lock for entire consolidated download operation
    async with browser_manager.request_lock:
        is_auth, auth_error = await _ensure_authenticated(db)
        if not is_auth:
            return {"success": False, **auth_error}

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

    # Outside browser lock: consolidation and logging don't need browser
    client_result = await db.execute(
        select(Client).where(Client.tenant_id == request.tenant_id)
    )
    client = client_result.scalar_one_or_none()
    await _log_download(db, client.id if client else None, "activity_statement", activity_result)
    await _log_download(db, client.id if client else None, "payroll_activity_summary", payroll_result)

    # Step 3: Consolidate files
    if downloaded_files:
        logger.info("Step 3/3: Consolidating reports...")
        try:
            import re
            safe_name = re.sub(r'[<>:"/\\|?*]', '', request.tenant_name)
            consolidated_filename = f"{safe_name} - {calendar.month_name[request.month]} {request.year} IAS.xlsx"

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
    Each client is processed sequentially (browser can only handle one at a time).

    Uses request_lock to prevent concurrent browser access.
    """
    logger.info("Batch download requested", reports=request.reports)

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

    browser_manager = await BrowserManager.get_instance()

    # Process each client sequentially with browser lock
    results = {
        "total": len(clients),
        "completed": 0,
        "failed": 0,
        "results": []
    }

    async with browser_manager.request_lock:
        # Ensure authenticated
        is_auth, auth_error = await _ensure_authenticated(db)
        if not is_auth:
            return {"success": False, **auth_error}

        automation = XeroAutomation(browser_manager)

        for client in clients:
            client_result = {
                "tenant_name": client.tenant_name,
                "tenant_id": client.tenant_id,
                "success": False,
                "reports": {},
                "errors": []
            }

            try:
                # Switch to tenant if shortcode is available
                if client.tenant_shortcode:
                    switch_result = await automation.switch_tenant(
                        client.tenant_name,
                        client.tenant_shortcode
                    )
                    if not switch_result.get("success"):
                        client_result["errors"].append(f"Failed to switch tenant: {switch_result.get('error')}")
                        results["failed"] += 1
                        results["results"].append(client_result)
                        continue

                # Download requested reports
                for report_type in request.reports:
                    try:
                        if report_type == "activity_statement":
                            report_result = await automation.download_activity_statement(
                                tenant_name=client.tenant_name,
                                find_unfiled=True,
                                period=request.period or "",
                                tenant_shortcode=client.tenant_shortcode
                            )
                        elif report_type == "payroll_activity_summary":
                            report_result = await automation.download_payroll_activity_summary(
                                tenant_name=client.tenant_name,
                                month=request.month,
                                year=request.year,
                                tenant_shortcode=client.tenant_shortcode
                            )
                        else:
                            report_result = {"success": False, "error": f"Unknown report type: {report_type}"}

                        client_result["reports"][report_type] = report_result
                        await _log_download(db, client.id, report_type, report_result)

                    except Exception as e:
                        error_result = {"success": False, "error": str(e)}
                        client_result["reports"][report_type] = error_result
                        client_result["errors"].append(f"{report_type}: {str(e)}")
                        await _log_download(db, client.id, report_type, error_result)

                # Check if all reports succeeded
                all_success = all(
                    r.get("success", False)
                    for r in client_result["reports"].values()
                )
                client_result["success"] = all_success

                if all_success:
                    results["completed"] += 1
                else:
                    results["failed"] += 1

            except Exception as e:
                client_result["errors"].append(str(e))
                results["failed"] += 1

            results["results"].append(client_result)

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


import asyncio as _asyncio
from app.db.connection import async_session_maker as AsyncSessionLocal


@router.post("/run")
async def run_report(
    request: ConsolidatedReportRequest,
    api_key: str = Depends(verify_api_key),
):
    """
    Start a consolidated report job in the background.
    Returns immediately with a job_id. Poll GET /api/reports/job/{job_id} for status.
    """
    import calendar

    job_id = _create_job()
    period = request.period or f"{calendar.month_name[request.month]} {request.year}"

    _update_job(job_id, f"Queued: {request.tenant_name} — {period}")

    # Kick off in the background (asyncio task, not BackgroundTasks,
    # so it can create its own db session independently of the request lifecycle)
    _asyncio.create_task(_run_consolidated_job(job_id, request))

    return {"job_id": job_id, "tenant_name": request.tenant_name, "period": period}


async def _run_consolidated_job(job_id: str, request: ConsolidatedReportRequest) -> None:
    """Background coroutine that runs the full consolidated report workflow."""
    import calendar
    from app.services.file_manager import get_file_manager

    period = request.period or f"{calendar.month_name[request.month]} {request.year}"

    # Create a fresh DB session for the background task
    async with AsyncSessionLocal() as db:
        try:
            browser_manager = await BrowserManager.get_instance()

            async with browser_manager.request_lock:
                # Step 1: Auth
                _update_job(job_id, "Ensuring authenticated with Xero...")
                is_auth, auth_error = await _ensure_authenticated(db)
                if not is_auth:
                    _finish_job(job_id, False, auth_error)
                    return

                automation = XeroAutomation(browser_manager)

                # Step 2: Switch tenant
                if request.tenant_shortcode:
                    _update_job(job_id, f"Switching to {request.tenant_name}...")
                    switch_result = await automation.switch_tenant(
                        request.tenant_name, request.tenant_shortcode
                    )
                    if not switch_result.get("success"):
                        _finish_job(job_id, False, {"error": f"Failed to switch tenant: {switch_result.get('error')}"})
                        return

                # Step 3: Activity Statement
                _update_job(job_id, "Downloading Activity Statement...")
                activity_result = await automation.download_activity_statement(
                    tenant_name=request.tenant_name,
                    find_unfiled=request.find_unfiled,
                    period=period,
                    tenant_shortcode=request.tenant_shortcode,
                    month=request.month,
                    year=request.year,
                )

                # Step 4: Payroll Summary
                _update_job(job_id, "Downloading Payroll Activity Summary...")
                payroll_result = await automation.download_payroll_activity_summary(
                    tenant_name=request.tenant_name,
                    month=request.month,
                    year=request.year,
                    tenant_shortcode=request.tenant_shortcode,
                )

            # Step 5: Consolidate (outside browser lock)
            downloaded_files = []
            sheet_names = []
            errors = []

            if activity_result.get("success"):
                downloaded_files.append(activity_result["file_path"])
                sheet_names.append("Activity_Statement")
            else:
                errors.append(f"Activity Statement: {activity_result.get('error')}")

            if payroll_result.get("success"):
                downloaded_files.append(payroll_result["file_path"])
                sheet_names.append("Payroll_Activity_Summary")
            else:
                errors.append(f"Payroll Summary: {payroll_result.get('error')}")

            consolidated_file = None
            file_manager = None
            if downloaded_files:
                _update_job(job_id, "Consolidating reports into single Excel file...")
                file_manager = get_file_manager()
                import re
                safe_name = re.sub(r'[<>:"/\\|?*]', '', request.tenant_name)
                consolidated_filename = f"{safe_name} - {calendar.month_name[request.month]} {request.year} IAS.xlsx"
                consolidated_path = file_manager.consolidate_excel_files(
                    file_paths=downloaded_files,
                    output_filename=consolidated_filename,
                    sheet_names=sheet_names,
                )
                consolidated_file = {"file_path": consolidated_path, "file_name": consolidated_filename}

            # Look up client for OneDrive folder and logging
            from sqlalchemy import select as _select
            client_result = await db.execute(_select(Client).where(Client.tenant_id == request.tenant_id))
            client = client_result.scalar_one_or_none()

            # Step 6: Copy to OneDrive (if configured)
            onedrive_path = None
            if consolidated_file and settings.one_drive_folder_origin and client and client.onedrive_folder:
                try:
                    _update_job(job_id, "Copying to OneDrive...")
                    if not file_manager:
                        file_manager = get_file_manager()
                    fy_year = _get_australian_fy_year()
                    onedrive_folder_with_fy = os.path.join(client.onedrive_folder, f"FY {fy_year}")
                    onedrive_path = file_manager.copy_to_onedrive(
                        source_path=consolidated_file["file_path"],
                        onedrive_origin=settings.one_drive_folder_origin,
                        client_onedrive_folder=onedrive_folder_with_fy,
                    )
                    consolidated_file["onedrive_path"] = onedrive_path
                    _update_job(job_id, f"Saved to OneDrive: {os.path.basename(onedrive_path)}")
                except Exception as e:
                    logger.warning("OneDrive copy failed (non-fatal)", error=str(e))
                    errors.append(f"OneDrive copy failed: {str(e)}")
            elif consolidated_file:
                if not settings.one_drive_folder_origin:
                    logger.info("OneDrive skipped: ONE_DRIVE_FOLDER_ORIGIN not configured")
                elif not client or not client.onedrive_folder:
                    logger.info("OneDrive skipped: client has no onedrive_folder set",
                                client_name=request.tenant_name)

            # Step 7: Cleanup individual files (only if OneDrive copy succeeded)
            if onedrive_path:
                _update_job(job_id, "Cleaning up temporary files...")
                if not file_manager:
                    file_manager = get_file_manager()
                files_to_delete = []
                if activity_result.get("success") and activity_result.get("file_path"):
                    files_to_delete.append(activity_result["file_path"])
                if payroll_result.get("success") and payroll_result.get("file_path"):
                    files_to_delete.append(payroll_result["file_path"])
                if consolidated_file and consolidated_file.get("file_path"):
                    files_to_delete.append(consolidated_file["file_path"])
                if files_to_delete:
                    cleanup_result = file_manager.cleanup_job_files(files_to_delete)
                    logger.info("Cleanup complete",
                                deleted=cleanup_result["deleted"],
                                errors=cleanup_result["errors"])
                    if cleanup_result["errors"]:
                        errors.extend([f"Cleanup: {e}" for e in cleanup_result["errors"]])

            # Step 8: Update Asana task (if client has a task ID configured)
            asana_updated = False
            asana_error = None
            if onedrive_path and client and client.asana_task_id and settings.asana_api_key:
                _update_job(job_id, "Updating Asana task...")
                from app.services.asana_service import get_asana_service
                asana_service = get_asana_service()
                consolidated_filename = consolidated_file["file_name"] if consolidated_file else None
                asana_link = (
                    file_manager.build_sharepoint_url(
                        onedrive_folder=client.onedrive_folder,
                        fy_year=fy_year,
                        local_prefix=settings.onedrive_local_prefix,
                        sharepoint_base_url=settings.sharepoint_base_url,
                        filename=consolidated_filename,
                    )
                    or onedrive_path
                )
                asana_result = await asana_service.update_task_after_export(
                    task_id_or_url=client.asana_task_id,
                    onedrive_link=asana_link,
                    filename=consolidated_filename,
                )
                asana_updated = asana_result["success"]
                asana_error = asana_result.get("error")
                if not asana_updated:
                    logger.warning("Asana update failed, sending fallback email", error=asana_error)
                    await asana_service.send_fallback_email(onedrive_path, asana_error or "Unknown error")
                    errors.append(f"Asana update failed: {asana_error}")
                else:
                    _update_job(job_id, "Asana task updated")
            elif client and client.asana_task_id and not settings.asana_api_key:
                logger.info("Asana update skipped: ASANA_API_KEY not configured")

            # Log to Supabase
            await _log_download(db, client.id if client else None, "activity_statement", activity_result)
            await _log_download(db, client.id if client else None, "payroll_activity_summary", payroll_result)

            success = len(downloaded_files) > 0
            result = {
                "consolidated_file": consolidated_file,
                "errors": errors,
                "activity_statement": activity_result,
                "payroll_summary": payroll_result,
                "asana_updated": asana_updated,
                "asana_error": asana_error,
            }
            if errors:
                result["error"] = "; ".join(errors)

            _finish_job(job_id, success, result)
            _update_job(job_id, f"Done — {consolidated_file['file_name']}" if consolidated_file else "Done with errors")

            # Update consolidated download log with OneDrive info
            if onedrive_path:
                consolidated_log_result = {
                    "success": True,
                    "file_path": onedrive_path,
                    "file_name": consolidated_file["file_name"] if consolidated_file else None,
                }
                consolidated_log = await db.execute(
                    _select(DownloadLog)
                    .where(DownloadLog.client_id == (client.id if client else None))
                    .where(DownloadLog.report_type == "consolidated_report")
                    .order_by(DownloadLog.started_at.desc())
                    .limit(1)
                )
                log_entry = consolidated_log.scalar_one_or_none()
                if log_entry:
                    log_entry.uploaded_to_onedrive = True
                    log_entry.onedrive_path = onedrive_path
                    await db.commit()

        except Exception as e:
            logger.error("Background job failed", job_id=job_id, error=str(e))
            _finish_job(job_id, False, {"error": str(e)})


@router.get("/job/{job_id}")
async def get_job_status(job_id: str, api_key: str = Depends(verify_api_key)):
    """
    Poll the status of a background report job.
    Returns 404 if the job has expired (> 1 hour old) or never existed.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return {
        "job_id": job_id,
        "status": job["status"],
        "message": job["message"],
        "steps": job["steps"],
        "result": job["result"],
    }


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
