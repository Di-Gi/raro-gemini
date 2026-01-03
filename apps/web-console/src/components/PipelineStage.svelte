<!-- // [[RARO]]/apps/web-console/src/components/PipelineStage.svelte
// Purpose: Interactive DAG visualization with "Chip" aesthetic.
// Architecture: Visual Component (D3-lite)
// Dependencies: Stores -->

<script lang="ts">
  import { 
    agentNodes, 
    pipelineEdges, 
    selectedNode, 
    selectNode, 
    deselectNode, // Added import
    runtimeStore, 
    planningMode,
    type PipelineEdge,
    type AgentNode
  } from '$lib/stores'

  let { expanded, ontoggle }: { expanded: boolean, ontoggle?: () => void } = $props();

  // Reactive state for DOM element bindings
  let svgElement = $state<SVGSVGElement | undefined>();
  let nodesLayer = $state<HTMLDivElement | undefined>();
  let pipelineStageElement = $state<HTMLDivElement | undefined>();

  let isRunComplete = $derived($runtimeStore.status === 'COMPLETED' || $runtimeStore.status === 'FAILED');

  // CLEANUP HOOK: Clear selection when minimizing
  $effect(() => {
    if (!expanded && $selectedNode) {
        deselectNode();
    }
  });

  $effect(() => {
    if (!pipelineStageElement) return;
    const resizeObserver = new ResizeObserver(() => {
      renderGraph();
    });
    resizeObserver.observe(pipelineStageElement);
    return () => {
      resizeObserver.disconnect();
    };
  })

  function renderGraph() {
    if (!svgElement || !nodesLayer || !pipelineStageElement) return

    const svg = svgElement
    const w = pipelineStageElement.clientWidth
    const h = pipelineStageElement.clientHeight

    // === 1. CALCULATE MINIMIZED POSITIONS (CLUSTERING) ===
    // We calculate this regardless of state to ensure smooth transitions
    
    // Group nodes by their approximate X coordinate (Rank)
    const clusters = new Map<number, AgentNode[]>();
    const sortedNodes = [...$agentNodes].sort((a, b) => {
        if (Math.abs(a.x - b.x) > 2) return a.x - b.x;
        return a.id.localeCompare(b.id);
    });

    sortedNodes.forEach(node => {
        const rankKey = Math.round(node.x / 5) * 5; // Quantize X to nearest 5%
        if (!clusters.has(rankKey)) clusters.set(rankKey, []);
        clusters.get(rankKey)!.push(node);
    });

    const nodeOffsets = new Map<string, number>();
    
    clusters.forEach((clusterNodes) => {
        const count = clusterNodes.length;
        if (count === 1) {
            nodeOffsets.set(clusterNodes[0].id, 0);
            return;
        }
        
        // Spread logic: Tighter spacing for the "Fuse" look
        const SPACING = 24; 
        const totalSpread = (count - 1) * SPACING;
        const startOffset = -totalSpread / 2;

        clusterNodes.forEach((node, index) => {
            nodeOffsets.set(node.id, startOffset + (index * SPACING));
        });
    });

    // === 2. COORDINATE FUNCTIONS ===
    const getY = (n: AgentNode) => {
        return expanded ? (n.y / 100) * h : h / 2;
    }

    const getX = (n: AgentNode) => {
        const baseX = (n.x / 100) * w;
        if (expanded) return baseX;
        return baseX + (nodeOffsets.get(n.id) || 0);
    }

    // === 3. RENDER EDGES ===
    svg.innerHTML = ''

    $pipelineEdges.forEach((link: PipelineEdge) => {
      const fromNode = $agentNodes.find((n) => n.id === link.from)
      const toNode = $agentNodes.find((n) => n.id === link.to)

      if (!fromNode || !toNode) return

      const x1 = getX(fromNode)
      const y1 = getY(fromNode)
      const x2 = getX(toNode)
      const y2 = getY(toNode)

      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
      
      const curvature = expanded ? 60 : 20;
      const d = `M ${x1} ${y1} C ${x1 + curvature} ${y1}, ${x2 - curvature} ${y2}, ${x2} ${y2}`

      path.setAttribute('d', d)
      
      let classes = `cable`;
      if (link.active) classes += ` active`;
      if (link.pulseAnimation) classes += ` pulse`;
      if (link.finalized) classes += ` finalized`;
      
      path.setAttribute('class', classes);
      path.setAttribute('id', `link-${link.from}-${link.to}`)

      if (link.signatureHash) {
        path.setAttribute('data-signature', link.signatureHash)
      }

      svg.appendChild(path)
    })

    // === 4. RENDER NODES ===
    if (nodesLayer) {
      nodesLayer.innerHTML = ''
      $agentNodes.forEach((node) => {
        const el = document.createElement('div')
        
        el.className = `node ${$selectedNode === node.id ? 'selected' : ''} ${
          node.status === 'running' ? 'running' : ''
        } ${node.status === 'complete' ? 'complete' : ''}`

        const role = node.role ? node.role.toUpperCase() : 'WORKER';

        el.innerHTML = `
            <!-- EXPANDED CONTENT -->
            <div class="node-indicator"></div>
            <div class="node-content">
                <div class="node-role">${role}</div>
                <div class="node-label">${node.label}</div>
            </div>
            <div class="node-decor"></div>
            
            <!-- MINIMIZED CONTENT (The Fuse) -->
            <div class="fuse-cap top"></div>
            <div class="fuse-filament"></div>
            <div class="fuse-cap bottom"></div>
        `
        
        el.id = `node-${node.id}`

        const x = getX(node)
        const y = getY(node)

        el.style.left = `${x}px`
        el.style.top = `${y}px`
        
        const zIndexBase = node.status === 'running' ? 100 : 10;
        el.style.zIndex = `${zIndexBase}`;

        el.onclick = (e) => {
          if (!expanded) {
            // If minimized, bubble up to container to trigger toggle
            return; 
          }
          e.stopPropagation()
          selectNode(node.id)
        }

        nodesLayer!.appendChild(el)
      })
    }
  }

  // React to changes
  $effect(() => {
    // Dependencies to trigger re-render
    const _expanded = expanded;
    const _nodeCount = $agentNodes.length; 
    const _edgeCount = $pipelineEdges.length;
    const _nodes = $agentNodes;
    const _selected = $selectedNode;
    const _status = $runtimeStore.status;

    requestAnimationFrame(() => {
      renderGraph();
    });
  })

  function handleClick() {
    if (!expanded) {
      ontoggle?.()
    }
  }
</script>

<div
  id="pipeline-stage"
  class="{expanded ? 'expanded' : ''} {isRunComplete ? 'run-complete' : ''}"
  onclick={handleClick}
  onkeydown={(e) => e.key === 'Enter' && handleClick()}
  role="button"
  tabindex="0"
  bind:this={pipelineStageElement}
>
  <div id="hud-banner">
    <div class="hud-title">
      {#if isRunComplete}
         <div class="hud-status-dot complete"></div>
         SESSION COMPLETE // DATA HARDENED
      {:else if $runtimeStore.status === 'RUNNING'}
         <div class="hud-status-dot active"></div>
         PIPELINE ACTIVE // PROCESSING
      {:else if $planningMode}
         <div class="hud-status-dot blueprint"></div>
         BLUEPRINT MODE // ARCHITECT ACTIVE
      {:else}
         <div class="hud-status-dot"></div>
         READY // EXECUTION MODE
      {/if}
    </div>
    <button
      class="btn-minimize"
      onclick={(e) => {
        e.stopPropagation()
        ontoggle?.()
      }}
    >
      ▼ MINIMIZE
    </button>
  </div>

  <svg id="graph-svg" bind:this={svgElement}></svg>
  <div id="nodes-layer" bind:this={nodesLayer}></div>
</div>

<style>
  #pipeline-stage {
    height: 80px;
    background: var(--digi-void);
    border-top: 1px solid var(--paper-line);
    border-bottom: 1px solid var(--paper-line);
    position: relative;
    z-index: 100;
    transition: height 0.5s var(--ease-snap), border-color 0.3s;
    overflow: hidden;
    cursor: pointer;
    background-image: 
        linear-gradient(color-mix(in srgb, var(--digi-line), transparent 50%) 1px, transparent 1px),
        linear-gradient(90deg, color-mix(in srgb, var(--digi-line), transparent 50%) 1px, transparent 1px);
    background-size: 40px 40px;
  }

  #pipeline-stage.expanded {
    height: 65vh;
    cursor: default;
    box-shadow: 0 20px 80px rgba(0, 0, 0, 0.4);
    border-top: 1px solid var(--digi-line);
  }

  #pipeline-stage.expanded.run-complete {
    border-top: 1px solid var(--arctic-cyan);
    border-bottom: 1px solid var(--arctic-cyan);
  }

  #hud-banner {
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 32px;
    background: var(--digi-void);
    border-bottom: 1px solid var(--digi-line);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 12px;
    transform: translateY(-100%);
    transition: transform 0.3s ease;
    z-index: 200;
  }

  #pipeline-stage.expanded #hud-banner {
    transform: translateY(0);
  }

  .hud-title {
    color: var(--digi-text-dim);
    font-family: var(--font-code);
    font-size: 10px;
    letter-spacing: 1px;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  
  .run-complete .hud-title { color: var(--arctic-cyan); }

  .hud-status-dot {
    width: 6px; height: 6px;
    background: var(--digi-text-dim);
    border-radius: 50%;
  }

  .hud-status-dot.active {
    background: var(--alert-amber);
    box-shadow: 0 0 8px var(--alert-amber);
    animation: blink 1s infinite alternate;
  }

  .hud-status-dot.complete {
    background: var(--arctic-cyan);
    box-shadow: 0 0 8px var(--arctic-cyan);
  }

  .hud-status-dot.blueprint {
    background: var(--arctic-cyan);
    box-shadow: 0 0 8px var(--arctic-cyan);
    animation: pulse 2s infinite;
  }

  .btn-minimize {
    background: transparent;
    border: none;
    color: var(--digi-text-dim);
    font-size: 10px;
    font-family: var(--font-code);
    cursor: pointer;
    transition: color 0.2s;
  }
  .btn-minimize:hover { color: var(--arctic-cyan); }

  #graph-svg {
    width: 100%; height: 100%;
    position: absolute; top: 0; left: 0;
  }

  #nodes-layer {
    width: 100%; height: 100%;
    position: absolute; top: 0; left: 0;
  }

  /* === NODE STYLING === */
  
  :global(.node) {
    position: absolute;
    transform: translate(-50%, -50%);
    transition: all 0.5s var(--ease-snap);
    user-select: none;
    display: flex;
    
    /* DEFAULT: MINIMIZED FUSE AESTHETIC */
    width: 14px;
    height: 36px;
    background: #000;
    border: 1px solid var(--digi-line);
    border-radius: 2px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.5);
    flex-direction: column;
    align-items: center;
    justify-content: space-between;
    padding: 2px 0;
    overflow: visible; /* Allow glow to spill */
    pointer-events: none;
  }

  /* --- FUSE ELEMENTS (MINIMIZED ONLY) --- */
  :global(.fuse-cap) {
    width: 8px;
    height: 2px;
    background: var(--digi-text-dim);
    opacity: 0.5;
  }
  
  :global(.fuse-filament) {
    width: 2px;
    flex: 1;
    background: var(--digi-line);
    margin: 2px 0;
    transition: background 0.3s, box-shadow 0.3s;
  }

  /* Hover effect for Minimized Nodes */
  #pipeline-stage:not(.expanded) :global(.node):hover {
    transform: translate(-50%, -55%) scale(1.1);
    border-color: var(--arctic-cyan);
    z-index: 200 !important;
  }

  /* --- EXPANDED CARD OVERRIDES --- */
  
  #pipeline-stage.expanded :global(.node) {
    width: auto;
    min-width: 140px;
    height: auto;
    background: var(--digi-panel);
    border-radius: 0;
    padding: 0;
    align-items: stretch;
    justify-content: flex-start;
    flex-direction: row;
    box-shadow: 0 4px 10px rgba(0,0,0,0.3);
    overflow: hidden;
    pointer-events: auto;
    cursor: pointer;
  }

  /* Hide Fuse elements in expanded */
  #pipeline-stage.expanded :global(.node) :global(.fuse-cap),
  #pipeline-stage.expanded :global(.node) :global(.fuse-filament) {
    display: none;
  }

  /* --- CARD ELEMENTS (EXPANDED ONLY) --- */
  
  :global(.node-indicator), 
  :global(.node-content), 
  :global(.node-decor) {
    display: none; /* Hidden by default (minimized) */
  }

  #pipeline-stage.expanded :global(.node) :global(.node-indicator) { 
    display: block; 
    width: 4px;
    background: var(--digi-line);
    transition: background 0.3s;
  }

  #pipeline-stage.expanded :global(.node) :global(.node-content) { 
    display: flex;
    flex: 1;
    padding: 8px 12px;
    flex-direction: column;
    gap: 2px;
  }

  #pipeline-stage.expanded :global(.node) :global(.node-decor) { 
    display: block;
    width: 12px;
    background: repeating-linear-gradient(0deg, transparent, transparent 2px, var(--digi-line) 2px, var(--digi-line) 3px);
    opacity: 0.3;
    border-left: 1px solid var(--digi-line);
  }

  :global(.node-role) {
    font-size: 8px;
    text-transform: uppercase;
    color: var(--digi-text-dim);
    opacity: 0.7;
    letter-spacing: 0.5px;
  }

  :global(.node-label) {
    font-size: 11px;
    font-weight: 600;
    color: var(--digi-text);
  }

  /* --- STATE STYLING (Shared Mapping) --- */

  /* RUNNING */
  :global(.node.running) {
    border-color: var(--alert-amber);
  }
  /* Card */
  :global(.node.running .node-indicator) { 
    background: var(--alert-amber);
    box-shadow: 0 0 8px var(--alert-amber);
  }
  :global(.node.running .node-label) { color: var(--alert-amber); }
  /* Fuse */
  :global(.node.running .fuse-filament) {
    background: var(--alert-amber);
    box-shadow: 0 0 8px var(--alert-amber), 0 0 4px #fff;
    width: 4px; /* Thicker when running */
    animation: flickerglow 0.1s infinite alternate;
  }

  /* COMPLETE */
  :global(.node.complete) {
    border-color: var(--signal-success);
  }
  /* Card */
  :global(.node.complete .node-indicator) { background: var(--signal-success); }
  :global(.node.complete .node-label) { color: var(--signal-success); }
  /* Fuse */
  :global(.node.complete .fuse-filament) {
    background: var(--signal-success);
    box-shadow: 0 0 4px var(--signal-success);
  }

  /* SELECTED (Expanded Only) */
  :global(.node.selected) {
    border-color: var(--arctic-cyan);
    background: var(--arctic-dim);
  }
  :global(.node.selected .node-indicator) { background: var(--arctic-cyan); }
  :global(.node.selected .node-label) { color: var(--arctic-cyan); }

  /* --- HOVER (Expanded Only) --- */
  #pipeline-stage.expanded :global(.node:hover) {
    border-color: var(--arctic-cyan);
    transform: translate(-50%, -52%);
    box-shadow: 0 8px 20px rgba(0,0,0,0.5);
  }
  #pipeline-stage.expanded :global(.node:hover .node-label) { color: white; }

  /* --- CABLES --- */
  :global(.cable) {
    fill: none;
    stroke: var(--digi-line);
    stroke-width: 1.5px;
    transition: stroke 0.3s;
  }

  :global(.cable.active) {
    stroke: var(--arctic-cyan);
    stroke-dasharray: 8 4;
    animation: flow 0.6s linear infinite;
    opacity: 0.9;
  }

  :global(.cable.active.pulse) {
    stroke-width: 2.5px;
    filter: drop-shadow(0 0 6px var(--arctic-cyan));
  }

  :global(.cable.finalized) {
    stroke: var(--arctic-cyan);
    stroke-width: 1.5px;
    opacity: 0.6;
  }

  @keyframes flow {
    to { stroke-dashoffset: -12; }
  }
  @keyframes blink {
    from { opacity: 0.4; }
    to { opacity: 1; }
  }
  @keyframes pulse {
    0% { opacity: 1; }
    50% { opacity: 0.5; }
    100% { opacity: 1; }
  }
  @keyframes flickerglow {
      0% { opacity: 0.8; }
      100% { opacity: 1; box-shadow: 0 0 12px var(--alert-amber); }
  }
</style>