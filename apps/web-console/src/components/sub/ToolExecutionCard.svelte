<!-- [[RARO]]/apps/web-console/src/components/sub/ToolExecutionCard.svelte -->
<!-- Purpose: Display live tool execution logs with proper formatting for calls, results, and errors -->

<script lang="ts">
  import { fade } from 'svelte/transition';
  import Spinner from './Spinner.svelte';

  let {
    category,
    message,
    metadata,
    agentId
  }: {
    category: 'TOOL_CALL' | 'TOOL_RESULT',
    message: string,
    metadata: string,
    agentId: string
  } = $props();

  // Parse tool name from message (e.g., "web_search(...)")
  let toolName = $derived.by(() => {
    const match = message.match(/^([a-z_]+)\(/);
    return match ? match[1] : 'unknown';
  });

  // Extract args preview (truncated)
  let argsPreview = $derived.by(() => {
    const match = message.match(/\((.+)\)/);
    return match ? match[1] : '';
  });

  // Check if this is an error result
  let isError = $derived(metadata === 'IO_ERR');

  // Check if message contains traceback (for expandable display)
  let hasTraceback = $derived(message.includes('Traceback') || message.includes('Error:') || message.includes('---'));

  // State for expandable traceback
  let expanded = $state(false);

  // Smart truncation for long messages
  let displayMessage = $derived.by(() => {
    if (category === 'TOOL_RESULT' && !expanded && message.length > 200 && hasTraceback) {
      // Show first line + indicator
      const firstLine = message.split('\n')[0];
      return firstLine + '...';
    }
    return message;
  });
</script>

<div
  class="tool-card {category.toLowerCase()} {isError ? 'error' : 'success'}"
  transition:fade={{ duration: 150 }}
>

  {#if category === 'TOOL_CALL'}
    <!-- TOOL CALL: Request initiated -->
    <div class="call-row">
      <div class="call-meta">
        <span class="agent-badge">{agentId}</span>
        <span class="arrow">→</span>
      </div>

      <div class="call-content">
        <span class="tool-name">{toolName}</span>
        <span class="tool-args">({argsPreview})</span>
      </div>

      <div class="call-status">
        <Spinner />
        <span class="status-text">EXECUTING</span>
      </div>
    </div>

  {:else if category === 'TOOL_RESULT'}
    <!-- TOOL RESULT: Response received -->
    <div class="result-row">
      <div class="result-meta">
        <span class="connector">↳</span>
        <span class="status-icon {isError ? 'err' : 'ok'}">{isError ? '✕' : '✓'}</span>
      </div>

      <div class="result-content">
        <div class="result-message {isError ? 'error-text' : 'success-text'}">
          {#if isError && hasTraceback}
            <!-- Error with traceback: Make expandable -->
            <div class="error-summary">
              <code class="error-code">{displayMessage}</code>
            </div>

            {#if message.length > 200}
              <button class="expand-btn" onclick={() => expanded = !expanded}>
                {expanded ? '▼ COLLAPSE' : '▶ VIEW FULL TRACEBACK'}
              </button>
            {/if}

            {#if expanded}
              <div class="traceback-block" transition:fade>
                <pre class="traceback-text">{message}</pre>
              </div>
            {/if}
          {:else}
            <!-- Normal result -->
            <span class="result-text">{message}</span>
          {/if}
        </div>

        <div class="result-tag">{metadata}</div>
      </div>
    </div>
  {/if}

</div>

<style>
  /* === CARD CONTAINER === */
  .tool-card {
    margin: 8px 0;
    font-family: var(--font-code);
    font-size: 11px;
    border-radius: 2px;
    overflow: hidden;
  }

  /* TOOL_CALL styling */
  .tool-card.tool_call {
    background: color-mix(in srgb, var(--paper-bg), var(--alert-amber) 3%);
    border-left: 3px solid var(--alert-amber);
    padding: 10px 12px;
  }

  /* TOOL_RESULT styling */
  .tool-card.tool_result {
    padding: 10px 12px 10px 24px; /* Extra left padding for indent */
  }

  .tool-card.tool_result.success {
    background: color-mix(in srgb, var(--paper-bg), var(--signal-success) 3%);
    border-left: 3px solid var(--signal-success);
  }

  .tool-card.tool_result.error {
    background: color-mix(in srgb, var(--paper-bg), #d32f2f 3%);
    border-left: 3px solid #d32f2f;
  }

  /* === CALL ROW === */
  .call-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .call-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .agent-badge {
    font-size: 9px;
    font-weight: 700;
    color: var(--paper-line);
    background: var(--paper-surface);
    padding: 2px 6px;
    border-radius: 2px;
  }

  .arrow {
    color: var(--alert-amber);
    font-size: 14px;
    font-weight: 700;
  }

  .call-content {
    flex: 1;
    display: flex;
    align-items: baseline;
    gap: 4px;
    overflow: hidden;
  }

  .tool-name {
    color: var(--arctic-cyan);
    font-weight: 700;
    flex-shrink: 0;
  }

  .tool-args {
    color: var(--paper-ink);
    opacity: 0.7;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .call-status {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .status-text {
    font-size: 9px;
    color: var(--paper-line);
    font-weight: 700;
    letter-spacing: 0.5px;
    animation: pulse 1.5s infinite;
  }

  /* === RESULT ROW === */
  .result-row {
    display: flex;
    gap: 10px;
  }

  .result-meta {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    flex-shrink: 0;
    padding-top: 2px;
  }

  .connector {
    color: var(--paper-line);
    font-size: 14px;
    opacity: 0.5;
  }

  .status-icon {
    font-size: 11px;
    font-weight: 900;
  }

  .status-icon.ok {
    color: var(--signal-success);
  }

  .status-icon.err {
    color: #d32f2f;
  }

  .result-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .result-message {
    line-height: 1.4;
  }

  .success-text {
    color: var(--paper-ink);
  }

  .error-text {
    color: #d32f2f;
  }

  .result-text {
    opacity: 0.9;
  }

  .result-tag {
    font-size: 8px;
    font-weight: 700;
    color: var(--paper-line);
    background: var(--paper-surface);
    padding: 2px 6px;
    border-radius: 2px;
    align-self: flex-start;
    letter-spacing: 0.5px;
  }

  /* === ERROR HANDLING === */
  .error-summary {
    margin-bottom: 8px;
  }

  .error-code {
    font-family: var(--font-code);
    font-size: 10px;
    color: #d32f2f;
    background: color-mix(in srgb, var(--paper-bg), #d32f2f 8%);
    padding: 4px 8px;
    border-radius: 2px;
    display: inline-block;
    max-width: 100%;
    overflow-wrap: break-word;
  }

  .expand-btn {
    background: transparent;
    border: 1px solid var(--paper-line);
    color: var(--paper-ink);
    font-family: var(--font-code);
    font-size: 9px;
    font-weight: 700;
    padding: 4px 8px;
    cursor: pointer;
    border-radius: 2px;
    transition: all 0.2s;
    margin-top: 8px;
  }

  .expand-btn:hover {
    background: var(--paper-surface);
    border-color: var(--paper-ink);
  }

  .traceback-block {
    margin-top: 8px;
    background: var(--paper-surface);
    border: 1px solid var(--paper-line);
    border-radius: 2px;
    overflow: hidden;
  }

  .traceback-text {
    font-family: var(--font-code);
    font-size: 9px;
    color: #d32f2f;
    padding: 12px;
    margin: 0;
    overflow-x: auto;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
  }

  /* === ANIMATIONS === */
  @keyframes pulse {
    0%, 100% { opacity: 0.6; }
    50% { opacity: 1; }
  }

  /* === THEME OVERRIDES === */
  :global(.mode-phosphor) .tool-name {
    color: #00ff66;
  }

  :global(.mode-archival) .tool-name {
    color: #005cc5;
  }
</style>
