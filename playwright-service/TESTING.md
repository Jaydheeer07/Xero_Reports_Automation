# Xero Reports Automation - Testing Guide

This guide covers how to test the Playwright service both with and without Docker, including the authentication flow, report downloads, and selector refinement.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Testing with Docker](#testing-with-docker)
3. [Testing without Docker (Local Development)](#testing-without-docker-local-development)
4. [API Endpoints Reference](#api-endpoints-reference)
5. [Authentication Flow](#authentication-flow)
6. [Testing Report Downloads](#testing-report-downloads)
7. [Selector Refinement Guide](#selector-refinement-guide)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Software

- **Python 3.10+** (for local development)
- **Docker & Docker Compose** (for containerized testing)
- **PowerShell** or **curl** (for API testing)
- **A Xero account** with access to at least one organisation

### Environment Variables

Create a `.env` file in the `playwright-service` directory:

```env
# Database (for local development without Docker)
DATABASE_URL=postgresql+asyncpg://xero_user:xero_password@localhost:5432/xero_automation

# Encryption key (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
ENCRYPTION_KEY=your-generated-fernet-key-here

# Playwright settings
PLAYWRIGHT_TIMEOUT=30000

# Directories
DOWNLOAD_DIR=./downloads
SCREENSHOT_DIR=./screenshots
SESSION_DIR=./sessions

# Logging
LOG_LEVEL=DEBUG
```

---

## Testing with Docker

Docker testing is ideal for verifying the service runs correctly in a containerized environment. However, **headed browser mode won't display** in Docker, so authentication must be done differently.

### 1. Start the Services

```powershell
cd playwright-service
docker-compose up --build -d
```

### 2. Verify Services are Running

```powershell
# Check container status
docker ps

# Check logs
docker logs playwright-service --tail 20
docker logs xero-postgres --tail 10
```

### 3. Test Health Endpoint

```powershell
Invoke-RestMethod -Method GET -Uri "http://localhost:8000/api/health"
```

**Expected Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "browser": {
    "initialized": false,
    "headless": true,
    "browser_connected": false,
    "context_active": false,
    "page_active": false
  }
}
```

### 4. Test Browser Start/Stop

```powershell
# Start browser (headless)
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/browser/start?headless=true"

# Check health again - browser should be initialized
Invoke-RestMethod -Method GET -Uri "http://localhost:8000/api/health"

# Stop browser
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/browser/stop"
```

### 5. Test Client CRUD

```powershell
# Create a client
$body = @{
    tenant_id = "test-tenant-123"
    tenant_name = "Test Organisation"
    display_name = "Test Org"
} | ConvertTo-Json

Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/clients" -Body $body -ContentType "application/json"

# List clients
Invoke-RestMethod -Method GET -Uri "http://localhost:8000/api/clients"

# Get specific client
Invoke-RestMethod -Method GET -Uri "http://localhost:8000/api/clients/1"
```

### 6. Stop Docker Services

```powershell
docker-compose down
```

### Docker Limitations

- **No headed browser display** - Cannot see the browser window
- **Authentication requires workaround** - Must authenticate locally first, then copy session to Docker
- **Best for**: CI/CD, production deployment, headless automation after auth is set up

---

## Testing without Docker (Local Development)

Local development allows you to see the browser window, which is essential for:
- Initial Xero authentication (MFA)
- Debugging automation scripts
- Refining CSS selectors

### 1. Set Up PostgreSQL Locally

**Option A: Use Docker for PostgreSQL only**
```powershell
docker run -d --name xero-postgres `
  -e POSTGRES_USER=xero_user `
  -e POSTGRES_PASSWORD=xero_password `
  -e POSTGRES_DB=xero_automation `
  -p 5432:5432 `
  -v ${PWD}/scripts/init_db.sql:/docker-entrypoint-initdb.d/init_db.sql `
  postgres:15-alpine
```

**Option B: Use existing PostgreSQL**
Update your `.env` file with the correct `DATABASE_URL`.

### 2. Set Up Python Environment

```powershell
cd playwright-service

# Create virtual environment
python -m venv venv

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### 3. Generate Encryption Key

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output to your `.env` file as `ENCRYPTION_KEY`.

### 4. Start the FastAPI Server

```powershell
# Make sure virtual environment is activated
.\venv\Scripts\Activate.ps1

# Start server with auto-reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Verify Server is Running

```powershell
Invoke-RestMethod -Method GET -Uri "http://localhost:8000/api/health"
```

---

## API Endpoints Reference

### Health & Browser Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Service health check |
| POST | `/api/browser/start?headless=true` | Start browser |
| POST | `/api/browser/stop` | Stop browser |
| POST | `/api/browser/restart?headless=true` | Restart browser |

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/setup` | Start manual login (opens headed browser) |
| POST | `/api/auth/complete` | Save session after login |
| GET | `/api/auth/status` | Check authentication status |
| POST | `/api/auth/restore` | Restore session from database |
| GET | `/api/auth/tenants` | List available organisations |
| POST | `/api/auth/switch-tenant?tenant_name=X` | Switch to organisation |
| DELETE | `/api/auth/session` | Delete stored session |

### Clients

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/clients` | List all clients |
| GET | `/api/clients/{id}` | Get client by ID |
| POST | `/api/clients` | Create client |
| PUT | `/api/clients/{id}` | Update client |
| DELETE | `/api/clients/{id}` | Delete client (soft delete) |

### Reports

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/reports/activity-statement` | Download Activity Statement |
| POST | `/api/reports/payroll-activity-summary` | Download Payroll Activity Summary |
| POST | `/api/reports/batch` | Batch download for multiple tenants |
| GET | `/api/reports/files` | List downloaded files |
| GET | `/api/reports/logs` | View download history |
| GET | `/api/reports/download/{filename}` | Download a file |

---

## Authentication Flow

### Step 1: Start Manual Login

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/auth/setup"
```

**What happens:**
- A **visible browser window** opens to `https://login.xero.com`
- You must manually log in with your Xero credentials
- Complete MFA if prompted
- Wait for the Xero dashboard to load

**Expected Response:**
```json
{
  "success": true,
  "status": "waiting_for_login",
  "message": "Browser opened. Please log into Xero manually.",
  "instructions": [
    "1. Enter your Xero email and password",
    "2. Complete MFA if prompted",
    "3. Wait for the dashboard to load",
    "4. Call POST /api/auth/complete to save the session"
  ],
  "current_url": "https://login.xero.com"
}
```

### Step 2: Complete Login (After Manual Login)

Once you've logged in and see the Xero dashboard:

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/auth/complete"
```

**What happens:**
- Captures all cookies from the browser
- Encrypts and stores them in the database
- Restarts browser in headless mode
- Restores session in headless browser

**Expected Response:**
```json
{
  "success": true,
  "message": "Session saved and browser switched to headless mode",
  "current_tenant": {
    "name": "Your Organisation Name"
  },
  "session_restored": true
}
```

### Step 3: Verify Authentication Status

```powershell
Invoke-RestMethod -Method GET -Uri "http://localhost:8000/api/auth/status"
```

**Expected Response (authenticated):**
```json
{
  "logged_in": true,
  "current_tenant": {
    "name": "Your Organisation Name"
  },
  "needs_reauth": false,
  "session_status": {
    "has_session": true,
    "is_valid": true,
    "expires_at": "2025-01-27T14:00:00",
    "cookie_count": 15
  }
}
```

### Step 4: Restore Session (On Service Restart)

If the service restarts, restore the session:

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/auth/restore"
```

---

## Testing Report Downloads

### Prerequisites

1. You must be authenticated (complete the auth flow above)
2. You need a valid tenant/organisation name

### Test Activity Statement Download

```powershell
$body = @{
    tenant_id = "your-tenant-id"
    tenant_name = "Your Organisation Name"
    find_unfiled = $true
} | ConvertTo-Json

Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/reports/activity-statement" -Body $body -ContentType "application/json"
```

**Expected Response (success):**
```json
{
  "success": true,
  "file_path": "/app/downloads/activity_statement_Your_Organisation_Name_20250126_143000.xlsx",
  "file_name": "activity_statement_Your_Organisation_Name_20250126_143000.xlsx",
  "tenant_name": "Your Organisation Name",
  "report_type": "activity_statement"
}
```

### Test Payroll Activity Summary Download

```powershell
$body = @{
    tenant_id = "your-tenant-id"
    tenant_name = "Your Organisation Name"
} | ConvertTo-Json

Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/reports/payroll-activity-summary" -Body $body -ContentType "application/json"
```

### Test Batch Download

```powershell
# First, create clients in the database
$client1 = @{
    tenant_id = "tenant-1"
    tenant_name = "Organisation One"
    display_name = "Org 1"
} | ConvertTo-Json

Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/clients" -Body $client1 -ContentType "application/json"

# Then run batch download
$batch = @{
    reports = @("activity_statement", "payroll_summary")
} | ConvertTo-Json

Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/reports/batch" -Body $batch -ContentType "application/json"
```

### Check Downloaded Files

```powershell
# List all downloaded files
Invoke-RestMethod -Method GET -Uri "http://localhost:8000/api/reports/files"

# View download logs
Invoke-RestMethod -Method GET -Uri "http://localhost:8000/api/reports/logs"
```

---

## Selector Refinement Guide

The automation uses CSS selectors to find elements in the Xero UI. These selectors may need adjustment based on the actual Xero interface.

### Where Selectors Are Defined

File: `app/services/xero_automation.py`

```python
SELECTORS = {
    "org_switcher": [
        '[data-testid="org-switcher"]',
        '[data-automationid="org-switcher"]',
        'button[aria-label*="organisation"]',
        # ... more fallbacks
    ],
    # ... more elements
}
```

### How to Find Correct Selectors

1. **Run automation and check screenshots**
   - Screenshots are saved to `./screenshots/` on failure
   - Review what the page looks like when it fails

2. **Inspect Xero UI manually**
   - Open Xero in your browser
   - Right-click on the element you need
   - Click "Inspect" to open DevTools
   - Look for reliable attributes:
     - `data-testid` (most stable)
     - `data-automationid`
     - `aria-label`
     - Unique class names
     - Text content

3. **Test selectors in browser console**
   ```javascript
   // Test if selector finds the element
   document.querySelector('[data-testid="org-switcher"]')
   
   // Test text-based selector
   document.evaluate("//button[contains(text(), 'Reports')]", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue
   ```

4. **Update selectors in code**
   - Add new selectors to the beginning of the array (tried first)
   - Keep old selectors as fallbacks

### Common Selector Patterns

```python
# By data attribute (most reliable)
'[data-testid="element-name"]'
'[data-automationid="element-name"]'

# By aria label
'button[aria-label="Export"]'
'button[aria-label*="organisation"]'  # contains

# By text content (Playwright-specific)
'text=Reports'
'button:has-text("Export")'
'a:has-text("Activity Statement")'

# By role
'[role="menuitem"]'
'nav[role="navigation"]'

# Combined
'button[data-testid="export"]:has-text("Excel")'
```

### Enable Debug Screenshots

To capture screenshots at every step:

```python
# In xero_automation.py, set:
automation._debug_screenshots = True
```

Or modify the `XeroAutomation` class:

```python
def __init__(self, browser_manager: BrowserManager):
    self.browser = browser_manager
    self.file_manager = get_file_manager()
    self._debug_screenshots = True  # Enable debug mode
```

---

## Troubleshooting

### Common Issues

#### 1. "Database connection failed"

**Cause:** PostgreSQL is not running or connection string is wrong.

**Fix:**
```powershell
# Check if PostgreSQL is running
docker ps | Select-String postgres

# Or start it
docker-compose up -d postgres
```

#### 2. "Invalid encryption key"

**Cause:** The `ENCRYPTION_KEY` environment variable is not a valid Fernet key.

**Fix:**
```powershell
# Generate a new key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Update .env or docker-compose.yml with the new key
```

#### 3. "No browser page available"

**Cause:** Browser not started or crashed.

**Fix:**
```powershell
# Start the browser
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/browser/start?headless=false"
```

#### 4. "Could not find organisation switcher"

**Cause:** Selector doesn't match Xero's current UI.

**Fix:**
1. Check screenshot in `./screenshots/`
2. Manually inspect Xero UI for correct selector
3. Update `SELECTORS["org_switcher"]` in `xero_automation.py`

#### 5. "Session cookies are invalid or expired"

**Cause:** Xero session has expired (typically after 30 days).

**Fix:**
```powershell
# Delete old session
Invoke-RestMethod -Method DELETE -Uri "http://localhost:8000/api/auth/session"

# Re-authenticate
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/auth/setup"
# ... complete login manually ...
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/auth/complete"
```

#### 6. Browser window doesn't appear (local development)

**Cause:** Running in headless mode.

**Fix:**
```powershell
# Start browser in headed mode
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/browser/start?headless=false"

# Or use auth/setup which forces headed mode
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/auth/setup"
```

### Viewing Logs

**Docker:**
```powershell
docker logs playwright-service -f
```

**Local:**
Logs appear in the terminal where `uvicorn` is running.

### Checking Screenshots

Screenshots are saved when automation fails:
- Docker: `./screenshots/` (mounted volume)
- Local: `./screenshots/`

---

## Quick Test Checklist

### Docker Environment

- [ ] `docker-compose up --build -d` succeeds
- [ ] Health endpoint returns "healthy"
- [ ] Database shows "connected"
- [ ] Browser start/stop works
- [ ] Client CRUD operations work

### Local Environment

- [ ] PostgreSQL is accessible
- [ ] Virtual environment activated
- [ ] `uvicorn` starts without errors
- [ ] Health endpoint returns "healthy"
- [ ] Auth setup opens browser window
- [ ] Can log into Xero manually
- [ ] Auth complete saves session
- [ ] Auth status shows logged in
- [ ] Report download attempts (may fail on selectors)
- [ ] Screenshots are captured on failure

### Authentication Flow

- [ ] `/api/auth/setup` opens browser
- [ ] Manual Xero login works
- [ ] `/api/auth/complete` captures cookies
- [ ] `/api/auth/status` shows logged in
- [ ] `/api/auth/restore` works after restart

### Report Downloads

- [ ] Activity Statement download attempted
- [ ] Payroll Activity Summary download attempted
- [ ] Files appear in downloads directory
- [ ] Download logs are recorded

---

## Next Steps

After completing testing:

1. **Refine selectors** based on actual Xero UI
2. **Test with multiple tenants** to verify switching works
3. **Set up n8n integration** (Phase 5)
4. **Deploy to DigitalOcean** (Phase 6)
