# Windows Native Setup Guide

Run the Playwright service directly on Windows without Docker.
No Xvfb, no `/dev/shm` hacks, no container overhead.

**No nginx required.** The app binds to `0.0.0.0:8000` so n8n connects directly
to `http://<your-vm-ip>:8000`.

---

## Why Native Windows Instead of Docker

| | Docker Desktop on Windows | Native Python on Windows |
|---|---|---|
| RAM overhead | ~1.5-2GB (WSL2 + daemon) | ~0MB |
| Xvfb required | Yes (still Linux containers) | **No** |
| `/dev/shm` rendering issues | Still present in WSL2 | **Gone** |
| Available RAM for Chromium | ~5-6GB | **~6-7GB** |
| Rendering stability | Same Linux issues in WSL2 | Playwright's native platform |

---

## Prerequisites

### 1. Install Python 3.11+

Download from https://www.python.org/downloads/

During installation, check **"Add Python to PATH"**.

Verify in PowerShell (run as Administrator):
```powershell
python --version
# Should show: Python 3.11.x or 3.12.x
```

### 2. Install Git

Download from https://git-scm.com/download/win

Verify:
```powershell
git --version
```

### 3. Install NSSM (Windows Service Manager)

Download from https://nssm.cc/download — get the latest release.

Extract it and copy `nssm.exe` (from the `win64` folder) to `C:\Windows\System32\`
so it's available from any terminal.

Verify:
```powershell
nssm version
```

---

## Installation

### 1. Get the Code

```powershell
# Create a clean service directory
mkdir C:\xero-service
cd C:\xero-service

# Clone the repo
git clone <your-repo-url> .
# OR copy the playwright-service folder here manually
```

If you copy manually, the folder structure should look like:
```
C:\xero-service\
  app\
  scripts\
  requirements.txt
  run.py
  ...
```

### 2. Create a Virtual Environment

```powershell
cd C:\xero-service
python -m venv venv
.\venv\Scripts\Activate.ps1
```

If you get an execution policy error:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
Then try again.

### 3. Install Dependencies

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Install Playwright's Chromium Browser

```powershell
playwright install chromium
playwright install-deps chromium
```

This downloads Chromium to `%USERPROFILE%\AppData\Local\ms-playwright\`.

---

## Configuration

### 1. Create the `.env` File

Create `C:\xero-service\.env`:

```env
# Database (your existing Supabase connection string)
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname
PGBOUNCER_MODE=true

# Security (generate a real Fernet key — see below)
ENCRYPTION_KEY=your-fernet-key-here
API_KEY=your-secure-api-key

# Xero login credentials
XERO_EMAIL=your@email.com
XERO_PASSWORD=yourpassword
XERO_SECURITY_ANSWER_1=answer1
XERO_SECURITY_ANSWER_2=answer2
XERO_SECURITY_ANSWER_3=answer3

# Windows paths (use forward slashes or double backslashes)
DOWNLOAD_DIR=C:/xero-service/downloads
SCREENSHOT_DIR=C:/xero-service/screenshots
SESSION_DIR=C:/xero-service/sessions

# Playwright
PLAYWRIGHT_TIMEOUT=30000

# CORS — add your n8n server URL here
ALLOWED_ORIGINS=http://localhost:8000,http://<your-n8n-ip>:<n8n-port>

# Logging
LOG_LEVEL=INFO
```

**Generate a Fernet encryption key:**
```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Copy the output and paste it as `ENCRYPTION_KEY`.

### 2. Create Required Directories

```powershell
mkdir C:\xero-service\downloads
mkdir C:\xero-service\screenshots
mkdir C:\xero-service\sessions
```

---

## Test the App Manually

Before installing as a service, verify it works:

```powershell
cd C:\xero-service
.\venv\Scripts\Activate.ps1
python run.py
```

You should see:
```
INFO:     Started server process [XXXX]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Test the health endpoint from another terminal:
```powershell
curl http://localhost:8000/api/health
```

Expected response: `{"status": "healthy", ...}`

Press `Ctrl+C` to stop it once confirmed working.

---

## Install as a Windows Service with NSSM

This keeps the app running 24/7, auto-starts on boot, and auto-restarts on crash.

### 1. Install the Service

Open **PowerShell as Administrator**:

```powershell
nssm install XeroPlaywrightService
```

A GUI dialog will open. Fill in:

**Application tab:**
- Path: `C:\xero-service\venv\Scripts\python.exe`
- Startup directory: `C:\xero-service`
- Arguments: `run.py`

**Details tab:**
- Display name: `Xero Playwright Service`
- Description: `Playwright browser automation service for Xero report downloads`
- Startup type: `Automatic`

**Environment tab** (add these so the service finds the `.env` file):
- Add: `PYTHONUNBUFFERED=1`

**I/O tab** (for logging):
- Output (stdout): `C:\xero-service\logs\service-stdout.log`
- Error (stderr): `C:\xero-service\logs\service-stderr.log`

Click **Install service**.

Then create the logs directory:
```powershell
mkdir C:\xero-service\logs
```

### 2. Start the Service

```powershell
nssm start XeroPlaywrightService
```

Check its status:
```powershell
nssm status XeroPlaywrightService
# Should show: SERVICE_RUNNING
```

Test the health endpoint again:
```powershell
curl http://localhost:8000/api/health
```

### 3. Useful Service Commands

```powershell
nssm start XeroPlaywrightService     # Start
nssm stop XeroPlaywrightService      # Stop
nssm restart XeroPlaywrightService   # Restart
nssm status XeroPlaywrightService    # Check status
nssm remove XeroPlaywrightService    # Uninstall (confirm when prompted)
nssm edit XeroPlaywrightService      # Edit configuration
```

---

## Open Windows Firewall for n8n

n8n needs to reach port 8000 on this VM from the network.

Open **PowerShell as Administrator**:

```powershell
New-NetFirewallRule `
  -DisplayName "Xero Playwright Service" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 8000 `
  -Action Allow
```

Verify the rule was created:
```powershell
Get-NetFirewallRule -DisplayName "Xero Playwright Service"
```

### Find Your VM's IP Address

```powershell
ipconfig
# Look for the IPv4 Address under your active adapter (Ethernet or Wi-Fi)
# Example: 192.168.1.105
```

---

## Connect n8n to the Service

In your n8n HTTP Request node, use:

```
URL: http://<your-vm-ip>:8000/api/...
```

For example:
- Health check: `http://192.168.1.105:8000/api/health`
- Trigger login: `http://192.168.1.105:8000/api/auth/automated-login`
- Download report: `http://192.168.1.105:8000/api/reports/...`

Add the `X-API-Key` header with your `API_KEY` value from `.env`.

---

## Updating the App

When you pull new code:

```powershell
# Stop the service
nssm stop XeroPlaywrightService

# Pull latest code
cd C:\xero-service
git pull

# Install any new dependencies
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Restart the service
nssm start XeroPlaywrightService
```

---

## Troubleshooting

### Service won't start — check logs

```powershell
# View last 50 lines of stdout log
Get-Content C:\xero-service\logs\service-stdout.log -Tail 50

# View last 50 lines of stderr log
Get-Content C:\xero-service\logs\service-stderr.log -Tail 50
```

### Port 8000 already in use

```powershell
netstat -ano | findstr :8000
# Note the PID, then:
taskkill /PID <pid> /F
```

### n8n can't reach the service

1. Confirm the service is running: `nssm status XeroPlaywrightService`
2. Test locally first: `curl http://localhost:8000/api/health`
3. Test from the n8n machine: `curl http://<vm-ip>:8000/api/health`
4. Check the firewall rule exists: `Get-NetFirewallRule -DisplayName "Xero Playwright Service"`
5. If the VM is behind a router/NAT, you may also need a port forward rule on the router

### Chromium not found

```powershell
.\venv\Scripts\Activate.ps1
playwright install chromium
```

### Database connection errors

- Ensure `DATABASE_URL` in `.env` uses `asyncpg` driver:
  `postgresql+asyncpg://user:pass@host:5432/db`
- If using Supabase/PgBouncer, ensure `PGBOUNCER_MODE=true`
- Check your Supabase project's connection pooling settings allow connections from this IP

### `.env` file not being loaded

The service runs from `C:\xero-service` as its startup directory, and
`pydantic-settings` automatically loads `.env` from that directory.
Confirm `C:\xero-service\.env` exists and is not named `.env.txt`.

---

## Do You Need nginx?

**No.** The FastAPI app already binds to `0.0.0.0:8000`, which means it listens
on all network interfaces. n8n connects directly via `http://<vm-ip>:8000`.

You would only need nginx if you wanted:
- HTTPS / SSL certificates (e.g., via Let's Encrypt)
- A domain name instead of an IP address
- Port 80/443 instead of 8000

For an internal automation tool talking to n8n, direct port 8000 access is
simpler and perfectly sufficient.
