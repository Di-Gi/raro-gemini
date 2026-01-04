<!-- [[RARO]]/apps/web-console/src/components/EnvironmentRail.svelte -->
<!-- Purpose: Left-hand navigation rail for File System management. -->
<!-- Architecture: UI Component -->
<!-- Dependencies: stores, api -->

<script lang="ts">
  import { onMount } from 'svelte';
  import { libraryFiles, attachedFiles, toggleAttachment, addLog } from '$lib/stores';
  import { getLibraryFiles } from '$lib/api';
  import Spinner from './sub/Spinner.svelte';

  let hovered = $state(false);
  let isRefreshing = $state(false);

  // Initial Load
  onMount(async () => {
    refreshLibrary();
  });

  async function refreshLibrary() {
      isRefreshing = true;
      try {
        const files = await getLibraryFiles();
        libraryFiles.set(files);
      } catch(err) {
          console.error(err);
      } finally {
          isRefreshing = false;
      }
  }

  // MVP: Manual refresh as upload is physical placement for now
  function handleRefresh() {
      refreshLibrary();
      addLog('SYSTEM', 'Library index updated.', 'IO_OK');
  }
</script>

<div 
  class="env-rail {hovered ? 'expanded' : ''}"
  onmouseenter={() => hovered = true}
  onmouseleave={() => hovered = false}
  role="complementary"
>
  <div class="milled-bg"></div>

  <div class="rail-container">
    
    <!-- TOP: LABEL -->
    <div class="sector top">
      <div class="label-vertical">ENV</div>
      <div class="micro-bolt"></div>
    </div>

    <!-- MIDDLE: CONTENT -->
    <div class="sector middle">
      
      <!-- COLLAPSED STATE -->
      <div class="compact-view" style="opacity: {hovered ? 0 : 1}">
        <div class="disk-indicator {$attachedFiles.length > 0 ? 'active' : ''}">
            {#if isRefreshing}<Spinner />{/if}
        </div>
        {#if $attachedFiles.length > 0}
            <div class="count-badge">{$attachedFiles.length}</div>
        {/if}
      </div>

      <!-- EXPANDED STATE -->
      <div class="expanded-view" style="opacity: {hovered ? 1 : 0}; pointer-events: {hovered ? 'auto' : 'none'}">
        <div class="panel-header">WORKSPACE LIB</div>
        
        <div class="file-list">
            {#each $libraryFiles as file}
                <button 
                    class="file-item {$attachedFiles.includes(file) ? 'linked' : ''}"
                    onclick={() => toggleAttachment(file)}
                >
                    <div class="status-led"></div>
                    <span class="filename" title={file}>{file}</span>
                    <span class="link-status">
                        {$attachedFiles.includes(file) ? 'LINKED' : 'IDLE'}
                    </span>
                </button>
            {/each}
            
            {#if $libraryFiles.length === 0}
                <div class="empty-state">
                    {#if isRefreshing}
                        READING DISK...
                    {:else}
                        NO_DATA
                    {/if}
                </div>
            {/if}
        </div>

        <div class="actions">
            <div class="hint-text">DROP FILES IN STORAGE/LIBRARY</div>
            <button class="btn-refresh" onclick={handleRefresh} disabled={isRefreshing}>
                {#if isRefreshing}SCANNING...{:else}↻ REFRESH{/if}
            </button>
        </div>
      </div>

    </div>

    <!-- BOTTOM: DECOR -->
    <div class="sector bottom">
      <div class="micro-bolt"></div>
      <div class="label-vertical">IO</div>
    </div>
  </div>
</div>

<style>
  .env-rail {
    position: absolute; left: 0; top: 0;
    height: 100vh; width: 48px;
    border-right: 1px solid var(--paper-line);
    background: var(--paper-bg);
    display: flex; flex-direction: column;
    transition: width 0.3s var(--ease-snap), background-color 0.3s;
    overflow: hidden; z-index: 50;
  }

  .env-rail.expanded {
    width: 200px; /* Wider than settings rail to accommodate filenames */
    background: var(--paper-surface);
    box-shadow: 15px 0 50px rgba(0,0,0,0.1);
  }

  .milled-bg {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    opacity: 0.03;
    background-image: repeating-linear-gradient(-45deg, transparent, transparent 1px, var(--paper-ink) 1px, var(--paper-ink) 2px);
    pointer-events: none;
  }

  .rail-container {
    position: relative; z-index: 2; height: 100%;
    display: flex; flex-direction: column; justify-content: space-between;
  }

  .sector { display: flex; flex-direction: column; align-items: center; padding: 24px 0; gap: 12px; }
  .sector.middle { flex: 1; justify-content: flex-start; padding-top: 60px; width: 100%; }

  .label-vertical {
    writing-mode: vertical-lr; text-orientation: mixed; transform: rotate(180deg);
    font-family: var(--font-code); font-size: 8px;
    color: var(--paper-line); letter-spacing: 1px; font-weight: 700;
  }

  .micro-bolt { width: 4px; height: 4px; background: var(--paper-line); border-radius: 50%; opacity: 0.5; }

  /* COMPACT VIEW */
  .compact-view { position: absolute; top: 50%; transform: translateY(-50%); display: flex; flex-direction: column; gap: 8px; align-items: center; }
  
  .disk-indicator {
    width: 8px; height: 8px; background: var(--paper-line); border-radius: 1px;
    transition: all 0.3s;
  }
  .disk-indicator.active { background: var(--alert-amber); box-shadow: 0 0 6px var(--alert-amber); }
  
  .count-badge {
    font-family: var(--font-code); font-size: 9px; color: var(--paper-bg);
    background: var(--paper-ink); padding: 1px 4px; border-radius: 2px;
  }

  /* EXPANDED VIEW */
  .expanded-view {
    width: 100%; height: 100%; padding: 0 16px;
    display: flex; flex-direction: column; gap: 16px;
  }

  .panel-header {
    font-family: var(--font-code); font-size: 10px; font-weight: 700;
    color: var(--paper-ink); border-bottom: 1px solid var(--paper-line);
    padding-bottom: 8px; letter-spacing: 1px;
  }

  .file-list {
    flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px;
  }

  .file-item {
    background: transparent; border: 1px solid transparent;
    padding: 8px; display: flex; align-items: center; gap: 8px;
    cursor: pointer; transition: all 0.2s; text-align: left;
    border-radius: 2px;
  }

  .file-item:hover { background: var(--paper-bg); border-color: var(--paper-line); }
  
  .file-item.linked {
    background: color-mix(in srgb, var(--paper-ink), transparent 95%);
    border-color: var(--paper-ink);
  }

  .status-led {
    width: 4px; height: 4px; background: var(--paper-line); border-radius: 50%;
  }
  .file-item.linked .status-led { background: var(--alert-amber); box-shadow: 0 0 4px var(--alert-amber); }

  .filename {
    flex: 1; font-family: var(--font-code); font-size: 10px; color: var(--paper-ink);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  .link-status {
    font-size: 7px; font-weight: 700; color: var(--paper-line);
  }
  .file-item.linked .link-status { color: var(--paper-ink); }

  .empty-state {
    font-family: var(--font-code); font-size: 10px; color: var(--paper-line);
    text-align: center; margin-top: 20px; opacity: 0.5;
  }

  .actions {
    padding-top: 12px; border-top: 1px solid var(--paper-line);
  }

  .hint-text {
      font-size: 8px; color: var(--paper-line); text-align: center; margin-bottom: 8px; opacity: 0.7;
  }

  .btn-refresh {
    width: 100%; background: var(--paper-ink); color: var(--paper-bg);
    border: none; padding: 8px; font-family: var(--font-code);
    font-size: 10px; font-weight: 700; cursor: pointer; letter-spacing: 1px;
  }
  .btn-refresh:hover { opacity: 0.9; }
  .btn-refresh:disabled { opacity: 0.5; cursor: wait; }
</style>