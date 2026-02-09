Here is the implementation guide to visually enrich system signals and fix the header alignment issues.

### Part 1: The Visual Enrichment System

We need a dedicated component to render these system tags (Status, Bypass, etc.) and a parser update in `SmartText` to detect them within the text stream.

#### 1. Create `SystemTag.svelte`
**File:** `apps/web-console/src/components/sub/SystemTag.svelte`
**Action:** Create New File

This component parses the raw tag string (e.g., `[BYPASS: No need for tool]`) and renders a styled terminal badge.

```svelte
<!-- [[RARO]]/apps/web-console/src/components/sub/SystemTag.svelte -->
<script lang="ts">
    import { fade } from 'svelte/transition';

    let { type, value }: { type: string, value: string } = $props();

    // Normalization
    let normalizedType = $derived(type.toUpperCase().trim());
    let displayValue = $derived(value.trim());

    // Style Logic
    let styleClass = $derived.by(() => {
        if (normalizedType.includes('SUCCESS') || displayValue.includes('SUCCESS')) return 'success';
        if (normalizedType.includes('FAIL') || normalizedType.includes('NULL')) return 'fail';
        if (normalizedType.includes('BYPASS')) return 'bypass';
        if (normalizedType.includes('CONTEXT')) return 'context';
        return 'info';
    });

    let icon = $derived.by(() => {
        if (styleClass === 'success') return '✓';
        if (styleClass === 'fail') return '✕';
        if (styleClass === 'bypass') return '↷';
        if (styleClass === 'context') return '📎';
        return 'ℹ';
    });
</script>

<div class="sys-tag {styleClass}" in:fade={{ duration: 200 }}>
    <div class="tag-label">
        <span class="icon">{icon}</span>
        {normalizedType}
    </div>
    <div class="tag-value">{displayValue}</div>
</div>

<style>
    .sys-tag {
        display: inline-flex;
        align-items: center;
        margin: 4px 0;
        font-family: var(--font-code);
        font-size: 10px;
        border-radius: 2px;
        overflow: hidden;
        border: 1px solid;
        max-width: 100%;
        vertical-align: middle;
        user-select: none;
    }

    .tag-label {
        padding: 2px 6px;
        font-weight: 700;
        display: flex;
        align-items: center;
        gap: 4px;
        text-transform: uppercase;
    }

    .tag-value {
        padding: 2px 8px;
        background: var(--paper-bg);
        color: var(--paper-ink);
        border-left: 1px solid;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    /* === VARIANTS === */
    
    /* SUCCESS / STATUS: SUCCESS */
    .sys-tag.success { border-color: var(--signal-success); }
    .sys-tag.success .tag-label { background: var(--signal-success); color: #fff; }
    .sys-tag.success .tag-value { border-left-color: var(--signal-success); }

    /* FAIL / STATUS: NULL */
    .sys-tag.fail { border-color: #d32f2f; }
    .sys-tag.fail .tag-label { background: #d32f2f; color: #fff; }
    .sys-tag.fail .tag-value { border-left-color: #d32f2f; color: #d32f2f; }

    /* BYPASS */
    .sys-tag.bypass { border-color: var(--alert-amber); }
    .sys-tag.bypass .tag-label { background: var(--alert-amber); color: #000; }
    .sys-tag.bypass .tag-value { border-left-color: var(--alert-amber); }

    /* CONTEXT */
    .sys-tag.context { border-color: var(--arctic-cyan); }
    .sys-tag.context .tag-label { background: rgba(0, 240, 255, 0.1); color: var(--arctic-cyan); }
    .sys-tag.context .tag-value { border-left-color: var(--arctic-cyan); }

    /* DEFAULT */
    .sys-tag.info { border-color: var(--paper-line); }
    .sys-tag.info .tag-label { background: var(--paper-line); color: var(--paper-bg); }
    .sys-tag.info .tag-value { border-left-color: var(--paper-line); }
</style>
```

#### 2. Update `SmartText.svelte`
**File:** `apps/web-console/src/components/sub/SmartText.svelte`
**Action:** Modify

We update the parser to split "Text" blocks further into "System Tag" blocks.

```svelte
<!-- // [[RARO]]/apps/web-console/src/components/sub/SmartText.svelte -->

<script lang="ts">
  import CodeBlock from './CodeBlock.svelte';
  import DelegationCard from './DelegationCard.svelte';
  import SystemTag from './SystemTag.svelte'; // [[NEW]]
  import { parseMarkdown } from '$lib/markdown';

  let { text }: { text: string } = $props();

  const ATTACHMENT_HEADER = "\n\n[AUTOMATED CONTEXT ATTACHMENT]";
  let cleanedText = $derived.by(() => {
    if (!text) return '';
    const splitIndex = text.indexOf(ATTACHMENT_HEADER);
    return splitIndex !== -1 ? text.substring(0, splitIndex) : text;
  });

  // Updated Parser to handle both Code Blocks AND System Tags
  function parseContent(input: string) {
    const parts = [];
    
    // 1. Split by Code Blocks (Existing Logic)
    const codeRegex = /```([a-zA-Z0-9:_-]+)?\n([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;

    while ((match = codeRegex.exec(input)) !== null) {
      if (match.index > lastIndex) {
        // Process the text chunk before the code block for System Tags
        const textChunk = input.slice(lastIndex, match.index);
        parts.push(...parseSystemTags(textChunk));
      }

      parts.push({
        type: 'code',
        lang: match[1] || 'text',
        content: match[2]
      });

      lastIndex = codeRegex.lastIndex;
    }

    if (lastIndex < input.length) {
      const textChunk = input.slice(lastIndex);
      parts.push(...parseSystemTags(textChunk));
    }

    return parts;
  }

  // [[NEW]] Sub-parser for [TYPE: VALUE] tags
  function parseSystemTags(textChunk: string) {
    // Regex for: [STATUS: SUCCESS], [BYPASS: ...], [SYSTEM: ...]
    // Case insensitive, permissive spacing
    const tagRegex = /\[\s*(STATUS|BYPASS|SYSTEM|CONTEXT|OPERATIONAL)\s*:\s*(.*?)\s*\]/gi;
    
    const subParts = [];
    let lastIndex = 0;
    let match;

    while ((match = tagRegex.exec(textChunk)) !== null) {
        if (match.index > lastIndex) {
            subParts.push({
                type: 'text',
                content: textChunk.slice(lastIndex, match.index)
            });
        }

        subParts.push({
            type: 'tag',
            tagType: match[1], // e.g. "BYPASS"
            tagValue: match[2] // e.g. "Context Provided"
        });

        lastIndex = tagRegex.lastIndex;
    }

    if (lastIndex < textChunk.length) {
        subParts.push({
            type: 'text',
            content: textChunk.slice(lastIndex)
        });
    }

    return subParts;
  }

  let blocks = $derived(parseContent(cleanedText));
</script>

<div class="smart-text-wrapper">
  {#each blocks as block}
    {#if block.type === 'code'}
      {#if block.lang === 'json:delegation'}
        <DelegationCard rawJson={block.content} />
      {:else}
        <CodeBlock code={block.content} language={block.lang || 'text'} />
      {/if}
    
    <!-- [[NEW]] Render System Tag -->
    {:else if block.type === 'tag'}
        <SystemTag type={block.tagType} value={block.tagValue} />

    {:else}
      <div class="markdown-body">
        {@html parseMarkdown(block.content)}
      </div>
    {/if}
  {/each}
</div>

<style>
  /* ... Styles remain unchanged ... */
  .smart-text-wrapper {
    display: flex;
    flex-direction: column;
    width: 100%;
    gap: 8px; 
  }
  /* ... include previous CSS ... */
</style>
```

---

### Part 2: Layout Fix (Overlap Correction)

To ensure the "Node Title" (e.g., `MASTER_PLANNER`) doesn't visually collide with the metadata tags of the first log entry, we simply push the content stack down slightly within the log group layout.

#### 3. Update `OutputPane.svelte`
**File:** `apps/web-console/src/components/OutputPane.svelte`
**Action:** Modify CSS

We add specific spacing to the `.group-body` (the right column containing the logs) to ensure its first child sits below the visual baseline of the sticky identity label on the left.

```svelte
<!-- In <style> block at the bottom of OutputPane.svelte -->

<style>
  /* ... existing styles ... */

  /* RIGHT COLUMN */
  .group-body {
    display: flex;
    flex-direction: column;
    gap: 12px;
    
    /* [[FIX]] Add top padding to push content down relative to the sticky Agent Name on the left */
    padding-top: 6px; 
  }

  /* Alternatively, target the metadata header specifically */
  .item-meta-header {
    display: flex;
    align-items: center;
    gap: 8px;
    /* [[FIX]] Add bottom margin to separate headers from content */
    margin-bottom: 6px; 
  }

  /* ... rest of styles ... */
</style>
```

### Summary of Changes

1.  **`SystemTag.svelte`**: A new visual primitive that renders text like `[BYPASS: Reason]` as a high-contrast badge (Amber for Bypass, Green for Success, Red for Null/Fail).
2.  **`SmartText.svelte`**: Now recursively parses text blocks to extract these patterns and render the `SystemTag` component inline with Markdown.
3.  **`OutputPane.svelte`**: Added `padding-top: 6px` to `.group-body` to visually decouple the first log entry's metadata line from the sticky Agent ID label on the left, fixing the visual overlap.