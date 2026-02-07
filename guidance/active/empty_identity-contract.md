This document consolidates the **Failure Recovery**, **Semantic Null Detection**, and **Protocol Enforcement** strategies into a single, unified implementation plan.

This specification serves as the blueprint for the coding agent to implement the **RARO Protocol Integrity Layer**.

---

# RARO Protocol Integrity & Circuit Breaker Specification

**Objective:** Transform "Agent Failures" (errors, hallucinations, empty results) from silent runtime crashes into **Interactive Safety Events**. This ensures the Operator is consulted before the DAG collapses or proceeds with corrupted context.

**Scope:**
1.  **Configuration:** Define the Cortex Pattern for failure interception.
2.  **Agent Service:** Enforce Identity Contracts and Status Tagging in System Prompts.
3.  **Kernel:** Implement Pre-flight (Context Drought) and Post-flight (Protocol Validation) guards.
4.  **UI:** Visualize these interventions distinctively.

---

## 1. Configuration Layer: The Safety Pattern

**File:** `apps/kernel-server/config/cortex_patterns.json`

We must register a pattern that listens for the `AgentFailed` event. This bridges the gap between a Rust runtime error and the Cortex approval workflow.

```json
{
  "id": "guard_execution_failure",
  "name": "Failure Recovery Guard",
  "trigger_event": "AgentFailed",
  "condition": "*",
  "action": {
    "RequestApproval": {
      "reason": "Execution Exception detected. Review logs and authorize Retry or Skip."
    }
  }
}
```

---

## 2. Intelligence Layer: The Worker Protocol

**File:** `apps/agent-service/src/intelligence/prompts.py`

We update `render_runtime_system_instruction` to enforce the **Identity Contract**. The agent must know that its `id` determines its mandatory tools, and it must explicitly report its success status.

**Implementation Logic:**
1.  Define the `[IDENTITY CONTRACT]`.
2.  Define the `[OUTPUT CATEGORIZATION]` tags.
3.  Define the `[BYPASS: ...]` escape hatch.

**Code Reference:**

```python
# In apps/agent-service/src/intelligence/prompts.py

def render_runtime_system_instruction(agent_id: str, tools: Optional[List[str]]) -> str:
    # ... [Existing Identity Logic] ...

    instruction += """
[IDENTITY CONTRACT & PROTOCOL ENFORCEMENT]
Your Agent ID prefix determines your mandatory behavior. You are being monitored by the Kernel.
1. 'research_': You MUST use `web_search` to verify facts. Do not answer from memory.
2. 'analyze_' / 'coder_': You MUST use `execute_python` for calculations, data processing, or file logic.
3. 'writer_': You rely on context. No mandatory tools.

[BYPASS LOGIC]
If you legitimately do not need your mandatory tool (e.g., formatting data already provided), you MUST start your response with `[BYPASS: JUSTIFICATION]` followed by your reasoning.

[OUTPUT CATEGORIZATION]
You must end every response with exactly one of these status tags:
- `[STATUS: SUCCESS]`: Task completed with valid data/results.
- `[STATUS: NULL]`: Could not find information, tools failed, or context was insufficient.

[CRITICAL]: If you output `[STATUS: NULL]`, the system will pause for human assistance. Use this if you are stuck.
"""
    return instruction
```

---

## 3. Kernel Layer: The Circuit Breaker

**File:** `apps/kernel-server/src/runtime.rs`

The Kernel requires two distinct guards. One runs *before* an agent starts (checking upstream data quality), and one runs *after* an agent finishes (checking protocol compliance).

### A. Pre-Flight: Context Drought Prevention
**Location:** Inside `prepare_invocation_payload` method.

**Logic:**
If an agent has dependencies (`depends_on`), check the content inherited from them. If the combined context contains `[STATUS: NULL]` and no files were generated, block execution.

```rust
// Inside prepare_invocation_payload (after context_prompt_appendix is built)

// 1. Context Health Check
let has_null_signal = context_prompt_appendix.contains("[STATUS: NULL]");
let has_files = !dynamic_file_mounts.is_empty();
let is_root_node = agent_config.depends_on.is_empty();

// If we depend on others, and they gave us nothing but NULLs or empty text, pause.
if !is_root_node && (context_prompt_appendix.trim().is_empty() || (has_null_signal && !has_files)) {
    let drought_msg = format!("Pre-Execution Halt: Agent '{}' is facing a Context Drought. Upstream nodes provided no usable data.", agent_id);
    tracing::warn!("{}", drought_msg);
    
    // Trigger the Circuit Breaker
    self.request_approval(run_id, Some(agent_id), &drought_msg).await;
    
    return Err("Halted: Contextual Data Drought".to_string());
}
```

### B. Post-Flight: Protocol Validator & Semantic Check
**Location:** Inside `execute_dynamic_dag` loop, specifically in the `match response` block.

**Logic:**
1.  **Semantic Check:** Did the agent output `[STATUS: NULL]`?
2.  **Protocol Check:** Did a `research_` agent fail to search? Did an `analyze_` agent fail to code?
3.  **Action:** If either check fails, trigger `request_approval` and `break` the loop (pausing the DAG).

```rust
// Inside execute_dynamic_dag -> match response -> Ok(res)

let text = res.output.as_ref()
    .and_then(|o| o.get("result"))
    .and_then(|v| v.as_str())
    .unwrap_or("");

// 1. Analyze Signals
let is_semantic_null = text.contains("[STATUS: NULL]");
let is_bypassed = text.contains("[BYPASS:");

// 2. Check Tool Evidence (Did the response include tool usage logs?)
let used_python = text.contains("execute_python") || text.contains("Tool 'execute_python' Result");
let used_search = text.contains("web_search") || text.contains("Tool 'web_search' Result");

// 3. Protocol Validation Logic
let mut protocol_violation = None;

if !is_bypassed {
    if agent_id.starts_with("research_") && !used_search {
        protocol_violation = Some("Protocol Violation: 'research_' agent did not use web_search (Hallucination Risk).");
    } else if (agent_id.starts_with("analyze_") || agent_id.starts_with("coder_")) && !used_python {
        protocol_violation = Some("Protocol Violation: 'analyze_'/'coder_' agent did not use execute_python (Integrity Risk).");
    }
}

// 4. Circuit Breaker Decision
if res.success && !is_semantic_null && protocol_violation.is_none() {
    // ... [Proceed with Standard Success Logic (Signatures, Artifacts, etc.)] ...
} else {
    // ... [FAILURE / PAUSE LOGIC] ...
    
    let pause_reason = if is_semantic_null {
        format!("Agent '{}' reported a Semantic Null (found no data).", agent_id)
    } else if let Some(violation) = protocol_violation {
        violation.to_string()
    } else {
        res.error.unwrap_or_else(|| "Unknown Execution Error".to_string())
    };

    tracing::error!("Circuit Breaker Triggered for {}: {}", agent_id, pause_reason);

    // A. Pause the Run
    self.request_approval(&run_id, Some(&agent_id), &pause_reason).await;

    // B. Emit Event for Cortex/UI
    self.emit_event(RuntimeEvent::new(
        &run_id,
        EventType::AgentFailed, // This triggers the Config Pattern defined in Section 1
        Some(agent_id.clone()),
        serde_json::json!({ 
            "error": pause_reason, 
            "recovery_hint": "Check Prompt or Data Sources" 
        }),
    ));

    // C. Break Execution Loop
    break; 
}
```

---

## 4. UI Layer: Visual Differentiation

**File:** `apps/web-console/src/components/OutputPane.svelte`

The UI must distinguish between a "Safety Pause" (Prompt Injection) and a "Quality Pause" (Context Drought/Protocol Violation).

**Implementation:**
The `addLog` logic currently handles `INTERVENTION`. We ensure `ApprovalCard` receives the specific `reason` string generated by the Kernel.

*Note: The existing codebase in the prompt history for `OutputPane.svelte` essentially handles this via the `log.metadata === 'INTERVENTION'` check. The key is ensuring the **Kernel** sends the specific error message in the `reason` field of the event, which aligns with the Rust implementation above.*

---

## Summary of Recovery Flow

1.  **Event:** Agent `research_market` runs but finds no data. It outputs `[STATUS: NULL]`.
2.  **Detection:** Kernel `execute_dynamic_dag` sees the tag.
3.  **Action:** Kernel calls `request_approval` with reason "Semantic Null". Run status becomes `AWAITING_APPROVAL`.
4.  **UI:** Operator sees **Approval Card**: *"Agent 'research_market' reported a Semantic Null."*
5.  **Recovery:** Operator edits the prompt via Control Deck to be more specific, then clicks **[ EXECUTE ]** on the card.
6.  **Resume:** Kernel resumes the loop, re-invoking the failed agent with the new instructions.