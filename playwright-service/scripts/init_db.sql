-- Xero Reports Automation Database Schema
-- Run this script to initialize the database tables

-- Clients/Tenants to process
CREATE TABLE IF NOT EXISTS clients (
    id SERIAL PRIMARY KEY,
    tenant_id VARCHAR(255) UNIQUE NOT NULL,
    tenant_name VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    onedrive_folder VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Xero session storage (encrypted cookies)
CREATE TABLE IF NOT EXISTS xero_sessions (
    id INTEGER PRIMARY KEY DEFAULT 1,
    cookies TEXT NOT NULL,  -- Encrypted JSON
    oauth_tokens TEXT,      -- Optional: encrypted OAuth tokens
    expires_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT single_session CHECK (id = 1)
);

-- Download logs for tracking and debugging
CREATE TABLE IF NOT EXISTS download_logs (
    id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id),
    report_type VARCHAR(50) NOT NULL,  -- 'activity_statement' or 'payroll_summary'
    status VARCHAR(20) NOT NULL,       -- 'success', 'failed', 'pending'
    file_path VARCHAR(500),
    file_name VARCHAR(255),
    file_size INTEGER,
    error_message TEXT,
    screenshot_path VARCHAR(500),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    uploaded_to_onedrive BOOLEAN DEFAULT false,
    onedrive_path VARCHAR(500)
);

-- Indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_clients_active ON clients(is_active);
CREATE INDEX IF NOT EXISTS idx_clients_tenant_id ON clients(tenant_id);
CREATE INDEX IF NOT EXISTS idx_download_logs_client ON download_logs(client_id);
CREATE INDEX IF NOT EXISTS idx_download_logs_status ON download_logs(status);
CREATE INDEX IF NOT EXISTS idx_download_logs_date ON download_logs(started_at);
CREATE INDEX IF NOT EXISTS idx_download_logs_report_type ON download_logs(report_type);

-- Function to update the updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers to auto-update updated_at
DROP TRIGGER IF EXISTS update_clients_updated_at ON clients;
CREATE TRIGGER update_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_xero_sessions_updated_at ON xero_sessions;
CREATE TRIGGER update_xero_sessions_updated_at
    BEFORE UPDATE ON xero_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Insert sample clients for testing (optional - comment out in production)
-- INSERT INTO clients (tenant_id, tenant_name, onedrive_folder) VALUES
--     ('sample-tenant-1', 'Sample Client 1', '/Xero Reports/Sample Client 1'),
--     ('sample-tenant-2', 'Sample Client 2', '/Xero Reports/Sample Client 2');

COMMENT ON TABLE clients IS 'Xero client tenants to process for report downloads';
COMMENT ON TABLE xero_sessions IS 'Encrypted Xero session cookies (single row)';
COMMENT ON TABLE download_logs IS 'Audit log of all report download attempts';
