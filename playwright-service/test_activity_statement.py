"""
Test script for Activity Statement download.

This script tests the Activity Statement download feature by calling the API endpoint.
Make sure the FastAPI server is running before executing this script.
"""

import requests
import json
from datetime import datetime

# Configuration
API_BASE_URL = "http://localhost:8000/api"
TENANT_NAME = "Marsill Pty Ltd"
# Note: tenant_id is used for database lookups only, not for Xero navigation
# The actual tenant is identified by tenant_name via page title
TENANT_ID = "!mkK34"  # Xero shortcode from URL (e.g., https://go.xero.com/app/!mkK34/homepage)
PERIOD = "December 2025"

def test_activity_statement_download():
    """Test downloading Activity Statement for November 2025."""
    
    print("=" * 60)
    print("Testing Activity Statement Download")
    print("=" * 60)
    print(f"Tenant: {TENANT_NAME}")
    print(f"Period: {PERIOD}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Step 1: Check authentication status
    print("\n[1/3] Checking authentication status...")
    try:
        response = requests.get(f"{API_BASE_URL}/auth/status")
        auth_status = response.json()
        
        if auth_status.get("logged_in"):
            print("✓ Authenticated")
            print(f"  Current tenant: {auth_status.get('current_tenant', {}).get('name', 'Unknown')}")
        else:
            print("✗ Not authenticated")
            print("  Please run: Invoke-RestMethod -Method POST -Uri 'http://localhost:8000/api/auth/setup'")
            return
    except Exception as e:
        print(f"✗ Error checking auth status: {e}")
        return
    
    # Step 2: Prepare request
    print("\n[2/3] Preparing download request...")
    request_body = {
        "tenant_id": TENANT_ID,
        "tenant_name": TENANT_NAME,
        "period": PERIOD,
        "find_unfiled": False  # Set to False - we'll just download the statement directly
    }
    print(f"  Request: {json.dumps(request_body, indent=2)}")
    
    # Step 3: Download Activity Statement
    print("\n[3/3] Downloading Activity Statement...")
    print("  This may take 30-60 seconds...")
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/reports/activity-statement",
            json=request_body,
            timeout=120  # 2 minutes timeout
        )
        
        result = response.json()
        
        print("\n" + "=" * 60)
        print("RESULT")
        print("=" * 60)
        
        if result.get("success"):
            print("✓ SUCCESS!")
            print(f"  File: {result.get('file_name')}")
            print(f"  Path: {result.get('file_path')}")
            print(f"  Period: {result.get('period')}")
            print(f"  Tenant: {result.get('tenant_name')}")
        else:
            print("✗ FAILED")
            print(f"  Error: {result.get('error')}")
            if result.get('screenshot'):
                print(f"  Screenshot: {result.get('screenshot')}")
        
        print("\nFull response:")
        print(json.dumps(result, indent=2))
        
    except requests.exceptions.Timeout:
        print("✗ Request timed out (>2 minutes)")
        print("  The automation may still be running. Check the server logs.")
    except Exception as e:
        print(f"✗ Error: {e}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    test_activity_statement_download()
