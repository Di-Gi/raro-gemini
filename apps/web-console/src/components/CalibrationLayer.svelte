<!-- [[RARO]]/apps/web-console/src/components/CalibrationLayer.svelte -->
<script lang="ts">
    import { onMount, tick } from 'svelte';
    import { fade } from 'svelte/transition';
    import GhostReticle from './sub/GhostReticle.svelte';
    import {
        calibrationActive, completeCalibration,
        toggleTheme,
        resetSimulation,
        reinitializeConsole,
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

            // Trigger hover state by dispatching mouseenter event
            const mouseEnterEvent = new MouseEvent('mouseenter', {
                bubbles: true,
                cancelable: true,
                view: window
            });
            settingsRail.dispatchEvent(mouseEnterEvent);

            await wait(400);
            toggleTheme();
            await wait(600);
            toggleTheme();
            await wait(400);

            // Trigger mouseleave to collapse
            const mouseLeaveEvent = new MouseEvent('mouseleave', {
                bubbles: true,
                cancelable: true,
                view: window
            });
            settingsRail.dispatchEvent(mouseLeaveEvent);
            await wait(300);
        }

        // --- STEP 2: RFS CHECK ---
        narrative = "STEP 2: VERIFYING SECURE STORAGE...";
        const envRail = await findElement('.env-rail');
        if (envRail) {
            await setTarget(envRail);
            await wait(1200);

            // Trigger hover state by dispatching mouseenter event
            const mouseEnterEvent = new MouseEvent('mouseenter', {
                bubbles: true,
                cancelable: true,
                view: window
            });
            envRail.dispatchEvent(mouseEnterEvent);

            await wait(1500);

            // Trigger mouseleave to collapse
            const mouseLeaveEvent = new MouseEvent('mouseleave', {
                bubbles: true,
                cancelable: true,
                view: window
            });
            envRail.dispatchEvent(mouseLeaveEvent);
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

            // Deselect node to close configuration panel before moving to Step 4
            deselectNode();
            await wait(400);
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

            narrative = "INITIALIZING AGENTS...";
            currentTargetRect = null; // Hide reticle to watch execution

            // Wait for first couple nodes to execute
            await wait(4000);

            // Minimize pipeline to show main view with logs
            if (pipeline && pipeline.classList.contains('expanded')) {
                narrative = "MONITORING LIVE EXECUTION...";
                const minimizeBtn = await findElement('.btn-minimize');
                if (minimizeBtn) {
                    await setTarget(minimizeBtn);
                    await wait(600);
                    minimizeBtn.click();
                    await wait(500);
                }
            }

            // Watch simulation run in minimized view (give more time to reach interrupt)
            await wait(5000);

            // Wait for approval interrupt to appear
            narrative = "SAFETY PATTERN DETECTED...";
            const approvalBtn = await findElement('.btn-action.approve', 20); // Increased retries
            if (approvalBtn) {
                await setTarget(approvalBtn);
                await wait(1500);

                narrative = "AUTHORIZING CONTINUATION...";
                approvalBtn.click();
                await wait(1000);
            }

            // Wait for simulation to complete fully after approval
            narrative = "OBSERVING COMPLETION...";
            currentTargetRect = null;
            await wait(3000);
        }

        // --- STEP 5: TEARDOWN ---
        // Brief pause after completion
        await wait(1000);

        narrative = "DIAGNOSTIC COMPLETE. REVIEWING TOPOLOGY...";

        // Re-expand pipeline to show final state
        if (pipeline && !pipeline.classList.contains('expanded')) {
            await setTarget(pipeline);
            await wait(800);
            pipeline.click();
            await wait(1200);
        }

        narrative = "PURGING CACHE...";
        await wait(500);

        // Return to simulation tab before resetting (for visual consistency)
        const simTabFinal = await findElement('#nav-tab-sim');
        if (simTabFinal) {
            simTabFinal.click();
            await wait(400);
        }

        // Clean up simulation state
        resetSimulation();
        deselectNode();

        // Restore initial console logs
        reinitializeConsole();

        await wait(800);

        // Minimize pipeline using the minimize button
        if (pipeline && pipeline.classList.contains('expanded')) {
            const minimizeBtn = await findElement('.btn-minimize');
            if (minimizeBtn) {
                minimizeBtn.click();
                await wait(500);
            }
        }

        // Target input for handover
        narrative = "SYSTEM READY. TRANSFERRING CONTROL...";
        const input = await findElement('#cmd-input');
        if (input) {
            await setTarget(input);
            await wait(1200);
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
        if (p && p.classList.contains('expanded')) {
            const minimizeBtn = document.querySelector('.btn-minimize') as HTMLElement;
            if (minimizeBtn) minimizeBtn.click();
        }

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
