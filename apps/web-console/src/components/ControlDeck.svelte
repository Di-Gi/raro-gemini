<!-- // [[RARO]]/apps/web-console/src/components/ControlDeck.svelte
// Purpose: Main interaction panel. Orchestrates the API call to start the run.
// Architecture: View Controller
// Dependencies: Stores, API -->

<script lang="ts">
  import { selectedNode, agentNodes, pipelineEdges, addLog, updateNodeStatus, deselectNode, telemetry, connectRuntimeWebSocket } from '$lib/stores'
  import { startRun, type WorkflowConfig, type AgentConfig } from '$lib/api'
  import { get } from 'svelte/store'

  let { expanded }: { expanded: boolean } = $props();

  let cmdInput = $state('')
  let activePane = $state('input') // 'input' | 'overview' | 'sim' | 'stats' | 'node-config'
  let currentModel = $state('gemini-2.5-flash-lite')
  let currentPrompt = $state('')
  let thinkingBudget = $state(5)
  let isSubmitting = $state(false)

  // === STATE SYNCHRONIZATION ===
  $effect(() => {
    if ($selectedNode && expanded) {
      // 1. Node selected -> FORCE view to Config
      // Load node specific data
      const node = $agentNodes.find((n) => n.id === $selectedNode)
      if (node) {
        currentModel = node.model.toLowerCase()
        currentPrompt = node.prompt
      }
      
      // Force switch to node-config if not already there
      if (activePane !== 'node-config') {
        activePane = 'node-config'
      }
    } else if (!$selectedNode && activePane === 'node-config') {
      // 2. Node deselected while in config -> Fallback to Overview
      activePane = 'overview'
    } else if (!expanded && activePane !== 'input') {
      // 3. If collapsed, ensure we return to input mode
      activePane = 'input'
    }
  });

  async function executeRun() {
    if (!cmdInput) return
    if (isSubmitting) return

    isSubmitting = true
    addLog('OPERATOR', `<strong>${cmdInput}</strong>`, 'USER_INPUT')

    try {
        // 1. Construct Workflow Config from Store State
        const nodes = get(agentNodes)
        const edges = get(pipelineEdges)
        
        // Map UI Nodes to Kernel AgentConfig
        const agents: AgentConfig[] = nodes.map(n => {
            // Find dependencies
            const dependsOn = edges
                .filter(e => e.to === n.id)
                .map(e => e.from);
            
            // Determine Model variant enum string
            let modelVariant = 'gemini-2.5-flash-lite';
            if (n.model.toUpperCase().includes('FLASH')) modelVariant = 'gemini-3-flash-preview';
            if (n.model.toUpperCase().includes('DEEP')) modelVariant = 'gemini-2.5-flash';

            return {
                id: n.id,
                role: n.role,
                model: modelVariant,
                tools: [], // Configurable tools could be added here
                input_schema: {},
                output_schema: {},
                cache_policy: 'ephemeral',
                depends_on: dependsOn,
                prompt: n.prompt,
                position: { x: n.x, y: n.y }
            };
        });

        // Inject User Input into Orchestrator
        const orchestrator = agents.find(a => a.role === 'orchestrator');
        if (orchestrator) {
            orchestrator.prompt = `${orchestrator.prompt}\n\nUSER REQUEST: ${cmdInput}`;
        }

        const config: WorkflowConfig = {
            id: `flow-${Date.now()}`,
            name: 'RARO_Session',
            agents: agents,
            max_token_budget: 100000,
            timeout_ms: 60000
        };

        addLog('KERNEL', 'Compiling DAG manifest...', 'SYS');

        // 2. Send to Kernel
        const response = await startRun(config);
        
        addLog('KERNEL', `Workflow started. Run ID: ${response.run_id}`, 'OK');
        
        // 3. Connect WebSocket for live updates
        connectRuntimeWebSocket(response.run_id);

        cmdInput = ''

    } catch (e: any) {
        addLog('KERNEL', `Execution failed: ${e.message}`, 'ERR');
    } finally {
        isSubmitting = false;
    }
  }

  function handlePaneSelect(pane: string) {
    activePane = pane
    // Safety: If user manually clicks a tab, ensure no node is selected
    if ($selectedNode) {
      deselectNode()
    }
  }

  function handleCloseNode() {
    // Deselecting triggers the $effect above, which sets activePane = 'overview'
    deselectNode()
  }

  // Handle Node Config Updates
  function saveNodeConfig() {
      if (!$selectedNode) return;
      
      agentNodes.update(nodes => nodes.map(n => {
          if (n.id === $selectedNode) {
              return {
                  ...n,
                  model: currentModel.toUpperCase(),
                  prompt: currentPrompt
              }
          }
          return n;
      }));
  }
</script>

<div id="control-deck" class:architect-mode={expanded}>
  {#if expanded}
    <div id="deck-nav">
      <!-- === NAVIGATION LOGIC === -->
      {#if activePane === 'node-config'}
        <!-- MODE A: NODE CONFIGURATION (Exclusive) -->
        <div class="nav-item node-tab active">
          COMPONENT SETTINGS // {$selectedNode}
        </div>
        <div class="nav-item action-close" onclick={handleCloseNode}>
          ×
        </div>
      {:else}
        <!-- MODE B: STANDARD TABS (Exclusive) -->
        <div 
          class="nav-item {activePane === 'overview' ? 'active' : ''}" 
          onclick={() => handlePaneSelect('overview')}
        >
          Overview
        </div>
        <!-- [Restored Functionality] Simulation Tab -->
        <div 
          class="nav-item {activePane === 'sim' ? 'active' : ''}" 
          onclick={() => handlePaneSelect('sim')}
        >
          Simulation
        </div>
        <div 
          class="nav-item {activePane === 'stats' ? 'active' : ''}" 
          onclick={() => handlePaneSelect('stats')}
        >
          Telemetry
        </div>
      {/if}
    </div>
  {/if}

  <!-- === CONTENT LOGIC === -->
  <div class="pane-container">
    
    {#if !expanded || activePane === 'input'}
      <!-- 1. INPUT -->
      <div id="pane-input" class="deck-pane">
        <textarea
          id="cmd-input"
          placeholder=">> Enter research directive or click pipeline to configure..."
          bind:value={cmdInput}
          disabled={isSubmitting}
        ></textarea>
        <button id="btn-run" onclick={executeRun} disabled={isSubmitting}>
            {#if isSubmitting}
                INITIALIZING...
            {:else}
                INITIATE RUN
            {/if}
        </button>
      </div>

    {:else if activePane === 'node-config'}
      <!-- 2. NODE CONFIG -->
      <div id="pane-node-config" class="deck-pane">
        <div class="form-grid">
          <div class="form-group">
            <label>Agent ID</label>
            <input class="input-std input-readonly" value={$selectedNode} readonly />
          </div>
          <div class="form-group">
            <label>Model Runtime</label>
            <select class="input-std" bind:value={currentModel} onchange={saveNodeConfig}>
              <option value="gemini-2.5-flash-lite">GEMINI-3-PRO</option>
              <option value="gemini-3-flash-preview">GEMINI-3-FLASH</option>
              <option value="gemini-2.5-flash">GEMINI-3-DEEP-THINK</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label>System Instruction (Prompt)</label>
          <textarea 
            class="input-std" 
            bind:value={currentPrompt} 
            oninput={saveNodeConfig}
            style="height:80px; resize:none;"
          ></textarea>
        </div>

        {#if currentModel === 'gemini-2.5-flash'}
          <div class="form-group deep-think-config">
            <label>Thinking Budget (Extended Reasoning Depth)</label>
            <div class="slider-container">
              <input type="range" min="1" max="10" bind:value={thinkingBudget} class="thinking-slider"/>
              <span class="slider-value">{thinkingBudget}</span>
            </div>
            <div class="slider-description">
              {#if thinkingBudget <= 3}
                <span>Fast reasoning with focused hypothesis generation</span>
              {:else if thinkingBudget <= 6}
                <span>Balanced reasoning depth for synthesis tasks</span>
              {:else}
                <span>Extended thinking for complex cross-paper analysis</span>
              {/if}
            </div>
          </div>
        {/if}
      </div>

    {:else if activePane === 'overview'}
      <!-- 3. OVERVIEW -->
      <div id="pane-overview" class="deck-pane">
        <div class="form-grid">
          <div class="form-group">
            <label>Pipeline Identifier</label>
            <input class="input-std" value="RARO_Live_Session" readonly />
          </div>
          <div class="form-group">
            <label>Max Token Budget</label>
            <input class="input-std" value="100,000" />
          </div>
          <div class="form-group">
            <label>Agent Service Status</label>
            <div class="status-indicator">ONLINE</div>
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

    {:else if activePane === 'sim'}
      <!-- 4. SIMULATION [Restored] -->
      <div id="pane-sim" class="deck-pane">
        <div style="display:flex; gap:10px; margin-bottom:15px;">
          <button class="input-std action-btn" onclick={() => addLog('SYSTEM', 'Simulating step 1...')}>▶ STEP EXECUTION</button>
          <button class="input-std action-btn" onclick={() => addLog('SYSTEM', 'Resetting context...')}>↺ RESET CONTEXT</button>
        </div>
        <div style="font-family:var(--font-code); font-size:11px; color:#555; background:white; border:1px solid var(--paper-line); padding:10px; height:100px; overflow-y:auto;">
          &gt; Ready for test vector injection...<br />
          &gt; Agents loaded: {$agentNodes.length}
        </div>
      </div>

    {:else if activePane === 'stats'}
      <!-- 5. TELEMETRY -->
      <div id="pane-stats" class="deck-pane">
        <div class="stat-grid">
          <div class="stat-card">
            <span class="stat-val">{($telemetry.tokensUsed / 1000).toFixed(1)}k</span>
            <span class="stat-lbl">Tokens</span>
          </div>
          <div class="stat-card">
            <span class="stat-val">${$telemetry.totalCost.toFixed(4)}</span>
            <span class="stat-lbl">Est. Cost</span>
          </div>
          <div class="stat-card">
            <span class="stat-val">{$telemetry.errorCount}</span>
            <span class="stat-lbl">Errors</span>
          </div>
           <div class="stat-card">
            <span class="stat-val">LIVE</span>
            <span class="stat-lbl">Mode</span>
          </div>
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .status-indicator {
      color: #00C853;
      font-weight: 700;
      font-size: 11px;
      margin-top: 10px;
  }

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
    flex-shrink: 0;
    /* Ensure items don't wrap unexpectedly */
    overflow: hidden; 
  }

  .pane-container {
    flex: 1;
    overflow: hidden;
    position: relative;
    display: flex;
    flex-direction: column;
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

  /* Node Tab specific styles */
  .nav-item.node-tab {
    flex: 4; 
    justify-content: flex-start;
    padding-left: 20px;
    background: var(--paper-bg);
    color: var(--paper-ink);
    border-bottom: 2px solid var(--paper-ink);
    cursor: default;
  }

  .action-close {
    flex: 0;
    min-width: 50px;
    font-size: 16px; /* Larger hit target for X */
    color: #d32f2f;
    border-right: none;
    border-left: 1px solid var(--paper-line);
  }
  .action-close:hover {
    background: #ffeeee;
    color: #b71c1c;
  }

  .deck-pane {
    flex: 1;
    height: 100%;
    padding: 20px;
    overflow-y: auto;
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
    flex-shrink: 0;
  }

  #btn-run:hover {
    background: #f5f5f5;
  }

  #btn-run:disabled {
      background: #eee;
      color: #999;
      cursor: not-allowed;
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

  .deep-think-config {
    margin-top: 20px;
    padding: 12px;
    background: #f9f9f9;
    border: 1px solid #ddd;
    border-radius: 4px;
  }

  .slider-container {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 10px 0;
  }

  .thinking-slider {
    flex: 1;
    height: 6px;
    border-radius: 3px;
    background: linear-gradient(to right, #888, #1a1918);
    outline: none;
    -webkit-appearance: none;
  }

  .thinking-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: #1a1918;
    cursor: pointer;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
  }

  .thinking-slider::-moz-range-thumb {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: #1a1918;
    cursor: pointer;
    border: none;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
  }

  .slider-value {
    font-weight: 700;
    font-size: 14px;
    color: #1a1918;
    min-width: 30px;
    text-align: center;
  }

  .slider-description {
    font-size: 11px;
    color: #666;
    font-style: italic;
    margin-top: 8px;
  }
</style>