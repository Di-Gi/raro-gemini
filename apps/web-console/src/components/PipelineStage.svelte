<!-- // [[RARO]]/apps/web-console/src/components/PipelineStage.svelte
// Purpose: Interactive DAG visualization with "Chip" aesthetic.
// Architecture: Visual Component (D3-lite)
// Dependencies: Stores -->

<script lang="ts">
  import { agentNodes, pipelineEdges, selectedNode, selectNode, deselectNode, runtimeStore, type PipelineEdge } from '$lib/stores'

  let { expanded, ontoggle }: { expanded: boolean, ontoggle?: () => void } = $props();

  // Reactive state for DOM element bindings
  let svgElement = $state<SVGSVGElement | undefined>();
  let nodesLayer = $state<HTMLDivElement | undefined>();
  let pipelineStageElement = $state<HTMLDivElement | undefined>();

  let isRunComplete = $derived($runtimeStore.status === 'COMPLETED' || $runtimeStore.status === 'FAILED');

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
    if (!svgElement || !nodesLayer) return

    const svg = svgElement
    const w = svg.clientWidth
    const h = svg.clientHeight

    svg.innerHTML = ''

    $pipelineEdges.forEach((link: PipelineEdge) => {
      const fromNode = $agentNodes.find((n) => n.id === link.from)
      const toNode = $agentNodes.find((n) => n.id === link.to)

      if (!fromNode || !toNode) return

      const getY = (n: any) => (expanded ? (n.y / 100) * h : h / 2)
      const getX = (n: any) => (n.x / 100) * w

      const x1 = getX(fromNode)
      const y1 = getY(fromNode)
      const x2 = getX(toNode)
      const y2 = getY(toNode)

      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
      const d = `M ${x1} ${y1} C ${x1 + 60} ${y1}, ${x2 - 60} ${y2}, ${x2} ${y2}`

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

    if (nodesLayer) {
      nodesLayer.innerHTML = ''
      $agentNodes.forEach((node) => {
        const el = document.createElement('div')
        // Styling handled by logic below, plus updated innerHTML structure
        el.className = `node ${$selectedNode === node.id ? 'selected' : ''} ${
          node.status === 'running' ? 'running' : ''
        } ${node.status === 'complete' ? 'complete' : ''}`
        
        // Detailed "Chip" Layout
        el.innerHTML = `
            <div class="node-indicator"></div>
            <div class="node-content">
                <div class="node-role">${node.role.toUpperCase()}</div>
                <div class="node-label">${node.label}</div>
            </div>
            <div class="node-decor"></div>
        `
        
        el.id = `node-${node.id}`

        const getY = (n: any) => (expanded ? (n.y / 100) * nodesLayer!.parentElement!.clientHeight : nodesLayer!.parentElement!.clientHeight / 2)
        const getX = (n: any) => (n.x / 100) * nodesLayer!.parentElement!.clientWidth

        const x = getX(node)
        const y = getY(node)

        el.style.left = `${x}px`
        el.style.top = `${y}px`

        el.onclick = (e) => {
          if (!expanded) return
          e.stopPropagation()
          selectNode(node.id)
        }

        nodesLayer!.appendChild(el)
      })
    }
  }

  $effect(() => {
    const _expanded = expanded;
    const _nodes = $agentNodes;
    const _edges = $pipelineEdges;
    const _selected = $selectedNode;
    const _status = $runtimeStore.status; 
    
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        renderGraph();
      });
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
      {:else}
         <div class="hud-status-dot"></div>
         ARCHITECT VIEW // EDIT MODE
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
    /* Detailed Grid Background using variables with transparency via color-mix */
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

  /* VISUAL INDICATOR FOR COMPLETED RUN */
  #pipeline-stage.expanded.run-complete {
    border-top: 1px solid var(--arctic-cyan);
    border-bottom: 1px solid var(--arctic-cyan);
  }

  #hud-banner {
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 32px;
    background: var(--digi-void); /* Matches container bg */
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
    color: var(--digi-text-dim); /* Replaced #8b949e */
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
    background: var(--digi-text-dim); /* Replaced #484f58 */
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

  .btn-minimize {
    background: transparent;
    border: none;
    color: var(--digi-text-dim); /* Replaced #484f58 */
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

  /* NODE CHIP STYLING */
  :global(.node) {
    position: absolute;
    background: var(--digi-panel);
    border: 1px solid var(--digi-line);
    color: var(--digi-text-dim); /* Replaced #8b949e */
    min-width: 140px;
    padding: 0; /* Reset */
    font-family: var(--font-code);
    transform: translate(-50%, -50%);
    transition: all 0.3s var(--ease-snap);
    user-select: none;
    pointer-events: none;
    display: flex;
    align-items: stretch;
    box-shadow: 0 4px 10px rgba(0,0,0,0.3);
    overflow: hidden;
  }
  
  /* Status Bar (Left) */
  :global(.node-indicator) {
    width: 4px;
    background: var(--digi-line);
    transition: background 0.3s;
  }
  
  :global(.node-content) {
    flex: 1;
    padding: 8px 12px;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  :global(.node-role) {
    font-size: 8px;
    text-transform: uppercase;
    color: var(--digi-text-dim); /* Replaced #484f58 */
    opacity: 0.7;
    letter-spacing: 0.5px;
  }

  :global(.node-label) {
    font-size: 11px;
    font-weight: 600;
    color: var(--digi-text); /* Replaced #c9d1d9 */
  }
  
  /* Decor (Right) */
  :global(.node-decor) {
    width: 12px;
    background: 
        repeating-linear-gradient(
            0deg,
            transparent,
            transparent 2px,
            var(--digi-line) 2px,
            var(--digi-line) 3px
        );
    opacity: 0.3;
    border-left: 1px solid var(--digi-line);
  }

  #pipeline-stage.expanded :global(.node) {
    pointer-events: auto;
    cursor: pointer;
  }

  /* HOVER */
  #pipeline-stage.expanded :global(.node:hover) {
    border-color: var(--arctic-cyan); /* Replaced #58a6ff */
    transform: translate(-50%, -52%);
    box-shadow: 0 8px 20px rgba(0,0,0,0.5);
  }
  #pipeline-stage.expanded :global(.node:hover .node-label) { color: white; }

  /* SELECTED */
  :global(.node.selected) {
    border-color: var(--arctic-cyan);
    background: var(--arctic-dim);
  }
  :global(.node.selected .node-indicator) { background: var(--arctic-cyan); }
  :global(.node.selected .node-label) { color: var(--arctic-cyan); }
  
  /* RUNNING */
  :global(.node.running) {
    border-color: var(--alert-amber);
    box-shadow: 0 0 20px rgba(255, 179, 0, 0.15);
  }
  :global(.node.running .node-indicator) { 
    background: var(--alert-amber);
    box-shadow: 0 0 8px var(--alert-amber);
  }
  :global(.node.running .node-label) { color: var(--alert-amber); }

  /* COMPLETE */
  :global(.node.complete) {
    border-color: var(--signal-success); /* Replaced #238636 */
  }
  :global(.node.complete .node-indicator) { background: var(--signal-success); }
  :global(.node.complete .node-label) { color: var(--signal-success); }

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
</style>