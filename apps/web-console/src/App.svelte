<!-- apps/web-console/src/App.svelte -->
<script lang="ts">
  import { onMount } from 'svelte'
  import { fade } from 'svelte/transition'
  import OutputPane from '$components/OutputPane.svelte'
  import PipelineStage from '$components/PipelineStage.svelte'
  import ControlDeck from '$components/ControlDeck.svelte'
  import Hero from '$components/Hero.svelte'
  import { runtimeStore, addLog } from '$lib/stores'

  let expanded = $state(false)
  let appState = $state<'HERO' | 'CONSOLE'>('HERO')

  function togglePipeline() {
    expanded = !expanded
  }

  function enterConsole() {
    appState = 'CONSOLE'
    setTimeout(() => {
        addLog('KERNEL', 'RARO Runtime Environment v0.1.0.', 'SYSTEM_BOOT')
        setTimeout(() => addLog('SYSTEM', 'Connection established. Status: IDLE.', 'NET_OK'), 300)
    }, 500)
  }
</script>

{#if appState === 'HERO'}
  <Hero onenter={enterConsole} />
{:else}
  <div 
    id="chassis" 
    class={expanded ? 'expanded' : ''}
    in:fade={{ duration: 600, delay: 200 }}
  >
    <OutputPane />
    <PipelineStage {expanded} ontoggle={togglePipeline} />
    <ControlDeck {expanded} />
  </div>
{/if}

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

  /* === SCROLLBAR STYLING (Cross-Browser) === */
  
  /* 1. Firefox Standard */
  * {
    scrollbar-width: thin;
    scrollbar-color: var(--paper-accent) transparent;
  }

  /* 2. WebKit (Chrome, Edge, Safari) */
  ::-webkit-scrollbar {
    width: 6px;
    height: 6px;
  }

  ::-webkit-scrollbar-track {
    background: transparent;
  }

  ::-webkit-scrollbar-thumb {
    background-color: var(--paper-accent);
    border-radius: 3px;
    /* Creates padding around scrollbar to make it look thinner/floating */
    border: 1px solid transparent; 
    background-clip: content-box;
  }

  ::-webkit-scrollbar-thumb:hover {
    background-color: var(--paper-line);
  }

  /* Optional: Digital Mode Overrides for Dark Areas */
  .expanded * {
    scrollbar-color: var(--digi-line) transparent;
  }
  
  .expanded ::-webkit-scrollbar-thumb {
    background-color: var(--digi-line);
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
    width: 800px;         
    min-width: 800px;     
    flex-shrink: 0;        
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