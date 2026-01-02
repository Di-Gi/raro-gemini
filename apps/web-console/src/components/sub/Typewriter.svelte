<!-- // [[RARO]]/apps/web-console/src/components/sub/Typewriter.svelte
// Purpose: Streaming text with terminal cursor and technical status footer.
// Architecture: UI Component
// Dependencies: Spinner -->

<script lang="ts">
  import Spinner from './Spinner.svelte';

  let { text, onComplete }: { text: string, onComplete?: () => void } = $props();

  let displayedText = $state('');
  let isTyping = $state(true);
  let showCursor = $state(true);
  
  // Track how much we have displayed
  let currentIndex = 0;
  let timer: any;

  // Cleanup on destroy
  $effect(() => {
      return () => clearTimeout(timer);
  });

  // Cursor Blinker
  $effect(() => {
    if (!isTyping) {
        showCursor = false;
        return;
    }
    const blinkInterval = setInterval(() => {
        showCursor = !showCursor;
    }, 500);
    return () => clearInterval(blinkInterval);
  });

  // Reactive Effect: Watch for text updates (Streaming)
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
      const remaining = text.length - currentIndex;
      let chunk = 1;
      let speed = 15; 

      // Adaptive chunking for heavy loads
      if (remaining > 1000) { chunk = 50; speed = 1; }      
      else if (remaining > 200) { chunk = 10; speed = 5; }  
      else if (remaining > 50) { chunk = 3; speed = 10; }   
      
      const nextIndex = Math.min(currentIndex + chunk, text.length);
      
      displayedText = text.substring(0, nextIndex);
      currentIndex = nextIndex;
      
      timer = setTimeout(typeNext, speed);
    } else {
      isTyping = false;
      if (onComplete) onComplete();
    }
  }
</script>

<div class="typewriter-container">
  <div class="content">
    {@html displayedText}{#if isTyping}<span class="cursor" style:opacity={showCursor ? 1 : 0}>█</span>{/if}
  </div>
  
  {#if isTyping}
    <div class="status-bar">
      <span class="ingress-label">DATA INGRESS //</span>
      <Spinner />
    </div>
  {/if}
</div>

<style>
  .typewriter-container {
    position: relative;
    font-family: var(--font-code);
    display: block; 
    width: 100%;
  }

  .content {
    white-space: pre-wrap;
    word-break: break-word;
    display: block;
    line-height: 1.5;
  }

  .cursor {
    display: inline-block;
    color: var(--paper-ink);
    margin-left: 2px;
    font-size: 0.8em;
    vertical-align: baseline;
  }

  /* The invisible bar at the bottom right */
  .status-bar {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    height: 20px;
    margin-top: 8px;
    opacity: 0.6;
    gap: 8px;
  }

  .ingress-label {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
    color: var(--paper-line);
    text-transform: uppercase;
  }
</style>