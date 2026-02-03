# Parser Fix Analysis - Root Cause & Solution

## Issue Summary

**Symptom:** `write_file` tool calls with markdown content were not being parsed, instead rendering as code blocks in the UI.

**Root Cause:** Over-aggressive preprocessing was converting ALL `\n` escape sequences to actual newlines, including those inside JSON string values, which broke JSON parsing.

---

## Test Results Analysis

### ✅ Passing Tests (Before Fix)
```python
# TEST 1: Minimal - No newlines in JSON strings
{"name": "web_search", "args": {"query": "test"}}

# TEST 4: Literal escapes - LLM format with no content newlines
```json:function\n{\n  "name": "web_search"...
(Converts to valid JSON with no embedded newlines)

# TEST 5: Integration - Clean JSON
{"name": "web_search", "args": {"query": "quantum computing"}}
```

### ❌ Failing Tests (Before Fix)
```python
# TEST 2: Write file with \n in content string
{
  "name": "write_file",
  "args": {
    "filename": "test.md",
    "content": "# Hello World\nThis is a test."  # ← \n here
  }
}

# After preprocessing: "content": "# Hello World
# This is a test."  ← INVALID JSON! (literal newline)

# Error: Invalid control character at: line 5 column 30
```

---

## The Core Problem

### Original Preprocessing Logic
```python
# TOO AGGRESSIVE - Breaks JSON string values
if '\\n' in text:
    text = text.replace('\\n', '\n')  # Converts EVERYWHERE
```

### What Happened
1. LLM returns: `\`\`\`json:function\n{\n  "content": "Hello\\nWorld"\n}\n\`\`\``
2. Preprocessing converts: `\`\`\`json:function\n{\n  "content": "Hello\nWorld"\n}\n\`\`\``
3. After balanced brace extraction, JSON is:
   ```json
   {
     "content": "Hello
   World"
   }
   ```
4. JSON parser sees literal newline inside string → **INVALID**

---

## The Fix: Selective Preprocessing

### New Logic
**Only convert `\n` between fence marker and opening brace, preserve JSON content:**

```python
def fix_fence_newlines(match):
    """Convert \n to actual newline only in fence header"""
    full_match = match.group(0)
    json_start = full_match.find('{')

    # Split at first brace
    header = full_match[:json_start].replace('\\n', '\n')  # Fix header only
    body = full_match[json_start:]                          # Preserve JSON
    return header + body

# Apply only to fence markers
fence_pattern = r'```json:[a-zA-Z0-9_-]+[^\{]*'
text = re.sub(fence_pattern, fix_fence_newlines, text)
```

### Result
```
# Input:
```json:function\n{\n  "content": "Hello\\nWorld"\n}

# After preprocessing:
```json:function
{
  "content": "Hello\\nWorld"  # ← \n preserved in JSON string!
}

# Valid JSON! ✅
```

---

## Expected Test Results (After Fix)

| Test | Before | After | Description |
|------|--------|-------|-------------|
| 1. Minimal | ✅ PASS | ✅ PASS | Simple query, no newlines |
| 2. Simple write | ❌ FAIL | ✅ PASS | Content with `\n` preserved |
| 3. Nested markdown | ❌ FAIL | ✅ PASS | Nested code blocks work |
| 4. Literal escapes | ✅ PASS | ✅ PASS | LLM format handled |
| 5. Full integration | ✅ PASS | ✅ PASS | End-to-end parsing |
| 6. Market strategy | ❌ FAIL | ✅ PASS | Real-world case fixed |

---

## Verification Steps

1. **Rebuild container** with updated parser
2. **Run test suite:**
   ```bash
   docker exec -it agent-service python /app/tests/parser_debug_test.py
   ```
3. **Expected output:**
   ```
   TEST 2: Found 1 blocks  # ← Should now pass
   TEST 3: Found 1 blocks  # ← Should now pass
   TEST 6: Found 1 blocks  # ← Should now pass
   ```
4. **Test in UI** with actual agent response
5. **Check logs** for no "loose recovery" warnings

---

## Why This Approach

### Alternative Approaches Considered

**❌ Remove preprocessing entirely**
- Doesn't solve literal `\n` after fence markers (TEST 4 would fail)

**❌ JSON-aware string parsing**
- Overly complex, error-prone
- Need to implement full JSON string lexer

**✅ Selective preprocessing** (Chosen)
- Surgical fix targeting exact issue
- Preserves JSON content integrity
- Handles both literal escapes and proper formatting
- Minimal performance impact

---

## File Modified

- `apps/agent-service/src/core/parsers.py` - Line ~124-145
- Added `fix_fence_newlines()` function
- Updated preprocessing logic with regex-based selective replacement

---

## Next Steps

1. Rebuild agent-service container
2. Run `parser_debug_test.py`
3. Verify all 6 tests pass
4. Test with real agent workflow
5. Monitor logs for JSON parse errors
