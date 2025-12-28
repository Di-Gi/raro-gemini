<script lang="ts">
  import { selectedNode, agentNodes, addLog, updateNodeStatus, deselectNode } from '$lib/stores'

  // 1. Props are now destuctured from $props()
  let { expanded }: { expanded: boolean } = $props();

  // 2. Local reactive state uses $state()
  let cmdInput = $state('')
  let activePane = $state('input')
  let currentModel = $state('GEMINI-3-PRO')
  let currentPrompt = $state('')

  // 3. Reactive statements ($:) are replaced by $effect()
  $effect(() => {
    if ($selectedNode && expanded) {
      const node = $agentNodes.find((n) => n.id === $selectedNode)
      if (node) {
        currentModel = node.model
        currentPrompt = node.prompt
      }
    }
  });

  function executeRun() {
    if (!cmdInput) return

    addLog('OPERATOR', `<strong>${cmdInput}</strong>`, 'USER_INPUT')
    cmdInput = ''

    if (!expanded) {
      highlightNode('n1')
      setTimeout(() => {
        highlightNode('n2')
        highlightNode('n3')
      }, 800)
      setTimeout(() => highlightNode('n4'), 1600)
      setTimeout(() => addLog('SYSTEM', 'Synthesis complete. 1284 tokens consumed.'), 2400)
    }
  }

  function highlightNode(id: string) {
    updateNodeStatus(id, 'running')
    setTimeout(() => updateNodeStatus(id, 'complete'), 600)
  }

  function handlePaneSelect(pane: string) {
    activePane = pane
  }
</script>

<div id="control-deck" class:architect-mode={expanded}>
  {#if expanded}
    <div id="deck-nav">
      <!-- 4. Event listeners are now attributes (onclick instead of on:click) -->
      <div class="nav-item {activePane === 'overview' ? 'active' : ''}" onclick={() => handlePaneSelect('overview')}>
        Overview
      </div>
      <div class="nav-item {activePane === 'sim' ? 'active' : ''}" onclick={() => handlePaneSelect('sim')}>Simulation</div>
      <div class="nav-item {activePane === 'stats' ? 'active' : ''}" onclick={() => handlePaneSelect('stats')}>
        Telemetry
      </div>
      {#if $selectedNode}
        <div class="nav-item-label">COMPONENT SETTINGS</div>
        <div class="nav-item action-close" onclick={() => deselectNode()}>×</div>
      {/if}
    </div>
  {/if}

  {#if !expanded || activePane === 'input'}
    <div id="pane-input" class="deck-pane">
      <textarea
        id="cmd-input"
        placeholder=">> Enter research directive or click pipeline to configure..."
        bind:value={cmdInput}
      ></textarea>
      <button id="btn-run" onclick={executeRun}>INITIATE RUN</button>
    </div>
  {/if}

  {#if expanded && activePane === 'overview'}
    <div id="pane-overview" class="deck-pane">
      <div class="form-grid">
        <div class="form-group">
          <label>Pipeline Identifier</label>
          <input class="input-std" value="Research_Synthesis_Alpha" />
        </div>
        <div class="form-group">
          <label>Max Token Budget</label>
          <input class="input-std" value="128,000" />
        </div>
        <div class="form-group">
          <label>Latency Timeout (ms)</label>
          <input class="input-std" value="15000" />
        </div>
        <div class="form-group">
          <label>Persistence Layer</label>
          <select class="input-std">
            <option>Redis (Hot)</option>
            <option>PostgreSQL (Cold)</option>
          </select>
        </div>
      </div>
    </div>
  {/if}

  {#if expanded && activePane === 'sim'}
    <div id="pane-sim" class="deck-pane">
      <div style="display:flex; gap:10px; margin-bottom:15px;">
        <button class="input-std action-btn" onclick={() => addLog('SYSTEM', 'Simulating step 1...')}
          >▶ STEP EXECUTION</button
        >
        <button class="input-std action-btn" onclick={() => addLog('SYSTEM', 'Resetting context...')}>
          ↺ RESET CONTEXT
        </button>
      </div>
      <div style="font-family:var(--font-code); font-size:11px; color:#555; background:white; border:1px solid var(--paper-line); padding:10px; height:100px; overflow-y:auto;">
        > Ready for test vector injection...<br />
        > Agents loaded: 4
      </div>
    </div>
  {/if}

  {#if expanded && activePane === 'stats'}
    <div id="pane-stats" class="deck-pane">
      <div class="stat-grid">
        <div class="stat-card">
          <span class="stat-val">98ms</span>
          <span class="stat-lbl">P99 Latency</span>
        </div>
        <div class="stat-card">
          <span class="stat-val">94.2%</span>
          <span class="stat-lbl">Cache Hit</span>
        </div>
        <div class="stat-card">
          <span class="stat-val">$0.002</span>
          <span class="stat-lbl">Cost/Run</span>
        </div>
        <div class="stat-card">
          <span class="stat-val">0</span>
          <span class="stat-lbl">Errors</span>
        </div>
      </div>
    </div>
  {/if}

  {#if expanded && $selectedNode}
    <div id="pane-node-config" class="deck-pane">
      <div class="form-grid">
        <div class="form-group">
          <label>Agent ID</label>
          <input class="input-std input-readonly" value={$selectedNode} readonly />
        </div>
        <div class="form-group">
          <label>Model Runtime</label>
          <select class="input-std" bind:value={currentModel}>
            <option>GEMINI-3-PRO</option>
            <option>GEMINI-3-FLASH</option>
            <option>GEMINI-3-DEEP-THINK</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>System Instruction (Prompt)</label>
        <textarea class="input-std" bind:value={currentPrompt} style="height:80px; resize:none;"></textarea>
      </div>
    </div>
  {/if}
</div>

<style>
  #control-deck {
    height: 160px;
    background: var(--paper-bg);
    border-top: 1px solid var(--paper-line);
    display: flex;
    flex-direction: column;
    transition: height 0.5s var(--ease-snap);
    position: relative;
    z-index: 150;
  }

  #control-deck.architect-mode {
    height: 260px;
  }

  #deck-nav {
    height: 36px;
    background: var(--paper-surface);
    border-bottom: 1px solid var(--paper-line);
    display: flex;
  }

  .nav-item {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #888;
    cursor: pointer;
    border-right: 1px solid var(--paper-line);
    transition: all 0.2s;
  }

  .nav-item:hover {
    color: var(--paper-ink);
    background: white;
  }

  .nav-item.active {
    background: var(--paper-bg);
    color: var(--paper-ink);
    border-bottom: 2px solid var(--paper-ink);
  }

  .nav-item-label {
    flex: 4;
    display: flex;
    align-items: center;
    padding-left: 20px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--paper-ink);
    border-right: none;
  }

  .action-close {
    max-width: 48px;
    color: #d32f2f;
    border-right: none;
    border-left: 1px solid var(--paper-line);
    flex: 0;
  }

  .deck-pane {
    display: none;
    height: 100%;
    padding: 20px;
    overflow-y: auto;
  }

  .deck-pane:not(#pane-input) {
    display: block;
  }

  #pane-input {
    display: flex;
    flex-direction: column;
    padding: 0;
  }

  #cmd-input {
    flex: 1;
    border: none;
    background: transparent;
    padding: 20px;
    font-family: var(--font-code);
    font-size: 13px;
    color: var(--paper-ink);
    resize: none;
    outline: none;
  }

  #btn-run {
    height: 48px;
    border: none;
    border-top: 1px solid var(--paper-line);
    background: white;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 1px;
    cursor: pointer;
    transition: background 0.2s;
    color: var(--paper-ink);
  }

  #btn-run:hover {
    background: #f5f5f5;
  }

  .form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }

  .form-group {
    margin-bottom: 16px;
  }

  label {
    display: block;
    font-size: 9px;
    color: #888;
    text-transform: uppercase;
    margin-bottom: 6px;
    font-weight: 600;
  }

  .input-std {
    width: 100%;
    padding: 10px;
    border: 1px solid var(--paper-line);
    background: white;
    font-family: var(--font-code);
    font-size: 12px;
    color: var(--paper-ink);
    outline: none;
  }

  .input-std:focus {
    border-color: var(--paper-ink);
  }

  .input-readonly {
    background: var(--paper-surface);
    color: #666;
    cursor: default;
  }

  .action-btn {
    width: auto;
    cursor: pointer;
    background: #1a1918;
    color: white;
  }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
  }

  .stat-card {
    border: 1px solid var(--paper-line);
    background: white;
    padding: 12px;
    text-align: center;
  }

  .stat-val {
    font-size: 16px;
    font-weight: 700;
    color: var(--paper-ink);
    display: block;
  }

  .stat-lbl {
    font-size: 9px;
    color: #888;
    text-transform: uppercase;
    margin-top: 4px;
    display: block;
  }
</style>
