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

# Security question mapping - maps question text to settings attribute
SECURITY_QUESTIONS = {
    "As a child, what did you want to be when you grew up": "xero_security_answer_1",
    "What is your most disliked holiday": "xero_security_answer_2",
    "What is your dream job": "xero_security_answer_3",
}


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
        
        Extracts:
        - name: From the page title or org switcher element
        - id: The shortcode from the URL (e.g., "!23kBq" from /app/!23kBq/homepage)
        
        Returns:
            Dict with tenant info {"name": str, "id": str}
        """
        try:
            if not self.browser.is_initialized:
                return {"name": None, "id": None}
            
            tenant_name = None
            tenant_id = None
            
            page = self.browser.page
            
            # Extract tenant ID from URL shortcode
            # URL format: https://go.xero.com/app/!23kBq/homepage
            current_url = await self.browser.get_url()
            if "/app/" in current_url:
                import re
                match = re.search(r'/app/([^/]+)/', current_url)
                if match:
                    tenant_id = match.group(1)
                    logger.debug(f"Extracted tenant ID from URL: {tenant_id}")
            
            # Strategy 1: Get tenant name from page title
            # Title format: "Homepage – Tenant Name – Xero"
            try:
                title = await self.browser.get_title()
                if title and "Xero" in title:
                    for separator in [" – ", " - ", "–", "-"]:
                        if separator in title:
                            parts = title.split(separator)
                            if len(parts) >= 3:
                                tenant_name = parts[-2].strip()
                                if tenant_name and tenant_name != "Xero":
                                    logger.debug(f"Got tenant name from title: {tenant_name}")
                                    break
                            elif len(parts) == 2:
                                tenant_name = parts[0].strip()
                                if tenant_name and tenant_name != "Xero":
                                    break
                            break
            except Exception as e:
                logger.debug(f"Failed to get tenant from title: {e}")
            
            # Strategy 2: Try org switcher element
            if not tenant_name:
                selectors = [
                    '[data-testid="org-switcher"]',
                    '[class*="org-switcher"]',
                    '[data-automationid="org-switcher"]',
                ]
                for selector in selectors:
                    text = await self.browser.get_text_content(selector)
                    if text:
                        tenant_name = text.strip()
                        logger.debug(f"Got tenant name from org-switcher: {tenant_name}")
                        break
            
            # Strategy 3: Look for company name text on the homepage
            # This searches for visible text that looks like a company name
            if not tenant_name:
                try:
                    # Try to find heading elements that might contain the company name
                    heading_selectors = [
                        'h1', 'h2',
                        '[class*="company-name"]',
                        '[class*="organisation-name"]',
                        '[class*="org-name"]',
                    ]
                    for selector in heading_selectors:
                        elements = page.locator(selector)
                        count = await elements.count()
                        for i in range(min(count, 3)):  # Check first 3 elements
                            text = await elements.nth(i).text_content()
                            if text and len(text.strip()) > 2 and len(text.strip()) < 100:
                                # Skip common non-company texts
                                skip_texts = ['dashboard', 'homepage', 'xero', 'welcome', 'hello']
                                if not any(skip in text.lower() for skip in skip_texts):
                                    tenant_name = text.strip()
                                    logger.debug(f"Got tenant name from heading: {tenant_name}")
                                    break
                        if tenant_name:
                            break
                except Exception as e:
                    logger.debug(f"Failed to get tenant from headings: {e}")
            
            return {"name": tenant_name, "id": tenant_id}
            
        except Exception as e:
            logger.error("Error getting current tenant", error=str(e))
            return {"name": None, "id": None}
    
    async def automated_login(self) -> dict:
        """
        Perform fully automated login to Xero using credentials and security questions.
        
        This method:
        1. Navigates to Xero login page
        2. Enters email and password
        3. Clicks "Use another authentication method"
        4. Selects "Security questions"
        5. Answers 1, 2, or 3 security questions dynamically
        6. Confirms and completes login
        
        Returns:
            Dict with login status, cookies, and tenant info
        """
        try:
            # Validate credentials are configured
            if not settings.xero_email or not settings.xero_password:
                return {
                    "success": False,
                    "error": "Xero credentials not configured. Set XERO_EMAIL and XERO_PASSWORD in .env"
                }
            
            # Check if at least one security answer is configured
            has_security_answers = any([
                settings.xero_security_answer_1,
                settings.xero_security_answer_2,
                settings.xero_security_answer_3
            ])
            
            if not has_security_answers:
                return {
                    "success": False,
                    "error": "No security question answers configured. Set XERO_SECURITY_ANSWER_1/2/3 in .env"
                }
            
            logger.info("Starting automated Xero login")
            
            # Initialize browser in headed mode (visible) to avoid anti-bot detection
            # Xero blocks headless browsers, so we need a visible browser for login
            await self.browser.initialize(headless=False)
            
            page = self.browser.page
            
            # Navigate to Xero login page
            logger.info("Navigating to Xero login page")
            await page.goto(XERO_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            
            # Wait for page to fully render (Xero uses React with heavy JS)
            logger.info("Waiting for page to fully render")
            await asyncio.sleep(5)  # Increased wait for Xvfb rendering
            
            # Try to wait for networkidle, but don't fail if it times out
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                logger.debug("networkidle timeout during page load, continuing...")
            
            # Additional wait for JS framework to initialize
            await asyncio.sleep(2)
            
            # Always take a debug screenshot to diagnose rendering issues
            screenshot_path = await self.browser.take_screenshot("login_page_initial")
            logger.info(f"Login page screenshot saved: {screenshot_path}")
            
            # Verify page rendered correctly by checking for expected elements
            try:
                # Wait for the login form to be visible - this confirms page rendered
                await page.wait_for_selector("form", state="visible", timeout=30000)
                logger.info("Login form detected - page rendered successfully")
            except Exception as e:
                logger.error(f"Login form not found - page may not have rendered correctly: {e}")
                # Take another screenshot for debugging
                await self.browser.take_screenshot("login_page_render_failed")
                return {
                    "success": False,
                    "error": "Login page failed to render correctly. Check Xvfb display configuration.",
                    "screenshot": screenshot_path,
                    "details": str(e)
                }
            
            # Step 1: Enter email
            logger.info("Entering email")
            email_input = page.get_by_role("textbox", name="Please enter your email")
            
            # Wait longer for React to render the form
            await email_input.wait_for(state="visible", timeout=30000)
            await email_input.click()
            await email_input.fill(settings.xero_email)
            
            # Step 2: Enter password
            logger.info("Entering password")
            password_input = page.get_by_role("textbox", name="Please enter your password")
            await password_input.wait_for(state="visible", timeout=10000)
            await password_input.click()
            await password_input.fill(settings.xero_password)
            
            # Step 3: Click Log in button
            logger.info("Clicking Log in button")
            login_button = page.get_by_role("button", name="Log in")
            await login_button.click()
            
            # Wait for MFA page to load
            await asyncio.sleep(3)
            
            # Step 4: Click "Use another authentication method"
            logger.info("Selecting alternative authentication method")
            try:
                alt_auth_button = page.get_by_role("button", name="Use another authentication")
                await alt_auth_button.wait_for(state="visible", timeout=15000)
                await alt_auth_button.click()
                await asyncio.sleep(1)
            except Exception as e:
                logger.debug(f"Alt auth button not found, may already be on security questions: {e}")
            
            # Step 5: Click "Security questions"
            logger.info("Selecting security questions")
            try:
                security_questions_button = page.get_by_role("button", name="Security questions")
                await security_questions_button.wait_for(state="visible", timeout=10000)
                await security_questions_button.click()
                await asyncio.sleep(2)
            except Exception as e:
                logger.debug(f"Security questions button not found: {e}")
            
            # Step 6: Answer security questions dynamically
            logger.info("Answering security questions")
            await self._answer_security_questions(page)
            
            # Step 7: Click Confirm button
            logger.info("Clicking Confirm button")
            confirm_button = page.get_by_role("button", name="Confirm")
            await confirm_button.wait_for(state="visible", timeout=10000)
            await confirm_button.click()
            
            # Wait for dashboard to load
            logger.info("Waiting for dashboard to load")
            await asyncio.sleep(5)
            
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                logger.debug("networkidle timeout, continuing...")
            
            # Verify login was successful
            is_logged_in = await self._check_logged_in()
            
            if is_logged_in:
                cookies = await self.browser.get_cookies()
                tenant_info = await self._get_current_tenant()
                
                logger.info("Automated login successful", tenant=tenant_info.get("name"))
                
                return {
                    "success": True,
                    "message": "Automated login successful",
                    "cookies": cookies,
                    "current_tenant": tenant_info,
                    "current_url": await self.browser.get_url()
                }
            else:
                screenshot = await self.browser.take_screenshot("automated_login_failed")
                return {
                    "success": False,
                    "error": "Login verification failed - not on Xero dashboard",
                    "screenshot": screenshot,
                    "current_url": await self.browser.get_url()
                }
                
        except Exception as e:
            import traceback
            logger.error("Automated login failed", error=str(e), traceback=traceback.format_exc())
            screenshot = await self.browser.take_screenshot("automated_login_error")
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot,
                "details": traceback.format_exc()
            }
    
    async def _answer_security_questions(self, page) -> None:
        """
        Dynamically answer security questions on the page.
        
        Handles 1, 2, or 3 questions by:
        1. Finding all visible question labels
        2. Matching each question to its configured answer
        3. Filling in the corresponding textbox
        
        Args:
            page: Playwright page object
        """
        # Build answer mapping from settings
        answer_map = {}
        for question_text, setting_attr in SECURITY_QUESTIONS.items():
            answer = getattr(settings, setting_attr, None)
            if answer:
                answer_map[question_text.lower()] = answer
        
        logger.info(f"Configured {len(answer_map)} security question answers")
        
        # Find all textboxes that might be security question inputs
        # The questions appear as labels/text near the textboxes
        questions_answered = 0
        
        for question_text, answer in answer_map.items():
            try:
                # Try to find the textbox by the question text (partial match)
                # Xero uses the question text as the textbox name/label
                textbox = None
                
                # Strategy 1: Find by role with partial name match
                for full_question in SECURITY_QUESTIONS.keys():
                    if question_text in full_question.lower():
                        try:
                            textbox = page.get_by_role("textbox", name=full_question)
                            await textbox.wait_for(state="visible", timeout=3000)
                            break
                        except Exception:
                            continue
                
                # Strategy 2: Find by placeholder or aria-label containing question text
                if not textbox:
                    try:
                        # Look for any visible textbox and check its context
                        textboxes = page.locator('input[type="text"], input[type="password"], input:not([type])')
                        count = await textboxes.count()
                        
                        for i in range(count):
                            tb = textboxes.nth(i)
                            try:
                                # Check if this textbox is associated with our question
                                aria_label = await tb.get_attribute("aria-label") or ""
                                placeholder = await tb.get_attribute("placeholder") or ""
                                name = await tb.get_attribute("name") or ""
                                
                                combined = (aria_label + placeholder + name).lower()
                                if any(q.lower() in combined for q in SECURITY_QUESTIONS.keys() if question_text in q.lower()):
                                    textbox = tb
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        logger.debug(f"Strategy 2 failed: {e}")
                
                if textbox:
                    try:
                        await textbox.click()
                        await textbox.fill(answer)
                        questions_answered += 1
                        logger.info(f"Answered security question {questions_answered}")
                    except Exception as e:
                        logger.debug(f"Failed to fill textbox: {e}")
                        
            except Exception as e:
                logger.debug(f"Error processing question '{question_text}': {e}")
        
        # Alternative approach: Find all visible textboxes and fill them based on nearby text
        if questions_answered == 0:
            logger.info("Trying alternative approach to find security questions")
            try:
                # Get all text on the page to identify which questions are shown
                page_text = await page.content()
                page_text_lower = page_text.lower()
                
                for question_text, setting_attr in SECURITY_QUESTIONS.items():
                    if question_text.lower() in page_text_lower:
                        answer = getattr(settings, setting_attr, None)
                        if answer:
                            try:
                                # Try clicking on the question text first, then find nearby input
                                question_element = page.get_by_text(question_text, exact=False)
                                await question_element.first.click()
                                await asyncio.sleep(0.3)
                                
                                # Find the textbox with this question as name
                                textbox = page.get_by_role("textbox", name=question_text)
                                await textbox.fill(answer)
                                questions_answered += 1
                                logger.info(f"Answered security question (alt): {question_text[:30]}...")
                            except Exception as e:
                                logger.debug(f"Alt approach failed for '{question_text}': {e}")
            except Exception as e:
                logger.debug(f"Alternative approach failed: {e}")
        
        logger.info(f"Total security questions answered: {questions_answered}")
    
    async def logout(self) -> dict:
        """
        Log out from Xero.
        
        This method:
        1. Clicks the user menu button
        2. Clicks the "Log out" link
        3. Verifies logout was successful
        
        Returns:
            Dict with logout status
        """
        try:
            if not self.browser.is_initialized:
                return {
                    "success": False,
                    "error": "Browser not initialized"
                }
            
            # Check if we're logged in first
            is_logged_in = await self._check_logged_in()
            if not is_logged_in:
                return {
                    "success": True,
                    "message": "Already logged out"
                }
            
            logger.info("Starting Xero logout")
            
            page = self.browser.page
            
            # Step 1: Click user menu button
            # The button name includes the organization name, so we use partial match
            logger.info("Clicking user menu")
            try:
                # Try to find user menu button with various patterns
                user_menu = None
                
                # Strategy 1: Find button with "User menu" in the name
                try:
                    user_menu = page.get_by_role("button", name="User menu")
                    await user_menu.wait_for(state="visible", timeout=5000)
                except Exception:
                    pass
                
                # Strategy 2: Find by aria-label pattern
                if not user_menu:
                    try:
                        user_menu = page.locator('button[aria-label*="User menu"]')
                        await user_menu.first.wait_for(state="visible", timeout=5000)
                        user_menu = user_menu.first
                    except Exception:
                        pass
                
                # Strategy 3: Find by class or data attribute
                if not user_menu:
                    try:
                        user_menu = page.locator('[data-testid="user-menu"], [class*="user-menu"], [class*="avatar"]')
                        await user_menu.first.wait_for(state="visible", timeout=5000)
                        user_menu = user_menu.first
                    except Exception:
                        pass
                
                # Strategy 4: JavaScript to find and click
                if not user_menu:
                    clicked = await page.evaluate('''
                        () => {
                            const buttons = document.querySelectorAll('button');
                            for (const btn of buttons) {
                                const label = btn.getAttribute('aria-label') || btn.textContent || '';
                                if (label.toLowerCase().includes('user menu')) {
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    ''')
                    if not clicked:
                        raise Exception("Could not find user menu button")
                else:
                    await user_menu.click()
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Failed to click user menu: {e}")
                screenshot = await self.browser.take_screenshot("logout_user_menu_error")
                return {
                    "success": False,
                    "error": f"Could not find user menu: {e}",
                    "screenshot": screenshot
                }
            
            # Step 2: Click "Log out" link
            logger.info("Clicking Log out link")
            try:
                logout_link = page.get_by_role("link", name="Log out")
                await logout_link.wait_for(state="visible", timeout=10000)
                await logout_link.click()
            except Exception as e:
                # Try alternative selectors
                try:
                    await page.locator('a:has-text("Log out")').first.click()
                except Exception:
                    try:
                        await page.get_by_text("Log out", exact=True).click()
                    except Exception:
                        logger.error(f"Failed to click logout link: {e}")
                        screenshot = await self.browser.take_screenshot("logout_link_error")
                        return {
                            "success": False,
                            "error": f"Could not find logout link: {e}",
                            "screenshot": screenshot
                        }
            
            # Wait for logout to complete
            await asyncio.sleep(3)
            
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                logger.debug("networkidle timeout during logout, continuing...")
            
            # Verify logout was successful
            current_url = await self.browser.get_url()
            is_logged_out = "login.xero.com" in current_url or not await self._check_logged_in()
            
            if is_logged_out:
                logger.info("Logout successful")
                return {
                    "success": True,
                    "message": "Successfully logged out from Xero",
                    "current_url": current_url
                }
            else:
                screenshot = await self.browser.take_screenshot("logout_verification_failed")
                return {
                    "success": False,
                    "error": "Logout verification failed - still appears to be logged in",
                    "screenshot": screenshot,
                    "current_url": current_url
                }
                
        except Exception as e:
            import traceback
            logger.error("Logout failed", error=str(e), traceback=traceback.format_exc())
            screenshot = await self.browser.take_screenshot("logout_error")
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot,
                "details": traceback.format_exc()
            }
