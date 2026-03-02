#!/bin/bash
# Verification script for shared memory configuration

echo "=== Checking Shared Memory Configuration ==="
echo ""

echo "1. Container shm_size setting:"
docker inspect playwright-service --format='{{.HostConfig.ShmSize}}' 2>/dev/null || echo "Container not found or not running"
echo ""

echo "2. Actual /dev/shm size inside container:"
docker exec playwright-service df -h /dev/shm 2>/dev/null || echo "Cannot access container"
echo ""

echo "3. Xvfb process status:"
docker exec playwright-service ps aux | grep -i xvfb | grep -v grep || echo "Xvfb not running"
echo ""

echo "4. Current docker-compose file being used:"
docker inspect playwright-service --format='{{.Config.Labels}}' | grep -o 'com.docker.compose.project.config_files=[^,}]*' || echo "Not started with docker-compose"
echo ""

echo "=== Expected Values ==="
echo "ShmSize should be: 2147483648 (2GB)"
echo "/dev/shm should show: 2.0G"
echo ""

echo "=== Recommendation ==="
SHMSIZE=$(docker inspect playwright-service --format='{{.HostConfig.ShmSize}}' 2>/dev/null)
if [ "$SHMSIZE" != "2147483648" ]; then
    echo "⚠️  WARNING: shm_size is NOT 2GB!"
    echo "Run these commands to fix:"
    echo "  cd ~/xero_reports_automation/playwright-service"
    echo "  docker-compose down"
    echo "  docker-compose up -d"
else
    echo "✅ shm_size is correctly configured"
fi
