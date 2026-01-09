This is a crucial pause. You are right—we have fallen into a trap called **"Coupling Representation with Logic."**

In our attempt to make the Frontend look clean, we broke the Backend's brain. And in fixing the Backend's brain, we risked making the Frontend messy again. We are currently patching holes instead of fixing the pipe.

Let's break down exactly why it feels scattered and propose a **Unified Artifact Protocol** to fix it once and for all.

---

### 1. The Anatomy of the "Mess"

Currently, a single JSON object in Redis (`run:X:agent:Y:output`) is trying to serve three masters with conflicting needs:

1.  **The Kernel (Context Builder):**
    *   *Needs:* High-signal information (Text results, File paths).
    *   *Hates:* Noise (Status codes, Model names, raw JSON braces).
    *   *Current State:* It blindly grabs the Redis blob and dumps it into the Prompt. If the text is missing (because we hid it for the UI), the Agent sees `{ "status": "completed" }` and hallucinates.

2.  **The Frontend (Visualizer):**
    *   *Needs:* Display text, Metadata for badges (tokens/models), and Triggers for UI cards (images).
    *   *Hates:* Ambiguity. (Is this a string? Is it JSON? Do I parse it?)
    *   *Current State:* Contains complex heuristic logic ("If it looks like metadata, hide it, unless there's a file...") to sanitize the data stream.

3.  **The Agent Service (Producer):**
    *   *Needs:* To dump everything execution-related for audit.
    *   *Current State:* We keep toggling fields on/off based on who complained last (Frontend vs Kernel).

---

### 2. The Solution: The "RARO Artifact Protocol"

We need a strict contract. The Agent Service saves a **Master Record**. The consumers (Kernel and Frontend) extract *only what they need*.

**The Master Record (Redis)** should always look like this:

```json
{
  "content": "The actual text response from the LLM.",
  "data": { "key": "value" },  // Structured data if the agent returned a specific JSON object
  "files": ["chart.png"],      // Files generated
  "meta": {                    // Operational metadata (IGNORED by next agent)
    "model": "fast",
    "tokens": 150,
    "latency": 450,
    "status": "completed"
  }
}
```

---

### 3. Implementation Plan

Here is how we clean up the scatter across the stack.

#### Step A: Agent Service (`main.py`)
**Role:** The Writer. It writes the *full* protocol, never hiding data.

*Change in `_execute_agent_logic`:*
```python
# Instead of a flat dict, we structure it
artifact_data = {
    "content": response_text or "",  # ALWAYS present, even if empty
    "files": files_generated,        # ALWAYS a list
    "meta": {                        # ALWAYS isolated
        "status": "completed",
        "model": request.model,
        "thinking_depth": request.thinking_level or 0
    }
}
# Save to Redis
redis_client.setex(key, 3600, json.dumps(artifact_data))
```

#### Step B: Kernel (`runtime.rs`)
**Role:** The Filter. It extracts *only* content/files for the next agent. It MUST NOT dump the `meta` block into the prompt.

*Change in `prepare_invocation_payload`:*
```rust
// Inside loop over parent artifacts...
if let Ok(val) = serde_json::from_str::<serde_json::Value>(&json_str) {
    
    // 1. Extract Text (High Priority)
    if let Some(text) = val.get("content").and_then(|s| s.as_str()) {
        context_prompt_appendix.push_str(&format!("\n=== OUTPUT FROM {} ===\n{}\n", parent_id, text));
    }

    // 2. Extract Files (High Priority)
    if let Some(files) = val.get("files").and_then(|v| v.as_array()) {
        for file in files {
            // ... mount logic ...
        }
    }
    
    // 3. IGNORE "meta" field completely. 
    // The next agent doesn't care about the previous agent's latency or model.
}
```

#### Step C: Frontend (`stores.ts`)
**Role:** The Presenter. It uses the `meta` for badges, `content` for the typewriter, and `files` for the cards.

*Change in `syncState`:*
```typescript
// No more guessing types. We trust the protocol.
const artifact = await getArtifact(...);

if (artifact) {
    // 1. Text is safe to render
    let displayText = artifact.content || ""; 

    // 2. Files trigger UI cards
    if (artifact.files && artifact.files.length > 0) {
        // Append system tag for ArtifactCard
        displayText += `\n[SYSTEM: Generated Image saved to '${artifact.files[0]}']`;
    }

    // 3. Meta is used for the log header, not the body
    const metaInfo = artifact.meta ? 
        `TOKENS: ${artifact.meta.tokens} | MDL: ${artifact.meta.model}` : 
        "";

    updateLog(inv.id, {
        message: displayText,
        metadata: metaInfo,
        isAnimated: true
    });
}
```

### Why this is better:

1.  **Zero Hallucinations:** Agents will never see `{"status": "completed"}` as input text again. They will only see the actual text output of their predecessor.
2.  **Clean Logs:** The Frontend doesn't need regex or heuristics to hide metadata. It just reads `artifact.content`.
3.  **Observability:** The metadata is still saved in Redis (under `meta`), so we can still calculate costs/latency, but it doesn't pollute the execution context.

### Shall we execute this refactor?
I can provide the specific code blocks for `main.py` (Agent Service), `runtime.rs` (Kernel), and `stores.ts` (Frontend) to align them to this protocol.