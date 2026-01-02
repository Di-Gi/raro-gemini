<!-- // [[RARO]]/apps/web-console/src/components/sub/Typewriter.svelte -->

<script lang="ts">
  import Spinner from './Spinner.svelte';
  import CodeBlock from './CodeBlock.svelte';

  let { text, onComplete }: { text: string, onComplete?: () => void } = $props();

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
  // This derived state splits the partially typed string into segments
  let segments = $derived(parseStream(displayedText));

  function parseStream(input: string) {
    const parts = [];
    // Regex for CLOSED blocks
    const closedBlockRegex = /```(\w+)?\n([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;

    // 1. Find all fully closed blocks within the current stream
    while ((match = closedBlockRegex.exec(input)) !== null) {
      if (match.index > lastIndex) {
        parts.push({ type: 'text', content: input.slice(lastIndex, match.index) });
      }
      parts.push({ type: 'code', lang: match[1] || 'text', content: match[2] });
      lastIndex = closedBlockRegex.lastIndex;
    }

    // 2. Handle the "Tail"
    const tail = input.slice(lastIndex);
    
    // Check if the tail starts a NEW block that hasn't closed yet
    // Matches: ``` optionally followed by lang, optionally followed by newline, then content
    const openBlockMatch = /```(\w+)?(?:\n)?([\s\S]*)$/.exec(tail);

    if (openBlockMatch) {
       // Push text BEFORE the ```
       if (openBlockMatch.index > 0) {
         parts.push({ type: 'text', content: tail.slice(0, openBlockMatch.index) });
       }
       // Push the OPEN code block
       parts.push({ 
         type: 'code', 
         lang: openBlockMatch[1] || 'text', 
         content: openBlockMatch[2] || '', // Content might be empty if just ``` typed
         isOpen: true // Flag to tell CodeBlock it's incomplete
       });
    } else {
       // Treat as standard text
       if (tail.length > 0) {
         parts.push({ type: 'text', content: tail });
       }
    }
    
    return parts;
  }

  // === 2. STANDARD TYPEWRITER LOGIC (Unchanged) ===
  
  $effect(() => {
    return () => clearTimeout(timer);
  });

  // Cursor Blink Logic
  $effect(() => {
    if (!isTyping) { showCursor = false; return; }
    const blinkInterval = setInterval(() => {
        if (Date.now() - lastFrameTime > 100) showCursor = !showCursor;
        else showCursor = true;
    }, 500);
    return () => clearInterval(blinkInterval);
  });

  $effect(() => {
    if (text && text.length > currentIndex) {
      isTyping = true;
      typeNext();
    } else if (text && text.length === currentIndex) {
        isTyping = false;
        if (onComplete) onComplete();
    }
  });

  function typeNext() {
    clearTimeout(timer);
    
    if (currentIndex < text.length) {
      const now = Date.now();
      if (lastFrameTime) {
          const delta = now - lastFrameTime;
          charSpeed = Math.floor(1000 / delta); 
      }
      lastFrameTime = now;

      const remaining = text.length - currentIndex;
      let chunk = 1;
      let delay = 20;

      // HTML Tag Skip (Basic)
      if (text[currentIndex] === '<') {
          const closeIdx = text.indexOf('>', currentIndex);
          if (closeIdx !== -1) {
              chunk = (closeIdx - currentIndex) + 1;
              delay = 0; 
          }
      } 
      // Speed up for code blocks (simple heuristic)
      else if (text.slice(currentIndex, currentIndex+3) === '```') {
           chunk = 3; delay = 10;
      }
      else if (remaining > 500) { chunk = 25; delay = 2; }
      else if (remaining > 100) { chunk = 5; delay = 10; }
      
      const nextIndex = Math.min(currentIndex + chunk, text.length);
      
      displayedText = text.substring(0, nextIndex);
      currentIndex = nextIndex;
      charCount = currentIndex;
      
      timer = setTimeout(typeNext, delay);
    } else {
      isTyping = false;
      if (onComplete) onComplete();
    }
  }
</script>

<div class="typewriter-container">
  
  <div class="stream-content">
    {#each segments as segment, i}
      {#if segment.type === 'code'}
        <!-- 
          Render as CodeBlock. 
          We pass 'activeCursor' ONLY if we are typing AND this is the last segment 
        -->
        <CodeBlock 
            code={segment.content} 
            language={segment.lang || 'text'} 
            activeCursor={isTyping && i === segments.length - 1} 
        />
      {:else}
        <span class="text-body">{@html segment.content}</span>
        
        <!-- Render Cursor for Text Segments -->
        {#if isTyping && i === segments.length - 1}
           <span class="cursor" style:opacity={showCursor ? 1 : 0}>▋</span>
        {/if}
      {/if}
    {/each}
    
    <!-- Edge case: Stream is empty/starting, show cursor at start -->
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
    /* Important: Must be flex-col to handle mixing divs (CodeBlock) and spans (Text) */
    display: flex;
    flex-direction: column; 
    align-items: flex-start;
    line-height: 1.6;
    word-break: break-word;
    color: var(--paper-ink);
  }

  /* Text bodies need to handle newlines naturally via whitespace */
  .text-body {
    white-space: pre-wrap; 
    width: 100%;
  }

  /* Telemetry Footer styles (unchanged) */
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

  /* Standard Cursor */
  .cursor {
    display: inline-block;
    color: var(--arctic-cyan);
    margin-left: 1px;
    vertical-align: text-bottom;
    line-height: 1;
    font-weight: 900;
  }
</style>