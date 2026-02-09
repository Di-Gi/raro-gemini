<!-- // [[RARO]]/apps/web-console/src/components/sub/SmartText.svelte -->

<script lang="ts">
  import CodeBlock from './CodeBlock.svelte';
  import DelegationCard from './DelegationCard.svelte';
  import SystemTag from './SystemTag.svelte';
  import { parseMarkdown } from '$lib/markdown';

  // 1. Define specific types for each block kind
  type CodePart = { type: 'code'; lang: string; content: string };
  type TagPart = { type: 'tag'; tagType: string; tagValue: string };
  type TextPart = { type: 'text'; content: string };

  // 2. Create a Union type
  type ContentBlock = CodePart | TagPart | TextPart;

  let { text }: { text: string } = $props();

  const ATTACHMENT_HEADER = "\n\n[AUTOMATED CONTEXT ATTACHMENT]";
  let cleanedText = $derived.by(() => {
    if (!text) return '';
    const splitIndex = text.indexOf(ATTACHMENT_HEADER);
    return splitIndex !== -1 ? text.substring(0, splitIndex) : text;
  });

  // 3. Explicitly type the return value of your parsers
  function parseContent(input: string): ContentBlock[] {
    const parts: ContentBlock[] = [];

    const codeRegex = /```([a-zA-Z0-9:_-]+)?\n([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;

    while ((match = codeRegex.exec(input)) !== null) {
      if (match.index > lastIndex) {
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

  function parseSystemTags(textChunk: string): ContentBlock[] {
    const tagRegex = /\[\s*(STATUS|BYPASS|SYSTEM|CONTEXT|OPERATIONAL)\s*:\s*(.*?)\s*\]/gi;
    const subParts: ContentBlock[] = [];
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
            tagType: match[1],
            tagValue: match[2]
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
      <!-- Inside this IF, TS knows 'block' is a CodePart -->
      {#if block.lang === 'json:delegation'}
        <DelegationCard rawJson={block.content} />
      {:else}
        <CodeBlock code={block.content} language={block.lang} />
      {/if}

    {:else if block.type === 'tag'}
        <!-- Inside this ELSE IF, TS knows 'block' is a TagPart -->
        <SystemTag type={block.tagType} value={block.tagValue} />

    {:else if block.type === 'text'}
      <!-- Inside this ELSE IF, TS knows 'block' is a TextPart -->
      <div class="markdown-body">
        {@html parseMarkdown(block.content)}
      </div>
    {/if}
  {/each}
</div>

<style>
  .smart-text-wrapper {
    display: flex;
    flex-direction: column;
    width: 100%;
    gap: 8px; 
  }

  /* === MARKDOWN TYPOGRAPHY SYSTEM === */

  :global(.markdown-body) {
    font-size: 13px;
    line-height: 1.6;
    color: var(--paper-ink);
    /* FIX: Force wrapping to prevent horizontal scroll */
    overflow-wrap: break-word;
    word-break: break-word;
  }

  /* HEADERS */
  :global(.markdown-body h1), 
  :global(.markdown-body h2), 
  :global(.markdown-body h3) {
    margin-top: 24px;
    margin-bottom: 12px;
    font-weight: 700;
    letter-spacing: -0.5px;
    color: var(--paper-ink);
  }

  :global(.markdown-body h1) { font-size: 18px; border-bottom: 1px solid var(--paper-line); padding-bottom: 8px; }
  :global(.markdown-body h2) { font-size: 16px; }
  :global(.markdown-body h3) { font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.8; }

  /* PARAGRAPHS */
  :global(.markdown-body p) {
    margin-bottom: 12px;
  }
  :global(.markdown-body p:last-child) {
    margin-bottom: 0;
  }

  /* LISTS */
  :global(.markdown-body ul), 
  :global(.markdown-body ol) {
    padding-left: 20px;
    margin-bottom: 12px;
  }
  :global(.markdown-body li) {
    margin-bottom: 4px;
    padding-left: 4px;
  }
  :global(.markdown-body li::marker) {
    color: var(--paper-line);
  }

  /* INLINE ELEMENTS */
  :global(.markdown-body strong) {
    font-weight: 700;
    color: var(--paper-ink);
  }
  
  :global(.markdown-body em) {
    font-style: italic;
    opacity: 0.8;
  }

  :global(.markdown-body code) {
    font-family: var(--font-code);
    font-size: 11px;
    padding: 2px 4px;
    background: var(--paper-surface);
    border: 1px solid var(--paper-line);
    border-radius: 2px;
    color: var(--arctic-cyan);
  }
  
  :global(.mode-archival .markdown-body code) {
    color: #e36209;
  }

  /* LINKS (Configured in markdown.ts) */
  :global(.md-link) {
    color: var(--arctic-lilac);
    text-decoration: none;
    border-bottom: 1px dotted var(--arctic-lilac);
    transition: all 0.2s;
  }
  :global(.md-link:hover) {
    background: var(--arctic-lilac-lite);
    border-bottom-style: solid;
  }

  /* BLOCKQUOTES (Configured in markdown.ts) */
  :global(.md-quote) {
    margin: 16px 0;
    padding: 8px 16px;
    border-left: 3px solid var(--paper-line);
    background: var(--paper-surface);
    font-style: italic;
    color: var(--paper-line);
  }
  
  /* TABLES */
  :global(.markdown-body table) {
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
    font-family: var(--font-code);
    font-size: 11px;
  }
  
  :global(.markdown-body th) {
    text-align: left;
    padding: 8px;
    border-bottom: 1px solid var(--paper-line);
    color: var(--paper-line);
    text-transform: uppercase;
    font-weight: 600;
  }
  
  :global(.markdown-body td) {
    padding: 8px;
    border-bottom: 1px dashed var(--paper-line);
    color: var(--paper-ink);
  }
</style>