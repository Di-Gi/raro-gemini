# Docker Integration Fix - Quick Solution

## Problem
Web console getting 404 on `/api/runtime/start` because:
1. Environment variable name mismatch (`VITE_API_URL` vs `VITE_KERNEL_URL`)
2. Vite build-time variables don't update at Docker runtime
3. Vite preview mode doesn't have proxy

## Quick Fix: Update Docker Configuration

### Step 1: Fix docker-compose.yml

Change the web service environment variable:

```yaml
web:
  build:
    context: ./apps/web-console
    dockerfile: Dockerfile
    args:
      - VITE_KERNEL_URL=http://localhost:3000  # Build-time arg
      - VITE_AGENT_URL=http://localhost:8000   # Build-time arg
  container_name: raro-web
  ports:
    - "5173:5173"
  environment:
    - VITE_KERNEL_URL=http://localhost:3000  # Also set at runtime (though won't work due to build issue)
  depends_on:
    kernel:
      condition: service_healthy
  networks:
    - raro-net
```

### Step 2: Fix Dockerfile to Accept Build Args

```dockerfile
FROM node:20-alpine AS builder

WORKDIR /app

# Accept build arguments for API URLs
ARG VITE_KERNEL_URL=http://localhost:3000
ARG VITE_AGENT_URL=http://localhost:8000

# Set them as environment variables for the build
ENV VITE_KERNEL_URL=$VITE_KERNEL_URL
ENV VITE_AGENT_URL=$VITE_AGENT_URL

# Copy package management files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm install

# Copy source code
COPY . .

# Build the application (env vars baked in here)
RUN npm run build

# --- Runtime Stage ---
FROM node:20-alpine

WORKDIR /app

# Copy built assets and package files
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules

# Expose Vite's default port
EXPOSE 5173

# Bind to 0.0.0.0 to ensure accessibility outside container
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0"]
```

### Step 3: Fix .env File

Update `apps/web-console/.env`:

```env
VITE_KERNEL_URL=http://localhost:3000
VITE_AGENT_URL=http://localhost:8000
VITE_USE_MOCK_API=false
```

### Step 4: Rebuild

```bash
docker-compose down
docker-compose build web
docker-compose up
```

## Why This Works

1. **Build args** pass values into the Docker build process
2. **ENV in Dockerfile** makes them available during `npm run build`
3. **Vite bakes them in** to the JavaScript bundle at build time
4. **Browser uses absolute URLs** like `http://localhost:3000/runtime/start`

## Limitations

- **Browser CORS**: Browser running on host must access `localhost:3000` directly
- **Not true Docker isolation**: Services exposed to host
- **Development only**: Not suitable for production deployment

---

## Better Approach: Use Dev Mode in Docker

Alternatively, run Vite in **dev mode** inside Docker (proxy will work):

### Dockerfile.dev (New File)

```dockerfile
FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

### docker-compose.yml (Update web service)

```yaml
web:
  build:
    context: ./apps/web-console
    dockerfile: Dockerfile.dev  # Use dev Dockerfile
  container_name: raro-web
  ports:
    - "5173:5173"
  volumes:
    - ./apps/web-console/src:/app/src  # Hot reload!
  depends_on:
    kernel:
      condition: service_healthy
  networks:
    - raro-net
```

### Update vite.config.ts proxy targets

Since services are on the same Docker network:

```typescript
server: {
  port: 5173,
  host: '0.0.0.0',  // Add this
  proxy: {
    '/api': {
      target: 'http://kernel:3000',  // Use Docker service name!
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ''),
    },
    '/agent-api': {
      target: 'http://agents:8000',  // Use Docker service name!
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/agent-api/, ''),
    },
  },
}
```

### Benefits

- ✅ Proxy works (dev mode)
- ✅ Hot reload works
- ✅ True Docker isolation
- ✅ No CORS issues
- ✅ Service names instead of localhost

### Rebuild

```bash
docker-compose down
docker-compose build web
docker-compose up
```

---

## Which Fix to Use?

- **Quick & Dirty:** Build args solution (5 minutes)
- **Recommended:** Dev mode with Docker service names (10 minutes, better DX)
- **Production:** See PRODUCTION_DEPLOYMENT.md (nginx solution)
