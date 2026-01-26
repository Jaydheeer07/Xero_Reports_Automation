# Xero Reports Automation - Playwright Service

A FastAPI microservice that automates Xero report downloads using Playwright browser automation.

## Features

- **Automated Report Downloads**: Activity Statement (BAS) and Payroll Activity Summary
- **Multi-tenant Support**: Switch between Xero client organisations
- **Session Management**: Encrypted cookie storage for persistent sessions
- **OneDrive Integration**: Upload reports via n8n workflow

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development without Docker)

### Local Development

1. **Clone and navigate to the service directory:**
   ```bash
   cd playwright-service
   ```

2. **Copy environment file:**
   ```bash
   cp .env.example .env
   ```

3. **Generate an encryption key:**
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Update the `ENCRYPTION_KEY` in `.env` with the generated key.

4. **Start the services:**
   ```bash
   docker-compose up --build
   ```

5. **Verify the service is running:**
   ```bash
   curl http://localhost:8000/api/health
   ```

### API Documentation

Once running, access the interactive API docs at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API Endpoints

### Health
- `GET /api/health` - Service health check

### Authentication
- `POST /api/auth/setup` - Start manual login flow
- `POST /api/auth/complete` - Save session after login
- `GET /api/auth/status` - Check session validity
- `GET /api/auth/tenants` - List available tenants
- `POST /api/auth/switch-tenant` - Switch to a tenant

### Reports
- `POST /api/reports/activity-statement` - Download Activity Statement
- `POST /api/reports/payroll-activity-summary` - Download Payroll Summary
- `GET /api/reports/download/{filename}` - Download a report file

## Project Structure

```
playwright-service/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application
│   ├── config.py               # Configuration settings
│   ├── api/
│   │   └── routes/
│   │       ├── auth.py         # Authentication endpoints
│   │       ├── reports.py      # Report download endpoints
│   │       └── health.py       # Health check
│   ├── db/
│   │   ├── connection.py       # Database connection
│   │   └── models.py           # SQLAlchemy models
│   ├── services/               # Business logic (Phase 2+)
│   └── models/                 # Pydantic models
├── scripts/
│   └── init_db.sql             # Database schema
├── downloads/                  # Downloaded reports
├── screenshots/                # Error screenshots
├── sessions/                   # Session backups
├── Dockerfile
├── docker-compose.yml          # Local development
├── docker-compose.prod.yml     # Production deployment
└── requirements.txt
```

## Deployment to DigitalOcean

1. Copy the `playwright-service` directory to your droplet
2. Update `docker-compose.prod.yml` with your network name
3. Set environment variables (DATABASE_URL, ENCRYPTION_KEY)
4. Run: `docker-compose -f docker-compose.prod.yml up -d`

## Development Phases

- [x] Phase 1: Infrastructure Setup
- [ ] Phase 2: Core FastAPI Service
- [ ] Phase 3: Authentication Module
- [ ] Phase 4: Xero Automation Scripts
- [ ] Phase 5: n8n Integration
- [ ] Phase 6: Testing
- [ ] Phase 7: Documentation & Deployment

## License

Internal use only - Dexterous Group
