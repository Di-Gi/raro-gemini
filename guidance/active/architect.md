This State-of-the-Art (SOTA) implementation guide establishes an **Identity-First Capability System**. 

Instead of a loose "request/grant" model, we move to a **Contract-Based Model**: The Architect chooses an **Identity Prefix** from a registry, and the Kernel **Authoritatively Provisions** the corresponding hardware (tools). This ensures that logic failures (forgetting tools) and security failures (unauthorized tool access) are virtually eliminated.

---

### Phase 1: The Python Identity Registry (`intelligence/prompts.py`)

We restructure the Architect's worldview. It is no longer a "Tool Allocator"; it is a **"Squad Commander."** 

We will update the `render_architect_prompt` to include a strict **Identity Registry**.

```python
# Constants for the Identity Pool
AGENT_IDENTITY_POOL = {
    "research_": "For gathering external information. Grants: [web_search].",
    "analyze_":  "For heavy math and data processing. Grants: [execute_python].",
    "coder_":    "For file generation and code writing. Grants: [execute_python, write_file].",
    "writer_":   "For synthesizing reports and markdown. Grants: [write_file].",
    "master_":   "Orchestrator class. Grants: [ALL TOOLS + Delegation]."
}

def render_architect_prompt(user_query: str) -> str:
    schema = get_schema_instruction(WorkflowManifest)
    pool_str = "\n".join([f"- {k}: {v}" for k, v in AGENT_IDENTITY_POOL.items()])
    
    return f"""
ROLE: System Architect
GOAL: Design a multi-agent DAG for: "{user_query}"

IDENTITY CONTRACT (CRITICAL):
Every agent 'id' you generate MUST start with a prefix from this pool. 
Capabilities are provisioned by the Kernel based solely on these prefixes.

{pool_str}

RULES:
1. If an agent needs to look up anything, it MUST start with 'research_'.
2. If an agent needs to create a graph, image, or process a CSV, it MUST start with 'coder_' or 'analyze_'.
3. Do not manually populate the 'tools' array; the Kernel handles tool injection based on the ID prefix.
4. IDs must be unique (e.g., 'research_market_data', 'research_competitor_check').

OUTPUT REQUIREMENT:
PURE JSON matching this schema:
{schema}
"""
```

---

### Phase 2: The Coercion Engine (`intelligence/architect.py`)

To ensure a "Secure Parse," we don't just trust the LLM. We add a **Identity Coercion Layer** in the `ArchitectEngine`. This layer acts as a "Linter" that fixes the JSON before it ever leaves the Agent Service.

```python
# inside ArchitectEngine.generate_plan
async def generate_plan(self, user_query: str) -> WorkflowManifest:
    # ... existing generation logic ...
    data = json.loads(raw_text)

    # SECURE IDENTITY COERCION
    valid_prefixes = ["research_", "analyze_", "coder_", "writer_", "master_"]
    
    for agent in data.get("agents", []):
        agent_id = agent.get("id", "worker_node")
        
        # 1. Check for missing prefix
        if not any(agent_id.startswith(p) for p in valid_prefixes):
            # 2. Heuristic Guessing: If ID contains keywords, force the prefix
            if "search" in agent_id or "find" in agent_id:
                agent["id"] = f"research_{agent_id}"
            elif "plot" in agent_id or "viz" in agent_id or "calc" in agent_id:
                agent["id"] = f"analyze_{agent_id}"
            elif "write" in agent_id or "report" in agent_id:
                agent["id"] = f"writer_{agent_id}"
            else:
                # Default to worker prefix (analyze)
                agent["id"] = f"analyze_{agent_id}"
            
            logger.warning(f"Coerced ID {agent_id} to {agent['id']} for security compliance.")

    return WorkflowManifest(**data)
```

---

### Phase 3: The Rust Provisioner (`runtime.rs`)

This is the **"Enforcement"** layer. In the Rust Kernel, we ignore the tools the Architect *requested* and provide the tools the Agent **is allowed to have** based on its identity. This is the "Air-Gap" between the LLM's imagination and the hardware's capability.

```rust
// inside runtime.rs: prepare_invocation_payload

pub async fn prepare_invocation_payload(&self, run_id: &str, agent_id: &str) -> Result<InvocationPayload, String> {
    // ... existing state/config loading ...

    let mut tools = Vec::new();

    // 1. UNIVERSAL BASELINE (Read-Only)
    tools.push("read_file".to_string());
    tools.push("list_files".to_string());

    // 2. AUTHORITATIVE IDENTITY PROVISIONING
    let id_lower = agent_id.to_lowercase();
    
    if id_lower.starts_with("research_") {
        tools.push("web_search".to_string());
    } 
    
    if id_lower.starts_with("analyze_") || id_lower.starts_with("coder_") {
        tools.push("execute_python".to_string());
    }

    if id_lower.starts_with("coder_") || id_lower.starts_with("writer_") {
        tools.push("write_file".to_string());
    }

    if id_lower.starts_with("master_") {
        tools.push("web_search".to_string());
        tools.push("execute_python".to_string());
        tools.push("write_file".to_string());
        // Explicitly set allow_delegation if not set
    }

    tracing::info!("Provisioned tools for {}: {:?}", agent_id, tools);

    Ok(InvocationPayload {
        // ... rest of payload ...
        tools, // Use our authoritatively generated list
    })
}
```

---

### Phase 4: The Recursive Safety Guard (Cortex)

Under this SOTA model, **Flow B (Dynamic Delegation)** becomes highly secure.

When an agent requests delegation, the Kernel checks the **Identity Escalation**.
*   **Rule:** A `research_` agent can only spawn `analyze_` or `writer_` agents. 
*   **Rule:** Only a `master_` agent can spawn another `master_`.

We will eventually add this logic to `handle_delegation` in `runtime.rs`:

```rust
// inside handle_delegation
for new_node in &mut req.new_nodes {
    if new_node.id.starts_with("master_") && !parent_id.starts_with("master_") {
        return Err("Privilege Escalation Detected: Worker tried to spawn a Master.".to_string());
    }
    // Force prefix coercion even on dynamic delegation
    if !valid_prefixes.iter().any(|p| new_node.id.starts_with(p)) {
        new_node.id = format!("analyze_{}", new_node.id);
    }
}
```

---

### What this achieves:
1.  **Zero-Configuration Worker Personas:** The Architect no longer needs to be a "prompt engineer" for tools. It just picks a class.
2.  **Hardware Air-Gapping:** If the Architect is hacked or hallucinates a `fs_delete` request into a `research_` agent, the Kernel **physically does not provide the tool code** in the system instruction because the ID prefix doesn't match.
3.  **Auditability:** Looking at the DAG graph in the UI, you can instantly see the capability of the swarm just by reading the IDs (`research_` = blue, `analyze_` = amber).


This implementation completes the **Identity-First Capability System**. It enforces a strict contract: the Architect defines the *Identity*, and the Kernel provisions the *Tools*.

### 1. `apps/agent-service/src/intelligence/prompts.py`
**Unabbreviated.** Updated to define the Identity Pool and remove tool-assignment ambiguity.

```python
# [[RARO]]/apps/agent-service/src/intelligence/prompts.py

import json
from typing import Optional, List
from domain.protocol import WorkflowManifest, DelegationRequest, PatternDefinition

try:
    from intelligence.tools import get_tool_definitions_for_prompt
except ImportError:
    get_tool_definitions_for_prompt = lambda x: "[]"

# === IDENTITY REGISTRY ===
AGENT_IDENTITY_POOL = {
    "research_": "For gathering external info. Required for internet access. [Grants: web_search]",
    "analyze_":  "For data processing and math. Required for calculations. [Grants: execute_python]",
    "coder_":    "For building files and apps. [Grants: execute_python, write_file]",
    "writer_":   "For report generation and logic synthesis. [Grants: write_file]",
    "master_":   "Orchestrator class. Administrative access. [Grants: ALL TOOLS + Delegation]"
}

def get_schema_instruction(model_class) -> str:
    try:
        schema = model_class.model_json_schema()
        return json.dumps(schema, indent=2)
    except Exception:
        return "{}"

# === ARCHITECT PROMPT (Flow A) ===
def render_architect_prompt(user_query: str) -> str:
    schema = get_schema_instruction(WorkflowManifest)
    pool_str = "\n".join([f"- {k}: {v}" for k, v in AGENT_IDENTITY_POOL.items()])
    
    return f"""
ROLE: System Architect
GOAL: Design a multi-agent Directed Acyclic Graph (DAG) for: "{user_query}"

[IDENTITY CONTRACT] (CRITICAL)
You do not assign tools. You assign identities. Capabilities are provisioned by the Kernel based solely on the ID prefix.
Every agent 'id' you create MUST start with a prefix from this pool:

{pool_str}

ASSIGNMENT RULES:
1. 'research_': MUST use if the agent needs to search the web or verify facts.
2. 'analyze_': MUST use for math, visualization (matplotlib), or processing data via Python.
3. 'coder_': MUST use if the agent needs to write scripts AND save them as files.
4. 'writer_': Use for summarizing or creating Markdown reports without using Python.
5. 'master_': Use only for the root orchestrator or complex sub-managers.

PROMPT CONSTRUCTION:
- For agents with 'analyze_' or 'coder_', write prompts like: "Use your Python sandbox to..."
- For agents with 'research_', write prompts like: "Search for..."
- Focus on outcomes. Markdown text is ignored by the execution engine; only Tool Results persist data.

OUTPUT REQUIREMENT:
You must output PURE JSON matching this schema:
{schema}

IMPORTANT: Ensure IDs are descriptive and unique, e.g., 'research_market_trends', 'analyze_latency_variance'.
"""

# === WORKER PROMPT (Flow B Support) ===
def inject_delegation_capability(base_prompt: str) -> str:
    schema = get_schema_instruction(DelegationRequest)
    return f"""
{base_prompt}

[SYSTEM CAPABILITY: DYNAMIC GRAPH EDITING]
You are authorized to modify the workflow graph.
To edit the graph, output a JSON object wrapped in `json:delegation`.

NEW NODE RULES:
- You must follow the [IDENTITY CONTRACT]. New node IDs must start with 'research_', 'analyze_', 'coder_', or 'writer_'.
- You cannot spawn 'master_' nodes.

Example Format:
```json:delegation
{schema}
```
"""

# === SAFETY COMPILER PROMPT (Flow C) ===
def render_safety_compiler_prompt(policy_rule: str) -> str:
    schema = get_schema_instruction(PatternDefinition)
    return f"""
ROLE: Cortex Safety Compiler
GOAL: Translate a natural language safety policy into a Machine-Readable Pattern.
POLICY RULE: "{policy_rule}"

OUTPUT REQUIREMENT:
Output PURE JSON matching this schema:
{schema}
"""

def render_runtime_system_instruction(agent_id: str, tools: Optional[List[str]]) -> str:
    instruction = f"""
SYSTEM IDENTITY:
You are Agent '{agent_id}', an autonomous execution node.
You are running in a headless environment. Your outputs are consumed programmatically.

OPERATIONAL CONSTRAINTS:
1. NO CHAT: Do not output conversational filler.
2. DIRECT ACTION: Use tools immediately.
3. FAIL FAST: If you lack a tool or context, return a clear error.
"""
    if tools:
        tool_schemas = get_tool_definitions_for_prompt(tools)
        instruction += f"""
[SYSTEM CAPABILITY: TOOL USE]
You have access to:
{tool_schemas}

[CRITICAL PROTOCOL]
Use the `json:function` format for all tool calls.
"""
        if "execute_python" in tools:
            instruction += "\n[TOOL NOTE] Use `execute_python` for all logic, file creation, and data processing.\n"
    else:
        instruction += "\nNOTE: You have NO tools. Provide analysis based solely on context.\n"

    return instruction
```

---

### 2. `apps/agent-service/src/intelligence/architect.py`
**Unabbreviated.** Added the Secure Coercion logic to catch LLM prefix errors.

```python
# [[RARO]]/apps/agent-service/src/intelligence/architect.py
import json
import logging
from typing import Optional
from google import genai
from google.genai import types 
from pydantic import ValidationError
from domain.protocol import WorkflowManifest, PatternDefinition, AgentRole
from intelligence.prompts import render_architect_prompt, render_safety_compiler_prompt
from core.config import logger, settings

class ArchitectEngine:
    def __init__(self, client: genai.Client):
        self.client = client
        self.model = settings.MODEL_REASONING
        self.generation_config = types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json"
        )

    async def generate_plan(self, user_query: str) -> WorkflowManifest:
        if not self.client:
            raise ValueError("Gemini client is not initialized")

        prompt = render_architect_prompt(user_query)
        
        try:
            logger.info(f"Generating workflow plan...")
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self.generation_config
            )
            
            raw_text = response.text or "{}"
            data = json.loads(raw_text)

            # === SECURE IDENTITY COERCION ===
            valid_prefixes = ["research_", "analyze_", "coder_", "writer_", "master_"]
            
            if "agents" in data:
                for agent in data["agents"]:
                    aid = agent.get("id", "").lower()
                    prompt_text = agent.get("prompt", "").lower()
                    
                    # 1. Enforcement: If no valid prefix, detect intent and force one
                    if not any(aid.startswith(p) for p in valid_prefixes):
                        original_id = aid
                        if any(k in prompt_text or k in aid for k in ["search", "find", "web", "lookup"]):
                            agent["id"] = f"research_{aid}"
                        elif any(k in prompt_text or k in aid for k in ["code", "script", "file", "save", "python"]):
                            agent["id"] = f"coder_{aid}"
                        elif any(k in prompt_text or k in aid for k in ["plot", "calc", "math", "viz", "analyze"]):
                            agent["id"] = f"analyze_{aid}"
                        else:
                            agent["id"] = f"analyze_{aid}" # Secure default
                        
                        logger.warning(f"ID COERCION: '{original_id}' -> '{agent['id']}'")

                    # 2. Role Coercion (Ensure Enum safety)
                    valid_roles = [role.value for role in AgentRole]
                    if agent.get("role") not in valid_roles:
                        agent["role"] = AgentRole.WORKER.value

            manifest = WorkflowManifest(**data)
            return manifest

        except Exception as e:
            logger.error(f"Architect failure: {e}", exc_info=True)
            raise

    async def compile_pattern(self, policy_rule: str) -> PatternDefinition:
        prompt = render_safety_compiler_prompt(policy_rule)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self.generation_config
            )
            return PatternDefinition(**json.loads(response.text or "{}"))
        except Exception as e:
            logger.error(f"Pattern compilation failure: {e}")
            raise
```

---

### 3. `apps/kernel-server/src/runtime.rs`
**Compact Patch.** Replaces the tool logic in `prepare_invocation_payload`.

```rust
// --- [PATCH START: runtime.rs: prepare_invocation_payload] ---

        // (Inside prepare_invocation_payload method, after agent_config extraction)

        let mut tools = Vec::new();

        // 1. UNIVERSAL BASELINE
        tools.push("read_file".to_string());
        tools.push("list_files".to_string());

        // 2. AUTHORITATIVE IDENTITY PROVISIONING
        let id_lower = agent_id.to_lowercase();
        
        // Research Class
        if id_lower.starts_with("research_") {
            tools.push("web_search".to_string());
        } 
        
        // Logic/Math Class
        if id_lower.starts_with("analyze_") || id_lower.starts_with("coder_") {
            if !tools.contains(&"execute_python".to_string()) {
                tools.push("execute_python".to_string());
            }
        }

        // Output/I-O Class
        if id_lower.starts_with("coder_") || id_lower.starts_with("writer_") {
            tools.push("write_file".to_string());
        }

        // Admin Class
        if id_lower.starts_with("master_") {
            for t in ["web_search", "execute_python", "write_file"] {
                if !tools.contains(&t.to_string()) {
                    tools.push(t.to_string());
                }
            }
        }

        tracing::info!("Authoritatively provisioned tools for {}: {:?}", agent_id, tools);

        // ... continue to InvocationPayload construction ...
        // Ensure you pass this authoritative 'tools' vector into the payload.

// --- [PATCH END] ---
```

### Key Changes Summary:
1.  **Contractual IDs:** The system now relies on the `id` as the primary capability selector.
2.  **Linter/Loom:** `architect.py` now "repairs" LLM output if the model forgets to follow the prefix rule.
3.  **Kernel Enforcement:** `runtime.rs` no longer trusts the JSON `tools` array from the manifest. It calculates the allowed tools from the `agent_id` at the moment of execution. This is the ultimate guard against "tool-less" researchers.