<script lang="ts">
  import { logs } from '$lib/stores'
  // 1. onMount is often unnecessary with $effect, but you can keep it if preferred.
  // However, $effect handles both the initial mount and subsequent updates.

  // 2. Use $state for element bindings
  let outputElement = $state<HTMLDivElement | null>(null);

  // 3. Use $effect to handle the side-effect of scrolling
  $effect(() => {
    // By referencing $logs inside this function, Svelte 5 
    // automatically re-runs this effect whenever logs change.
    const _currentLogs = $logs; 

    if (outputElement) {
      outputElement.scrollTop = outputElement.scrollHeight;
    }
  });
</script>

<div id="output-pane" bind:this={outputElement}>
  {#each $logs as log (log.timestamp)}
    <!-- 
      Note: Removed 'in:slideUp' because slideUp is defined as CSS below. 
      Svelte 'in:' directives expect a JavaScript transition function. 
      The CSS animation in your <style> block will handle the entrance automatically.
    -->
    <div class="log-entry">
      <div class="log-meta">{log.metadata || 'SYSTEM'}</div>
      <div>
        <span class="log-role">{log.role}</span>
        <div class="log-content">{@html log.message}</div>
      </div>
    </div>
  {/each}
</div>

<style>
  #output-pane {
    flex: 1;
    padding: 24px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    /* justify-content: flex-end; */ /* Removed this to allow scrolling to work properly */
    scroll-behavior: smooth;
  }

  .log-entry {
    border-top: 1px solid var(--paper-accent);
    padding: 12px 0;
    display: grid;
    grid-template-columns: 80px 1fr;
    gap: 16px;
    /* This CSS animation handles the entry effect without needing in:slideUp */
    animation: slideUp 0.3s var(--ease-snap) forwards;
  }

  @keyframes slideUp {
    from {
      opacity: 0;
      transform: translateY(10px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  /* ... rest of your CSS stays the same ... */
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
    margin-bottom: 2px;
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