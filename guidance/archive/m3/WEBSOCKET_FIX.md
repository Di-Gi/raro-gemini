# WebSocket Connection Fix - No Logs After Workflow Start

## Problem Diagnosis

**Symptoms:**
- Workflow starts successfully (run_id received)
- No logs appear after "Workflow started"
- Backend processes agents successfully (docker logs show completion)
- UI doesn't show agent progress

**Root Cause:**
WebSocket connection is not being proxied correctly. The Vite proxy only handles HTTP requests, not WebSocket upgrades by default.

## Analysis

### Backend is Working ✅
From `docker.logs`:
```
INFO raro_kernel::runtime: Processing agent: n1 ✅
INFO raro_kernel::runtime: Processing agent: n2 ✅
INFO raro_kernel::runtime: Processing agent: n3 ✅
INFO raro_kernel::runtime: Processing agent: n4 ✅
INFO raro_kernel::runtime: Workflow run ... completed successfully ✅
```

### WebSocket Endpoint Exists ✅
`apps/kernel-server/src/server/handlers.rs:134-223`:
- Sends state updates every 250ms
- Includes agent status, signatures, timestamps
- Closes on completion

### Proxy Missing WebSocket Support ❌
`apps/web-console/vite.config.ts`:
- `/api` proxy exists for HTTP
- **Missing:** `ws: true` flag for WebSocket upgrades
- **Missing:** `/ws` proxy configuration

---

## Solution

### Fix 1: Add WebSocket Support to Vite Proxy

Update `apps/web-console/vite.config.ts`:

```typescript
export default defineConfig({
  plugins: [svelte()],
  resolve: {
    alias: {
      $lib: path.resolve(__dirname, './src/lib'),
      $components: path.resolve(__dirname, './src/components'),
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      // HTTP API Proxy
      '/api': {
        target: process.env.DOCKER_ENV === 'true' ? 'http://kernel:3000' : 'http://localhost:3000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },

      // WebSocket Proxy (NEW!)
      '/ws': {
        target: process.env.DOCKER_ENV === 'true' ? 'ws://kernel:3000' : 'ws://localhost:3000',
        ws: true,  // ← Enable WebSocket proxying!
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/ws/, '/ws'),  // Keep /ws prefix
      },

      // Agent API Proxy
      '/agent-api': {
        target: process.env.DOCKER_ENV === 'true' ? 'http://agents:8000' : 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/agent-api/, ''),
      },
    },
  },
})
```

### Fix 2: Update WebSocket URL Construction

The current `getWebSocketURL()` in `api.ts` tries to parse `KERNEL_API` as a URL, but `/api` is relative. Update it:

```typescript
// apps/web-console/src/lib/api.ts

export function getWebSocketURL(runId: string): string {
  if (USE_MOCK) return `mock://runtime/${runId}`;

  // In Docker with dev proxy, use relative path
  if (import.meta.env.DEV) {
    // Development mode - use Vite proxy
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/runtime/${runId}`;
  }

  // Production mode - use direct connection
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const apiUrl = import.meta.env.VITE_KERNEL_URL || 'http://localhost:3000';

  try {
    const url = new URL(apiUrl);
    const wsProtocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProtocol}//${url.host}/ws/runtime/${runId}`;
  } catch {
    // Fallback to window location
    return `${protocol}//${window.location.host}/ws/runtime/${runId}`;
  }
}
```

### Fix 3: Add WebSocket Debugging

Enhance logging to diagnose connection issues:

```typescript
// apps/web-console/src/lib/stores.ts

export function connectRuntimeWebSocket(runId: string) {
  if (ws) {
    ws.close();
  }

  const url = getWebSocketURL(runId);
  console.log('[WS] Connecting to:', url);  // ← Add debug log

  if (USE_MOCK) {
    addLog('SYSTEM', 'Initializing MOCK runtime environment...', 'DEBUG');
    ws = new MockWebSocket(url);
  } else {
    ws = new WebSocket(url);
  }

  ws.onopen = () => {
    console.log('[WS] Connected successfully');  // ← Add debug log
    addLog('KERNEL', `Connected to runtime stream: ${runId}`, 'NET_OK');
    runtimeStore.set({ status: 'RUNNING', runId });
  };

  ws.onmessage = (event: any) => {
    console.log('[WS] Message received:', event.data);  // ← Add debug log
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'state_update' && data.state) {
        console.log('[WS] State update:', data.state);  // ← Add debug log
        syncState(data.state, data.signatures);

        if (data.state.status) {
          runtimeStore.update(s => ({ ...s, status: data.state.status.toUpperCase() }));
        }
      } else if (data.error) {
        addLog('KERNEL', `Runtime error: ${data.error}`, 'ERR');
      }
    } catch (e) {
      console.error('[WS] Failed to parse message:', e, event.data);  // ← Enhanced error
    }
  };

  ws.onerror = (e) => {
    console.error('[WS] Error:', e);  // ← Add debug log
    addLog('KERNEL', 'WebSocket connection error.', 'ERR');
  };

  ws.onclose = (e) => {
    console.log('[WS] Closed:', e.code, e.reason);  // ← Add debug log
    addLog('KERNEL', 'Connection closed.', 'NET_END');

    // Force status updates...
  };
}
```

---

## Implementation

Apply these changes:

```bash
# 1. Update vite.config.ts (add /ws proxy)
# 2. Update api.ts (fix getWebSocketURL)
# 3. Update stores.ts (add debug logs)

# Rebuild
docker-compose down
docker-compose build web
docker-compose up

# Check browser console for WebSocket logs
```

---

## Expected Behavior After Fix

### Browser Console Logs:
```
[WS] Connecting to: ws://localhost:5173/ws/runtime/2ea77c64-5da9-4911-af6d-8ab90c9a395a
[WS] Connected successfully
[WS] Message received: {"type":"state_update","state":{...}}
[WS] State update: {status: "running", active_agents: ["n1"], ...}
[WS] Message received: {"type":"state_update","state":{...}}
[WS] State update: {active_agents: ["n2"], completed_agents: ["n1"], ...}
...
```

### UI Logs Panel:
```
USER_INPUT OPERATOR: test
SYS KERNEL: Compiling DAG manifest...
OK KERNEL: Workflow started. Run ID: 2ea77c64...
NET_OK KERNEL: Connected to runtime stream: 2ea77c64...
N1 [Agent log]: Processing...
N2 [Agent log]: Processing...
...
```

---

## Verification

### Test 1: Check Proxy Config
```bash
# In browser console (after vite.config.ts update)
# DevTools → Network → WS tab
# Should see: ws://localhost:5173/ws/runtime/{id}
# Status: 101 Switching Protocols ✅
```

### Test 2: Check Backend Logs
```bash
docker-compose logs -f kernel | grep "Client disconnected\|runtime stream"
# Should see: INFO Client connected to runtime stream: {id}
```

### Test 3: End-to-End
1. Click RUN in UI
2. Check browser console for `[WS] Connected successfully`
3. Check for `[WS] Message received` every 250ms
4. Verify logs appear in UI for each agent

---

## Troubleshooting

### Issue: Still No WebSocket Connection

**Check 1: Is proxy configured?**
```bash
# In browser, try manual WebSocket connection:
# Open DevTools Console
const ws = new WebSocket('ws://localhost:5173/ws/runtime/test-id');
ws.onopen = () => console.log('WS Open!');
ws.onerror = (e) => console.error('WS Error:', e);
```

**Check 2: Is kernel endpoint working?**
```bash
# From host machine (not container)
wscat -c ws://localhost:3000/ws/runtime/test-id
# Should get: {"error": "Run not found"} or state updates
```

**Check 3: Are logs showing connection?**
```bash
docker-compose logs kernel | grep "runtime stream"
# Should see connection attempts
```

### Issue: WebSocket Connects But No Messages

**Check: Message format**
```javascript
// In browser console, log raw messages
ws.onmessage = (e) => console.log('RAW:', e.data);
```

**Expected format:**
```json
{
  "type": "state_update",
  "state": {
    "run_id": "...",
    "workflow_id": "...",
    "status": "Running",
    "active_agents": ["n1"],
    "completed_agents": [],
    "failed_agents": [],
    // ...
  },
  "signatures": {
    "n1": "..."
  },
  "timestamp": "2026-01-03T05:01:04.473Z"
}
```

---

## Why This Fixes It

1. **WebSocket Proxy:** Vite now forwards `ws://localhost:5173/ws/*` → `ws://kernel:3000/ws/*`
2. **Correct URL:** `getWebSocketURL()` uses Vite proxy path in dev mode
3. **Debug Logs:** Easy to diagnose connection issues
4. **Kernel Ready:** Backend already sends updates every 250ms

**Result:** UI receives real-time state updates and displays agent progress! ✅
