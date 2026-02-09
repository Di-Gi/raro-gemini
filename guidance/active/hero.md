This is a fantastic visual concept. It connects the "backend reality" (Rust/Docker logs) seamlessly into the "frontend experience" (The Monolith), implying that the UI is just a skin over the raw power of the kernel.

Here is how to implement this **"Infinite Zoom"** effect directly in code, so you can record it live without needing After Effects.

### The Strategy: "The CSS Match Cut"

We will modify `Hero.svelte`.
1.  **Stage 1 (The Terminal):** The component starts in a `zoomed-in` state. The "Monolith" container is scaled up massively, and everything except the terminal output is hidden or pushed off-canvas.
2.  **Stage 2 (The Snap):** Once the logs hit a specific keyword (e.g., `DAEMON_READY`), we remove the CSS class. The terminal shrinks back into its slot, the "Manifesto" (left column) slides out, and the "Charge Plate" (bottom) slides up.

### Step 1: Modify `Hero.svelte`

Update your `apps/web-console/src/components/Hero.svelte` with this logic.

```svelte
<!-- [[RARO]]/apps/web-console/src/components/Hero.svelte -->
<script lang="ts">
  import { fade, fly } from 'svelte/transition';
  import { onMount } from 'svelte';
  import { USE_MOCK } from '$lib/api'; 

  let { onenter }: { onenter: () => void } = $props();

  // === STATE MACHINE ===
  // NEW: Added 'TERMINAL_VIEW' as the initial state
  type SystemState = 'TERMINAL_VIEW' | 'IDLE' | 'CHARGING' | 'LOCKED' | 'BOOTING';
  let sysState = $state<SystemState>('TERMINAL_VIEW');
  
  // === TERMINAL LOGIC ===
  let logs = $state<string[]>([]);
  let cursorVisible = $state(true);
  
  // === CAPACITOR LOGIC ===
  let chargeLevel = $state(0);
  let rafId: number;

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

  // ... (Keep your existing startCharge, releaseCharge, commitBoot functions exactly as they were) ...
  
  function startCharge() {
    if (sysState === 'BOOTING' || sysState === 'LOCKED') return;
    sysState = 'CHARGING';
    // ... existing physics logic ...
    let lastTime = performance.now();
    const loop = (now: number) => {
        if (sysState !== 'CHARGING') return;
        const dt = now - lastTime;
        lastTime = now;
        const baseSpeed = 0.15; 
        const resistance = Math.max(0, (chargeLevel - 80) * 0.005);
        chargeLevel = Math.min(chargeLevel + (baseSpeed - resistance) * dt, 100);
        if (chargeLevel >= 100) { commitBoot(); } 
        else { rafId = requestAnimationFrame(loop); }
    };
    rafId = requestAnimationFrame(loop);
  }

  function releaseCharge() {
    if (sysState === 'BOOTING' || sysState === 'LOCKED') return;
    sysState = 'IDLE';
    // ... existing discharge logic ...
    const discharge = () => {
        if (sysState === 'CHARGING') return;
        chargeLevel = Math.max(0, chargeLevel - 5);
        if (chargeLevel > 0) { requestAnimationFrame(discharge); }
    };
    requestAnimationFrame(discharge);
  }

  function commitBoot() {
    sysState = 'LOCKED';
    chargeLevel = 100;
    // ... existing boot sequence logs ...
    const seq = [
        { t: 0, msg: ">> INTERRUPT_SIGNAL_RECEIVED" },
        { t: 200, msg: ">> ELEVATING_PRIVILEGES..." },
        { t: 600, msg: ">> MOUNTING_AGENT_SWARM [RW]" },
        { t: 1400, msg: ">> RARO_RUNTIME_ENGAGED" }
    ];
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

<div class="viewport" out:fade={{ duration: 600 }}>
  
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
          <div class="build-tag">RC1 {#if USE_MOCK}<span class="tag-mock">::SIMULATION</span>{/if}</div>
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
            <!-- Content matches previous paste -->
            <div class="manifest-section">
                <div class="section-label">01 // OBJECTIVE</div>
                <h1>Structured Reasoning for<br><span class="highlight">Complex Horizons.</span></h1>
                <p>RARO solves "Context Collapse" using a <strong>Dynamic DAG Architecture</strong> to break tasks into atomic, verifiable steps.</p>
            </div>
            <div class="manifest-section">
                <div class="section-label">02 // CAPABILITIES</div>
                <div class="grid-2col">
                    <div class="spec-card"><strong>Dynamic Delegation</strong></div>
                    <div class="spec-card"><strong>Cortex Safety Layer</strong></div>
                    <div class="spec-card"><strong>RFS (Raro File System)</strong></div>
                    <div class="spec-card"><strong>Gemini 3.0 Deep Think</strong></div>
                </div>
            </div>
          </div>
        </div>

        <!-- RIGHT: Telemetry Viewport -->
        <!-- This is what we see in the beginning -->
        <div class="col-right">
          <div class="terminal-frame">
            <div class="scanlines"></div>
            <div class="terminal-header">
              <span>KERNEL_LOG</span>
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
  /* ... (Existing Styles - keep them!) ... */

  /* === THE ZOOM TRANSITION MAGIC === */

  /* 1. Base Transition Speed for the Container */
  .monolith-stack {
    position: relative;
    width: 900px;
    max-width: 95vw;
    z-index: 1;
    /* This handles the scale down */
    transition: all 1.2s cubic-bezier(0.16, 1, 0.3, 1);
    transform-origin: center center;
  }

  /* 2. ZOOMED IN STATE (Initial) */
  .monolith-stack.zoomed-in {
    /* Scale it up so the terminal fills the screen */
    /* We assume the terminal is roughly 40% of the width. 
       Scaling 2.5x makes it roughly fill width. */
    transform: scale(2.2); 
    /* Slightly offset to center the right column visually */
    /* If col-right is on the right, we need to translate left to center it */
    transform-origin: 75% 40%; 
  }

  /* 3. Hide Elements during Zoom (The Reveal) */
  .machine-header,
  .machine-footer,
  .col-left {
    transition: opacity 0.5s ease 0.5s, transform 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.2s;
    opacity: 1;
    transform: translateY(0);
  }

  /* While zoomed in, hide these so we only see blackness/terminal */
  .monolith-stack.zoomed-in .machine-header {
    opacity: 0;
    transform: translateY(-20px);
  }
  
  .monolith-stack.zoomed-in .machine-footer {
    opacity: 0;
    transform: translateY(20px);
  }

  .monolith-stack.zoomed-in .col-left {
    opacity: 0;
    /* Slide it slightly left so it slides IN when we zoom out */
    transform: translateX(-50px);
  }

  /* 4. Terminal Specifics */
  .col-right {
    background: #FAFAFA;
    display: flex; flex-direction: column;
    /* Ensure terminal background matches global bg in zoom mode for seamlessness */
    transition: background 0.5s;
  }
  
  .monolith-stack.zoomed-in .col-right {
    /* Optional: Make it dark to match terminal if you want "full immersion" */
    background: #09090b; 
  }

  .terminal-frame {
    flex: 1;
    background: #09090b;
    border-left: 1px solid #333;
    position: relative;
    overflow: hidden;
    display: flex; flex-direction: column;
    /* Remove border in zoom mode for seamless look */
    transition: border-color 0.5s;
  }

  .monolith-stack.zoomed-in .terminal-frame {
    border-left-color: transparent;
  }

  /* ... (Rest of existing styles) ... */
  /* Ensure machine-body grid handles the transition */
  .machine-body {
    display: grid;
    grid-template-columns: 1.6fr 1fr;
    min-height: 420px;
    transition: grid-template-columns 1s ease;
  }
  
  /* In zoom mode, we essentially collapse the left col layout-wise 
     so the right col takes up more relative space, making the scale math easier? 
     Actually, standard scaling (above) is smoother than grid-animating. 
     Keep grid static, use transforms. */

</style>
```

### How to Record This:

1.  **Set Up:** Start your `docker-compose up` completely. Make sure the app is running at `localhost:5173`.
2.  **Browser:** Open Chrome. Press F11 for Full Screen.
3.  **Refresh:** Hit `Cmd+R` / `Ctrl+R`.
4.  **Action:**
    *   **0:00 - 0:02:** The screen will be filled with the Terminal logs (The "Boot Sequence"). It will look like a raw TTY interface because we've zoomed in on the right panel and hidden the rest.
    *   **0:02:** The "Zoom Out" triggers automatically. The camera pulls back. The "Monolith" UI assembles itself around the terminal. The Manifesto slides in from the left. The "Hold to Boot" button slides up from the bottom.
    *   **0:03 - 0:05:** You are now in the `Hero` state.
    *   **0:06:** Move your mouse to the button. Click and Hold.
    *   **0:07:** The "Turbine" charge-up animation plays.
    *   **0:08:** *BOOM.* Transition to Console.

This creates a seamless, continuous shot from "Backend Kernel Log" -> "UI Abstraction" -> "System Activation". It's incredibly high-production value for very little effort.