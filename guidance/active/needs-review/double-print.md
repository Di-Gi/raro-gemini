The "double-printing" behavior is occurring because of a conflict between the **Live Telemetry** (which sends tool results to the UI as they happen) and the **Final Artifact Sync** (which fetches the entire history once the agent finishes).

Specifically, in `llm.py`, you are appending the `execution_context_buffer` to the final text. Since the UI already rendered those tool results via Redis Pub/Sub, they appear twice when the final result is fetched.

Here are the specific fixes for **Agent Service** (`llm.py`) and the **Web Console** (`stores.ts`).

### 1. Fix in `apps/agent-service/src/core/llm.py`
We need to stop baking the "Context Attachment" (raw tool outputs) into the `text` field intended for the human. Instead, we should store it in the artifact dictionary so downstream agents can see it, but the UI ignores it in the main text block.

```python
# [[RARO]]/apps/agent-service/src/core/llm.py

# ... find the finalization block at the end of call_gemini_with_context ...

    # === FIX: REMOVE REDUNDANT TEXT ATTACHMENT ===
    # We no longer append context_dump to final_response_text because 
    # it causes double-printing in the UI. 
    # The 'execution_context_buffer' is still useful for the machine.
    
    machine_context = ""
    if execution_context_buffer:
        machine_context = "\n\n".join(execution_context_buffer)

    logger.info(f"Agent {safe_agent_id} Completed. Tokens: {input_tokens}/{output_tokens}")

    return {
        "text": final_response_text, # Clean text for the human
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thought_signature": thought_signature,
        "cache_hit": cache_hit,
        "files_generated": all_files_generated,
        "cached_content_id": final_cache_id,
        # PASS MACHINE DATA SEPARATELY
        "machine_data_context": machine_context 
    }
```

### 2. Fix in `apps/agent-service/src/main.py`
Ensure the separate `machine_data_context` is stored in Redis so the next agent can find it, without it being part of the `result` string.

```python
# [[RARO]]/apps/web-console/src/main.py -> _execute_agent_logic

# ... inside artifact_data assignment ...
    artifact_data = {
        "result": response_text, # Clean model output
        "status": "completed",
        "files_generated": files_generated,
        "machine_context": result.get("machine_data_context", ""), # Hidden from UI
        "artifact_stored": True
    }
```

### 3. Fix in `apps/web-console/src/lib/stores.ts`
The UI logic in `syncState` currently creates a "Loading" log when an agent succeeds, which then updates with the final text. We need to tell it to **merge** with the existing live logs instead of creating a new entry if the agent already has live activity.

```typescript
// [[RARO]]/apps/web-console/src/lib/stores.ts -> syncState()

// ... inside the invocations.forEach loop ...

if (inv.status === 'success') {
    if (inv.artifact_id) {
        // CHECK: Does a log for this agent already exist from the live stream?
        const currentLogs = get(logs);
        const existingLiveLog = currentLogs.find(l => 
            l.role === agentLabel && 
            (l.category === 'REASONING' || l.category === 'TOOL_CALL')
        );

        if (existingLiveLog) {
            // OPTION A: If live logs exist, just update the LAST one or add a silent completion
            // For now, let's just update the specific invocation record without 
            // creating the "Initiating output retrieval..." placeholder.
            fetchAndPopulateArtifact(state.run_id, inv); 
        } else {
            // OPTION B: No live logs yet (e.g. simple agent), use standard behavior
            addLog(agentLabel, 'Initiating output retrieval...', 'LOADING', false, inv.id);
            fetchAndPopulateArtifact(state.run_id, inv);
        }
    }
}

// Extract the fetching logic to a helper function inside syncState
async function fetchAndPopulateArtifact(runId: string, inv: any) {
    const { getArtifact } = await import('./api');
    const artifact = await getArtifact(runId, inv.agent_id);
    if (artifact) {
        let outputText = artifact.result || artifact.output || artifact.text || "";
        
        // If the artifact text is identical to a reasoning log we already have, 
        // we can suppress it to prevent the "Double Print"
        updateLog(inv.id, {
            message: outputText,
            metadata: `TOKENS: ${inv.tokens_used} | LATENCY: ${Math.round(inv.latency_ms)}ms`,
            isAnimated: true
        });
    }
}
```

### Why this fixes it:
1.  **Cleaner Final Response:** By removing the `[AUTOMATED CONTEXT ATTACHMENT]` from the `text` field in `llm.py`, the final response retrieved by the UI won't contain a repeat of the tool results.
2.  **Logic Separation:** Machine-to-machine context is stored in a separate Redis key/field (`machine_context`), keeping the "human" logs strictly for the model's prose.
3.  **UI Intelligence:** The Web Console now checks if it already knows about the agent (via live logs) before creating a new "Artifact Retrieval" entry, preventing that "third" duplicate block from appearing at the end of a run.