<!-- // [[RARO]]/apps/web-console/src/components/sub/CodeBlock.svelte -->

<script lang="ts">
  import { fade } from 'svelte/transition';
  import { highlight } from '$lib/syntax-lite';
  
  let { 
    code, 
    language, 
    activeCursor = false 
  }: { 
    code: string, 
    language: string, 
    activeCursor?: boolean 
  } = $props();

  let copied = $state(false);
  let timeout: any;

  // 1. NEW: Handle escaped newlines (e.g. from JSON logs) to ensure <pre> breaks lines correctly
  let cleanCode = $derived(code ? code.replace(/\\n/g, '\n') : '');

  // 2. Highlight logic (pass the cleaned code)
  let highlightedCode = $derived(highlight(cleanCode, language));

  function copyToClipboard() {
    // Copy the cleaned code (actual newlines), not the escaped version
    navigator.clipboard.writeText(cleanCode);
    copied = true;
    clearTimeout(timeout);
    timeout = setTimeout(() => copied = false, 2000);
  }
</script>

<div class="code-chassis" transition:fade={{ duration: 200 }}>
  <div class="code-header">
    <div class="lang-tag">
      <div class="status-dot"></div>
      {language || 'TXT'}
    </div>
    
    <button class="action-copy" onclick={copyToClipboard} class:success={copied}>
      {#if copied} COPIED {:else} COPY_ {/if}
    </button>
  </div>

  <div class="code-viewport">
    <pre><code class="language-{language}"><!-- 
      --><span class="code-inner">{@html highlightedCode}</span><!--
      -->{#if activeCursor}<span class="cursor-block">▋</span>{/if}<!-- 
    --></code></pre>
  </div>
</div>

<style>
  /* ... Existing styles remain unchanged ... */
  
  .code-chassis {
    margin: 16px 0;
    border: 1px solid var(--paper-line);
    background: color-mix(in srgb, var(--paper-bg), var(--paper-ink) 3%);
    border-radius: 2px;
    overflow: hidden;
    font-family: var(--font-code);
    transition: border-color 0.3s;
    display: block; 
    width: 100%;
  }

  .code-chassis:hover { border-color: var(--paper-ink); }
  .code-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 12px; border-bottom: 1px solid var(--paper-line);
    background: var(--paper-surface); user-select: none;
  }
  .lang-tag {
    font-size: 9px; font-weight: 700; text-transform: uppercase;
    color: var(--paper-ink); display: flex; align-items: center; gap: 6px;
  }
  .status-dot { width: 4px; height: 4px; background: var(--alert-amber); border-radius: 50%; }
  .action-copy {
    background: transparent; border: none; font-family: var(--font-code);
    font-size: 9px; font-weight: 600; color: var(--paper-line); cursor: pointer;
  }
  .action-copy:hover { color: var(--paper-ink); }
  .action-copy.success { color: var(--signal-success); }
  
  .code-viewport { padding: 16px; overflow-x: auto; font-size: 11px; line-height: 1.5; }
  pre { margin: 0; font-family: var(--font-code); }

  :global(.token-kw) { color: var(--arctic-cyan); font-weight: 700; }
  :global(.mode-archival .token-kw) { color: #005cc5; }
  :global(.token-str) { color: #a5d6ff; }
  :global(.mode-archival .token-str) { color: #032f62; }
  :global(.token-comment) { color: var(--paper-line); font-style: italic; }
  :global(.token-num), :global(.token-bool) { color: var(--alert-amber); }
  :global(.mode-archival .token-num), :global(.mode-archival .token-bool) { color: #d73a49; }

  .cursor-block {
    display: inline-block;
    color: var(--arctic-cyan);
    margin-left: 1px;
    vertical-align: text-bottom;
    line-height: 1;
    animation: blink 1s infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }
</style>