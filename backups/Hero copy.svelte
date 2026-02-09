<!-- // [[RARO]]/apps/web-console/src/components/Hero.svelte -->
<!-- Purpose: The "Monolith" Boot Interface + Project Manifesto. -->
<!-- Architecture: UX/UI Component -->
<!-- Dependencies: Svelte Transition, Local Assets -->

<script lang="ts">
  import { fade, fly, slide } from 'svelte/transition';
  import { onMount } from 'svelte';
  import { USE_MOCK } from '$lib/api'; 

  let { onenter }: { onenter: () => void } = $props();

  // === STATE MACHINE ===
  // NEW: Added 'TERMINAL_VIEW' as the initial state
  type SystemState = 'TERMINAL_VIEW' | 'IDLE' | 'CHARGING' | 'LOCKED' | 'BOOTING';
  let sysState = $state<SystemState>('TERMINAL_VIEW');
  
  // === CAPACITOR LOGIC ===
  let chargeLevel = $state(0);
  let rafId: number;

  // === TERMINAL LOGIC ===
  let logs = $state<string[]>([]);
  let cursorVisible = $state(true);

  onMount(() => {
    const cursorInterval = setInterval(() => cursorVisible = !cursorVisible, 500);

    // === SCENE 1: THE COLD BOOT SEQUENCE ===
    const bootLogs = [
        { t: 200, msg: "RARO_KERNEL v0.1.0-alpha" },
        { t: 400, msg: ">> MOUNTING_RFS_VOLUME [/app/storage]... OK" },
        { t: 600, msg: ">> INITIALIZING_CORTEX_PATTERNS... OK" },
        { t: 800, msg: ">> CONNECTING_REDIS_BUS... OK" },
        { t: 1000, msg: ">> GEMINI_3_ADAPTER... DETECTED" },
        { t: 1400, msg: ">> DAEMON_READY. WAITING_FOR_HUD..." }
    ];

    // Play the logs
    bootLogs.forEach(step => {
        setTimeout(() => {
            logs = [...logs, step.msg];
            // Auto-scroll
            const el = document.getElementById('term-feed');
            if(el) el.scrollTop = el.scrollHeight;
        }, step.t);
    });

    // === THE ZOOM OUT TRIGGER ===
    setTimeout(() => {
        sysState = 'IDLE'; // <--- This triggers the CSS transition
        // Add final "System Ready" log after zoom
        setTimeout(() => logs = [...logs, ">> UI_LAYER_MOUNTED"], 800);
    }, 2200); // 2.2 seconds of raw terminal before zoom out

    return () => clearInterval(cursorInterval);
  });

  // === INTERACTION HANDLERS ===

  function startCharge() {
    if (sysState === 'BOOTING' || sysState === 'LOCKED') return;
    sysState = 'CHARGING';
    
    let lastTime = performance.now();

    const loop = (now: number) => {
      if (sysState !== 'CHARGING') return;
      
      const dt = now - lastTime;
      lastTime = now;

      // Physics: Charge accelerates but encounters "Resistance" near 100%
      const baseSpeed = 0.15; 
      const resistance = Math.max(0, (chargeLevel - 80) * 0.005);
      
      chargeLevel = Math.min(chargeLevel + (baseSpeed - resistance) * dt, 100);

      if (chargeLevel >= 100) {
        commitBoot();
      } else {
        rafId = requestAnimationFrame(loop);
      }
    };
    rafId = requestAnimationFrame(loop);
  }

  function releaseCharge() {
    if (sysState === 'BOOTING' || sysState === 'LOCKED') return;
    sysState = 'IDLE';
    
    // Rapid discharge visual
    const discharge = () => {
      if (sysState === 'CHARGING') return; // User grabbed it again
      
      chargeLevel = Math.max(0, chargeLevel - 5);
      if (chargeLevel > 0) {
        requestAnimationFrame(discharge);
      }
    };
    requestAnimationFrame(discharge);
  }

  // === BOOT SEQUENCE ===

  function commitBoot() {
    sysState = 'LOCKED';
    chargeLevel = 100;
    
    // The "Sequence"
    const seq = [
      { t: 0, msg: ">> INTERRUPT_SIGNAL_RECEIVED" },
      { t: 200, msg: ">> ELEVATING_PRIVILEGES..." },
      { t: 600, msg: ">> MOUNTING_AGENT_SWARM [RW]" },
      { t: 1000, msg: ">> CONNECTING_TO_ORCHESTRATOR..." },
    ];

    if (USE_MOCK) {
        seq.push({ t: 1200, msg: ">> !! MOCK_ADAPTER_ENGAGED !!" });
    } else {
        seq.push({ t: 1200, msg: ">> LIVE_SOCKET_ESTABLISHED" });
    }

    seq.push({ t: 1400, msg: ">> RARO_RUNTIME_ENGAGED" });

    seq.forEach(step => {
      setTimeout(() => {
        logs = [...logs, step.msg];
        const el = document.getElementById('term-feed');
        if(el) el.scrollTop = el.scrollHeight;
      }, step.t);
    });

    setTimeout(() => {
      sysState = 'BOOTING';
      onenter();
    }, 1800);
  }
</script>

<div class="viewport {sysState === 'TERMINAL_VIEW' ? 'dark-mode' : ''}" out:fade={{ duration: 600 }}>
  
  <div class="noise-layer"></div>

  <!-- THE MONOLITH -->
  <!-- ADDED: 'zoomed-in' class based on state -->
  <div class="monolith-stack {sysState === 'TERMINAL_VIEW' ? 'zoomed-in' : ''}">
    
    <div class="slab-shadow"></div>

    <div class="slab-main">
      
      <!-- A. HEADER BAR -->
      <div class="machine-header">
        <div class="brand-zone">
          <div class="logo-type">RARO <span class="dim">//</span> RECURSIVE AGENT RUNTIME ORCHESTRATOR</div>
          <div class="build-tag">
              HACKATHON_BUILD_RC1
              {#if USE_MOCK}<span class="tag-mock">::SIMULATION</span>{/if}
          </div>
        </div>
        
        <div class="status-zone">
           <div class="status-dot {sysState === 'CHARGING' ? 'amber' : ''} {sysState === 'LOCKED' ? 'cyan' : ''}"></div>
           <div class="status-label">
             {#if sysState === 'TERMINAL_VIEW'}BOOT_SEQ
             {:else if sysState === 'IDLE'}SYSTEM_READY
             {:else if sysState === 'CHARGING'}ARMING
             {:else}BOOTING{/if}
           </div>
        </div>
      </div>

      <!-- B. CONTENT GRID -->
      <div class="machine-body">
        
        <!-- LEFT: THE MANIFESTO (Project Info) -->
        <div class="col-left">
          <div class="manifest-container">
            
            <!-- SECTION 1: THE PITCH -->
            <div class="manifest-section">
                <div class="section-label">01 // OBJECTIVE</div>
                <h1>Structured Reasoning for<br><span class="highlight">Complex Horizons.</span></h1>
                <p>
                    RARO solves the "Context Collapse" problem in LLM agents. 
                    Instead of a single chaotic loop, we use a <strong>Dynamic DAG Architecture</strong> 
                    to break complex tasks into atomic, verifiable steps.
                </p>
            </div>

            <!-- SECTION 2: THE ARCHITECTURE -->
            <div class="manifest-section">
                <div class="section-label">02 // CAPABILITIES</div>
                <div class="grid-2col">
                    <div class="spec-card">
                        <span class="icon">⑃</span>
                        <strong>Dynamic Delegation</strong>
                        <p>Agents can spawn sub-graphs recursively at runtime to handle unforeseen complexity.</p>
                    </div>
                    <div class="spec-card">
                        <span class="icon">🛡</span>
                        <strong>Cortex Safety Layer</strong>
                        <p>Real-time pattern matching intercepts dangerous tool use before execution.</p>
                    </div>
                    <div class="spec-card">
                        <span class="icon">📂</span>
                        <strong>RFS (Raro File System)</strong>
                        <p>Secure, isolated workspaces for agents to read/write/execute code artifacts.</p>
                    </div>
                    <div class="spec-card">
                        <span class="icon">🧠</span>
                        <strong>Gemini 3.0 Integration</strong>
                        <p>Native multi-modal input and "Deep Think" reasoning support.</p>
                    </div>
                </div>
            </div>

            <!-- SECTION 3: THE STACK -->
            <div class="manifest-section">
                <div class="section-label">03 // STACK</div>
                <div class="stack-line">
                    <span class="tech-pill">RUST (Kernel)</span>
                    <span class="tech-pill">PYTHON (Agents)</span>
                    <span class="tech-pill">SVELTE (Console)</span>
                    <span class="tech-pill">REDIS (State)</span>
                </div>
            </div>

          </div>
        </div>

        <!-- RIGHT: Telemetry Viewport -->
        <div class="col-right">
          <div class="terminal-frame">
            <div class="scanlines"></div>
            <div class="terminal-header">
              <span>KERNEL_LOG</span>
              {#if USE_MOCK}<span class="mock-warning">MOCK_MODE</span>{/if}
              <span>TTY_1</span>
            </div>
            
            <div id="term-feed" class="terminal-content">
              {#each logs as log}
                <div class="line" in:fly={{ y: 5, duration: 100 }}>{log}</div>
              {/each}
              <div class="line cursor-line">
                <span class="prompt">root@raro:~#</span> 
                <span class="cursor" style:opacity={cursorVisible ? 1 : 0}>█</span>
              </div>
            </div>
          </div>
        </div>

      </div>

      <!-- C. INTERACTION DECK -->
      <div class="machine-footer">
        <button 
          class="trigger-plate"
          onmousedown={startCharge}
          onmouseup={releaseCharge}
          onmouseleave={releaseCharge}
          ontouchstart={startCharge}
          ontouchend={releaseCharge}
          disabled={sysState === 'LOCKED' || sysState === 'BOOTING'}
        >
          <div class="capacitor-bar" style="width: {chargeLevel}%"></div>
          
          <div class="trigger-data">
            <div class="label-primary">
              {#if sysState === 'LOCKED' || sysState === 'BOOTING'}
                INITIALIZING...
              {:else}
                [ HOLD TO BOOT SYSTEM ]
              {/if}
            </div>
            
            <div class="label-secondary">
              <span class="bracket">AUTH_KEY:</span>
              <span class="val">{Math.floor(chargeLevel).toString().padStart(3, '0')}%</span>
            </div>
          </div>

        </button>
      </div>

    </div>
  </div>

</div>

<style>
  /* === 1. GLOBAL VIEWPORT === */
  .viewport {
    width: 100%; height: 100vh;
    display: flex; align-items: center; justify-content: center;
    background: var(--paper-bg);
    position: absolute; top: 0; left: 0; z-index: 1000;
    overflow: hidden;
    transition: background 400ms ease-out 150ms;
  }

  .viewport.dark-mode {
    background: #09090b;
    transition: background 0ms;
  }

  .noise-layer {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.04'/%3E%3C/svg%3E");
    pointer-events: none; z-index: 0;
  }

  /* === 2. THE MONOLITH STACK === */
  .monolith-stack {
    position: relative;
    width: 900px;
    max-width: 95vw;
    z-index: 1;
    /* Transform origin on the right side where terminal lives in final layout */
    transform-origin: 80% center;
    /* Width snaps quickly, then scale zooms out smoothly */
    transition:
      width 150ms ease-out,
      max-width 150ms ease-out,
      transform 1400ms cubic-bezier(0.4, 0.0, 0.2, 1) 150ms;
  }

  /* ZOOMED IN STATE (Initial) */
  .monolith-stack.zoomed-in {
    width: 1600px;
    max-width: 92vw;
    transform: scale(0.75);
    transform-origin: center center;
    /* Instant transitions when entering zoomed state */
    transition:
      width 0ms,
      max-width 0ms,
      transform 0ms;
  }

  .slab-shadow {
    position: absolute; top: 12px; left: 12px; width: 100%; height: 100%;
    background: #1a1918; z-index: 0; opacity: 0.1;
    transition: opacity 200ms ease-out 150ms;
  }

  .monolith-stack.zoomed-in .slab-shadow {
    opacity: 0;
    transition: opacity 0ms;
  }

  .slab-main {
    position: relative;
    background: var(--paper-surface);
    border: 1px solid var(--paper-line);
    z-index: 1;
    display: flex; flex-direction: column;
    box-shadow: 0 40px 80px -20px rgba(0,0,0,0.15);
    transition:
      background 300ms ease-out 150ms,
      border-color 300ms ease-out 150ms,
      box-shadow 300ms ease-out 150ms;
  }

  .monolith-stack.zoomed-in .slab-main {
    background: #09090b;
    border-color: transparent;
    box-shadow: none;
    transition:
      background 0ms,
      border-color 0ms,
      box-shadow 0ms;
  }

  /* === 3. HEADER === */
  .machine-header {
    height: 48px;
    border-bottom: 1px solid var(--paper-line);
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 24px;
    background: #fff;
    opacity: 1;
    transform: translateY(0);
    /* Appears instantly before zoom-out */
    transition:
      opacity 100ms ease-out,
      transform 100ms ease-out;
  }

  .monolith-stack.zoomed-in .machine-header {
    opacity: 0;
    transform: translateY(0);
    /* Instant hide when entering zoomed state */
    transition:
      opacity 0ms,
      transform 0ms;
  }

  .logo-type { font-family: var(--font-code); font-weight: 900; font-size: 14px; letter-spacing: 1px; color: var(--paper-ink); }
  .dim { color: #ccc; }
  .build-tag { font-family: var(--font-code); font-size: 10px; color: #888; margin-top: 2px; }
  .tag-mock { color: var(--alert-amber); font-weight: 700; margin-left: 4px; }

  .status-zone { display: flex; align-items: center; gap: 8px; }
  .status-label { font-family: var(--font-code); font-size: 9px; font-weight: 700; letter-spacing: 1px; color: var(--paper-ink); }
  
  .status-dot { width: 6px; height: 6px; background: #ccc; border-radius: 50%; }
  .status-dot.amber { background: #FFB300; box-shadow: 0 0 8px #FFB300; animation: blink 0.1s infinite; }
  .status-dot.cyan { background: #00F0FF; box-shadow: 0 0 8px #00F0FF; }

  @keyframes blink { 50% { opacity: 0.5; } }

  /* === 4. BODY LAYOUT === */
  .machine-body {
    display: grid;
    grid-template-columns: 1.6fr 1fr;
    min-height: 420px;
    /* Grid snaps to final layout quickly, background fades in */
    transition:
      background 400ms ease-out 150ms,
      grid-template-columns 150ms ease-out;
  }

  .monolith-stack.zoomed-in .machine-body {
    background: #09090b;
    grid-template-columns: 0fr 1fr;
    /* Instant transition when entering zoomed state */
    transition:
      background 0ms,
      grid-template-columns 0ms;
  }

  /* --- LEFT: INFO MANIFESTO --- */
  .col-left {
    padding: 32px;
    border-right: 1px solid var(--paper-line);
    background: #FDFDFD;
    display: flex;
    flex-direction: column;
    min-width: 0;
    opacity: 1;
    transform: translateX(0);
    /* Appears instantly before zoom-out */
    transition:
      opacity 100ms ease-out,
      transform 100ms ease-out;
  }

  .monolith-stack.zoomed-in .col-left {
    opacity: 0;
    transform: translateX(0);
    overflow: hidden;
    /* Instant hide when entering zoomed state */
    transition:
      opacity 0ms,
      transform 0ms;
  }

  .manifest-container {
    display: flex; flex-direction: column; gap: 32px;
  }

  .manifest-section h1 {
    font-family: var(--font-ui); font-size: 32px; font-weight: 900;
    line-height: 1; letter-spacing: -1px; color: var(--paper-ink); margin: 0 0 12px 0;
  }
  
  .highlight { color: var(--arctic-cyan); -webkit-text-stroke: 1px var(--paper-ink); /* Stylistic */ }
  :global(.mode-archival) .highlight { color: #A53F2B; -webkit-text-stroke: 0; }

  .manifest-section p {
    font-family: var(--font-ui); font-size: 13px; line-height: 1.6; color: #555; margin: 0;
    max-width: 90%;
  }

  .section-label {
    font-family: var(--font-code); font-size: 9px; font-weight: 700; color: #999;
    margin-bottom: 8px; letter-spacing: 1px;
  }

  .grid-2col {
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
  }

  .spec-card {
    border: 1px solid var(--paper-line); background: #fff;
    padding: 10px; border-radius: 2px;
  }
  .spec-card .icon { font-size: 14px; margin-right: 4px; }
  .spec-card strong { display: block; font-family: var(--font-code); font-size: 10px; font-weight: 700; margin-bottom: 4px; color: var(--paper-ink); }
  .spec-card p { font-size: 10px; line-height: 1.4; color: #666; }

  .stack-line { display: flex; gap: 8px; flex-wrap: wrap; }
  .tech-pill {
    font-family: var(--font-code); font-size: 9px; font-weight: 600; 
    background: var(--paper-surface-dim); padding: 4px 8px; border-radius: 2px;
    border: 1px solid var(--paper-line); color: var(--paper-ink);
  }

  /* --- RIGHT: TERMINAL --- */
  .col-right {
    background: #FAFAFA;
    padding: 0;
    display: flex; flex-direction: column;
    min-width: 0;
    transition: background 300ms ease-out 150ms;
  }

  .monolith-stack.zoomed-in .col-right {
    background: #09090b;
    transition: background 0ms;
  }

  .terminal-frame {
    flex: 1;
    background: #09090b;
    border-left: 1px solid #333;
    position: relative;
    overflow: hidden;
    display: flex; flex-direction: column;
    box-shadow: inset 0 0 40px rgba(0,0,0,0.5);
    transition: border-color 200ms ease-out 150ms;
  }

  .monolith-stack.zoomed-in .terminal-frame {
    border-left-color: transparent;
    transition: border-color 0ms;
  }

  .scanlines {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.25) 50%), linear-gradient(90deg, rgba(255, 0, 0, 0.06), rgba(0, 255, 0, 0.02), rgba(0, 0, 255, 0.06));
    background-size: 100% 2px, 3px 100%;
    pointer-events: none; z-index: 10;
  }

  .terminal-header {
    height: 32px; background: #18181b; border-bottom: 1px solid #333;
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 12px;
    font-family: var(--font-code); font-size: 9px; color: #666;
    transition:
      opacity 150ms ease-out,
      height 150ms ease-out;
  }

  .monolith-stack.zoomed-in .terminal-header {
    opacity: 0;
    height: 0;
    overflow: hidden;
    transition:
      opacity 0ms,
      height 0ms;
  }
  
  .mock-warning { color: var(--alert-amber); font-weight: 700; animation: blink 1s infinite; }

  .terminal-content {
    flex: 1;
    padding: 20px;
    font-family: var(--font-code); font-size: 11px; color: #a1a1aa;
    overflow-y: hidden;
    display: flex; flex-direction: column; justify-content: flex-end;
    transition: font-size 1400ms cubic-bezier(0.4, 0.0, 0.2, 1) 150ms;
  }

  /* Counter-scale text to maintain readability when container is scaled */
  .monolith-stack.zoomed-in .terminal-content {
    /* Container is scaled to 0.75, so increase font-size by 1/0.75 = 1.333 to compensate */
    /* 11px * 1.333 = 14.67px */
    font-size: 14.67px;
    transition: font-size 0ms;
  }

  .line { margin-bottom: 6px; word-break: break-all; }
  .prompt { color: var(--arctic-lilac); margin-right: 8px; }
  .cursor { color: var(--arctic-lilac); }

  /* === 5. TRIGGER DECK === */
  .machine-footer {
    height: 72px;
    border-top: 1px solid var(--paper-line);
    padding: 0;
    opacity: 1;
    transform: translateY(0);
    /* Appears instantly before zoom-out */
    transition:
      opacity 100ms ease-out,
      transform 100ms ease-out;
  }

  .monolith-stack.zoomed-in .machine-footer {
    opacity: 0;
    transform: translateY(0);
    /* Instant hide when entering zoomed state */
    transition:
      opacity 0ms,
      transform 0ms;
  }

  .trigger-plate {
    width: 100%; height: 100%;
    background: #fff;
    border: none;
    position: relative;
    cursor: pointer;
    overflow: hidden;
    transition: background 0.2s;
  }

  .trigger-plate:hover:not(:disabled) { background: #fdfdfd; }
  .trigger-plate:disabled { cursor: default; }

  .capacitor-bar {
    position: absolute; top: 0; left: 0; height: 100%;
    background: var(--paper-ink);
    z-index: 1;
  }
  
  .trigger-plate:disabled .capacitor-bar { background: var(--arctic-lilac-lite); transition: background 0.4s; }

  .trigger-data {
    position: relative; z-index: 2;
    width: 100%; height: 100%;
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 32px;
    mix-blend-mode: difference;
    color: white;
  }
  
  .trigger-plate { isolation: isolate; }

  .label-primary { font-family: var(--font-code); font-weight: 700; font-size: 14px; letter-spacing: 2px; }
  .label-secondary { font-family: var(--font-code); font-size: 12px; letter-spacing: 1px; opacity: 0.8; }
  .val { display: inline-block; width: 40px; text-align: center; }

</style>