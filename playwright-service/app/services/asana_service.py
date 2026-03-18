"""
Asana Service - Automates Asana task updates after successful report exports.

After a consolidated report is uploaded to OneDrive, this service:
1. Updates the task assignee and due date
2. Moves the task to the "Ready to Export" section
3. Adds a comment with the OneDrive link

Falls back to sending an email notification if the Asana API calls fail.
"""

import asyncio
from datetime import date, timedelta
from typing import Optional

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

ASANA_API_BASE = "https://app.asana.com/api/1.0"

# Retry config: only for transient errors (5xx / network issues)
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2, 4]  # seconds, exponential backoff


class AsanaService:
    """Handles Asana task updates after report exports."""

    def __init__(self) -> None:
        self._headers = {
            "Authorization": f"Bearer {settings.asana_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def update_task_after_export(
        self,
        task_id_or_url: str,
        onedrive_link: str,
    ) -> dict:
        """
        Update an Asana task after a successful report export.

        Performs:
          1. PUT /tasks/{gid}  — change assignee + set due date
          2. POST /sections/{section_gid}/addTask  — move to "Ready to Export"
          3. POST /tasks/{gid}/stories  — add comment with OneDrive link

        Returns:
            {"success": bool, "error": str | None}
        """
        task_gid = self._extract_task_gid(task_id_or_url)
        due_date = self._calculate_due_date()

        log = logger.bind(task_gid=task_gid, due_date=str(due_date))

        # 1. Update assignee + due date
        log.info("Updating Asana task assignee and due date")
        result = await self._api_call_with_retry(
            method="PUT",
            url=f"{ASANA_API_BASE}/tasks/{task_gid}",
            json={
                "data": {
                    "assignee": settings.asana_reassignee_gid,
                    "due_on": due_date.isoformat(),
                }
            },
        )
        if not result["success"]:
            return result

        # 2. Move to "Ready to Export" section
        if settings.asana_ready_to_export_section_gid:
            log.info("Moving task to Ready to Export section")
            result = await self._api_call_with_retry(
                method="POST",
                url=f"{ASANA_API_BASE}/sections/{settings.asana_ready_to_export_section_gid}/addTask",
                json={"data": {"task": task_gid}},
            )
            if not result["success"]:
                return result
        else:
            log.warning("ASANA_READY_TO_EXPORT_SECTION_GID not set — skipping section move")

        # 3. Add comment
        comment = (
            f"Hi Ms. {settings.asana_reassignee_name}, files has been exported to the link below:\n"
            f"{onedrive_link}\n\n"
            f"Thanks!"
        )
        log.info("Adding comment to Asana task")
        result = await self._api_call_with_retry(
            method="POST",
            url=f"{ASANA_API_BASE}/tasks/{task_gid}/stories",
            json={"data": {"text": comment}},
        )
        if not result["success"]:
            return result

        log.info("Asana task updated successfully")
        return {"success": True, "error": None}

    async def send_fallback_email(self, onedrive_link: str, error: str) -> None:
        """
        Send a fallback email when Asana update fails after all retries.

        Uses Office 365 SMTP (smtp.office365.com:587).
        Only sends if all required SMTP settings are configured.
        """
        if not all([
            settings.smtp_email,
            settings.smtp_password,
            settings.smtp_fallback_email,
        ]):
            logger.warning(
                "Fallback email skipped: SMTP settings not fully configured",
                has_email=bool(settings.smtp_email),
                has_password=bool(settings.smtp_password),
                has_fallback=bool(settings.smtp_fallback_email),
            )
            return

        try:
            import aiosmtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg["From"] = settings.smtp_email
            msg["To"] = settings.smtp_fallback_email
            msg["Subject"] = "[Xero Automation] Report ready — Asana update failed"

            body = (
                f"The Xero report export completed successfully, but the Asana task could not be updated automatically.\n\n"
                f"OneDrive link:\n{onedrive_link}\n\n"
                f"Error:\n{error}\n\n"
                f"Please update the Asana task manually:\n"
                f"  - Change assignee to {settings.asana_reassignee_name}\n"
                f"  - Set due date to nearest Friday (or Monday if today is Friday)\n"
                f"  - Move task to 'Ready to Export' section\n"
                f"  - Add comment with the OneDrive link above"
            )
            msg.attach(MIMEText(body, "plain"))

            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_email,
                password=settings.smtp_password,
                start_tls=True,
            )
            logger.info(
                "Fallback email sent",
                to=settings.smtp_fallback_email,
            )
        except Exception as e:
            logger.error("Failed to send fallback email", error=str(e))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_task_gid(self, task_id_or_url: str) -> str:
        """
        Extract the numeric task GID from either a raw GID or a full Asana URL.

        Examples:
            "1187099860745007"                              → "1187099860745007"
            "https://app.asana.com/1/.../task/1187099..."  → "1187099..."
        """
        if task_id_or_url.startswith("http"):
            # URL format: .../task/{gid} or .../task/{gid}/...
            parts = task_id_or_url.split("/task/")
            if len(parts) < 2:
                raise ValueError(f"Cannot extract task GID from URL: {task_id_or_url}")
            gid = parts[-1].split("/")[0].split("?")[0]
            return gid
        return task_id_or_url.strip()

    def _calculate_due_date(self, reference_date: Optional[date] = None) -> date:
        """
        Calculate the due date based on the day the task was assigned.

        Rules:
            Mon–Thu → that week's Friday
            Friday  → next Monday
            Sat–Sun → next Friday
        """
        today = reference_date or date.today()
        weekday = today.weekday()  # 0=Mon, 1=Tue, ..., 4=Fri, 5=Sat, 6=Sun

        if weekday == 4:        # Friday → next Monday
            return today + timedelta(days=3)
        elif weekday == 5:      # Saturday → next Friday
            return today + timedelta(days=6)
        elif weekday == 6:      # Sunday → next Friday
            return today + timedelta(days=5)
        else:                   # Mon–Thu → this Friday
            return today + timedelta(days=4 - weekday)

    async def _api_call_with_retry(
        self,
        method: str,
        url: str,
        json: dict,
    ) -> dict:
        """
        Make an Asana API call with retry logic for transient errors.

        - Retries up to _MAX_RETRIES times on 5xx / network errors
        - Fails immediately on 4xx errors (client errors won't be fixed by retrying)
        """
        last_error: str = ""

        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=self._headers,
                        json=json,
                    )

                if response.status_code in (200, 201):
                    return {"success": True, "error": None}

                # 4xx: client error — no point retrying
                if 400 <= response.status_code < 500:
                    error_msg = f"Asana API client error {response.status_code}: {response.text}"
                    logger.error("Asana API 4xx error (not retrying)", url=url, status=response.status_code)
                    return {"success": False, "error": error_msg}

                # 5xx: server error — retry
                last_error = f"Asana API server error {response.status_code}: {response.text}"
                logger.warning(
                    "Asana API 5xx error, will retry",
                    url=url,
                    status=response.status_code,
                    attempt=attempt + 1,
                )

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_error = f"Network error: {str(e)}"
                logger.warning(
                    "Asana API network error, will retry",
                    url=url,
                    error=str(e),
                    attempt=attempt + 1,
                )

            # Wait before next retry (skip wait on last attempt)
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[attempt])

        return {"success": False, "error": last_error}


# Singleton instance
_asana_service: Optional[AsanaService] = None


def get_asana_service() -> AsanaService:
    """Get the singleton AsanaService instance."""
    global _asana_service
    if _asana_service is None:
        _asana_service = AsanaService()
    return _asana_service
