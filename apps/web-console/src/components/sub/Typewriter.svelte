<!-- apps/web-console/src/components/sub/Typewriter.svelte -->
<script lang="ts">
  import Spinner from './Spinner.svelte';

  let { text, onComplete }: { text: string, onComplete?: () => void } = $props();

  let displayedText = $state('');
  let isTyping = $state(true);
  
  // Track how much we have displayed
  let currentIndex = 0;
  let timer: any;

  // Cleanup on destroy
  $effect(() => {
      return () => clearTimeout(timer);
  });

  // Reactive Effect: Watch for text updates (Streaming)
  $effect(() => {
    // If the source text is longer than what we are showing...
    if (text && text.length > currentIndex) {
      // Ensure typing indicator is on
      isTyping = true;
      // Start/Resume the loop
      typeNext();
    } else if (text && text.length === currentIndex) {
        // We are caught up
        isTyping = false;
        if (onComplete) onComplete();
    }
  });

  function typeNext() {
    clearTimeout(timer);
    
    // Safety check
    if (currentIndex < text.length) {
      // Adaptive chunking: If we are falling far behind (large stream), type faster
      const remaining = text.length - currentIndex;
      let chunk = 1;
      let speed = 15; // Standard mechanical typing speed

      if (remaining > 1000) { chunk = 50; speed = 1; }      // Super fast catchup
      else if (remaining > 200) { chunk = 10; speed = 5; }  // Fast catchup
      else if (remaining > 50) { chunk = 3; speed = 10; }   // Moderate catchup
      
      const nextIndex = Math.min(currentIndex + chunk, text.length);
      
      // Update state
      displayedText = text.substring(0, nextIndex);
      currentIndex = nextIndex;
      
      // Schedule next keystroke
      timer = setTimeout(typeNext, speed);
    } else {
      // Done
      isTyping = false;
      if (onComplete) onComplete();
    }
  }
</script>

<div class="typewriter-container">
  <!-- pre-wrap preserves formatting from LLM/Markdown -->
  <div class="content">{@html displayedText}</div>
  
  {#if isTyping}
    <div class="indicator">
      <Spinner />
    </div>
  {/if}
</div>

<style>
  .typewriter-container {
    position: relative;
    font-family: var(--font-code);
    /* Changed from inline-block to block to ensure width consistency during typing */
    display: block; 
    width: 100%;
  }

  .content {
    white-space: pre-wrap;
    word-break: break-word;
    display: inline;
  }

  .indicator {
    display: inline-block;
    margin-left: 5px;
    vertical-align: middle;
  }
</style>