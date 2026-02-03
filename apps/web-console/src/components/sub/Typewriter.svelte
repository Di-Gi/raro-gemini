<!-- // [[RARO]]/apps/web-console/src/components/sub/Typewriter.svelte -->

<script lang="ts">
  import Spinner from './Spinner.svelte';
  import CodeBlock from './CodeBlock.svelte';
  import DelegationCard from './DelegationCard.svelte';

  let { text, onComplete }: { text: string, onComplete?: () => void } = $props();

  // Strip context attachment BEFORE typewriter to prevent flash
  const ATTACHMENT_HEADER = "\n\n[AUTOMATED CONTEXT ATTACHMENT]";
  let cleanedText = $derived.by(() => {
    if (!text) return '';
    const splitIndex = text.indexOf(ATTACHMENT_HEADER);
    return splitIndex !== -1 ? text.substring(0, splitIndex) : text;
  });

  let displayedText = $state('');
  let isTyping = $state(true);
  let showCursor = $state(true);

  // Telemetry
  let charCount = $state(0);
  let charSpeed = $state(0);
  let lastFrameTime = 0;

  // Internal State
  let currentIndex = 0;
  let timer: any;



  // === 1. LIVE PARSER ===
  let segments = $derived(parseStream(displayedText));



  function parseStream(input: string) {
    const parts = [];
    const closedBlockRegex = /```([a-zA-Z0-9:_-]+)?\n([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;

    // 1. Fully closed blocks
    while ((match = closedBlockRegex.exec(input)) !== null) {
      if (match.index > lastIndex) {
        parts.push({ type: 'text', content: input.slice(lastIndex, match.index) });
      }
      parts.push({ 
          type: 'code', 
          lang: match[1] || 'text', 
          content: match[2],
          isOpen: false 
      });
      lastIndex = closedBlockRegex.lastIndex;
    }

    // 2. The "Tail" (Potentially open block)
    const tail = input.slice(lastIndex);
    const openBlockMatch = /```([a-zA-Z0-9:_-]+)?(?:\n)?([\s\S]*)$/.exec(tail);

    if (openBlockMatch) {
       if (openBlockMatch.index > 0) {
         parts.push({ type: 'text', content: tail.slice(0, openBlockMatch.index) });
       }
       parts.push({ 
         type: 'code', 
         lang: openBlockMatch[1] || 'text', 
         content: openBlockMatch[2] || '', 
         isOpen: true // Flag to indicate loading/incomplete
       });
    } else {
       if (tail.length > 0) {
         parts.push({ type: 'text', content: tail });
       }
    }
    
    return parts;
  }

  // === 2. STANDARD TYPEWRITER LOGIC ===
  
  $effect(() => {
    return () => clearTimeout(timer);
  });

  $effect(() => {
    if (!isTyping) { showCursor = false; return; }
    const blinkInterval = setInterval(() => {
        if (Date.now() - lastFrameTime > 100) showCursor = !showCursor;
        else showCursor = true;
    }, 500);
    return () => clearInterval(blinkInterval);
  });

  $effect(() => {
    if (cleanedText && cleanedText.length > currentIndex) {
      isTyping = true;
      typeNext();
    } else if (cleanedText && cleanedText.length === currentIndex) {
        isTyping = false;
        if (onComplete) onComplete();
    }
  });

  function typeNext() {
    clearTimeout(timer);

    if (currentIndex < cleanedText.length) {
      const now = Date.now();
      if (lastFrameTime) {
          const delta = now - lastFrameTime;
          charSpeed = Math.floor(1000 / delta); 
      }
      lastFrameTime = now;

      const remaining = cleanedText.length - currentIndex;
      let chunk = 1;
      let delay = 20;

      // HTML Tag Skip
      if (cleanedText[currentIndex] === '<') {
          const closeIdx = cleanedText.indexOf('>', currentIndex);
          if (closeIdx !== -1) {
              chunk = (closeIdx - currentIndex) + 1;
              delay = 0;
          }
      }
      // Speed up for code blocks
      else if (cleanedText.slice(currentIndex, currentIndex+3) === '```') {
           chunk = 3; delay = 10;
      }
      else if (remaining > 500) { chunk = 25; delay = 2; }
      else if (remaining > 100) { chunk = 5; delay = 10; }

      const nextIndex = Math.min(currentIndex + chunk, cleanedText.length);
      displayedText = cleanedText.substring(0, nextIndex);
      currentIndex = nextIndex;
      charCount = currentIndex;
      
      timer = setTimeout(typeNext, delay);
    } else {
      isTyping = false;
      if (onComplete) onComplete();
    }
  }

  function escapeHtml(unsafe: string) {
      return unsafe
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;");
  }

</script>

<div class="typewriter-container">
  
  <div class="stream-content">
    {#each segments as segment, i}
      {#if segment.type === 'code'}
        <!-- ROUTER -->
        {#if segment.lang === 'json:delegation'}
            <DelegationCard 
                rawJson={segment.content} 
                loading={segment.isOpen} 
            />
        {:else}
            <CodeBlock 
                code={segment.content} 
                language={segment.lang || 'text'} 
                activeCursor={isTyping && i === segments.length - 1} 
            />
        {/if}
      {:else}
        <!--
           1. Escape HTML (So "<Button>" shows as text, not a hidden tag)
           2. Replace newlines with actual line breaks for pre-wrap
        -->
        <span class="text-body">{@html escapeHtml(segment.content).replace(/\\n/g, '\n')}</span>
        {#if isTyping && i === segments.length - 1}
           <span class="cursor" style:opacity={showCursor ? 1 : 0}>▋</span>
        {/if}
      {/if}
    {/each}
    
    {#if isTyping && segments.length === 0}
        <span class="cursor" style:opacity={showCursor ? 1 : 0}>▋</span>
    {/if}
  </div>
  
  {#if isTyping}
    <div class="telemetry-footer">
      <div class="stat-group">
        <span class="label">SPEED</span>
        <span class="value">{charSpeed} CPS</span>
      </div>
      <div class="stat-group">
        <span class="label">SIZE</span>
        <span class="value">{charCount} B</span>
      </div>
      <div class="stat-group right-aligned">
        <span class="label ingress">DATA_INGRESS</span>
        <Spinner />
      </div>
    </div>
  {/if}
</div>

<style>
  .typewriter-container {
    position: relative;
    width: 100%;
    font-family: var(--font-code);
  }

  .stream-content {
    display: block; 
    line-height: 1.6;
    word-break: break-word; /* Ensure long non-breaking strings don't scroll */
    overflow-wrap: break-word;
    color: var(--paper-ink);
  }

  .text-body {
    white-space: pre-wrap; 
    display: inline;
  }

  .telemetry-footer {
    display: flex; align-items: center; gap: 16px; margin-top: 12px; padding-top: 8px;
    border-top: 1px dashed rgba(0,0,0,0.1); font-size: 9px; color: #888; user-select: none;
    animation: fadeIn 0.3s ease;
  }
  .stat-group { display: flex; align-items: center; gap: 6px; }
  .right-aligned { margin-left: auto; color: var(--paper-ink); }
  .label { font-weight: 600; opacity: 0.6; letter-spacing: 0.5px; }
  .value { font-family: var(--font-code); font-weight: 400; }
  .ingress { color: var(--paper-line); font-weight: 700; letter-spacing: 1px; animation: pulse 1s infinite alternate; }
  @keyframes pulse { from { opacity: 0.6; } to { opacity: 1; } }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(-5px); } to { opacity: 1; transform: translateY(0); } }

  .cursor {
    display: inline-block;
    color: var(--arctic-cyan);
    margin-left: 1px;
    vertical-align: text-bottom;
    line-height: 1;
    font-weight: 900;
  }
</style>