# Patch Testing - Quick Start

## Copy-Paste Test Responses

### 🔧 PATCH 1: Nested Parser Test
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
**✅ PASS IF:** No "parse error" or "unbalanced" in logs

---

### 🧠 PATCH 2: Reasoning Display Test
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
**✅ PASS IF:**
- UI shows reasoning text before tool call
- Narrative ticker shows "Reasoning about next steps..."
- Log category = `REASONING` (not `THOUGHT`)

---

### 📎 PATCH 3: Context Card Test
```
Based on my research, quantum computing has made significant progress in error correction.

[AUTOMATED CONTEXT ATTACHMENT]
--- web_search results ---
{
  "result": "[{\"url\": \"https://nature.com/quantum-2026\", \"content\": \"Researchers at MIT demonstrated a 99.9% error correction rate using topological qubits, marking a breakthrough in quantum stability.\"}, {\"url\": \"https://science.org/quantum-advance\", \"content\": \"Google's Willow chip achieved quantum advantage in protein folding simulations, reducing computation time from weeks to hours.\"}]"
}
```
**✅ PASS IF:**
- Collapsible card appears with "📎 AUTOMATED_CONTEXT"
- Badge shows "2 SOURCES"
- Grid displays: `nature.com` and `science.org`
- Links are clickable
- Copy button works

---

### 🚀 INTEGRATION TEST (All 3 Patches)
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

**✅ PASS IF:**
1. Two reasoning blocks appear
2. Nested JSON in `market_analysis.md` parsed correctly
3. Context card shows Bloomberg source
4. No errors in console or logs

---

## Fast Validation

```bash
# Backend health
tail -f logs/agent-service.log | grep -i "error\|warning"

# Frontend check
open http://localhost:5173
# Look for: REASONING logs, Context cards, no JS errors

# Redis verification
redis-cli KEYS "raro:e2b_files:*"
```

## Expected Behavior Summary

| Feature | Before | After |
|---------|--------|-------|
| Nested MD | ❌ Parse fails | ✅ Extracts correctly |
| Reasoning | ❌ Not shown | ✅ Displays in UI |
| Context | ❌ Messy JSON | ✅ Clean cards |

---

**Full docs:** See `patch-test-suite.md` for edge cases
