# [[RARO]]/apps/agent-service/src/intelligence/prompts.py

import json
from typing import Optional, List
from domain.protocol import WorkflowManifest, DelegationRequest, PatternDefinition
try:
    from intelligence.tools import get_tool_definitions_for_prompt
except ImportError:
    get_tool_definitions_for_prompt = lambda x: "[]"

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
    return f"""
ROLE: System Architect
GOAL: Design a multi-agent Directed Acyclic Graph (DAG) to solve the user's request.

USER REQUEST: "{user_query}"

INSTRUCTIONS:
1. Break the request into atomic steps.
2. For each agent, you must use one of these STRUCTURAL ROLES:
   - 'worker': For standard tasks (Research, Analysis, Coding).
   - 'orchestrator': Only for complex sub-management.
   - 'observer': For monitoring/logging.
3. Use the 'id' field to define the functional role (e.g., 'web_researcher', 'data_analyst').
4. Define dependencies (e.g., 'data_analyst' depends_on ['web_researcher']).
5. Select model: 'gemini-2.5-flash' (speed) or 'gemini-2.5-flash-lite' (reasoning).
6. TOOL ASSIGNMENT RULES (CRITICAL):
   Available Tools: ['execute_python', 'web_search', 'read_file', 'write_file', 'list_files']

   ASSIGNMENT GUIDELINES:
   - 'execute_python': REQUIRED for ANY agent that needs to:
     * Create files (images, graphs, PDFs, CSV, JSON)
     * Perform calculations or data analysis
     * Process or transform data
     * Generate visualizations
     When in doubt, INCLUDE this tool - it's the most versatile.

   - 'web_search': REQUIRED for agents that need:
     * Real-time information or current events
     * Fact verification
     * Research from the internet

   - 'read_file', 'write_file', 'list_files':
     * Baseline tools are auto-assigned by the system
     * You CAN explicitly include them, but it's optional

   - IMPORTANT: Be GENEROUS with tool assignments. If an agent MIGHT need a tool, assign it.
     Better to over-assign than under-assign (prevents UNEXPECTED_TOOL_CALL errors).

7. PROMPT CONSTRUCTION:
   - For agents with 'execute_python', write prompts like: "Write and EXECUTE Python code to..."
   - Do NOT ask agents to "output code" or "describe the approach"
   - Ask for RESULTS, not explanations

8. STRICT OUTPUT PROTOCOL:
   - Agents MUST NOT output Python code in Markdown blocks (```python).
   - Agents MUST use the 'execute_python' tool for all logic.
   - The pipeline relies on the *Tool Result* to pass data to the next agent. Markdown text is ignored by the compiler.

OUTPUT REQUIREMENT:
You must output PURE JSON matching this schema:
{schema}

IMPORTANT: The 'role' field MUST be exactly 'worker', 'orchestrator', or 'observer'.
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
You are authorized to modify the workflow graph if the current plan is insufficient.
You can ADD new agents or UPDATE existing future agents.

To edit the graph, output a JSON object wrapped in `json:delegation`.

EDITING RULES:
1. **ADD A NEW STEP**:
   - Create a node with a **NEW, UNIQUE ID**.
   - It will be inserted into the graph.

2. **UPDATE A PENDING STEP**:
   - Create a node using the **SAME ID** as an existing [PENDING] node in your context.
   - The system will **OVERWRITE** the old node's instructions and dependencies with your new definition.
   - Use this to refine future steps based on your current findings (e.g., changing a generic 'analyst' to a specific 'python_data_processor').

Example Format:
```json:delegation
{schema}
```

The system will pause your execution, apply these changes, and then resume.
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

    return instruction
