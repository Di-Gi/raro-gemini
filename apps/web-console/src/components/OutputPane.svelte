<!-- // [[RARO]]/apps/web-console/src/components/OutputPane.svelte
// Purpose: Log display with "Perforated Paper" styling and robust auto-scroll.
// Architecture: UI View
// Dependencies: Typewriter, Stores -->

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
        <!-- Column 1: Metadata -->
        <div class="log-meta">
            <span class="meta-tag">{log.metadata || 'SYSTEM'}</span>
        </div>

        <!-- Column 2: Content -->
        <div class="log-body">
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
  /* Error Block Styling - Global for HTML injection */
  :global(.error-block) {
    background: rgba(211, 47, 47, 0.05); /* Subtle red tint that works on light and dark */
    border-left: 3px solid #d32f2f;      /* Semantic Red - kept constant */
    color: var(--paper-ink);             /* Adaptive text color */
    padding: 10px;
    margin-top: 8px;
    font-family: var(--font-code);
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-all;
  }

  :global(.log-content strong) {
    color: var(--paper-ink);
    font-weight: 700;
  }

  #output-pane {
    flex: 1;
    padding: 24px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    /* Important: Remove CSS scroll-behavior to allow JS to control 'auto' vs 'smooth' explicitly */
    scrollbar-gutter: stable;
    will-change: scroll-position;
  }

  .log-wrapper {
    display: flex;
    flex-direction: column;
    min-height: min-content;
  }
  
  .log-entry {
    /* "Perforated" divider style */
    border-top: 1px dashed var(--paper-line);
    padding: 16px 0;
    display: grid;
    grid-template-columns: 80px 1fr;
    gap: 16px;
    animation: slideUp 0.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  }

  @keyframes slideUp {
    from { opacity: 0; transform: translateY(5px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .log-meta {
    padding-top: 3px;
  }

  .meta-tag {
    font-family: var(--font-code);
    font-size: 9px;
    color: var(--paper-line); /* Replaced #888 */
    background: var(--paper-surface); /* Replaced #f5f5f5 */
    padding: 2px 6px;
    border-radius: 2px;
    display: inline-block;
    border: 1px solid transparent;
  }
  
  /* In dark mode, we might want a slight border to define the tag */
  :global(.mode-phosphor) .meta-tag {
      border-color: var(--paper-line);
  }

  .log-body {
    display: flex;
    flex-direction: column;
  }

  .log-role {
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.5px;
    color: var(--paper-ink);
    display: block;
    margin-bottom: 6px;
    text-transform: uppercase;
  }

  .log-content {
    font-size: 13px;
    line-height: 1.6;
    color: var(--paper-ink); /* Replaced #333 */
    opacity: 0.9;
  }
</style>