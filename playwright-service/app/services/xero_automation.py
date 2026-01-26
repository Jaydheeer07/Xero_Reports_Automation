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
        'text=Reports',
        'a:has-text("Reports")',
        'nav >> text=Reports',
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
        'button:has-text("Export")',
        '[data-testid="export-button"]',
        'button[aria-label*="Export"]',
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
        'text=Activity Statement',
        'a:has-text("Activity Statement")',
        '[data-testid="activity-statement"]',
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
    
    def __init__(self, browser_manager: BrowserManager):
        self.browser = browser_manager
        self.file_manager = get_file_manager()
        self._debug_screenshots = False
    
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
        """Get the name of the currently selected tenant."""
        try:
            element = await self._find_element("org_switcher", timeout=5000)
            if element:
                text = await element.text_content()
                return text.strip() if text else None
        except Exception:
            pass
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
        find_unfiled: bool = True
    ) -> dict:
        """
        Download the Activity Statement (BAS Report).
        
        Args:
            tenant_name: Name of the tenant (for file naming)
            find_unfiled: If True, look for draft/unfiled statements
            
        Returns:
            Dict with success status and file path
        """
        try:
            logger.info(f"Downloading Activity Statement for {tenant_name}")
            
            await self._take_debug_screenshot("activity_start")
            
            # Navigate to Reports
            await self.page.goto(XERO_URLS["reports"], wait_until="networkidle")
            await asyncio.sleep(2)
            
            # Search for Activity Statement
            search_input = await self._find_element("report_search", timeout=10000)
            if not search_input:
                search_input = await self._find_element("search_input", timeout=5000)
            
            if search_input:
                await search_input.fill("Activity Statement")
                await asyncio.sleep(1)
            
            # Click on Activity Statement
            if not await self._click_element("activity_statement_link", timeout=10000):
                try:
                    await self.page.click('text=Activity Statement', timeout=10000)
                except PlaywrightTimeout:
                    screenshot = await self.browser.take_screenshot("activity_statement_not_found")
                    return {
                        "success": False,
                        "error": "Could not find Activity Statement report",
                        "screenshot": screenshot
                    }
            
            await self.page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            
            await self._take_debug_screenshot("activity_statement_page")
            
            # If looking for unfiled, try to find draft/unfiled statement
            if find_unfiled:
                draft_found = await self._click_element("draft_statement", timeout=5000)
                if draft_found:
                    await self.page.wait_for_load_state("networkidle")
                    await asyncio.sleep(2)
                    await self._take_debug_screenshot("draft_statement_selected")
            
            # Export to Excel
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
                    "report_type": "activity_statement"
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
    
    async def _export_to_excel(self, report_type: str, tenant_name: str) -> Optional[str]:
        """
        Export the current report to Excel.
        
        Returns:
            Path to downloaded file, or None if failed
        """
        try:
            await self._take_debug_screenshot("export_start")
            
            # Try to find and click Export button
            export_clicked = await self._click_element("export_button", timeout=5000)
            
            if not export_clicked:
                # Try clicking "More" first, then Export
                if await self._click_element("more_button", timeout=3000):
                    await asyncio.sleep(0.5)
                    export_clicked = await self._click_element("export_button", timeout=5000)
            
            if not export_clicked:
                logger.error("Could not find Export button")
                return None
            
            await asyncio.sleep(0.5)
            await self._take_debug_screenshot("export_menu_opened")
            
            # Click Excel option and wait for download
            async def click_excel():
                if not await self._click_element("excel_option", timeout=5000):
                    # Try direct text click
                    await self.page.click('text=Excel', timeout=5000)
            
            try:
                file_path = await self.browser.wait_for_download(
                    click_excel,
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
