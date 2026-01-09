<!-- [[RARO]]/apps/web-console/src/components/sub/ToolExecutionCard.svelte -->
<script lang="ts">
  import { fade, slide } from 'svelte/transition';
  import Spinner from './Spinner.svelte';

  // DEFINITION: Explicitly define the props interface
  let {
    category,
    message,
    metadata,
    agentId,
    // New Props for "Merged" state
    isComplete = false,
    toolResult = null,
    toolStatus = 'success'
  }: {
    category: string,
    message: string,
    metadata: string,
    agentId: string,
    isComplete?: boolean,
    toolResult?: string | null,
    toolStatus?: 'success' | 'error'
  } = $props();

  // Parsing logic for the "Call" part (always present in 'message')
  let toolName = $derived.by(() => {
    const match = message.match(/^([a-z_]+)\(/);
    return match ? match[1] : 'unknown';
  });

  let argsPreview = $derived.by(() => {
    const match = message.match(/\(([\s\S]*)\)$/);
    if (!match) return '';
    const args = match[1];
    return args.length > 60 ? args.substring(0, 60) + '...' : args;
  });

  // State
  let isExpanded = $state(false);
  let isError = $derived(toolStatus === 'error' || metadata === 'IO_ERR');

  // Auto-expand on error to show traceback
  $effect(() => {
    if (isError) isExpanded = true;
  });
</script>

<div 
  class="tool-card {isComplete ? 'complete' : 'executing'} {isError ? 'error' : ''}"
  transition:fade={{ duration: 150 }}
>
  <!-- HEADER -->
  <div 
    class="card-header" 
    onclick={() => { if(isComplete) isExpanded = !isExpanded; }}
    role="button"
    tabindex="0"
    onkeydown={(e) => e.key === 'Enter' && isComplete && (isExpanded = !isExpanded)}
  >
    <div class="header-main">
        <div class="meta-badges">
            <span class="agent-badge">{agentId}</span>
            <span class="arrow">→</span>
            <span class="tool-name">{toolName}</span>
        </div>
        {#if !isExpanded}
            <span class="args-preview" transition:fade>({argsPreview})</span>
        {/if}
    </div>

    <div class="header-status">
        {#if !isComplete}
            <!-- STATE: EXECUTING -->
            <div class="status-active">
                <Spinner />
                <span class="status-text">EXECUTING</span>
            </div>
        {:else}
            <!-- STATE: DONE -->
            <div class="status-done" class:err={isError}>
                {#if isError}
                    <span class="icon">✕</span> FAILED
                {:else}
                    <span class="icon">✓</span> DONE
                {/if}
            </div>
            <div class="chevron {isExpanded ? 'up' : 'down'}">▼</div>
        {/if}
    </div>
  </div>

  <!-- BODY: Expanded Content -->
  {#if isExpanded}
    <div class="card-body" transition:slide={{ duration: 200 }}>
        <!-- Input Arguments -->
        <div class="section">
            <div class="label">INPUT_PAYLOAD</div>
            <div class="code-block input">{message}</div>
        </div>

        <!-- Output Result -->
        {#if isComplete && toolResult}
            <div class="section result-section" class:err-section={isError}>
                <div class="label">{isError ? 'ERROR_TRACE' : 'OUTPUT_DATA'}</div>
                <div class="code-block output {isError ? 'text-err' : 'text-ok'}">
                    {toolResult}
                </div>
            </div>
        {/if}
    </div>
  {/if}
</div>

<style>
  .tool-card { margin: 8px 0; font-family: var(--font-code); font-size: 11px; border: 1px solid var(--paper-line); background: var(--paper-bg); border-radius: 2px; overflow: hidden; transition: all 0.3s; }
  .tool-card.executing { border-left: 3px solid var(--alert-amber); background: color-mix(in srgb, var(--paper-bg), var(--alert-amber) 2%); }
  .tool-card.complete { border-left: 3px solid var(--paper-line); }
  .tool-card.complete.error { border-left: 3px solid #d32f2f; border-color: #d32f2f; background: color-mix(in srgb, var(--paper-bg), #d32f2f 3%); }
  
  .card-header { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: var(--paper-surface); cursor: default; user-select: none; }
  .tool-card.complete .card-header { cursor: pointer; }
  .tool-card.complete .card-header:hover { background: color-mix(in srgb, var(--paper-surface), var(--paper-ink) 5%); }

  .header-main { display: flex; align-items: center; gap: 8px; flex: 1; overflow: hidden; }
  .meta-badges { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
  .agent-badge { font-size: 9px; font-weight: 700; color: var(--paper-ink); background: var(--paper-bg); padding: 2px 6px; border-radius: 2px; border: 1px solid var(--paper-line); }
  .arrow { color: var(--paper-line); font-size: 10px; }
  .tool-name { color: var(--arctic-cyan); font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
  :global(.mode-phosphor) .tool-name { color: #00ff66; }
  :global(.mode-archival) .tool-name { color: #005cc5; }

  .args-preview { color: var(--paper-line); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 10px; }

  .header-status { display: flex; align-items: center; gap: 8px; }
  .status-active { display: flex; align-items: center; gap: 6px; color: var(--paper-line); }
  .status-text { font-size: 9px; font-weight: 700; letter-spacing: 0.5px; animation: pulse 1s infinite; }
  .status-done { display: flex; align-items: center; gap: 4px; font-size: 9px; font-weight: 700; color: var(--signal-success); background: color-mix(in srgb, var(--signal-success), transparent 90%); padding: 2px 6px; border-radius: 2px; }
  .status-done.err { color: #d32f2f; background: color-mix(in srgb, #d32f2f, transparent 90%); }
  .chevron { font-size: 8px; color: var(--paper-line); transition: transform 0.2s; }
  .chevron.up { transform: rotate(180deg); }

  .card-body { padding: 12px; border-top: 1px solid var(--paper-line); background: var(--paper-bg); display: flex; flex-direction: column; gap: 12px; }
  .section { display: flex; flex-direction: column; gap: 4px; }
  .label { font-size: 8px; font-weight: 700; color: var(--paper-line); text-transform: uppercase; letter-spacing: 0.5px; }
  .code-block { background: var(--paper-surface); padding: 8px; border-radius: 2px; font-family: var(--font-code); font-size: 10px; white-space: pre-wrap; word-break: break-all; line-height: 1.4; border: 1px solid transparent; }
  .code-block.input { color: var(--paper-line); }
  .code-block.output { color: var(--paper-ink); border-color: var(--paper-line); }
  .result-section.err-section .code-block { background: color-mix(in srgb, #d32f2f, transparent 95%); border-color: #d32f2f; color: #d32f2f; }

  @keyframes pulse { 50% { opacity: 0.5; } }
</style>