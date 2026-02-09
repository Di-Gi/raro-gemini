<!-- [[RARO]]/apps/web-console/src/components/sub/SystemTag.svelte -->
<script lang="ts">
    import { fade } from 'svelte/transition';

    let { type, value }: { type: string, value: string } = $props();

    // Normalization
    let normalizedType = $derived(type.toUpperCase().trim());
    let displayValue = $derived(value.trim());

    // Style Logic
    let styleClass = $derived.by(() => {
        if (normalizedType.includes('SUCCESS') || displayValue.includes('SUCCESS')) return 'success';
        if (normalizedType.includes('FAIL') || normalizedType.includes('NULL')) return 'fail';
        if (normalizedType.includes('BYPASS')) return 'bypass';
        if (normalizedType.includes('CONTEXT')) return 'context';
        return 'info';
    });

    let icon = $derived.by(() => {
        if (styleClass === 'success') return '✓';
        if (styleClass === 'fail') return '✕';
        if (styleClass === 'bypass') return '↷';
        if (styleClass === 'context') return '📎';
        return 'ℹ';
    });
</script>

<div class="sys-tag {styleClass}" in:fade={{ duration: 200 }}>
    <div class="tag-label">
        <span class="icon">{icon}</span>
        {normalizedType}
    </div>
    <div class="tag-value">{displayValue}</div>
</div>

<style>
    .sys-tag {
        display: inline-flex;
        align-items: center;
        margin: 4px 0;
        font-family: var(--font-code);
        font-size: 10px;
        border-radius: 2px;
        overflow: hidden;
        border: 1px solid;
        max-width: 100%;
        vertical-align: middle;
        user-select: none;
    }

    .tag-label {
        padding: 2px 6px;
        font-weight: 700;
        display: flex;
        align-items: center;
        gap: 4px;
        text-transform: uppercase;
    }

    .tag-value {
        padding: 2px 8px;
        background: var(--paper-bg);
        color: var(--paper-ink);
        border-left: 1px solid;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    /* === VARIANTS === */

    /* SUCCESS / STATUS: SUCCESS */
    .sys-tag.success { border-color: var(--signal-success); }
    .sys-tag.success .tag-label { background: var(--signal-success); color: #fff; }
    .sys-tag.success .tag-value { border-left-color: var(--signal-success); }

    /* FAIL / STATUS: NULL */
    .sys-tag.fail { border-color: #d32f2f; }
    .sys-tag.fail .tag-label { background: #d32f2f; color: #fff; }
    .sys-tag.fail .tag-value { border-left-color: #d32f2f; color: #d32f2f; }

    /* BYPASS */
    .sys-tag.bypass { border-color: var(--alert-amber); }
    .sys-tag.bypass .tag-label { background: var(--alert-amber); color: #000; }
    .sys-tag.bypass .tag-value { border-left-color: var(--alert-amber); }

    /* CONTEXT */
    .sys-tag.context { border-color: var(--arctic-cyan); }
    .sys-tag.context .tag-label { background: rgba(0, 240, 255, 0.1); color: var(--arctic-cyan); }
    .sys-tag.context .tag-value { border-left-color: var(--arctic-cyan); }

    /* DEFAULT */
    .sys-tag.info { border-color: var(--paper-line); }
    .sys-tag.info .tag-label { background: var(--paper-line); color: var(--paper-bg); }
    .sys-tag.info .tag-value { border-left-color: var(--paper-line); }
</style>
