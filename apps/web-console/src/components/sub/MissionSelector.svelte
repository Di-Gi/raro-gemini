<!-- [[RARO]]/apps/web-console/src/components/sub/MissionSelector.svelte -->
<!-- Change Type: Modified -->
<!-- Purpose: Simple Mission Tags for One-Click Scenario Loading -->
<!-- Architectural Context: Accessibility UX Component -->
<!-- Dependencies: scenarios -->

<script lang="ts">
    import { SCENARIOS, type MissionScenario } from '$lib/scenarios';

    let { onSelect }: { onSelect: (s: MissionScenario) => void } = $props();
</script>

<div class="mission-bar">
    {#each SCENARIOS as mission}
        <button
            class="mission-tag"
            onclick={() => onSelect(mission)}
            title={mission.description}
        >
            {mission.icon} {mission.title}
        </button>
    {/each}
</div>

<style>
    .mission-bar {
        display: flex;
        gap: 8px;
        margin-bottom: 8px;
        animation: slideDown 0.2s ease-out;
    }

    @keyframes slideDown {
        from { opacity: 0; transform: translateY(5px); }
        to { opacity: 1; transform: translateY(0); }
    }

    .mission-tag {
        background: var(--paper-surface);
        border: 1px solid var(--paper-line);
        color: var(--paper-line);
        font-family: var(--font-code);
        font-size: 9px;
        font-weight: 700;
        padding: 4px 10px;
        border-radius: 2px;
        cursor: pointer;
        transition: all 0.2s;
        letter-spacing: 0.5px;
        text-transform: uppercase;
    }

    .mission-tag:hover {
        border-color: var(--paper-ink);
        color: var(--paper-ink);
        background: var(--paper-bg);
    }
</style>
