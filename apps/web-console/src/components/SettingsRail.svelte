<!-- // [[RARO]]/apps/web-console/src/components/SettingsRail.svelte
// Purpose: "Micro-Latch" Service Rail. Compact, high-precision system control.
// Architecture: Ancillary UI Component
// Dependencies: Stores -->

<script lang="ts">
  import { themeStore, toggleTheme } from '$lib/stores';
  
  let hovered = $state(false);
  let focused = $state(false); 

  let isOpen = $derived(hovered || focused);

  function handleFocus() { focused = true; }
  function handleBlur() { focused = false; }

  function handleInteraction(e: MouseEvent | KeyboardEvent) {
    toggleTheme();
    if (e.detail > 0 && e.currentTarget instanceof HTMLElement) {
      e.currentTarget.blur();
      focused = false; 
    }
  }
</script>

<div 
  class="service-rail {isOpen ? 'expanded' : ''}"
  onmouseenter={() => hovered = true}
  onmouseleave={() => hovered = false}
  onfocusin={handleFocus}
  onfocusout={handleBlur}
  role="complementary"
  aria-label="System Configuration"
>
  <!-- Fine Grain Texture -->
  <div class="milled-bg"></div>

  <div class="rail-container">
    
    <!-- TOP: ID -->
    <div class="sector top">
      <div class="label-vertical">SYS</div>
      <div class="micro-bolt"></div>
    </div>

    <!-- MIDDLE: The Precision Switch -->
    <div class="sector middle">
      
      <!-- Collapsed: Nano LED -->
      <div class="compact-view" style="opacity: {isOpen ? 0 : 1}">
        <div class="pilot-dot {$themeStore === 'PHOSPHOR' ? 'active' : ''}"></div>
      </div>

      <!-- Expanded: Micro Latch -->
      <div class="mechanism-view" style="opacity: {isOpen ? 1 : 0}; pointer-events: {isOpen ? 'auto' : 'none'}">
        <div class="mech-label">REALITY</div>
        
        <button 
          class="micro-track" 
          onclick={handleInteraction} 
          aria-label="Toggle Reality"
          aria-pressed={$themeStore === 'PHOSPHOR'}
        >
          <!-- Internal Hairline Glow -->
          <div class="hairline-luma {$themeStore === 'PHOSPHOR' ? 'glow' : ''}"></div>

          <!-- The Compact Block -->
          <div class="micro-block {$themeStore === 'PHOSPHOR' ? 'engaged' : 'disengaged'}">
            <!-- Fine Grip Lines -->
            <div class="fine-grip">
              <span></span><span></span><span></span><span></span>
            </div>
          </div>
        </button>

        <div class="readout-group">
          <span class="value">{$themeStore === 'ARCHIVAL' ? 'ARC' : 'PHO'}</span>
        </div>
      </div>

    </div>

    <!-- BOTTOM: Decor -->
    <div class="sector bottom">
      <div class="micro-bolt"></div>
      <div class="label-vertical">V1</div>
    </div>

  </div>
</div>

<style>
  /* === RAIL CHASSIS === */
  .service-rail {
    position: absolute; right: 0; top: 0;
    height: 100vh; width: 48px;
    border-left: 1px solid var(--paper-line);
    background: var(--paper-bg); 
    display: flex; flex-direction: column;
    transition: width 0.3s var(--ease-snap), background-color 0.3s;
    overflow: hidden; z-index: 50;
  }

  .service-rail.expanded {
    width: 80px; /* Tighter expansion */
    background: var(--paper-surface);
    box-shadow: -15px 0 50px rgba(0,0,0,0.1);
  }

  .milled-bg {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    opacity: 0.03;
    background-image: repeating-linear-gradient(45deg, transparent, transparent 1px, var(--paper-ink) 1px, var(--paper-ink) 2px);
    pointer-events: none;
  }

  .rail-container {
    position: relative; z-index: 2; height: 100%; 
    display: flex; flex-direction: column; justify-content: space-between;
  }

  /* === SECTORS === */
  .sector { display: flex; flex-direction: column; align-items: center; padding: 24px 0; gap: 12px; }

  .label-vertical {
    writing-mode: vertical-rl; text-orientation: mixed;
    font-family: var(--font-code); font-size: 8px;
    color: var(--paper-line); letter-spacing: 1px; font-weight: 700;
    user-select: none;
  }

  .micro-bolt {
    width: 4px; height: 4px; background: var(--paper-line); border-radius: 50%; opacity: 0.5;
  }

  /* === CONTROLS === */
  .sector.middle { flex: 1; justify-content: center; }

  /* Compact View */
  .compact-view { position: absolute; transition: opacity 0.2s; pointer-events: none; }
  
  .pilot-dot {
    width: 4px; height: 4px; background: var(--paper-line); border-radius: 50%;
    transition: all 0.3s;
  }
  .pilot-dot.active {
    background: var(--arctic-cyan);
    box-shadow: 0 0 6px var(--arctic-cyan);
  }

  /* Expanded View */
  .mechanism-view {
    display: flex; flex-direction: column; align-items: center; gap: 12px;
    transition: opacity 0.2s 0.1s; width: 100%;
  }

  .mech-label {
    font-family: var(--font-code); font-size: 7px; color: var(--paper-ink); opacity: 0.5; letter-spacing: 1px;
  }

  /* === MICRO TRACK === */
  .micro-track {
    width: 26px; height: 64px; /* Much smaller footprint */
    background: var(--digi-void);
    border: 1px solid var(--paper-line);
    border-radius: 2px;
    position: relative; cursor: pointer; padding: 0;
    box-shadow: inset 0 2px 8px rgba(0,0,0,0.3);
    overflow: hidden;
  }
  .micro-track:focus-visible { outline: 1px solid var(--arctic-cyan); }

  /* Hairline Luma */
  .hairline-luma {
    position: absolute; left: 50%; top: 4px; bottom: 4px; width: 1px;
    background: var(--paper-line); opacity: 0.2; transform: translateX(-50%);
    transition: all 0.3s;
  }
  .hairline-luma.glow {
    background: var(--arctic-cyan); opacity: 0.8;
    box-shadow: 0 0 4px var(--arctic-cyan);
  }

  /* === MICRO BLOCK === */
  .micro-block {
    width: 20px; height: 28px;
    background: var(--paper-surface);
    border: 1px solid var(--paper-ink);
    border-radius: 1px;
    position: absolute; left: 2px;
    z-index: 10;
    /* Precise, snappy movement */
    transition: top 0.3s cubic-bezier(0.25, 1, 0.5, 1);
    box-shadow: 0 2px 6px rgba(0,0,0,0.2);
    display: flex; align-items: center; justify-content: center;
  }

  /* States */
  .micro-block.disengaged { top: 2px; }
  .micro-block.engaged { 
    top: 32px; /* 64 - 28 - 2 - 2(borders) */
    background: #111;
    border-color: var(--arctic-cyan);
    box-shadow: 0 0 10px rgba(0, 240, 255, 0.2);
  }

  /* Fine Grip Texture */
  .fine-grip { display: flex; flex-direction: column; gap: 2px; }
  .fine-grip span {
    width: 10px; height: 1px; background: var(--paper-ink); opacity: 0.5;
  }
  .micro-block.engaged .fine-grip span { background: var(--arctic-cyan); }

  /* === READOUT === */
  .readout-group .value { 
    font-family: var(--font-code); font-size: 9px; font-weight: 700; color: var(--paper-ink); 
  }
</style>