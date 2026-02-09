This leakage is caused by an **architectural misalignment in the Kernel**, specifically in how "Context" is injected.

### The Diagnosis

1.  **The Leak:** The model output starts with `"You are Agent 'research_validator'..."`.
2.  **The Source:** This text comes from the `system_instruction` sent to Gemini.
3.  **The Cause:** In `kernel-server/src/runtime.rs`, the code **appends the output of previous agents (Context) to the `prompt` field**.
4.  **The Mechanism:** The Agent Service (`llm.py`) takes the `prompt` field and puts it directly into the **System Instruction**.

**Why this breaks:**
When you put dynamic data ("The previous agent said X") into the System Instruction, the model (especially `flash-lite`) gets confused. It treats the System Instruction as a "Conversation History" that it needs to acknowledge or summarize, rather than a "Constitution" it needs to obey.

To fix this, we must strictly separate **Identity** (System) from **Context** (User).

---

### The Fix

We need to modify the Kernel to inject the upstream context into the `user_directive` (which becomes a User Message) instead of the `prompt` (which becomes the System Instruction).

#### 1. Modify `apps/kernel-server/src/runtime.rs`

Locate the `prepare_invocation_payload` function. We need to change where `context_prompt_appendix` is attached.

```rust
// apps/kernel-server/src/runtime.rs

pub async fn prepare_invocation_payload(
    &self,
    run_id: &str,
    agent_id: &str,
) -> Result<InvocationPayload, String> {
    // ... (existing code fetching state and workflow) ...

    // [EXISTING CONTEXT FETCHING LOGIC - UNCHANGED]
    let mut context_prompt_appendix = String::new();
    // ... (logic that populates context_prompt_appendix from Redis) ...

    // ... (Pre-flight Context Drought Checks - UNCHANGED) ...

    // [FIX START: SEPARATE IDENTITY FROM CONTEXT]
    
    // 1. Prepare System Prompt (Identity Only)
    // Do NOT append context here. Keep this pure.
    let mut final_prompt = agent_config.prompt.clone();

    if tools.contains(&"write_file".to_string()) {
        final_prompt.push_str("\n\n[SYSTEM NOTICE]: You have access to 'write_file'. ...");
    }

    // 2. Prepare User Directive (Task + Context)
    // We append the context here, so it appears in the USER message.
    let mut final_user_directive = agent_config.user_directive.clone();
    
    if !context_prompt_appendix.is_empty() {
        // Prepend context to the directive so the model sees data before the command
        final_user_directive = format!("{}\n\n[OPERATIONAL CONTEXT]\n{}", context_prompt_appendix, final_user_directive);
    }

    // [FIX END]

    let graph_view = self.generate_graph_context(
        run_id,
        agent_id,
        agent_config.allow_delegation
    );

    Ok(InvocationPayload {
        run_id: run_id.to_string(),
        agent_id: agent_id.to_string(),
        model: model_string,
        prompt: final_prompt, // Pure Identity (System Instruction)
        user_directive: final_user_directive, // Task + Context (User Message)
        input_data: serde_json::Value::Object(input_data_map),
        parent_signature,
        cached_content_id,
        thinking_level,
        file_paths: full_file_paths,
        tools,
        allow_delegation: agent_config.allow_delegation,
        graph_view,
    })
}
```

### Why this solves it

1.  **Gemini API Behavior:**
    *   `system_instruction`: "You are a validator." (Immutable Rule)
    *   `contents` (User): "Here is the data from the previous agent. Verify it." (Dynamic Task)
2.  **Outcome:** The model will implicitly accept the "You are..." rule without repeating it, and will immediately act upon the "Verify it" command in the User block.

### Optional: Reinforce `llm.py` (Defensive Coding)

In `apps/agent-service/src/core/llm.py`, we can ensure the User Directive is formatted clearly to distinguish it from the System Prompt.

```python
# apps/agent-service/src/core/llm.py

# Inside _prepare_gemini_request

# ...

# 3. User Directive (if provided) - This is the runtime task
if user_directive:
    # Add a clear separator so the model knows this is the "In-Tray"
    user_parts.append({
        "text": f"[INCOMING TRANSMISSION]\n{user_directive}\n\n"
    })

# ...
```

By moving the context to the User Message, you stop the model from "leaking" its system instructions because it no longer feels the need to summarize them as part of the context processing.