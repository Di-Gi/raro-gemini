**Calibration Protocol**.

### 1. Update Stores (`src/lib/stores.ts`)
We need a persistent flag to track if the user has completed the calibration.

```typescript
// [[RARO]]/apps/web-console/src/lib/stores.ts
// ... existing imports
import { writable, get } from 'svelte/store';

// ... existing code ...

// === CALIBRATION STATE ===
export const calibrationActive = writable<boolean>(false);

export function checkCalibrationStatus() {
    // Check local storage for the flag
    const hasCalibrated = localStorage.getItem('raro_calibrated');
    
    // Check if we are in a fresh session (no logs, no run ID) to prevent 
    // calibration triggering mid-workflow on a refresh if storage was cleared
    const isFreshState = get(runtimeStore).status === 'IDLE';

    if (!hasCalibrated && isFreshState) {
        calibrationActive.set(true);
    }
}

export function completeCalibration() {
    localStorage.setItem('raro_calibrated', 'true');
    calibrationActive.set(false);
}

// ... rest of existing code ...
```

### 2. Update ControlDeck (`src/components/ControlDeck.svelte`)
We need to add specific **IDs** to the navigation tabs and buttons so the Ghost Replay can target them reliably.

**Change 1: Add IDs to Nav Items**
Find the `deck-nav` section and add IDs `nav-tab-overview`, `nav-tab-pipeline`, `nav-tab-sim`, etc.

```svelte
<!-- Inside #deck-nav -->
<div
    id="nav-tab-sim" 
    class="nav-item {activePane === 'sim' ? 'active' : ''}"
    role="button"
    tabindex="0"
    onclick={() => handlePaneSelect('sim')}
    onkeydown={(e) => e.key === 'Enter' && handlePaneSelect('sim')}
>Simulation</div>
```

**Change 2: Add ID to Simulation Button**
Find the Simulation Controls section and add `id="btn-sim-auto"`.

```svelte
<!-- Inside #pane-sim -->
<div class="sim-controls">
  <!-- ... step button ... -->
  <button id="btn-sim-auto" class="input-std action-btn auto" onclick={runSimulation}>
    ▶▶ RUN AUTO
  </button>
  <!-- ... reset button ... -->
</div>
```

### 3. Create Ghost Reticle (`src/components/sub/GhostReticle.svelte`)
This component handles the smooth visual interpolation of the cursor.

```svelte
<!-- [[RARO]]/apps/web-console/src/components/sub/GhostReticle.svelte -->
<script lang="ts">
    import { spring } from 'svelte/motion';
    
    let { targetRect }: { targetRect: DOMRect | null } = $props();

    // Spring physics for "robotic" movement
    const coords = spring({ x: window.innerWidth / 2, y: window.innerHeight / 2 }, {
        stiffness: 0.04,
        damping: 0.35
    });

    $effect(() => {
        if (targetRect) {
            // Add slight randomness to make it look "AI-driven"
            const offsetX = (Math.random() - 0.5) * 10; 
            const offsetY = (Math.random() - 0.5) * 10;

            coords.set({
                x: targetRect.left + (targetRect.width / 2) + offsetX,
                y: targetRect.top + (targetRect.height / 2) + offsetY
            });
        }
    });
</script>

<div 
    class="reticle" 
    style="transform: translate({$coords.x}px, {$coords.y}px)"
>
    <div class="crosshair"></div>
    <div class="label">SYS_ADMIN</div>
    <div class="coords">X: {Math.round($coords.x)} Y: {Math.round($coords.y)}</div>
</div>

<style>
    .reticle {
        position: fixed; top: 0; left: 0;
        pointer-events: none; z-index: 10000;
        margin-left: -20px; margin-top: -20px;
        transition: opacity 0.2s;
    }

    .crosshair {
        width: 40px; height: 40px;
        border: 1px solid var(--arctic-cyan);
        border-radius: 50%;
        position: relative;
        background: rgba(0, 240, 255, 0.05);
        box-shadow: 0 0 15px var(--arctic-cyan);
        animation: pulse 2s infinite;
    }

    .crosshair::before, .crosshair::after {
        content: ''; position: absolute; background: var(--arctic-cyan);
    }
    .crosshair::before { top: 50%; left: -15px; right: -15px; height: 1px; }
    .crosshair::after { left: 50%; top: -15px; bottom: -15px; width: 1px; }

    .label {
        position: absolute; top: -15px; left: 50%; transform: translateX(-50%);
        font-family: var(--font-code); font-size: 8px;
        color: var(--arctic-cyan); font-weight: 700;
        white-space: nowrap; letter-spacing: 1px;
    }

    .coords {
        position: absolute; bottom: -15px; left: 50%; transform: translateX(-50%);
        font-family: var(--font-code); font-size: 7px;
        color: var(--paper-line); white-space: nowrap;
    }

    @keyframes pulse { 0% { opacity: 0.5; } 50% { opacity: 1; } 100% { opacity: 0.5; } }
</style>
```

### 4. Create Calibration Layer (`src/components/CalibrationLayer.svelte`)
This manages the script execution.

```svelte
<!-- [[RARO]]/apps/web-console/src/components/CalibrationLayer.svelte -->
<script lang="ts">
    import { onMount, tick } from 'svelte';
    import { fade } from 'svelte/transition';
    import GhostReticle from './sub/GhostReticle.svelte';
    import { 
        calibrationActive, completeCalibration, 
        toggleTheme, 
        resetSimulation, 
        selectNode, deselectNode, agentNodes
    } from '$lib/stores';
    import { get } from 'svelte/store';

    let currentTargetRect = $state<DOMRect | null>(null);
    let narrative = $state("INITIALIZING DIAGNOSTIC...");
    
    // === HELPER UTILS ===
    async function wait(ms: number) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    async function findElement(selector: string, retries = 5): Promise<HTMLElement | null> {
        await tick();
        const el = document.querySelector(selector) as HTMLElement;
        if (el) return el;
        if (retries > 0) {
            await wait(200);
            return findElement(selector, retries - 1);
        }
        return null;
    }

    async function setTarget(el: HTMLElement) {
        if (!el) return;
        // Scroll into view if needed (mostly for mobile/small screens)
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        currentTargetRect = el.getBoundingClientRect();
    }

    // === THE SCRIPT ===
    async function runSequence() {
        await wait(1000);

        // --- STEP 1: SETTINGS CHECK ---
        narrative = "STEP 1: CALIBRATING OPTICAL SENSORS...";
        const settingsRail = await findElement('.service-rail');
        if (settingsRail) {
            await setTarget(settingsRail);
            await wait(1200);
            
            // Simulate Interaction
            settingsRail.classList.add('expanded'); // Visual flare
            toggleTheme(); 
            await wait(600);
            toggleTheme();
            settingsRail.classList.remove('expanded');
            await wait(500);
        }

        // --- STEP 2: RFS CHECK ---
        narrative = "STEP 2: VERIFYING SECURE STORAGE...";
        const envRail = await findElement('.env-rail');
        if (envRail) {
            await setTarget(envRail);
            await wait(1200);
            envRail.classList.add('expanded'); // Force expansion visual
            await wait(1500);
            envRail.classList.remove('expanded');
        }

        // --- STEP 3: PIPELINE LOGIC ---
        narrative = "STEP 3: ANALYZING NEURAL TOPOLOGY...";
        const pipeline = await findElement('#pipeline-stage');
        if (pipeline) {
            await setTarget(pipeline);
            await wait(1200);
            
            // Expand Pipeline
            if (!pipeline.classList.contains('expanded')) {
                pipeline.click();
            }
            await wait(1000);

            // Select First Node
            const nodes = get(agentNodes);
            if (nodes.length > 0) {
                const nodeId = nodes[0].id;
                narrative = `INSPECTING NODE [${nodeId.toUpperCase().slice(0,8)}]...`;
                
                // Note: The DOM ID format from PipelineStage is `node-{id}`
                const nodeEl = await findElement(`#node-${nodeId}`);
                if (nodeEl) {
                    await setTarget(nodeEl);
                    await wait(800);
                    selectNode(nodeId);
                    await wait(2000);
                }
            }
        }

        // --- STEP 4: SIMULATION ---
        narrative = "STEP 4: RUNNING SYNTHETIC TEST PATTERN...";
        
        // Switch to Sim Tab
        const simTab = await findElement('#nav-tab-sim');
        if (simTab) {
            await setTarget(simTab);
            await wait(1000);
            simTab.click();
            await wait(800);
        }

        // Click Run Auto
        const runBtn = await findElement('#btn-sim-auto');
        if (runBtn) {
            await setTarget(runBtn);
            await wait(800);
            runBtn.click(); // This triggers the stores.runSimulation()
            
            narrative = "OBSERVING DATA FLOW...";
            currentTargetRect = null; // Hide reticle so user watches graph
            
            // Let it run for a bit
            await wait(6000);
        }

        // --- STEP 5: TEARDOWN ---
        narrative = "DIAGNOSTIC COMPLETE. PURGING CACHE...";
        resetSimulation();
        deselectNode();
        
        if (pipeline && pipeline.classList.contains('expanded')) {
            pipeline.click(); // Minimize
        }

        // Target input for handover
        const input = await findElement('#cmd-input');
        if (input) {
            await setTarget(input);
            await wait(1000);
        }

        completeCalibration();
    }

    onMount(() => {
        runSequence();
    });

    function skip() {
        resetSimulation();
        deselectNode();
        // Ensure pipeline minimized
        const p = document.getElementById('pipeline-stage');
        if (p && p.classList.contains('expanded')) p.click();
        
        completeCalibration();
    }
</script>

<div class="cal-overlay" transition:fade>
    <div class="curtain"></div>
    
    <!-- Cinematic Letterboxing -->
    <div class="bar top"></div>
    <div class="bar bottom"></div>

    <GhostReticle targetRect={currentTargetRect} />

    <div class="hud-center">
        <div class="spinner"></div>
        <div class="text">{narrative}</div>
    </div>

    <button class="skip-btn" onclick={skip}>SKIP CALIBRATION [ESC]</button>
</div>

<style>
    .cal-overlay {
        position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
        z-index: 9999;
        pointer-events: auto; /* Block interaction */
        cursor: progress;
        font-family: var(--font-code);
    }

    /* Dim background but keep UI visible */
    .curtain {
        position: absolute; width: 100%; height: 100%;
        background: rgba(0,0,0,0.3);
        backdrop-filter: grayscale(0.8);
        transition: all 0.5s;
    }

    /* Cinematic Bars */
    .bar {
        position: absolute; left: 0; width: 100%; height: 0;
        background: #000;
        animation: letterbox 0.5s forwards ease-out;
        z-index: 1;
    }
    .bar.top { top: 0; }
    .bar.bottom { bottom: 0; }

    @keyframes letterbox { to { height: 60px; } }

    .hud-center {
        position: absolute; bottom: 120px; left: 50%; transform: translateX(-50%);
        background: rgba(5, 5, 5, 0.9);
        border: 1px solid var(--arctic-cyan);
        padding: 12px 24px;
        display: flex; align-items: center; gap: 16px;
        box-shadow: 0 0 40px rgba(0, 240, 255, 0.2);
        z-index: 2;
        min-width: 300px;
    }

    .text {
        color: var(--arctic-cyan); font-weight: 700; letter-spacing: 1px; font-size: 12px;
        text-transform: uppercase;
    }

    .spinner {
        width: 12px; height: 12px;
        border: 2px solid var(--arctic-cyan); border-top-color: transparent;
        border-radius: 50%; animation: spin 0.8s linear infinite;
    }

    .skip-btn {
        position: absolute; bottom: 80px; left: 50%; transform: translateX(-50%);
        background: transparent; border: none;
        color: rgba(255,255,255,0.5); font-size: 10px;
        cursor: pointer; z-index: 2; letter-spacing: 1px;
    }
    .skip-btn:hover { color: #fff; }

    @keyframes spin { to { transform: rotate(360deg); } }
</style>
```

### 5. Integration (`src/App.svelte`)
Finally, mount the layer.

```svelte
<script lang="ts">
  import { fade } from 'svelte/transition'
  // ... existing imports ...
  import CalibrationLayer from '$components/CalibrationLayer.svelte'
  import { 
      addLog, themeStore, 
      checkCalibrationStatus, calibrationActive // Import Store
  } from '$lib/stores'

  // ... existing variables ...

  function enterConsole() {
    appState = 'CONSOLE'
    setTimeout(() => {
        addLog('KERNEL', 'RARO Runtime Environment v0.1.0.', 'SYSTEM_BOOT')
        // Trigger check AFTER entering console
        checkCalibrationStatus(); 
    }, 500)
  }

  // ... existing code ...
</script>

<main class="mode-{$themeStore.toLowerCase()} {slowMotion ? 'slow-motion' : ''}">
    <!-- ... noise overlay ... -->

    {#if appState === 'HERO'}
      <Hero onenter={enterConsole} />
    {:else}
      
      <!-- Mount Calibration Layer if Active -->
      {#if $calibrationActive}
          <CalibrationLayer />
      {/if}

      <div class="workspace" in:fade={{ duration: 800, delay: 200 }}>
         <!-- ... existing workspace components ... -->
      </div>
    {/if}
</main>
```

### Summary of Effect
1.  User enters. Hero screen plays.
2.  User clicks "Boot".
3.  Console loads. `checkCalibrationStatus` sees no local storage key.
4.  `<CalibrationLayer>` mounts.
5.  Letterbox bars slide in. Ghost Reticle appears.
6.  The system "takes over," checking Settings, Files, Pipeline, and running a Simulation.
7.  User learns the layout implicitly by watching the "Diagnostic."
8.  System hands control back. `raro_calibrated` is set to `true`.