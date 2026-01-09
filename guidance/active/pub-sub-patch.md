To pipe intermediate progress (tool calls, thoughts, partial steps) from the Python Agent Service back to the User Interface.



1.  **Redis Pub/Sub (Recommended):**
    *   **How:** The Agent Service publishes log messages to a Redis channel (e.g., `raro:live_logs`) as it executes tools. The Kernel Server subscribes to this channel and forwards messages to the WebSocket connected to the UI.
    *   **Pros:** Highly decoupled. Uses infrastructure you already have (`redis`). Doesn't require rewriting the synchronous HTTP contract between Kernel and Agent.
    *   **Cons:** Requires a background listener task in the Kernel.


#### 1. Agent Service: Emit Logs (`apps/agent-service/src/core/llm.py`)

We need to modify `call_gemini_with_context` to publish logs to Redis whenever a tool is called or returns.

```python
# [[RARO]]/apps/agent-service/src/core/llm.py

# ... imports ...
from core.config import gemini_client, logger, resolve_model, settings, redis_client # Ensure redis_client is imported

# ... (Previous code) ...

# === NEW HELPER ===
def emit_intermediate_log(run_id: str, agent_id: str, message: str, meta: str = "TOOL"):
    """Fire-and-forget log to Redis for the Kernel to pick up."""
    if not redis_client: return
    try:
        payload = json.dumps({
            "run_id": run_id,
            "agent_id": agent_id,
            "type": "intermediate_log",
            "message": message,
            "metadata": meta,
            "timestamp": datetime.now().isoformat()
        })
        # Publish to a global channel that the Kernel listens to
        redis_client.publish("raro:live_logs", payload)
    except Exception as e:
        logger.warning(f"Failed to emit intermediate log: {e}")

# ... (Inside call_gemini_with_context function) ...

async def call_gemini_with_context(...):
    # ... (setup code) ...

    try:
        # ... (probe_sink logic) ...
        
        # [EXISTING LOOP START]
        while turn_count < max_turns:
            turn_count += 1

            # 1. Call LLM
            # ... (generate_content) ...
            
            # ... (extract text) ...

            # 3. Parse for Manual Function Calls
            function_calls = parse_function_calls(content_text)

            if not function_calls:
                final_response_text = content_text
                break

            # === [MODIFIED BLOCK START] ===
            # 4. Process Tool Calls
            tool_outputs_text = ""

            for tool_name, tool_args in function_calls:
                # 1. LOG START
                log_msg = f"Executing tool: {tool_name}"
                logger.info(f"[TOOL] {agent_id} -> {tool_name}")
                emit_intermediate_log(run_id, safe_agent_id, log_msg, "TOOL_CALL")

                # Execute
                result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)

                # ... (file capture logic) ...

                # 2. LOG RESULT
                success = result_dict.get('success', True)
                result_summary = str(result_dict.get('result', 'No output'))[:100] + "..."
                emit_intermediate_log(
                    run_id, 
                    safe_agent_id, 
                    f"Tool output: {result_summary}", 
                    "TOOL_OK" if success else "TOOL_ERR"
                )

                # Format Output for the Model
                tool_outputs_text += f"\n[SYSTEM: Tool '{tool_name}' Result]\n{json.dumps(result_dict, indent=2)}\n"
            
            # === [MODIFIED BLOCK END] ===

            # ... (rest of loop) ...
```

#### 2. Kernel Server: Define Event (`apps/kernel-server/src/events.rs`)

Add a new event type so the frontend knows how to handle it.

```rust
// [[RARO]]/apps/kernel-server/src/events.rs

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EventType {
    NodeCreated,
    AgentStarted,
    AgentCompleted,
    AgentFailed,
    ToolCall,
    SystemIntervention,
    // === NEW ===
    IntermediateLog, 
}
```

#### 3. Kernel Server: Subscribe & Broadcast (`apps/kernel-server/src/main.rs`)

Spawn a background task to listen to Redis and inject messages into the `event_bus`.

```rust
// [[RARO]]/apps/kernel-server/src/main.rs

// ... imports ...
use futures::StreamExt; // Ensure futures is imported

#[tokio::main]
async fn main() {
    // ... (tracing init) ...
    // ... (runtime init) ...

    // === NEW: REDIS LOG SUBSCRIBER ===
    // Spawns a dedicated connection to listen for logs from Python agents
    if let Some(redis_client) = &runtime.redis_client {
        let client = redis_client.clone();
        let event_bus = runtime.event_bus.clone();
        
        tokio::spawn(async move {
            tracing::info!("🎧 Started Redis Log Subscriber on 'raro:live_logs'");
            
            // Get pubsub connection
            let mut pubsub_conn = client.get_async_connection().await
                .expect("Failed to connect to Redis for PubSub")
                .into_pubsub();

            if let Err(e) = pubsub_conn.subscribe("raro:live_logs").await {
                tracing::error!("Failed to subscribe to logs: {}", e);
                return;
            }

            let mut stream = pubsub_conn.on_message();

            while let Some(msg) = stream.next().await {
                // Parse Payload: { run_id, agent_id, message, metadata, ... }
                let payload_str: String = msg.get_payload().unwrap_or_default();
                
                if let Ok(data) = serde_json::from_str::<serde_json::Value>(&payload_str) {
                    let run_id = data["run_id"].as_str().unwrap_or_default();
                    let agent_id = data["agent_id"].as_str();
                    let message = data["message"].as_str().unwrap_or("");
                    let metadata = data["metadata"].as_str().unwrap_or("INFO");

                    // Bridge to internal Event Bus (sends to WebSockets)
                    let _ = event_bus.send(crate::events::RuntimeEvent::new(
                        run_id,
                        crate::events::EventType::IntermediateLog,
                        agent_id.map(|s| s.to_string()),
                        serde_json::json!({
                            "message": message,
                            "metadata": metadata
                        })
                    ));
                }
            }
        });
    }

    // ... (rest of main: rehydration, cortex, http server) ...
}
```

#### 4. Web Console: Handle Event (`apps/web-console/src/lib/stores.ts`)

Update the WebSocket handler to process the new event type.

```typescript
// [[RARO]]/apps/web-console/src/lib/stores.ts

// ...

ws.onmessage = (event: any) => {
  try {
    const data = JSON.parse(event.data);
    
    // 1. Existing State Update Logic
    if (data.type === 'state_update' && data.state) {
        // ... (existing syncState logic) ...
    } 
    
    // 2. === NEW: EVENT STREAM HANDLING ===
    // The Kernel emits `RuntimeEvent` objects via the WS stream too (if configured in handlers.rs)
    // OR we might need to verify handlers.rs sends specific event packets.
    // Assuming handlers.rs logic:
    /* 
       update = json!({
         "type": "state_update", ...
       });
       
       We need to ensure handlers.rs also forwards raw events, or we pack events into state updates.
       Alternatively, simpler implementation in UI:
    */
    
    // Check if the data structure matches a RuntimeEvent wrapper
    // (You might need to adjust handlers.rs to forward these specific events, 
    // or rely on the fact that logs are usually derived from state invocations.
    // HOWEVER, for intermediate logs, we need immediate forwarding).
  }
  // ...
};
```

**Correction for Step 4:**
The `handlers.rs` in your provided code currently *polls* for state. To support real-time events, we need to modify `handlers.rs` to subscribe to the `event_bus` and push events down the WebSocket.

#### 5. Kernel Server: WebSocket Forwarding (`apps/kernel-server/src/server/handlers.rs`)

Modify `handle_runtime_stream` to select between the Interval Tick and the Event Bus.

```rust
// [[RARO]]/apps/kernel-server/src/server/handlers.rs

async fn handle_runtime_stream(
    socket: WebSocket,
    runtime: Arc<RARORuntime>,
    run_id: String,
) {
    let (mut sender, mut receiver) = socket.split();
    
    // 1. Subscribe to Event Bus
    let mut bus_rx = runtime.event_bus.subscribe();

    // ... (initial state send) ...

    let mut interval = tokio::time::interval(std::time::Duration::from_millis(250));

    loop {
        tokio::select! {
            // A. Client Disconnect
            msg = receiver.next() => {
                if msg.is_none() { break; }
            }

            // B. Periodic State Sync (Keep this for robustness)
            _ = interval.tick() => {
                // ... (existing state update logic) ...
            }

            // C. === NEW: Real-time Events ===
            Ok(event) = bus_rx.recv() => {
                // Only forward events for THIS run_id
                if event.run_id == run_id {
                    // Filter specifically for Logs (or all events if you want)
                    if let crate::events::EventType::IntermediateLog = event.event_type {
                        let ws_msg = json!({
                            "type": "log_event",
                            "agent_id": event.agent_id,
                            "payload": event.payload,
                            "timestamp": event.timestamp
                        });
                        
                        if sender.send(Message::Text(ws_msg.to_string())).await.is_err() {
                            break;
                        }
                    }
                }
            }
        }
    }
}
```

#### 6. Web Console: Final UI Hook (`apps/web-console/src/lib/stores.ts`)

Now update `stores.ts` to catch `type: "log_event"`.

```typescript
// [[RARO]]/apps/web-console/src/lib/stores.ts

ws.onmessage = (event: any) => {
    const data = JSON.parse(event.data);

    // Existing State Logic
    if (data.type === 'state_update') {
        syncState(data.state, data.signatures, data.topology);
    }
    
    // === NEW: Intermediate Log Handler ===
    else if (data.type === 'log_event') {
        const agentId = data.agent_id ? data.agent_id.toUpperCase() : 'SYSTEM';
        const msg = data.payload.message;
        const meta = data.payload.metadata || 'INFO';
        
        // Add to log store immediately
        addLog(agentId, msg, meta, false); 
    }
};
```


With the Redis Pub/Sub architecture, you are essentially creating a **real-time telemetry stream**.

Here is exactly what the messages will look like, how to structure the payload for maximum utility, and how to render a "Live Agent HUD" in your frontend.

### 1. The Message Protocol (Python to UI)

We will define three distinct categories of intermediate logs. The Python Agent Service will broadcast these JSON payloads to Redis.

#### A. The "Thinking" Pulse
Sent when the agent starts processing but hasn't called a tool yet.
```json
{
  "type": "log_event",
  "agent_id": "researcher",
  "payload": {
    "category": "THOUGHT",
    "message": "Analyzing request parameters...",
    "metadata": "PLANNING"
  }
}
```

#### B. The "Tool Call" (Request)
Sent immediately before `execute_tool_call`.
```json
{
  "type": "log_event",
  "agent_id": "researcher",
  "payload": {
    "category": "TOOL_CALL",
    "message": "web_search(query='RARO architecture')",
    "metadata": "IO_REQ", 
    "tool_name": "web_search",
    "tool_args": { "query": "RARO architecture" }
  }
}
```

#### C. The "Tool Result" (Response)
Sent immediately after `execute_tool_call` returns.
```json
{
  "type": "log_event",
  "agent_id": "researcher",
  "payload": {
    "category": "TOOL_RESULT",
    "message": "Found 12 citations.",
    "metadata": "IO_OK", // or IO_ERR
    "tool_name": "web_search",
    "duration_ms": 450
  }
}
```

---

### 2. Implementation: Agent Service (`llm.py`)

We modify the loop in `apps/agent-service/src/core/llm.py` to emit these payloads.

```python
# In src/core/llm.py

# Helper function
def emit_telemetry(run_id: str, agent_id: str, category: str, message: str, meta: str, extra: dict = {}):
    if not redis_client: return
    try:
        payload = {
            "run_id": run_id,
            "agent_id": agent_id,
            "message": message,
            "metadata": meta,
            "category": category, # Used for UI styling
            "timestamp": datetime.now().isoformat(),
            **extra
        }
        # Publish stringified JSON
        redis_client.publish("raro:live_logs", json.dumps(payload))
    except Exception as e:
        logger.warning(f"Telemetry emit failed: {e}")

# ... Inside call_gemini_with_context loop ...

    # 4. Process Tool Calls
    for tool_name, tool_args in function_calls:
        
        # 1. EMIT: TOOL CALL
        args_str = json.dumps(tool_args)
        emit_telemetry(
            run_id, safe_agent_id, 
            category="TOOL_CALL",
            message=f"{tool_name}({args_str[:50]}...)", # Truncate for clean UI
            meta="IO_REQ",
            extra={"tool_name": tool_name, "args": tool_args}
        )

        start_time = datetime.now()
        
        # EXECUTE
        result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)
        
        duration = (datetime.now() - start_time).total_seconds() * 1000

        # 2. EMIT: TOOL RESULT
        success = result_dict.get('success', True)
        
        # Smart summary based on output type
        output_summary = "Operation complete."
        if "result" in result_dict:
            res = str(result_dict["result"])
            output_summary = res[:100] + "..." if len(res) > 100 else res
        elif "error" in result_dict:
            output_summary = result_dict["error"]

        emit_telemetry(
            run_id, safe_agent_id,
            category="TOOL_RESULT", 
            message=output_summary,
            meta="IO_OK" if success else "IO_ERR",
            extra={"tool_name": tool_name, "duration_ms": int(duration)}
        )
```

---

### 3. Frontend Visualization (`Web Console`)

To render this cleanly, we will update `OutputPane.svelte`. We don't want these logs to look like chat bubbles; we want them to look like **system operations**.

#### Updated `stores.ts` (Handling the data)

```typescript
// apps/web-console/src/lib/stores.ts

ws.onmessage = (event: any) => {
    const data = JSON.parse(event.data);

    if (data.type === 'log_event') {
        const p = data.payload; // This matches the Python structure above
        
        addLog(
            data.agent_id.toUpperCase(), 
            p.message, 
            p.metadata, 
            false, 
            undefined, 
            p.category // Pass category to addLog for styling
        );
    }
    // ... rest of code
}

// Update addLog signature to accept category
export function addLog(role: string, message: string, metadata: string = '', isAnimated: boolean = false, customId?: string, category?: string) {
    logs.update(l => [...l, {
        id: customId || crypto.randomUUID(),
        timestamp: new Date().toISOString(),
        role,
        message,
        metadata,
        isAnimated,
        category // Store this!
    }]);
}
```

#### Updated `OutputPane.svelte` (The "Live View")

We will add special rendering for `TOOL_CALL` and `TOOL_RESULT` to make them look technical and distinct from chat text.

```svelte
<!-- apps/web-console/src/components/OutputPane.svelte -->

<script>
  // ... existing imports
  // Helper to format tool arguments nicely
  function formatToolCall(msg: string) {
      // Basic highlighting for tool syntax "tool(args)"
      return msg.replace(/([a-zA-Z0-9_]+)(\(.*?\))/, '<span class="syntax-fn">$1</span><span class="syntax-args">$2</span>');
  }
</script>

<!-- Inside the {#each $logs as log} loop -->

<div class="log-entry {log.category ? log.category.toLowerCase() : 'std'}">
    
    <!-- META COLUMN -->
    <div class="log-meta">
        <span class="meta-tag {log.metadata === 'IO_ERR' ? 'err' : ''} {log.metadata === 'IO_REQ' ? 'req' : ''}">
            {log.metadata || 'SYS'}
        </span>
        {#if log.category === 'TOOL_CALL'}
            <div class="connector-line"></div> <!-- Vertical line connecting Call to Result -->
        {/if}
    </div>

    <!-- BODY COLUMN -->
    <div class="log-body">
        
        <!-- TOOL CALL RENDERER -->
        {#if log.category === 'TOOL_CALL'}
            <div class="tool-req-row">
                <span class="agent-badge">{log.role}</span>
                <span class="op-arrow">→</span>
                <code class="tool-code">{@html formatToolCall(log.message)}</code>
                <span class="loading-pulse">...</span>
            </div>

        <!-- TOOL RESULT RENDERER -->
        {:else if log.category === 'TOOL_RESULT'}
            <div class="tool-res-row">
                <span class="res-arrow">↳</span>
                <span class="res-text">{log.message}</span>
            </div>

        <!-- STANDARD MESSAGE -->
        {:else}
            <span class="log-role">{log.role}</span>
            <div class="log-content">
                 <!-- existing SmartText/Typewriter/Artifact logic -->
            </div>
        {/if}
    </div>
</div>

<style>
    /* ... existing styles ... */

    /* === TOOL LOG STYLING === */
    
    /* Make tool logs more compact */
    .log-entry.tool_call, .log-entry.tool_result {
        padding: 8px 0;
        border-top: none; /* Remove separator for tight coupling */
    }

    /* Meta Tags Colors */
    .meta-tag.req { color: var(--alert-amber); border-color: var(--alert-amber); }
    .meta-tag.err { color: #d32f2f; border-color: #d32f2f; }

    /* Tool Call Row */
    .tool-req-row {
        display: flex; align-items: center; gap: 10px;
        font-family: var(--font-code); font-size: 11px;
    }
    .agent-badge { font-weight: 700; color: var(--paper-line); font-size: 9px; }
    .op-arrow { color: var(--alert-amber); }
    
    /* Syntax Highlighting */
    :global(.syntax-fn) { color: var(--arctic-cyan); font-weight: 700; }
    :global(.mode-archival .syntax-fn) { color: #005cc5; }
    :global(.syntax-args) { opacity: 0.7; }

    .loading-pulse { animation: blink 1s infinite; color: var(--paper-line); }

    /* Tool Result Row */
    .tool-res-row {
        display: flex; align-items: flex-start; gap: 10px;
        font-family: var(--font-code); font-size: 11px;
        color: var(--paper-ink); opacity: 0.8;
        padding-left: 4px; /* Indent slightly */
    }
    .res-arrow { color: var(--signal-success); transform: scaleX(-1); display: inline-block; }
    
    /* Connector Line (Visual thread) */
    .connector-line {
        width: 1px; height: 20px;
        background: var(--paper-line);
        opacity: 0.3;
        margin: 4px auto 0 auto;
    }
</style>
```

### The Result: What the User Sees

With this implementation, your console will look like a structured execution trace rather than just a chat window.

**Visual Example:**

```text
[IO_REQ]  RESEARCHER → web_search(query="RARO docs") ...
   |
[IO_OK]   ↳ Found 12 citations (Documentation, API Reference...)
   |
[IO_REQ]  RESEARCHER → read_file(filename="api_ref.md") ...
   |
[IO_OK]   ↳ Read 14kb. Content: "# RARO Kernel API..."
   |
[SYSTEM]  RESEARCHER
          Based on the documentation, I will now configure the workflow...
```

This effectively gives you a **Live Stack Trace** of your agent's cognition loop, satisfying the requirement for "clean" and "live" rendering of tools.