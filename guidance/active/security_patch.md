### Root Cause Analysis
1.  **Protocol Violation:** The Kernel checks the **final text response** from the agent to see if a tool was used. However, because the agent runs in a "Tool Loop" (LLM -> Tool -> LLM), the *final* text response often describes the result (e.g., "I found the data") but doesn't contain the raw tool invocation string. The Kernel sees a "Researcher" identity that didn't output a `web_search` string in its *final* answer, triggering the safety guard.
2.  **Premature Status Tag:** The logs show `[STATUS: SUCCESS]` appearing in the reasoning block *before* the tool execution. This confuses the system state.

### Solution
We will explicitly track executed tools in the Python service and pass them as a structured list to the Rust Kernel. The Kernel will verify this list instead of regex-searching the text body.

---

### 1. Update Python Protocol
Add the `executed_tools` field to the response schema.

**File:** `agent-service/src/domain/protocol.py`

```python
# [[RARO]]/apps/agent-service/src/domain/protocol.py
# ... imports remain the same ...

# ... existing enums ...

# ... existing definitions ...

class AgentResponse(BaseModel):
    """Result of an agent execution returned to the Kernel."""
    agent_id: str
    success: bool
    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit: bool = False

    # [[CONTEXT CACHING]]
    cached_content_id: Optional[str] = None

    latency_ms: float = 0.0
    thought_signature: Optional[str] = None
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    delegation: Optional[DelegationRequest] = None
    
    # [[FIX: EXPLICIT TOOL TRACKING]]
    # List of tool names successfully executed during this run loop
    executed_tools: List[str] = Field(default_factory=list)
```

### 2. Update LLM Logic to Track Tools
Modify the execution loop to accumulate used tools and return them.

**File:** `agent-service/src/core/llm.py`

```python
# [[RARO]]/apps/agent-service/src/core/llm.py
# ... imports ...

# ... existing code ...

# ============================================================================
# Unified Gemini API Caller (Sync/Batch)
# ============================================================================
async def call_gemini_with_context(
    # ... args ...
) -> Dict[str, Any]:
    # ... setup code ...

    try:
        # ... setup params ...

        # ... probe/puppet blocks ...

        # --- FIX START: Initialize variables ---
        response = None
        content_text = ""
        all_files_generated = []
        _seen_files = set()
        machine_context_buffer = []
        puppet_turn_used = False
        
        # [[NEW]] Track unique tools executed
        executed_tools_set = set() 
        # --- FIX END ---

        logger.debug(f"Agent {safe_agent_id}: Starting manual tool loop")

        while turn_count < max_turns:
            turn_count += 1
            
            # ... LLM Call Logic (Standard or Mock) ...

            # ... Response Extraction ...

            # 3. Parse for Manual Function Calls
            function_calls = parse_function_calls(content_text)

            if not function_calls:
                # No tools called, this is the final answer
                final_response_text = content_text
                break

            # ... Telemetry Emission ...

            # 4. Process Tool Calls
            tool_outputs_text = ""

            for tool_name, tool_args in function_calls:
                # [[NEW]] Track execution
                executed_tools_set.add(tool_name)

                # ... Emit Start Telemetry ...

                # Execute
                result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)

                # ... (rest of tool execution logic) ...

            # ... Loop continues ...

        # --- Finalization ---

        # ... (Metadata extraction) ...

        return {
            "text": final_response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thought_signature": thought_signature,
            "cache_hit": cache_hit,
            "files_generated": all_files_generated,
            "cached_content_id": final_cache_id,
            "machine_data_context": machine_context,
            # [[NEW]] Return the set as a list
            "executed_tools": list(executed_tools_set)
        }

    except Exception as e:
        # ... error handling ...
        raise
```

### 3. Update Main Handler to Pass Data
Map the new field in the main endpoint.

**File:** `agent-service/src/main.py`

```python
# [[RARO]]/apps/agent-service/src/main.py

# ... imports ...

# ... inside _execute_agent_logic function ...

        # 6. Build Response
        return AgentResponse(
            agent_id=request.agent_id,
            success=True,
            output={
                "result": response_text,
                "artifact_stored": artifact_stored,
                "files_generated": files_generated
            },
            delegation=delegation_request,
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            tokens_used=result["input_tokens"] + result["output_tokens"],
            thought_signature=result["thought_signature"],
            cache_hit=result["cache_hit"],
            cached_content_id=result.get("cached_content_id"),
            
            # [[FIX]] Pass the tracked tools back to Kernel
            executed_tools=result.get("executed_tools", []),

            latency_ms=latency_ms
        )
```

### 4. Fix Prompt Instruction
Prevent the LLM from outputting `[STATUS: SUCCESS]` prematurely (during tool calls).

**File:** `agent-service/src/intelligence/prompts.py`

```python
# [[RARO]]/apps/agent-service/src/intelligence/prompts.py

# ... existing code ...

def render_runtime_system_instruction(agent_id: str, tools: Optional[List[str]]) -> str:
    # ... existing prompt start ...

    # ... existing tool sections ...

    # === PROTOCOL ENFORCEMENT ===
    instruction += """
[IDENTITY CONTRACT & PROTOCOL ENFORCEMENT]
Your Agent ID prefix determines your mandatory behavior. You are being monitored by the Kernel.
1. 'research_': You MUST use `web_search` to verify facts. Do not answer from memory.
2. 'analyze_' / 'coder_': You MUST use `execute_python` for calculations, data processing, or file logic.
3. 'writer_': You rely on context. No mandatory tools.

[BYPASS LOGIC]
If you legitimately do not need your mandatory tool (e.g., formatting data already provided), you MUST start your response with `[BYPASS: JUSTIFICATION]` followed by your reasoning.

[OUTPUT CATEGORIZATION]
When you have completed the task and have NO MORE tool calls to make, end your response with exactly one of these status tags:
- `[STATUS: SUCCESS]`: Task completed with valid data/results.
- `[STATUS: NULL]`: Could not find information, tools failed, or context was insufficient.

[CRITICAL]: 
1. DO NOT output `[STATUS: SUCCESS]` if you are about to call a tool. Only use it in the final text response.
2. If you output `[STATUS: NULL]`, the system will pause for human assistance.
"""

    return instruction
```

### 5. Update Rust Kernel Models
Add the field to the Rust struct so it can deserialize the new Python response.

**File:** `kernel-server/src/models.rs`

```rust
// [[RARO]]/apps/kernel-server/src/models.rs

// ... existing code ...

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteAgentResponse {
    pub agent_id: String,
    pub success: bool,
    pub output: Option<serde_json::Value>,
    pub error: Option<String>,
    pub tokens_used: usize,
    pub thought_signature: Option<String>,
    pub input_tokens: usize,
    pub output_tokens: usize,
    pub cache_hit: bool,
    pub latency_ms: f64,
    pub cached_content_id: Option<String>, 
    
    // [[NEW]] List of tools actually executed by the Python service
    #[serde(default)] 
    pub executed_tools: Vec<String>,

    pub delegation: Option<DelegationRequest>,
}

// ... existing code ...
```

### 6. Update Rust Runtime Validation
Change the check logic to use the robust `executed_tools` list.

**File:** `kernel-server/src/runtime.rs`

```rust
// [[RARO]]/apps/kernel-server/src/runtime.rs

// ... inside execute_dynamic_dag function ...
// ... inside match response -> Ok(res) ...

                    // === POST-FLIGHT: PROTOCOL VALIDATOR & SEMANTIC CHECK ===
                    let text = res.output.as_ref()
                        .and_then(|o| o.get("result"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("");

                    // 1. Analyze Signals
                    let is_semantic_null = text.contains("[STATUS: NULL]");
                    let is_bypassed = text.contains("[BYPASS:");

                    // 2. Check Tool Evidence (Robust Check)
                    // We check the explicit list from Python first, fall back to text scan
                    let used_python = res.executed_tools.contains(&"execute_python".to_string()) 
                                      || text.contains("execute_python") 
                                      || text.contains("Tool 'execute_python' Result");
                                      
                    let used_search = res.executed_tools.contains(&"web_search".to_string()) 
                                      || text.contains("web_search") 
                                      || text.contains("Tool 'web_search' Result");

                    // 3. Protocol Validation Logic
                    let mut protocol_violation = None;

                    if !is_bypassed {
                        if agent_id.starts_with("research_") && !used_search {
                            protocol_violation = Some("Protocol Violation: 'research_' agent did not use web_search (Hallucination Risk).");
                        } else if (agent_id.starts_with("analyze_") || agent_id.starts_with("coder_")) && !used_python {
                            protocol_violation = Some("Protocol Violation: 'analyze_'/'coder_' agent did not use execute_python (Integrity Risk).");
                        }
                    }

                    // ... rest of circuit breaker logic ...
```

#### 7. Fix Kernel Deadlock (Cleanup on Circuit Break)

We need to ensure that if the circuit breaker trips, we mark the agent as failed/stopped so the scheduler doesn't wait for it forever upon resumption.

**File:** `kernel-server/src/runtime.rs`

```rust
// [[RARO]]/apps/kernel-server/src/runtime.rs

// ... inside execute_dynamic_dag ...
// ... inside match response -> Ok(res) ...
// ... after protocol validation logic ...

                    // 4. Circuit Breaker Decision
                    if res.success && !is_semantic_null && protocol_violation.is_none() {
                        // ... (Existing success logic) ...
                    } else {
                        // === CIRCUIT BREAKER: FAILURE / PAUSE LOGIC ===
                        let pause_reason = if is_semantic_null {
                            format!("Agent '{}' reported a Semantic Null (found no data).", agent_id)
                        } else if let Some(violation) = protocol_violation {
                            violation.to_string()
                        } else {
                            res.error.unwrap_or_else(|| "Unknown Execution Error".to_string())
                        };

                        tracing::error!("Circuit Breaker Triggered for {}: {}", agent_id, pause_reason);

                        // [[FIX START: CLEANUP STATE TO PREVENT DEADLOCK]]
                        // We must remove the agent from active_agents so the scheduler doesn't 
                        // waiting for it indefinitely upon resume.
                        {
                            if let Some(mut state) = self.runtime_states.get_mut(&run_id) {
                                // Remove from active
                                state.active_agents.retain(|a| a != &agent_id);
                                // Mark as failed so the scheduler knows it's "done" (albeit unsuccessfully)
                                if !state.failed_agents.contains(&agent_id) {
                                    state.failed_agents.push(agent_id.clone());
                                }
                                
                                // Record the failed invocation so it appears in logs/history
                                state.invocations.push(AgentInvocation {
                                    id: Uuid::new_v4().to_string(),
                                    agent_id: agent_id.clone(),
                                    model_variant: ModelVariant::Fast, // Fallback
                                    thought_signature: None,
                                    tools_used: payload.tools.clone(),
                                    tokens_used: res.tokens_used,
                                    latency_ms: res.latency_ms as u64,
                                    status: InvocationStatus::Failed,
                                    timestamp: Utc::now().to_rfc3339(),
                                    artifact_id: None,
                                    error_message: Some(pause_reason.clone()),
                                });
                            }
                            // Persist the clean state
                            self.persist_state(&run_id).await;
                        }
                        // [[FIX END]]

                        // A. Pause the Run
                        self.request_approval(&run_id, Some(&agent_id), &pause_reason).await;

                        // B. Emit Event for Cortex/UI
                        self.emit_event(RuntimeEvent::new(
                            &run_id,
                            EventType::AgentFailed, 
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

