This allows you to intercept the execution loop just before the LLM is called and substitute your own payload. This is incredibly powerful for:
1.  **Testing Topology Changes:** Manually injecting `json:delegation` blocks to see if the Kernel splices the graph correctly.
2.  **Testing Parsers:** Injecting malformed JSON to test robustness.
3.  **Testing Tool Flows:** Injecting a `json:function` block to force a specific tool execution without relying on the LLM's luck.

Here is the implementation plan for the **Puppeteer System**.

---

### 1. Architecture: The Interceptor Pattern

We will use Redis as a "mailbox".
1.  **Web Console:** Sends a mock payload to an endpoint: `/debug/inject`.
2.  **Agent Service:** Stores this payload in Redis keyed by `mock:{run_id}:{agent_id}`.
3.  **LLM Module:** Before calling Google/Gemini, checks Redis.
    *   **If key exists:** Returns the mock payload immediately (bypassing Gemini).
    *   **If no key:** Proceed as normal.

### 2. Backend Implementation (Agent Service)

#### A. Add Injection Endpoint (`src/main.py`)

We need a way to push the mock data into the system.

```python
# [[RARO]]/apps/agent-service/src/main.py

# ... imports ...
from pydantic import BaseModel

class MockInjection(BaseModel):
    run_id: str
    agent_id: str
    content: str  # The text you want the agent to "say"
    force_tool_execution: bool = True # Should the system actually run the tools in your text?

@app.post("/debug/inject")
async def inject_mock_response(payload: MockInjection):
    """
    Staging Area: Registers a mock response for a specific agent in a specific run.
    The next time this agent tries to think, it will use this text instead.
    """
    if not redis_client:
        raise HTTPException(503, "Redis unavailable")
        
    key = f"mock:{payload.run_id}:{payload.agent_id}"
    
    # Store for 10 minutes
    redis_client.setex(key, 600, payload.model_dump_json())
    
    logger.info(f"💉 MOCK INJECTED for {payload.agent_id} in {payload.run_id}")
    return {"status": "armed", "key": key}
```

#### B. Modify the LLM Loop (`src/core/llm.py`)

We patch the `call_gemini_with_context` function to check for these mocks.

```python
# [[RARO]]/apps/agent-service/src/core/llm.py

# ... existing imports ...

async def call_gemini_with_context(...):
    # ... existing setup code ...
    
    # logger.info(f"AGENT INVOCATION: ...") 

    # === 🛡️ PUPPETEER INTERCEPTOR ===
    mock_key = f"mock:{run_id}:{safe_agent_id}"
    mock_data = None
    
    if redis_client:
        raw_mock = redis_client.get(mock_key)
        if raw_mock:
            mock_data = json.loads(raw_mock)
            # Delete after use so it doesn't loop forever if the agent retries
            redis_client.delete(mock_key)
            logger.warning(f"🎭 INTERCEPTED: Using mocked response for {safe_agent_id}")

    # ... prepare_gemini_request ...

    while turn_count < max_turns:
        turn_count += 1
        
        content_text = ""

        # === BRANCH: REAL vs MOCK ===
        if mock_data and turn_count == 1:
            # FIRST TURN OVERRIDE
            content_text = mock_data["content"]
            # Mock the response object structure just enough for logging
            logger.info(">> INJECTING MOCK PAYLOAD <<")
        else:
            # STANDARD LLM CALL
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                # ... args
            )
            content_text = response.text or ""

        # ... (Rest of the loop: History append, Parsing, Tool Execution) ...
        
        # NOTE: If your mock text contained ```json:function```, the existing
        # logic below will parse it and ACTUALLY EXECUTE THE TOOL. 
        # This is perfect for testing side effects (file creation, etc).
```

---

### 3. Frontend Implementation (Web Console)

We'll create a **"Puppet Master"** panel that slides out when you hold a specific key (e.g., `Alt`) and click a node, or just a dedicated debug tab.

#### A. New Component: `InjectorPanel.svelte`

```svelte
<!-- [[RARO]]/apps/web-console/src/components/sub/InjectorPanel.svelte -->
<script lang="ts">
  import { runtimeStore, agentNodes } from '$lib/stores';
  
  let selectedTarget = $state('');
  let mockContent = $state('');
  let isArming = $state(false);

  // Snippets for quick testing
  const SNIPPETS = {
    'DELEGATION': 'I need help.\n```json:delegation\n{\n  "reason": "Test delegation",\n  "new_nodes": [\n    { "id": "sub_worker", "role": "worker", "prompt": "echo" }\n  ]\n}\n```',
    'FILE_WRITE': 'Saving report.\n```json:function\n{\n  "name": "write_file",\n  "args": { "filename": "test.txt", "content": "Hello World" }\n}\n```'
  };

  async function inject() {
    if (!selectedTarget || !mockContent) return;
    isArming = true;
    
    try {
        await fetch('/agent-api/debug/inject', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                run_id: $runtimeStore.runId,
                agent_id: selectedTarget,
                content: mockContent
            })
        });
        alert(`Agent ${selectedTarget} is now puppeted.`);
    } catch(e) {
        alert("Injection failed");
    } finally {
        isArming = false;
    }
  }
</script>

<div class="injector-chassis">
    <div class="header">PUPPET MASTER // OVERRIDE</div>
    
    <select bind:value={selectedTarget} class="target-select">
        <option value="">SELECT TARGET NODE...</option>
        {#each $agentNodes as node}
            <option value={node.id}>{node.id.toUpperCase()}</option>
        {/each}
    </select>

    <div class="snippets">
        {#each Object.entries(SNIPPETS) as [label, code]}
            <button class="snip-btn" onclick={() => mockContent = code}>{label}</button>
        {/each}
    </div>

    <textarea 
        bind:value={mockContent} 
        placeholder="Enter exact text the agent should output..."
        class="payload-editor"
    ></textarea>

    <button class="inject-btn" disabled={isArming} onclick={inject}>
        {isArming ? 'ARMING...' : 'INJECT PAYLOAD'}
    </button>
</div>

<style>
    .injector-chassis {
        padding: 12px;
        background: #111;
        border: 1px solid #333;
        color: #fff;
        font-family: monospace;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }
    .header { font-weight: bold; color: #ff00ff; letter-spacing: 1px; }
    .target-select, .payload-editor {
        background: #222; border: 1px solid #444; color: #0f0;
        padding: 8px; font-family: monospace;
    }
    .payload-editor { min-height: 150px; resize: vertical; }
    .inject-btn {
        background: #ff00ff; color: #000; font-weight: bold; border: none; padding: 10px; cursor: pointer;
    }
    .snippets { display: flex; gap: 5px; }
    .snip-btn { font-size: 10px; background: #333; color: #ccc; border: none; cursor: pointer; padding: 2px 6px; }
</style>
```

---

### 4. How to Use It (The Workflow)

1.  **Start a Workflow:** Use `ControlDeck` to start a standard run (e.g., "Standard Template").
2.  **Identify a Target:** While `ORCHESTRATOR` is thinking, you decide you want to force the `RETRIEVAL` node to output specific data (to test how `SYNTHESIS` handles it).
3.  **Inject:**
    *   Open **Puppet Master**.
    *   Select Target: `RETRIEVAL`.
    *   Paste Payload:
        ```markdown
        I have found the critical documents.
        ```json:function
        {
          "name": "write_file",
          "args": {
            "filename": "secret_plans.txt",
            "content": "The reactor code is 7734."
          }
        }
        ```
    *   Click **INJECT PAYLOAD**.
4.  **Wait:** When the Kernel activates `RETRIEVAL`, the Agent Service sees the Redis key.
5.  **Observe Propagation:**
    *   Agent Service acts as if Gemini produced that text.
    *   `parsers.py` detects `json:function`.
    *   `tools.py` executes `write_file`, creating `secret_plans.txt`.
    *   **Artifact Promotion** kicks in (Kernel sees the file generation).
    *   `SYNTHESIS` starts. Since `RETRIEVAL` is a dependency, `SYNTHESIS` receives the context *"I have found the critical documents..."* AND (if your context mounting logic is correct) gets the `secret_plans.txt` mounted.

This setup allows you to **unit test the entire pipeline logic** without nondeterministic LLM behavior. You can specifically verify if the Kernel correctly handles file outputs and passes them to downstream dependencies.