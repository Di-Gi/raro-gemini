<!-- // [[RARO]]/apps/web-console/src/components/ControlDeck.svelte
// Purpose: Main interaction panel. Orchestrates the API call to start the run.
// Architecture: View Controller
// Dependencies: Stores, API -->

<script lang="ts">
  import { selectedNode, agentNodes, pipelineEdges, addLog, updateNodeStatus,
    deselectNode, telemetry, connectRuntimeWebSocket, runtimeStore,
    planningMode,           // Import new store
    loadWorkflowManifest,    // Import new action
    attachedFiles,
    // Import graph mutation actions
    addConnection,
    removeConnection,
    createNode,
    deleteNode
  } from '$lib/stores'
  import { 
    startRun, 
    generateWorkflowPlan, // Import API call
    type WorkflowConfig, 
    type AgentConfig 
  } from '$lib/api'
  import { get } from 'svelte/store'
  // import { fade } from 'svelte/transition'

  let { expanded }: { expanded: boolean } = $props();

  let cmdInput = $state('')
  let activePane = $state('input') // 'input' | 'overview' | 'sim' | 'stats' | 'node-config' | 'pipeline'
  let currentModel = $state('fast')
  let currentPrompt = $state('')
  let currentAcceptsDirective = $state(false)  // Directive toggle state
  let thinkingBudget = $state(5)
  let isSubmitting = $state(false)
  let isInputFocused = $state(false)

  // Pipeline control state
  let connectionFrom = $state('')
  let connectionTo = $state('')
  let nodeToDelete = $state('')

  // Reactive derivation for HITL state
  let isAwaitingApproval = $derived($runtimeStore.status === 'AWAITING_APPROVAL' || $runtimeStore.status === 'PAUSED')
  // === STATE SYNCHRONIZATION ===
  $effect(() => {
    if ($selectedNode && expanded) {
      // 1. Node selected -> FORCE view to Config
      // Load node specific data
      const node = $agentNodes.find((n) => n.id === $selectedNode)
      if (node) {
        currentModel = node.model
        currentPrompt = node.prompt
        currentAcceptsDirective = node.acceptsDirective  // Load directive flag
      }

      // Force switch to node-config if not already there
      if (activePane !== 'node-config') {
        activePane = 'node-config'
      }
    } else if (!$selectedNode && activePane === 'node-config') {
      // 2. Node deselected while in config -> Fallback to Overview
      activePane = 'overview'
    } else if (!expanded && activePane !== 'input' && !isAwaitingApproval) {
      // 3. If collapsed, ensure we return to input mode (unless awaiting approval)
      activePane = 'input'
    }
  });

  // Force expand if approval needed
  $effect(() => {
      if (isAwaitingApproval && !expanded) {
          // In a real app we might emit an event to parent, here we just assume user sees the indicator
      }
  })

  // === 1. THE ARCHITECT HANDLER (Flow A: Planning) ===
  // Pure State Mutation: Generates graph, does NOT execute.
  async function submitPlan() {
    if (!cmdInput) return;
    isSubmitting = true;
    
    addLog('ARCHITECT', `Analyzing directive: "${cmdInput}"`, 'THINKING');

    try {
        const manifest = await generateWorkflowPlan(cmdInput);
        
        // Pure state mutation via Store Action
        loadWorkflowManifest(manifest);
        
        addLog('ARCHITECT', 'Graph construction complete.', 'DONE');

    } catch (e: any) {
        addLog('ARCHITECT', `Planning failed: ${e.message}`, 'ERR');
    } finally {
        isSubmitting = false;
    }
  }

  // === 2. THE KERNEL HANDLER (Flow B: Execution) ===
  // Pure Execution: Runs whatever is in the store.
  async function submitRun() {
    // Allow running if we have input OR if we have a graph to run
    if (!cmdInput && $agentNodes.length === 0) return
    if (isSubmitting) return

    isSubmitting = true;
    if (cmdInput) addLog('OPERATOR', `<strong>${cmdInput}</strong>`, 'EXECUTE');

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

            return {
                id: n.id,
                role: n.role,
                model: n.model, // Use semantic alias directly (fast, reasoning, thinking)
                tools: [],
                input_schema: {},
                output_schema: {},
                cache_policy: 'ephemeral',
                depends_on: dependsOn,
                prompt: n.prompt,  // Keep persona clean
                user_directive: (n.acceptsDirective && cmdInput) ? cmdInput : "",  // Inject directive based on flag
                position: { x: n.x, y: n.y },
                accepts_directive: n.acceptsDirective  // Pass flag to backend
            };
        });

        const config: WorkflowConfig = {
            id: `flow-${Date.now()}`,
            name: 'RARO_Session',
            agents: agents,
            max_token_budget: 100000,
            timeout_ms: 60000,
            attached_files: get(attachedFiles) // <--- Send linked files
        };


        addLog('KERNEL', 'Compiling DAG manifest...', 'SYS');

        // 2. Send to Kernel
        const response = await startRun(config);
        
        addLog('KERNEL', `Workflow started. Run ID: ${response.run_id}`, 'OK');
        
        // 3. Connect WebSocket for live updates
        connectRuntimeWebSocket(response.run_id);

        cmdInput = '' // Clear input on successful run

    } catch (e: any) {
        addLog('KERNEL', `Execution failed: ${e.message}`, 'ERR');
    } finally {
        isSubmitting = false;
    }
  }

  // === 3. THE ROUTER ===
  function handleCommand() {
    if (isSubmitting) return;
    
    if ($planningMode) {
        submitPlan();
    } else {
        submitRun();
    }
  }

  function toggleMode() {
    planningMode.update(v => !v);
  }

  // === HELPERS ===
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
              return { ...n, model: currentModel, prompt: currentPrompt, acceptsDirective: currentAcceptsDirective }
          }
          return n;
      }));
  }

  function handleKey(e: KeyboardEvent) {
      if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          handleCommand(); // Route through the mode selector
      }
  }

  // === PIPELINE CONTROL ACTIONS ===
  function handleAddConnection() {
    if (!connectionFrom || !connectionTo) {
      addLog('GRAPH', 'Please select both source and target nodes', 'WARN')
      return
    }
    addConnection(connectionFrom, connectionTo)
    addLog('GRAPH', `Connection added: ${connectionFrom} → ${connectionTo}`, 'OK')
    connectionFrom = ''
    connectionTo = ''
  }

  function handleRemoveConnection() {
    if (!connectionFrom || !connectionTo) {
      addLog('GRAPH', 'Please select both source and target nodes', 'WARN')
      return
    }
    removeConnection(connectionFrom, connectionTo)
    addLog('GRAPH', `Connection removed: ${connectionFrom} ⨯ ${connectionTo}`, 'OK')
    connectionFrom = ''
    connectionTo = ''
  }

  function handleCreateNode() {
    // Create node at center of viewport (normalized coords)
    const centerX = 50
    const centerY = 50
    createNode(centerX, centerY)
    addLog('GRAPH', 'New node created at center', 'OK')
  }

  function handleDeleteNode() {
    if (!nodeToDelete) {
      addLog('GRAPH', 'Please select a node to delete', 'WARN')
      return
    }
    const nodeLabel = $agentNodes.find(n => n.id === nodeToDelete)?.label || nodeToDelete
    deleteNode(nodeToDelete)
    addLog('GRAPH', `Node deleted: ${nodeLabel}`, 'OK')
    nodeToDelete = ''
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
        <div class="nav-item {activePane === 'pipeline' ? 'active' : ''}" onclick={() => handlePaneSelect('pipeline')}>Pipeline</div>
        <div class="nav-item {activePane === 'sim' ? 'active' : ''}" onclick={() => handlePaneSelect('sim')}>Simulation</div>
        <div class="nav-item {activePane === 'stats' ? 'active' : ''}" onclick={() => handlePaneSelect('stats')}>Telemetry</div>
      {/if}
    </div>
  {/if}

  <div class="pane-container">

    <!-- Normal Panes -->
    {#if !expanded || activePane === 'input'}
      <!-- 1. INPUT CONSOLE -->
      <div id="pane-input" class="deck-pane">
        
        <!-- === NEW: CONTEXT RACK (EXEC MODE ONLY) === -->
        {#if !$planningMode && $attachedFiles.length > 0}
            <div class="context-rack">
                <div class="rack-label">LIB_LINK</div>
                <div class="rack-files">
                    {#each $attachedFiles as file}
                        <div class="ctx-chip">
                            <div class="ctx-dot"></div>
                            {file}
                        </div>
                    {/each}
                </div>
            </div>
        {/if}

        <!-- Input Wrapper: Changes visual state based on Planning Mode -->
        <div class="cmd-wrapper {isInputFocused ? 'focused' : ''} {$planningMode ? 'mode-plan' : ''}">
            <textarea
                id="cmd-input"
                placeholder={$planningMode ? "ENTER ARCHITECTURAL DIRECTIVE..." : "ENTER RUNTIME DIRECTIVE..."}
                bind:value={cmdInput}
                disabled={isSubmitting || isAwaitingApproval}
                onkeydown={handleKey}
                onfocus={() => isInputFocused = true}
                onblur={() => isInputFocused = false}
            ></textarea>
            
            <!-- Main Action Button: Routes to handleCommand -->
            <button 
                id="btn-run" 
                onclick={handleCommand} 
                disabled={isSubmitting || isAwaitingApproval}
            >
                {#if isSubmitting}
                    <span class="loader"></span>
                {:else if $planningMode}
                    <!-- Plan Icon -->
                    <span>◈</span> 
                {:else}
                    <!-- Execute Icon -->
                    <span>↵</span>
                {/if}
            </button>
        </div>

        <!-- Footer: Mode Toggle & Hints -->
        <div class="deck-footer">
            
            <!-- Mode Toggle Switch -->
            <div 
                class="mode-toggle" 
                onclick={toggleMode} 
                onkeydown={(e) => e.key === 'Enter' && toggleMode()}
                role="button" 
                tabindex="0"
            >
                <div class="toggle-label {!$planningMode ? 'active' : 'dim'}">EXEC</div>
                
                <div class="toggle-track">
                    <div class="toggle-thumb" style="left: {$planningMode ? '14px' : '2px'}"></div>
                </div>
                
                <div class="toggle-label {$planningMode ? 'active' : 'dim'}">PLAN</div>
            </div>

            <!-- Dynamic Hint -->
            <div class="input-hint">
                {#if $planningMode}
                    GENERATIVE MODE // OVERWRITES GRAPH
                {:else}
                    RUNTIME MODE // EXECUTES GRAPH
                {/if}
            </div>
        </div>
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
              <option value="fast">FAST</option>
              <option value="reasoning">REASONING</option>
              <option value="thinking">THINKING</option>
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

        {#if currentModel === 'thinking'}
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

        <!-- DIRECTIVE INPUT PORT -->
        <div class="form-group directive-port-config">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <label>Operator Directive Port</label>
            <button
              class="port-toggle {currentAcceptsDirective ? 'port-open' : 'port-closed'}"
              onclick={() => {
                currentAcceptsDirective = !currentAcceptsDirective;
                saveNodeConfig();
              }}
            >
              {currentAcceptsDirective ? 'LISTENING' : 'LOCKED'}
            </button>
          </div>

          <div class="directive-hint">
            {#if currentAcceptsDirective}
              <span class="hint-active">This node will receive operator directives at runtime</span>
            {:else}
              <span class="hint-inactive">Enable to inject runtime commands directly to this node</span>
            {/if}
          </div>
        </div>
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
    {:else if activePane === 'pipeline'}
      <div id="pane-pipeline" class="deck-pane compact-pane">
        <div class="pipeline-compact">

          <!-- LEFT COLUMN: Connection & Node Controls -->
          <div class="control-col">
            <div class="compact-section">
              <div class="section-mini-header">CONNECTIONS</div>
              <div class="compact-row">
                <select class="input-mini" bind:value={connectionFrom}>
                  <option value="">From...</option>
                  {#each $agentNodes as node}
                    <option value={node.id}>{node.label}</option>
                  {/each}
                </select>
                <span class="arrow-sep">→</span>
                <select class="input-mini" bind:value={connectionTo}>
                  <option value="">To...</option>
                  {#each $agentNodes as node}
                    <option value={node.id}>{node.label}</option>
                  {/each}
                </select>
              </div>
              <div class="btn-row">
                <button class="btn-mini add-btn" onclick={handleAddConnection} title="Add Connection">+</button>
                <button class="btn-mini remove-btn" onclick={handleRemoveConnection} title="Remove Connection">−</button>
              </div>
            </div>

            <div class="compact-section">
              <div class="section-mini-header">NODES</div>
              <div class="btn-row">
                <button class="btn-mini create-btn" onclick={handleCreateNode} title="Create Node">+ New</button>
              </div>
              <div class="compact-row" style="margin-top: 6px;">
                <select class="input-mini" bind:value={nodeToDelete} style="flex: 1;">
                  <option value="">Select to delete...</option>
                  {#each $agentNodes as node}
                    <option value={node.id}>{node.label}</option>
                  {/each}
                </select>
                <button class="btn-mini delete-btn" onclick={handleDeleteNode} disabled={!nodeToDelete} title="Delete Node">⊗</button>
              </div>
            </div>
          </div>

          <!-- RIGHT COLUMN: Topology Display -->
          <div class="topology-col">
            <div class="compact-section">
              <div class="section-mini-header">TOPOLOGY</div>
              <div class="topo-stats">
                <div class="topo-stat">
                  <span class="topo-num">{$agentNodes.length}</span>
                  <span class="topo-label">Nodes</span>
                </div>
                <div class="topo-stat">
                  <span class="topo-num">{$pipelineEdges.length}</span>
                  <span class="topo-label">Edges</span>
                </div>
              </div>
              <div class="edge-list">
                {#each $pipelineEdges.slice(0, 6) as edge}
                  <div class="edge-micro">
                    {($agentNodes.find(n => n.id === edge.from)?.label || edge.from).substring(0, 8)} → {($agentNodes.find(n => n.id === edge.to)?.label || edge.to).substring(0, 8)}
                  </div>
                {/each}
                {#if $pipelineEdges.length > 6}
                  <div class="edge-micro more">+{$pipelineEdges.length - 6} more</div>
                {/if}
              </div>
            </div>
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
    color: var(--paper-line);
    cursor: pointer; 
    border-right: 1px solid var(--paper-line); 
    transition: all 0.2s; 
  }
  
  .nav-item:hover { 
    color: var(--paper-ink); 
    background: var(--paper-bg);
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
    color: #d32f2f;
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
      padding-bottom: 8px; /* Give space for the new footer */
  }

  /* === CONTEXT RACK (FILES) === */
  .context-rack {
      margin-bottom: 12px;
      display: flex;
      align-items: center;
      gap: 12px;
      animation: slideDown 0.2s ease-out;
  }
  .rack-label {
      font-family: var(--font-code);
      font-size: 9px;
      color: var(--paper-line);
      font-weight: 700;
      letter-spacing: 1px;
      flex-shrink: 0;
  }
  .rack-files {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      overflow: hidden;
  }
  .ctx-chip {
      font-family: var(--font-code);
      font-size: 9px;
      color: var(--paper-ink);
      background: var(--paper-surface);
      border: 1px solid var(--paper-line);
      padding: 2px 6px;
      border-radius: 2px;
      display: flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
      cursor: default;
  }
  .ctx-dot {
      width: 4px; height: 4px;
      background: var(--alert-amber);
      border-radius: 50%;
      box-shadow: 0 0 4px var(--alert-amber);
  }
  
  @keyframes slideDown {
      from { opacity: 0; transform: translateY(5px); }
      to { opacity: 1; transform: translateY(0); }
  }


  /* The floating "Device" wrapper for input */
  .cmd-wrapper {
      display: flex;
      background: var(--paper-bg);
      border: 1px solid var(--paper-line);
      height: 80px; 
      transition: border-color 0.2s, box-shadow 0.2s;
  }

  /* Highlight for Planning Mode */
  .cmd-wrapper.mode-plan {
      border-color: var(--arctic-cyan);
      box-shadow: 0 0 10px rgba(0, 240, 255, 0.15); /* Soft cyan glow */
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
      background: var(--paper-surface);
      color: var(--paper-ink); /* Default for Execute */
      font-weight: 900;
      font-size: 20px;
      cursor: pointer;
      transition: all 0.1s;
      display: flex; align-items: center; justify-content: center;
  }

  /* Color change for button icon when in Planning Mode */
  .cmd-wrapper.mode-plan #btn-run {
      color: var(--arctic-cyan); /* Architect icon color */
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

  /* === DECK FOOTER & MODE TOGGLE === */
  .deck-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 10px; /* Space from the cmd-wrapper */
      padding: 0 4px; /* Slight horizontal padding */
      width: 100%;
  }

  .input-hint {
      font-family: var(--font-code);
      font-size: 9px;
      color: var(--paper-line);
      text-align: right; /* Aligned to the right of the footer */
      letter-spacing: 0.5px;
  }

  /* === MODE TOGGLE === */
  .mode-toggle {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      user-select: none;
      opacity: 0.8;
      transition: opacity 0.2s;
      outline: none; /* Remove default focus outline */
  }
  .mode-toggle:hover { opacity: 1; }
  /* Custom focus style */
  .mode-toggle:focus-visible { outline: 1px dotted var(--arctic-cyan); outline-offset: 2px; }

  .toggle-label {
      font-family: var(--font-code);
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 1px;
      transition: color 0.3s;
  }
  .toggle-label.active { color: var(--paper-ink); }
  .toggle-label.dim { color: var(--paper-line); }

  .toggle-track {
      width: 28px; /* Slightly wider track */
      height: 12px;
      background: var(--paper-surface);
      border: 1px solid var(--paper-line);
      border-radius: 6px;
      position: relative;
      transition: background 0.2s;
  }

  .toggle-thumb {
      width: 8px;
      height: 8px;
      background: var(--paper-ink);
      border-radius: 50%;
      position: absolute;
      top: 1px; /* Center vertically in track */
      transition: left 0.2s var(--ease-snap), background 0.2s;
  }
  /* Thumb color for Planning Mode */
  .cmd-wrapper.mode-plan + .deck-footer .mode-toggle .toggle-thumb {
      background: var(--arctic-cyan);
  }

  /* === FORMS & UTILS === */
  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .form-group { margin-bottom: 16px; }
  
  label { 
    display: block; 
    font-size: 9px; 
    color: var(--paper-line);
    text-transform: uppercase; 
    margin-bottom: 6px; 
    font-weight: 600; 
  }
  
  .input-std { 
    width: 100%; 
    padding: 10px; 
    border: 1px solid var(--paper-line); 
    background: var(--paper-bg);
    font-family: var(--font-code); 
    font-size: 12px; 
    color: var(--paper-ink); 
    outline: none; 
  }
  
  .input-std:focus { border-color: var(--paper-ink); }
  
  .input-readonly { 
    background: var(--paper-surface); 
    color: var(--paper-line);
    cursor: default; 
  }
  
  .status-indicator { color: #00C853; font-weight: 700; font-size: 11px; margin-top: 10px; }
  
  .action-btn { 
    width: auto; 
    cursor: pointer; 
    background: var(--paper-ink);
    color: var(--paper-bg);
    border: 1px solid var(--paper-ink);
  }
  .action-btn:hover {
      background: var(--paper-bg);
      color: var(--paper-ink);
  }
  
  .sim-terminal { 
    font-family: var(--font-code); 
    font-size: 11px; 
    color: var(--paper-ink);
    background: var(--paper-bg);
    border: 1px solid var(--paper-line); 
    padding: 10px; 
    height: 100px; 
    overflow-y: auto; 
  }

  /* === STATS === */
  .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
  
  .stat-card { 
    border: 1px solid var(--paper-line); 
    background: var(--paper-bg);
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
    color: var(--paper-line);
    text-transform: uppercase; 
    margin-top: 4px; 
    display: block; 
  }

  /* === SLIDER === */
  .deep-think-config { 
    padding: 16px; 
    background: var(--paper-surface-dim);
    border: 1px solid var(--paper-line); 
    border-radius: 0; 
  }
  
  .slider-value-badge { 
    font-size: 10px; 
    background: var(--paper-ink); 
    color: var(--paper-bg);
    padding: 2px 6px; 
    border-radius: 2px; 
  }
  
  .slider-container { display: flex; align-items: center; margin: 12px 0; }
  
  .thinking-slider {
    flex: 1; 
    -webkit-appearance: none; 
    height: 4px; 
    background: var(--paper-line);
    outline: none;
  }
  
  .thinking-slider::-webkit-slider-thumb {
    -webkit-appearance: none; 
    width: 16px; 
    height: 16px; 
    border-radius: 0; 
    background: var(--paper-ink); 
    cursor: ew-resize; 
    border: 2px solid var(--paper-bg);
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
    border: 2px solid var(--paper-bg);
    box-shadow: 0 1px 3px rgba(0,0,0,0.3);
  }

  .slider-description { 
    font-size: 11px; 
    color: var(--paper-line);
    font-style: italic; 
    min-height: 1.2em; 
  }
  
  /* Loader */
  .loader {
    width: 16px;
    height: 16px;
    border: 2px solid var(--paper-line);
    border-bottom-color: transparent;
    border-radius: 50%;
    display: inline-block;
    box-sizing: border-box;
    animation: rotation 1s linear infinite;
  }

  @keyframes rotation { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

  /* === PIPELINE CONTROL - COMPACT === */
  .compact-pane {
    padding: 12px !important;
  }

  .pipeline-compact {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    height: 100%;
  }

  .control-col, .topology-col {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .compact-section {
    background: var(--paper-surface-dim);
    border: 1px solid var(--paper-line);
    padding: 10px;
  }

  .section-mini-header {
    font-size: 8px;
    font-weight: 700;
    color: var(--paper-line);
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 8px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--paper-line);
  }

  .compact-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .arrow-sep {
    color: var(--paper-line);
    font-size: 12px;
    font-weight: 600;
    flex-shrink: 0;
  }

  .input-mini {
    flex: 1;
    padding: 6px 8px;
    border: 1px solid var(--paper-line);
    background: var(--paper-bg);
    font-family: var(--font-code);
    font-size: 10px;
    color: var(--paper-ink);
    outline: none;
    height: 28px;
  }

  .input-mini:focus {
    border-color: var(--paper-ink);
  }

  .btn-row {
    display: flex;
    gap: 6px;
    margin-top: 6px;
  }

  .btn-mini {
    flex: 1;
    padding: 6px 10px;
    border: 1px solid;
    font-family: var(--font-code);
    font-size: 10px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .add-btn {
    background: var(--paper-bg);
    color: #00C853;
    border-color: #00C853;
  }

  .add-btn:hover:not(:disabled) {
    background: #00C853;
    color: var(--paper-bg);
  }

  .remove-btn {
    background: var(--paper-bg);
    color: #FF6F00;
    border-color: #FF6F00;
  }

  .remove-btn:hover:not(:disabled) {
    background: #FF6F00;
    color: var(--paper-bg);
  }

  .create-btn {
    background: var(--paper-bg);
    color: var(--arctic-cyan);
    border-color: var(--arctic-cyan);
  }

  .create-btn:hover:not(:disabled) {
    background: var(--arctic-cyan);
    color: var(--paper-bg);
  }

  .delete-btn {
    background: var(--paper-bg);
    color: #d32f2f;
    border-color: #d32f2f;
    flex: 0 0 32px;
  }

  .delete-btn:hover:not(:disabled) {
    background: #d32f2f;
    color: var(--paper-bg);
  }

  .delete-btn:disabled {
    opacity: 0.35;
    cursor: not-allowed;
  }

  /* Topology Display */
  .topo-stats {
    display: flex;
    gap: 12px;
    margin-bottom: 8px;
    padding: 8px;
    background: var(--paper-bg);
    border: 1px solid var(--paper-line);
  }

  .topo-stat {
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1;
  }

  .topo-num {
    font-size: 16px;
    font-weight: 700;
    color: var(--paper-ink);
    font-family: var(--font-code);
  }

  .topo-label {
    font-size: 8px;
    color: var(--paper-line);
    text-transform: uppercase;
    margin-top: 2px;
  }

  .edge-list {
    background: var(--paper-bg);
    border: 1px solid var(--paper-line);
    padding: 6px;
    max-height: 110px;
    overflow-y: auto;
  }

  .edge-micro {
    font-family: var(--font-code);
    font-size: 9px;
    color: var(--paper-ink);
    padding: 3px 6px;
    margin-bottom: 3px;
    background: var(--paper-surface);
    border-left: 2px solid var(--paper-line);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .edge-micro.more {
    color: var(--paper-line);
    font-style: italic;
    border-left-color: transparent;
    text-align: center;
  }

  /* === DIRECTIVE PORT CONFIG === */
  .directive-port-config {
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px solid var(--paper-line);
  }

  .port-toggle {
    padding: 6px 14px;
    border: 1px solid;
    font-family: var(--font-code);
    font-size: 10px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.2s;
    letter-spacing: 0.5px;
  }

  .port-toggle.port-open {
    background: var(--paper-bg);
    color: var(--arctic-lilac, #00E5FF);
    border-color: var(--arctic-cyan, #00E5FF);
    box-shadow: 0 0 8px rgba(0, 229, 255, 0.3);
  }

  .port-toggle.port-open:hover {
    background: var(--arctic-lilac);
    color: var(--paper-bg);
  }

  .port-toggle.port-closed {
    background: var(--paper-bg);
    color: var(--paper-line);
    border-color: var(--paper-line);
  }

  .port-toggle.port-closed:hover {
    border-color: var(--paper-ink);
    color: var(--paper-ink);
  }

  .directive-hint {
    margin-top: 8px;
    padding: 8px;
    background: var(--paper-surface-dim);
    border-left: 2px solid;
    font-family: var(--font-code);
    font-size: 10px;
  }

  .hint-active {
    color: var(--arctic-cyan, #00E5FF);
    border-color: var(--arctic-cyan, #00E5FF);
  }

  .hint-inactive {
    color: var(--paper-line);
    border-color: var(--paper-line);
  }
</style>