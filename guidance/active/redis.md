Here is the implementation guide to transition RARO to the **Artifact Store Pattern**. This ensures the Kernel remains a lightweight control plane while Redis handles the heavy data payloads.

### Phase 1: Infrastructure Configuration

Your `docker-compose.yml` has a network mismatch. The services are on `raro-net` but Redis/Postgres are on `raro`. They cannot talk to each other.

**File:** `docker-compose.yml`
**Action:** Unify the networks.

```yaml
version: '3.8'

services:
  kernel:
    # ... (keep build/ports)
    environment:
      - REDIS_URL=redis://redis:6379 # Add this
    depends_on:
      agents:
        condition: service_started
      redis:
        condition: service_healthy # Wait for Redis
    networks:
      - raro-net # Unified network

  agents:
    # ... (keep existing)
    networks:
      - raro-net

  web:
    # ... (keep existing)
    networks:
      - raro-net

  # Redis Configuration
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    networks:
      - raro-net # Changed from 'raro'
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # PostgreSQL (Optional for now, but on correct net)
  postgres:
    # ... (keep existing)
    networks:
      - raro-net # Changed from 'raro'

networks:
  raro-net:
    driver: bridge
```

---

### Phase 2: Kernel Implementation (Rust)

We need to add the Redis client, update the data models to store references instead of data, and implement the artifact storage logic.

#### 1. Dependencies
**File:** `apps/kernel-server/Cargo.toml`
**Action:** Add the `redis` crate with async features.

```toml
[dependencies]
# ... existing dependencies
redis = { version = "0.24", features = ["tokio-comp"] }
```

#### 2. Models Update
**File:** `apps/kernel-server/src/models.rs`
**Action:** Replace `output` with `artifact_id`.

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentInvocation {
    pub id: String,
    pub agent_id: String,
    pub model_variant: ModelVariant,
    pub thought_signature: Option<String>,
    pub tools_used: Vec<String>,
    pub tokens_used: usize,
    pub latency_ms: u64,
    pub status: InvocationStatus,
    pub timestamp: String,
    pub artifact_id: Option<String>, // new field
}
```

#### 3. Runtime Logic
**File:** `apps/kernel-server/src/runtime.rs`
**Action:** Inject Redis client and offload results.

```rust
// Add imports
use redis::AsyncCommands;

pub struct RARORuntime {
    // ... existing fields
    pub redis_client: redis::Client, // Make public for handlers to access
}

impl RARORuntime {
    pub fn new() -> Self {
        // Initialize Redis Client
        let redis_url = env::var("REDIS_URL").unwrap_or_else(|_| "redis://127.0.0.1:6379".to_string());
        let redis_client = redis::Client::open(redis_url).expect("Invalid Redis URL");

        RARORuntime {
            // ... existing inits
            redis_client,
        }
    }
    
    // ... inside execute_dag ...
    
    // 4. Handle Response
    match response {
        Ok(res) => {
            if res.success {
                // ... signature handling ...

                // === ARTIFACT STORE LOGIC ===
                let artifact_id = format!("run:{}:agent:{}:output", run_id, agent_id);
                
                // Serialize the full output
                if let Some(output_data) = res.output {
                    let json_str = serde_json::to_string(&output_data).unwrap_or_default();
                    
                    // Async Redis Set with 1 Hour TTL
                    let mut con = self.redis_client.get_async_connection().await.ok();
                    if let Some(mut c) = con {
                        let _: redis::RedisResult<()> = c.set_ex(&artifact_id, json_str, 3600).await;
                    } else {
                        tracing::error!("Failed to connect to Redis to save artifact");
                    }
                }

                // Record metrics with REFERENCE
                let invocation_record = AgentInvocation {
                    // ... other fields
                    output: None, // Remove if you kept the field, or just don't set it
                    artifact_id: Some(artifact_id), // Point to Redis
                };
                
                // ... record invocation ...
            }
        }
    }
}
```

#### 4. API Handler
**File:** `apps/kernel-server/src/server/handlers.rs`
**Action:** Add endpoint to fetch data from Redis.

```rust
// Add imports
use redis::AsyncCommands;

// ... existing handlers ...

pub async fn get_artifact(
    State(runtime): State<Arc<RARORuntime>>,
    Path((run_id, agent_id)): Path<(String, String)>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let key = format!("run:{}:agent:{}:output", run_id, agent_id);
    
    let mut con = runtime.redis_client
        .get_async_connection()
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    let data: String = con.get(key).await.map_err(|_| StatusCode::NOT_FOUND)?;
    
    let json_val: serde_json::Value = serde_json::from_str(&data)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    Ok(Json(json_val))
}
```

#### 5. Router Update
**File:** `apps/kernel-server/src/main.rs`
**Action:** Register the new route.

```rust
// ... inside main ...
let app = Router::new()
    // ... existing routes
    .route("/runtime/:run_id/artifact/:agent_id", get(handlers::get_artifact)) // NEW
    .layer(cors)
    .with_state(runtime);
```

---

### Phase 3: Web Console Implementation (Svelte)

Now update the frontend to listen for success, then fetch the data.

**File:** `apps/web-console/src/lib/stores.ts`
**Action:** Implement Lazy Loading.

```typescript
// ... inside syncState function ...

// 5. Sync Logs (Lazy Load Artifacts)
if (state.invocations && Array.isArray(state.invocations)) {
  state.invocations.forEach(async (inv: any) => {
    if (!processedInvocationIds.has(inv.id)) {
      processedInvocationIds.add(inv.id);
      
      const agentLabel = inv.agent_id.toUpperCase();
      
      if (inv.status === 'success') {
        // 1. Log Loading State
        const loadingId = crypto.randomUUID(); // Temp ID to update later if desired
        addLog(agentLabel, "Processing complete. Fetching artifacts...", "LOADING");

        // 2. Fetch Heavy Data from Kernel API
        try {
            const res = await fetch(`/api/runtime/${state.run_id}/artifact/${inv.agent_id}`);
            if (res.ok) {
                const data = await res.json();
                
                // Extract text from Gemini JSON structure
                let text = "Output received";
                if (data.result) text = data.result;
                else if (data.output) text = data.output;
                
                // 3. Log Actual Content
                addLog(agentLabel, text, `TOKENS: ${inv.tokens_used}`);
            } else {
                addLog(agentLabel, "Artifact not found in store.", "WARN");
            }
        } catch (e) {
            console.error(e);
            addLog(agentLabel, "Failed to retrieve output artifacts.", "NET_ERR");
        }

      } else if (inv.status === 'failed') {
        addLog(agentLabel, "Agent execution failed.", 'ERR');
      }
    }
  });
}
```

### Summary of Resulting Flow

1.  **Orchestration:** Kernel coordinates agents via DAG.
2.  **Execution:** Agent Service computes result.
3.  **Storage:** Kernel puts result into Redis (`run:X:agent:Y:output`) and keeps only the key in memory.
4.  **Notification:** Kernel sends lightweight JSON via WebSocket to UI ("Agent Y finished").
5.  **Retrieval:** UI sees "Finished", calls `GET /runtime/X/artifact/Y`, retrieves full text, and displays it.

This architecture is robust, scalable, and keeps your Docker logs and WebSocket channels clean.