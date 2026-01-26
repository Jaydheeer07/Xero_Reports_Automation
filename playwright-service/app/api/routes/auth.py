from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.db.connection import get_db
from app.services.browser_manager import BrowserManager
from app.services.xero_auth import XeroAuthService
from app.services.xero_session import XeroSessionService

router = APIRouter()
logger = structlog.get_logger()


@router.post("/setup")
async def setup_auth(db: AsyncSession = Depends(get_db)):
    """
    Start manual login flow.
    Opens a visible browser for manual Xero authentication.
    
    This endpoint opens a headed (visible) browser window.
    The user must manually log into Xero and complete MFA.
    After login, call POST /api/auth/complete to save the session.
    """
    browser_manager = await BrowserManager.get_instance()
    auth_service = XeroAuthService(browser_manager)
    
    result = await auth_service.start_manual_login()
    return result


@router.post("/complete")
async def complete_auth(db: AsyncSession = Depends(get_db)):
    """
    Complete authentication and save session.
    Called after manual login to capture and store cookies.
    
    Captures cookies from the browser and stores them encrypted in the database.
    """
    browser_manager = await BrowserManager.get_instance()
    auth_service = XeroAuthService(browser_manager)
    session_service = XeroSessionService(db)
    
    # Complete the login and get cookies
    result = await auth_service.complete_login()
    
    if not result.get("success"):
        return result
    
    # Save cookies to database
    cookies = result.get("cookies", [])
    saved = await session_service.save_session(cookies)
    
    if not saved:
        return {
            "success": False,
            "error": "Failed to save session to database"
        }
    
    # Switch browser to headless mode for automation
    await browser_manager.restart(headless=True)
    
    # Restore session in headless browser
    restore_result = await auth_service.restore_session(cookies)
    
    return {
        "success": True,
        "message": "Session saved and browser switched to headless mode",
        "current_tenant": result.get("current_tenant"),
        "session_restored": restore_result.get("success", False)
    }


@router.get("/status")
async def auth_status(db: AsyncSession = Depends(get_db)):
    """
    Check if current session is valid.
    
    Returns the current authentication status including:
    - Whether logged into Xero
    - Current tenant/organisation
    - Whether re-authentication is needed
    """
    browser_manager = await BrowserManager.get_instance()
    session_service = XeroSessionService(db)
    
    # First check stored session status
    session_status = await session_service.get_session_status()
    
    # If no session or expired, need reauth
    if not session_status.get("has_session") or not session_status.get("is_valid"):
        return {
            "logged_in": False,
            "current_tenant": None,
            "needs_reauth": True,
            "session_status": session_status,
            "message": "No valid session found. Please run /api/auth/setup"
        }
    
    # Check if browser has active session
    if browser_manager.is_initialized:
        auth_service = XeroAuthService(browser_manager)
        browser_status = await auth_service.check_auth_status()
        
        return {
            "logged_in": browser_status.get("logged_in", False),
            "current_tenant": browser_status.get("current_tenant"),
            "needs_reauth": browser_status.get("needs_reauth", True),
            "session_status": session_status
        }
    
    # Browser not initialized - session exists but not loaded
    return {
        "logged_in": False,
        "current_tenant": None,
        "needs_reauth": False,
        "session_status": session_status,
        "message": "Session exists but browser not initialized. Call /api/auth/restore to load session."
    }


@router.post("/restore")
async def restore_session(db: AsyncSession = Depends(get_db)):
    """
    Restore session from stored cookies.
    
    Loads the stored session cookies into the browser and verifies login.
    """
    browser_manager = await BrowserManager.get_instance()
    session_service = XeroSessionService(db)
    auth_service = XeroAuthService(browser_manager)
    
    # Get stored session
    session_data = await session_service.get_session()
    
    if not session_data:
        return {
            "success": False,
            "error": "No stored session found. Please run /api/auth/setup first."
        }
    
    cookies = session_data.get("cookies", [])
    
    if not cookies:
        return {
            "success": False,
            "error": "Stored session has no cookies."
        }
    
    # Restore session
    result = await auth_service.restore_session(cookies)
    
    if result.get("success"):
        # Update session timestamp
        await session_service.save_session(cookies)
    
    return result


@router.get("/tenants")
async def list_tenants(db: AsyncSession = Depends(get_db)):
    """
    List available Xero tenants/organisations.
    
    Returns a list of all organisations the logged-in user has access to.
    """
    browser_manager = await BrowserManager.get_instance()
    
    if not browser_manager.is_initialized:
        return {
            "success": False,
            "tenants": [],
            "error": "Browser not initialized. Please restore session first."
        }
    
    auth_service = XeroAuthService(browser_manager)
    result = await auth_service.get_available_tenants()
    
    return result


@router.post("/switch-tenant")
async def switch_tenant(tenant_name: str, db: AsyncSession = Depends(get_db)):
    """
    Switch to a specified Xero tenant/organisation.
    """
    browser_manager = await BrowserManager.get_instance()
    
    if not browser_manager.is_initialized:
        # Try to restore session first
        session_service = XeroSessionService(db)
        session_data = await session_service.get_session()
        
        if not session_data:
            return {
                "success": False,
                "error": "No session found. Please run /api/auth/setup first."
            }
        
        auth_service = XeroAuthService(browser_manager)
        restore_result = await auth_service.restore_session(session_data.get("cookies", []))
        
        if not restore_result.get("success"):
            return {
                "success": False,
                "error": "Failed to restore session. Please re-authenticate."
            }
    
    # Use automation service to switch tenant
    from app.services.xero_automation import XeroAutomation
    
    automation = XeroAutomation(browser_manager)
    result = await automation.switch_tenant(tenant_name)
    
    return result


@router.delete("/session")
async def delete_session(db: AsyncSession = Depends(get_db)):
    """
    Delete the stored session.
    
    Clears the stored cookies from the database.
    """
    session_service = XeroSessionService(db)
    
    deleted = await session_service.delete_session()
    
    if deleted:
        return {
            "success": True,
            "message": "Session deleted"
        }
    else:
        return {
            "success": False,
            "error": "Failed to delete session"
        }
