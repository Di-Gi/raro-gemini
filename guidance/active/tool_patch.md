Here is the investigation report and the necessary fixes to resolve the architectural flaws in the tool provisioning flow.

### 1. Investigation Report

#### Issue 1: Tool Authority (The "Air-Gap" Problem)
**Status: CONFIRMED & CRITICAL**

You are correct. Currently, the Frontend allows users to toggle tools manually (e.g., adding `execute_python` to a `research_` node). However, the **Kernel** (`apps/kernel-server/src/runtime.rs`) completely ignores the tools list sent from the Frontend.

In `prepare_invocation_payload` inside `runtime.rs`, the Kernel reconstructs the tool list from scratch based *solely* on the Agent ID:

```rust
// Current logic in runtime.rs
let mut tools = Vec::new(); // Starts empty!
tools.push("read_file".to_string()); // Adds Universal tools...

// Then adds tools based ONLY on ID string matching
if id_lower.starts_with("research_") { tools.push("web_search".to_string()); }
```

**Consequence:** Any manual customization done in the UI (Control Deck) is silently discarded at runtime. The Kernel acts as a dictator rather than a validator.

#### Issue 2: Identity Casing & Stability
**Status: PARTIALLY MITIGATED, BUT BRITTLE**

The codebase currently uses `.toLowerCase()` in both the Frontend (`stores.ts`) and Backend (`runtime.rs`) before checking prefixes. This prevents a hard crash if you type `Research_Node` vs `research_node`.

**However**, the flaw lies in **Prefix Strictness**.
*   Current logic requires `research_` (with underscore).
*   If a user names a node `researcher` (without underscore), the system detects *zero* tools.
*   The "Crash" you fear is an **Intent Crash**: The agent will spawn, have 0 tools, and fail to execute its prompt because it lacks the capabilities the user implies it has.

---

### 2. The Solution

We need to change the architecture from **"Kernel Dictates"** to **"Kernel Merges & Validates"**.

1.  **Frontend:** Sends the "Desired State" (Identity Defaults + User Manual Overrides).
2.  **Kernel:** Takes the "Desired State", **unions** it with "Mandatory Identity Defaults" (to prevent user error), and passes the result to the Agent.

### 3. Implementation

#### Step A: Fix the Kernel (`apps/kernel-server/src/runtime.rs`)

We need to modify `prepare_invocation_payload` to respect the `agent_config.tools` incoming from the `WorkflowConfig`, while still ensuring identity-based tools are guaranteed.

```rust
// [[RARO]]/apps/kernel-server/src/runtime.rs

// ... inside prepare_invocation_payload ...

        // === AUTHORITATIVE IDENTITY PROVISIONING (FIXED) ===
        // STRATEGY: Merge User Configuration with Identity Mandates
        
        // 1. Start with tools defined in the Configuration (from UI/Architect)
        let mut tools = agent_config.tools.clone();

        // 2. UNIVERSAL BASELINE (Always ensure these exist)
        if !tools.contains(&"read_file".to_string()) { tools.push("read_file".to_string()); }
        if !tools.contains(&"list_files".to_string()) { tools.push("list_files".to_string()); }

        let id_lower = agent_id.to_lowercase();

        // 3. MANDATORY GRANTS (Enforce identity contract)
        // Even if user removed them in UI, identity demands them.
        
        // Research Class
        if id_lower.contains("research") || id_lower.starts_with("web_") {
            if !tools.contains(&"web_search".to_string()) {
                tools.push("web_search".to_string());
            }
        }

        // Logic/Math Class
        if id_lower.contains("analyze") || id_lower.contains("code") || id_lower.contains("math") {
            if !tools.contains(&"execute_python".to_string()) {
                tools.push("execute_python".to_string());
            }
        }

        // Output/I-O Class
        if id_lower.contains("code") || id_lower.contains("write") {
            if !tools.contains(&"write_file".to_string()) {
                tools.push("write_file".to_string());
            }
        }

        // Admin Class
        if id_lower.starts_with("master_") || id_lower.starts_with("orchestrator") {
            for t in ["web_search", "execute_python", "write_file"] {
                if !tools.contains(&t.to_string()) {
                    tools.push(t.to_string());
                }
            }
        }

        // Dynamic artifacts require python (Special Case)
        if has_dynamic_artifacts && !tools.contains(&"execute_python".to_string()) {
            tools.push("execute_python".to_string());
            tracing::info!("Agent {}: Provisioned 'execute_python' for dynamic artifact handling", agent_id);
        }

        tracing::info!("Final provisioned tools for {}: {:?}", agent_id, tools);

        // ... rest of the function
```

#### Step B: Relax Frontend Identity Logic (`apps/web-console/src/lib/stores.ts`)

Make the identity derivation heuristic looser so it catches `researcher` as well as `research_`, preventing the "Intent Crash".

```typescript
// [[RARO]]/apps/web-console/src/lib/stores.ts

// ...

// === IDENTITY UTILITY HELPER (UPDATED) ===
// Made robust against casing and underscores
export function deriveToolsFromId(id: string): string[] {
    const tools = new Set<string>();
    const lowerId = id.toLowerCase();

    // 1. Base Identity Grants (Broader matching)
    // Research: "research_", "researcher", "web_searcher"
    if (lowerId.includes('research') || lowerId.includes('web')) {
        tools.add('web_search');
    }

    // Analysis/Code: "analyze_", "analyst", "coder", "python"
    if (lowerId.includes('analy') || lowerId.includes('code') || lowerId.includes('math')) {
        tools.add('execute_python');
    }

    // IO: "writer", "coder", "logger"
    if (lowerId.includes('writ') || lowerId.includes('code') || lowerId.includes('log')) {
        tools.add('write_file');
    }

    // 2. Master Grant
    if (lowerId.startsWith('master') || lowerId.startsWith('orch')) {
        tools.add('web_search');
        tools.add('execute_python');
        tools.add('write_file');
    }

    return Array.from(tools);
}

// ...
```

#### Step C: Update ControlDeck UI Logic (`apps/web-console/src/components/ControlDeck.svelte`)

Ensure the UI properly differentiates between "Locked by Identity" (Backend will force this) and "Selected by User" (Backend will accept this).

```svelte
<!-- [[RARO]]/apps/web-console/src/components/ControlDeck.svelte -->

<!-- inside <script> -->
  
  // Updated Helper: Tools are locked if the identity requires them.
  // If simply manually added, they are NOT locked.
  function isToolLocked(tool: string): boolean {
    return identityTools.includes(tool);
  }

  // Updated Toggle:
  function toggleTool(tool: string) {
    if (isToolLocked(tool)) return; // Cannot disable mandatory identity tools

    if (currentTools.includes(tool)) {
      currentTools = currentTools.filter(t => t !== tool);
    } else {
      currentTools = [...currentTools, tool];
    }
    saveNodeConfig();
  }

<!-- inside Markup (tool-config section) -->
<!-- 
   The logic already implemented in your paste looks correct for the UI, 
   but ensure the class logic matches the helper above. 
-->
```

### Summary of Changes

1.  **Kernel (`runtime.rs`):** Changed `tools` vector initialization. Instead of `Vec::new()`, it now starts with `agent_config.tools.clone()`. It then **unions** the mandatory identity tools. This fixes Issue #1.
2.  **Frontend (`stores.ts`):** Changed string matching from strict `startsWith('research_')` to loose `includes('research')`. This fixes Issue #2 (fragility).

These changes ensure that:
1.  If a user manually enables Python for a Researcher, the Backend accepts it.
2.  If a user names an agent `MarketResearcher` (no underscore), it still gets `web_search`.
3.  If a user tries to disable `web_search` for a `Research_Node`, the UI locks it (and the Backend enforces it anyway).