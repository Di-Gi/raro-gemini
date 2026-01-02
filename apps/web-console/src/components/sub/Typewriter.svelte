<!-- // [[RARO]]/apps/web-console/src/components/sub/Typewriter.svelte
// Purpose: High-fidelity streaming text component with telemetry footer.
// Architecture: UI Component
// Dependencies: Spinner -->

<script lang="ts">
  import Spinner from './Spinner.svelte';

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

  $effect(() => {
    return () => clearTimeout(timer);
  });

  // Cursor Blink Logic (Solid when typing, Blinks when idle)
  $effect(() => {
    if (!isTyping) {
        showCursor = false;
        return;
    }
    
    const blinkInterval = setInterval(() => {
        // Only blink if we aren't typing fast
        if (Date.now() - lastFrameTime > 100) {
            showCursor = !showCursor;
        } else {
            showCursor = true; // Solid while active
        }
    }, 500);
    
    return () => clearInterval(blinkInterval);
  });

  // Watch input
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
      
      // Calculate instantaneous speed (chars/sec) for effect
      if (lastFrameTime) {
          const delta = now - lastFrameTime;
          // Rough smoothing
          charSpeed = Math.floor(1000 / delta); 
      }
      lastFrameTime = now;

      // Adaptive Chunking
      const remaining = text.length - currentIndex;
      let chunk = 1;
      let delay = 20; // Base mechanical delay

      // 1. Detect HTML Tag to skip it (Simple detector)
      if (text[currentIndex] === '<') {
          const closeIdx = text.indexOf('>', currentIndex);
          if (closeIdx !== -1) {
              chunk = (closeIdx - currentIndex) + 1;
              delay = 0; // Instant render for tags
          }
      } 
      // 2. Heavy Load catch-up
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
    <!-- 
      Using pre-wrap to respect newlines from LLM.
      HTML injection allowed for basic formatting like <b> or <br>.
    -->
    <span class="text-body">{@html displayedText}</span>{#if isTyping}<span class="cursor" style:opacity={showCursor ? 1 : 0}>▋</span>{/if}
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
    word-break: break-word;
    white-space: pre-wrap; /* Critical for preserving LLM paragraph structure */
    color: var(--paper-ink);
  }

  .cursor {
    display: inline-block;
    color: var(--arctic-cyan); /* Cyber accent for the cursor */
    margin-left: 1px;
    vertical-align: text-bottom;
    line-height: 1;
    font-weight: 900;
  }

  /* === TELEMETRY FOOTER === */
  .telemetry-footer {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-top: 12px;
    padding-top: 8px;
    border-top: 1px dashed rgba(0,0,0,0.1);
    font-size: 9px;
    color: #888;
    user-select: none;
    animation: fadeIn 0.3s ease;
  }

  .stat-group {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .right-aligned {
    margin-left: auto; /* Pushes status to far right */
    color: var(--paper-ink);
  }

  .label {
    font-weight: 600;
    opacity: 0.6;
    letter-spacing: 0.5px;
  }

  .value {
    font-family: var(--font-code);
    font-weight: 400;
  }

  .ingress {
    color: var(--paper-line);
    font-weight: 700;
    letter-spacing: 1px;
    animation: pulse 1s infinite alternate;
  }

  @keyframes pulse {
    from { opacity: 0.6; }
    to { opacity: 1; }
  }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(-5px); }
    to { opacity: 1; transform: translateY(0); }
  }
</style>