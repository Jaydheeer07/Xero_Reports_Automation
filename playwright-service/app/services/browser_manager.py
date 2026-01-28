"""
Browser Manager - Playwright browser lifecycle management.

Handles browser instance creation, context management, and cleanup.
Supports both headless (automated) and headed (manual auth) modes.

IMPORTANT: On Windows, Playwright requires ProactorEventLoop for subprocess support.
When running with uvicorn, do NOT use --reload flag as it switches to SelectorEventLoop
which is incompatible with Playwright's subprocess requirements.

Run with: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
(without --reload)
"""

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from typing import Optional, Callable, Any
import structlog
import asyncio
import os
from datetime import datetime

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class BrowserManager:
    """
    Manages Playwright browser lifecycle.
    
    Provides singleton-like access to browser instance with support for:
    - Headless mode for automated operations
    - Headed mode for manual authentication
    - Download handling
    - Screenshot capture on errors
    """
    
    _instance: Optional["BrowserManager"] = None
    _lock = asyncio.Lock()
    
    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._is_initialized = False
        self._headless = settings.headless
    
    @classmethod
    async def get_instance(cls) -> "BrowserManager":
        """Get or create the singleton browser manager instance."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
    
    @property
    def is_initialized(self) -> bool:
        """Check if browser is initialized."""
        return self._is_initialized and self._browser is not None
    
    @property
    def page(self) -> Optional[Page]:
        """Get the current page."""
        return self._page
    
    @property
    def context(self) -> Optional[BrowserContext]:
        """Get the current browser context."""
        return self._context
    
    async def initialize(self, headless: bool = True) -> None:
        """
        Initialize the browser.
        
        Args:
            headless: If True, run in headless mode. If False, show browser window.
        """
        if self._is_initialized:
            logger.warning("Browser already initialized, closing existing instance")
            await self.close()
        
        try:
            logger.info("Initializing Playwright browser", headless=headless)
            
            self._playwright = await async_playwright().start()
            
            # Launch browser with appropriate settings
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ]
            )
            
            # Create browser context with download handling
            self._context = await self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                accept_downloads=True,
            )
            
            # Set default timeout
            self._context.set_default_timeout(settings.playwright_timeout)
            
            # Create initial page
            self._page = await self._context.new_page()
            
            self._is_initialized = True
            self._headless = headless
            
            logger.info("Browser initialized successfully", headless=headless)
            
        except Exception as e:
            logger.error("Failed to initialize browser", error=str(e))
            await self.close()
            raise
    
    async def ensure_initialized(self, headless: bool = True) -> None:
        """Ensure browser is initialized, starting it if needed."""
        if not self._is_initialized:
            await self.initialize(headless=headless)
    
    async def new_page(self) -> Page:
        """Create a new page in the current context."""
        if not self._context:
            raise RuntimeError("Browser context not initialized")
        
        page = await self._context.new_page()
        logger.debug("New page created")
        return page
    
    async def get_cookies(self) -> list[dict]:
        """Get all cookies from the current context."""
        if not self._context:
            raise RuntimeError("Browser context not initialized")
        
        cookies = await self._context.cookies()
        logger.debug("Retrieved cookies", count=len(cookies))
        return cookies
    
    async def set_cookies(self, cookies: list[dict]) -> None:
        """Set cookies in the current context."""
        if not self._context:
            raise RuntimeError("Browser context not initialized")
        
        await self._context.add_cookies(cookies)
        logger.debug("Cookies set", count=len(cookies))
    
    async def clear_cookies(self) -> None:
        """Clear all cookies from the current context."""
        if not self._context:
            raise RuntimeError("Browser context not initialized")
        
        await self._context.clear_cookies()
        logger.debug("Cookies cleared")
    
    async def take_screenshot(self, name: str = "screenshot") -> str:
        """
        Take a screenshot of the current page.
        
        Args:
            name: Base name for the screenshot file
            
        Returns:
            Path to the saved screenshot
        """
        if not self._page:
            raise RuntimeError("No page available for screenshot")
        
        os.makedirs(settings.screenshot_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.png"
        filepath = os.path.join(settings.screenshot_dir, filename)
        
        await self._page.screenshot(path=filepath, full_page=True)
        logger.info("Screenshot saved", path=filepath)
        
        return filepath
    
    async def wait_for_download(self, trigger_action: Callable, timeout: int = 30000) -> str:
        """
        Wait for a download to complete after triggering an action.
        
        Args:
            trigger_action: Async function that triggers the download
            timeout: Maximum time to wait for download in milliseconds
            
        Returns:
            Path to the downloaded file
        """
        if not self._page:
            raise RuntimeError("No page available")
        
        os.makedirs(settings.download_dir, exist_ok=True)
        
        async with self._page.expect_download(timeout=timeout) as download_info:
            await trigger_action()
        
        download = await download_info.value
        filename = download.suggested_filename
        filepath = os.path.join(settings.download_dir, filename)
        
        await download.save_as(filepath)
        logger.info("Download completed", path=filepath)
        
        return filepath
    
    async def health_check(self) -> dict:
        """
        Perform a health check on the browser.
        
        Returns:
            Dict with browser health status
        """
        status = {
            "initialized": self._is_initialized,
            "headless": self._headless,
            "browser_connected": False,
            "context_active": False,
            "page_active": False,
        }
        
        if self._browser:
            status["browser_connected"] = self._browser.is_connected()
        
        if self._context:
            status["context_active"] = True
            
        if self._page:
            status["page_active"] = not self._page.is_closed()
        
        return status
    
    async def close(self) -> None:
        """Close the browser and cleanup resources."""
        logger.info("Closing browser")
        
        try:
            if self._page and not self._page.is_closed():
                await self._page.close()
                self._page = None
            
            if self._context:
                await self._context.close()
                self._context = None
            
            if self._browser:
                await self._browser.close()
                self._browser = None
            
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            
            self._is_initialized = False
            logger.info("Browser closed successfully")
            
        except Exception as e:
            logger.error("Error closing browser", error=str(e))
            raise
    
    async def restart(self, headless: bool = True) -> None:
        """Restart the browser with fresh state."""
        await self.close()
        await self.initialize(headless=headless)
    
    # Helper methods for page operations (used by xero_auth.py)
    async def goto(self, url: str, wait_until: str = "load") -> None:
        """Navigate to a URL."""
        if not self._page:
            raise RuntimeError("No page available")
        await self._page.goto(url, wait_until=wait_until)
    
    async def get_url(self) -> str:
        """Get current page URL."""
        if not self._page:
            raise RuntimeError("No page available")
        return self._page.url
    
    async def get_title(self) -> str:
        """Get current page title."""
        if not self._page:
            raise RuntimeError("No page available")
        return await self._page.title()
    
    async def query_selector(self, selector: str) -> Optional[Any]:
        """Query for an element."""
        if not self._page:
            raise RuntimeError("No page available")
        return await self._page.query_selector(selector)
    
    async def query_selector_all(self, selector: str) -> list:
        """Query for all matching elements."""
        if not self._page:
            raise RuntimeError("No page available")
        return await self._page.query_selector_all(selector)
    
    async def click(self, selector: str, timeout: int = 30000) -> None:
        """Click an element."""
        if not self._page:
            raise RuntimeError("No page available")
        await self._page.click(selector, timeout=timeout)
    
    async def fill(self, selector: str, value: str) -> None:
        """Fill an input field."""
        if not self._page:
            raise RuntimeError("No page available")
        await self._page.fill(selector, value)
    
    async def press_key(self, key: str) -> None:
        """Press a keyboard key."""
        if not self._page:
            raise RuntimeError("No page available")
        await self._page.keyboard.press(key)
    
    async def get_text_content(self, selector: str) -> Optional[str]:
        """Get text content of an element."""
        if not self._page:
            raise RuntimeError("No page available")
        element = await self._page.query_selector(selector)
        if element:
            return await element.text_content()
        return None
    
    async def wait_for_selector(self, selector: str, timeout: int = 30000, state: str = "visible") -> Optional[Any]:
        """Wait for an element to appear."""
        if not self._page:
            raise RuntimeError("No page available")
        return await self._page.wait_for_selector(selector, timeout=timeout, state=state)
    
    async def wait_for_load_state(self, state: str = "load", timeout: int = 30000) -> None:
        """Wait for page load state."""
        if not self._page:
            raise RuntimeError("No page available")
        await self._page.wait_for_load_state(state, timeout=timeout)


# Convenience function for dependency injection
async def get_browser_manager() -> BrowserManager:
    """Get the browser manager instance."""
    return await BrowserManager.get_instance()
