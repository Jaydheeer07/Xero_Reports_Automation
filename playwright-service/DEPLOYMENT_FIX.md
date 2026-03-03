# Fix for Partial Page Rendering in Docker

## Problem
Xero login page only renders halfway, showing incomplete content. This happens in Docker but works fine locally.

## Root Cause
**Insufficient shared memory (`/dev/shm`) for Chromium's rendering engine.**

Chromium uses shared memory for:
- Inter-process communication (IPC)
- Canvas/WebGL rendering
- Image decoding
- Font caching

Docker containers default to 64MB of `/dev/shm`, which is far too small for modern web applications like Xero.

## Solution

### 1. Add `shm_size` to Docker Compose (CRITICAL)

The `docker-compose.yml` already has this, but ensure you're using the correct compose file:

```yaml
services:
  playwright-service:
    # ... other config ...
    shm_size: '2gb'  # Allocates 2GB of shared memory
```

### 2. Updated Browser Launch Args

The browser manager now includes optimized flags for Docker rendering:
- `--disable-software-rasterizer` - Use CPU rendering instead
- `--disable-gpu-compositing` - Disable GPU compositing
- `--font-render-hinting=none` - Better font rendering in Xvfb

### 3. Deployment Steps

**On your DigitalOcean droplet:**

```bash
# 1. Pull the latest code
cd ~/xero_reports_automation/playwright-service
git pull

# 2. Rebuild the container with new browser args
docker-compose down
docker-compose build --no-cache

# 3. Start with proper shm_size
docker-compose up -d

# 4. Verify shm_size is applied
docker inspect playwright-service | grep -i shm
# Should show: "ShmSize": 2147483648 (2GB in bytes)

# 5. Test the login
docker exec playwright-service curl -X POST http://localhost:8000/api/auth/automated-login
```

### 4. Verification

Check that the container has sufficient shared memory:

```bash
# Inside container
docker exec -it playwright-service df -h /dev/shm
# Should show 2.0G

# Check browser process
docker exec playwright-service ps aux | grep chromium
```

### 5. If Still Having Issues

If the problem persists after applying shm_size:

**Option A: Increase shm_size**
```yaml
shm_size: '3gb'  # Use 3GB instead
```

**Option B: Use tmpfs mount (alternative to shm_size)**
```yaml
services:
  playwright-service:
    tmpfs:
      - /dev/shm:rw,nosuid,nodev,size=2g
```

**Option C: Check Xvfb display**
```bash
# Verify Xvfb is running properly
docker exec playwright-service ps aux | grep Xvfb
docker exec playwright-service xdpyinfo -display :99
```

## Why This Happens

1. **Docker Default**: 64MB `/dev/shm`
2. **Chromium Needs**: 500MB-2GB for complex SPAs like Xero
3. **Without Enough**: Partial rendering, crashes, or OOM errors
4. **The Flag `--disable-dev-shm-usage`**: Helps but doesn't fully solve it - still needs adequate shm_size

## Resource Usage

With 4GB RAM droplet:
- Container limit: 3GB
- Shared memory: 2GB (part of the 3GB)
- Leaves ~1GB for OS

This is optimal for a single Playwright instance.

## References

- [Playwright Docker docs](https://playwright.dev/docs/docker)
- [Chromium shared memory requirements](https://github.com/puppeteer/puppeteer/blob/main/docs/troubleshooting.md#tips)
