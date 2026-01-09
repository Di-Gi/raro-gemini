<!-- // [[RARO]]/apps/web-console/src/components/OutputPane.svelte -->
<script lang="ts">
  import { logs, updateLog, runtimeStore } from '$lib/stores'
  import Typewriter from './sub/Typewriter.svelte'
  import SmartText from './sub/SmartText.svelte'
  import ApprovalCard from './sub/ApprovalCard.svelte'
  import ArtifactCard from './sub/ArtifactCard.svelte'
  import ToolExecutionCard from './sub/ToolExecutionCard.svelte' // NEW: Live tool logs
  import { tick } from 'svelte';

  // Refs
  let scrollContainer = $state<HTMLDivElement | null>(null);
  let contentWrapper = $state<HTMLDivElement | null>(null);
  
  // State
  let isPinnedToBottom = $state(true);
  let isAutoScrolling = false;

  function handleScroll() {
    if (!scrollContainer) return;
    if (isAutoScrolling) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
    const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
    isPinnedToBottom = distanceFromBottom < 50;
  }

  function scrollToBottom(behavior: ScrollBehavior = 'auto') {
    if (!scrollContainer) return;
    isAutoScrolling = true;
    try {
      scrollContainer.scrollTo({ top: scrollContainer.scrollHeight, behavior });
    } finally {
      requestAnimationFrame(() => { isAutoScrolling = false; });
    }
  }

  $effect(() => {
    if (!contentWrapper) return;
    const observer = new ResizeObserver(() => {
      if (isPinnedToBottom) scrollToBottom('auto'); 
    });
    observer.observe(contentWrapper);
    return () => observer.disconnect();
  });

  $effect(() => {
    const _logs = $logs;
    tick().then(() => {
      if (isPinnedToBottom) scrollToBottom('smooth');
    });
  });

  // Callback for Typewriter completion
  function handleTypewriterComplete(id: string) {
    updateLog(id, { isAnimated: false });
  }

  /**
   * Detects if a log message contains a generated image reference.
   * Supports:
   * 1. Specific System Tag from tools.py: [SYSTEM: Generated Image saved to '...']
   * 2. Standard Markdown: ![...](...)
   */
  function extractImageFilename(msg: string): string | null {
      // Regex 1: System Tag
      const sysMatch = msg.match(/\[SYSTEM: Generated Image saved to '([^']+)'\]/);
      if (sysMatch) return sysMatch[1];

      // Regex 2: Markdown
      const mdMatch = msg.match(/!\[.*?\]\(([^)]+\.(?:png|jpg|jpeg|svg))\)/i);
      if (mdMatch) return mdMatch[1];

      return null;
  }

  /**
   * Removes markdown image syntax from text to avoid duplicate rendering.
   * The ArtifactCard will handle displaying the image properly.
   */
  function stripMarkdownImage(msg: string): string {
      // Remove markdown image syntax: ![alt](filename.png)
      return msg.replace(/!\[.*?\]\([^)]+\.(?:png|jpg|jpeg|svg)\)/gi, '').trim();
  }
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
            {#if log.category === 'TOOL_CALL' || log.category === 'TOOL_RESULT'}
              <!-- TOOL EXECUTION: Live telemetry -->
              <ToolExecutionCard
                category={log.category}
                message={log.message}
                metadata={log.metadata || 'INFO'}
                agentId={log.role}
              />
            {:else if log.metadata === 'INTERVENTION'}
              <!-- HITL: Approval Card -->
              <ApprovalCard
                reason={log.message === 'SAFETY_PATTERN_TRIGGERED' ? "System Policy Violation or Manual Pause Triggered" : log.message}
                runId={$runtimeStore.runId || ''}
              />
            {:else if log.isAnimated}
              <!-- ANIMATING: Typewriter -->
              <Typewriter
                text={log.message}
                onComplete={() => handleTypewriterComplete(log.id)}
              />
            {:else}
              <!-- STATIC: Smart Text + Artifact Detection -->

              {@const imageFilename = extractImageFilename(log.message)}

              <!-- 1. Render text content (strip markdown image if present) -->
              <SmartText text={imageFilename ? stripMarkdownImage(log.message) : log.message} />

              <!-- 2. If an image is detected, append the Artifact Card -->
              {#if imageFilename}
                 <ArtifactCard
                    filename={imageFilename}
                    runId={$runtimeStore.runId || ''}
                 />
              {/if}
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
    background: rgba(211, 47, 47, 0.05);
    border-left: 3px solid #d32f2f;
    color: var(--paper-ink);
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
    scrollbar-gutter: stable;
    will-change: scroll-position;
  }

  .log-wrapper {
    display: flex;
    flex-direction: column;
    min-height: min-content;
  }
  
  .log-entry {
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
    color: var(--paper-line);
    background: var(--paper-surface);
    padding: 2px 6px;
    border-radius: 2px;
    display: inline-block;
    border: 1px solid transparent;
  }
  
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
    color: var(--paper-ink);
    opacity: 0.9;
  }
</style>