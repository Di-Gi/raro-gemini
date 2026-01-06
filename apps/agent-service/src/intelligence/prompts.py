# [[RARO]]/apps/agent-service/src/intelligence/prompts.py

import json
from typing import Optional, List
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

def render_runtime_system_instruction(agent_id: str, tools: Optional[List[str]]) -> str:
    """
    Generates the high-priority System Instruction for the Runtime Loop (Flow B).
    This establishes the agent's identity as a headless execution node, NOT a chatbot.
    """
    # 1. Base Identity
    instruction = f"""
SYSTEM IDENTITY:
You are Agent '{agent_id}', an autonomous execution node within the RARO Kernel.
You are running in a headless environment. You do NOT interact with a human user.
Your outputs are consumed programmatically by the Kernel and other agents.

OPERATIONAL CONSTRAINTS:
1. NO CHAT: Do not output conversational filler like "Here is the code", "I will now", or "Sure!".
2. DIRECT ACTION: If the user request implies an action (calculating, scraping, plotting), you MUST use a tool immediately.
3. FAIL FAST: If you cannot complete the task with available tools, return a clear error description.
4. TOOL USAGE: When you need to use a tool, call it immediately. Do not describe what you plan to do.
"""

    # 2. Tool-Specific Protocols
    if tools:
        instruction += "\nAVAILABLE TOOLS:\n"
        instruction += f"You have access to the following tools: {', '.join(tools)}\n"
        instruction += "\nTOOL PROTOCOLS:\n"

        # Specific strictness for Python execution to prevent "Lazy Markdown"
        if "execute_python" in tools:
            instruction += """
[TOOL: execute_python]
- CRITICAL: You have access to a secure Python sandbox with filesystem access.
- FORBIDDEN: Do NOT output Python code in Markdown blocks (```python ... ```). The system CANNOT execute text.
- MANDATORY: You MUST call the `execute_python` tool function to run code.
- DATA HANDLING: If generating artifacts (images, PDFs, plots), save them to the current working directory.
  The system will automatically detect and mount generated files for downstream agents.
- LIBRARIES: Common libraries are pre-installed (numpy, pandas, matplotlib, etc.).
- OUTPUT: Your final answer should reference the *result* of the execution, not the code itself.
- ERROR HANDLING: If execution fails, the error will be returned to you. Analyze it and retry with fixes.
"""

        if "web_search" in tools:
            instruction += """
[TOOL: web_search]
- Use this for factual verification, current events, or retrieving real-time data.
- The search returns AI-optimized context snippets.
- Synthesize search results into a concise, accurate summary.
- Always cite when presenting facts from search results.
"""

        if "read_file" in tools:
            instruction += """
[TOOL: read_file]
- Read files from the session workspace (input directory or output from previous agents).
- Files are automatically truncated if too large to prevent token overflow.
- Binary files will return an error; only text files can be read.
"""

        if "write_file" in tools:
            instruction += """
[TOOL: write_file]
- Write text content to files in the session workspace.
- Files written here are available to downstream agents in the workflow.
- For programmatic file generation (images, plots), use `execute_python` instead.
"""

        if "list_files" in tools:
            instruction += """
[TOOL: list_files]
- List all files available in your workspace (both input and output directories).
- Use this to discover what files are available before reading or processing.
"""
    else:
        instruction += "\nNOTE: You have NO tools available. Provide analysis based solely on the provided context.\n"

    return instruction