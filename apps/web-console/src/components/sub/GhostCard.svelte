<!-- [[RARO]]/apps/web-console/src/components/sub/GhostCard.svelte -->
<script lang="ts">
  import { fade } from 'svelte/transition';

  let { agentId }: { agentId: string } = $props();
</script>

<div class="ghost-group" transition:fade={{ duration: 200 }}>

  <!-- LEFT: Identity Column -->
  <div class="ghost-meta">
    <span class="ghost-role">{agentId}</span>
    <div class="timeline-line"></div>
  </div>

  <!-- RIGHT: The Shimmering Skeleton -->
  <div class="ghost-body">
    <div class="skeleton-bubble">
      <!-- Header / Metadata Line -->
      <div class="shimmer-line w-30"></div>

      <!-- Content Simulation -->
      <div class="shimmer-block">
        <div class="shimmer-line w-90 delay-1"></div>
        <div class="shimmer-line w-70 delay-2"></div>
        <div class="shimmer-line w-50 delay-3"></div>
      </div>
    </div>
  </div>

</div>

<style>
  .ghost-group {
    display: grid;
    grid-template-columns: 60px 1fr;
    gap: 16px;
    padding: 16px 0;
    border-top: 1px dashed transparent; /* Maintains vertical rhythm */
    opacity: 0.7;
  }

  /* Identity Column (Matches LogEntry styles) */
  .ghost-meta {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    position: relative;
  }

  .ghost-role {
    font-family: var(--font-code);
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.5px;
    color: var(--paper-line); /* Dimmer than active agents */
    background: var(--paper-bg);
    padding: 2px 4px;
    border-radius: 2px;
    border: 1px dashed var(--paper-line);
    text-transform: uppercase;
    z-index: 2;
  }

  .timeline-line {
    position: absolute;
    left: 50%; top: 20px; bottom: -20px;
    width: 1px;
    background: var(--paper-line);
    opacity: 0.1;
    z-index: 1;
    transform: translateX(-50%);
  }

  /* Skeleton Body */
  .ghost-body {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .skeleton-bubble {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 4px 0;
  }

  /* The Magic Shimmer Animation */
  .shimmer-line {
    height: 10px;
    border-radius: 2px;
    background: var(--paper-surface);
    background-image: linear-gradient(
      to right,
      var(--paper-surface) 0%,
      color-mix(in srgb, var(--paper-surface), var(--paper-ink) 5%) 20%,
      var(--paper-surface) 40%,
      var(--paper-surface) 100%
    );
    background-repeat: no-repeat;
    background-size: 1200px 100%;
    animation: shimmer 1.5s infinite linear;
    will-change: background-position;
  }

  /* Width Utilities */
  .w-30 { width: 30%; height: 8px; margin-bottom: 4px; }
  .w-50 { width: 50%; }
  .w-70 { width: 70%; }
  .w-90 { width: 90%; }

  .shimmer-block {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  /* Staggered Delays for organic feel */
  .delay-1 { animation-delay: 0.1s; }
  .delay-2 { animation-delay: 0.2s; }
  .delay-3 { animation-delay: 0.3s; }

  @keyframes shimmer {
    0% { background-position: -1200px 0; }
    100% { background-position: 1200px 0; }
  }

  /* === PHOSPHOR MODE ENHANCEMENT === */
  /* Holographic cyan shimmer for night mode */
  :global(.mode-phosphor) .shimmer-line {
    background: rgba(0, 240, 255, 0.05); /* Very faint cyan base */
    background-image: linear-gradient(
      to right,
      rgba(0, 240, 255, 0.05) 0%,
      rgba(0, 240, 255, 0.15) 20%, /* Brighter cyan shimmer */
      rgba(0, 240, 255, 0.05) 40%,
      rgba(0, 240, 255, 0.05) 100%
    );
  }
</style>
