Phase 3 is where we transform the Kernel from a "reactive task runner" into an **observable, pattern-matching engine**.

This involves implementing the "Cortex" architecture inspired by Atlas:
1.  **Event Bus (`events.rs`):** A centralized channel for broadcasting lifecycle events (NodeCreated, Error, ToolUsed).
2.  **Pattern Registry (`registry.rs`):** A storage for ECA (Event-Condition-Action) rules.
3.  **Observer Logic:** Wiring the `RARORuntime` to emit events and a background worker to consume them.

---

### Step 1: Create `apps/kernel-server/src/events.rs`

This defines the signals that the nervous system carries.

```rust
// [[RARO]]/apps/kernel-server/src/events.rs
// Purpose: Event definitions for the Nervous System (Pattern Engine).
// Architecture: Domain Event Layer
// Dependencies: Serde, Chrono

use serde::{Deserialize, Serialize};
use serde_json::Value;
use chrono::Utc;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EventType {
    /// A new agent node has been added to the DAG (static or dynamic)
    NodeCreated,
    /// An agent started execution
    AgentStarted,
    /// An agent completed successfully
    AgentCompleted,
    /// An agent failed
    AgentFailed,
    /// An agent requested a tool (e.g., shell, python)
    ToolCall,
    /// A human/system intervention
    SystemIntervention,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeEvent {
    pub id: String,
    pub run_id: String,
    pub event_type: EventType,
    pub agent_id: Option<String>,
    pub timestamp: String,
    pub payload: Value,
}

impl RuntimeEvent {
    pub fn new(run_id: &str, event_type: EventType, agent_id: Option<String>, payload: Value) -> Self {
        Self {
            id: uuid::Uuid::new_v4().to_string(),
            run_id: run_id.to_string(),
            event_type,
            agent_id,
            timestamp: Utc::now().to_rfc3339(),
            payload,
        }
    }
}
```

---

### Step 2: Create `apps/kernel-server/src/registry.rs`

This manages the active patterns. For this phase, we implement a simplified registry that holds hard-coded safety patterns (Flow C support foundation).

```rust
// [[RARO]]/apps/kernel-server/src/registry.rs
// Purpose: Pattern Registry. Stores active Event-Condition-Action rules.
// Architecture: Cortex Layer
// Dependencies: DashMap, Models

use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use crate::models::AgentNodeConfig;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Pattern {
    pub id: String,
    pub name: String,
    /// The event type that wakes this pattern up (matched against EventType debug string)
    pub trigger_event: String, 
    /// JSONPath-like filter string (e.g., "$.payload.tool == 'fs_delete'")
    pub condition: String,
    pub action: PatternAction,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum PatternAction {
    /// Stop the agent immediately
    Interrupt { reason: String },
    /// Pause and ask for human approval
    RequestApproval { reason: String },
    /// Spawn a "fixer" agent to handle the error (Flow C / Self-Healing)
    SpawnAgent { config: AgentNodeConfig },
}

pub struct PatternRegistry {
    patterns: DashMap<String, Pattern>,
}

impl PatternRegistry {
    pub fn new() -> Self {
        let registry = Self {
            patterns: DashMap::new(),
        };
        
        // Initialize Default Safety Patterns (Proof of Concept)
        registry.register_default_patterns();
        
        registry
    }

    pub fn register(&self, pattern: Pattern) {
        self.patterns.insert(pattern.id.clone(), pattern);
    }

    /// Retrieve all patterns listening for a specific event type
    pub fn get_patterns_for_trigger(&self, event_type: &str) -> Vec<Pattern> {
        self.patterns
            .iter()
            .filter(|p| p.trigger_event == event_type)
            .map(|p| p.value().clone())
            .collect()
    }

    fn register_default_patterns(&self) {
        // 1. Safety Guard: Prevent file deletion
        // This corresponds to Atlas 'prevent_destructive_shell'
        self.register(Pattern {
            id: "guard_fs_delete".to_string(),
            name: "Prevent File Deletion".to_string(),
            trigger_event: "ToolCall".to_string(),
            condition: "fs_delete".to_string(), // Simplified matching for Phase 3 MVP
            action: PatternAction::Interrupt { 
                reason: "Safety Violation: File deletion is prohibited by system policy.".to_string() 
            },
        });

        // 2. Infinite Loop Detector (Heuristic)
        // If an agent fails 3 times, stop the run.
        // Note: Real implementation requires stateful counting, this is a stateless example.
        self.register(Pattern {
            id: "guard_max_failures".to_string(),
            name: "Max Failure Guard".to_string(),
            trigger_event: "AgentFailed".to_string(),
            condition: "*".to_string(), 
            action: PatternAction::RequestApproval { 
                reason: "Agent failed. Requesting human intervention before retry.".to_string() 
            },
        });
    }
}
```

---

### Step 3: Wire it into `runtime.rs`

We need to add the event channel to the `RARORuntime` struct and emit events during execution.

**Modifications to `runtime.rs`:**

1.  **Add Imports:**
    ```rust
    use crate::events::{RuntimeEvent, EventType};
    use crate::registry::{PatternRegistry, PatternAction};
    use tokio::sync::broadcast;
    ```

2.  **Update Struct:**
    ```rust
    pub struct RARORuntime {
        // ... existing fields ...
        pub event_bus: broadcast::Sender<RuntimeEvent>,
        pub pattern_registry: Arc<PatternRegistry>,
    }
    ```

3.  **Update `new()`:**
    ```rust
    pub fn new() -> Self {
        let (tx, _) = broadcast::channel(100); // Buffer 100 events
        
        RARORuntime {
            // ... existing fields ...
            event_bus: tx,
            pattern_registry: Arc::new(PatternRegistry::new()),
            // ...
        }
    }
    ```

4.  **Add `emit_event` helper:**
    ```rust
    fn emit_event(&self, event: RuntimeEvent) {
        // Broadcast to subscribers (Observers, WebSocket, PatternEngine)
        let _ = self.event_bus.send(event);
    }
    ```

5.  **Inject Emitters in `execute_dynamic_dag`:**
    *   Start of loop: `emit_event(EventType::AgentStarted)`
    *   Completion: `emit_event(EventType::AgentCompleted)`
    *   Failure: `emit_event(EventType::AgentFailed)`

---

### Step 4: The "Cortex" Loop (Main.rs)

Finally, we need a background task that listens to the bus and executes patterns.

**In `apps/kernel-server/src/main.rs`:**

```rust
// Add this logic before starting axum::serve

// === CORTEX: Pattern Engine ===
let mut rx = runtime.event_bus.subscribe();
let runtime_ref = runtime.clone();

tokio::spawn(async move {
    loop {
        if let Ok(event) = rx.recv().await {
            // 1. Find matching patterns
            let patterns = runtime_ref.pattern_registry.get_patterns_for_trigger(&format!("{:?}", event.event_type));
            
            for pattern in patterns {
                // 2. Evaluate Condition (Simple string match for MVP)
                // In Phase 4, we use a real JSONPath engine here.
                let condition_met = if pattern.condition == "*" {
                    true
                } else {
                    // Very basic check: Does payload string contain the condition keyword?
                    // TODO: Replace with `predicate.rs` logic
                    event.payload.to_string().contains(&pattern.condition) 
                };

                if condition_met {
                    tracing::info!("⚠️ Pattern Triggered: {} on Agent {}", pattern.name, event.agent_id.as_deref().unwrap_or("?"));
                    
                    // 3. Execute Action
                    match pattern.action {
                        crate::registry::PatternAction::Interrupt { reason } => {
                            if let Some(agent) = &event.agent_id {
                                // Direct call to fail_run (simulating interrupt)
                                runtime_ref.fail_run(&event.run_id, agent, &reason).await;
                            }
                        }
                        _ => tracing::warn!("Action type not yet implemented in Cortex"),
                    }
                }
            }
        }
    }
});
```

---

### Execution Plan for You
1.  Modify `runtime.rs` to include the bus and emitters.
2.  Update `main.rs` to spawn the Cortex listener.