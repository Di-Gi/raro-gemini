# Puppet Mode Fix: Preventing LLM Leakage After Tool Execution

## Problem Summary

When a puppet mock containing tool calls was injected, the system would:
1. ✅ Use the mock on Turn 1
2. ✅ Execute the tools from the mock
3. ❌ **Call the real LLM on Turn 2** with tool results
4. ❌ LLM autonomously continues execution

This caused "LLM leakage" where deterministic testing was compromised by autonomous LLM behavior.

## Root Cause

The puppet interceptor logic in `apps/agent-service/src/core/llm.py` only checked:

```python
if mock_data and turn_count == 1:
    # Use mock
else:
    # Call real Gemini
```

**The Issue:** After tool execution, the loop continued to Turn 2, where `turn_count == 2`, causing the condition to fail and the system to fall back to the real LLM.

## The Fix

### Changes Made

**File:** `apps/agent-service/src/core/llm.py`

1. **Added puppet tracking flag** (line ~510):
```python
puppet_turn_used = False  # Track if current turn used puppet mock
```

2. **Set flag when puppet is used** (line ~522):
```python
if mock_data and turn_count == 1:
    content_text = mock_data["content"]
    puppet_turn_used = True  # Mark this turn as puppet-controlled
    logger.info(f">> 🎭 INJECTING MOCK PAYLOAD (turn {turn_count}) <<")
    response = None
else:
    puppet_turn_used = False  # This is a real LLM turn
```

3. **Terminate after tool execution** (after line ~656):
```python
# 5. Append Tool Outputs to History
current_contents.append({
    "role": "user",
    "parts": [{"text": tool_outputs_text}]
})

# === PUPPET FIX: TERMINATE AFTER TOOL EXECUTION ===
# If this turn was a puppet mock with tool calls, treat the tool execution
# as the final response. Don't loop back to call the real LLM.
if puppet_turn_used:
    logger.info(f"🎭 PUPPET MODE: Tool execution complete. Terminating agent (no LLM follow-up).")
    # Set final_response_text to the original mock text (includes reasoning + tool call)
    final_response_text = content_text
    break  # Exit loop - don't call real LLM with tool results
# ===================================================

logger.debug(f"Agent {agent_id}: Turning over with tool results...")
```

## Behavior Now

### Before Fix
```
Turn 1: Mock injected → "```json:function { write_file... }```"
  ├─ Parse tools ✓
  ├─ Execute write_file ✓
  └─ Loop continues...
Turn 2: Call REAL GEMINI with tool results ❌
  ├─ Gemini sees file was written
  ├─ Gemini autonomously runs list_files ❌
  ├─ Gemini autonomously runs read_file ❌
  └─ Gemini generates summary ❌
```

### After Fix
```
Turn 1: Mock injected → "```json:function { write_file... }```"
  ├─ Parse tools ✓
  ├─ Execute write_file ✓
  └─ TERMINATE (break loop) ✓
Final Response: Original mock text + tool execution complete
No Turn 2, no LLM leakage ✓
```

## Testing the Fix

### Test Case 1: Mock with Tool Call
**Inject:**
```
Creating test file.
```json:function
{
  "name": "write_file",
  "args": {
    "filename": "test.txt",
    "content": "Hello World"
  }
}
\```
```

**Expected:**
- ✅ write_file executes
- ✅ Agent terminates
- ✅ NO follow-up LLM calls
- ✅ Final response is the injected mock text

### Test Case 2: Mock without Tool Call
**Inject:**
```
Task completed successfully.
All requirements met.
```

**Expected:**
- ✅ Agent returns mock text
- ✅ Agent terminates
- ✅ NO tool execution
- ✅ NO follow-up LLM calls

### Test Case 3: Delegation Test
**Inject:**
```
I need help with this task.
```json:delegation
{
  "reason": "Testing delegation",
  "strategy": "child",
  "new_nodes": [...]
}
\```
```

**Expected:**
- ✅ Delegation processed by Kernel
- ✅ Graph modified
- ✅ Agent terminates
- ✅ NO follow-up LLM calls

## Impact

✅ **Deterministic Testing** - Puppet mocks now have complete control
✅ **No LLM Leakage** - Real LLM never sees tool results from mocks
✅ **Single-Shot Execution** - Mocks execute exactly once and terminate
✅ **Predictable Behavior** - Tool execution doesn't trigger autonomous LLM
✅ **Faster Tests** - No unnecessary LLM calls after mock tool execution

## Limitations

- **Single-shot only**: Puppet mocks cannot currently span multiple turns
- **No mixed mode**: Can't have a puppet mock call tools and then ask LLM to continue
- If you need multi-turn puppet behavior, you would need to inject separate mocks for each turn (not currently supported)

These limitations are acceptable for the current use case: deterministic testing of individual agent behaviors and tool execution flows.

## Files Modified

1. `apps/agent-service/src/core/llm.py` - Added puppet termination logic
2. `apps/debug-puppet/README.md` - Updated documentation to reflect single-shot behavior

## Logs to Verify

When puppet mode is working correctly, you should see:

```
agents | 🎭 PUPPETEER: Intercepted execution for code_interpreter
agents | >> 🎭 INJECTING MOCK PAYLOAD (turn 1) <<
agents | [TOOL DETECTED] Agent: code_interpreter | Tool: write_file
agents | Captured 1 file(s) from write_file: ['test.txt']
agents | 🎭 PUPPET MODE: Tool execution complete. Terminating agent (no LLM follow-up).
agents | Agent code_interpreter Completed.
```

**What you should NOT see after this fix:**
- `AFC is enabled` (indicates Gemini was called)
- Additional tool calls like `list_files` or `read_file` after mock tools
- Multiple turns after a puppet mock with tools

## Related Issues

This fix also resolves:
- Tool restriction warnings (puppet can now inject tools regardless of agent role)
- Autonomous agent behavior after mock injection
- Non-deterministic test results from LLM follow-up
