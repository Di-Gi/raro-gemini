<!-- apps/web-console/src/components/sub/Typewriter.svelte -->
<script lang="ts">
  import Spinner from './Spinner.svelte';

  let { text, onComplete }: { text: string, onComplete?: () => void } = $props();

  let displayedText = $state('');
  let isTyping = $state(true);
  
  // Calculate typing speed based on length. Cap at 2ms for long texts.
  let speed = text.length > 500 ? 1 : 15; 
  let charIndex = 0;
  let timer: any;

  $effect(() => {
    // Only restart if text content actually changes and we haven't started this specific text
    if (text && text !== displayedText && charIndex === 0) {
      isTyping = true;
      startTyping();
    }
  });

  function startTyping() {
    clearTimeout(timer);
    
    const type = () => {
      if (charIndex < text.length) {
        // Chunking ensures large texts render quickly without blocking UI
        const chunk = text.length > 2000 ? 20 : (text.length > 500 ? 5 : 1);
        
        // Safety: Ensure we don't slice past end
        const nextIndex = Math.min(charIndex + chunk, text.length);
        displayedText += text.substring(charIndex, nextIndex);
        charIndex = nextIndex;
        
        timer = setTimeout(type, speed);
      } else {
        isTyping = false;
        displayedText = text; // Ensure consistency
        if (onComplete) onComplete();
      }
    };
    type();
  }
  
  // Cleanup on destroy
  $effect(() => {
      return () => clearTimeout(timer);
  });
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
    font-family: var(--font-code); /* Ensure monospace for code outputs */
  }

  .content {
    white-space: pre-wrap;
    word-break: break-word;
    display: inline;
  }

  .indicator {
    display: inline-block;
    margin-left: 5px;
    vertical-align: text-bottom;
  }
</style>