# Patch Testing Suite
**Purpose:** Validate Patches 1, 2, 3 using agent response probe

---

## TEST 1: Nested Code Block Parser (Patch 1)

### Test Case 1A: Simple Nested Markdown
**Trigger:** Agent writes a file containing markdown code blocks

```
I'll create a documentation file for you.

```json:function
{
  "name": "write_file",
  "args": {
    "filename": "api_guide.md",
    "content": "# API Guide\n\nExample request:\n\n```json\n{\n  \"endpoint\": \"/api/users\",\n  \"method\": \"POST\"\n}\n```\n\nThis should work!"
  }
}
```
```

**Expected:** Parser correctly extracts the outer JSON block, ignores inner markdown fence

---

### Test Case 1B: Multiple Nested Levels
**Trigger:** Agent writes markdown containing code examples

```
Creating a developer guide with multiple examples.

```json:function
{
  "name": "write_file",
  "args": {
    "filename": "delegation_schema.md",
    "content": "# Delegation Schema\n\n## Format\n\n```json:delegation\n{\n  \"agent_id\": \"researcher\",\n  \"task\": \"Find data\"\n}\n```\n\n## Tool Format\n\n```json:function\n{\n  \"name\": \"web_search\",\n  \"args\": {\"query\": \"test\"}\n}\n```"
  }
}
```
```

**Expected:** Parser handles triple-nested fences correctly

---

### Test Case 1C: Edge Case - Unbalanced Braces in String
**Trigger:** JSON with literal braces in string values

```
```json:function
{
  "name": "execute_python",
  "args": {
    "code": "data = {'key': 'value with } brace'}\nprint(data)"
  }
}
```
```

**Expected:** Brace balancer respects string boundaries, doesn't break on `}` inside quotes

---

## TEST 2: Reasoning Display (Patch 2)

### Test Case 2A: Reasoning Before Tool Call
**Trigger:** Agent thinks before acting

```
I need to search for recent information about quantum computing advances. Let me query the web for the latest research papers.

```json:function
{
  "name": "web_search",
  "args": {
    "query": "quantum computing breakthroughs 2026"
  }
}
```
```

**Expected:**
- Reasoning text appears in UI with `category=REASONING`
- Narrative ticker shows "Reasoning about next steps..."

---

### Test Case 2B: Multi-Tool with Reasoning
**Trigger:** Complex reasoning between multiple tools

```
First, I'll search for the company's financial data, then analyze it with Python.

```json:function
{
  "name": "web_search",
  "args": {
    "query": "Acme Corp Q4 2025 earnings"
  }
}
```

Now I'll process the data to calculate growth metrics.

```json:function
{
  "name": "execute_python",
  "args": {
    "code": "revenue_growth = (450000 - 380000) / 380000 * 100\nprint(f'Growth: {revenue_growth:.2f}%')"
  }
}
```
```

**Expected:** Both reasoning blocks appear separately in UI before their respective tool calls

---

### Test Case 2C: No Reasoning (Tool Only)
**Trigger:** Direct tool call without preamble

```
```json:function
{
  "name": "list_files",
  "args": {}
}
```
```

**Expected:** No REASONING log entry, only TOOL_CALL

---

## TEST 3: Context Attachment Card (Patch 3)

### Test Case 3A: Web Search Results
**Trigger:** Response with automated context attachment

```
Based on my research, quantum computing has made significant progress in error correction.

[AUTOMATED CONTEXT ATTACHMENT]
--- web_search results ---
{
  "result": "[{\"url\": \"https://nature.com/quantum-2026\", \"content\": \"Researchers at MIT demonstrated a 99.9% error correction rate using topological qubits, marking a breakthrough in quantum stability.\"}, {\"url\": \"https://science.org/quantum-advance\", \"content\": \"Google's Willow chip achieved quantum advantage in protein folding simulations, reducing computation time from weeks to hours.\"}]"
}
```

**Expected:**
- Main text appears in SmartText
- Collapsible card labeled "AUTOMATED_CONTEXT"
- Badge shows "2 SOURCES"
- Grid displays clickable cards: `nature.com` and `science.org`
- Snippets truncated at 150 chars

---

### Test Case 3B: Multiple Tool Results
**Trigger:** Multiple tools with context

```
I've gathered the data and processed it.

[AUTOMATED CONTEXT ATTACHMENT]
--- web_search results ---
{
  "result": "[{\"url\": \"https://data.gov/gdp\", \"content\": \"US GDP grew 2.8% in Q4 2025\"}]"
}
--- read_file results ---
{
  "result": "Sales Data:\nQ1: $1.2M\nQ2: $1.5M\nQ3: $1.8M\nQ4: $2.1M"
}
```

**Expected:**
- Card shows "2 SOURCES"
- Section 1: "WEB_SEARCH DATA" with grid
- Section 2: "READ_FILE DATA" with pre-formatted text

---

### Test Case 3C: Malformed Context (Fallback Test)
**Trigger:** Unparseable context attachment

```
Here are the results.

[AUTOMATED CONTEXT ATTACHMENT]
This is not valid JSON format
Just plain text that can't be parsed
```

**Expected:**
- Fallback card displays with ⚠️ icon
- Label: "RAW_CONTEXT"
- Content shown in `<pre>` block (truncated at 500 chars)
- Copy button works

---

### Test Case 3D: Context with Generated Files
**Trigger:** Combined context + artifacts

```
I've created a visualization based on the web data.

[SYSTEM: Generated Image saved to 'chart_20260131_142530.png']

[AUTOMATED CONTEXT ATTACHMENT]
--- web_search results ---
{
  "result": "[{\"url\": \"https://data.source/api\", \"content\": \"Revenue data for analysis\"}]"
}
```

**Expected:**
- Main message cleaned (no system tag)
- Context card appears
- Artifact card appears separately below
- All three sections render independently

---

## COMBINED TEST: All Patches at Once

### Test Case 4: Full Integration
**Trigger:** Reasoning + Nested Parser + Context in one response

```
I need to create a comprehensive analysis. First, let me search for market data.

```json:function
{
  "name": "web_search",
  "args": {
    "query": "tech market trends 2026"
  }
}
```

Now I'll document the findings in a structured format.

```json:function
{
  "name": "write_file",
  "args": {
    "filename": "market_analysis.md",
    "content": "# Market Analysis\n\n## Data Sources\n\n```json\n{\n  \"primary\": \"Bloomberg\",\n  \"secondary\": \"Reuters\"\n}\n```\n\n## Key Findings\n- AI chips up 45%\n- Cloud revenue stable"
  }
}
```

Based on the data, the market shows strong growth in AI infrastructure.

[AUTOMATED CONTEXT ATTACHMENT]
--- web_search results ---
{
  "result": "[{\"url\": \"https://bloomberg.com/tech-2026\", \"content\": \"AI semiconductor market reached $180B in 2025, projected 45% growth for 2026 driven by enterprise adoption.\"}]"
}
```

**Expected:**
1. ✅ First reasoning block: "I need to create..."
2. ✅ Tool call: web_search
3. ✅ Second reasoning block: "Now I'll document..."
4. ✅ Tool call: write_file (nested JSON parsed correctly)
5. ✅ Final response: "Based on the data..."
6. ✅ Context card with Bloomberg source
7. ✅ No parser errors from nested markdown in file content

---

## Success Criteria

| Patch | Test | Pass Condition |
|-------|------|----------------|
| **1** | 1A-1C | No JSON parse errors in logs |
| **1** | 1B | Nested markdown doesn't break extraction |
| **1** | 1C | Braces in strings handled correctly |
| **2** | 2A-2B | REASONING logs appear in UI |
| **2** | 2A | Narrative ticker shows "Reasoning..." |
| **2** | 2C | Tool-only calls don't create empty reasoning |
| **3** | 3A | Web results render as grid cards |
| **3** | 3B | Multiple sections collapse independently |
| **3** | 3C | Fallback displays raw data cleanly |
| **3** | 3D | Context + artifacts don't conflict |
| **All** | 4 | Full integration works end-to-end |

---

## Quick Validation Commands

```bash
# Check parser logs for errors
grep -i "parse.*error\|unbalanced\|failed" <agent-service-logs>

# Verify REASONING category in frontend
grep "category.*REASONING" apps/web-console/src/components/OutputPane.svelte

# Check if ContextAttachmentCard is imported
grep "ContextAttachmentCard" apps/web-console/src/components/OutputPane.svelte
```

---

## Notes
- Use agent probe/mock system to inject these responses
- Test in order: 1 → 2 → 3 → 4
- Check browser console for JavaScript errors
- Verify Redis keys for file sync: `redis-cli KEYS "raro:e2b_files:*"`
