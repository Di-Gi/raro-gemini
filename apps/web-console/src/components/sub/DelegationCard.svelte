<!-- // [[RARO]]/apps/web-console/src/components/sub/DelegationCard.svelte -->
<script lang="ts">
  import { fade } from 'svelte/transition';
  import Spinner from './Spinner.svelte';

  let { rawJson, loading = false }: { rawJson: string, loading?: boolean } = $props();

  let data = $derived.by(() => {
    try {
        if (loading) return null;
        return JSON.parse(rawJson);
    } catch (e) {
        return null; // Invalid or incomplete JSON
    }
  });
</script>

<div class="delegation-card" transition:fade={{ duration: 200 }}>
  
  <!-- Header -->
  <div class="card-header">
    <div class="header-title">
        <span class="icon">⑃</span>
        <span>GRAPH MUTATION DETECTED</span>
    </div>
    
    {#if !loading && data}
      <div class="strategy-badge" transition:fade>
          STRATEGY: {data.strategy || 'CHILD'}
      </div>
    {/if}
  </div>

  <!-- Body -->
  <div class="card-body">
    
    {#if loading}
        <!-- LOADING STATE -->
        <div class="state-loading">
            <Spinner />
            <span>CALCULATING SHARD DELEGATION...</span>
        </div>
    {:else if data}
        <!-- DATA STATE -->
        <div class="section">
            <div class="label">REASONING</div>
            <div class="content reasoning">"{data.reason || 'No reason provided'}"</div>
        </div>

        <!-- [NEW] PRUNING BLOCK -->
        {#if data.prune_nodes && data.prune_nodes.length > 0}
            <div class="section">
                <div class="label" style="color: #d32f2f;">PRUNING NODES ({data.prune_nodes.length})</div>
                <div class="node-list">
                    {#each data.prune_nodes as nodeId}
                        <div class="node-chip prune">
                            <div class="chip-role">REMOVED</div>
                            <div class="chip-id strike">{nodeId}</div>
                        </div>
                    {/each}
                </div>
            </div>
        {/if}

        {#if data.new_nodes && Array.isArray(data.new_nodes)}
            <div class="section">
                <div class="label">INJECTING NODES ({data.new_nodes.length})</div>
                <div class="node-list">
                    {#each data.new_nodes as node}
                        <div class="node-chip">
                            <div class="chip-role">{node.role || 'WORKER'}</div>
                            <div class="chip-id">{node.id}</div>
                            <div class="chip-model">{node.model}</div>
                        </div>
                    {/each}
                </div>
            </div>
        {/if}
    {:else}
        <!-- ERROR / RAW STATE -->
        <div class="section">
             <div class="label" style="color: var(--alert-amber)">MALFORMED DELEGATION DATA</div>
             <div class="content raw">{rawJson}</div>
        </div>
    {/if}

  </div>

</div>

<style>
  .delegation-card {
    margin: 16px 0;
    border: 1px solid var(--arctic-lilac);
    background: color-mix(in srgb, var(--paper-bg), var(--arctic-lilac) 5%);
    border-radius: 2px;
    font-family: var(--font-code);
    overflow: hidden;
    box-shadow: 0 4px 12px rgba(113, 113, 242, 0.1);
  }

  .card-header {
    background: color-mix(in srgb, var(--paper-surface), var(--arctic-lilac) 10%);
    border-bottom: 1px solid var(--arctic-lilac);
    padding: 8px 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    height: 32px;
  }

  .header-title {
    color: var(--arctic-lilac);
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 1px;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .icon { font-size: 14px; line-height: 0; }

  .strategy-badge {
    font-size: 8px;
    background: var(--paper-bg);
    border: 1px solid var(--paper-line);
    padding: 2px 6px;
    border-radius: 2px;
    color: var(--paper-ink);
    text-transform: uppercase;
  }

  .card-body {
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    min-height: 60px; /* Prevent collapse during load */
    justify-content: center;
  }

  /* LOADING STATE */
  .state-loading {
    display: flex;
    align-items: center;
    gap: 12px;
    color: var(--paper-line);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    animation: pulse 1s infinite alternate;
  }

  .label {
    font-size: 8px;
    color: var(--paper-line);
    text-transform: uppercase;
    font-weight: 700;
    margin-bottom: 6px;
    letter-spacing: 0.5px;
  }

  .reasoning {
    font-size: 12px;
    color: var(--paper-ink);
    font-style: italic;
    line-height: 1.4;
    padding-left: 8px;
    border-left: 2px solid var(--paper-line);
  }

  .raw {
    font-size: 10px;
    opacity: 0.7;
    white-space: pre-wrap;
    word-break: break-all;
  }

  .node-list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .node-chip {
    display: flex;
    align-items: center;
    border: 1px solid var(--paper-line);
    background: var(--paper-surface);
    border-radius: 2px;
    overflow: hidden;
  }

  .chip-role {
    background: var(--paper-line);
    color: var(--paper-bg);
    font-size: 8px;
    padding: 4px 6px;
    text-transform: uppercase;
    font-weight: 700;
  }

  .chip-id {
    padding: 4px 8px;
    font-size: 10px;
    font-weight: 700;
    color: var(--paper-ink);
    border-right: 1px dashed var(--paper-line);
  }

  .chip-model {
    padding: 4px 8px;
    font-size: 9px;
    color: var(--paper-line);
  }

  /* Pruning-specific styles */
  .node-chip.prune {
    border-color: #d32f2f;
    opacity: 0.7;
  }

  .node-chip.prune .chip-role {
    background: #d32f2f;
    color: white;
  }

  .chip-id.strike {
    text-decoration: line-through;
    color: var(--paper-line);
  }

  @keyframes pulse { from { opacity: 0.6; } to { opacity: 1; } }
</style>

