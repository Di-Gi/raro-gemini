This conclusive implementation guidebook for **RARO (Reconfigurable Agentic Runtime Operator)** integrates the stateful reasoning of Gemini 3 with the performance of a Rust/Python/Svelte stack. 

---

## 1. The Core Mental Model: "Cognitive State"
RARO manages two distinct types of state that are required for production-grade Gemini 3 workflows:
1.  **Data State (The Library):** Large-scale context (PDFs/Video) managed via **Explicit CacheResources** to ensure deterministic 90% cost reduction.
2.  **Reasoning State (The Thought Chain):** Continuous logic flow managed via **Thought Signatures** to prevent "agent amnesia" across the DAG.

---

## 2. Execution Layer: Python Agent Service
This layer translates RARO Kernel instructions into Gemini 3 API calls.

### 2.1 The "Stateful" Invocation Loop
Replace mock logic in `apps/agent-service/src/main.py` with this pattern. It explicitly handles the hand-off of signatures between agents.

```python
import google.generativeai as genai

async def invoke_gemini_node(request: AgentRequest):
    model = genai.GenerativeModel(request.model)
    
    # 1. Setup Explicit Cache for deterministic performance
    # Requesting the kernel-provided cache ID for 1M context reliability
    generation_config = {
        "cached_content": request.cached_content_id if request.cached_content_id else None
    }

    # 2. Build the Turn with Signature
    # We MUST pass the parent's signature to resume the reasoning chain
    contents = [{
        "role": "user",
        "parts": [{"text": request.prompt}],
        "thought_signature": request.parent_signature  # Resume from parent's thought
    }]

    response = await model.generate_content_async(
        contents,
        generation_config=generation_config
    )

    # 3. Extract New Signature for the next child node in the DAG
    new_signature = response.candidates[0].thought_signature
    
    return {
        "output": response.text,
        "thought_signature": new_signature,
        "metrics": response.usage_metadata
    }
```

### 2.2 Multimodal Native Handling
*   **PDF Extraction**: Do not OCR. Pass `mime_type: "application/pdf"` directly.
*   **Video**: Use `resolution: "high"` for data charts/graphs and `low` for talking-head portions.

---

## 3. Orchestration Layer: Rust Kernel
The Kernel acts as the scheduler. Its primary job is **Signature Routing**.

### 3.1 Thought Signature Routing logic
In `apps/kernel-server/src/runtime.rs`, implement the logic that finds the parent's output signature and provides it to the child.

```rust
// Logic to prepare the payload for the Python Agent Service
pub async fn prepare_invocation_payload(&self, run_id: &str, agent_id: &str) -> InvocationPayload {
    // 1. Get DAG dependencies
    let dependencies = self.dag.get_dependencies(agent_id);
    
    // 2. Fetch the most recent signature from the immediate parent
    // If orchestrator is parent of extractor, extractor gets orchestrator's signature
    let parent_signature = if let Some(parent_id) = dependencies.first() {
        self.thought_signatures.get_signature(run_id, parent_id)
    } else {
        None // Root node starts a fresh chain
    };

    InvocationPayload {
        agent_id: agent_id.to_string(),
        parent_signature,
        cached_content_id: self.get_project_cache(run_id),
        // ... prompt and model variants
    }
}
```

---

## 4. UI Layer: SvelteKit Web Console
The UX must transform from a static dashboard to a **"Glass Box"** where reasoning is visible.

### 4.1 Visualizing the Reasoning Flow
Update `PipelineStage.svelte` to show the "pulse" of reasoning:
*   **Active Links**: Use an animated SVG `stroke-dasharray` when a signature is successfully passed between nodes.
*   **Thought Trace**: Create a component that renders the `thought_signature` hash on hover, allowing researchers to verify reasoning continuity.

### 4.2 Deep Think Configuration
Add a "Thinking Budget" slider to the `ControlDeck.svelte`. This maps to the `thinking_level` in Gemini 3 Deep Think, allowing users to trade latency for innovation.

---

## 5. Scaling Strategy: The Caching Tier
To handle the 1M token context of research papers without breaking the hackathon budget:

1.  **Pre-Warm Cache**: The `orchestrator` agent uploads all PDFs once to create a `CacheResource`.
2.  **Shared ID**: The Rust Kernel stores the `cached_content_id` in the `RuntimeState`.
3.  **Universal Reference**: Every subsequent agent (KG Builder, Hypothesis Generator) references this ID.
    *   *Result:* Subsequent agents process 1M tokens at a ~90% cost discount compared to fresh uploads.

---

## 6. The Final 24h Roadmap

| Milestone | Action | Output |
| :--- | :--- | :--- |
| **T-24h** | **State Wiring** | Complete the gRPC/REST handoff of Thought Signatures between Rust and Python. |
| **T-18h** | **Specialized Agents** | Implement `gemini-3-deep-think` for the `hypothesis_generator` agent. |
| **T-12h** | **Multimodal Integration** | Interleave PDF and Video inputs into a single "Research Project" prompt. |
| **T-6h** | **Observability Dash** | Ensure the Web Console displays live token usage and reasoning signatures. |
| **T-0h** | **Submission** | Record a demo showing a "Hot Reload" (reconfiguring an agent mid-workflow). |

---

## 7. Key Differentiators for Judges
*   **Reconfigurability**: RARO can hot-swap a "Flash" agent for a "Pro" agent mid-run without losing reasoning context.
*   **Glass Box AI**: Transparent view into the model's internal reasoning chain via signature logging.
*   **Platform Thinking**: It is not just an app; it is a **Runtime** for any future multimodal agentic research.

**This guide establishes RARO as a high-engineering-floor submission that uses Gemini 3's stateful nature exactly as intended by the new API architecture.**