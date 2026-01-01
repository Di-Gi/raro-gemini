<!-- apps/web-console/src/components/Hero.svelte -->
<script lang="ts">
  import { fade } from 'svelte/transition';

  // Props definition in Svelte 5
  let { onenter }: { onenter: () => void } = $props();

  let loading = $state(false);

  function handleEnter() {
    loading = true;
    // Simulate a brief boot sequence delay for effect
    setTimeout(() => {
      onenter();
    }, 800);
  }
</script>

<div class="hero-container" out:fade={{ duration: 400 }}>
  <div class="content-wrapper">
    <div class="header">
      <div class="logo-mark">RARO // KERNEL</div>
      <div class="version">v0.1.0</div>
    </div>

    <div class="main-title">
      <h1>RECURSIVE<br>AGENT<br>REASONING</h1>
    </div>

    <div class="description">
      <p>
        An orchestrator for <span class="highlight">Gemini 3</span> deep-think protocols.
        <br>Synthesizing complex research via DAG-based agent chains.
      </p>
    </div>

    <div class="action-area">
      <button 
        class="btn-enter {loading ? 'loading' : ''}" 
        onclick={handleEnter} 
        disabled={loading}
      >
        {#if loading}
          INITIALIZING RUNTIME...
        {:else}
          BOOT SYSTEM
        {/if}
      </button>
    </div>

    <div class="footer">
      <div class="stat">STATUS: <strong>STANDBY</strong></div>
      <div class="stat">MEMORY: <strong>CLEAN</strong></div>
    </div>
  </div>
</div>

<style>
  .hero-container {
    width: 100%;
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--paper-bg);
    position: absolute;
    top: 0;
    left: 0;
    z-index: 1000;
  }

  .content-wrapper {
    max-width: 600px;
    width: 100%;
    padding: 40px;
    border: 1px solid var(--paper-line);
    background: var(--paper-surface);
    box-shadow: 20px 20px 0 rgba(0,0,0,0.05);
    position: relative;
  }

  /* Decorative corners */
  .content-wrapper::before {
    content: '';
    position: absolute;
    top: -1px; left: -1px;
    width: 10px; height: 10px;
    border-top: 2px solid var(--paper-ink);
    border-left: 2px solid var(--paper-ink);
  }
  .content-wrapper::after {
    content: '';
    position: absolute;
    bottom: -1px; right: -1px;
    width: 10px; height: 10px;
    border-bottom: 2px solid var(--paper-ink);
    border-right: 2px solid var(--paper-ink);
  }

  .header {
    display: flex;
    justify-content: space-between;
    font-family: var(--font-code);
    font-size: 11px;
    color: #888;
    margin-bottom: 40px;
    border-bottom: 1px solid var(--paper-line);
    padding-bottom: 10px;
  }

  .main-title h1 {
    font-family: var(--font-ui);
    font-size: 64px;
    font-weight: 800;
    line-height: 0.9;
    letter-spacing: -2px;
    margin: 0 0 30px 0;
    color: var(--paper-ink);
  }

  .description {
    font-family: var(--font-code);
    font-size: 13px;
    line-height: 1.6;
    color: #555;
    margin-bottom: 50px;
    max-width: 80%;
  }

  .highlight {
    color: var(--paper-ink);
    font-weight: 700;
    background: rgba(0,0,0,0.05);
    padding: 0 4px;
  }

  .action-area {
    margin-bottom: 40px;
  }

  .btn-enter {
    width: 100%;
    height: 56px;
    background: var(--paper-ink);
    color: white;
    border: none;
    font-family: var(--font-code);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 1px;
    cursor: pointer;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .btn-enter:hover {
    background: #000;
    transform: translateY(-2px);
    box-shadow: 0 5px 15px rgba(0,0,0,0.2);
  }

  .btn-enter:active {
    transform: translateY(0);
  }

  .btn-enter.loading {
    background: #333;
    cursor: wait;
    opacity: 0.8;
  }

  .footer {
    display: flex;
    gap: 20px;
    font-family: var(--font-code);
    font-size: 10px;
    color: #888;
    text-transform: uppercase;
  }

  .stat strong {
    color: var(--paper-ink);
  }
</style>