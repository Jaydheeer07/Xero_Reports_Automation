"""
Test script for Payroll Activity Summary download.

This script tests the Payroll Activity Summary download feature by calling the API endpoint.
Make sure the FastAPI server is running before executing this script.

Usage:
    python test_payroll_activity_summary.py
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

# Test with October 2025 as requested
TEST_MONTH = 10
TEST_YEAR = 2025


def test_payroll_activity_summary_download():
    """Test downloading Payroll Activity Summary for October 2025."""
    
    print("=" * 60)
    print("Testing Payroll Activity Summary Download")
    print("=" * 60)
    print(f"Tenant: {TENANT_NAME}")
    print(f"Period: {TEST_MONTH}/{TEST_YEAR} (October 2025)")
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
        "month": TEST_MONTH,
        "year": TEST_YEAR
    }
    print(f"  Request: {json.dumps(request_body, indent=2)}")
    print(f"  Expected date range: 1 October 2025 to 31 October 2025")
    
    # Step 3: Download Payroll Activity Summary
    print("\n[3/3] Downloading Payroll Activity Summary...")
    print("  This may take 30-60 seconds...")
    print("  Workflow: Reporting > All Reports > Payroll Activity Summary > Enter dates > Update > Export > Excel")
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/reports/payroll-activity-summary",
            json=request_body,
            timeout=180  # 3 minutes timeout
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
            print(f"  Start Date: {result.get('start_date')}")
            print(f"  End Date: {result.get('end_date')}")
            print(f"  Tenant: {result.get('tenant_name')}")
        else:
            print("✗ FAILED")
            print(f"  Error: {result.get('error')}")
            if result.get('screenshot'):
                print(f"  Screenshot: {result.get('screenshot')}")
        
        print("\nFull response:")
        print(json.dumps(result, indent=2))
        
    except requests.exceptions.Timeout:
        print("✗ Request timed out (>3 minutes)")
        print("  The automation may still be running. Check the server logs.")
    except Exception as e:
        print(f"✗ Error: {e}")
    
    print("\n" + "=" * 60)


def test_date_range_calculation():
    """Test the date range calculation for various months."""
    import calendar
    
    print("\n" + "=" * 60)
    print("Date Range Calculation Test")
    print("=" * 60)
    
    test_cases = [
        (1, 2025, "January 2025", "1 January 2025", "31 January 2025"),
        (2, 2024, "February 2024 (leap year)", "1 February 2024", "29 February 2024"),
        (2, 2025, "February 2025 (non-leap)", "1 February 2025", "28 February 2025"),
        (4, 2025, "April 2025 (30 days)", "1 April 2025", "30 April 2025"),
        (10, 2025, "October 2025", "1 October 2025", "31 October 2025"),
        (12, 2025, "December 2025", "1 December 2025", "31 December 2025"),
    ]
    
    all_passed = True
    for month, year, desc, expected_start, expected_end in test_cases:
        _, last_day = calendar.monthrange(year, month)
        month_name = calendar.month_name[month]
        
        start_date = f"1 {month_name} {year}"
        end_date = f"{last_day} {month_name} {year}"
        
        start_ok = start_date == expected_start
        end_ok = end_date == expected_end
        
        status = "✓" if (start_ok and end_ok) else "✗"
        print(f"{status} {desc}")
        print(f"    Start: {start_date} (expected: {expected_start})")
        print(f"    End:   {end_date} (expected: {expected_end})")
        
        if not (start_ok and end_ok):
            all_passed = False
    
    print("\n" + "-" * 40)
    if all_passed:
        print("✓ All date range calculations passed!")
    else:
        print("✗ Some date range calculations failed!")
    print("=" * 60)


if __name__ == "__main__":
    # First run the date calculation test
    test_date_range_calculation()
    
    # Then run the actual download test
    test_payroll_activity_summary_download()
