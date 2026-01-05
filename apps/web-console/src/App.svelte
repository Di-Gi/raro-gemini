<!-- // [[RARO]]/apps/web-console/src/App.svelte
// Purpose: Root Application Layout. Handles Global State (Theme/Hero) and Top-Level Layout.
// Architecture: Layout Orchestrator
// Dependencies: Stores, Components -->

<script lang="ts">
  import { fade } from 'svelte/transition'
  import OutputPane from '$components/OutputPane.svelte'
  import PipelineStage from '$components/PipelineStage.svelte'
  import ControlDeck from '$components/ControlDeck.svelte'
  import Hero from '$components/Hero.svelte'
  import SettingsRail from '$components/SettingsRail.svelte'
  import EnvironmentRail from '$components/EnvironmentRail.svelte'
  import { addLog, themeStore } from '$lib/stores'

  let expanded = $state(false)
  let appState = $state<'HERO' | 'CONSOLE'>('HERO')
  
  // DEBUG STATE: Toggle with Alt + S
  let slowMotion = $state(false); 

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

  // GLOBAL SHORTCUTS
  function handleGlobalKey(e: KeyboardEvent) {
    // Alt + S: Toggle Slow Motion for animation debugging
    if (e.altKey && e.code === 'KeyS') {
        slowMotion = !slowMotion;
        if (slowMotion) addLog('DEBUG', 'Time dilation enabled (Slow Motion).', 'SYS');
        else addLog('DEBUG', 'Time dilation disabled.', 'SYS');
    }
  }
</script>

<svelte:window onkeydown={handleGlobalKey} />

<!-- Apply .slow-motion class based on state -->
<main class="mode-{$themeStore.toLowerCase()} {slowMotion ? 'slow-motion' : ''}">
    
    <!-- Global Texture Overlay -->
    <div class="noise-overlay"></div>

    {#if appState === 'HERO'}
      <Hero onenter={enterConsole} />
    {:else}
      <!-- 
        WORKSPACE LAYOUT
        SettingsRail is absolute positioned (right), chassis is centered.
      -->
      <div class="workspace" in:fade={{ duration: 800, delay: 200 }}>
        

        <!-- LEFT: Environment -->
        <EnvironmentRail />

        <div 
          id="chassis" 
          class={expanded ? 'expanded' : ''}
        >
          <OutputPane />
          <PipelineStage {expanded} ontoggle={togglePipeline} />
          <ControlDeck {expanded} />
        </div>

        <SettingsRail />
        
      </div>
    {/if}

</main>

<style>
  /* 
    GLOBAL RESET & VARS
  */
  :global(:root) {
    /* === CONSTANTS === */
    --font-ui: 'Inter', -apple-system, system-ui, sans-serif;
    --font-code: 'JetBrains Mono', 'Fira Code', monospace;
    --ease-snap: cubic-bezier(0.16, 1, 0.3, 1);
    
    /* Digital Constants */
    --arctic-cyan: #00F0FF;
    --arctic-dim: rgba(0, 240, 255, 0.08);
    --arctic-glow: rgba(0, 240, 255, 0.4);
    --arctic-lilac: rgba(113, 113, 242, 0.7);
    --arctic-lilac-lite: rgba(55, 49, 242, 0.2);
    
    /* Semantic Signals */
    --alert-amber: #FFB300;
    --signal-success: #2ea043; /* Added: Standard Terminal Green */
  }

  /* === DEBUG: SLOW MOTION OVERRIDE === */
  /* This forces all transitions and animations to take 3 seconds */
  :global(.slow-motion) :global(*),
  :global(.slow-motion) :global(*::before),
  :global(.slow-motion) :global(*::after) {
      transition-duration: 3s !important;
      animation-duration: 3s !important;
  }
  
  /* Exclude things that look broken when slow (like typing cursors) */
  :global(.slow-motion) :global(.cursor),
  :global(.slow-motion) :global(.blink),
  :global(.slow-motion) :global(.led) {
      animation-duration: 0.5s !important;
  }

  /* === REALITY 1: ARCHIVAL (Day / Physical) === */
  :global(.mode-archival) {
    --paper-bg: #EAE6DF;
    --paper-surface: #F2EFEA;
    --paper-surface-dim: #E6E2DD;
    --paper-ink: #1A1918;
    --paper-line: #A8A095;
    --paper-accent: #D4CDC5;
    
    /* The Screen stays dark even in day mode */
    --digi-void: #090C10;
    --digi-panel: #161B22;
    --digi-line: #30363D;
    --digi-text: #e6edf3;
    --digi-text-dim: #8b949e;
  }

  /* === REALITY 2: PHOSPHOR (Night / Digital) === */
  :global(.mode-phosphor) {
    --paper-bg: #050505;
    --paper-surface: #090C10;
    --paper-surface-dim: #020202;
    --paper-ink: #E0E0E0;
    --paper-line: #7087a7;
    --paper-accent: #30363d;
    
    --digi-void: #050505;
    --digi-panel: #0d1117;
    --digi-line: #21262d;
    --digi-text: #e6edf3;
    --digi-text-dim: #8b949e;
  }


  :global(*) { box-sizing: border-box; }

  /* SCROLLBARS */
  :global(*) { scrollbar-width: thin; scrollbar-color: var(--paper-accent) transparent; }
  :global(::-webkit-scrollbar) { width: 6px; height: 6px; }
  :global(::-webkit-scrollbar-track) { background: transparent; }
  :global(::-webkit-scrollbar-thumb) { background-color: var(--paper-accent); border-radius: 3px; border: 1px solid transparent; background-clip: content-box; }
  :global(::-webkit-scrollbar-thumb:hover) { background-color: var(--paper-line); }
  
  :global(.mode-phosphor ::-webkit-scrollbar-thumb) { background-color: var(--paper-line); }
  :global(.mode-phosphor ::-webkit-scrollbar-thumb:hover) { background-color: var(--paper-ink); }

  :global(html), :global(body) {
    margin: 0; padding: 0; width: 100%; height: 100%;
    background: #000; overflow: hidden;
  }

  :global(body) {
    font-family: var(--font-ui);
    color: var(--paper-ink);
  }

  main {
    width: 100vw; height: 100vh; display: flex; justify-content: center;
    background: var(--paper-bg);
    transition: background 0.6s var(--ease-snap), color 0.6s var(--ease-snap);
    position: relative; overflow: hidden;
  }

  .noise-overlay {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.04'/%3E%3C/svg%3E");
    pointer-events: none; z-index: 9999; opacity: 0.35; mix-blend-mode: overlay;
  }

  .workspace {
    width: 100%; height: 100vh;
    display: flex; justify-content: center; align-items: flex-start;
    position: relative; /* Context for SettingsRail absolute positioning */
  }

  #chassis {
    width: 800px; min-width: 800px; flex-shrink: 0; height: 100vh;
    border-left: 1px solid var(--paper-line); border-right: 1px solid var(--paper-line);
    background: var(--paper-bg);
    display: flex; flex-direction: column;
    position: relative;
    box-shadow: 0 0 100px rgba(0,0,0,0.1);
    transition: border-color 0.6s, background 0.6s, box-shadow 0.6s;
    z-index: 10;
  }
</style>