#!/bin/bash
# Startup script for Playwright service with Xvfb support
# This provides a virtual display for headed browser mode (required for Xero login)

# Start Xvfb (X Virtual Framebuffer) in the background
# This creates a virtual display that allows headed browsers to run without a physical monitor
echo "Starting Xvfb virtual display..."
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &

# Wait for Xvfb to start
sleep 2

# Set the DISPLAY environment variable to use the virtual display
export DISPLAY=:99

# Verify Xvfb is running
if pgrep -x "Xvfb" > /dev/null; then
    echo "Xvfb started successfully on display :99"
else
    echo "WARNING: Xvfb may not have started properly"
fi

# Start the FastAPI application
echo "Starting Playwright service..."
exec python run.py
