Here are the fixes for the identified issues.

### 1. Major Fix: `parsers.py` (Nested Code Block Parsing)

The issue is that the standard non-greedy Regex (`.*?`) stops at the **first** closing triple backtick it finds. When an agent writes a file (like `market_strategy.md`) that *contains* markdown code blocks (e.g., describing a delegation schema), the parser cuts the JSON off prematurely at the inner code block's closing fence, resulting in invalid JSON.

We need a parser that identifies the **outermost** block.

**File:** `apps/agent-service/src/core/parsers.py`

```python
# [[RARO]]/apps/agent-service/src/core/parsers.py
# Purpose: Unified Parser Module for Markdown Code Block Extraction
# Architecture: Core Layer - Shared parsing utilities
# Dependencies: re, json, typing

import re
import json
from typing import Optional, List, Dict, Any, Tuple
from core.config import logger


class ParsedBlock:
    """
    Represents a parsed code block with its type and data.
    """
    def __init__(self, block_type: str, data: Dict[str, Any], raw_json: str):
        self.block_type = block_type
        self.data = data
        self.raw_json = raw_json

    def __repr__(self):
        return f"ParsedBlock(type={self.block_type}, keys={list(self.data.keys())})"


def _repair_json_string(json_str: str) -> str:
    """
    Attempts to repair common JSON errors made by LLMs.
    """
    # 1. Fix escaped newlines that were double-escaped or malformed
    # 2. Fix invalid escape sequences (e.g., '\d' in regex strings)
    pattern = r'\\(?![/u"\\bfnrt])'
    return re.sub(pattern, r'\\\\', json_str)


def _parse_with_repair(json_str: str, block_type: str) -> Optional[Dict[str, Any]]:
    """
    Helper to parse JSON with a fallback repair mechanism.
    """
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            logger.warning(f"Initial JSON parse failed for ```json:{block_type}```, attempting regex repair...")
            repaired_json = _repair_json_string(json_str)
            data = json.loads(repaired_json)
            logger.info(f"JSON repair successful for ```json:{block_type}```.")
            return data
        except json.JSONDecodeError as e:
            # Last ditch: Use a specialized library or heuristic if available, otherwise fail
            logger.error(f"Failed to parse ```json:{block_type}``` block: {e}")
            return None


def extract_all_code_blocks(text: str, block_type: str) -> List[ParsedBlock]:
    """
    Extract and parse ALL code blocks.
    
    CRITICAL FIX: Handles nested code blocks (e.g., writing a file that contains markdown).
    Instead of a simple regex which fails on nested '```', we find the start tag
    and then attempt to parse JSON up to the LAST '```' in the string, then backtrack
    until valid JSON is found.
    """
    blocks = []
    
    # Marker for the specific block type we want
    start_marker = f"```json:{block_type}"
    
    # Find all start indices
    start_indices = [m.start() for m in re.finditer(re.escape(start_marker), text, re.IGNORECASE)]
    
    for start_idx in start_indices:
        # The content starts after the marker + newline (heuristically)
        # We look for the first brace '{'
        json_start_search = text.find('{', start_idx)
        if json_start_search == -1:
            continue
            
        # We need to find the *correct* closing brace/fence. 
        # Since regex is unreliable with nested markdown, we use a candidate approach.
        # We take everything from the start brace to the very end of the string,
        # then we try to find the LAST occurrence of closing fence '```'.
        # If that fails to parse, we find the second to last, etc.
        
        substring = text[json_start_search:]
        
        # Find all closing fences
        closing_fences = [m.start() for m in re.finditer("```", substring)]
        
        # Try parsing from largest possible block downwards (Greedy approach)
        parsed_data = None
        valid_json_str = ""
        
        # Iterate backwards through possible closing points
        for fence_idx in reversed(closing_fences):
            candidate_json = substring[:fence_idx].strip()
            
            # Optimization: Check if it ends with '}'
            if not candidate_json.endswith('}'):
                continue
                
            data = _parse_with_repair(candidate_json, block_type)
            if data:
                parsed_data = data
                valid_json_str = candidate_json
                break # Found the valid outer block
        
        if parsed_data:
            blocks.append(ParsedBlock(block_type=block_type, data=parsed_data, raw_json=valid_json_str))
        else:
            logger.warning(f"Failed to extract valid JSON for {block_type} starting at index {start_idx}")

    # --- LOOSE PASS (Fallback for generic ```json) ---
    if not blocks and block_type == 'function':
        loose_marker = "```json"
        # Simple regex is usually fine for loose/simple blocks, but let's apply the same logic for consistency
        # or stick to regex for speed if complex nesting isn't expected in loose mode.
        # Stick to regex for loose pass to avoid over-complication.
        loose_pattern = r"```json\s*(\{[\s\S]*?\})\s*```"
        loose_matches = re.finditer(loose_pattern, text, re.IGNORECASE)
        
        for match in loose_matches:
            json_str = match.group(1)
            data = _parse_with_repair(json_str, block_type)
            # Heuristic check
            if data and isinstance(data, dict) and "name" in data and "args" in data:
                logger.info(f"Recovered valid tool call from loose JSON block: {data['name']}")
                blocks.append(ParsedBlock(block_type=block_type, data=data, raw_json=json_str))

    return blocks


# ============================================================================
# Specialized Parsers
# ============================================================================

def parse_delegation_request(text: str) -> Optional[Dict[str, Any]]:
    blocks = extract_all_code_blocks(text, 'delegation')
    return blocks[0].data if blocks else None


def parse_function_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    blocks = extract_all_code_blocks(text, 'function')
    
    function_calls = []
    for block in blocks:
        tool_name = block.data.get('name')
        tool_args = block.data.get('args', {})

        if not tool_name:
            continue

        function_calls.append((tool_name, tool_args))

    return function_calls
```

---

### 2. Fix: `llm.py` (Capture Reasoning)

We need to check if there is text surrounding the tool call and emit it as a `THOUGHT` event so the user sees the logic *before* the tool executes.

**File:** `apps/agent-service/src/core/llm.py`

```python
# ... (inside call_gemini_with_context function loop) ...

            # 3. Parse for Manual Function Calls
            function_calls = parse_function_calls(content_text)

            if not function_calls:
                # No tools called, this is the final answer
                final_response_text = content_text
                break

            # === FIX: Emit Reasoning / Pre-computation ===
            # Regex to strip out the tool call block, leaving only the "thought" text.
            # We remove the ```json:function ... ``` block.
            # Use non-greedy match including newlines
            reasoning_text = re.sub(r"```json:function[\s\S]*?```", "", content_text).strip()
            
            if reasoning_text:
                # Send to UI via Redis
                emit_telemetry(
                    run_id=run_id,
                    agent_id=safe_agent_id,
                    category="THOUGHT", 
                    message=reasoning_text,
                    meta="PLANNING"
                )
                logger.info(f"Agent {agent_id} thought: {reasoning_text[:50]}...")

            # 4. Process Tool Calls
            tool_outputs_text = ""
            
            for tool_name, tool_args in function_calls:
                # ... (rest of tool execution logic)
```

---
### 3. The `[AUTOMATED CONTEXT ATTACHMENT]` block is massive, unstructured text injected by `llm.py`, and when `SmartText` tries to render it via Markdown, it creates a mess of broken JSON and escaped characters.

We need to **slice** this block off the main message before it hits the Markdown renderer and pass it to a dedicated visualization component.

Here is the solution:

1.  **New Component**: `ContextAttachmentCard.svelte` to parse and render Web Search results (and other tool context) beautifully.
2.  **Update**: `OutputPane.svelte` to extract this block separately.

### 1. New Component: `ContextAttachmentCard.svelte`

This card parses the specific format coming from `llm.py` (`--- tool results --- \n {json}`) and renders it as a structured list of sources.

**File:** `apps/web-console/src/components/sub/ContextAttachmentCard.svelte`

```svelte
<!-- [[RARO]]/apps/web-console/src/components/sub/ContextAttachmentCard.svelte -->
<!-- Purpose: Special renderer for the [AUTOMATED CONTEXT ATTACHMENT] block -->
<script lang="ts">
  import { slide } from 'svelte/transition';

  let { rawData }: { rawData: string } = $props();

  let isExpanded = $state(false);

  // Parse the raw text dump into structured data
  let parsedContent = $derived.by(() => {
    const sections: Array<{ type: string, items: any[] }> = [];
    
    // Regex to split multiple tool outputs if present
    // Format: "--- tool_name results ---\n{json}"
    const pattern = /--- (.*?) results ---\n([\s\S]*?)(?=\n---|$)/g;
    
    let match;
    while ((match = pattern.exec(rawData)) !== null) {
        const toolName = match[1];
        const jsonStr = match[2];
        
        try {
            const data = JSON.parse(jsonStr);
            
            // Special handling for Web Search results (Tavily format)
            if (toolName === 'web_search' && data.result) {
                // Tavily often returns 'result' as a stringified JSON string
                let results = [];
                if (typeof data.result === 'string') {
                    try { results = JSON.parse(data.result); } catch (e) { results = [{ content: data.result }]; }
                } else if (Array.isArray(data.result)) {
                    results = data.result;
                }

                sections.push({
                    type: 'WEB_SEARCH',
                    items: results.map((r: any) => ({
                        title: extractDomain(r.url),
                        url: r.url,
                        content: r.content || r.body
                    }))
                });
            }
            // Fallback for generic tools (read_file, etc)
            else {
                sections.push({
                    type: toolName.toUpperCase(),
                    items: [{ content: typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2) }]
                });
            }
        } catch (e) {
            console.warn("Failed to parse context attachment", e);
        }
    }
    return sections;
  });

  function extractDomain(url: string): string {
      if (!url) return 'Unknown Source';
      try {
          const hostname = new URL(url).hostname;
          return hostname.replace('www.', '');
      } catch (e) { return 'External Link'; }
  }

  function toggle() { isExpanded = !isExpanded; }
</script>

{#if parsedContent.length > 0}
    <div class="ctx-card">
        <!-- HEADER -->
        <button class="ctx-header" onclick={toggle}>
            <div class="header-left">
                <span class="icon">📎</span>
                <span class="label">AUTOMATED_CONTEXT</span>
                <span class="count-badge">{parsedContent.length} SOURCE{parsedContent.length > 1 ? 'S' : ''}</span>
            </div>
            <div class="chevron {isExpanded ? 'up' : 'down'}">▼</div>
        </button>

        <!-- BODY -->
        {#if isExpanded}
            <div class="ctx-body" transition:slide={{ duration: 200 }}>
                {#each parsedContent as section}
                    <div class="section-group">
                        <div class="section-title">{section.type} DATA</div>
                        
                        {#if section.type === 'WEB_SEARCH'}
                            <div class="search-grid">
                                {#each section.items as item}
                                    <a href={item.url} target="_blank" rel="noopener noreferrer" class="search-result">
                                        <div class="res-domain">{item.title}</div>
                                        <div class="res-snippet">{item.content.slice(0, 150)}...</div>
                                    </a>
                                {/each}
                            </div>
                        {:else}
                            <!-- Generic Pre Block for other tools -->
                            {#each section.items as item}
                                <div class="generic-block">
                                    <pre>{item.content.slice(0, 300)}{item.content.length > 300 ? '...' : ''}</pre>
                                </div>
                            {/each}
                        {/if}
                    </div>
                {/each}
            </div>
        {/if}
    </div>
{/if}

<style>
    .ctx-card {
        margin-top: 12px;
        background: var(--paper-surface);
        border: 1px dashed var(--paper-line);
        border-radius: 2px;
        font-family: var(--font-code);
        overflow: hidden;
    }

    .ctx-header {
        width: 100%;
        display: flex; justify-content: space-between; align-items: center;
        padding: 8px 12px;
        background: transparent; border: none; cursor: pointer;
        transition: background 0.2s;
    }
    .ctx-header:hover { background: color-mix(in srgb, var(--paper-ink), transparent 95%); }

    .header-left { display: flex; align-items: center; gap: 8px; }
    .icon { font-size: 12px; }
    .label { font-size: 10px; font-weight: 700; color: var(--paper-line); letter-spacing: 0.5px; }
    .count-badge { font-size: 9px; background: var(--paper-line); color: var(--paper-bg); padding: 1px 4px; border-radius: 2px; }

    .chevron { font-size: 8px; color: var(--paper-line); transition: transform 0.2s; }
    .chevron.up { transform: rotate(180deg); }

    .ctx-body {
        border-top: 1px dashed var(--paper-line);
        padding: 12px;
        background: var(--paper-bg);
    }

    .section-group { margin-bottom: 12px; }
    .section-group:last-child { margin-bottom: 0; }

    .section-title {
        font-size: 9px; font-weight: 700; color: var(--paper-ink); 
        margin-bottom: 8px; opacity: 0.7;
    }

    /* WEB SEARCH STYLES */
    .search-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
    
    .search-result {
        display: block; text-decoration: none;
        background: var(--paper-surface);
        border: 1px solid var(--paper-line);
        padding: 8px; border-radius: 2px;
        transition: border-color 0.2s;
    }
    .search-result:hover { border-color: var(--arctic-cyan); }

    .res-domain { font-size: 10px; font-weight: 700; color: var(--arctic-cyan); margin-bottom: 4px; }
    .res-snippet { font-size: 9px; color: var(--paper-ink); line-height: 1.4; opacity: 0.8; }

    /* GENERIC STYLES */
    .generic-block pre {
        margin: 0; font-size: 10px; color: var(--paper-line);
        white-space: pre-wrap; word-break: break-all;
    }
</style>
```

### 2. Updated: `OutputPane.svelte`

We modify the logic to identify the `[AUTOMATED CONTEXT ATTACHMENT]` block, extract it, and pass it to the new component instead of `SmartText`.

**File:** `apps/web-console/src/components/OutputPane.svelte`

```svelte
<!-- [[RARO]]/apps/web-console/src/components/OutputPane.svelte -->
<script lang="ts">
  import { logs, updateLog, runtimeStore, type LogEntry } from '$lib/stores'
  import Typewriter from './sub/Typewriter.svelte'
  import SmartText from './sub/SmartText.svelte'
  import ApprovalCard from './sub/ApprovalCard.svelte'
  import ArtifactCard from './sub/ArtifactCard.svelte'
  import ToolExecutionCard from './sub/ToolExecutionCard.svelte'
  import ContextAttachmentCard from './sub/ContextAttachmentCard.svelte' // [[NEW]]
  import { tick } from 'svelte';

  // ... (Standard logic for grouping and scrolling remains unchanged) ...

  let scrollContainer = $state<HTMLDivElement | null>(null);
  let contentWrapper = $state<HTMLDivElement | null>(null);
  let isPinnedToBottom = $state(true);
  let isAutoScrolling = false;

  let groupedLogs = $derived.by(() => {
    // ... (Grouping logic unchanged) ...
    const rawLogs = $logs;
    const groups: { id: string, role: string, items: LogEntry[] }[] = [];
    if (rawLogs.length === 0) return [];
    let currentGroup = { id: rawLogs[0].id, role: rawLogs[0].role, items: [rawLogs[0]] };
    for (let i = 1; i < rawLogs.length; i++) {
      const log = rawLogs[i];
      if (log.role === currentGroup.role) {
        currentGroup.items.push(log);
      } else {
        groups.push(currentGroup);
        currentGroup = { id: log.id, role: log.role, items: [log] };
      }
    }
    groups.push(currentGroup);
    return groups;
  });

  // ... (Scroll logic unchanged) ...
  function handleScroll() {
    if (!scrollContainer) return;
    if (isAutoScrolling) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
    const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
    isPinnedToBottom = distanceFromBottom < 50;
  }

  function scrollToBottom(behavior: ScrollBehavior = 'auto') {
    if (!scrollContainer) return;
    isAutoScrolling = true;
    try {
      scrollContainer.scrollTo({ top: scrollContainer.scrollHeight, behavior });
    } finally {
      requestAnimationFrame(() => { isAutoScrolling = false; });
    }
  }

  $effect(() => {
    if (!contentWrapper) return;
    const observer = new ResizeObserver(() => {
      if (isPinnedToBottom) scrollToBottom('auto'); 
    });
    observer.observe(contentWrapper);
    return () => observer.disconnect();
  });

  $effect(() => {
    const _l = $logs;
    tick().then(() => {
      if (isPinnedToBottom) scrollToBottom('auto');
    });
  });

  function handleTypewriterComplete(id: string) {
    updateLog(id, { isAnimated: false });
  }

  // ... (Artifact extraction logic unchanged) ...
  function extractAllFilenames(msg: string): string[] {
      const files: string[] = [];
      const systemRegex = /\[\s*SYSTEM\s*:\s*Generated\s*(?:Image|File)\s*saved\s*to\s*['"]([^'"]+)['"]\s*\]/gi;
      let match;
      while ((match = systemRegex.exec(msg)) !== null) files.push(match[1]);
      
      const mdRegex = /!\[.*?\]\(([^)]+\.(?:png|jpg|jpeg|svg|json|csv|txt))\)/gi;
      while ((match = mdRegex.exec(msg)) !== null) {
          if (!files.includes(match[1])) files.push(match[1]);
      }
      return files;
  }

  // === UPDATED: EXTRACTOR FUNCTION ===
  // Splitting the message into [Main Body] and [Attachment Data]
  function processMessageContent(msg: string) {
      if (!msg) return { body: '', attachment: null };

      const ATTACHMENT_HEADER = "\n\n[AUTOMATED CONTEXT ATTACHMENT]";
      const splitIndex = msg.indexOf(ATTACHMENT_HEADER);

      let body = msg;
      let attachment = null;

      if (splitIndex !== -1) {
          // 1. Extract Body (Everything before the tag)
          body = msg.substring(0, splitIndex);
          // 2. Extract Attachment (Everything after the tag)
          // + length of header to skip the tag itself
          attachment = msg.substring(splitIndex + ATTACHMENT_HEADER.length).trim();
      }

      // Further clean the body (Standard System tags)
      body = body.replace(/!\[.*?\]\(([^)]+)\)/gi, '');
      body = body.replace(/\[\s*SYSTEM\s*:\s*Generated\s*(?:Image|File)\s*saved\s*to\s*['"]([^'"]+)['"]\s*\]/gi, '');

      return { body: body.trim(), attachment };
  }

  // ... (Narrative translation logic unchanged) ...
  function translateLogToNarrative(log: LogEntry): string {
    if (log.category === 'TOOL_CALL') {
        if (log.message.includes('execute_python')) return "Running Python analysis on data...";
        if (log.message.includes('web_search')) return `Searching the internet for information...`;
        if (log.message.includes('read_file')) return "Reading file contents...";
        if (log.message.includes('write_file')) return "Generating output file...";
        return "Executing tool...";
    }
    if (log.category === 'THOUGHT') return "Reasoning about next steps...";
    if (log.role === 'ORCHESTRATOR' || log.role === 'PLANNER' || log.role === 'ARCHITECT') return "Orchestrating workflow delegation...";
    if (log.metadata === 'THINKING') return "Deep reasoning in progress...";
    return "Processing...";
  }

  let lastNarrative = $derived.by(() => {
    const reversed = [...$logs].reverse();
    const active = reversed.find(l => l.category === 'TOOL_CALL' && !l.isComplete)
                || reversed.find(l => l.category === 'THOUGHT');
    return active ? translateLogToNarrative(active) : "System Idle";
  });
</script>

<div id="output-pane" bind:this={scrollContainer} onscroll={handleScroll}>

  {#if $runtimeStore.status === 'RUNNING'}
    <div class="narrative-ticker">
      <span class="pulse-dot"></span>
      <span class="narrative-text">{lastNarrative}</span>
    </div>
  {/if}

  <div class="log-wrapper" bind:this={contentWrapper}>

    {#each groupedLogs as group (group.id)}
      <div class="log-group">
        <div class="group-meta">
            <span class="group-role">{group.role}</span>
            <div class="timeline-line"></div>
        </div>

        <div class="group-body">
          {#each group.items as log (log.id)}
            <div class="log-item">
              
              {#if log.metadata && log.metadata !== 'INFO'}
                <div class="item-meta-header">
                  <span class="meta-tag">{log.metadata}</span>
                  <span class="meta-time">{new Date(log.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'})}</span>
                </div>
              {/if}

              <div class="log-content">
                {#if log.category === 'TOOL_CALL' || log.category === 'TOOL_RESULT'}
                  <ToolExecutionCard
                    category={log.category}
                    message={log.message}
                    metadata={log.metadata || 'INFO'}
                    agentId={log.role}
                    isComplete={log.isComplete}
                    toolResult={log.toolResult}
                    toolStatus={log.toolStatus}
                  />
                
                {:else if log.metadata === 'INTERVENTION'}
                  <ApprovalCard
                    reason={log.message === 'SAFETY_PATTERN_TRIGGERED' ? "System Policy Violation" : log.message}
                    runId={$runtimeStore.runId || ''}
                  />
                
                {:else if log.isAnimated}
                  <Typewriter
                    text={log.message}
                    onComplete={() => handleTypewriterComplete(log.id)}
                  />
                
                {:else}
                  <!-- STATIC LOG RENDERER -->
                  {@const fileList = extractAllFilenames(log.message)}
                  <!-- [[NEW]] Process Split Content -->
                  {@const { body, attachment } = processMessageContent(log.message)}
                  
                  <!-- 1. Text Content -->
                  {#if body}
                      <SmartText text={body} />
                  {/if}
                  
                  <!-- 2. Attachments (Web Search Results, etc) -->
                  {#if attachment}
                      <ContextAttachmentCard rawData={attachment} />
                  {/if}
                  
                  <!-- 3. Artifact Cards -->
                  {#if fileList.length > 0}
                     <ArtifactCard 
                        filenames={fileList} 
                        runId={$runtimeStore.runId || ''} 
                     />
                  {/if}
                {/if}
              </div>
            </div>
          {/each}
        </div>

      </div>
    {/each}

  </div>
</div>

<style>
  /* ... (Styles unchanged) ... */
  :global(.error-block) { background: rgba(211, 47, 47, 0.05); border-left: 3px solid #d32f2f; color: var(--paper-ink); padding: 10px; margin-top: 8px; font-family: var(--font-code); font-size: 11px; white-space: pre-wrap; word-break: break-all; }
  :global(.log-content strong) { color: var(--paper-ink); font-weight: 700; }
  
  #output-pane { flex: 1; padding: 24px; overflow-y: auto; display: flex; flex-direction: column; scrollbar-gutter: stable; will-change: scroll-position; }
  
  .log-wrapper { display: flex; flex-direction: column; gap: 0; min-height: min-content; }

  /* GROUP CONTAINER */
  .log-group {
    display: grid; 
    grid-template-columns: 60px 1fr;
    gap: 16px;
    padding: 16px 0;
    border-top: 1px dashed var(--paper-line);
    animation: slideUp 0.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  }

  @keyframes slideUp { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

  /* LEFT COLUMN */
  .group-meta {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    position: relative;
  }

  .group-role {
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.5px;
    color: var(--paper-ink);
    background: var(--paper-surface);
    padding: 2px 4px;
    border-radius: 2px;
    border: 1px solid var(--paper-line);
    text-transform: uppercase;
    z-index: 2;
  }

  .timeline-line {
    position: absolute;
    left: 50%; top: 20px; bottom: -20px;
    width: 1px;
    background: var(--paper-line);
    opacity: 0.2;
    z-index: 1;
    transform: translateX(-50%);
  }

  /* RIGHT COLUMN */
  .group-body {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .log-item {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .item-meta-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 2px;
  }

  .meta-tag { 
    font-family: var(--font-code); 
    font-size: 8px; 
    font-weight: 700;
    color: var(--paper-line); 
    background: var(--paper-surface); 
    padding: 1px 4px; 
    border-radius: 2px; 
    display: inline-block; 
    border: 1px solid transparent; 
  }
  
  :global(.mode-phosphor) .meta-tag { border-color: var(--paper-line); }

  .meta-time {
    font-family: var(--font-code);
    font-size: 8px;
    color: var(--paper-line);
    opacity: 0.5;
  }

  .log-content {
    font-size: 13px;
    line-height: 1.6;
    color: var(--paper-ink);
    opacity: 0.9;
  }

  .narrative-ticker {
    position: sticky;
    top: 0;
    background: var(--paper-bg);
    border-bottom: 1px solid var(--paper-line);
    padding: 8px 16px;
    z-index: 10;
    display: flex;
    align-items: center;
    gap: 8px;
    font-family: var(--font-code);
    font-size: 10px;
    color: var(--paper-ink);
    text-transform: uppercase;
    letter-spacing: 1px;
  }

  .pulse-dot {
    width: 6px;
    height: 6px;
    background: var(--arctic-cyan);
    border-radius: 50%;
    animation: pulse 1s infinite;
  }

  @keyframes pulse {
    0% { opacity: 1; }
    50% { opacity: 0.3; }
    100% { opacity: 1; }
  }
</style>
```