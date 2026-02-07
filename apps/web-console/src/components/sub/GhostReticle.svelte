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
