<!-- // [[RARO]]/apps/web-console/src/components/Hero.svelte -->
<!-- Purpose: The "Monolith" Boot Interface. High-fidelity entry point. -->
<!-- Architecture: UX/UI Component -->
<!-- Dependencies: Svelte Transition, Local Assets -->

<script lang="ts">
  import { fade, fly } from 'svelte/transition';
  import { onMount } from 'svelte';
  import { USE_MOCK } from '$lib/api'; 

  let { onenter }: { onenter: () => void } = $props();

  // === STATE MACHINE ===
  type SystemState = 'IDLE' | 'CHARGING' | 'LOCKED' | 'BOOTING';
  let sysState = $state<SystemState>('IDLE');
  
  // === CAPACITOR LOGIC ===
  let chargeLevel = $state(0);
  let chargeVelocity = 0;
  let rafId: number;

  // === TERMINAL LOGIC ===
  let logs = $state<string[]>([]);
  let cursorVisible = $state(true);

  // Initial "Idle" Animation
  onMount(() => {
    const cursorInterval = setInterval(() => cursorVisible = !cursorVisible, 500);
    
    // Add some initial "noise" to the system
    setTimeout(() => logs.push("KERNEL_DAEMON_OK"), 200);
    
    // === MOCK DETECTION LOG ===
    if (USE_MOCK) {
      setTimeout(() => logs.push(">> VIRTUAL_ENVIRONMENT_DETECTED"), 400);
      setTimeout(() => logs.push(">> BYPASSING_HARDWARE_LINKS..."), 500);
    }

    setTimeout(() => logs.push("MEMORY_INTEGRITY_CHECK..."), 600);
    
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
      // This creates tactile "weight"
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

    // Add specific mock confirmation in boot sequence
    if (USE_MOCK) {
        seq.push({ t: 1200, msg: ">> !! MOCK_ADAPTER_ENGAGED !!" });
    } else {
        seq.push({ t: 1200, msg: ">> LIVE_SOCKET_ESTABLISHED" });
    }

    seq.push({ t: 1400, msg: ">> RARO_RUNTIME_ENGAGED" });

    seq.forEach(step => {
      setTimeout(() => {
        logs = [...logs, step.msg];
        // Keep terminal scrolled to bottom
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

<div class="viewport" out:fade={{ duration: 600 }}>
  
  <!-- OPTIONAL: NOISE TEXTURE OVERLAY -->
  <div class="noise-layer"></div>

  <!-- THE MONOLITH -->
  <div class="monolith-stack">
    
    <!-- 1. THE SHADOW SLAB (Depth Anchor) -->
    <div class="slab-shadow"></div>

    <!-- 2. THE MAIN UNIT -->
    <div class="slab-main">
      
      <!-- A. HEADER BAR -->
      <div class="machine-header">
        <div class="brand-zone">
          <div class="logo-type">RARO <span class="dim">//</span> KERNEL</div>
          <div class="build-tag">
              BUILD_2026.01.02
              {#if USE_MOCK}<span class="tag-mock">::SIM</span>{/if}
          </div>
        </div>
        
        <!-- Status Array -->
        <div class="status-zone">
           <div class="status-dot {sysState === 'CHARGING' ? 'amber' : ''} {sysState === 'LOCKED' ? 'cyan' : ''}"></div>
           <div class="status-label">
             {#if sysState === 'IDLE'}STANDBY{:else if sysState === 'CHARGING'}ARMING{:else}ACTIVE{/if}
           </div>
        </div>
      </div>

      <!-- B. CONTENT GRID -->
      <div class="machine-body">
        
        <!-- LEFT: Typography Engine -->
        <div class="col-left">
          <div class="hero-block">
             <h1>RECURSIVE</h1>
             <h1>AGENT</h1>
             <h1>REASONING<span class="dot">.</span></h1>
          </div>
          
          <div class="meta-block">
            <p>
              High-latency orchestration runtime for <span class="highlight">Gemini 3 Protocol</span>.
              Designed for deep-context synthesis and multi-hop reasoning chains.
            </p>
          </div>
        </div>

        <!-- RIGHT: Telemetry Viewport -->
        <div class="col-right">
          <div class="terminal-frame">
            <div class="scanlines"></div>
            <div class="terminal-header">
              <span>SYS_OUT</span>
              
              <!-- MOCK INDICATOR -->
              {#if USE_MOCK}
                <span class="mock-warning">MOCK_ENV</span>
              {/if}

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

      <!-- C. INTERACTION DECK (The Trigger) -->
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
          <!-- The Capacitor Fill -->
          <div class="capacitor-bar" style="width: {chargeLevel}%"></div>
          
          <!-- The Data Overlay -->
          <div class="trigger-data">
            <div class="label-primary">
              {#if sysState === 'LOCKED' || sysState === 'BOOTING'}
                SYSTEM_ENGAGED
              {:else}
                INITIALIZE_RUNTIME
              {/if}
            </div>
            
            <div class="label-secondary">
              <span class="bracket">[</span>
              <span class="val">{Math.floor(chargeLevel).toString().padStart(3, '0')}%</span>
              <span class="bracket">]</span>
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
  }

  .noise-layer {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.04'/%3E%3C/svg%3E");
    pointer-events: none;
    z-index: 0;
  }

  /* === 2. THE MONOLITH STACK === */
  .monolith-stack {
    position: relative;
    width: 700px;
    z-index: 1;
  }

  /* The physical depth shadow layer */
  .slab-shadow {
    position: absolute;
    top: 12px; left: 12px;
    width: 100%; height: 100%;
    background: #1a1918;
    z-index: 0;
    opacity: 0.1;
  }

  /* The Main Interface Unit */
  .slab-main {
    position: relative;
    background: var(--paper-surface);
    border: 1px solid var(--paper-line);
    z-index: 1;
    display: flex; flex-direction: column;
    box-shadow: 0 40px 80px -20px rgba(0,0,0,0.15); /* Soft ambient float */
  }

  /* === 3. HEADER === */
  .machine-header {
    height: 48px;
    border-bottom: 1px solid var(--paper-line);
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 24px;
    background: #fff;
  }

  .logo-type { font-family: var(--font-code); font-weight: 700; font-size: 12px; letter-spacing: 1px; color: var(--paper-ink); }
  .dim { color: #ccc; }
  .build-tag { font-family: var(--font-code); font-size: 9px; color: #888; margin-top: 2px; }
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
    grid-template-columns: 1.4fr 1fr;
    min-height: 320px;
  }

  /* Left Column: Typography */
  .col-left {
    padding: 40px 32px;
    display: flex; flex-direction: column; justify-content: space-between;
    border-right: 1px solid var(--paper-line);
  }

  .hero-block h1 {
    font-family: var(--font-ui);
    font-size: 56px;
    font-weight: 900;
    line-height: 0.82;
    letter-spacing: -3px;
    color: var(--paper-ink);
    margin: 0;
  }
  .dot { color: #A53F2B; }

  .meta-block {
    font-family: var(--font-code);
    font-size: 11px;
    line-height: 1.6;
    color: #666;
    max-width: 90%;
    margin-top: 40px;
  }
  .highlight { color: var(--paper-ink); font-weight: 700; border-bottom: 1px solid #ccc; }

  /* Right Column: Terminal */
  .col-right {
    background: #FAFAFA;
    padding: 24px;
    display: flex; flex-direction: column;
  }

  .terminal-frame {
    flex: 1;
    background: #111;
    border: 1px solid #333;
    position: relative;
    overflow: hidden;
    display: flex; flex-direction: column;
    box-shadow: inset 0 0 20px rgba(0,0,0,0.5);
  }

  .scanlines {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.25) 50%), linear-gradient(90deg, rgba(255, 0, 0, 0.06), rgba(0, 255, 0, 0.02), rgba(0, 0, 255, 0.06));
    background-size: 100% 2px, 3px 100%;
    pointer-events: none; z-index: 10;
  }

  .terminal-header {
    height: 24px; background: #222; border-bottom: 1px solid #333;
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 8px;
    font-family: var(--font-code); font-size: 8px; color: #666;
  }
  
  .mock-warning {
    color: var(--alert-amber);
    font-weight: 700;
    animation: blink 1s infinite;
  }

  .terminal-content {
    flex: 1;
    padding: 12px;
    font-family: var(--font-code); font-size: 10px; color: #8b949e;
    overflow-y: hidden; /* Programmatic scroll */
    display: flex; flex-direction: column; justify-content: flex-end;
  }

  .line { margin-bottom: 4px; word-break: break-all; }
  .prompt { color: var(--arctic-lilac); margin-right: 6px; }
  .cursor { color: var(--arctic-lilac); }

  /* === 5. TRIGGER DECK === */
  .machine-footer {
    height: 80px;
    border-top: 1px solid var(--paper-line);
    padding: 0; /* Full bleed button */
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

  /* The Capacitor Bar */
  .capacitor-bar {
    position: absolute; top: 0; left: 0; height: 100%;
    background: var(--paper-ink);
    z-index: 1;
    /* No transition for instant physical feel */
  }
  
  /* Success State */
  .trigger-plate:disabled .capacitor-bar { background: var(--arctic-lilac-lite); transition: background 0.4s; }

  /* Data Overlay */
  .trigger-data {
    position: relative; z-index: 2;
    width: 100%; height: 100%;
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 32px;
    mix-blend-mode: difference;
    color: white; /* Inverts to black on white bg, white on black fill */
  }
  
  /* Isolate stacking context for mix-blend-mode */
  .trigger-plate { isolation: isolate; }

  .label-primary { font-family: var(--font-code); font-weight: 700; font-size: 14px; letter-spacing: 2px; }
  
  .label-secondary { font-family: var(--font-code); font-size: 12px; letter-spacing: 1px; opacity: 0.8; margin-right: 30px; }
  .val { display: inline-block; width: 40px; text-align: center; }

</style>