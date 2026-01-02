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
  let isInputFocused = $state(false)

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
            if (n.model.toUpperCase().includes('FLASH')) modelVariant = 'gemini-2.5-flash';
            if (n.model.toUpperCase().includes('DEEP')) modelVariant = 'gemini-2.5-flash';

            return {
                id: n.id,
                role: n.role,
                model: modelVariant,
                tools: [], 
                input_schema: {},
                output_schema: {},
                cache_policy: 'ephemeral',
                depends_on: dependsOn,
                prompt: n.prompt,
                position: { x: n.x, y: n.y }
            };
        });

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
    if ($selectedNode) deselectNode()
  }

  function handleCloseNode() {
    deselectNode()
  }

  function saveNodeConfig() {
      if (!$selectedNode) return;
      agentNodes.update(nodes => nodes.map(n => {
          if (n.id === $selectedNode) {
              return { ...n, model: currentModel.toUpperCase(), prompt: currentPrompt }
          }
          return n;
      }));
  }

  function handleKey(e: KeyboardEvent) {
      if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          executeRun();
      }
  }
</script>

<div id="control-deck" class:architect-mode={expanded}>
  {#if expanded}
    <div id="deck-nav">
      {#if activePane === 'node-config'}
        <div class="nav-item node-tab active">
          COMPONENT SETTINGS // {$selectedNode}
        </div>
        <div class="nav-item action-close" onclick={handleCloseNode}>×</div>
      {:else}
        <div class="nav-item {activePane === 'overview' ? 'active' : ''}" onclick={() => handlePaneSelect('overview')}>Overview</div>
        <div class="nav-item {activePane === 'sim' ? 'active' : ''}" onclick={() => handlePaneSelect('sim')}>Simulation</div>
        <div class="nav-item {activePane === 'stats' ? 'active' : ''}" onclick={() => handlePaneSelect('stats')}>Telemetry</div>
      {/if}
    </div>
  {/if}

  <div class="pane-container">
    {#if !expanded || activePane === 'input'}
      <!-- 1. INPUT CONSOLE (Refined Wrapper) -->
      <div id="pane-input" class="deck-pane">
        <div class="cmd-wrapper {isInputFocused ? 'focused' : ''}">
            <textarea
                id="cmd-input"
                placeholder="ENTER DIRECTIVE..."
                bind:value={cmdInput}
                disabled={isSubmitting}
                onkeydown={handleKey}
                onfocus={() => isInputFocused = true}
                onblur={() => isInputFocused = false}
            ></textarea>
            <button id="btn-run" onclick={executeRun} disabled={isSubmitting}>
                {#if isSubmitting}
                    <span class="loader"></span>
                {:else}
                    ↵
                {/if}
            </button>
        </div>
        <div class="input-hint">PRESS ENTER TO EXECUTE // SHIFT+ENTER FOR NEW LINE</div>
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
              <option value="gemini-2.5-flash">GEMINI-3-FLASH</option>
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
            <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                <label>Thinking Budget (Depth)</label>
                <span class="slider-value-badge">LEVEL {thinkingBudget}</span>
            </div>
            
            <div class="slider-container">
              <input type="range" min="1" max="10" bind:value={thinkingBudget} class="thinking-slider"/>
            </div>
            
            <div class="slider-description">
              {#if thinkingBudget <= 3}
                <span>Fast reasoning with focused hypothesis generation.</span>
              {:else if thinkingBudget <= 6}
                <span>Balanced reasoning depth for synthesis tasks.</span>
              {:else}
                <span>Extended thinking for complex cross-paper analysis.</span>
              {/if}
            </div>
          </div>
        {/if}
      </div>

    {:else if activePane === 'overview'}
       <div id="pane-overview" class="deck-pane">
        <div class="form-grid">
          <div class="form-group"><label>Pipeline Identifier</label><input class="input-std" value="RARO_Live_Session" readonly /></div>
          <div class="form-group"><label>Max Token Budget</label><input class="input-std" value="100,000" /></div>
          <div class="form-group"><label>Service Status</label><div class="status-indicator">ONLINE</div></div>
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
      <div id="pane-sim" class="deck-pane">
        <div style="display:flex; gap:10px; margin-bottom:15px;">
          <button class="input-std action-btn" onclick={() => addLog('SYSTEM', 'Simulating step 1...')}>▶ STEP EXECUTION</button>
          <button class="input-std action-btn" onclick={() => addLog('SYSTEM', 'Resetting context...')}>↺ RESET CONTEXT</button>
        </div>
        <div class="sim-terminal">
          &gt; Ready for test vector injection...<br />
          &gt; Agents loaded: {$agentNodes.length}
        </div>
      </div>
    {:else if activePane === 'stats'}
      <div id="pane-stats" class="deck-pane">
        <div class="stat-grid">
          <div class="stat-card"><span class="stat-val">{($telemetry.tokensUsed / 1000).toFixed(1)}k</span><span class="stat-lbl">Tokens</span></div>
          <div class="stat-card"><span class="stat-val">${$telemetry.totalCost.toFixed(4)}</span><span class="stat-lbl">Est. Cost</span></div>
          <div class="stat-card"><span class="stat-val">{$telemetry.errorCount}</span><span class="stat-lbl">Errors</span></div>
          <div class="stat-card"><span class="stat-val">LIVE</span><span class="stat-lbl">Mode</span></div>
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  /* === LAYOUT & BASICS === */
  #control-deck {
    height: 160px;
    background: var(--paper-bg);
    border-top: 1px solid var(--paper-line);
    display: flex;
    flex-direction: column;
    transition: height 0.5s var(--ease-snap), background 0.3s, border-color 0.3s;
    position: relative;
    z-index: 150;
  }
  #control-deck.architect-mode { height: 260px; }

  /* NAVIGATION */
  #deck-nav { 
    height: 36px; 
    background: var(--paper-surface); 
    border-bottom: 1px solid var(--paper-line); 
    display: flex; 
    flex-shrink: 0; 
    overflow: hidden; 
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
    color: var(--paper-line); /* Replaced #888 */
    cursor: pointer; 
    border-right: 1px solid var(--paper-line); 
    transition: all 0.2s; 
  }
  
  .nav-item:hover { 
    color: var(--paper-ink); 
    background: var(--paper-bg); /* Replaced white */
  }
  
  .nav-item.active { 
    background: var(--paper-bg); 
    color: var(--paper-ink); 
    border-bottom: 2px solid var(--paper-ink); 
  }
  
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
    font-size: 16px; 
    color: #d32f2f; /* Kept semantic red, but it works on dark backgrounds too */
    border-right: none; 
    border-left: 1px solid var(--paper-line); 
  }
  
  .action-close:hover { 
    background: var(--paper-surface-dim); 
    color: #b71c1c; 
  }

  .pane-container { flex: 1; overflow: hidden; position: relative; display: flex; flex-direction: column; }
  .deck-pane { flex: 1; height: 100%; padding: 20px; overflow-y: auto; }

  /* === INPUT CONSOLE STYLING === */
  #pane-input {
      display: flex;
      flex-direction: column;
      justify-content: center;
  }

  /* The floating "Device" wrapper for input */
  .cmd-wrapper {
      display: flex;
      background: var(--paper-bg); /* Replaced white */
      border: 1px solid var(--paper-line);
      height: 80px; 
      transition: border-color 0.2s, box-shadow 0.2s;
  }

  .cmd-wrapper.focused {
      border-color: var(--paper-ink);
      box-shadow: 0 4px 12px rgba(0,0,0,0.05);
  }

  #cmd-input {
      flex: 1;
      border: none;
      background: transparent;
      padding: 16px;
      font-family: var(--font-code);
      font-size: 13px;
      color: var(--paper-ink);
      resize: none;
      outline: none;
  }

  #cmd-input::placeholder { opacity: 0.4; text-transform: uppercase; color: var(--paper-ink); }

  #btn-run {
      width: 60px;
      border: none;
      border-left: 1px solid var(--paper-line);
      background: var(--paper-surface); /* Replaced #FAFAFA */
      color: var(--paper-ink); /* Replaced Rust #A53F2B for better dark mode support */
      font-weight: 900;
      font-size: 20px;
      cursor: pointer;
      transition: all 0.1s;
      display: flex; align-items: center; justify-content: center;
  }

  #btn-run:hover:not(:disabled) { 
    background: var(--paper-ink); 
    color: var(--paper-bg); 
  }
  
  #btn-run:active:not(:disabled) { 
    opacity: 0.8; 
  }
  
  #btn-run:disabled { 
    background: var(--paper-surface-dim); 
    color: var(--paper-line); 
    cursor: not-allowed; 
  }

  .input-hint {
      font-family: var(--font-code);
      font-size: 9px;
      color: var(--paper-line); /* Replaced #999 */
      text-align: right;
      margin-top: 8px;
      letter-spacing: 0.5px;
  }

  /* === FORMS & UTILS === */
  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .form-group { margin-bottom: 16px; }
  
  label { 
    display: block; 
    font-size: 9px; 
    color: var(--paper-line); /* Replaced #888 */
    text-transform: uppercase; 
    margin-bottom: 6px; 
    font-weight: 600; 
  }
  
  .input-std { 
    width: 100%; 
    padding: 10px; 
    border: 1px solid var(--paper-line); 
    background: var(--paper-bg); /* Replaced white */
    font-family: var(--font-code); 
    font-size: 12px; 
    color: var(--paper-ink); 
    outline: none; 
  }
  
  .input-std:focus { border-color: var(--paper-ink); }
  
  .input-readonly { 
    background: var(--paper-surface); 
    color: var(--paper-line); /* Replaced #666 */
    cursor: default; 
  }
  
  .status-indicator { color: #00C853; font-weight: 700; font-size: 11px; margin-top: 10px; }
  
  .action-btn { 
    width: auto; 
    cursor: pointer; 
    background: var(--paper-ink); /* Replaced #1a1918 */
    color: var(--paper-bg); /* Replaced white */
    border: 1px solid var(--paper-ink);
  }
  .action-btn:hover {
      background: var(--paper-bg);
      color: var(--paper-ink);
  }
  
  .sim-terminal { 
    font-family: var(--font-code); 
    font-size: 11px; 
    color: var(--paper-ink); /* Replaced #555 */
    background: var(--paper-bg); /* Replaced white */
    border: 1px solid var(--paper-line); 
    padding: 10px; 
    height: 100px; 
    overflow-y: auto; 
  }

  /* === STATS === */
  .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
  
  .stat-card { 
    border: 1px solid var(--paper-line); 
    background: var(--paper-bg); /* Replaced white */
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
    color: var(--paper-line); /* Replaced #888 */
    text-transform: uppercase; 
    margin-top: 4px; 
    display: block; 
  }

  /* === SLIDER === */
  .deep-think-config { 
    padding: 16px; 
    background: var(--paper-surface-dim); /* Replaced rgba(0,0,0,0.03) */
    border: 1px solid var(--paper-line); 
    border-radius: 0; 
  }
  
  .slider-value-badge { 
    font-size: 10px; 
    background: var(--paper-ink); 
    color: var(--paper-bg); /* Replaced white */
    padding: 2px 6px; 
    border-radius: 2px; 
  }
  
  .slider-container { display: flex; align-items: center; margin: 12px 0; }
  
  .thinking-slider {
    flex: 1; 
    -webkit-appearance: none; 
    height: 4px; 
    background: var(--paper-line); /* Replaced #ddd */
    outline: none;
  }
  
  .thinking-slider::-webkit-slider-thumb {
    -webkit-appearance: none; 
    width: 16px; 
    height: 16px; 
    border-radius: 0; 
    background: var(--paper-ink); 
    cursor: ew-resize; 
    border: 2px solid var(--paper-bg); /* Replaced white */
    box-shadow: 0 1px 3px rgba(0,0,0,0.3); 
    transition: transform 0.1s;
  }
  
  .thinking-slider::-webkit-slider-thumb:hover { transform: scale(1.2); }
  
  .thinking-slider::-moz-range-thumb {
    width: 16px; 
    height: 16px; 
    border-radius: 0; 
    background: var(--paper-ink); 
    cursor: ew-resize; 
    border: 2px solid var(--paper-bg); /* Replaced white */
    box-shadow: 0 1px 3px rgba(0,0,0,0.3);
  }

  .slider-description { 
    font-size: 11px; 
    color: var(--paper-line); /* Replaced #666 */
    font-style: italic; 
    min-height: 1.2em; 
  }
  
  /* Loader */
  .loader {
    width: 16px; 
    height: 16px; 
    border: 2px solid var(--paper-line); /* Replaced #ccc */
    border-bottom-color: transparent; 
    border-radius: 50%; 
    display: inline-block; 
    box-sizing: border-box; 
    animation: rotation 1s linear infinite;
  }
  
  @keyframes rotation { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
</style>