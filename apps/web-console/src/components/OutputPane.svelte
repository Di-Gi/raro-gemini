<!-- apps/web-console/src/components/OutputPane.svelte -->
<script lang="ts">
  import { logs } from '$lib/stores'
  import Typewriter from './sub/Typewriter.svelte'

  let outputElement = $state<HTMLDivElement | null>(null);

  $effect(() => {
    const _ = $logs; // Dependency tracking
    if (outputElement) {
      // Small timeout to allow DOM to grow before scrolling
      setTimeout(() => {
         if (outputElement) outputElement.scrollTop = outputElement.scrollHeight;
      }, 50);
    }
  });
</script>

<div id="output-pane" bind:this={outputElement}>
  {#each $logs as log (log.id)}
    <div class="log-entry">
      <div class="log-meta">{log.metadata || 'SYSTEM'}</div>
      <div>
        <span class="log-role">{log.role}</span>
        
        <div class="log-content">
          {#if log.isAnimated}
            <!-- Use Typewriter for Agent Outputs -->
             <Typewriter text={log.message} />
          {:else}
            <!-- Standard Render for System Logs -->
            {@html log.message}
          {/if}
        </div>
        
      </div>
    </div>
  {/each}
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
    scroll-behavior: smooth;
  }
  
  .log-entry {
    border-top: 1px solid var(--paper-accent);
    padding: 12px 0;
    display: grid;
    grid-template-columns: 80px 1fr;
    gap: 16px;
    animation: slideUp 0.3s var(--ease-snap) forwards;
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