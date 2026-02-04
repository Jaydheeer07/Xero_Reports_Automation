#!/bin/bash
# Startup script for Playwright service with Xvfb support
# This provides a virtual display for headed browser mode (required for Xero login)

# Kill any existing Xvfb processes
pkill -9 Xvfb 2>/dev/null || true

# Remove any stale lock files
rm -f /tmp/.X99-lock 2>/dev/null || true

# Start Xvfb (X Virtual Framebuffer) in the background
# -screen 0 1920x1080x24: 1920x1080 resolution with 24-bit color
# -ac: disable access control (allow any client to connect)
# +extension GLX: enable OpenGL extension for proper rendering
# +render: enable RENDER extension for font rendering
# -noreset: don't reset between client connections
# -dpi 96: set standard DPI for consistent font rendering
echo "Starting Xvfb virtual display..."
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset -dpi 96 &

# Wait for Xvfb to fully start and stabilize
echo "Waiting for Xvfb to initialize..."
sleep 5

# Set the DISPLAY environment variable to use the virtual display
export DISPLAY=:99

# Verify Xvfb is running
if pgrep -x "Xvfb" > /dev/null; then
    echo "Xvfb started successfully on display :99"
    # Test the display is working
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        echo "Display :99 is responsive and ready"
    else
        echo "WARNING: Display :99 may not be fully functional"
    fi
else
    echo "ERROR: Xvfb failed to start!"
    exit 1
fi

# Start the FastAPI application
echo "Starting Playwright service..."
exec python run.py
