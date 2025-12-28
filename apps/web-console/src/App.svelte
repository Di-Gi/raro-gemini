<script lang="ts">
  import { onMount } from 'svelte'
  import OutputPane from '$components/OutputPane.svelte'
  import PipelineStage from '$components/PipelineStage.svelte'
  import ControlDeck from '$components/ControlDeck.svelte'
  import { runtimeStore, addLog } from '$lib/stores'

  let expanded = false

  onMount(() => {
    addLog('KERNEL', 'RARO Runtime Environment v0.1.0. Status: IDLE. Pipeline ready for configuration.', 'SYSTEM_BOOT')
  })

  function togglePipeline() {
    expanded = !expanded
  }
</script>

<div id="chassis" class:expanded>
  <OutputPane />
  <PipelineStage {expanded} on:toggle={togglePipeline} />
  <ControlDeck {expanded} />
</div>

<style global>
  :root {
    /* === PALETTE: PHYSICAL (The Desk) === */
    --paper-bg: #EAE6DF;
    --paper-surface: #F2EFEA;
    --paper-ink: #1A1918;
    --paper-line: #A8A095;
    --paper-accent: #D4CDC5;

    /* === PALETTE: DIGITAL (The Machine) === */
    --digi-void: #090C10;
    --digi-panel: #161B22;
    --digi-line: #30363D;
    --arctic-cyan: #00F0FF;
    --arctic-dim: rgba(0, 240, 255, 0.08);
    --arctic-glow: rgba(0, 240, 255, 0.4);
    --alert-amber: #FFB300;

    /* === TYPE === */
    --font-ui: 'Inter', -apple-system, system-ui, sans-serif;
    --font-code: 'JetBrains Mono', 'Fira Code', monospace;

    /* === MOTION === */
    --ease-snap: cubic-bezier(0.16, 1, 0.3, 1);
  }

  * {
    box-sizing: border-box;
  }

  :global(body) {
    margin: 0;
    background: var(--paper-bg);
    color: var(--paper-ink);
    font-family: var(--font-ui);
    height: 100vh;
    display: flex;
    justify-content: center;
    overflow: hidden;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.03'/%3E%3C/svg%3E");
  }

  #chassis {
    width: 100%;
    max-width: 800px;
    height: 100vh;
    border-left: 1px solid var(--paper-line);
    border-right: 1px solid var(--paper-line);
    background: var(--paper-bg);
    display: flex;
    flex-direction: column;
    position: relative;
    box-shadow: 0 0 100px rgba(0,0,0,0.1);
  }
</style>
