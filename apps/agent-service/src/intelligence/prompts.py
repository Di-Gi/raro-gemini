# [[RARO]]/apps/agent-service/src/intelligence/prompts.py

import json
from domain.protocol import WorkflowManifest, DelegationRequest, PatternDefinition

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

[SYSTEM CAPABILITY: DYNAMIC DELEGATION]
If the task is too complex, missing data, or requires sub-tasks:
You are authorized to spawn sub-agents.

To delegate, output a JSON object wrapped in a SPECIAL code block.
You MUST use the tag `json:delegation` for the system to recognize it.

Example Format:
```json:delegation
{schema}
```

The system will:
1. Pause your execution.
2. Run these new agents.
3. Return their results to you as context.
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
