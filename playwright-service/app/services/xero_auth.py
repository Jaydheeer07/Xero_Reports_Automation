"""
Xero Authentication Service - Handles Xero login and session management.

Provides:
- Manual login flow (headed browser for MFA)
- Session restoration from stored cookies
- Login state verification
- Tenant detection
"""

from typing import Optional
from datetime import datetime
import structlog
import asyncio

from app.services.browser_manager import BrowserManager
from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# Xero URLs
XERO_LOGIN_URL = "https://login.xero.com"
XERO_DASHBOARD_URL = "https://go.xero.com/Dashboard"
XERO_APP_URL = "https://go.xero.com"


class XeroAuthService:
    """
    Handles Xero authentication operations.
    
    Supports:
    - Manual login with headed browser (for MFA)
    - Cookie-based session restoration
    - Login state verification
    """
    
    def __init__(self, browser_manager: BrowserManager):
        self.browser = browser_manager
    
    async def start_manual_login(self) -> dict:
        """
        Start the manual login flow.
        
        Opens a visible browser window for the user to log in manually.
        This is required for initial setup and when MFA is needed.
        
        Returns:
            Dict with status and instructions
        """
        try:
            # Initialize browser in headed mode (visible)
            await self.browser.initialize(headless=False)
            
            # Navigate to Xero login
            await self.browser.goto(XERO_LOGIN_URL, wait_until="networkidle")
            
            logger.info("Manual login started - browser opened to Xero login page")
            
            current_url = await self.browser.get_url()
            
            return {
                "success": True,
                "status": "waiting_for_login",
                "message": "Browser opened. Please log into Xero manually.",
                "instructions": [
                    "1. Enter your Xero email and password",
                    "2. Complete MFA if prompted",
                    "3. Wait for the dashboard to load",
                    "4. Call POST /api/auth/complete to save the session"
                ],
                "current_url": current_url
            }
            
        except Exception as e:
            import traceback
            error_msg = str(e) if str(e) else repr(e)
            logger.error("Failed to start manual login", error=error_msg, traceback=traceback.format_exc())
            return {
                "success": False,
                "status": "error",
                "error": error_msg,
                "details": traceback.format_exc()
            }
    
    async def complete_login(self) -> dict:
        """
        Complete the login flow and save cookies.
        
        Should be called after the user has manually logged in.
        Captures cookies and verifies login was successful.
        
        Returns:
            Dict with cookies and login status
        """
        try:
            if not self.browser.is_initialized:
                return {
                    "success": False,
                    "error": "No browser page available. Call /api/auth/setup first."
                }
            
            current_url = await self.browser.get_url()
            
            # Check if we're on a Xero app page (logged in)
            is_logged_in = await self._check_logged_in()
            
            if not is_logged_in:
                # Take screenshot for debugging
                screenshot = await self.browser.take_screenshot("login_incomplete")
                return {
                    "success": False,
                    "error": "Login not complete. Please finish logging in.",
                    "current_url": current_url,
                    "screenshot": screenshot
                }
            
            # Get cookies
            cookies = await self.browser.get_cookies()
            
            # Get current tenant info
            tenant_info = await self._get_current_tenant()
            
            logger.info(
                "Login completed successfully",
                cookie_count=len(cookies),
                tenant=tenant_info.get("name")
            )
            
            return {
                "success": True,
                "message": "Login successful. Session captured.",
                "cookies": cookies,
                "current_tenant": tenant_info,
                "current_url": current_url
            }
            
        except Exception as e:
            logger.error("Failed to complete login", error=str(e))
            screenshot = await self.browser.take_screenshot("login_error")
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot
            }
    
    async def restore_session(self, cookies: list[dict]) -> dict:
        """
        Restore a session from stored cookies.
        
        Args:
            cookies: List of cookie dicts to restore
            
        Returns:
            Dict with restoration status
        """
        try:
            # Ensure browser is initialized (headless for automation)
            await self.browser.ensure_initialized(headless=True)
            
            # Clear existing cookies
            await self.browser.clear_cookies()
            
            # Set the stored cookies
            await self.browser.set_cookies(cookies)
            
            # Navigate to Xero to verify session
            # Use domcontentloaded instead of networkidle for faster loading
            # Xero is a heavy SPA that may never reach networkidle
            try:
                await self.browser.goto(XERO_APP_URL, wait_until="domcontentloaded")
                # Give extra time for JavaScript to initialize
                await asyncio.sleep(5)
            except Exception as nav_error:
                logger.warning(f"Navigation warning (may be ok): {nav_error}")
            
            # Wait for page to stabilize
            try:
                await self.browser._page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                # networkidle timeout is ok, page may still be functional
                logger.debug("networkidle timeout during session restore, continuing...")
            
            # Check if we're logged in
            is_logged_in = await self._check_logged_in()
            
            if is_logged_in:
                tenant_info = await self._get_current_tenant()
                logger.info("Session restored successfully", tenant=tenant_info.get("name"))
                return {
                    "success": True,
                    "logged_in": True,
                    "current_tenant": tenant_info
                }
            else:
                logger.warning("Session restoration failed - not logged in")
                screenshot = await self.browser.take_screenshot("session_invalid")
                return {
                    "success": False,
                    "logged_in": False,
                    "error": "Session cookies are invalid or expired",
                    "screenshot": screenshot
                }
                
        except Exception as e:
            logger.error("Failed to restore session", error=str(e))
            return {
                "success": False,
                "error": str(e)
            }
    
    async def check_auth_status(self) -> dict:
        """
        Check current authentication status.
        
        Returns:
            Dict with auth status information
        """
        try:
            if not self.browser.is_initialized:
                return {
                    "logged_in": False,
                    "needs_reauth": True,
                    "reason": "Browser not initialized"
                }
            
            # Navigate to Xero if not already there
            current_url = await self.browser.get_url()
            if not current_url.startswith("https://go.xero.com"):
                await self.browser.goto(XERO_APP_URL, wait_until="networkidle")
            
            is_logged_in = await self._check_logged_in()
            
            if is_logged_in:
                tenant_info = await self._get_current_tenant()
                current_url = await self.browser.get_url()
                return {
                    "logged_in": True,
                    "needs_reauth": False,
                    "current_tenant": tenant_info,
                    "current_url": current_url
                }
            else:
                current_url = await self.browser.get_url()
                return {
                    "logged_in": False,
                    "needs_reauth": True,
                    "reason": "Not logged into Xero",
                    "current_url": current_url
                }
                
        except Exception as e:
            logger.error("Failed to check auth status", error=str(e))
            return {
                "logged_in": False,
                "needs_reauth": True,
                "error": str(e)
            }
    
    async def get_available_tenants(self) -> dict:
        """
        Get list of available Xero tenants/organisations.
        
        Returns:
            Dict with list of tenants
        """
        try:
            if not await self._check_logged_in():
                return {
                    "success": False,
                    "error": "Not logged in",
                    "tenants": []
                }
            
            # Click on the organisation switcher
            org_switcher = await self.browser.query_selector('[data-testid="org-switcher"], [class*="org-switcher"], [data-automationid="org-switcher"]')
            
            if not org_switcher:
                # Try alternative selectors
                org_switcher = await self.browser.query_selector('button[aria-label*="organisation"], button[aria-label*="organization"]')
            
            if not org_switcher:
                logger.warning("Could not find organisation switcher")
                return {
                    "success": False,
                    "error": "Could not find organisation switcher",
                    "tenants": []
                }
            
            await self.browser.click('[data-testid="org-switcher"], [class*="org-switcher"], [data-automationid="org-switcher"]')
            await asyncio.sleep(1)  # Wait for dropdown
            
            # Get list of organisations
            # Note: Selectors may need adjustment based on actual Xero UI
            org_items = await self.browser.query_selector_all('[data-testid="org-item"], [class*="org-list"] li, [role="menuitem"]')
            
            tenants = []
            for item in org_items:
                # Items are sync elements, get text in thread pool
                text = item.text_content() if item else None
                if text:
                    tenants.append({"name": text.strip()})
            
            # Close the dropdown by pressing Escape
            await self.browser.press_key("Escape")
            
            logger.info("Retrieved tenants", count=len(tenants))
            
            return {
                "success": True,
                "tenants": tenants
            }
            
        except Exception as e:
            logger.error("Failed to get tenants", error=str(e))
            return {
                "success": False,
                "error": str(e),
                "tenants": []
            }
    
    async def _check_logged_in(self) -> bool:
        """
        Check if currently logged into Xero.
        
        Returns:
            True if logged in
        """
        try:
            if not self.browser.is_initialized:
                return False
            
            current_url = await self.browser.get_url()
            
            # Check URL patterns
            if "login.xero.com" in current_url:
                return False
            
            if "go.xero.com" in current_url:
                # Look for elements that indicate logged-in state
                # Try multiple selectors
                selectors = [
                    '[data-testid="org-switcher"]',
                    '[class*="org-switcher"]',
                    '[data-automationid="navigation"]',
                    'nav[role="navigation"]',
                    '[data-testid="shell-header"]',
                ]
                
                for selector in selectors:
                    element = await self.browser.query_selector(selector)
                    if element:
                        return True
                
                # Check if we're on a valid Xero page
                title = await self.browser.get_title()
                if "Xero" in title and "Login" not in title:
                    return True
            
            return False
            
        except Exception as e:
            logger.error("Error checking login status", error=str(e))
            return False
    
    async def _get_current_tenant(self) -> dict:
        """
        Get information about the current tenant/organisation.
        
        Returns:
            Dict with tenant info
        """
        try:
            if not self.browser.is_initialized:
                return {"name": None, "id": None}
            
            # Try to find the org switcher and get its text
            selectors = [
                '[data-testid="org-switcher"]',
                '[class*="org-switcher"]',
                '[data-automationid="org-switcher"]',
            ]
            
            for selector in selectors:
                text = await self.browser.get_text_content(selector)
                if text:
                    return {"name": text.strip(), "id": None}
            
            return {"name": None, "id": None}
            
        except Exception as e:
            logger.error("Error getting current tenant", error=str(e))
            return {"name": None, "id": None}
