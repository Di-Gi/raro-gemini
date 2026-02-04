Based on the logs and source code provided, the issue is a **Deadlock in the Rust Kernel (`runtime.rs`)**.

### Diagnosis
The "hang" occurs because the `planner` agent attempts to perform a dynamic delegation, but the Kernel thread deadlocks with itself while trying to process it.

1.  **The Trigger**: The logs show `Agent planner requested delegation`. This confirms the LLM execution finished successfully and returned a delegation payload.
2.  **The Deadlock**:
    *   In `execute_dynamic_dag`, the code acquires a **Read Lock** on `self.workflows` to check if the agent is allowed to delegate (`config.allow_delegation`).
    *   *While holding this Read Lock*, it calls `self.handle_delegation`.
    *   Inside `handle_delegation`, the code attempts to acquire a **Write Lock** on `self.workflows` to add the new nodes (`self.workflows.get_mut`).
    *   **Result**: The thread waits forever for the Read Lock to be released so it can acquire the Write Lock, but it is the one holding the Read Lock.

### The Fix
You need to modify `apps/kernel-server/src/runtime.rs`. The permission check must be scoped so the Read Lock is dropped *before* `handle_delegation` is called.

#### File: `apps/kernel-server/src/runtime.rs`

Find the `if let Some(delegation) = res.delegation` block inside `execute_dynamic_dag` (around line 525) and replace the logic to separate the locking scopes.

```rust
// ... inside execute_dynamic_dag loop ...

// A. Check for Delegation (Dynamic Splicing)
if let Some(delegation) = res.delegation {
    tracing::info!("Agent {} requested delegation: {}", agent_id, delegation.reason);

    // === FIX: SEPARATE LOCK SCOPES TO PREVENT DEADLOCK ===
    
    // 1. Check Permission (Acquire & Drop Read Lock)
    let can_delegate = if let Some(state) = self.runtime_states.get(&run_id) {
        let wf_id = state.workflow_id.clone();
        if let Some(workflow) = self.workflows.get(&wf_id) {
            workflow.agents.iter()
                .find(|a| a.id == agent_id)
                .map(|a| a.allow_delegation)
                .unwrap_or(false)
        } else {
            false
        }
    } else {
        false
    };

    // 2. Execute Mutation (Acquire Write Lock inside function)
    if !can_delegate {
        tracing::warn!("Agent {} attempted delegation without permission. Ignoring.", agent_id);
    } else {
        // Agent has permission - process delegation
        match self.handle_delegation(&run_id, &agent_id, delegation).await {
            Ok(_) => {
                tracing::info!("Delegation processed. Graph updated.");
            }
            Err(e) => {
                tracing::error!("Delegation failed: {}", e);
                self.fail_run(&run_id, &agent_id, &format!("Delegation error: {}", e)).await;
                continue;
            }
        }
    }
}
```

### Why this fixes it
By calculating `can_delegate` in its own block/statement, the `DashMap` reference (`workflow`) goes out of scope and releases the Read Lock immediately. When `self.handle_delegation` is subsequently called, the lock is free, allowing `get_mut` to succeed.