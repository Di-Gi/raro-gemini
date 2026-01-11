**The Problem:** Currently, your `debug-probe` attempts to *emulate* the Agent Service's logic (`simulate_system_instruction`). This violates the DRY principle; if you change your prompts in the Agent Service, your Debug Probe is instantly outdated.

**The Solution:** Use the **Agent Service** as the single source of truth.
1. Route Kernel traffic to the **Real Agent Service**.
2. Configure the Agent Service to enter a **"Mock Mode"** if a Debug Probe URL is detected.
3. The Agent Service generates the *real* prompt (using its actual logic), sends a copy to the Debug Probe for visualization, and returns a dummy response **without** calling Gemini.

Here is the implementation plan:

### 1. Update Agent Service Config
Add a setting to define where the debug probe lives.

**File:** `apps/agent-service/src/core/config.py`

```python
class Settings(BaseSettings):
    # ... existing keys ...
    GEMINI_API_KEY: Optional[str] = None 
    
    # NEW: If set, we skip Gemini calls and send prompt dumps here
    DEBUG_PROBE_URL: Optional[str] = None 

    # ... existing ...
```

### 2. Update Agent Service LLM Logic (The Interceptor)
Modify the core execution function. We will let it build the full request using production logic, then hijack it before the network call to Google.

**File:** `apps/agent-service/src/core/llm.py`

```python
# Add imports
import httpx 
from core.config import settings # Ensure settings is imported

# ... existing code ...

async def call_gemini_with_context(
    model: str,
    prompt: str,
    # ... args ...
    agent_id: Optional[str] = None,
    run_id: str = "default_run"
) -> Dict[str, Any]:
    
    # ... existing setup code ...

    try:
        # 1. GENERATE THE REAL PROMPT (Single Source of Truth)
        params = await _prepare_gemini_request(
            concrete_model, prompt, safe_agent_id, user_directive, input_data, file_paths,
            parent_signature, thinking_level, tools
        )

        # =========================================================
        # INTERCEPTION LAYER
        # =========================================================
        if settings.DEBUG_PROBE_URL:
            logger.warning(f"DEBUG PROBE ACTIVE. Intercepting Agent {safe_agent_id}. No LLM call.")
            
            # Extract the generated system instruction and user content
            system_instruction = params["config"].get("system_instruction", "")
            
            # Extract user message parts for visualization
            user_parts = params["contents"][0]["parts"]
            formatted_user_msg = "\n".join([
                p.get("text", "[Binary Data/File]") for p in user_parts 
                if "text" in p
            ])

            # Fire-and-forget log payload to the Probe
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{settings.DEBUG_PROBE_URL}/capture",
                        json={
                            "id": run_id,
                            "time": datetime.now().isoformat(),
                            "agent_id": safe_agent_id,
                            "run_id": run_id,
                            "tools": tools,
                            # The EXACT text the model would have seen
                            "final_system_prompt": system_instruction,
                            "final_user_message": formatted_user_msg,
                            "original_payload": {"model": model, "prompt": prompt}
                        },
                        timeout=1.0
                    )
            except Exception as e:
                logger.error(f"Failed to send debug capture: {e}")

            # Return Dummy Response to keep Kernel alive
            return {
                "text": f"[DEBUG MODE] Agent {safe_agent_id} prompted successfully. LLM invoke skipped.",
                "input_tokens": 0,
                "output_tokens": 0,
                "thought_signature": "debug_signature",
                "cache_hit": False,
                "files_generated": []
            }
        # =========================================================

        current_contents = params["contents"]
        # ... Continue with standard Gemini call logic ...
```

### 3. Gut the Debug Probe (Make it Passive)
The probe no longer needs to know *how* to build prompts. It just renders what it receives.

**File:** `apps/debug-probe/src/main.py`

```python
# apps/debug-probe/src/main.py
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI(title="RARO Debug Probe")
templates = Jinja2Templates(directory="templates")

# In-memory storage
captured_logs = []

class CapturePayload(BaseModel):
    id: str
    time: str
    agent_id: str
    run_id: str
    tools: Optional[List[str]] = []
    final_system_prompt: str
    final_user_message: str
    original_payload: Dict[str, Any]

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "captures": reversed(captured_logs)
    })

@app.post("/clear")
async def clear_logs():
    captured_logs.clear()
    return {"status": "cleared"}

# NEW: Passive Capture Endpoint
@app.post("/capture")
async def capture_debug_log(payload: CapturePayload):
    captured_logs.append(payload.model_dump())
    return {"status": "captured"}
```

### 4. Update Docker Compose
Rewire the network so Kernel talks to Agents, and Agents talk to Probe.

**File:** `docker-compose.yml`

```yaml
services:
  kernel:
    # ...
    environment:
      # POINT BACK TO REAL AGENTS
      - AGENT_HOST=agents 
      - AGENT_PORT=8000
    depends_on:
      - agents

  agents:
    # ...
    environment:
      # ... keys ...
      # ACTIVATE DEBUG INTERCEPTION
      - DEBUG_PROBE_URL=http://debug-probe:8080 
    depends_on:
      - debug-probe

  debug-probe:
    # ... (Keep existing definition)
```

### Summary of Changes
1.  **Removed:** Logic duplication in `debug-probe/src/main.py`.
2.  **Added:** `DEBUG_PROBE_URL` setting in Agent Service.
3.  **Modified:** `core/llm.py` to check for that URL, send the *exact* computed prompt to the probe, and return a dummy response.

Now, when you run the stack:
1.  Kernel sends request to `agents:8000`.
2.  `agents` builds the prompt (applying all current rules, tools, and formatting).
3.  `agents` POSTs the result to `debug-probe:8080/capture`.
4.  `agents` returns a success message to Kernel.
5.  Kernel moves to the next node in the DAG.
6.  You refresh the Probe UI and see the **exact** system instructions generated by your production code.