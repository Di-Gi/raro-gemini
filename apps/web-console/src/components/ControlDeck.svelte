<!-- // [[RARO]]/apps/web-console/src/components/ControlDeck.svelte
// Change Type: Modified
// Purpose: Add Template Selection Tags above input area
// Architectural Context: View Controller
// Dependencies: Stores, API, Templates -->

<script lang="ts">
  import { selectedNode, agentNodes, pipelineEdges, addLog, updateNodeStatus,
    deselectNode, telemetry, connectRuntimeWebSocket, runtimeStore,
    planningMode,
    loadWorkflowManifest,
    attachedFiles,
    addConnection,
    removeConnection,
    createNode,
    deleteNode,
    renameNode,
    deriveToolsFromId,  // [[NEW]] Import Identity Helper
    applyTemplate,  // [[NEW]] Import Template Action
    stepSimulation,  // [[NEW]] Import Simulation Controls
    runSimulation,
    resetSimulation
  } from '$lib/stores'
  import {
    startRun,
    generateWorkflowPlan,
    type WorkflowConfig,
    type AgentConfig
  } from '$lib/api'
  import { get } from 'svelte/store'
  import { TEMPLATES } from '$lib/templates' // [[NEW]] Import Definitions
  import { SCENARIOS, type MissionScenario } from '$lib/scenarios' // [[NEW]] Import Scenarios
  import MissionSelector from './sub/MissionSelector.svelte' // [[NEW]] Import Mission Selector

  let { expanded }: { expanded: boolean } = $props();

  let cmdInput = $state('')
  let activePane = $state('input')
  let currentModel = $state('fast')
  let currentPrompt = $state('')
  let currentAcceptsDirective = $state(false)
  let currentAllowDelegation = $state(false)
  let currentTools = $state<string[]>([])  // Manual tool additions (beyond identity baseline)
  let identityTools = $state<string[]>([])  // Derived from ID prefix (read-only)
  let thinkingBudget = $state(5)
  let isUpdating = $state(false)  // Flag to prevent reactive loops
  let isSubmitting = $state(false)
  let isInputFocused = $state(false)
  let tempId = $state('')

  let isAwaitingApproval = $derived($runtimeStore.status === 'AWAITING_APPROVAL' || $runtimeStore.status === 'PAUSED')

  $effect(() => {
    if ($selectedNode) {
      tempId = $selectedNode;
    }
  });

  // Track the last loaded node ID to prevent unnecessary reloads
  let lastLoadedNodeId = $state<string | null>(null);

  $effect(() => {
    // Skip if we're in the middle of an update to prevent loops
    if (isUpdating) return;

    if ($selectedNode && expanded) {
      // Only reload if we're selecting a different node
      if (lastLoadedNodeId !== $selectedNode) {
        const node = $agentNodes.find((n) => n.id === $selectedNode)
        if (node) {
          currentModel = node.model
          currentPrompt = node.prompt
          currentAcceptsDirective = node.acceptsDirective
          currentAllowDelegation = node.allowDelegation

          // Compute authoritative identity tools
          identityTools = deriveToolsFromId(node.id)

          // Manual tools are stored ones minus the identity baseline
          const stored = node.tools || []
          currentTools = stored.filter(t => !identityTools.includes(t))

          lastLoadedNodeId = $selectedNode
        }
      }
      if (activePane !== 'node-config') {
        activePane = 'node-config'
      }
    } else if (!$selectedNode && activePane === 'node-config') {
      activePane = 'overview'
      lastLoadedNodeId = null
    } else if (!expanded && activePane !== 'input' && !isAwaitingApproval) {
      activePane = 'input'
    }
  });

  $effect(() => {
      if (isAwaitingApproval && !expanded) {
          // auto expand logic if desired
      }
  })

  // [[NEW]] Template Handler
  function handleLoadTemplate(key: string) {
      if (isSubmitting || $runtimeStore.status === 'RUNNING') return;
      applyTemplate(key);
      addLog('SYSTEM', `Pipeline Configuration reset to [${key}] template.`, 'RESET');
  }

  // [[NEW]] Mission Selector Handler
  function handleMissionSelect(mission: MissionScenario) {
      if (isSubmitting || $runtimeStore.status === 'RUNNING') return;

      // 1. Apply Topology
      applyTemplate(mission.templateKey);

      // 2. Clear then Fill Input (Typewriter effect optional, but let's just set it)
      cmdInput = mission.directive;

      // 3. Attach Files
      // Reset attachments for purity
      attachedFiles.set(mission.suggestedFiles);

      addLog('SYSTEM', `Mission Protocol [${mission.id.toUpperCase()}] loaded.`, 'CONFIG_LOAD');
  }

  async function submitPlan() {
    if (!cmdInput) return;
    isSubmitting = true;

    addLog('ARCHITECT', `Analyzing directive: "${cmdInput}"`, 'THINKING');

    try {
        const manifest = await generateWorkflowPlan(cmdInput);
        loadWorkflowManifest(manifest);
        addLog('ARCHITECT', 'Graph construction complete.', 'DONE');
        cmdInput = ''

    } catch (e: any) {
        addLog('ARCHITECT', `Planning failed: ${e.message}`, 'ERR');
    } finally {
        isSubmitting = false;
    }
  }

  async function submitRun() {
    if (!cmdInput && $agentNodes.length === 0) return
    if (isSubmitting) return

    isSubmitting = true;
    if (cmdInput) addLog('OPERATOR', `<strong>${cmdInput}</strong>`, 'EXECUTE');

    try {
        const nodes = get(agentNodes)
        const edges = get(pipelineEdges)
        
        const agents: AgentConfig[] = nodes.map(n => {
            const dependsOn = edges
                .filter(e => e.to === n.id)
                .map(e => e.from);

            return {
                id: n.id,
                role: n.role,
                model: n.model,
                tools: n.tools || [],  // [[NEW]] Pass tools from frontend
                input_schema: {},
                output_schema: {},
                cache_policy: 'ephemeral',
                depends_on: dependsOn,
                prompt: n.prompt,
                user_directive: (n.acceptsDirective && cmdInput) ? cmdInput : "",
                position: { x: n.x, y: n.y },
                accepts_directive: n.acceptsDirective,
                allow_delegation: n.allowDelegation
            };
        });

        const config: WorkflowConfig = {
            id: `flow-${Date.now()}`,
            name: 'RARO_Session',
            agents: agents,
            max_token_budget: 100000,
            timeout_ms: 60000,
            attached_files: get(attachedFiles)
        };

        addLog('KERNEL', 'Compiling DAG manifest...', 'SYS');

        const response = await startRun(config);
        
        addLog('KERNEL', `Workflow started. Run ID: ${response.run_id}`, 'OK');
        
        connectRuntimeWebSocket(response.run_id);

        cmdInput = ''

    } catch (e: any) {
        addLog('KERNEL', `Execution failed: ${e.message}`, 'ERR');
    } finally {
        isSubmitting = false;
    }
  }

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

  function handlePaneSelect(pane: string) {
    activePane = pane
    if ($selectedNode) deselectNode()
  }

  function handleCloseNode() {
    deselectNode()
  }

  function saveNodeConfig() {
      if (!$selectedNode || isUpdating) return;
      isUpdating = true;
      agentNodes.update(nodes => nodes.map(n => {
          if (n.id === $selectedNode) {
              // Merge identity baseline with manual additions
              const allTools = Array.from(new Set([...identityTools, ...currentTools]))
              return {
                  ...n,
                  model: currentModel,
                  prompt: currentPrompt,
                  acceptsDirective: currentAcceptsDirective,
                  allowDelegation: currentAllowDelegation,
                  tools: allTools  // Save merged tool set
              }
          }
          return n;
      }));
      // Reset flag after a small delay to allow reactive cycle to complete
      setTimeout(() => { isUpdating = false; }, 0);
  }

  function handleKey(e: KeyboardEvent) {
      if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          handleCommand();
      }
  }

  function handleRename() {
    if (!$selectedNode || !tempId) return;
    const cleanId = tempId.trim().replace(/\s+/g, '_').toLowerCase();
    const success = renameNode($selectedNode, cleanId);

    if (success) {
      addLog('SYSTEM', `Node renamed: ${cleanId}`, 'OK');

      // Recalculate identity tools based on new ID
      identityTools = deriveToolsFromId(cleanId);
      // Manual tools remain unchanged
      saveNodeConfig();
    } else {
      tempId = $selectedNode;
      addLog('SYSTEM', `Rename failed: ID exists or invalid`, 'WARN');
    }
  }

  // [[NEW]] Tool Toggle Handler
  function toggleTool(tool: string) {
    // If it's an identity-granted tool, it cannot be removed
    if (identityTools.includes(tool)) {
      return; // Locked by identity
    }

    // Toggle manual additions only
    if (currentTools.includes(tool)) {
      currentTools = currentTools.filter(t => t !== tool);
    } else {
      currentTools = [...currentTools, tool];
    }
    saveNodeConfig();
  }

  // Helper to check if a tool is active (either identity or manual)
  function isToolActive(tool: string): boolean {
    return identityTools.includes(tool) || currentTools.includes(tool);
  }

  // Helper to check if a tool is locked by identity
  function isToolLocked(tool: string): boolean {
    return identityTools.includes(tool);
  }

  function handleIdKey(e: KeyboardEvent) {
      if (e.key === 'Enter') {
          (e.target as HTMLInputElement).blur();
      }
  }

  function handleCreateNode() {
    const centerX = 50
    const centerY = 50
    createNode(centerX, centerY)
    addLog('GRAPH', 'New node created at center', 'OK')
  }
</script>

<div id="control-deck" class:architect-mode={expanded}>
  {#if expanded}
    <div id="deck-nav">
      {#if activePane === 'node-config'}
        <div class="nav-item node-tab active">
          COMPONENT SETTINGS // {$selectedNode}
        </div>
        <div
            class="nav-item action-close"
            role="button"
            tabindex="0"
            onclick={handleCloseNode}
            onkeydown={(e) => e.key === 'Enter' && handleCloseNode()}
        >×</div>
      {:else}
        <div
            id="nav-tab-overview"
            class="nav-item {activePane === 'overview' ? 'active' : ''}"
            role="button"
            tabindex="0"
            onclick={() => handlePaneSelect('overview')}
            onkeydown={(e) => e.key === 'Enter' && handlePaneSelect('overview')}
        >Overview</div>
        <div
            id="nav-tab-pipeline"
            class="nav-item {activePane === 'pipeline' ? 'active' : ''}"
            role="button"
            tabindex="0"
            onclick={() => handlePaneSelect('pipeline')}
            onkeydown={(e) => e.key === 'Enter' && handlePaneSelect('pipeline')}
        >Pipeline</div>
        <div
            id="nav-tab-sim"
            class="nav-item {activePane === 'sim' ? 'active' : ''}"
            role="button"
            tabindex="0"
            onclick={() => handlePaneSelect('sim')}
            onkeydown={(e) => e.key === 'Enter' && handlePaneSelect('sim')}
        >Simulation</div>
        <div
            id="nav-tab-stats"
            class="nav-item {activePane === 'stats' ? 'active' : ''}"
            role="button"
            tabindex="0"
            onclick={() => handlePaneSelect('stats')}
            onkeydown={(e) => e.key === 'Enter' && handlePaneSelect('stats')}
        >Telemetry</div>
      {/if}
    </div>
  {/if}

  <div class="pane-container">

    {#if !expanded || activePane === 'input'}
      <div id="pane-input" class="deck-pane">

        <!-- === [[NEW]] MISSION SELECTOR === -->
        <!-- Only visible if not in Planning Mode and not currently running -->
        {#if !$planningMode && $runtimeStore.status !== 'RUNNING'}
            <MissionSelector onSelect={handleMissionSelect} />
        {/if}

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
            
            <button 
                id="btn-run" 
                onclick={handleCommand} 
                disabled={isSubmitting || isAwaitingApproval}
            >
                {#if isSubmitting}
                    <span class="loader"></span>
                {:else if $planningMode}
                    <span>◈</span> 
                {:else}
                    <span>↵</span>
                {/if}
            </button>
        </div>

        <div class="deck-footer">
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
      <!-- ... Node Config Pane (Unchanged) ... -->
      <div id="pane-node-config" class="deck-pane">
        <div class="form-grid">
          <div class="form-group">
            <label>Agent ID</label>
            <input
              class="input-std"
              bind:value={tempId}
              onblur={handleRename}
              onkeydown={handleIdKey}
              disabled={$runtimeStore.status === 'RUNNING'}
              placeholder="Enter unique ID..."
            />
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

        <!-- [[NEW]] CAPABILITY PROVISIONING PANEL -->
        <div class="form-group tool-config">
          <label>Capability Provisioning</label>
          <div class="tool-grid">
            <!-- PYTHON SANDBOX -->
            <button
              class="tool-btn {isToolActive('execute_python') ? 'active' : ''} {isToolLocked('execute_python') ? 'locked' : ''}"
              onclick={() => toggleTool('execute_python')}
              title={isToolLocked('execute_python') ? 'Granted by identity prefix' : 'Click to toggle'}
            >
              <span class="tool-icon">🐍</span>
              <span class="tool-label">PYTHON_SANDBOX</span>
              {#if isToolLocked('execute_python')}
                <span class="lock-indicator">🔒</span>
              {/if}
            </button>

            <!-- WEB SEARCH -->
            <button
              class="tool-btn {isToolActive('web_search') ? 'active' : ''} {isToolLocked('web_search') ? 'locked' : ''}"
              onclick={() => toggleTool('web_search')}
              title={isToolLocked('web_search') ? 'Granted by identity prefix' : 'Click to toggle'}
            >
              <span class="tool-icon">🌐</span>
              <span class="tool-label">WEB_UPLINK</span>
              {#if isToolLocked('web_search')}
                <span class="lock-indicator">🔒</span>
              {/if}
            </button>

            <!-- FILE WRITE -->
            <button
              class="tool-btn {isToolActive('write_file') ? 'active' : ''} {isToolLocked('write_file') ? 'locked' : ''}"
              onclick={() => toggleTool('write_file')}
              title={isToolLocked('write_file') ? 'Granted by identity prefix' : 'Click to toggle'}
            >
              <span class="tool-icon">💾</span>
              <span class="tool-label">FS_WRITE</span>
              {#if isToolLocked('write_file')}
                <span class="lock-indicator">🔒</span>
              {/if}
            </button>
          </div>
          <div class="tool-hint">
            🔒 = Granted by identity prefix. Read/List access granted to all nodes.
          </div>
        </div>

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

        <div class="form-group directive-port-config">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <label>Delegation Capability</label>
            <button
              class="port-toggle {currentAllowDelegation ? 'port-open' : 'port-closed'}"
              onclick={() => {
                currentAllowDelegation = !currentAllowDelegation;
                saveNodeConfig();
              }}
            >
              {currentAllowDelegation ? 'ENABLED' : 'DISABLED'}
            </button>
          </div>

          <div class="directive-hint">
            {#if currentAllowDelegation}
              <span class="hint-active">This node can spawn sub-agents and modify the workflow graph</span>
            {:else}
              <span class="hint-inactive">Enable to allow this node to dynamically create sub-agents</span>
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
      <div id="pane-pipeline" class="deck-pane">
        <div class="deck-pane-wrapper">

          <!-- SIDEBAR: Node Management -->
          <div class="sidebar">
            <div class="section-header">NODES [{$agentNodes.length}]</div>
            <div class="node-list">
              {#each $agentNodes as node}
                <div class="node-item">
                  <span>{node.label.toUpperCase()}</span>
                  <span class="del-btn" onclick={() => { deleteNode(node.id); addLog('GRAPH', `Node deleted: ${node.label}`, 'OK'); }}>×</span>
                </div>
              {/each}
            </div>
            <button class="add-node-btn" onclick={handleCreateNode}>+ ADD NODE</button>
          </div>

          <!-- MATRIX AREA -->
          <div class="matrix-wrapper">
            <div class="matrix-scroll">
              <div
                class="patch-grid"
                style="grid-template-columns: 90px repeat({$agentNodes.length}, 28px);"
              >

                <!-- CORNER -->
                <div class="corner-cell">
                  <div class="corner-hint">
                    SRC<br/>▼<br/>TGT ▶
                  </div>
                </div>

                <!-- COLUMN HEADERS (Targets) -->
                {#each $agentNodes as target}
                  <div class="col-header"><span class="v-text">{target.label.toUpperCase()}</span></div>
                {/each}

                <!-- ROWS -->
                {#each $agentNodes as source, rowIdx}
                  <!-- ROW HEADER (Source) -->
                  <div class="row-header">{source.label.toUpperCase()}</div>

                  <!-- CELLS -->
                  {#each $agentNodes as target, colIdx}
                    {@const isSelf = source.id === target.id}
                    {@const isConnected = $pipelineEdges.some(e => e.from === source.id && e.to === target.id)}
                    <div
                      class="cell {isSelf ? 'disabled' : ''} {isConnected ? 'active' : ''}"
                      onclick={() => {
                        if (isSelf) return;
                        if (isConnected) {
                          removeConnection(source.id, target.id);
                          addLog('GRAPH', `Connection removed: ${source.label} ⨯ ${target.label}`, 'OK');
                        } else {
                          addConnection(source.id, target.id);
                          addLog('GRAPH', `Connection added: ${source.label} → ${target.label}`, 'OK');
                        }
                      }}
                      role="button"
                      tabindex={isSelf ? -1 : 0}
                      onkeydown={(e) => {
                        if (e.key === 'Enter' && !isSelf) {
                          if (isConnected) {
                            removeConnection(source.id, target.id);
                            addLog('GRAPH', `Connection removed: ${source.label} ⨯ ${target.label}`, 'OK');
                          } else {
                            addConnection(source.id, target.id);
                            addLog('GRAPH', `Connection added: ${source.label} → ${target.label}`, 'OK');
                          }
                        }
                      }}
                    >
                      {#if !isSelf}
                        <div class="led"></div>
                      {/if}
                    </div>
                  {/each}
                {/each}

              </div>
            </div>
          </div>

        </div>
      </div>
    {:else if activePane === 'sim'}
      <div id="pane-sim" class="deck-pane">
        <div class="sim-controls">
          <button class="input-std action-btn primary" onclick={stepSimulation}>
            ▶ STEP
          </button>
          <button id="btn-sim-auto" class="input-std action-btn auto" onclick={runSimulation}>
            ▶▶ RUN AUTO
          </button>
          <button class="input-std action-btn warn" onclick={resetSimulation}>
            ↺ RESET
          </button>
        </div>

        <!-- Live Telemetry Display -->
        <div class="sim-terminal">
          <div class="term-line">&gt; SYSTEM_STATUS: {$runtimeStore.status}</div>
          <div class="term-line">&gt; ACTIVE_AGENTS: {$agentNodes.filter(n => n.status === 'running').length}</div>
          <div class="term-line">&gt; GRAPH_NODES: {$agentNodes.length}</div>
          {#if $runtimeStore.runId}
            <div class="term-line highlight">&gt; SESSION_ID: {$runtimeStore.runId}</div>
          {/if}
          <div class="term-cursor">_</div>
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
  /* === EXISTING STYLES === */
  #control-deck {
    height: 185px;
    background: var(--paper-bg);
    border-top: 1px solid var(--paper-line);
    display: flex;
    flex-direction: column;
    transition: height 0.5s var(--ease-snap), background 0.3s, border-color 0.3s;
    position: relative;
    z-index: 150;
  }
  #control-deck.architect-mode { height: 260px; }

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
  
  .nav-item:hover { color: var(--paper-ink); background: var(--paper-bg); }
  .nav-item.active { background: var(--paper-bg); color: var(--paper-ink); border-bottom: 2px solid var(--paper-ink); }
  .nav-item.node-tab { flex: 4; justify-content: flex-start; padding-left: 20px; background: var(--paper-bg); color: var(--paper-ink); border-bottom: 2px solid var(--paper-ink); cursor: default; }
  .action-close { flex: 0; min-width: 50px; font-size: 16px; color: #d32f2f; border-right: none; border-left: 1px solid var(--paper-line); }
  .action-close:hover { background: var(--paper-surface-dim); color: #b71c1c; }

  .pane-container { flex: 1; overflow: hidden; position: relative; display: flex; flex-direction: column; }
  .deck-pane { flex: 1; height: 100%; padding: 20px; overflow-y: auto; }

  #pane-input { display: flex; flex-direction: column; justify-content: center; padding-bottom: 8px; }

  /* === CONTEXT RACK === */
  .context-rack { margin-bottom: 12px; display: flex; align-items: center; gap: 12px; animation: slideDown 0.2s ease-out; }
  .rack-label { font-family: var(--font-code); font-size: 9px; color: var(--paper-line); font-weight: 700; letter-spacing: 1px; flex-shrink: 0; }
  .rack-files { display: flex; gap: 8px; flex-wrap: wrap; overflow: hidden; }
  .ctx-chip { font-family: var(--font-code); font-size: 9px; color: var(--paper-ink); background: var(--paper-surface); border: 1px solid var(--paper-line); padding: 2px 6px; border-radius: 2px; display: flex; align-items: center; gap: 6px; white-space: nowrap; cursor: default; }
  .ctx-dot { width: 4px; height: 4px; background: var(--alert-amber); border-radius: 50%; box-shadow: 0 0 4px var(--alert-amber); }
  
  @keyframes slideDown { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

  .cmd-wrapper { display: flex; background: var(--paper-bg); border: 1px solid var(--paper-line); height: 80px; transition: border-color 0.2s, box-shadow 0.2s; }
  .cmd-wrapper.mode-plan { border-color: var(--arctic-cyan); box-shadow: 0 0 10px rgba(0, 240, 255, 0.15); }
  .cmd-wrapper.focused { border-color: var(--paper-ink); box-shadow: 0 4px 12px rgba(0,0,0,0.05); }

  #cmd-input { flex: 1; border: none; background: transparent; padding: 16px; font-family: var(--font-code); font-size: 13px; color: var(--paper-ink); resize: none; outline: none; }
  #cmd-input::placeholder { opacity: 0.4; text-transform: uppercase; color: var(--paper-ink); }

  #btn-run { width: 60px; border: none; border-left: 1px solid var(--paper-line); background: var(--paper-surface); color: var(--paper-ink); font-weight: 900; font-size: 20px; cursor: pointer; transition: all 0.1s; display: flex; align-items: center; justify-content: center; }
  .cmd-wrapper.mode-plan #btn-run { color: var(--arctic-cyan); }
  #btn-run:hover:not(:disabled) { background: var(--paper-ink); color: var(--paper-bg); }
  #btn-run:active:not(:disabled) { opacity: 0.8; }
  #btn-run:disabled { background: var(--paper-surface-dim); color: var(--paper-line); cursor: not-allowed; }

  .deck-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; padding: 0 4px; width: 100%; }
  .input-hint { font-family: var(--font-code); font-size: 9px; color: var(--paper-line); text-align: right; letter-spacing: 0.5px; }

  .mode-toggle { display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; opacity: 0.8; transition: opacity 0.2s; outline: none; }
  .mode-toggle:hover { opacity: 1; }
  .mode-toggle:focus-visible { outline: 1px dotted var(--arctic-cyan); outline-offset: 2px; }

  .toggle-label { font-family: var(--font-code); font-size: 9px; font-weight: 700; letter-spacing: 1px; transition: color 0.3s; }
  .toggle-label.active { color: var(--paper-ink); }
  .toggle-label.dim { color: var(--paper-line); }

  .toggle-track { width: 28px; height: 12px; background: var(--paper-surface); border: 1px solid var(--paper-line); border-radius: 6px; position: relative; transition: background 0.2s; }
  .toggle-thumb { width: 8px; height: 8px; background: var(--paper-ink); border-radius: 50%; position: absolute; top: 1px; transition: left 0.2s var(--ease-snap), background 0.2s; }
  .cmd-wrapper.mode-plan + .deck-footer .mode-toggle .toggle-thumb { background: var(--arctic-cyan); }

  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .form-group { margin-bottom: 16px; }
  
  label { display: block; font-size: 9px; color: var(--paper-line); text-transform: uppercase; margin-bottom: 6px; font-weight: 600; }
  .input-std { width: 100%; padding: 10px; border: 1px solid var(--paper-line); background: var(--paper-bg); font-family: var(--font-code); font-size: 12px; color: var(--paper-ink); outline: none; }
  .input-std:focus { border-color: var(--paper-ink); }
  .input-readonly { background: var(--paper-surface); color: var(--paper-line); cursor: default; }
  .status-indicator { color: #00C853; font-weight: 700; font-size: 11px; margin-top: 10px; }
  
  .action-btn { width: auto; cursor: pointer; background: var(--paper-ink); color: var(--paper-bg); border: 1px solid var(--paper-ink); }
  .action-btn:hover { background: var(--paper-bg); color: var(--paper-ink); }
  
  /* Simulation Pane Styles */
  .sim-controls { display: flex; flex-direction: row; gap: 10px; margin-bottom: 16px; }

  .action-btn.warn { border-color: #d32f2f; color: #d32f2f; background: transparent; }
  .action-btn.warn:hover { background: #d32f2f; color: var(--paper-bg); }

  .action-btn.primary { border-color: var(--arctic-cyan); color: var(--arctic-cyan); background: rgba(0, 240, 255, 0.05); font-weight: 900; }
  .action-btn.primary:hover { background: var(--arctic-cyan); color: #000; }

  .action-btn.auto { border-color: var(--alert-amber); color: var(--alert-amber); background: rgba(255, 179, 0, 0.05); font-weight: 700; }
  .action-btn.auto:hover { background: var(--alert-amber); color: #000; }

  .sim-terminal { font-family: var(--font-code); font-size: 11px; color: var(--paper-ink); background: var(--paper-bg); border: 1px solid var(--paper-line); padding: 10px; height: 100px; overflow-y: auto; }
  .term-line { margin-bottom: 4px; }
  .term-line.highlight { color: var(--arctic-cyan); }
  .term-cursor { animation: blink 1s infinite; }
  @keyframes blink { 0%, 50% { opacity: 1; } 51%, 100% { opacity: 0; } }

  .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
  .stat-card { border: 1px solid var(--paper-line); background: var(--paper-bg); padding: 12px; text-align: center; }
  .stat-val { font-size: 16px; font-weight: 700; color: var(--paper-ink); display: block; }
  .stat-lbl { font-size: 9px; color: var(--paper-line); text-transform: uppercase; margin-top: 4px; display: block; }

  /* Slider & Loader styles from previous implementation omitted for brevity but assumed present */
  .deep-think-config { padding: 16px; background: var(--paper-surface-dim); border: 1px solid var(--paper-line); border-radius: 0; }
  .slider-value-badge { font-size: 10px; background: var(--paper-ink); color: var(--paper-bg); padding: 2px 6px; border-radius: 2px; }
  .slider-container { display: flex; align-items: center; margin: 12px 0; }
  .thinking-slider { flex: 1; -webkit-appearance: none; height: 4px; background: var(--paper-line); outline: none; }
  .thinking-slider::-webkit-slider-thumb { -webkit-appearance: none; width: 16px; height: 16px; border-radius: 0; background: var(--paper-ink); cursor: ew-resize; border: 2px solid var(--paper-bg); box-shadow: 0 1px 3px rgba(0,0,0,0.3); transition: transform 0.1s; }
  .thinking-slider::-webkit-slider-thumb:hover { transform: scale(1.2); }
  .slider-description { font-size: 11px; color: var(--paper-line); font-style: italic; min-height: 1.2em; }
  .loader { width: 16px; height: 16px; border: 2px solid var(--paper-line); border-bottom-color: transparent; border-radius: 50%; display: inline-block; box-sizing: border-box; animation: rotation 1s linear infinite; }
  @keyframes rotation { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

  /* Patch Bay Matrix Styles */
  #pane-pipeline { padding: 0 !important; overflow: hidden; }
  .deck-pane-wrapper { display: flex; height: 100%; overflow: hidden; }

  /* LEFT COL: ACTIONS & LIST */
  .sidebar { width: 200px; border-right: 1px solid var(--paper-line); padding: 12px; display: flex; flex-direction: column; gap: 12px; background: var(--paper-surface); flex-shrink: 0; }
  .section-header { font-size: 10px; font-weight: 700; color: var(--paper-line); text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid var(--paper-line); padding-bottom: 4px; margin-bottom: 4px; }
  .node-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
  .node-item { display: flex; align-items: center; justify-content: space-between; background: var(--paper-surface-dim); border: 1px solid var(--paper-line); padding: 6px 8px; font-size: 10px; color: var(--paper-ink); cursor: default; transition: border-color 0.2s; }
  .node-item:hover { border-color: var(--paper-ink); }
  .node-item .del-btn { opacity: 0.3; cursor: pointer; font-size: 16px; line-height: 1; transition: opacity 0.2s, color 0.2s; }
  .node-item .del-btn:hover { opacity: 1; color: #d32f2f; }
  .add-node-btn { background: transparent; border: 1px dashed var(--paper-line); color: var(--paper-line); padding: 8px; font-size: 10px; cursor: pointer; text-align: center; text-transform: uppercase; transition: all 0.2s; font-family: var(--font-code); font-weight: 600; }
  .add-node-btn:hover { border-color: var(--arctic-cyan); color: var(--arctic-cyan); }

  /* RIGHT COL: THE PATCH BAY (MATRIX) */
  .matrix-wrapper { flex: 1; display: flex; flex-direction: column; overflow: hidden; position: relative; }
  .matrix-scroll { overflow: auto; padding: 10px 12px; scrollbar-width: thin; scrollbar-color: var(--paper-line) var(--paper-bg); }

  /* THE GRID */
  .patch-grid { display: grid; gap: 1px; }

  /* CORNER (Empty) */
  .corner-cell { grid-column: 1; grid-row: 1; border-bottom: 1px solid var(--paper-line); border-right: 1px solid var(--paper-line); background: var(--paper-bg); z-index: 20; position: sticky; top: 0; left: 0; height: 50px; }
  .corner-hint { position: absolute; bottom: 2px; right: 4px; font-size: 7px; color: var(--paper-line); text-align: right; line-height: 1.1; }

  /* COLUMN HEADERS (Targets) */
  .col-header { grid-row: 1; height: 50px; display: flex; align-items: flex-end; justify-content: center; padding-bottom: 6px; position: sticky; top: 0; background: var(--paper-bg); z-index: 10; border-bottom: 1px solid var(--paper-line); }
  .v-text { writing-mode: vertical-rl; transform: rotate(180deg); font-size: 8px; color: var(--paper-line); font-weight: 700; white-space: nowrap; letter-spacing: 0.5px; cursor: default; }

  /* ROW HEADERS (Sources) */
  .row-header { grid-column: 1; display: flex; align-items: center; justify-content: flex-end; padding-right: 8px; font-size: 8px; color: var(--paper-line); font-weight: 700; position: sticky; left: 0; background: var(--paper-bg); z-index: 10; border-right: 1px solid var(--paper-line); text-transform: uppercase; }

  /* THE CELL */
  .cell { width: 28px; height: 28px; background: var(--paper-surface-dim); border: 1px solid var(--paper-line); cursor: pointer; position: relative; display: flex; align-items: center; justify-content: center; transition: all 0.1s; outline: none; }
  .cell:hover:not(.disabled) { border-color: var(--paper-ink); background: var(--paper-surface); }
  .cell:focus-visible:not(.disabled) { border-color: var(--arctic-cyan); box-shadow: 0 0 0 2px rgba(0, 240, 255, 0.2); }

  /* Disabled (Self-Loop) */
  .cell.disabled { background: repeating-linear-gradient( 45deg, var(--paper-bg), var(--paper-bg) 2px, var(--paper-line) 2px, var(--paper-line) 3px ); cursor: not-allowed; opacity: 0.3; }
  .cell.disabled:hover { border-color: var(--paper-line); }

  /* Active (Connected) */
  .cell.active { background: rgba(0, 240, 255, 0.1); border-color: var(--arctic-cyan); box-shadow: inset 0 0 8px rgba(0, 240, 255, 0.2); }

  /* The "LED" inside the cell */
  .led { width: 6px; height: 6px; background: var(--paper-line); border-radius: 50%; transition: all 0.2s; }
  .cell.active .led { background: var(--arctic-cyan); box-shadow: 0 0 6px var(--arctic-cyan); }

  /* Hover Guides */
  .cell:not(.disabled):hover::after { content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0; border: 1px solid var(--paper-ink); pointer-events: none; }
  /* [[NEW]] Tool Configuration Styles */
  .tool-config { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--paper-line); }
  .tool-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 8px; }
  .tool-btn {
    background: var(--paper-bg); border: 1px solid var(--paper-line);
    padding: 10px 4px; display: flex; flex-direction: column; align-items: center; gap: 4px;
    cursor: pointer; border-radius: 2px; transition: all 0.2s; opacity: 0.7;
    position: relative;
  }
  .tool-btn:hover:not(.locked) { opacity: 1; border-color: var(--paper-ink); }
  .tool-btn.active {
    background: var(--paper-surface-dim);
    border-color: var(--arctic-cyan);
    color: var(--paper-ink);
    opacity: 1;
    box-shadow: inset 0 0 8px rgba(0, 240, 255, 0.1);
  }
  .tool-btn.locked {
    cursor: default;
    border-color: var(--alert-amber);
    background: rgba(255, 179, 0, 0.05);
  }
  .tool-btn.locked.active {
    border-color: var(--alert-amber);
    box-shadow: inset 0 0 8px rgba(255, 179, 0, 0.15);
  }
  .tool-icon { font-size: 16px; }
  .tool-label { font-family: var(--font-code); font-size: 8px; font-weight: 700; }
  .lock-indicator {
    font-size: 10px;
    position: absolute;
    top: 2px;
    right: 2px;
    opacity: 0.7;
  }
  .tool-hint { font-size: 9px; color: var(--paper-line); margin-top: 6px; font-style: italic; }

  .directive-port-config { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--paper-line); }
  .port-toggle { padding: 6px 14px; border: 1px solid; font-family: var(--font-code); font-size: 10px; font-weight: 700; cursor: pointer; transition: all 0.2s; letter-spacing: 0.5px; }
  .port-toggle.port-open { background: var(--paper-bg); color: var(--arctic-lilac, #00E5FF); border-color: var(--arctic-cyan, #00E5FF); box-shadow: 0 0 8px rgba(0, 229, 255, 0.3); }
  .port-toggle.port-open:hover { background: var(--arctic-lilac); color: var(--paper-bg); }
  .port-toggle.port-closed { background: var(--paper-bg); color: var(--paper-line); border-color: var(--paper-line); }
  .port-toggle.port-closed:hover { border-color: var(--paper-ink); color: var(--paper-ink); }
  .directive-hint { margin-top: 8px; padding: 8px; background: var(--paper-surface-dim); border-left: 2px solid; font-family: var(--font-code); font-size: 10px; }
  .hint-active { color: var(--arctic-cyan, #00E5FF); border-color: var(--arctic-cyan, #00E5FF); }
  .hint-inactive { color: var(--paper-line); border-color: var(--paper-line); }
</style>