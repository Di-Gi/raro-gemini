<!-- // [[RARO]]/apps/web-console/src/components/sub/Spinner.svelte
// Purpose: Reusable CSS loading spinner.
// Architecture: UI Atom
// Dependencies: None -->

<script lang="ts">
  // Simple CSS-based loading spinner
</script>

<div class="spinner"></div>

<style>
  .spinner {
    width: 12px;
    height: 12px;
    border: 2px solid rgba(0, 0, 0, 0.1);
    border-left-color: var(--paper-ink);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    display: block; /* Changed from inline-block for better layout control in parents */
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }
</style>