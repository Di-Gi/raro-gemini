# Redis Pub/Sub Live Logging System - Implementation Guide

**Status:** 📋 DESIGN COMPLETE - READY FOR APPROVAL
**Date:** 2026-01-08
**Architecture:** Redis Pub/Sub → Event Bus → WebSocket → UI

---

## Problem Statement

**Current Limitation:**
The UI only receives agent logs **after** the agent completes execution. Users cannot see intermediate progress like:
- Tool calls in progress
- Tool execution results
- Internal reasoning steps

**Goal:**
Pipe **real-time intermediate logs** from Python Agent Service → Kernel → Web Console with minimal latency and zero blocking.

---

## Architecture Overview

### Data Flow
```
Python Agent (llm.py)
    ↓ emit_telemetry()
Redis Pub/Sub Channel ("raro:live_logs")
    ↓ subscribe in background task
Kernel Event Bus (broadcast channel)
    ↓ tokio::select! in WebSocket handler
WebSocket to Browser
    ↓ ws.onmessage
UI Log Store (Svelte)
    ↓ OutputPane rendering
Live Agent HUD
```

### Key Design Decisions

1. **Redis Pub/Sub** (Not HTTP polling)
   - **Why:** Decouples Agent Service from Kernel, fire-and-forget semantics
   - **Trade-off:** Requires Redis infrastructure (already present)

2. **Event Bus Bridge**
   - **Why:** Leverage existing event infrastructure, per-run filtering
   - **Trade-off:** One extra hop, but maintains clean separation

3. **Category-Based Rendering**
   - **Why:** Different UI treatment for TOOL_CALL vs TOOL_RESULT vs THOUGHT
   - **Trade-off:** Slight complexity in frontend, but much better UX

---

## Message Protocol

### Three Log Categories

#### A. TOOL_CALL (Request)
Emitted immediately **before** tool execution.

```json
{
  "run_id": "flow-123",
  "agent_id": "researcher",
  "category": "TOOL_CALL",
  "message": "web_search(query='RARO architecture')",
  "metadata": "IO_REQ",
  "timestamp": "2026-01-08T10:30:45.123Z",
  "tool_name": "web_search",
  "args": { "query": "RARO architecture" }
}
```

#### B. TOOL_RESULT (Response)
Emitted immediately **after** tool execution.

```json
{
  "run_id": "flow-123",
  "agent_id": "researcher",
  "category": "TOOL_RESULT",
  "message": "Found 12 citations.",
  "metadata": "IO_OK",
  "timestamp": "2026-01-08T10:30:45.456Z",
  "tool_name": "web_search",
  "duration_ms": 333
}
```

#### C. THOUGHT (Optional - Future)
For reasoning steps between tool calls.

```json
{
  "run_id": "flow-123",
  "agent_id": "researcher",
  "category": "THOUGHT",
  "message": "Analyzing search results to extract key concepts...",
  "metadata": "PLANNING",
  "timestamp": "2026-01-08T10:30:45.789Z"
}
```

---

## Implementation Plan

### Phase 1: Kernel Updates (Rust) ✅ Ready

#### Step 1.1: Add Event Type

**File:** `apps/kernel-server/src/events.rs`

**Location:** Inside `EventType` enum (around line 10)

**Change:**
```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EventType {
    NodeCreated,
    AgentStarted,
    AgentCompleted,
    AgentFailed,
    ToolCall,
    SystemIntervention,

    // [[NEW]]
    IntermediateLog,  // For real-time tool/thought logs
}
```

---

#### Step 1.2: Add Redis Subscriber Task

**File:** `apps/kernel-server/src/main.rs`

**Location:** After Cortex Pattern Engine setup (line 98), before CORS middleware

**Dependencies Required:**
Add to `Cargo.toml`:
```toml
futures = "0.3"
```

**Import Required:**
```rust
use futures::StreamExt; // Add to imports at top
```

**Code to Add:**
```rust
    // === REDIS LIVE LOG SUBSCRIBER ===
    // Listens to "raro:live_logs" channel and bridges messages to internal event bus
    if let Some(redis_client) = &runtime.redis_client {
        let client = redis_client.clone();
        let event_bus = runtime.event_bus.clone();

        tokio::spawn(async move {
            tracing::info!("🎧 Started Redis Log Subscriber on 'raro:live_logs'");

            // Establish PubSub connection
            let mut pubsub_conn = match client.get_async_connection().await {
                Ok(conn) => conn.into_pubsub(),
                Err(e) => {
                    tracing::error!("Failed to connect to Redis for PubSub: {}", e);
                    return;
                }
            };

            // Subscribe to the channel
            if let Err(e) = pubsub_conn.subscribe("raro:live_logs").await {
                tracing::error!("Failed to subscribe to 'raro:live_logs': {}", e);
                return;
            }

            // Stream incoming messages
            let mut stream = pubsub_conn.on_message();

            while let Some(msg) = stream.next().await {
                let payload_str: String = msg.get_payload().unwrap_or_default();

                // Parse JSON payload from Python
                if let Ok(data) = serde_json::from_str::<serde_json::Value>(&payload_str) {
                    let run_id = data["run_id"].as_str().unwrap_or_default();
                    let agent_id = data["agent_id"].as_str();
                    let message = data["message"].as_str().unwrap_or("");
                    let metadata = data["metadata"].as_str().unwrap_or("INFO");
                    let category = data["category"].as_str().unwrap_or("INFO");

                    // Bridge to internal Event Bus (which WebSockets subscribe to)
                    let _ = event_bus.send(crate::events::RuntimeEvent::new(
                        run_id,
                        crate::events::EventType::IntermediateLog,
                        agent_id.map(|s| s.to_string()),
                        serde_json::json!({
                            "message": message,
                            "metadata": metadata,
                            "category": category
                        })
                    ));
                } else {
                    tracing::warn!("Failed to parse Redis log payload: {}", payload_str);
                }
            }

            tracing::warn!("Redis log subscriber exited unexpectedly");
        });
    } else {
        tracing::warn!("Redis client not available - live logs disabled");
    }
```

---

#### Step 1.3: Update WebSocket Handler

**File:** `apps/kernel-server/src/server/handlers.rs`

**Location:** Inside `handle_runtime_stream` function (starts at line 302)

**Current Implementation:**
The function currently has **two** `tokio::select!` arms:
1. `receiver.next()` - Client disconnect detection
2. `interval.tick()` - Periodic state polling

**Modification Required:**
Add a **third** select arm to forward real-time events from the event bus.

**Find this code block (lines ~341-387):**
```rust
    loop {
        tokio::select! {
            // Check for client disconnect
            msg = receiver.next() => {
                if msg.is_none() {
                    tracing::info!("Client disconnected from runtime stream: {}", run_id);
                    break;
                }
            }

            // Send periodic updates
            _ = interval.tick() => {
                // ... existing state update logic ...
            }
        }
    }
```

**Replace with:**
```rust
    // Subscribe to event bus for real-time logs
    let mut bus_rx = runtime.event_bus.subscribe();

    loop {
        tokio::select! {
            // Check for client disconnect
            msg = receiver.next() => {
                if msg.is_none() {
                    tracing::info!("Client disconnected from runtime stream: {}", run_id);
                    break;
                }
            }

            // Send periodic updates
            _ = interval.tick() => {
                // ... existing state update logic (keep unchanged) ...
            }

            // [[NEW]] Forward real-time events
            Ok(event) = bus_rx.recv() => {
                // Only forward events for THIS run
                if event.run_id == run_id {
                    // Filter for intermediate logs (you can forward all events if desired)
                    if let crate::events::EventType::IntermediateLog = event.event_type {
                        let ws_msg = serde_json::json!({
                            "type": "log_event",
                            "agent_id": event.agent_id,
                            "payload": event.payload,
                            "timestamp": event.timestamp
                        });

                        if sender.send(Message::Text(ws_msg.to_string())).await.is_err() {
                            tracing::info!("Failed to send log event, client disconnected");
                            break;
                        }
                    }
                }
            }
        }
    }
```

**Important Notes:**
- Keep the existing `interval.tick()` logic **unchanged** - this maintains backward compatibility
- The event bus subscription adds real-time capability on top of polling
- Events are filtered by `run_id` to ensure clients only receive their own logs

---

### Phase 2: Agent Service Updates (Python) ✅ Ready

#### Step 2.1: Add Telemetry Helper

**File:** `apps/agent-service/src/core/llm.py`

**Location:** Add near the top of the file, after imports and before `_prepare_gemini_request`

**Code to Add:**
```python
# ============================================================================
# Live Telemetry Emission (Redis Pub/Sub)
# ============================================================================

def emit_telemetry(
    run_id: str,
    agent_id: str,
    category: str,
    message: str,
    meta: str,
    extra: dict = {}
):
    """
    Publish live log event to Redis for Kernel to forward to UI.

    Args:
        run_id: Current workflow run ID
        agent_id: Agent generating this log
        category: TOOL_CALL | TOOL_RESULT | THOUGHT
        message: Human-readable message
        meta: Short tag (IO_REQ, IO_OK, IO_ERR, PLANNING, etc.)
        extra: Additional structured data (tool_name, duration_ms, etc.)
    """
    if not redis_client:
        return  # Graceful degradation if Redis unavailable

    try:
        payload = {
            "run_id": run_id,
            "agent_id": agent_id,
            "category": category,
            "message": message,
            "metadata": meta,
            "timestamp": datetime.now().isoformat(),
            **extra
        }

        # Fire-and-forget publish (non-blocking)
        redis_client.publish("raro:live_logs", json.dumps(payload))

    except Exception as e:
        logger.warning(f"Telemetry emit failed: {e}")
        # Don't crash execution if telemetry fails
```

---

#### Step 2.2: Instrument Tool Loop

**File:** `apps/agent-service/src/core/llm.py`

**Location:** Inside `call_gemini_with_context`, the tool execution loop (lines ~367-398)

**Find this code:**
```python
            for tool_name, tool_args in function_calls:
                logger.info(
                    f"[TOOL DETECTED] Agent: {agent_id} | Tool: {tool_name} | Args: {str(tool_args)[:100]}..."
                )

                # Execute
                result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)

                # Capture generated files
                if isinstance(result_dict, dict) and "files_generated" in result_dict:
                    files = result_dict["files_generated"]
                    if isinstance(files, list):
                        all_files_generated.extend(files)
                        logger.debug(f"Captured {len(files)} file(s) from {tool_name}: {files}")

                # Log result
                success = result_dict.get('success', True)
                logger.info(
                    f"[TOOL RESULT] Agent: {agent_id} | Status: {'✓' if success else '✗'}"
                )

                # Format Output for the Model
                tool_outputs_text += f"\n[SYSTEM: Tool '{tool_name}' Result]\n{json.dumps(result_dict, indent=2)}\n"
```

**Replace with:**
```python
            for tool_name, tool_args in function_calls:
                # === [NEW] EMIT: TOOL CALL START ===
                args_str = json.dumps(tool_args)
                emit_telemetry(
                    run_id=run_id,
                    agent_id=safe_agent_id,
                    category="TOOL_CALL",
                    message=f"{tool_name}({args_str[:50]}...)",  # Truncate for clean UI
                    meta="IO_REQ",
                    extra={"tool_name": tool_name, "args": tool_args}
                )

                logger.info(
                    f"[TOOL DETECTED] Agent: {agent_id} | Tool: {tool_name} | Args: {str(tool_args)[:100]}..."
                )

                # Measure execution time
                start_time = datetime.now()

                # Execute
                result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)

                duration_ms = (datetime.now() - start_time).total_seconds() * 1000

                # Capture generated files
                if isinstance(result_dict, dict) and "files_generated" in result_dict:
                    files = result_dict["files_generated"]
                    if isinstance(files, list):
                        all_files_generated.extend(files)
                        logger.debug(f"Captured {len(files)} file(s) from {tool_name}: {files}")

                # === [NEW] EMIT: TOOL RESULT ===
                success = result_dict.get('success', True)

                # Generate smart summary
                output_summary = "Operation complete."
                if "result" in result_dict:
                    res = str(result_dict["result"])
                    output_summary = res[:100] + "..." if len(res) > 100 else res
                elif "error" in result_dict:
                    output_summary = result_dict["error"]

                emit_telemetry(
                    run_id=run_id,
                    agent_id=safe_agent_id,
                    category="TOOL_RESULT",
                    message=output_summary,
                    meta="IO_OK" if success else "IO_ERR",
                    extra={
                        "tool_name": tool_name,
                        "duration_ms": int(duration_ms)
                    }
                )

                logger.info(
                    f"[TOOL RESULT] Agent: {agent_id} | Status: {'✓' if success else '✗'}"
                )

                # Format Output for the Model
                tool_outputs_text += f"\n[SYSTEM: Tool '{tool_name}' Result]\n{json.dumps(result_dict, indent=2)}\n"
```

**Key Changes:**
1. Added `emit_telemetry()` call **before** tool execution (TOOL_CALL)
2. Added timing measurement around execution
3. Added `emit_telemetry()` call **after** tool execution (TOOL_RESULT)
4. Preserved all existing logic (file capture, model output formatting)

---

### Phase 3: Web Console Updates (TypeScript/Svelte) ✅ Ready

#### Step 3.1: Update LogEntry Interface

**File:** `apps/web-console/src/lib/stores.ts`

**Location:** Line 12-19 (LogEntry interface definition)

**Change:**
```typescript
export interface LogEntry {
  id: string
  timestamp: string;
  role: string;
  message: string;
  metadata?: string;
  isAnimated?: boolean;
  category?: string;  // NEW: For tool/thought categorization
}
```

---

#### Step 3.2: Update addLog Function

**File:** `apps/web-console/src/lib/stores.ts`

**Location:** Lines 428-442 (addLog function)

**Find:**
```typescript
export function addLog(role: string, message: string, metadata: string = '', isAnimated: boolean = false, customId?: string) {
  logs.update(l => {
    if (customId && l.find(entry => entry.id === customId)) {
      return l;
    }
    return [...l, {
      id: customId || crypto.randomUUID(),
      timestamp: new Date().toISOString(),
      role,
      message,
      metadata,
      isAnimated
    }];
  });
}
```

**Replace with:**
```typescript
export function addLog(
    role: string,
    message: string,
    metadata: string = '',
    isAnimated: boolean = false,
    customId?: string,
    category?: string  // NEW parameter
) {
  logs.update(l => {
    if (customId && l.find(entry => entry.id === customId)) {
      return l;
    }
    return [...l, {
      id: customId || crypto.randomUUID(),
      timestamp: new Date().toISOString(),
      role,
      message,
      metadata,
      isAnimated,
      category  // NEW: Store category for styling
    }];
  });
}
```

---

#### Step 3.3: Update WebSocket Message Handler

**File:** `apps/web-console/src/lib/stores.ts`

**Location:** Lines 494-533 (`ws.onmessage` handler)

**Find:**
```typescript
  ws.onmessage = (event: any) => {
    console.log('[WS] Message received:', event.data.substring(0, 200));
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'state_update' && data.state) {

        // === APPROVAL DETECTION ===
        const currentState = get(runtimeStore);
        const newStateStr = (data.state.status || '').toUpperCase();

        if (newStateStr === 'AWAITING_APPROVAL' && currentState.status !== 'AWAITING_APPROVAL') {
          const logsList = get(logs);
          const hasPending = logsList.some(l => l.metadata === 'INTERVENTION');

          if (!hasPending) {
            addLog(
              'CORTEX',
              'SAFETY_PATTERN_TRIGGERED',
              'INTERVENTION',
              false,
              'approval-req-' + Date.now()
            );
          }
        }

        syncState(data.state, data.signatures, data.topology);

        if (data.state.status) {
             runtimeStore.update(s => ({ ...s, status: data.state.status.toUpperCase() }));
        }
      } else if (data.error) {
        addLog('KERNEL', `Runtime error: ${data.error}`, 'ERR');
      }
    } catch (e) {
      console.error('[WS] Failed to parse message:', e, event.data);
    }
  };
```

**Replace with:**
```typescript
  ws.onmessage = (event: any) => {
    console.log('[WS] Message received:', event.data.substring(0, 200));
    try {
      const data = JSON.parse(event.data);

      // Existing: State updates
      if (data.type === 'state_update' && data.state) {

        // === APPROVAL DETECTION ===
        const currentState = get(runtimeStore);
        const newStateStr = (data.state.status || '').toUpperCase();

        if (newStateStr === 'AWAITING_APPROVAL' && currentState.status !== 'AWAITING_APPROVAL') {
          const logsList = get(logs);
          const hasPending = logsList.some(l => l.metadata === 'INTERVENTION');

          if (!hasPending) {
            addLog(
              'CORTEX',
              'SAFETY_PATTERN_TRIGGERED',
              'INTERVENTION',
              false,
              'approval-req-' + Date.now()
            );
          }
        }

        syncState(data.state, data.signatures, data.topology);

        if (data.state.status) {
             runtimeStore.update(s => ({ ...s, status: data.state.status.toUpperCase() }));
        }
      }

      // [[NEW]] Intermediate log events
      else if (data.type === 'log_event') {
        const agentId = data.agent_id ? data.agent_id.toUpperCase() : 'SYSTEM';
        const p = data.payload;

        addLog(
          agentId,
          p.message,
          p.metadata || 'INFO',
          false,                    // Not animated (immediate)
          undefined,                // Auto-generate ID
          p.category                // NEW: Pass category for styling
        );
      }

      else if (data.error) {
        addLog('KERNEL', `Runtime error: ${data.error}`, 'ERR');
      }
    } catch (e) {
      console.error('[WS] Failed to parse message:', e, event.data);
    }
  };
```

---

#### Step 3.4: (Optional) Enhanced UI Rendering

**File:** `apps/web-console/src/components/OutputPane.svelte`

**Purpose:** Special styling for TOOL_CALL and TOOL_RESULT logs

**Location:** Inside the log rendering loop (find `{#each $logs as log}`)

**Enhancement:**
Add conditional rendering based on `log.category`:

```svelte
{#each $logs as log (log.id)}
  <div class="log-entry {log.category ? log.category.toLowerCase() : 'standard'}">

    <!-- Tool Call -->
    {#if log.category === 'TOOL_CALL'}
      <div class="tool-call">
        <span class="agent-badge">{log.role}</span>
        <span class="arrow">→</span>
        <code class="tool-name">{log.message}</code>
        <span class="meta-tag req">{log.metadata}</span>
      </div>

    <!-- Tool Result -->
    {:else if log.category === 'TOOL_RESULT'}
      <div class="tool-result">
        <span class="result-arrow">↳</span>
        <span class="result-text">{log.message}</span>
        <span class="meta-tag {log.metadata === 'IO_ERR' ? 'err' : 'ok'}">{log.metadata}</span>
      </div>

    <!-- Standard Log -->
    {:else}
      <!-- Existing log rendering logic -->
      <span class="log-role">{log.role}</span>
      <div class="log-content">
        <!-- SmartText, Typewriter, etc. -->
      </div>
    {/if}
  </div>
{/each}
```

**Styles to Add:**
```css
/* Tool Call Styling */
.log-entry.tool_call {
  background: rgba(255, 193, 7, 0.05);
  border-left: 3px solid var(--alert-amber);
  padding: 8px 12px;
  font-family: var(--font-code);
}

.tool-call {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
}

.tool-name {
  color: var(--arctic-cyan);
  font-weight: 600;
}

.arrow {
  color: var(--alert-amber);
  font-size: 14px;
}

/* Tool Result Styling */
.log-entry.tool_result {
  background: rgba(76, 175, 80, 0.05);
  border-left: 3px solid var(--signal-success);
  padding: 8px 12px;
  font-family: var(--font-code);
  margin-left: 20px;  /* Indent to show relationship */
}

.tool-result {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
}

.result-arrow {
  color: var(--signal-success);
}

.meta-tag.req {
  color: var(--alert-amber);
  border-color: var(--alert-amber);
}

.meta-tag.err {
  color: #d32f2f;
  border-color: #d32f2f;
}

.meta-tag.ok {
  color: var(--signal-success);
  border-color: var(--signal-success);
}
```

---

## Testing Strategy

### Unit Tests

1. **Rust: Redis Subscriber**
   - Verify pubsub connection establishes
   - Verify messages are parsed correctly
   - Verify events are forwarded to event bus

2. **Python: Telemetry Emission**
   - Verify emit_telemetry publishes to Redis
   - Verify graceful degradation if Redis unavailable
   - Verify message structure matches protocol

3. **TypeScript: WebSocket Handler**
   - Verify log_event messages are processed
   - Verify category field is preserved
   - Verify addLog is called with correct params

### Integration Tests

1. **End-to-End Flow**
   - Start workflow with tool-using agent
   - Verify TOOL_CALL appears in UI before execution
   - Verify TOOL_RESULT appears immediately after
   - Verify no blocking/delays

2. **Error Handling**
   - Redis connection failure (Agent Service should continue)
   - Event bus full (should drop oldest, not block)
   - Malformed messages (should log warning, continue)

3. **Performance**
   - Measure latency: Python emit → UI display (target: <100ms)
   - Verify no memory leaks in long-running workflows
   - Verify subscriber task doesn't crash on invalid JSON

---

## Migration & Rollback

### Backward Compatibility
- **100% backward compatible** - all existing functionality preserved
- Polling-based state updates remain unchanged
- If Redis unavailable, system degrades gracefully (no live logs, but execution continues)

### Rollback Plan
If issues arise:
1. Comment out Redis subscriber task in `main.rs`
2. Remove `IntermediateLog` handling from `handlers.rs`
3. No Agent Service changes needed (fire-and-forget publishes are harmless)

---

## Performance Considerations

### Message Volume
- **TOOL_CALL + TOOL_RESULT:** 2 messages per tool invocation
- **Example:** Agent with 5 tool calls = 10 messages
- **Redis throughput:** 100k+ messages/sec (far exceeds needs)

### Latency Budget
- Redis publish: <1ms
- Event bus broadcast: <1ms
- WebSocket send: <10ms (network dependent)
- **Total: ~12ms** from Python to UI

### Memory
- Event bus uses bounded channel (default 1024 events)
- Old events drop if channel full (backpressure protection)
- Redis pubsub uses minimal memory (streaming)

---

## Visual Example: Before/After

### Before (Current)
```
[SYSTEM] RESEARCHER
         Analyzing user request...

[20 seconds of silence]

[SYSTEM] RESEARCHER
         Analysis complete. Results: ...
```

### After (With Live Logs)
```
[SYSTEM] RESEARCHER
         Analyzing user request...

[IO_REQ]  RESEARCHER → web_search(query="RARO docs")

[IO_OK]   ↳ Found 12 citations (450ms)

[IO_REQ]  RESEARCHER → read_file(path="api_ref.md")

[IO_OK]   ↳ Read 14kb (23ms)

[SYSTEM] RESEARCHER
         Analysis complete. Results: ...
```

---

## Files Modified Summary

### Rust (3 files)
1. `apps/kernel-server/src/events.rs` - Add IntermediateLog variant
2. `apps/kernel-server/src/main.rs` - Add Redis subscriber task
3. `apps/kernel-server/src/server/handlers.rs` - Add event bus select arm

### Python (1 file)
1. `apps/agent-service/src/core/llm.py` - Add emit_telemetry + instrument tool loop

### TypeScript (1 file)
1. `apps/web-console/src/lib/stores.ts` - Add category field + log_event handler

### Svelte (1 file - Optional)
1. `apps/web-console/src/components/OutputPane.svelte` - Enhanced tool rendering

**Total:** 6 files (5 required, 1 optional)

---

## Next Steps (Post-Approval)

1. Add `futures` crate to Kernel `Cargo.toml`
2. Implement Phase 1 (Kernel changes)
3. Verify Rust compilation
4. Implement Phase 2 (Python changes)
5. Verify Python syntax
6. Implement Phase 3 (Frontend changes)
7. Test end-to-end with tool-using workflow
8. (Optional) Enhance OutputPane styling

---

**Implementation Status:** READY FOR APPROVAL AND EXECUTION
**Estimated Time:** 2-3 hours for full implementation
**Risk Level:** LOW (fully backward compatible, graceful degradation)
