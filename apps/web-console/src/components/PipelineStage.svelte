<!-- apps/web-console/src/components/PipelineStage.svelte -->
<script lang="ts">
  import { agentNodes, pipelineEdges, selectedNode, selectNode, deselectNode, runtimeStore, type PipelineEdge } from '$lib/stores'

  let { expanded, ontoggle }: { expanded: boolean, ontoggle?: () => void } = $props();

  // Reactive state for DOM element bindings
  let svgElement = $state<SVGSVGElement | undefined>();
  let nodesLayer = $state<HTMLDivElement | undefined>();
  let pipelineStageElement = $state<HTMLDivElement | undefined>();

  // Track runtime status for visuals
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
      
      // Determine classes based on link state active (running) vs finalized (done)
      // Note: Logic in stores.ts ensures active and finalized are mutually exclusive
      let classes = `cable`;
      if (link.active) classes += ` active`;
      if (link.pulseAnimation) classes += ` pulse`;
      if (link.finalized) classes += ` finalized`;
      
      path.setAttribute('class', classes);
      path.setAttribute('id', `link-${link.from}-${link.to}`)

      if (link.signatureHash) {
        path.setAttribute('data-signature', link.signatureHash)
        path.setAttribute('title', `Signature: ${link.signatureHash.substring(0, 16)}...`)
      }

      svg.appendChild(path)
    })

    if (nodesLayer) {
      nodesLayer.innerHTML = ''
      $agentNodes.forEach((node) => {
        const el = document.createElement('div')
        el.className = `node ${$selectedNode === node.id ? 'selected' : ''} ${
          node.status === 'running' ? 'running' : ''
        }`
        el.textContent = node.label
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
    // We also track global status to re-render when it flips from Running -> Completed
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
      ▼ EXIT & MINIMIZE
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
    background-image: linear-gradient(var(--digi-line) 1px, transparent 1px),
      linear-gradient(90deg, var(--digi-line) 1px, transparent 1px);
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
    top: 0;
    left: 0;
    right: 0;
    height: 40px;
    background: rgba(22, 27, 34, 0.9);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--digi-line);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 16px;
    transform: translateY(-100%);
    transition: transform 0.3s ease;
    z-index: 200;
  }

  #pipeline-stage.expanded #hud-banner {
    transform: translateY(0);
  }

  .hud-title {
    color: #8b949e; /* Default gray */
    font-family: var(--font-code);
    font-size: 10px;
    letter-spacing: 1px;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  
  /* State-specific Text Colors */
  .run-complete .hud-title {
    color: var(--arctic-cyan);
  }

  .hud-status-dot {
    width: 6px;
    height: 6px;
    background: #8b949e;
    border-radius: 50%;
  }

  .hud-status-dot.active {
    background: var(--alert-amber);
    box-shadow: 0 0 8px var(--alert-amber);
    animation: blink 2s infinite;
  }

  .hud-status-dot.complete {
    background: var(--arctic-cyan);
    box-shadow: 0 0 8px var(--arctic-cyan);
  }

  .btn-minimize {
    background: transparent;
    border: 1px solid var(--digi-line);
    color: #8b949e;
    font-size: 9px;
    padding: 4px 10px;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.2s;
    font-family: var(--font-code);
  }

  .btn-minimize:hover {
    border-color: var(--arctic-cyan);
    color: white;
    background: var(--arctic-dim);
  }

  #graph-svg {
    width: 100%;
    height: 100%;
    position: absolute;
    top: 0;
    left: 0;
  }

  #nodes-layer {
    width: 100%;
    height: 100%;
    position: absolute;
    top: 0;
    left: 0;
  }

  :global(.node) {
    position: absolute;
    background: var(--digi-panel);
    border: 1px solid var(--digi-line);
    color: #8b949e;
    padding: 8px 12px;
    min-width: 120px;
    font-family: var(--font-code);
    font-size: 10px;
    text-align: center;
    transform: translate(-50%, -50%);
    transition: all 0.3s ease;
    user-select: none;
    pointer-events: none;
  }

  #pipeline-stage.expanded :global(.node) {
    pointer-events: auto;
    cursor: pointer;
  }

  #pipeline-stage.expanded :global(.node:hover) {
    border-color: var(--arctic-cyan);
    color: white;
  }

  :global(.node.selected) {
    border-color: var(--arctic-cyan);
    background: var(--arctic-dim);
    color: white;
    box-shadow: 0 0 20px var(--arctic-dim);
  }

  :global(.node.running) {
    border-color: var(--alert-amber);
    color: var(--alert-amber);
    box-shadow: 0 0 15px rgba(255, 179, 0, 0.2);
  }

  :global(.cable) {
    fill: none;
    stroke: var(--digi-line);
    stroke-width: 1.5px;
    transition: stroke 0.3s;
  }

  /* PROCESSING STATE: Dotted, Moving */
  :global(.cable.active) {
    stroke: var(--arctic-cyan);
    stroke-dasharray: 6;
    animation: flow 0.8s linear infinite;
    opacity: 0.8;
  }

  :global(.cable.active.pulse) {
    stroke-width: 2.5px;
    filter: drop-shadow(0 0 4px var(--arctic-cyan));
  }

  /* FINISHED STATE: Solid, Static, Bright */
  :global(.cable.finalized) {
    stroke: var(--arctic-cyan); /* Solid color */
    stroke-width: 2px;
    stroke-dasharray: none !important; /* Force solid line */
    animation: none !important; /* Force stop animation */
    opacity: 1; /* Fully opaque */
    filter: drop-shadow(0 0 2px rgba(0, 240, 255, 0.3));
  }

  @keyframes flow {
    to {
      stroke-dashoffset: -12;
    }
  }
  
  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
</style>