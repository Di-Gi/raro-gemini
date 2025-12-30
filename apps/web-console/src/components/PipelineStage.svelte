<script lang="ts">
  import { agentNodes, pipelineEdges, selectedNode, selectNode, deselectNode } from '$lib/stores'

  // 1. Define Props and Callbacks (replacing export let and dispatch)
  let { expanded, ontoggle }: { expanded: boolean, ontoggle: () => void } = $props();

  // 2. Define reactive state for element bindings
  let svgElement = $state<SVGSVGElement | undefined>();
  let nodesLayer = $state<HTMLDivElement | undefined>();

  function renderGraph() {
    if (!svgElement || !nodesLayer) return

    const svg = svgElement
    const w = svg.clientWidth
    const h = svg.clientHeight

    svg.innerHTML = ''

    $pipelineEdges.forEach((link) => {
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
      path.setAttribute('class', `cable ${link.active ? 'active' : ''} ${link.pulseAnimation ? 'pulse' : ''}`)
      path.setAttribute('id', `link-${link.from}-${link.to}`)

      // Add signature hash as a data attribute for hover tooltips
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

        el.style.left = `${getX(node)}px`
        el.style.top = `${getY(node)}px`

        el.onclick = (e) => {
          if (!expanded) return
          e.stopPropagation()
          selectNode(node.id)
        }

        nodesLayer!.appendChild(el)
      })
    }
  }

  // 4. Use $effect to trigger the render whenever dependencies change
  // (This tracks svgElement, nodesLayer, expanded, $agentNodes, and $selectedNode)
  $effect(() => {
    renderGraph();
  })

  function handleClick() {
    if (!expanded) {
      ontoggle()
    }
  }
</script>

<div
  id="pipeline-stage"
  class:expanded
  onclick={handleClick}
  onkeydown={(e) => e.key === 'Enter' && handleClick()}
  role="button"
  tabindex="0"
>
  <div id="hud-banner">
    <div class="hud-title">
      <div class="hud-status-dot"></div>
      ARCHITECT VIEW // EDIT MODE
    </div>
    <button
      class="btn-minimize"
      onclick={(e) => {
        e.stopPropagation()
        ontoggle()
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
    transition: height 0.5s var(--ease-snap);
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
    color: var(--arctic-cyan);
    font-family: var(--font-code);
    font-size: 10px;
    letter-spacing: 1px;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .hud-status-dot {
    width: 6px;
    height: 6px;
    background: var(--arctic-cyan);
    border-radius: 50%;
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

  :global(.cable.active) {
    stroke: var(--arctic-cyan);
    stroke-dasharray: 6;
    animation: flow 0.8s linear infinite;
  }

  :global(.cable.active.pulse) {
    stroke-width: 2.5px;
    filter: drop-shadow(0 0 4px var(--arctic-cyan));
  }

  @keyframes flow {
    to {
      stroke-dashoffset: -12;
    }
  }

  @keyframes pulse {
    0%, 100% {
      opacity: 1;
    }
    50% {
      opacity: 0.6;
    }
  }
</style>
