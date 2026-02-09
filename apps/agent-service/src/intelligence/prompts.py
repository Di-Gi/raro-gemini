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
    """
    Extracts a clean JSON schema from a Pydantic model to inject into prompts.
    This guarantees the LLM knows the EXACT JSON format we require.
    """
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
#
# def render_architect_prompt(user_query: str) -> str:
#     schema = get_schema_instruction(WorkflowManifest)
#     return f"""
# ROLE: System Architect
# GOAL: Design a multi-agent Directed Acyclic Graph (DAG) for: "{user_query}"

# INSTRUCTIONS:
# 1. **Structural Role**: The 'role' field MUST be exactly one of: ['orchestrator', 'worker', 'observer']. 
#    - Use 'worker' for almost all tasks.
# 2. **Specialty**: Use the 'specialty' field for the functional title (e.g., 'Analyst', 'Researcher', 'Coder').
# 3. **ID**: Use unique slug-style IDs (e.g., 'research_node_1').

# OUTPUT REQUIREMENT:
# Output PURE JSON matching this schema:
# {schema}

# IMPORTANT: If you put 'Analyst' in the 'role' field, the system will crash. Put 'worker' in 'role' and 'Analyst' in 'specialty'.
# """


# === WORKER PROMPT (Flow B Support) ===
def inject_delegation_capability(base_prompt: str) -> str:
    schema = get_schema_instruction(DelegationRequest)
    return f"""
{base_prompt}

[SYSTEM CAPABILITY: DYNAMIC GRAPH EDITING]
You are authorized to modify the workflow graph.
To edit the graph, output a JSON object wrapped in `json:delegation`.

1. **Expansion**: Define `new_nodes` to handle complexity you cannot solve alone.
2. **Pruning**: If your new nodes render existing future nodes redundant, list their IDs in `prune_nodes`.
   - You can only prune nodes listed as [PENDING] in your graph context.

[CRITICAL: GRAPH CONTINUITY & GRAFTING]
When you inject new nodes, you must ensure the graph remains connected.
If there is an existing downstream node (e.g., a final writer) that should wait for your new nodes:
1. **DO NOT** prune it.
2. **DO** redefine it in `new_nodes` with the SAME ID, but update its `depends_on` list to point to your new agents.
   *This is called "Grafting". It overwrites the existing node's dependencies to ensure flow continuity.*

Example Format:
```json:delegation
{{
  "reason": "Spawning specific researchers and updating the writer to wait for them.",
  "strategy": "child",
  "new_nodes": [
    {{
      "id": "research_new_A",
      "role": "worker",
      "prompt": "Search A...",
      "tools": ["web_search"],
      "depends_on": ["master_planner"]
    }},
    {{
      "id": "research_new_B",
      "role": "worker",
      "prompt": "Search B...",
      "tools": ["web_search"],
      "depends_on": ["master_planner"]
    }},
    {{
      "id": "writer_final", 
      "role": "worker",
      "prompt": "Compile the report from the new researchers.",
      "tools": ["write_file"],
      "depends_on": ["research_new_A", "research_new_B"] 
    }}
  ],
  "prune_nodes": ["old_researcher_node"]
}}
```
*Note in the example above: 'writer_final' existed previously but is updated here to wait for A and B.*

[IDENTITY CONTRACT]
New node IDs must start with 'research_', 'analyze_', 'coder_', or 'writer_'.
You cannot spawn 'master_' nodes.

Schema:
{schema}
"""

# === SAFETY COMPILER PROMPT (Flow C) ===
def render_safety_compiler_prompt(policy_rule: str) -> str:
    schema = get_schema_instruction(PatternDefinition)
    return f"""
ROLE: Cortex Safety Compiler
GOAL: Translate a natural language safety policy into a Machine-Readable Pattern.

POLICY RULE: "{policy_rule}"

INSTRUCTIONS:
1. Identify the trigger event (e.g., ToolCall, AgentFailed).
2. Define the condition logic.
3. Determine the enforcement action (Interrupt, RequestApproval).

OUTPUT REQUIREMENT:
Output PURE JSON matching this schema:

{schema}
"""

def render_runtime_system_instruction(agent_id: str, tools: Optional[List[str]]) -> str:
    """
    Generates the high-priority System Instruction for the Runtime Loop (Flow B).
    Uses MANUAL PARSING MODE with json:function blocks.
    """
    instruction = f"""
SYSTEM IDENTITY:
You are Agent '{agent_id}', an autonomous execution node within the RARO Kernel.
You are running in a headless environment. Your outputs are consumed programmatically.

OPERATIONAL CONSTRAINTS:
1. NO CHAT: Do not output conversational filler.
2. DIRECT ACTION: If the user request implies an action, use a tool immediately.
3. FAIL FAST: If you cannot complete the task, return a clear error.
"""

    if tools:
        tool_schemas = get_tool_definitions_for_prompt(tools)

        instruction += f"""
[SYSTEM CAPABILITY: TOOL USE]
You have access to the following tools. 
To use a tool, you MUST output a specific Markdown code block. 
DO NOT use native function calling mechanisms.

AVAILABLE TOOLS (Reference):
{tool_schemas}

[CRITICAL PROTOCOL: MANUAL CALLING]
The system does not support native function calling. 
You must MANUALLY type the tool call using the `json:function` tag.

CORRECT FORMAT:
```json:function
{{
  "name": "tool_name",
  "args": {{
    "parameter_name": "value"
  }}
}}
```

[ONE-SHOT EXAMPLE]
User: "Calculate 25 * 4 using python"
Assistant:
```json:function
{{
  "name": "execute_python",
  "args": {{
    "code": "print(25 * 4)"
  }}
}}
```

INCORRECT FORMATS (FORBIDDEN):
- No standard ```json``` blocks.
- No ```python``` blocks for code execution.
- No native tool objects.
"""

        # Specific guidance for Python
        if "execute_python" in tools:
            instruction += """
[TOOL NOTE: execute_python]
You have a secure Python sandbox.
To run code, you MUST use the `execute_python` tool.
Do NOT output ```python ... ``` text blocks; the system ignores them.
[TOOL NOTE: execute_python vs read_file]
- Use `read_file` for: Inspecting file contents, checking headers, or reading small logs. It is fast and free.
- Use `execute_python` for: Heavy data transformation, math, creating charts/images, or processing large files.
  NOTE: Files created by previous agents are automatically available in your Python environment.
"""
    else:
        instruction += "\nNOTE: You have NO tools available. Provide analysis based solely on the provided context.\n"

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
