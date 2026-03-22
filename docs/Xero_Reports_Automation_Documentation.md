# Xero Reports Automation

> Automated accounting report downloads from Xero — browser to OneDrive with zero manual steps.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Prerequisites & Configuration](#3-prerequisites--configuration)
4. [Getting Started](#4-getting-started)
5. [Core Workflows](#5-core-workflows)
6. [API Reference](#6-api-reference)
7. [Integrations](#7-integrations)
8. [Database Schema](#8-database-schema)
9. [Security](#9-security)
10. [Deployment](#10-deployment)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Overview

**Xero Reports Automation** is a FastAPI-based microservice that eliminates manual report downloading from Xero. It automates the complete workflow end-to-end:

1. Logs into Xero using browser automation
2. Navigates to the correct reports for the specified client and period
3. Downloads the **Activity Statement (BAS)** and **Payroll Activity Summary**
4. Merges both reports into a single consolidated Excel workbook
5. Copies the file to the client's **OneDrive** sync folder
6. Updates the relevant **Asana task** with the assignee, due date, and a link to the file

### Key Capabilities

| Capability | Description |
|------------|-------------|
| **Automated Browser Control** | Uses Patchright (a patched build of Playwright) to navigate Xero's web UI, bypassing Akamai bot detection |
| **Activity Statement Downloads** | Downloads BAS reports for any period across multiple Xero organisations |
| **Payroll Summary Downloads** | Downloads Payroll Activity Summary reports for any month/year |
| **Excel Consolidation** | Merges multiple report sheets into a single workbook, preserving all cell data, formatting, and column widths |
| **OneDrive Auto-Upload** | Copies consolidated files directly to the client's synced OneDrive folder on the Windows host |
| **Asana Task Updates** | Reassigns tasks, sets due dates, moves to the correct section, and adds an @mention comment with the OneDrive link |
| **Multi-Tenant Support** | Manages multiple Xero organisations from a single authenticated browser session |
| **Background Job Processing** | Runs long-running reports asynchronously — clients poll for completion status |
| **Session Persistence** | Encrypted browser cookies are stored in PostgreSQL — no re-login required after service restarts |
| **Batch Downloads** | Downloads reports for multiple clients sequentially in a single API call |

---

## 2. Architecture

The system runs a single **FastAPI** application that controls a **Playwright/Patchright** browser to automate Xero's web interface. Since Xero uses **Akamai bot detection**, the service uses a patched browser build and a deferred connection strategy to appear as a real user.

### System Architecture Diagram

![System Architecture](./architecture.png)

> Open `architecture.excalidraw` in [Excalidraw](https://excalidraw.com) for an interactive view.

### Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Web Framework** | FastAPI 0.109 | REST API server, route handling, background jobs |
| **Browser Automation** | Patchright (patched Playwright) | Xero UI navigation, bypasses Akamai bot detection |
| **Database** | PostgreSQL 15 / Supabase | Session storage, client registry, download audit logs |
| **ORM** | SQLAlchemy (async) | Async database access layer |
| **File Processing** | openpyxl | Excel consolidation, formatting preservation |
| **Encryption** | Python `cryptography` (Fernet) | AES-128 encryption of stored browser cookies |
| **Containerisation** | Docker / Docker Compose | Service packaging and deployment |
| **Structured Logging** | structlog | JSON-formatted logs for cloud observability |
| **Task Notifications** | Asana API + Office 365 SMTP | Task updates and fallback email alerts |

### High-Level Data Flow

```
HTTP Request / n8n Webhook / UI Dashboard
              ↓
        FastAPI Routes
       /     |      \
      ↓      ↓       ↓
  Browser  File    Asana     Session
  Manager  Manager Service   Service
      ↓      ↓       ↓          ↓
    Xero  OneDrive  Asana   PostgreSQL
```

---

## 3. Prerequisites & Configuration

### System Requirements

- **Docker Engine 24+** and **Docker Compose v2**
- **2 GB shared memory** available on the host (required by Chromium)
- **Windows host** for OneDrive filesystem integration (OneDrive sync client must be running)
- **Node.js** is not required — the service is Python-only

### Environment Variables

Copy `.env.example` to `.env` in the `playwright-service/` directory and fill in all required values before starting.

#### Database

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `DATABASE_URL` | **Yes** | PostgreSQL connection string | `postgresql+asyncpg://user:pass@localhost:5432/xero` |
| `PGBOUNCER_MODE` | No | Set `true` when using Supabase (disables prepared statement cache) | `false` |

#### Security

| Variable | Required | Description | How to Generate |
|----------|----------|-------------|----------------|
| `ENCRYPTION_KEY` | **Yes** | Fernet symmetric key for encrypting stored cookies | Run: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `API_KEY` | **Yes** | Secret required in the `X-API-Key` header for all API calls | Any strong random string (e.g., a UUID) |

#### Xero Credentials

| Variable | Required | Description |
|----------|----------|-------------|
| `XERO_EMAIL` | Yes (automated login) | Email address for the Xero account |
| `XERO_PASSWORD` | Yes (automated login) | Password for the Xero account |
| `XERO_SECURITY_ANSWER_1` | Yes (automated login) | Answer to Xero MFA security question 1 |
| `XERO_SECURITY_ANSWER_2` | No | Answer to security question 2 (if configured in Xero) |
| `XERO_SECURITY_ANSWER_3` | No | Answer to security question 3 (if configured in Xero) |

#### OneDrive

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `ONE_DRIVE_FOLDER_ORIGIN` | Yes (OneDrive upload) | Base Windows path to the local OneDrive sync folder | `C:\Users\DexterVM\OneDrive - Dexterous Group\` |

#### Asana Integration

| Variable | Required | Description | How to Find |
|----------|----------|-------------|-------------|
| `ASANA_API_KEY` | Yes (Asana updates) | Asana personal access token | Asana → My Profile → Apps → Personal Access Tokens |
| `ASANA_REASSIGNEE_GID` | Yes (Asana updates) | GID of the user to reassign tasks to after export | From the Asana API or the user's profile URL |
| `ASANA_READY_TO_EXPORT_SECTION_GID` | Yes (Asana updates) | GID of the "Ready to Export" section in Asana | From the Asana API |

#### Email Fallback (SMTP)

| Variable | Required | Description |
|----------|----------|-------------|
| `SMTP_EMAIL` | No | Office 365 address used to send fallback alerts |
| `SMTP_PASSWORD` | No | Office 365 password for the above account |
| `SMTP_FALLBACK_EMAIL` | No | Recipient address if the Asana update fails after all retries |

#### Playwright / Browser Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `PLAYWRIGHT_TIMEOUT` | `30000` | Browser action timeout in milliseconds |
| `PLAYWRIGHT_HEADLESS` | `false` | Run browser headless (set to `true` on servers) |
| `SCREENSHOT_RETENTION_DAYS` | `7` | Days to keep error screenshots before auto-deletion |

---

## 4. Getting Started

### Local Development Setup

```bash
# 1. Clone the repository
git clone <repository-url>
cd Xero_Reports_Automation/playwright-service

# 2. Configure environment variables
cp .env.example .env
# Open .env and fill in your credentials

# 3. Start the services (FastAPI app + PostgreSQL)
docker-compose up --build

# 4. Verify the service is running
curl http://localhost:8000/health
```

Once running:
- **API base URL:** `http://localhost:8000/api`
- **Interactive Swagger docs:** `http://localhost:8000/docs`
- **Health check:** `http://localhost:8000/health`

### First-Time Authentication

Before downloading any reports, the service must be authenticated with Xero. There are two options:

**Option A — Manual Login (Recommended for initial setup)**

This is the safest method to avoid Akamai bot detection.

```
1. POST /api/auth/setup          → Opens Xero login in a browser window
2. (Log in manually in the browser that appears, including MFA)
3. POST /api/auth/complete        → Captures cookies and saves them to the database
```

**Option B — Automated Login**

For headless or unattended environments.

```
1. Ensure XERO_EMAIL, XERO_PASSWORD, and XERO_SECURITY_ANSWER_* are set in .env
2. POST /api/auth/automated-login  → Logs in automatically
```

Once authenticated, the session is encrypted and persisted in PostgreSQL. It will be **automatically restored** after service restarts — no re-login needed until the session expires (typically 8–24 hours).

---

## 5. Core Workflows

### Workflow 1 — Manual Login (Akamai Bot Bypass)

Xero uses **Akamai** bot detection. This workflow is designed to produce a genuine browser fingerprint:

1. `POST /api/auth/setup` — FastAPI opens Xero's login page using Chrome's **DevTools HTTP API** (not CDP, which would trigger Akamai)
2. The user manually completes login and answers MFA security questions in the browser window
3. `POST /api/auth/complete` — FastAPI establishes a CDP connection (Akamai checks are now done), captures all cookies
4. Cookies are encrypted with **Fernet** and stored in PostgreSQL
5. On all subsequent requests, the session is auto-restored from the database

> **Why this matters:** Akamai detects automated login via CDP signals. A real user completing the first login creates a legitimate session that the service can reuse safely.

---

### Workflow 2 — Automated Login

For unattended scheduling where manual interaction isn't possible:

1. Credentials from `.env` (`XERO_EMAIL`, `XERO_PASSWORD`) are used to fill the login form
2. Xero's MFA challenge is answered using `XERO_SECURITY_ANSWER_1/2/3`
3. The session is saved to the database after successful login

> **Note:** On fresh environments, automated login is more likely to trigger Akamai. It's recommended to complete a manual login first, then rely on automated login only for session renewal.

---

### Workflow 3 — Consolidated Report (Primary Workflow)

This is the main use case: download both reports for a client and consolidate them.

![Consolidated Report Workflow](./workflow.png)

> Open `workflow.excalidraw` for an interactive view.

**Endpoint:** `POST /api/reports/run` (async with job polling) or `POST /api/reports/consolidated` (synchronous)

| Step | Action | Detail |
|------|--------|--------|
| 1 | **Receive request** | Body includes `tenant_id`, `period` (e.g. `"October 2025"`), and optional `asana_task_id` |
| 2 | **Create background job** | Returns `{ "job_id": "..." }` immediately. Client polls `GET /api/reports/job/{job_id}` for status |
| 3 | **Restore browser session** | Loads encrypted cookies from PostgreSQL into the Patchright browser |
| 4 | **Switch Xero tenant** | Navigates to the Xero org selector and clicks the target organisation |
| 5 | **Download Activity Statement** | Goes to Reports → Activity Statement, finds the correct period, downloads as Excel |
| 6 | **Download Payroll Summary** | Goes to Reports → Payroll Activity Summary, selects month/year, downloads as Excel |
| 7 | **Consolidate Excel files** | Merges both files into a single workbook preserving all formatting, merged cells, and column widths |
| 8 | **Copy to OneDrive** | Writes the consolidated file to `{ONE_DRIVE_FOLDER_ORIGIN}/{client.onedrive_folder}/` |
| 9 | **Update Asana task** | Reassigns task, sets due date, moves to "Ready to Export", adds @mention comment with OneDrive link |
| 10 | **Log to PostgreSQL** | Records status, file path, file size, timestamps, and OneDrive path in `download_logs` |

**Polling for job status:**

```
GET /api/reports/job/{job_id}

Response:
{
  "status": "running" | "completed" | "failed",
  "result": { ... }   ← populated when status is "completed"
}
```

---

### Workflow 4 — Batch Report Download

Downloads reports for multiple clients in a single API call:

1. `POST /api/reports/batch` with a list of client configurations
2. Reports are downloaded **sequentially** (one browser session, one client at a time)
3. Each client's report is saved and optionally uploaded to OneDrive
4. Results are returned per-client when all downloads complete

---

## 6. API Reference

All endpoints require the `X-API-Key: {API_KEY}` header. Requests without a valid key return `403 Forbidden`.

**Base URL:** `http://localhost:8000/api`

Interactive Swagger documentation: `http://localhost:8000/docs`

---

### Authentication — `/auth`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/setup` | Open Xero login in the browser (Step 1 of manual login) |
| `POST` | `/auth/complete` | Capture cookies after manual login (Step 2) |
| `POST` | `/auth/restore` | Reload saved session into the browser |
| `GET` | `/auth/status` | Check authentication status and which tenant is active |
| `GET` | `/auth/tenants` | List all available Xero organisations |
| `POST` | `/auth/switch-tenant` | Switch the active Xero organisation |
| `POST` | `/auth/automated-login` | Log in automatically using credentials from `.env` |
| `POST` | `/auth/logout` | Log out of Xero |
| `DELETE` | `/auth/session` | Delete stored session cookies from the database |

---

### Reports — `/reports`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/reports/activity-statement` | Download Activity Statement (BAS) for a given period |
| `POST` | `/reports/payroll-activity-summary` | Download Payroll Activity Summary for a month/year |
| `POST` | `/reports/consolidated` | Download both reports and consolidate into one Excel file (synchronous) |
| `POST` | `/reports/batch` | Download reports for multiple clients sequentially |
| `POST` | `/reports/run` | Start a background consolidated report job (recommended for production) |
| `GET` | `/reports/job/{job_id}` | Poll the status of a background job |
| `GET` | `/reports/download/{filename}` | Download a previously generated file |
| `GET` | `/reports/files` | List all downloaded files with metadata |
| `GET` | `/reports/logs` | Retrieve download audit logs from the database |

**Example — Start a consolidated report job:**

```json
POST /api/reports/run
{
  "tenant_id": "abc123-xero-org-id",
  "period": "October 2025",
  "asana_task_id": "1234567890123456"
}
```

**Example — Poll job status:**

```json
GET /api/reports/job/job_abc123

{
  "status": "completed",
  "result": {
    "consolidated_file": "ACME_Oct2025_Consolidated.xlsx",
    "onedrive_path": "C:\\Users\\DexterVM\\OneDrive - Dexterous Group\\Clients\\ACME\\...",
    "asana_updated": true
  }
}
```

---

### Clients — `/clients`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/clients/` | List all active clients |
| `GET` | `/clients/{client_id}` | Get a specific client's details |
| `POST` | `/clients/` | Create a new client record |
| `PUT` | `/clients/{client_id}` | Update client details |
| `DELETE` | `/clients/{client_id}` | Soft-delete a client (sets `is_active = false`) |

**Client object:**

```json
{
  "id": 1,
  "tenant_id": "abc123-xero-org-id",
  "tenant_name": "ACME Pty Ltd",
  "tenant_shortcode": "acme01",
  "onedrive_folder": "Clients/ACME",
  "sharepoint_folder_url": "https://...",
  "asana_task_id": "1234567890123456",
  "is_active": true,
  "created_at": "2025-01-15T10:00:00Z",
  "updated_at": "2026-03-01T08:30:00Z"
}
```

---

### Health — `/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Returns service health status |

---

## 7. Integrations

### Xero

| Property | Detail |
|----------|--------|
| **Integration method** | Browser automation (no official Xero API used) |
| **Bot detection bypass** | Patchright build + deferred CDP connection + real Chrome fingerprint |
| **Session storage** | Encrypted cookies in PostgreSQL, auto-restored on restart |
| **Reports available** | Activity Statement (BAS), Payroll Activity Summary |
| **Multi-tenant** | Switches orgs using Xero's built-in organisation selector |

---

### Asana

After a successful OneDrive upload, the service performs these steps via the Asana REST API:

1. **Reassigns** the task to the user configured as `ASANA_REASSIGNEE_GID`
2. **Sets the due date** automatically:
   - Mon–Thu → next Friday
   - Friday → next Monday
   - Sat–Sun → next Friday
3. **Moves** the task to the "Ready to Export" section (`ASANA_READY_TO_EXPORT_SECTION_GID`)
4. **Adds a comment** with an @mention of the assignee and a direct link to the OneDrive file (using Asana HTML rich-text format)

**Retry logic:** Up to 3 attempts with exponential backoff for transient API errors.

**Fallback:** If all 3 Asana attempts fail, an email is sent to `SMTP_FALLBACK_EMAIL` with the error details and the file location.

---

### OneDrive

| Property | Detail |
|----------|--------|
| **Integration method** | Direct filesystem write — no Microsoft API used |
| **Mechanism** | Writes to the local OneDrive sync folder; the OneDrive client handles cloud upload |
| **Path structure** | `{ONE_DRIVE_FOLDER_ORIGIN}/{client.onedrive_folder}/{filename}` |
| **Folder creation** | Missing subdirectories are created automatically |
| **Requirement** | OneDrive desktop client must be running and syncing on the Windows host |

---

### SMTP (Email Fallback)

| Property | Detail |
|----------|--------|
| **When triggered** | Only if all 3 Asana API attempts fail |
| **Provider** | Office 365 (`smtp.office365.com:587` with STARTTLS) |
| **Content** | Error summary, file path, and instructions for manual Asana update |

---

## 8. Database Schema

The service uses three PostgreSQL tables, initialised automatically from `scripts/init_db.sql`.

### `clients` — Xero Organisations

Stores every Xero organisation managed by the system.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer (PK) | Auto-increment primary key |
| `tenant_id` | String (unique) | Xero organisation identifier |
| `tenant_name` | String | Human-readable organisation name |
| `tenant_shortcode` | String (unique) | Short code for fast tenant switching |
| `is_active` | Boolean | Soft-delete flag (`false` = deleted) |
| `onedrive_folder` | String | Relative subfolder path within `ONE_DRIVE_FOLDER_ORIGIN` |
| `sharepoint_folder_url` | String | SharePoint URL embedded in Asana comments |
| `asana_task_id` | String | Asana task GID or full task URL |
| `created_at` | DateTime | Record creation timestamp |
| `updated_at` | DateTime | Last modification timestamp |

---

### `xero_sessions` — Browser Session Storage

Holds the single active Xero browser session (only one row, `id = 1`).

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer (PK, always 1) | Fixed — only one row exists |
| `cookies` | Text | Fernet-encrypted JSON array of browser cookies |
| `oauth_tokens` | Text (nullable) | Encrypted OAuth tokens (if applicable) |
| `expires_at` | DateTime | Session expiry time (used to trigger re-authentication) |
| `updated_at` | DateTime | Last time cookies were saved |

---

### `download_logs` — Audit Trail

Records every report download attempt, whether successful or not.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer (PK) | Auto-increment primary key |
| `client_id` | Integer (FK) | References `clients.id` |
| `report_type` | String | `activity_statement`, `payroll_activity_summary`, or `consolidated_report` |
| `status` | String | `success` or `failed` |
| `file_path` | String | Full local path to the downloaded file |
| `file_name` | String | Filename (original or consolidated) |
| `file_size` | Integer | File size in bytes |
| `error_message` | String | Failure reason (populated only if `status = failed`) |
| `screenshot_path` | String | Path to the error screenshot (auto-deleted after `SCREENSHOT_RETENTION_DAYS`) |
| `started_at` | DateTime | When the download attempt started |
| `completed_at` | DateTime | When the download attempt finished |
| `uploaded_to_onedrive` | Boolean | Whether the file was successfully copied to OneDrive |
| `onedrive_path` | String | Final OneDrive path (populated if `uploaded_to_onedrive = true`) |

---

## 9. Security

### API Authentication

Every endpoint checks for a valid `X-API-Key` header. If missing or incorrect, the server returns `403 Forbidden`. Set `API_KEY` in `.env` to a strong, unique secret.

### Cookie Encryption

Browser cookies are encrypted using **Fernet** (AES-128-CBC + HMAC-SHA256) before being stored in PostgreSQL. The `ENCRYPTION_KEY` must remain secret — if it is lost, all stored sessions are permanently unrecoverable and a new manual login will be required.

### CORS

By default, CORS is configured to allow all origins (suitable for local development). For production, restrict `allow_origins` in `playwright-service/app/main.py` to your specific frontend domains.

### Anti-Bot Techniques

The service uses several layered techniques to appear as a legitimate user to Akamai:

| Technique | What it Does |
|-----------|-------------|
| **Patchright build** | Removes the CDP `Runtime.enable` signal that Akamai uses to detect Playwright |
| **Deferred CDP connection** | During manual login, Chrome is launched via DevTools HTTP API (not CDP) so no automation signals are sent until after the user has logged in |
| **Real browser fingerprint** | Reuses an existing Chrome context with real cookies and browsing history, rather than a fresh incognito session |
| **JavaScript patches** | Removes `navigator.webdriver` and `__playwright__` references from the page after each navigation |
| **Custom user agent** | Sets a realistic Chrome/Windows user agent string |

### Soft Deletes

Clients are never permanently deleted. `DELETE /clients/{id}` sets `is_active = false`, preserving all audit history in `download_logs`.

---

## 10. Deployment

### Local Development

`docker-compose.yml` starts both the FastAPI service and a local PostgreSQL database.

```bash
cd playwright-service
docker-compose up --build
```

**Key Docker settings in `docker-compose.yml`:**

```yaml
# Chromium requires at least 2 GB shared memory
shm_size: '2gb'

# Volume mounts
volumes:
  - ./downloads:/app/downloads       # Downloaded report files
  - ./screenshots:/app/screenshots   # Error screenshots (auto-cleaned)
  - ./sessions:/app/sessions         # Session backup files
```

---

### Production

`docker-compose.prod.yml` connects to an external **Supabase** PostgreSQL database and runs the browser in headless mode.

```bash
cd playwright-service
docker-compose -f docker-compose.prod.yml up -d
```

**Key differences from local:**

| Setting | Local | Production |
|---------|-------|-----------|
| PostgreSQL | Local container | External Supabase (connection pooler) |
| `PLAYWRIGHT_HEADLESS` | `false` | `true` |
| `PGBOUNCER_MODE` | `false` | `true` |
| Ports exposed | `8000`, `5432` | `8000` only |

> **Supabase note:** Set `PGBOUNCER_MODE=true` when using Supabase's connection pooler. This disables SQLAlchemy's prepared statement cache to avoid conflicts with PgBouncer's session management.

---

## 11. Troubleshooting

| Issue | Likely Cause | Fix |
|-------|-------------|-----|
| `403 Forbidden` on all requests | Missing or wrong `X-API-Key` header | Add `X-API-Key: {your API_KEY}` to every request |
| `Browser not initialized` error | Service restarted with no saved session | Call `POST /api/auth/restore` or `POST /api/auth/automated-login` |
| Xero login blocked or redirected | Akamai bot detection triggered | Use the manual login flow: `POST /auth/setup` → log in manually → `POST /auth/complete` |
| Session expires overnight | Xero sessions typically last 8–24 hours | Schedule `POST /api/auth/automated-login` to run before the morning job window |
| Downloaded file fails validation | Xero returned an HTML error page instead of an Excel file | Check that the specified period exists in Xero for that client |
| OneDrive copy fails | `ONE_DRIVE_FOLDER_ORIGIN` is wrong, or OneDrive isn't syncing | Verify the path exists on the Windows host and the OneDrive client is running |
| Asana update fails after 3 retries | Invalid API key, wrong task GID, or network issue | Verify `ASANA_API_KEY` in Asana settings; check the task GID from the Asana task URL |
| `asyncpg InterfaceError` with Supabase | PgBouncer reusing prepared statement IDs | Set `PGBOUNCER_MODE=true` in `.env` |
| Chromium crashes or OOM | Insufficient shared memory in Docker | Ensure `shm_size: '2gb'` is set in `docker-compose.yml` |
| Error screenshots not appearing | Volume mount missing for `screenshots/` | Check that `./screenshots:/app/screenshots` is in the `volumes` section |
| Batch job stops mid-way | One client's download failed, stopping the batch | Check `GET /api/reports/logs` for the failed client's error message |

---

*Last updated: March 2026*

*For questions or issues, contact the development team.*
