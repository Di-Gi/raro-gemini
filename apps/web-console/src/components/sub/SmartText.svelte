<!-- // [[RARO]]/apps/web-console/src/components/sub/SmartText.svelte -->

<script lang="ts">
  import CodeBlock from './CodeBlock.svelte';
  import DelegationCard from './DelegationCard.svelte';
  import { parseMarkdown } from '$lib/markdown';

  let { text }: { text: string } = $props();

  // Strip context attachment BEFORE rendering to prevent flash
  // (Should already be stripped in OutputPane, but defensive layering)
  const ATTACHMENT_HEADER = "\n\n[AUTOMATED CONTEXT ATTACHMENT]";
  let cleanedText = $derived.by(() => {
    if (!text) return '';
    const splitIndex = text.indexOf(ATTACHMENT_HEADER);
    return splitIndex !== -1 ? text.substring(0, splitIndex) : text;
  });

  function parseContent(input: string) {
    const regex = /```([a-zA-Z0-9:_-]+)?\n([\s\S]*?)```/g;
    const parts = [];
    let lastIndex = 0;
    let match;

    while ((match = regex.exec(input)) !== null) {
      if (match.index > lastIndex) {
        parts.push({
          type: 'text',
          content: input.slice(lastIndex, match.index)
        });
      }

      parts.push({
        type: 'code',
        lang: match[1] || 'text',
        content: match[2]
      });

      lastIndex = regex.lastIndex;
    }

    if (lastIndex < input.length) {
      parts.push({
        type: 'text',
        content: input.slice(lastIndex)
      });
    }

    return parts;
  }

  let blocks = $derived(parseContent(cleanedText));
</script>

<div class="smart-text-wrapper">
  {#each blocks as block}
    {#if block.type === 'code'}
      <!-- ROUTING LOGIC -->
      {#if block.lang === 'json:delegation'}
        <DelegationCard rawJson={block.content} />
      {:else}
        <CodeBlock code={block.content} language={block.lang || 'text'} />
      {/if}
    {:else}
      <!-- 
        Pass text segments through Marked.
        The wrapper div handles the CSS for the generated HTML.
      -->
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