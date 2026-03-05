"""
Browser Manager - Playwright browser lifecycle management.

Handles browser instance creation, context management, and cleanup.
Supports both headless (automated) and headed (manual auth) modes.

Features:
- Singleton pattern with async lock
- Request-level concurrency lock (prevents two API calls fighting over the page)
- Automatic crash recovery (detects dead Chromium and reinitializes)
- Resource-optimized launch args for low-resource servers (4GB RAM / 2 CPU)

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

CHROME_DEBUG_PORT = 9222  # Must match tray.py CHROME_DEBUG_PORT


class BrowserManager:
    """
    Manages Playwright browser lifecycle.

    Provides singleton-like access to browser instance with support for:
    - Headless mode for automated operations
    - Headed mode for manual authentication (requires Xvfb on Linux)
    - Download handling
    - Screenshot capture on errors
    - Request-level concurrency control
    - Automatic crash recovery
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
        self._owns_browser = False  # False when connected via connect_over_cdp (tray owns Chrome)
        # Request-level lock: prevents concurrent API calls from fighting over the browser
        self._request_lock = asyncio.Lock()

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

    @property
    def request_lock(self) -> asyncio.Lock:
        """Get the request-level concurrency lock.

        Use this to prevent concurrent API calls from using the browser simultaneously.
        Example:
            async with browser_manager.request_lock:
                # Only one request can use the browser at a time
                await automation.download_report(...)
        """
        return self._request_lock

    def _get_launch_args(self, headless: bool) -> list[str]:
        """
        Build Chromium launch arguments optimized for the current environment.

        These flags are specifically tuned for:
        - DigitalOcean 4GB RAM / 2 CPU droplets
        - Docker containers with Xvfb
        - Bypassing anti-bot detection

        Returns:
            List of Chromium command-line flags
        """
        # Base args for all modes
        launch_args = [
            "--disable-blink-features=AutomationControlled",  # Hide automation signals
            "--no-sandbox",                    # Required in Docker containers
            "--disable-setuid-sandbox",        # Required in Docker containers
            # NOTE: --disable-dev-shm-usage is intentionally omitted.
            # We allocate shm_size: '2gb' in docker-compose so Chromium can use
            # /dev/shm for rendering. Adding --disable-dev-shm-usage would tell
            # Chromium to ignore /dev/shm entirely (using /tmp instead), which
            # causes partial page rendering on low-resource servers.
            "--disable-gpu",                   # No GPU available in server environments
            "--disable-extensions",            # No browser extensions needed
            "--disable-software-rasterizer",   # Disable software rasterizer (use CPU rendering)
            "--disable-gpu-compositing",       # Disable GPU compositing
        ]

        # Resource optimization flags for server environments
        if os.name != "nt":
            launch_args.extend([
                "--disable-background-timer-throttling",              # Prevent timeout issues
                "--disable-renderer-backgrounding",                   # Keep renderer active
                "--disable-backgrounding-occluded-windows",           # Prevent throttling
                "--disable-ipc-flooding-protection",                  # Prevent IPC throttling
                "--disable-component-extensions-with-background-pages",  # Reduce memory
                "--font-render-hinting=none",                         # Improve font rendering in Xvfb
            ])

        # Headed mode specific args (for Xvfb-based login)
        if not headless:
            launch_args.extend([
                "--window-size=1920,1080",
                "--window-position=0,0",
                "--force-device-scale-factor=1",
            ])

        return launch_args

    async def initialize(self, headless: bool = True) -> None:
        """
        Initialize the browser.

        Args:
            headless: If True, launch headless Chrome for automation.
                      If False, connect to the existing Chrome window launched by tray.py
                      via remote debugging port (bypasses Akamai bot detection).
        """
        if self._is_initialized:
            logger.warning("Browser already initialized, closing existing instance")
            await self.close()

        try:
            self._playwright = await async_playwright().start()

            if not headless:
                # Login mode: connect to existing Chrome launched by tray.py.
                # This bypasses Akamai bot detection — Akamai sees a browser that was
                # already naturally running before any Xero visit.
                logger.info("Connecting to existing Chrome via CDP", port=CHROME_DEBUG_PORT)
                for attempt in range(10):
                    try:
                        self._browser = await self._playwright.chromium.connect_over_cdp(
                            f"http://localhost:{CHROME_DEBUG_PORT}"
                        )
                        break
                    except Exception:
                        if attempt == 9:
                            raise RuntimeError(
                                f"Could not connect to Chrome debug port {CHROME_DEBUG_PORT}. "
                                "Please start the app via tray.py before running automated login."
                            )
                        await asyncio.sleep(1.0)

                self._context = await self._browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    accept_downloads=True,
                )

                # Patch any remaining CDP automation signals that Akamai checks
                await self._context.add_init_script("""
                    (() => {
                        try { delete window.__playwright__binding__; } catch(e) {}
                        try { delete window.__pwInitScripts__; } catch(e) {}
                        try { delete window.__playwright_evaluator__; } catch(e) {}
                        try {
                            Object.defineProperty(navigator, 'webdriver', {
                                get: () => undefined, configurable: true
                            });
                        } catch(e) {}
                        if (!window.chrome) window.chrome = {};
                        if (!window.chrome.runtime) window.chrome.runtime = {};
                    })();
                """)

                self._owns_browser = False
                logger.info("Connected to existing Chrome via CDP")

            else:
                # Automation mode: launch headless Chrome for report downloads etc.
                # These operations use saved session cookies and never hit the login page.
                launch_args = self._get_launch_args(headless)
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=launch_args,
                    channel="chrome",
                )

                self._context = await self._browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    accept_downloads=True,
                )

                self._owns_browser = True
                logger.info("Launched headless Chrome", args_count=len(launch_args))

            self._context.set_default_timeout(settings.playwright_timeout)
            self._page = await self._context.new_page()
            self._is_initialized = True
            self._headless = headless

        except Exception as e:
            logger.error("Failed to initialize browser", error=str(e))
            await self.close()
            raise

    async def ensure_initialized(self, headless: bool = True) -> None:
        """Ensure browser is initialized, starting it if needed.

        Also performs crash recovery: if the browser process died,
        reinitializes automatically.
        """
        if self._is_initialized:
            # Check if browser process is still alive (crash recovery)
            if self._browser and not self._browser.is_connected():
                logger.warning("Browser process crashed, reinitializing...")
                await self._force_cleanup()
                await self.initialize(headless=headless)
            return

        await self.initialize(headless=headless)

    async def _force_cleanup(self) -> None:
        """Force cleanup of browser resources without raising errors.

        Used during crash recovery when the browser process may already be dead.
        """
        self._page = None
        self._context = None
        self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._is_initialized = False
        self._owns_browser = False
        logger.info("Force cleanup completed")

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

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
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

            if self._browser and self._owns_browser:
                # Only close the browser process if we launched it.
                # When connected via connect_over_cdp, tray.py owns the Chrome process.
                await self._browser.close()
            self._browser = None

            if self._playwright:
                await self._playwright.stop()
                self._playwright = None

            self._is_initialized = False
            self._owns_browser = False
            logger.info("Browser closed successfully")

        except Exception as e:
            logger.error("Error closing browser", error=str(e))
            # Force cleanup even if graceful close fails
            await self._force_cleanup()

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
