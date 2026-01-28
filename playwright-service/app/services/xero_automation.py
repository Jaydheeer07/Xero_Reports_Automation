"""
Xero Automation Service - Browser automation for Xero operations.

Handles:
- Tenant/organisation switching
- Report navigation and download
- Activity Statement download
- Payroll Activity Summary download
"""

from typing import Optional
from datetime import datetime
import structlog
import asyncio
import os

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.services.browser_manager import BrowserManager
from app.services.file_manager import get_file_manager
from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


# Xero URL patterns
XERO_URLS = {
    "dashboard": "https://go.xero.com/Dashboard",
    "reports": "https://go.xero.com/Reports",
    "activity_statement": "https://go.xero.com/Reports/Report.aspx?reportId=ActivityStatement",
    "payroll_reports": "https://go.xero.com/Reports/PayrollReports",
}

# Selectors with fallbacks - these may need adjustment based on actual Xero UI
SELECTORS = {
    # Organisation/Tenant switcher
    "org_switcher": [
        '[data-testid="org-switcher"]',
        '[data-automationid="org-switcher"]',
        'button[aria-label*="organisation"]',
        'button[aria-label*="organization"]',
        '[class*="org-switcher"]',
        '[class*="organisation-switcher"]',
        'header button:has-text("Switch")',
    ],
    
    # Organisation list items
    "org_list_item": [
        '[data-testid="org-item"]',
        '[class*="org-list"] li',
        '[role="menuitem"]',
        '[class*="organisation-list"] button',
    ],
    
    # Navigation
    "nav_reports": [
        'button:has-text("Reporting")',  # From codegen: get_by_role("button", name="Reporting")
        'text=Reporting',
        'a:has-text("Reporting")',
        'text=Reports',
        'a:has-text("Reports")',
        '[data-testid="nav-reports"]',
    ],
    
    "nav_accounting": [
        'text=Accounting',
        'a:has-text("Accounting")',
        '[data-testid="nav-accounting"]',
    ],
    
    # Search
    "search_input": [
        'input[placeholder*="Search"]',
        'input[type="search"]',
        '[data-testid="search-input"]',
        'input[aria-label*="Search"]',
    ],
    
    # Report specific
    "report_search": [
        'input[placeholder*="Find a report"]',
        'input[placeholder*="Search reports"]',
        '[data-testid="report-search"]',
    ],
    
    # Date range controls
    "date_range_dropdown": [
        'text=Date range',
        'button:has-text("Date range")',
        '[data-testid="date-range"]',
        'select[name*="date"]',
    ],
    
    "last_month_option": [
        'text=Last month',
        '[data-value="last-month"]',
        'option:has-text("Last month")',
    ],
    
    # Buttons
    "update_button": [
        'button:has-text("Update")',
        'button[type="submit"]:has-text("Update")',
        '[data-testid="update-button"]',
    ],
    
    "export_button": [
        'button:has-text("Export")',  # From codegen
        '[data-testid="export-button"]',
        'button[aria-label*="Export"]',
    ],
    
    "excel_radio": [
        'input[type="radio"]:has-text("Excel")',  # From codegen: radio button
        '[role="radio"][name*="Excel"]',
        'label:has-text("Excel")',
    ],
    
    "more_button": [
        'button:has-text("More")',
        '[data-testid="more-button"]',
        'button[aria-label*="More"]',
    ],
    
    "excel_option": [
        'text=Excel',
        'button:has-text("Excel")',
        '[data-testid="export-excel"]',
        'a:has-text("Excel")',
    ],
    
    # Activity Statement specific
    "activity_statement_link": [
        'a:has-text("Activity Statement"):not(:has-text("Activity Statement Summary"))',  # From codegen: exact match
        'text=Activity Statement',
        'a:has-text("Activity Statement")',
        '[data-testid="activity-statement"]',
    ],
    
    "create_new_statement": [
        'button:has-text("Create new statement")',  # From codegen: exact match
        'button:has-text("Create statement")',
        'text=Create new statement',
        '[data-testid="create-statement"]',
    ],
    
    "period_button": [
        # From codegen: periods are buttons, not dropdown options
        # Format: "October 2025 PAYG W" or similar
        'button:has-text("PAYG W")',
        'button[role="button"]',
    ],
    
    "draft_statement": [
        'text=Draft',
        'text=Unfiled',
        'text=New',
        '[class*="draft"]',
        '[class*="unfiled"]',
    ],
    
    # Payroll specific
    "payroll_activity_summary": [
        'text=Payroll Activity Summary',
        'a:has-text("Payroll Activity Summary")',
        '[data-testid="payroll-activity-summary"]',
    ],
}


class XeroAutomation:
    """
    Automates Xero browser interactions.
    
    Provides methods for:
    - Switching between tenants/organisations
    - Downloading Activity Statements
    - Downloading Payroll Activity Summaries
    """
    
    def __init__(self, browser_manager: BrowserManager, debug_screenshots: bool = True):
        self.browser = browser_manager
        self.file_manager = get_file_manager()
        self._debug_screenshots = debug_screenshots  # Enable by default for troubleshooting
    
    @property
    def page(self) -> Page:
        """Get the current browser page."""
        return self.browser.page
    
    async def _find_element(self, selector_key: str, timeout: int = 10000) -> Optional[any]:
        """
        Find an element using multiple fallback selectors.
        
        Args:
            selector_key: Key in SELECTORS dict
            timeout: Timeout in milliseconds
            
        Returns:
            Element if found, None otherwise
        """
        selectors = SELECTORS.get(selector_key, [])
        
        for selector in selectors:
            try:
                element = await self.page.wait_for_selector(
                    selector, 
                    timeout=timeout // len(selectors),
                    state="visible"
                )
                if element:
                    logger.debug(f"Found element with selector: {selector}")
                    return element
            except PlaywrightTimeout:
                continue
            except Exception as e:
                logger.debug(f"Selector {selector} failed: {e}")
                continue
        
        logger.warning(f"Could not find element: {selector_key}")
        return None
    
    async def _click_element(self, selector_key: str, timeout: int = 10000) -> bool:
        """
        Click an element using fallback selectors.
        
        Returns:
            True if clicked successfully
        """
        element = await self._find_element(selector_key, timeout)
        if element:
            await element.click()
            await asyncio.sleep(0.5)  # Brief pause after click
            return True
        return False
    
    async def _take_debug_screenshot(self, name: str) -> Optional[str]:
        """Take a screenshot if debug mode is enabled."""
        if self._debug_screenshots:
            return await self.browser.take_screenshot(name)
        return None
    
    async def switch_tenant(self, tenant_name: str) -> dict:
        """
        Switch to a specified Xero tenant/organisation.
        
        Args:
            tenant_name: Name of the organisation to switch to
            
        Returns:
            Dict with success status and current tenant
        """
        try:
            logger.info(f"Switching to tenant: {tenant_name}")
            
            # Take initial screenshot
            await self._take_debug_screenshot("switch_tenant_start")
            
            # Click organisation switcher
            if not await self._click_element("org_switcher"):
                screenshot = await self.browser.take_screenshot("org_switcher_not_found")
                return {
                    "success": False,
                    "error": "Could not find organisation switcher",
                    "screenshot": screenshot
                }
            
            await asyncio.sleep(1)  # Wait for dropdown to appear
            await self._take_debug_screenshot("org_switcher_opened")
            
            # Try to find the tenant by name
            # First try direct text match
            tenant_found = False
            
            try:
                # Try clicking directly on text matching tenant name
                await self.page.click(f'text="{tenant_name}"', timeout=5000)
                tenant_found = True
            except PlaywrightTimeout:
                # Try partial match
                try:
                    await self.page.click(f'text={tenant_name}', timeout=5000)
                    tenant_found = True
                except PlaywrightTimeout:
                    pass
            
            if not tenant_found:
                # Try searching if there's a search box
                search_input = await self._find_element("search_input", timeout=3000)
                if search_input:
                    await search_input.fill(tenant_name)
                    await asyncio.sleep(1)
                    
                    # Click the first result
                    try:
                        await self.page.click(f'text="{tenant_name}"', timeout=5000)
                        tenant_found = True
                    except PlaywrightTimeout:
                        pass
            
            if not tenant_found:
                # Close dropdown and report failure
                await self.page.keyboard.press("Escape")
                screenshot = await self.browser.take_screenshot("tenant_not_found")
                return {
                    "success": False,
                    "error": f"Could not find tenant: {tenant_name}",
                    "screenshot": screenshot
                }
            
            # Wait for page to reload after tenant switch
            await self.page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(2)
            
            await self._take_debug_screenshot("switch_tenant_complete")
            
            # Verify switch was successful
            current_tenant = await self._get_current_tenant_name()
            
            if current_tenant and tenant_name.lower() in current_tenant.lower():
                logger.info(f"Successfully switched to tenant: {current_tenant}")
                return {
                    "success": True,
                    "current_tenant": current_tenant
                }
            else:
                return {
                    "success": True,
                    "current_tenant": current_tenant,
                    "warning": "Tenant name may not match exactly"
                }
                
        except Exception as e:
            logger.error(f"Error switching tenant: {e}")
            screenshot = await self.browser.take_screenshot("switch_tenant_error")
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot
            }
    
    async def _get_current_tenant_name(self) -> Optional[str]:
        """
        Get the name of the currently selected tenant.
        
        Uses multiple strategies:
        1. Page title (e.g., "Homepage – Marsill Pty Ltd – Xero")
        2. URL shortcode pattern
        3. Org switcher element (fallback)
        """
        try:
            # Strategy 1: Extract from page title
            # Title format: "Page Name – Tenant Name – Xero"
            # Note: Xero uses en-dash (–) not hyphen (-)
            title = await self.page.title()
            logger.info(f"Page title: {title}")
            
            if title and "Xero" in title:
                # Try splitting by en-dash first, then regular dash
                for separator in [" – ", " - ", "–", "-"]:
                    if separator in title:
                        parts = title.split(separator)
                        logger.debug(f"Title parts with '{separator}': {parts}")
                        if len(parts) >= 3:
                            tenant_name = parts[-2].strip()  # Second to last part
                            if tenant_name and tenant_name != "Xero":
                                logger.info(f"Got tenant from title: {tenant_name}")
                                return tenant_name
                        elif len(parts) == 2:
                            # Format might be "Tenant Name – Xero"
                            tenant_name = parts[0].strip()
                            if tenant_name and tenant_name != "Xero":
                                logger.info(f"Got tenant from title (2 parts): {tenant_name}")
                                return tenant_name
                        break  # Only try the first matching separator
            
            # Strategy 2: Try org switcher element (with short timeout)
            try:
                element = await self._find_element("org_switcher", timeout=3000)
                if element:
                    text = await element.text_content()
                    if text:
                        logger.info(f"Got tenant from org_switcher: {text.strip()}")
                        return text.strip()
            except Exception:
                pass  # Ignore org_switcher failures
                    
        except Exception as e:
            logger.warning(f"Error getting current tenant name: {e}")
        
        return None
    
    async def download_payroll_activity_summary(
        self,
        tenant_name: str,
        month: Optional[int] = None,
        year: Optional[int] = None
    ) -> dict:
        """
        Download the Payroll Activity Summary report.
        
        Args:
            tenant_name: Name of the tenant (for file naming)
            month: Optional month (1-12), defaults to last month
            year: Optional year, defaults to current/last year
            
        Returns:
            Dict with success status and file path
        """
        try:
            logger.info(f"Downloading Payroll Activity Summary for {tenant_name}")
            
            await self._take_debug_screenshot("payroll_start")
            
            # Navigate to Reports
            await self.page.goto(XERO_URLS["reports"], wait_until="networkidle")
            await asyncio.sleep(2)
            
            await self._take_debug_screenshot("reports_page")
            
            # Search for Payroll Activity Summary
            search_input = await self._find_element("report_search", timeout=10000)
            if not search_input:
                search_input = await self._find_element("search_input", timeout=5000)
            
            if search_input:
                await search_input.fill("Payroll Activity Summary")
                await asyncio.sleep(1)
            
            # Click on the report
            if not await self._click_element("payroll_activity_summary", timeout=10000):
                # Try direct text click
                try:
                    await self.page.click('text=Payroll Activity Summary', timeout=10000)
                except PlaywrightTimeout:
                    screenshot = await self.browser.take_screenshot("payroll_report_not_found")
                    return {
                        "success": False,
                        "error": "Could not find Payroll Activity Summary report",
                        "screenshot": screenshot
                    }
            
            await self.page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            
            await self._take_debug_screenshot("payroll_report_page")
            
            # Set date range to "Last month"
            if await self._click_element("date_range_dropdown", timeout=5000):
                await asyncio.sleep(0.5)
                await self._click_element("last_month_option", timeout=5000)
                await asyncio.sleep(0.5)
                
                # Click Update button
                await self._click_element("update_button", timeout=5000)
                await self.page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)
            
            await self._take_debug_screenshot("payroll_report_loaded")
            
            # Export to Excel
            file_path = await self._export_to_excel(
                report_type="payroll_summary",
                tenant_name=tenant_name
            )
            
            if file_path:
                return {
                    "success": True,
                    "file_path": file_path,
                    "file_name": os.path.basename(file_path),
                    "tenant_name": tenant_name,
                    "report_type": "payroll_activity_summary"
                }
            else:
                screenshot = await self.browser.take_screenshot("payroll_export_failed")
                return {
                    "success": False,
                    "error": "Failed to export report",
                    "screenshot": screenshot
                }
                
        except Exception as e:
            logger.error(f"Error downloading payroll summary: {e}")
            screenshot = await self.browser.take_screenshot("payroll_error")
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot
            }
    
    async def download_activity_statement(
        self,
        tenant_name: str,
        find_unfiled: bool = True,
        period: str = "October 2025"
    ) -> dict:
        """
        Download the Activity Statement (BAS Report).
        
        Workflow:
        1. Click "Reporting" on navbar
        2. Click "Activity Statement"
        3. Click "Create new statement" button
        4. Select the period (e.g., October 2025) from dropdown
        5. Download the statement
        
        Args:
            tenant_name: Name of the tenant (for file naming)
            find_unfiled: If True, look for draft/unfiled statements
            period: Period to select (e.g., "October 2025")
            
        Returns:
            Dict with success status and file path
        """
        try:
            logger.info(f"Downloading Activity Statement for {tenant_name}, period: {period}")
            
            # Navigate to Xero homepage first to ensure we're on the right page
            # Use the homepage URL pattern from codegen: /app/{shortcode}/homepage
            current_url = self.page.url
            logger.info(f"Current URL: {current_url}")
            
            # If not on Xero, navigate to dashboard
            if "xero.com" not in current_url:
                await self.page.goto(XERO_URLS["dashboard"], wait_until="domcontentloaded", timeout=60000)
            
            # Wait for the page to fully load - Xero is a heavy SPA
            # Use try/except for networkidle as it may timeout on heavy SPAs
            try:
                await self.page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                logger.debug("networkidle timeout, continuing anyway...")
            await asyncio.sleep(3)  # Extra time for JavaScript to render navigation
            
            await self._take_debug_screenshot("activity_start")
            
            # Step 1: Click "Reporting" on navbar
            # Xero uses a navigation bar that loads dynamically
            reporting_clicked = await self._click_reporting_nav()
            
            if not reporting_clicked:
                screenshot = await self.browser.take_screenshot("reporting_nav_not_found")
                return {
                    "success": False,
                    "error": "Could not find Reporting navigation link",
                    "screenshot": screenshot
                }
            
            # Wait for dropdown/menu to appear after clicking Reporting
            await asyncio.sleep(2)
            await self._take_debug_screenshot("reporting_menu_opened")
            
            # Step 2: Click "Activity Statement" link in the dropdown
            activity_clicked = await self._click_activity_statement_link()
            
            if not activity_clicked:
                screenshot = await self.browser.take_screenshot("activity_statement_not_found")
                return {
                    "success": False,
                    "error": "Could not find Activity Statement link",
                    "screenshot": screenshot
                }
            
            try:
                await self.page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                logger.debug("networkidle timeout after Activity Statement click, continuing...")
            await asyncio.sleep(2)
            await self._take_debug_screenshot("activity_statement_page")
            
            # Step 3: Click "Create new statement" button
            create_clicked = await self._click_create_new_statement()
            if not create_clicked:
                logger.warning("Could not find 'Create new statement' button, trying to proceed anyway")
                await self._take_debug_screenshot("no_create_button")
            else:
                await asyncio.sleep(2)
                await self._take_debug_screenshot("create_button_clicked")
            
            # Step 4: Click the period button (e.g., "October 2025 PAYG W")
            period_clicked = await self._click_period_button(period)
            if not period_clicked:
                logger.warning(f"Could not find period button for: {period}")
                await self._take_debug_screenshot("period_not_found")
            else:
                await asyncio.sleep(1)
                await self._take_debug_screenshot("period_selected")
            
            # If looking for unfiled, try to find draft/unfiled statement
            if find_unfiled:
                draft_found = await self._click_element("draft_statement", timeout=5000)
                if draft_found:
                    await self.page.wait_for_load_state("networkidle")
                    await asyncio.sleep(2)
                    await self._take_debug_screenshot("draft_statement_selected")
            
            # Step 5: Export to Excel
            file_path = await self._export_to_excel(
                report_type="activity_statement",
                tenant_name=tenant_name
            )
            
            if file_path:
                return {
                    "success": True,
                    "file_path": file_path,
                    "file_name": os.path.basename(file_path),
                    "tenant_name": tenant_name,
                    "report_type": "activity_statement",
                    "period": period
                }
            else:
                screenshot = await self.browser.take_screenshot("activity_export_failed")
                return {
                    "success": False,
                    "error": "Failed to export report",
                    "screenshot": screenshot
                }
                
        except Exception as e:
            logger.error(f"Error downloading activity statement: {e}")
            screenshot = await self.browser.take_screenshot("activity_error")
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot
            }
    
    async def _click_reporting_nav(self) -> bool:
        """
        Click the Reporting button in the navigation bar.
        Uses multiple strategies to handle Xero's dynamic SPA.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click Reporting navigation button")
        
        # Strategy 1: Use get_by_role with exact match (from codegen)
        try:
            reporting_btn = self.page.get_by_role("button", name="Reporting")
            # Wait for the button to be visible
            await reporting_btn.wait_for(state="visible", timeout=15000)
            # Scroll into view if needed
            await reporting_btn.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)  # Brief pause after scroll
            await reporting_btn.click(timeout=5000)
            logger.info("Clicked Reporting button using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 (get_by_role button) failed: {e}")
        
        # Strategy 2: Try as a link instead of button
        try:
            reporting_link = self.page.get_by_role("link", name="Reporting")
            await reporting_link.wait_for(state="visible", timeout=10000)
            await reporting_link.scroll_into_view_if_needed()
            await reporting_link.click(timeout=5000)
            logger.info("Clicked Reporting link using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 (get_by_role link) failed: {e}")
        
        # Strategy 3: Use locator with text content
        try:
            reporting_locator = self.page.locator('button:has-text("Reporting"), [role="button"]:has-text("Reporting")')
            await reporting_locator.first.wait_for(state="visible", timeout=10000)
            await reporting_locator.first.scroll_into_view_if_needed()
            await reporting_locator.first.click(timeout=5000)
            logger.info("Clicked Reporting using locator with has-text")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 (locator has-text) failed: {e}")
        
        # Strategy 4: Try clicking by text with force option
        try:
            await self.page.locator('text=Reporting').first.click(force=True, timeout=10000)
            logger.info("Clicked Reporting using text locator with force")
            return True
        except Exception as e:
            logger.debug(f"Strategy 4 (text with force) failed: {e}")
        
        # Strategy 5: Find in navigation area specifically
        try:
            nav_area = self.page.locator('nav, header, [role="navigation"]')
            reporting_in_nav = nav_area.get_by_text("Reporting", exact=True)
            await reporting_in_nav.first.click(timeout=10000)
            logger.info("Clicked Reporting in navigation area")
            return True
        except Exception as e:
            logger.debug(f"Strategy 5 (nav area) failed: {e}")
        
        # Strategy 6: Use JavaScript click as last resort
        try:
            clicked = await self.page.evaluate('''
                () => {
                    const elements = document.querySelectorAll('button, a, [role="button"], [role="menuitem"]');
                    for (const el of elements) {
                        if (el.textContent && el.textContent.trim().includes('Reporting')) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''')
            if clicked:
                logger.info("Clicked Reporting using JavaScript evaluation")
                return True
        except Exception as e:
            logger.debug(f"Strategy 6 (JS click) failed: {e}")
        
        logger.error("All strategies to click Reporting button failed")
        return False
    
    async def _click_activity_statement_link(self) -> bool:
        """
        Click the Activity Statement link in the Reporting dropdown.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click Activity Statement link")
        
        # Strategy 1: Use get_by_role with exact match (from codegen)
        try:
            link = self.page.get_by_role("link", name="Activity Statement", exact=True)
            await link.wait_for(state="visible", timeout=10000)
            await link.click(timeout=5000)
            logger.info("Clicked Activity Statement using get_by_role exact")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Use menuitem role (common in dropdowns)
        try:
            menuitem = self.page.get_by_role("menuitem", name="Activity Statement")
            await menuitem.wait_for(state="visible", timeout=5000)
            await menuitem.click(timeout=5000)
            logger.info("Clicked Activity Statement using menuitem role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Locator with exact text to avoid matching "Activity Statement Summary"
        try:
            # Use filter to exclude links that contain "Summary"
            links = self.page.locator('a:has-text("Activity Statement")').filter(
                has_not_text="Summary"
            )
            await links.first.wait_for(state="visible", timeout=5000)
            await links.first.click(timeout=5000)
            logger.info("Clicked Activity Statement using filtered locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        # Strategy 4: Text locator with force
        try:
            await self.page.get_by_text("Activity Statement", exact=True).first.click(
                force=True, timeout=5000
            )
            logger.info("Clicked Activity Statement using text with force")
            return True
        except Exception as e:
            logger.debug(f"Strategy 4 failed: {e}")
        
        # Strategy 5: JavaScript click
        try:
            clicked = await self.page.evaluate('''
                () => {
                    const links = document.querySelectorAll('a, [role="menuitem"], [role="link"]');
                    for (const link of links) {
                        const text = link.textContent?.trim();
                        if (text === 'Activity Statement' || 
                            (text?.includes('Activity Statement') && !text?.includes('Summary'))) {
                            link.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''')
            if clicked:
                logger.info("Clicked Activity Statement using JavaScript")
                return True
        except Exception as e:
            logger.debug(f"Strategy 5 failed: {e}")
        
        logger.error("All strategies to click Activity Statement link failed")
        return False
    
    async def _click_create_new_statement(self) -> bool:
        """
        Click the 'Create new statement' button.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click 'Create new statement' button")
        
        # Strategy 1: Use get_by_role (from codegen)
        try:
            btn = self.page.get_by_role("button", name="Create new statement")
            await btn.wait_for(state="visible", timeout=10000)
            await btn.scroll_into_view_if_needed()
            await btn.click(timeout=5000)
            logger.info("Clicked 'Create new statement' using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Locator with has-text
        try:
            btn = self.page.locator('button:has-text("Create new statement")')
            await btn.first.wait_for(state="visible", timeout=5000)
            await btn.first.click(timeout=5000)
            logger.info("Clicked 'Create new statement' using locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Text with force click
        try:
            await self.page.get_by_text("Create new statement").click(force=True, timeout=5000)
            logger.info("Clicked 'Create new statement' using text with force")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        logger.warning("Could not find 'Create new statement' button")
        return False
    
    async def _click_period_button(self, period: str) -> bool:
        """
        Click the period button (e.g., "October 2025 PAYG W" or "December 2025 GST, PAYG W, PAYG I").
        
        The period text varies - some have just "PAYG W", others have "GST, PAYG W, PAYG I".
        The constant is that all contain "PAYG" and the month/year.
        
        Args:
            period: The period text to match (e.g., "October 2025", "December 2025")
            
        Returns:
            True if clicked successfully
        """
        logger.info(f"Attempting to click period button for: {period}")
        
        # Strategy 1: Use get_by_role with partial name match
        try:
            # The button text might be "October 2025 PAYG W" or "December 2025 GST, PAYG W, PAYG I"
            btn = self.page.get_by_role("button", name=period)
            await btn.wait_for(state="visible", timeout=10000)
            await btn.scroll_into_view_if_needed()
            await btn.click(timeout=5000)
            logger.info(f"Clicked period button using get_by_role: {period}")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Locator with has-text for partial match
        try:
            btn = self.page.locator(f'button:has-text("{period}")')
            await btn.first.wait_for(state="visible", timeout=5000)
            await btn.first.scroll_into_view_if_needed()
            await btn.first.click(timeout=5000)
            logger.info(f"Clicked period button using locator: {period}")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Look for any clickable element with the period text
        try:
            element = self.page.locator(f'[role="button"]:has-text("{period}"), button:has-text("{period}"), a:has-text("{period}")')
            await element.first.wait_for(state="visible", timeout=5000)
            await element.first.click(timeout=5000)
            logger.info(f"Clicked period element: {period}")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        # Strategy 4: JavaScript click - search for element containing both period AND "PAYG"
        try:
            clicked = await self.page.evaluate('''
                (periodText) => {
                    const elements = document.querySelectorAll('button, [role="button"], a, [role="option"], li');
                    for (const el of elements) {
                        const text = el.textContent;
                        // Match elements that contain the period (e.g., "December 2025") AND "PAYG"
                        if (text && text.includes(periodText) && text.includes('PAYG')) {
                            el.click();
                            return true;
                        }
                    }
                    // Fallback: just match the period text
                    for (const el of elements) {
                        if (el.textContent && el.textContent.includes(periodText)) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''', period)
            if clicked:
                logger.info(f"Clicked period button using JavaScript: {period}")
                return True
        except Exception as e:
            logger.debug(f"Strategy 4 failed: {e}")
        
        # Strategy 5: Try clicking on list items or options in dropdown
        try:
            # The dropdown might use li elements or role="option"
            options = self.page.locator(f'li:has-text("{period}"), [role="option"]:has-text("{period}")')
            count = await options.count()
            if count > 0:
                await options.first.click(timeout=5000)
                logger.info(f"Clicked period option in dropdown: {period}")
                return True
        except Exception as e:
            logger.debug(f"Strategy 5 failed: {e}")
        
        logger.warning(f"Could not find period button for: {period}")
        return False
    
    async def _export_to_excel(self, report_type: str, tenant_name: str) -> Optional[str]:
        """
        Export the current report to Excel.
        
        Workflow from codegen:
        1. Click "Export" button
        2. Check "Excel" radio button
        3. Click "Export" button again (the second one in the modal)
        
        Returns:
            Path to downloaded file, or None if failed
        """
        try:
            await self._take_debug_screenshot("export_start")
            
            # Step 1: Click first Export button to open export modal
            export_clicked = await self._click_export_button()
            
            if not export_clicked:
                logger.error("Could not find Export button")
                return None
            
            await asyncio.sleep(1.5)  # Wait for modal to open
            await self._take_debug_screenshot("export_modal_opened")
            
            # Step 2: Check Excel radio button
            await self._select_excel_format()
            
            await asyncio.sleep(0.5)
            await self._take_debug_screenshot("excel_selected")
            
            # Step 3: Click the BOTTOM Export button (not the top one which may be obscured by popups)
            # The page has an "Adjust G field values" popup that can overlap with the top Export button
            # Using the bottom Export button avoids this conflict
            await self._take_debug_screenshot("before_final_export")
            
            # First, scroll to the bottom of the page to ensure the bottom Export button is visible
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)
            
            async def click_final_export():
                # Click the LAST (bottom) Export button to avoid popup conflicts
                # Strategy 1: Use get_by_role and click the LAST Export button
                try:
                    export_buttons = self.page.get_by_role("button", name="Export")
                    count = await export_buttons.count()
                    logger.info(f"Found {count} Export buttons - clicking the LAST one (bottom)")
                    if count > 0:
                        # Click the last (bottom) Export button
                        await export_buttons.last.click(timeout=5000)
                        logger.info("Clicked LAST (bottom) Export button using get_by_role")
                        return
                except Exception as e:
                    logger.debug(f"Strategy 1 for final export failed: {e}")
                
                # Strategy 2: Use locator and click the last button
                try:
                    buttons = self.page.locator('button:has-text("Export")')
                    count = await buttons.count()
                    logger.info(f"Found {count} buttons with Export text - clicking the LAST one")
                    if count > 0:
                        await buttons.last.click(timeout=5000)
                        logger.info("Clicked LAST Export button using locator")
                        return
                except Exception as e:
                    logger.debug(f"Strategy 2 for final export failed: {e}")
                
                # Strategy 3: JavaScript click on the LAST Export button (bottom of page)
                try:
                    clicked = await self.page.evaluate('''
                        () => {
                            const buttons = Array.from(document.querySelectorAll('button'));
                            // Filter to only Export buttons
                            const exportButtons = buttons.filter(btn => 
                                btn.textContent && btn.textContent.trim() === 'Export'
                            );
                            if (exportButtons.length > 0) {
                                // Click the LAST one (bottom of page)
                                const lastBtn = exportButtons[exportButtons.length - 1];
                                lastBtn.scrollIntoView();
                                lastBtn.click();
                                return true;
                            }
                            return false;
                        }
                    ''')
                    if clicked:
                        logger.info("Clicked LAST Export button using JavaScript")
                        return
                except Exception as e:
                    logger.debug(f"Strategy 3 for final export failed: {e}")
                
                # Strategy 4: Click with force on the last button
                try:
                    await self.page.get_by_role("button", name="Export").last.click(force=True, timeout=5000)
                    logger.info("Clicked last Export button with force")
                    return
                except Exception as e:
                    logger.debug(f"Strategy 4 for final export failed: {e}")
                    raise e
            
            try:
                file_path = await self.browser.wait_for_download(
                    click_final_export,
                    timeout=60000
                )
            except Exception as e:
                logger.error(f"Download failed: {e}")
                return None
            
            # Rename file with proper naming convention
            new_filename = self.file_manager.generate_filename(
                report_type=report_type,
                tenant_name=tenant_name
            )
            
            final_path = self.file_manager.rename_download(file_path, new_filename)
            
            # Validate the file
            if self.file_manager.validate_excel_file(final_path):
                logger.info(f"Successfully downloaded: {new_filename}")
                return final_path
            else:
                logger.warning(f"Downloaded file may be invalid: {new_filename}")
                return final_path  # Return anyway, let caller decide
                
        except Exception as e:
            logger.error(f"Error exporting to Excel: {e}")
            return None
    
    async def _click_export_button(self) -> bool:
        """
        Click the Export button to open the export modal.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click Export button")
        
        # Strategy 1: Use get_by_role
        try:
            btn = self.page.get_by_role("button", name="Export")
            await btn.first.wait_for(state="visible", timeout=10000)
            await btn.first.scroll_into_view_if_needed()
            await btn.first.click(timeout=5000)
            logger.info("Clicked Export button using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Locator with has-text
        try:
            btn = self.page.locator('button:has-text("Export")')
            await btn.first.wait_for(state="visible", timeout=5000)
            await btn.first.click(timeout=5000)
            logger.info("Clicked Export button using locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Use fallback selectors
        if await self._click_element("export_button", timeout=5000):
            logger.info("Clicked Export button using fallback selectors")
            return True
        
        logger.error("Could not find Export button")
        return False
    
    async def _select_excel_format(self) -> bool:
        """
        Select Excel format in the export modal.
        
        Returns:
            True if selected successfully
        """
        logger.info("Attempting to select Excel format")
        
        # Strategy 1: Use get_by_role for radio button (from codegen)
        try:
            radio = self.page.get_by_role("radio", name="Excel")
            await radio.wait_for(state="visible", timeout=5000)
            await radio.check(timeout=5000)
            logger.info("Selected Excel using get_by_role radio")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Click the label
        try:
            label = self.page.get_by_label("Excel")
            await label.click(timeout=5000)
            logger.info("Selected Excel by clicking label")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Click text "Excel" near a radio button
        try:
            await self.page.locator('label:has-text("Excel")').click(timeout=5000)
            logger.info("Selected Excel using label locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        # Strategy 4: Click any element with Excel text in the modal
        try:
            modal = self.page.locator('[role="dialog"], .modal, [class*="modal"], [class*="export"]')
            excel_option = modal.get_by_text("Excel")
            await excel_option.click(timeout=5000)
            logger.info("Selected Excel in modal")
            return True
        except Exception as e:
            logger.debug(f"Strategy 4 failed: {e}")
        
        # Strategy 5: JavaScript to find and click Excel radio
        try:
            clicked = await self.page.evaluate('''
                () => {
                    // Try to find radio button with Excel label
                    const labels = document.querySelectorAll('label');
                    for (const label of labels) {
                        if (label.textContent?.includes('Excel')) {
                            label.click();
                            return true;
                        }
                    }
                    // Try to find radio input with Excel value
                    const radios = document.querySelectorAll('input[type="radio"]');
                    for (const radio of radios) {
                        if (radio.value?.toLowerCase().includes('excel') || 
                            radio.id?.toLowerCase().includes('excel')) {
                            radio.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''')
            if clicked:
                logger.info("Selected Excel using JavaScript")
                return True
        except Exception as e:
            logger.debug(f"Strategy 5 failed: {e}")
        
        logger.warning("Could not find Excel option, it might already be selected")
        return False
    
    async def download_reports_for_tenant(
        self,
        tenant_id: str,
        tenant_name: str,
        reports: list[str] = None
    ) -> dict:
        """
        Download all specified reports for a tenant.
        
        Args:
            tenant_id: Xero tenant ID
            tenant_name: Tenant name for switching and file naming
            reports: List of report types to download
            
        Returns:
            Dict with results for each report
        """
        if reports is None:
            reports = ["activity_statement", "payroll_summary"]
        
        results = {
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "reports": {},
            "success": True
        }
        
        # Switch to tenant first
        switch_result = await self.switch_tenant(tenant_name)
        if not switch_result.get("success"):
            results["success"] = False
            results["error"] = f"Failed to switch tenant: {switch_result.get('error')}"
            return results
        
        # Download each report
        for report_type in reports:
            if report_type == "activity_statement":
                result = await self.download_activity_statement(tenant_name)
            elif report_type in ["payroll_summary", "payroll_activity_summary"]:
                result = await self.download_payroll_activity_summary(tenant_name)
            else:
                result = {"success": False, "error": f"Unknown report type: {report_type}"}
            
            results["reports"][report_type] = result
            
            if not result.get("success"):
                results["success"] = False
        
        return results
