<!-- [[RARO]]/apps/web-console/src/components/sub/ApprovalCard.svelte -->
<script lang="ts">
  import { resumeRun, stopRun, runtimeStore } from '$lib/stores';
  import { fade } from 'svelte/transition';

  let { reason, runId }: { reason: string, runId: string } = $props();
  
  let processing = $state(false);
  let decision = $state<'APPROVED' | 'DENIED' | null>(null);

  async function handleApprove() {
      processing = true;
      await resumeRun(runId);
      decision = 'APPROVED';
      processing = false;
  }

  async function handleDeny() {
      processing = true;
      await stopRun(runId);
      decision = 'DENIED';
      processing = false;
  }
</script>

<div class="approval-card {decision ? decision.toLowerCase() : 'pending'}" transition:fade>
  <div class="header">
      <span class="icon">⚠</span>
      <span>INTERVENTION REQUIRED</span>
  </div>

  <div class="content">
      <div class="reason-label">REASONING:</div>
      <div class="reason-text">"{reason}"</div>
  </div>

  <div class="actions">
      {#if decision === 'APPROVED'}
          <div class="stamp success">AUTHORIZED</div>
      {:else if decision === 'DENIED'}
          <div class="stamp fail">TERMINATED</div>
      {:else}
          <button class="btn deny" onclick={handleDeny} disabled={processing}>
              STOP RUN
          </button>
          <button class="btn approve" onclick={handleApprove} disabled={processing}>
              {#if processing}PROCESSING...{:else}AUTHORIZE RESUME{/if}
          </button>
      {/if}
  </div>
</div>

<style>
  .approval-card {
      margin: 16px 0;
      border: 1px solid var(--alert-amber);
      background: color-mix(in srgb, var(--paper-bg), var(--alert-amber) 5%);
      font-family: var(--font-code);
      border-radius: 2px;
      overflow: hidden;
  }

  .approval-card.approved { border-color: var(--signal-success); opacity: 0.7; }
  .approval-card.denied { border-color: #d32f2f; opacity: 0.7; }

  .header {
      background: var(--alert-amber);
      color: #000;
      padding: 6px 12px;
      font-weight: 700;
      font-size: 10px;
      letter-spacing: 1px;
      display: flex; gap: 8px; align-items: center;
  }
  
  .approved .header { background: var(--signal-success); color: white; }
  .denied .header { background: #d32f2f; color: white; }

  .content { padding: 16px; border-bottom: 1px solid var(--paper-line); }
  .reason-label { font-size: 8px; color: var(--paper-line); font-weight: 700; margin-bottom: 4px; }
  .reason-text { font-size: 13px; color: var(--paper-ink); font-style: italic; }

  .actions { padding: 12px; display: flex; justify-content: flex-end; gap: 12px; height: 50px; align-items: center; }

  .btn {
      border: none; padding: 8px 16px; font-family: var(--font-code);
      font-size: 10px; font-weight: 700; cursor: pointer; border-radius: 2px;
  }

  .btn.deny { background: transparent; border: 1px solid var(--paper-line); color: var(--paper-ink); }
  .btn.deny:hover { background: #d32f2f; color: white; border-color: #d32f2f; }

  .btn.approve { background: var(--paper-ink); color: var(--paper-bg); }
  .btn.approve:hover { opacity: 0.9; }

  .stamp { font-weight: 900; letter-spacing: 2px; font-size: 12px; }
  .stamp.success { color: var(--signal-success); }
  .stamp.fail { color: #d32f2f; }
</style>