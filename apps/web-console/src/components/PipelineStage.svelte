<!-- // [[RARO]]/apps/web-console/src/components/PipelineStage.svelte
// Purpose: Interactive DAG visualization with "Tactical Arctic" aesthetic.
// Architecture: Visual Component (D3-lite) with DOM Diffing
// Dependencies: Stores -->

<script lang="ts">
  import { 
    agentNodes, 
    pipelineEdges, 
    selectedNode, 
    selectNode, 
    deselectNode, 
    runtimeStore, 
    planningMode,
    type PipelineEdge,
    type AgentNode
  } from '$lib/stores'

  let { expanded, ontoggle }: { expanded: boolean, ontoggle?: () => void } = $props();

  // DOM Bindings
  let svgElement = $state<SVGSVGElement | undefined>();
  let nodesLayer = $state<HTMLDivElement | undefined>();
  let pipelineStageElement = $state<HTMLDivElement | undefined>();

  let isRunComplete = $derived($runtimeStore.status === 'COMPLETED' || $runtimeStore.status === 'FAILED');

  // CLEANUP: Clear selection on minimize
  $effect(() => {
    if (!expanded && $selectedNode) deselectNode();
  });

  // === REACTIVITY ENGINE ===
  // We explicitly track store dependencies here to trigger the render loop.
  $effect(() => {
    if (!pipelineStageElement) return;

    // Register Dependencies
    const _nodes = $agentNodes;
    const _edges = $pipelineEdges;
    const _selected = $selectedNode;
    const _status = $runtimeStore.status;
    const _expanded = expanded;

    // Use RAF for smooth UI updates without blocking
    requestAnimationFrame(() => {
      renderGraph();
    });
  });

  // RESIZE OBSERVER (Handles window/container shifts)
  $effect(() => {
    if (!pipelineStageElement) return;
    const observer = new ResizeObserver(() => renderGraph());
    observer.observe(pipelineStageElement);
    return () => observer.disconnect();
  })

  function renderGraph() {
      if (!svgElement || !nodesLayer || !pipelineStageElement) return

      const svg = svgElement
      const w = pipelineStageElement.clientWidth
      const h = pipelineStageElement.clientHeight

      // === 1. CLUSTERING FOR MINIMIZED VIEW ===
      const clusters = new Map<number, AgentNode[]>();
      const sortedNodes = [...$agentNodes].sort((a, b) => {
          if (Math.abs(a.x - b.x) > 2) return a.x - b.x;
          return a.id.localeCompare(b.id);
      });

      sortedNodes.forEach(node => {
          const rankKey = Math.round(node.x / 5) * 5; 
          if (!clusters.has(rankKey)) clusters.set(rankKey, []);
          clusters.get(rankKey)!.push(node);
      });

      const nodeOffsets = new Map<string, number>();
      clusters.forEach((clusterNodes) => {
          const count = clusterNodes.length;
          if (count === 1) { nodeOffsets.set(clusterNodes[0].id, 0); return; }
          const SPACING = 24; 
          const startOffset = -((count - 1) * SPACING) / 2;
          clusterNodes.forEach((node, index) => {
              nodeOffsets.set(node.id, startOffset + (index * SPACING));
          });
      });

      // === 2. COORDINATE MAPPING ===
      const getY = (n: AgentNode) => expanded ? (n.y / 100) * h : h / 2;
      const getX = (n: AgentNode) => {
          const baseX = (n.x / 100) * w;
          return expanded ? baseX : baseX + (nodeOffsets.get(n.id) || 0);
      }

      // === 3. RENDER EDGES (Smart Diffing) ===
      const nodeHalfWidth = expanded ? 80 : 6;
      
      // Mark all current edges as "seen" to handle removal
      const activeEdgeIds = new Set<string>();

      $pipelineEdges.forEach((link: PipelineEdge) => {
        const edgeId = `link-${link.from}-${link.to}`;
        activeEdgeIds.add(edgeId);

        const fromNode = $agentNodes.find((n) => n.id === link.from)
        const toNode = $agentNodes.find((n) => n.id === link.to)
        if (!fromNode || !toNode) return

        const centerX1 = getX(fromNode)
        const centerY1 = getY(fromNode)
        const centerX2 = getX(toNode)
        const centerY2 = getY(toNode)

        // Ports: Right of source, Left of target
        const x1 = centerX1 + nodeHalfWidth; 
        const y1 = centerY1;
        const x2 = centerX2 - nodeHalfWidth;
        const y2 = centerY2;

        // Curve Logic
        const dist = Math.abs(x2 - x1);
        const curvature = Math.max(dist * 0.5, 20);
        const d = `M ${x1} ${y1} C ${x1 + curvature} ${y1}, ${x2 - curvature} ${y2}, ${x2} ${y2}`;
        
        let classes = `cable`;
        if (link.active) classes += ` active`;
        if (link.pulseAnimation) classes += ` pulse`;
        if (link.finalized) classes += ` finalized`;

        // Update or Create
        // FIX: Double Cast to satisfy TS (HTMLElement -> unknown -> SVGPathElement)
        let path = document.getElementById(edgeId) as unknown as SVGPathElement | null;
        
        if (!path) {
            path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.id = edgeId;
            svg.appendChild(path);
        }
        
        // Only touch DOM if changed
        if (path.getAttribute('d') !== d) path.setAttribute('d', d);
        if (path.getAttribute('class') !== classes) path.setAttribute('class', classes);
      });

      // Cleanup Removed Edges
      Array.from(svg.children).forEach(child => {
          if (!activeEdgeIds.has(child.id)) child.remove();
      });


      // === 4. RENDER NODES (Smart Diffing) ===
      const activeNodeIds = new Set<string>();
      
      $agentNodes.forEach((node) => {
        const domId = `node-${node.id}`;
        activeNodeIds.add(domId);

        let el = document.getElementById(domId) as HTMLDivElement;
        const isSel = $selectedNode === node.id;
        const className = `tactical-unit ${isSel ? 'selected' : ''} ${node.status}`;

        // CREATE if missing
        if (!el) {
            el = document.createElement('div');
            el.id = domId;
            // Inner HTML: Military/Arctic Aesthetic
            el.innerHTML = `
              <div class="reticle tl"></div>
              <div class="reticle tr"></div>
              <div class="reticle bl"></div>
              <div class="reticle br"></div>
              <div class="io-port input"></div>
              <div class="io-port output"></div>
              <div class="unit-body">
                  <div class="unit-header">
                      <span class="unit-id">:: ${node.id.toUpperCase().slice(0,6)}</span>
                      <div class="unit-status"></div>
                  </div>
                  <div class="unit-main">
                      <div class="unit-role">${node.role.toUpperCase()}</div>
                      <div class="unit-label">${node.label}</div>
                  </div>
                  <div class="unit-footer">
                      <span class="coord-text"></span>
                  </div>
              </div>
              <div class="isotope-core"></div>
            `;
            
            el.onclick = (e) => {
              if (!expanded) return;
              e.stopPropagation();
              selectNode(node.id);
            }
            nodesLayer!.appendChild(el);
        }

        // UPDATE Attributes
        if (el.className !== className) el.className = className;
        
        const targetLeft = `${getX(node)}px`;
        const targetTop = `${getY(node)}px`;
        
        if (el.style.left !== targetLeft) el.style.left = targetLeft;
        if (el.style.top !== targetTop) el.style.top = targetTop;
        el.style.zIndex = node.status === 'running' ? '100' : '10';

        // Update Coordinates Text (Only if needed)
        const coordEl = el.querySelector('.coord-text');
        if (coordEl) {
            coordEl.textContent = `X:${Math.round(node.x)} Y:${Math.round(node.y)}`;
        }
      });

      // Cleanup Removed Nodes
      if (nodesLayer) {
          Array.from(nodesLayer.children).forEach(child => {
              if (!activeNodeIds.has(child.id)) child.remove();
          });
      }
    }

  function handleClick() {
    if (!expanded) ontoggle?.()
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
  <!-- 1. TACTICAL GRID -->
  <div class="tactical-grid"></div>
  <div class="polar-overlay"></div> <!-- Frost effect -->
  
  <!-- 2. HUD INTERFACE -->
  <div id="hud-banner">
    <div class="hud-left">
        <div class="status-indicator">
            <div class="led"></div>
            {#if isRunComplete}
                <span>SYS_HALT</span>
            {:else if $runtimeStore.status === 'RUNNING'}
                <span>OPERATIONAL</span>
            {:else if $planningMode}
                <span>ARCHITECT</span>
            {:else}
                <span>STANDBY</span>
            {/if}
        </div>
        <div class="separator">/</div>
        <span class="hud-sub">SECURE_CHANNEL_01</span>
    </div>
    
    <button class="btn-minimize" onclick={(e) => { e.stopPropagation(); ontoggle?.(); }}>
        MINIMIZE_VIEW [-]
    </button>
  </div>

  <!-- 3. VISUALIZATION LAYERS -->
  <svg id="graph-svg" bind:this={svgElement}></svg>
  <div id="nodes-layer" bind:this={nodesLayer}></div>
  
</div>

<style>
  /* === PALETTE: TACTICAL ARCTIC === */
  :root {
      --tac-bg: #050505;
      --tac-panel: #0a0a0a;
      --tac-border: #333;
      --tac-cyan: #00F0FF;
      --tac-white: #E0E0E0;
      --tac-dim: #555;
      --tac-amber: #FFB300;
  }

  /* === CHASSIS === */
  #pipeline-stage {
    position: relative;
    height: 80px;
    background: var(--tac-bg);
    border-top: 1px solid var(--paper-line);
    border-bottom: 1px solid var(--paper-line);
    z-index: 100;
    transition: height 0.5s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.3s;
    overflow: hidden;
    cursor: pointer;
  }

  #pipeline-stage.expanded {
    height: 65vh;
    cursor: default;
    border-top: 1px solid var(--tac-cyan);
    border-bottom: 1px solid var(--tac-cyan);
    box-shadow: 0 0 50px rgba(0, 0, 0, 0.8);
  }

  /* === BACKGROUNDS === */
  .tactical-grid {
      position: absolute; top: 0; left: 0; width: 100%; height: 100%;
      background-image: 
          linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
      background-size: 50px 50px;
      pointer-events: none;
      z-index: 0;
  }
  
  /* Adding "Crosshairs" at intersections */
  .tactical-grid::after {
      content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 100%;
      background-image: radial-gradient(circle, rgba(255,255,255,0.1) 1px, transparent 1px);
      background-size: 50px 50px;
      background-position: -25px -25px; /* Offset to intersect */
  }

  .polar-overlay {
      position: absolute; top: 0; left: 0; width: 100%; height: 100%;
      background: radial-gradient(circle at 50% 0%, rgba(0, 240, 255, 0.03), transparent 70%);
      pointer-events: none;
      z-index: 1;
  }

  /* === HUD === */
  #hud-banner {
    position: absolute; top: 0; left: 0; right: 0;
    height: 32px;
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 16px;
    z-index: 50;
    transform: translateY(-100%);
    transition: transform 0.3s;
    background: rgba(5, 5, 5, 0.9);
    border-bottom: 1px solid var(--tac-border);
    font-family: var(--font-code);
  }
  #pipeline-stage.expanded #hud-banner { transform: translateY(0); }

  .hud-left { display: flex; align-items: center; gap: 12px; }
  
  .status-indicator {
      display: flex; align-items: center; gap: 8px;
      font-size: 10px; font-weight: 700; color: var(--tac-white);
      letter-spacing: 1px;
  }
  
  .led {
      width: 4px; height: 4px; background: var(--tac-dim);
      box-shadow: 0 0 4px var(--tac-dim);
  }
  /* Active states via parent context would be cleaner, but simple logic here: */
  #pipeline-stage:not(.run-complete) .led { background: var(--tac-cyan); box-shadow: 0 0 6px var(--tac-cyan); }
  .run-complete .led { background: var(--tac-white); }
  
  .separator { color: var(--tac-dim); font-size: 10px; }
  .hud-sub { font-size: 9px; color: var(--tac-dim); letter-spacing: 0.5px; }

  .btn-minimize {
      background: transparent; border: none;
      font-family: var(--font-code); font-size: 9px; font-weight: 700; 
      color: var(--tac-dim); cursor: pointer; transition: color 0.2s;
  }
  .btn-minimize:hover { color: var(--tac-white); }

  /* === LAYERS === */
  #graph-svg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 5; pointer-events: none; }
  #nodes-layer { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 10; }

  /* === TACTICAL UNIT (NODE) === */
  :global(.tactical-unit) {
      position: absolute;
      transform: translate(-50%, -50%);
      background: var(--tac-bg);
      border: 1px solid var(--tac-border);
      color: var(--tac-white);
      display: flex; flex-direction: column;
      transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
      
      /* Minimized State */
      width: 12px; height: 32px;
      border-radius: 0; /* Hard corners */
  }

  /* --- EXPANDED STATE --- */
  #pipeline-stage.expanded :global(.tactical-unit) {
      width: 160px; height: auto;
      min-height: 50px;
      background: rgba(10, 10, 10, 0.95);
      border: 1px solid var(--tac-border);
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      cursor: pointer;
  }

  /* Hide Minimized Element in Expanded */
  :global(.isotope-core) {
      width: 2px; height: 100%; background: var(--tac-dim); margin: 0 auto;
      transition: background 0.2s;
  }
  #pipeline-stage.expanded :global(.isotope-core) { display: none; }

  /* Hide Expanded Elements in Minimized */
  :global(.reticle), :global(.io-port), :global(.unit-body) { display: none; }

  /* --- EXPANDED DETAILS --- */
  
  /* Target Reticles (Corner Brackets) */
  #pipeline-stage.expanded :global(.reticle) {
      display: block; position: absolute; width: 6px; height: 6px;
      border-color: var(--tac-dim); opacity: 0.5; transition: all 0.2s;
  }
  #pipeline-stage.expanded :global(.reticle.tl) { top: -1px; left: -1px; border-top: 1px solid; border-left: 1px solid; }
  #pipeline-stage.expanded :global(.reticle.tr) { top: -1px; right: -1px; border-top: 1px solid; border-right: 1px solid; }
  #pipeline-stage.expanded :global(.reticle.bl) { bottom: -1px; left: -1px; border-bottom: 1px solid; border-left: 1px solid; }
  #pipeline-stage.expanded :global(.reticle.br) { bottom: -1px; right: -1px; border-bottom: 1px solid; border-right: 1px solid; }
  
  /* Active Hover State on Reticles */
  #pipeline-stage.expanded :global(.tactical-unit:hover .reticle) {
      width: 8px; height: 8px; border-color: var(--tac-cyan); opacity: 1;
  }

  /* IO Ports - ALIGNED WITH CABLE OFFSETS */
  #pipeline-stage.expanded :global(.io-port) {
      display: block; position: absolute; top: 50%; width: 4px; height: 8px;
      background: var(--tac-bg); border: 1px solid var(--tac-dim);
      transform: translateY(-50%);
      z-index: 20;
  }
  #pipeline-stage.expanded :global(.io-port.input) { left: -3px; border-right: none; }
  #pipeline-stage.expanded :global(.io-port.output) { right: -3px; border-left: none; }

  /* Unit Content */
  #pipeline-stage.expanded :global(.unit-body) {
      display: flex; flex-direction: column; width: 100%;
  }

  :global(.unit-header) {
      display: flex; justify-content: space-between; align-items: center;
      padding: 4px 8px; border-bottom: 1px solid var(--tac-border);
      background: rgba(255,255,255,0.02);
  }
  :global(.unit-id) { font-family: var(--font-code); font-size: 8px; color: var(--tac-dim); letter-spacing: 1px; }
  :global(.unit-status) { width: 4px; height: 4px; background: #222; }

  :global(.unit-main) { padding: 8px; display: flex; flex-direction: column; gap: 2px; }
  :global(.unit-role) { font-family: var(--font-code); font-size: 7px; color: var(--tac-dim); text-transform: uppercase; }
  :global(.unit-label) { 
      font-family: var(--font-code); font-size: 10px; font-weight: 700; 
      color: var(--tac-white); text-transform: uppercase; letter-spacing: 0.5px;
  }
  
  :global(.unit-footer) {
      padding: 2px 8px; display: flex; gap: 8px; border-top: 1px solid var(--tac-border);
      background: #020202;
  }
  :global(.coord-text) { font-family: var(--font-code); font-size: 7px; color: #333; }

  /* --- STATES --- */

  /* Running */
  :global(.tactical-unit.running) { border-color: var(--tac-amber); }
  :global(.tactical-unit.running .isotope-core) { background: var(--tac-amber); box-shadow: 0 0 6px var(--tac-amber); }
  :global(.tactical-unit.running .unit-status) { background: var(--tac-amber); box-shadow: 0 0 4px var(--tac-amber); animation: blink 0.2s infinite; }
  :global(.tactical-unit.running .unit-label) { color: var(--tac-amber); }
  
  /* Complete */
  :global(.tactical-unit.complete) { border-color: var(--tac-cyan); }
  :global(.tactical-unit.complete .isotope-core) { background: var(--tac-cyan); }
  :global(.tactical-unit.complete .unit-status) { background: var(--tac-cyan); box-shadow: 0 0 4px var(--tac-cyan); }
  
  /* Selected */
  :global(.tactical-unit.selected) { 
      background: #000; border-color: var(--tac-white); z-index: 200 !important; 
      box-shadow: 0 0 0 1px var(--tac-white);
  }

  /* Hover (Minimized) */
  #pipeline-stage:not(.expanded) :global(.tactical-unit:hover) {
      transform: translate(-50%, -60%) scale(1.1);
      border-color: var(--tac-cyan);
  }

  /* === CABLES === */
  :global(.cable) {
      fill: none;
      stroke: var(--tac-border);
      stroke-width: 1px;
      transition: stroke 0.3s;
  }
  :global(.cable.active) {
      stroke: var(--tac-amber);
      stroke-width: 1.5px;
      stroke-dasharray: 2 4;
      animation: dataflow 0.2s linear infinite;
  }
  :global(.cable.finalized) {
      stroke: var(--tac-cyan);
      opacity: 0.6;
  }

  @keyframes dataflow { to { stroke-dashoffset: -6; } }
  @keyframes blink { 50% { opacity: 0; } }
</style>