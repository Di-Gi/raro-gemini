<!-- [[RARO]]/apps/web-console/src/components/sub/ApprovalCard.svelte -->
<!-- Purpose: HITL Intervention UI. Styled as a physical security ticket/interrupt. -->

<script lang="ts">
  import { resumeRun, stopRun } from '$lib/stores';
  import { fade, slide } from 'svelte/transition';

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

<div 
  class="security-ticket {decision ? decision.toLowerCase() : 'pending'}" 
  transition:slide={{ duration: 300, axis: 'y' }}
>
  
  <!-- 1. HAZARD STRIP (Visual Indicator) -->
  <div class="hazard-strip"></div>

  <div class="ticket-body">
    
    <!-- 2. METADATA COLUMN -->
    <div class="col-meta">
      <div class="meta-row">
        <span class="label">TYPE</span>
        <span class="value">INTERRUPT</span>
      </div>
      <div class="meta-row">
        <span class="label">CODE</span>
        <span class="value warn">SEC_01</span>
      </div>
      <div class="icon-zone">
        {#if decision === 'APPROVED'}
           <span class="status-icon success">✓</span>
        {:else if decision === 'DENIED'}
           <span class="status-icon fail">✕</span>
        {:else}
           <span class="status-icon blink">!</span>
        {/if}
      </div>
    </div>

    <!-- 3. CONTENT COLUMN -->
    <div class="col-content">
      <div class="content-header">
        <span class="sys-msg">SYSTEM_PAUSE // AUTHORIZATION_REQUIRED</span>
      </div>
      
      <div class="reason-block">
        <span class="reason-label">TRIGGER_REASON:</span>
        <p class="reason-text">"{reason}"</p>
      </div>

      <!-- 4. ACTION DECK -->
      <div class="action-deck">
        {#if decision}
            <!-- STAMP RESULT -->
            <div class="stamp-container" in:fade>
                <div class="rubber-stamp {decision === 'APPROVED' ? 'stamp-ok' : 'stamp-fail'}">
                    {decision === 'APPROVED' ? 'AUTHORIZED' : 'TERMINATED'}
                </div>
                <span class="stamp-meta">OP_ID: {runId.slice(-6).toUpperCase()}</span>
            </div>
        {:else}
            <!-- INTERACTIVE BUTTONS -->
            <button class="btn-action deny" onclick={handleDeny} disabled={processing}>
                <span class="btn-bracket">[</span> ABORT <span class="btn-bracket">]</span>
            </button>
            
            <button class="btn-action approve" onclick={handleApprove} disabled={processing}>
                {#if processing}
                    <span class="blink">PROCESSING...</span>
                {:else}
                    <span class="btn-bracket">[</span> EXECUTE <span class="btn-bracket">]</span>
                {/if}
            </button>
        {/if}
      </div>
    </div>

  </div>
</div>

<style>
  /* === CHASSIS === */
  .security-ticket {
      margin: 20px 0;
      background: var(--paper-bg);
      border: 1px solid var(--paper-line);
      border-radius: 2px;
      font-family: var(--font-code);
      position: relative;
      overflow: hidden;
      box-shadow: 0 4px 20px rgba(0,0,0,0.05);
      display: flex;
      flex-direction: column;
      transition: border-color 0.3s, opacity 0.3s;
  }

  /* State Modifiers */
  .security-ticket.pending { border-color: var(--alert-amber); }
  .security-ticket.approved { border-color: var(--paper-line); opacity: 0.8; }
  .security-ticket.denied { border-color: #d32f2f; opacity: 0.8; }

  /* === HAZARD STRIP === */
  .hazard-strip {
      height: 4px;
      width: 100%;
      background-image: repeating-linear-gradient(
          -45deg,
          var(--alert-amber),
          var(--alert-amber) 10px,
          transparent 10px,
          transparent 20px
      );
      border-bottom: 1px solid var(--paper-line);
  }
  
  /* Strip Colors per State */
  .approved .hazard-strip {
      background-image: repeating-linear-gradient(-45deg, var(--signal-success), var(--signal-success) 10px, transparent 10px, transparent 20px);
  }
  .denied .hazard-strip {
      background-image: repeating-linear-gradient(-45deg, #d32f2f, #d32f2f 10px, transparent 10px, transparent 20px);
  }

  /* === BODY LAYOUT === */
  .ticket-body { display: flex; min-height: 100px; }

  /* Left Meta Column */
  .col-meta {
      width: 80px;
      background: var(--paper-surface);
      border-right: 1px dashed var(--paper-line);
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      flex-shrink: 0;
  }

  .meta-row { display: flex; flex-direction: column; gap: 2px; }
  .label { font-size: 8px; color: var(--paper-line); font-weight: 700; letter-spacing: 0.5px; }
  .value { font-size: 10px; color: var(--paper-ink); font-weight: 700; }
  .value.warn { color: var(--alert-amber); }

  .icon-zone {
      margin-top: auto;
      display: flex; justify-content: center; align-items: center;
      height: 32px; width: 32px;
      border: 1px solid var(--paper-line);
      border-radius: 50%;
      align-self: center;
      background: var(--paper-bg);
  }
  .status-icon { font-weight: 900; font-size: 14px; }
  .status-icon.blink { color: var(--alert-amber); animation: blink 1s infinite; }
  .status-icon.success { color: var(--signal-success); }
  .status-icon.fail { color: #d32f2f; }

  /* Right Content Column */
  .col-content { flex: 1; display: flex; flex-direction: column; }

  .content-header {
      padding: 8px 16px;
      border-bottom: 1px solid var(--paper-line);
      background: color-mix(in srgb, var(--paper-surface), transparent 50%);
  }
  .sys-msg {
      font-size: 9px; font-weight: 700; letter-spacing: 1px;
      color: var(--paper-line); text-transform: uppercase;
  }

  .reason-block { padding: 16px; flex: 1; }
  .reason-label {
      font-size: 9px; color: var(--paper-ink); font-weight: 700;
      background: var(--paper-surface); padding: 2px 6px;
      margin-right: 8px;
  }
  .reason-text {
      display: inline;
      font-size: 12px; line-height: 1.5; color: var(--paper-ink);
      font-style: italic;
  }

  /* === ACTION DECK === */
  .action-deck {
      padding: 12px 16px;
      border-top: 1px dashed var(--paper-line);
      background: var(--paper-bg);
      display: flex; justify-content: flex-end; align-items: center;
      min-height: 50px;
  }

  /* Buttons */
  .btn-action {
      background: transparent;
      border: 1px solid transparent;
      padding: 8px 16px;
      font-family: var(--font-code);
      font-size: 11px; font-weight: 700; letter-spacing: 1px;
      cursor: pointer;
      color: var(--paper-line);
      transition: all 0.2s;
      display: flex; align-items: center; gap: 4px;
  }
  .btn-bracket { opacity: 0.5; transition: opacity 0.2s; }
  
  /* Deny Style */
  .btn-action.deny:hover { color: #d32f2f; }
  .btn-action.deny:hover .btn-bracket { opacity: 1; color: #d32f2f; }

  /* Approve Style */
  .btn-action.approve { color: var(--paper-ink); border: 1px solid var(--paper-line); margin-left: 12px; background: var(--paper-surface); }
  .btn-action.approve:hover { background: var(--paper-ink); color: var(--paper-bg); border-color: var(--paper-ink); }
  .btn-action.approve:disabled { opacity: 0.5; cursor: wait; background: transparent; color: var(--paper-line); }

  /* === RUBBER STAMP === */
  .stamp-container { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
  
  .rubber-stamp {
      font-size: 14px; font-weight: 900; letter-spacing: 2px;
      padding: 4px 12px;
      border: 3px solid currentColor;
      border-radius: 4px;
      text-transform: uppercase;
      transform: rotate(-8deg);
      mask-image: url("data:image/svg+xml;utf8,<svg width='100%' height='100%' xmlns='http://www.w3.org/2000/svg'><filter id='noise'><feTurbulence type='fractalNoise' baseFrequency='1.5' numOctaves='3' stitchTiles='stitch'/></filter><rect width='100%' height='100%' fill='white'/><rect width='100%' height='100%' filter='url(%23noise)' opacity='0.5'/></svg>");
      mix-blend-mode: multiply; /* Looks like ink on paper */
  }
  /* Phosphor Mode Override for Stamp Blend */
  :global(.mode-phosphor) .rubber-stamp { mix-blend-mode: normal; opacity: 0.9; }

  .stamp-ok { color: var(--signal-success); }
  .stamp-fail { color: #d32f2f; }

  .stamp-meta { font-size: 8px; color: var(--paper-line); font-weight: 700; margin-right: 4px; }

  @keyframes blink { 50% { opacity: 0; } }
</style>