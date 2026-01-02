<!-- apps/web-console/src/components/OutputPane.svelte -->
<script lang="ts">
  import { logs } from '$lib/stores'
  import Typewriter from './sub/Typewriter.svelte'
  import { tick } from 'svelte';

  // Refs
  let scrollContainer = $state<HTMLDivElement | null>(null);
  let contentWrapper = $state<HTMLDivElement | null>(null);
  
  // State
  let isPinnedToBottom = $state(true);
  
  // Internal flag to ignore scroll events triggered by auto-scroll
  let isAutoScrolling = false;

  // 1. Handle User Scroll
  function handleScroll() {
    if (!scrollContainer) return;
    
    // If this scroll event was caused by our code, ignore it.
    if (isAutoScrolling) return;

    const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
    
    // Threshold (px). 
    // We use a larger buffer (50px) to account for mobile browsers or scaling issues.
    const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
    
    // Update pin state based on USER position
    isPinnedToBottom = distanceFromBottom < 50;
  }

  // 2. Robust Scroll Helper
  function scrollToBottom(behavior: ScrollBehavior = 'auto') {
    if (!scrollContainer) return;

    // Set lock
    isAutoScrolling = true;

    try {
      scrollContainer.scrollTo({
        top: scrollContainer.scrollHeight,
        behavior
      });
    } finally {
      // Release lock after a frame to ensure the 'scroll' event has fired/processed
      requestAnimationFrame(() => {
        isAutoScrolling = false;
      });
    }
  }

  // 3. Observer for Growing Content (Typewriter Effect)
  $effect(() => {
    if (!contentWrapper) return;

    const observer = new ResizeObserver(() => {
      // Only snap to bottom if we were already pinned
      if (isPinnedToBottom) {
        // MUST use 'auto' here. 'smooth' is too slow for typing effects and causes jitter.
        scrollToBottom('auto'); 
      }
    });

    observer.observe(contentWrapper);
    return () => observer.disconnect();
  });

  // 4. Observer for New Log Entries (Block addition)
  $effect(() => {
    // Track dependency
    const _logs = $logs;

    // Wait for DOM update, then scroll
    tick().then(() => {
      if (isPinnedToBottom) {
        // For new blocks, smooth scrolling is nice UX
        scrollToBottom('smooth');
      }
    });
  });
</script>

<div 
  id="output-pane" 
  bind:this={scrollContainer} 
  onscroll={handleScroll}
>
  <div class="log-wrapper" bind:this={contentWrapper}>
    {#each $logs as log (log.id)}
      <div class="log-entry">
        <div class="log-meta">{log.metadata || 'SYSTEM'}</div>
        <div>
          <span class="log-role">{log.role}</span>
          
          <div class="log-content">
            {#if log.isAnimated}
              <!-- Typewriter updates internal text, resizing contentWrapper, triggering ResizeObserver -->
              <Typewriter text={log.message} />
            {:else}
              {@html log.message}
            {/if}
          </div>
          
        </div>
      </div>
    {/each}
  </div>
</div>

<style>
  :global(.error-block) {
    background: #fff5f5;
    border: 1px solid #ffcdd2;
    color: #c62828;
    padding: 8px;
    margin-top: 4px;
    border-radius: 2px;
    font-family: var(--font-code);
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-all;
  }

  #output-pane {
    flex: 1;
    padding: 24px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    /* Important: Remove CSS scroll-behavior to allow JS to control 'auto' vs 'smooth' explicitly */
    /* scroll-behavior: smooth; <--- REMOVED */
    scrollbar-gutter: stable;
    will-change: scroll-position;
  }

  .log-wrapper {
    display: flex;
    flex-direction: column;
    min-height: min-content;
  }
  
  .log-entry {
    border-top: 1px solid var(--paper-accent);
    padding: 12px 0;
    display: grid;
    grid-template-columns: 80px 1fr;
    gap: 16px;
    /* Reduced animation duration for snappier feel */
    animation: slideUp 0.2s var(--ease-snap) forwards;
  }

  @keyframes slideUp {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .log-meta {
    font-family: var(--font-code);
    font-size: 10px;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding-top: 2px;
  }

  .log-role {
    font-weight: 700;
    color: var(--paper-ink);
    display: block;
    margin-bottom: 4px;
  }

  .log-content {
    font-size: 13px;
    line-height: 1.5;
    color: #333;
  }

  :global(.log-content strong) {
    color: #000;
    font-weight: 600;
  }
</style>