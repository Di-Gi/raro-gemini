# Agent Service Integration Proposal

## Executive Summary

The agent-service has undergone a **significant architectural refactor** from a monolithic FastAPI service to a **layered intelligence architecture**. Most of the patch has been successfully applied, but several critical features from the original implementation need to be restored to maintain full functionality.

---

## Current State Analysis

### ✅ Successfully Implemented (New Architecture)

**File Structure:**
```
apps/agent-service/src/
├── main.py                 # Slim FastAPI interface
├── core/
│   └── config.py          # Centralized config & clients
├── domain/
│   └── protocol.py        # Shared data models (DDD)
├── intelligence/
│   ├── prompts.py         # Dynamic prompt templates
│   └── architect.py       # Flow A & C logic
└── utils/
    └── schema_formatter.py # Schema extraction helper
```

**Capabilities:**
- ✅ **Flow A (Auto-DAG)**: `/plan` endpoint generates WorkflowManifest from natural language
- ✅ **Flow B (Delegation)**: Parses DelegationRequest from agent responses
- ✅ **Flow C (Safety)**: `/compile-pattern` converts policy rules to Pattern JSON
- ✅ **Architecture Separation**: Clean separation of concerns
- ✅ **WebSocket Support**: Real-time execution endpoint
- ✅ **Shared Logic**: `_execute_agent_logic` used by both HTTP and WS

### ❌ Missing from Original (main.py.bak)

The following **production-critical** features were lost during the refactor:

1. **Multimodal Support** (`_load_multimodal_file`)
   - PDF processing
   - Video file handling
   - Image support
   - **Impact**: Cannot process research papers or video content

2. **Parent Signature Handling**
   - Thought chain continuation
   - Reasoning context preservation
   - **Impact**: Breaks DAG reasoning flow

3. **Batch Invocation** (`/invoke/batch`)
   - Parallel agent execution
   - **Impact**: Performance degradation for multi-agent runs

4. **Utility Endpoints**
   - `/agents/list`: Agent discovery
   - `/models/available`: Model catalog
   - **Impact**: UI/tooling cannot discover capabilities

5. **Error Handling**
   - Comprehensive try/catch blocks
   - Token usage fallbacks
   - **Impact**: Silent failures, poor debugging

---

## Gap Analysis: What's Still Missing

### 1. Tool Invocation (Mentioned but Not Implemented)
- `tools` field exists in protocol but no execution logic
- No function calling integration with Gemini API
- **Needed For**: Shell commands, file operations, API calls

### 2. Streaming Support (Partial)
- WebSocket infrastructure exists but doesn't stream tokens
- Gemini 3 supports streaming but we're using synchronous API
- **Needed For**: Real-time UI feedback

### 3. Pattern Registry Integration (Incomplete)
- No `/patterns/register` endpoint to dynamically add safety rules
- Patterns are hardcoded in Rust
- **Needed For**: Runtime safety rule updates

### 4. Caching Implementation (Placeholder)
- `cached_content_id` field exists but not used
- Gemini 3 context caching not implemented
- **Needed For**: Cost savings on long documents

---

## Proposed Solution

### Phase 1: Critical Restoration (Priority: HIGH)

**Restore missing functionality from backup into new architecture:**

1. **Add `core/llm.py`** - Extract LLM logic from main.py
   - Multimodal file loading
   - Parent signature handling
   - Token usage parsing
   - Error handling wrappers

2. **Update `main.py`** - Add missing endpoints
   - `/invoke/batch`
   - `/agents/list`
   - `/models/available`

3. **Enhance `_execute_agent_logic`** - Integrate multimodal support
   - Call multimodal loader if `file_paths` present
   - Include parent signature in conversation
   - Improve error handling

### Phase 2: Feature Completion (Priority: MEDIUM)

4. **Implement Tool Calling**
   - Add `tools` parameter to Gemini API call
   - Parse function call responses
   - Execute tool logic or return to Kernel

5. **Add Caching Support**
   - Implement Gemini context caching
   - Cache management endpoints

6. **Pattern Registry API**
   - POST `/patterns/register` → Forward to Kernel
   - GET `/patterns/list` → Read from Kernel

### Phase 3: Optimization (Priority: LOW)

7. **Streaming Tokens**
   - Replace synchronous API with streaming
   - Send token-by-token via WebSocket

8. **Enhanced Monitoring**
   - Prometheus metrics
   - OpenTelemetry tracing

---

## Proposed File Changes

### 1. Create `core/llm.py`

**Purpose:** Centralize all LLM interaction logic

```python
# [[RARO]]/apps/agent-service/src/core/llm.py
# Purpose: LLM Wrapper with Multimodal & Error Handling
# Architecture: Core Layer

from typing import Dict, Any, List, Optional
import base64
import mimetypes
from pathlib import Path
from google.genai import types
from core.config import gemini_client, logger

async def load_multimodal_file(file_path: str) -> Dict[str, Any]:
    """Load PDF, video, or image for Gemini 3"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    mime_type, _ = mimetypes.guess_type(file_path)

    with open(file_path, "rb") as f:
        file_data = base64.standard_b64encode(f.read()).decode("utf-8")

    return {
        "inline_data": {
            "mime_type": mime_type or "application/octet-stream",
            "data": file_data
        }
    }

async def call_gemini_with_context(
    model: str,
    prompt: str,
    input_data: Dict[str, Any],
    file_paths: List[str],
    parent_signature: Optional[str],
    thinking_level: Optional[int],
    tools: List[str]
) -> Dict[str, Any]:
    """
    Unified Gemini API caller with:
    - Multimodal support
    - Parent signature continuation
    - Tool calling
    - Error handling
    """

    # Build config
    config_params: Dict[str, Any] = {"temperature": 1}

    if "deep-think" in model and thinking_level:
        config_params["thinking_config"] = types.ThinkingConfig(
            include_thoughts=True,
            thinking_budget=min(max(thinking_level * 1000, 1000), 10000)
        )

    # Build conversation
    contents: List[Dict[str, Any]] = []

    # Add parent signature for reasoning continuity
    if parent_signature:
        logger.info(f"Resuming from parent signature: {parent_signature[:20]}...")
        contents.append({
            "role": "user",
            "parts": [{"text": f"Previous Context Signature: {parent_signature}"}]
        })
        contents.append({
            "role": "model",
            "parts": [{"text": "Context acknowledged."}]
        })

    # Build user message
    user_parts: List[Dict[str, Any]] = []

    # Add multimodal files
    if file_paths:
        for file_path in file_paths:
            logger.info(f"Loading multimodal file: {file_path}")
            file_part = await load_multimodal_file(file_path)
            user_parts.append(file_part)

    # Add context data
    if input_data:
        context_str = json.dumps(input_data, indent=2)
        user_parts.append({"text": f"CONTEXT DATA:\n{context_str}\n"})

    # Add prompt
    user_parts.append({"text": prompt})

    contents.append({"role": "user", "parts": user_parts})

    # Call API
    response = await asyncio.to_thread(
        gemini_client.models.generate_content,
        model=model,
        contents=contents,
        config=config_params,
        # tools=tools if tools else None  # Enable when tool support is ready
    )

    # Parse response
    response_text = response.text or ""

    # Extract usage
    usage = getattr(response, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", 0) or 0
    output_tokens = getattr(usage, "candidates_token_count", 0) or 0

    # Generate signature
    thought_signature = base64.b64encode(
        f"{model}_{datetime.now().isoformat()}".encode()
    ).decode("utf-8")

    return {
        "text": response_text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thought_signature": thought_signature
    }
```

### 2. Update `main.py`

**Add missing endpoints:**

```python
@app.post("/invoke/batch")
async def invoke_batch(requests: List[AgentRequest]):
    """Invoke multiple agents in parallel"""
    logger.info(f"Invoking {len(requests)} agents in batch")
    results = []
    for req in requests:
        response = await invoke_agent(req)
        results.append(response)
    return results

@app.get("/agents/list")
async def list_agents():
    """List available agent configurations"""
    return {
        "agents": [
            {
                "id": "orchestrator",
                "role": "orchestrator",
                "model": "gemini-2.5-flash-lite",
                "tools": ["plan_task", "route_agents"]
            },
            {
                "id": "extractor",
                "role": "worker",
                "model": "gemini-2.5-flash",
                "tools": ["extract_pdf", "parse_video"]
            },
            {
                "id": "synthesizer",
                "role": "worker",
                "model": "gemini-2.5-flash-lite",
                "tools": ["combine_results", "summarize"]
            }
        ]
    }

@app.get("/models/available")
async def available_models():
    """List available Gemini 3 model variants"""
    return {
        "models": [
            {
                "id": "gemini-2.5-flash",
                "name": "Gemini 3 Flash",
                "description": "Fast, 69% cheaper, PhD-level reasoning"
            },
            {
                "id": "gemini-2.5-flash-lite",
                "name": "Gemini 3 Pro",
                "description": "Maximum reasoning depth for complex tasks"
            }
        ]
    }
```

### 3. Refactor `_execute_agent_logic`

**Use new `core/llm.py`:**

```python
from core.llm import call_gemini_with_context

async def _execute_agent_logic(request: AgentRequest) -> AgentResponse:
    start_time = time.time()

    if not gemini_client:
        raise ValueError("Gemini Client unavailable")

    try:
        # 1. Prompt Enhancement
        final_prompt = request.prompt
        if "deep-think" not in request.model:
            final_prompt = inject_delegation_capability(request.prompt)

        # 2. Call LLM (with multimodal + parent signature support)
        result = await call_gemini_with_context(
            model=request.model,
            prompt=final_prompt,
            input_data=request.input_data,
            file_paths=request.file_paths,
            parent_signature=request.parent_signature,
            thinking_level=request.thinking_level,
            tools=request.tools
        )

        # 3. Parse Delegation
        delegation_request = None
        cleaned = result["text"].strip()
        if cleaned.startswith("{") or cleaned.startswith("```json"):
            try:
                if cleaned.startswith("```json"):
                    cleaned = cleaned.split("```json")[1].split("```")[0].strip()
                data = json.loads(cleaned)
                if "delegation" in data:
                    delegation_request = DelegationRequest(**data["delegation"])
            except:
                pass

        # 4. Store to Redis
        if redis_client and result["text"]:
            try:
                key = f"run:{request.run_id}:agent:{request.agent_id}:output"
                redis_client.setex(key, 3600, json.dumps({"result": result["text"]}))
            except Exception as e:
                logger.warning(f"Redis write failed: {e}")

        # 5. Return Response
        latency_ms = (time.time() - start_time) * 1000

        return AgentResponse(
            agent_id=request.agent_id,
            success=True,
            output={"result": result["text"]},
            delegation=delegation_request,
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            tokens_used=result["input_tokens"] + result["output_tokens"],
            thought_signature=result["thought_signature"],
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.error(f"Execution failed: {e}")
        return AgentResponse(
            agent_id=request.agent_id,
            success=False,
            error=str(e),
            latency_ms=(time.time() - start_time) * 1000
        )
```

---

## Testing Strategy

### 1. Unit Tests
- Test multimodal file loading (PDF, video, image)
- Test parent signature handling
- Test delegation parsing
- Test error handling

### 2. Integration Tests
- Test Flow A: Natural language → WorkflowManifest
- Test Flow B: Agent → DelegationRequest → Graph splice
- Test Flow C: Policy rule → Pattern → Cortex trigger

### 3. End-to-End Tests
- Full research workflow with PDF inputs
- Multi-agent DAG with delegation
- Safety pattern interrupt

---

## Migration Plan

### Step 1: Create `core/llm.py`
- Extract multimodal logic
- Add parent signature handling
- Add comprehensive error handling

### Step 2: Update `main.py`
- Import from `core.llm`
- Add missing endpoints
- Update `_execute_agent_logic`

### Step 3: Verification
- Run existing tests
- Add new tests for restored features
- Manual testing with Kernel integration

### Step 4: Documentation
- Update API docs
- Add architecture diagrams
- Document Flow A/B/C with examples

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking existing Kernel integration | HIGH | Maintain backward compatibility in AgentResponse schema |
| Performance regression with multimodal | MEDIUM | Add caching, optimize file loading |
| Missing test coverage | MEDIUM | Comprehensive test suite before merge |
| Dependency conflicts | LOW | Lock file versions, test in isolation |

---

## Success Criteria

✅ All endpoints from backup restored
✅ Multimodal support working
✅ Parent signature preserves reasoning context
✅ Batch invocation performance matches original
✅ No regressions in Flow A/B/C
✅ Test coverage > 80%
✅ Documentation complete

---

## Recommendation

**Proceed with Phase 1 immediately** to restore critical functionality. The current architecture is solid, but missing production-essential features. The proposed changes are **low-risk, high-value** additions that maintain the clean architecture while restoring full capability.

**Estimated Effort:**
- Phase 1 (Critical): 2-3 hours
- Phase 2 (Features): 4-6 hours
- Phase 3 (Optimization): 8-10 hours

**Priority:** Phase 1 should be completed before any production deployment.
