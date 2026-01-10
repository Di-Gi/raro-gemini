<!-- [[RARO]]/apps/web-console/src/components/EnvironmentRail.svelte -->
<!-- Purpose: Left-hand navigation rail for File System and Artifact management. -->
<!-- Architecture: UI Component -->
<!-- Dependencies: stores, api -->

<script lang="ts">
  import { onMount } from 'svelte';
  import { libraryFiles, attachedFiles, toggleAttachment, addLog, runtimeStore } from '$lib/stores';
  import { getLibraryFiles, uploadFile, getAllArtifacts, deleteArtifactRun, getArtifactFileUrl, type ArtifactMetadata, type ArtifactFile } from '$lib/api';
  import Spinner from './sub/Spinner.svelte';
  import ArtifactViewer from './ArtifactViewer.svelte';

  // Props using Svelte 5 runes syntax
  let { currentRunId = null }: { currentRunId?: string | null } = $props();

  let hovered = $state(false);
  let isRefreshing = $state(false);

  // === UPLOAD STATE ===
  let isUploading = $state(false);
  let fileInput = $state<HTMLInputElement>();

  // === ARTIFACT STATE ===
  let artifacts = $state<ArtifactMetadata[]>([]);
  let artifactFilter = $state<'all' | 'recent'>('all');
  let lastRefreshTime = $state<number>(0);
  let lastRuntimeStatus = $state<string>('');
  let hasNewArtifacts = $state<boolean>(false);
  let newArtifactTimer: number | null = null;

  // === VIEWER STATE ===
  let selectedArtifact = $state<ArtifactFile | null>(null);
  let selectedRunMetadata = $state<ArtifactMetadata | null>(null);

  // === AUTO-REFRESH STATE ===
  let refreshDebounceTimer: number | null = null;

  // Initial Load
  onMount(async () => {
    refreshAll();

    // Subscribe to runtime status changes
    const unsubscribe = runtimeStore.subscribe((state) => {
      // Auto-refresh artifacts when workflow completes
      if (state.status === 'COMPLETED' && lastRuntimeStatus !== 'COMPLETED') {
        console.log('[EnvironmentRail] Workflow completed, refreshing artifacts...');
        silentRefreshArtifacts();
      }
      lastRuntimeStatus = state.status;
    });

    return () => {
      unsubscribe();
      if (refreshDebounceTimer) {
        clearTimeout(refreshDebounceTimer);
      }
      if (newArtifactTimer) {
        clearTimeout(newArtifactTimer);
      }
    };
  });

  // Watch for expansion and trigger auto-refresh
  $effect(() => {
    if (hovered) {
      handleExpansion();
    }
  });

  function handleExpansion() {
    // Debounce: Only refresh if rail hasn't been refreshed in the last 5 seconds
    const now = Date.now();
    if (now - lastRefreshTime > 5000) {
      silentRefreshArtifacts();
    }
  }

  async function refreshAll() {
    await Promise.all([refreshLibrary(), refreshArtifacts()]);
  }

  async function refreshLibrary() {
    isRefreshing = true;
    try {
      const files = await getLibraryFiles();
      libraryFiles.set(files);
    } catch (err) {
      console.error(err);
    } finally {
      isRefreshing = false;
    }
  }

  async function refreshArtifacts() {
    try {
      artifacts = await getAllArtifacts();
      lastRefreshTime = Date.now();
    } catch (err) {
      console.error('Failed to fetch artifacts:', err);
    }
  }

  async function silentRefreshArtifacts() {
    try {
      const newArtifacts = await getAllArtifacts();

      // Delta detection: Only update if there are actual changes
      const hasChanges = detectArtifactChanges(artifacts, newArtifacts);

      if (hasChanges) {
        console.log('[EnvironmentRail] New artifacts detected, updating UI...');
        artifacts = newArtifacts;
        lastRefreshTime = Date.now();

        // Show visual indicator
        hasNewArtifacts = true;

        // Clear indicator after 3 seconds
        if (newArtifactTimer) clearTimeout(newArtifactTimer);
        newArtifactTimer = setTimeout(() => {
          hasNewArtifacts = false;
        }, 3000) as unknown as number;
      } else {
        console.log('[EnvironmentRail] No artifact changes detected');
        lastRefreshTime = Date.now();
      }
    } catch (err) {
      console.error('Failed to silently refresh artifacts:', err);
    }
  }

  function detectArtifactChanges(
    oldArtifacts: ArtifactMetadata[],
    newArtifacts: ArtifactMetadata[]
  ): boolean {
    // Quick check: different lengths means changes
    if (oldArtifacts.length !== newArtifacts.length) {
      return true;
    }

    // Deep check: compare run IDs and file counts
    const oldSignature = oldArtifacts
      .map(a => `${a.run_id}:${a.artifacts.length}`)
      .sort()
      .join('|');

    const newSignature = newArtifacts
      .map(a => `${a.run_id}:${a.artifacts.length}`)
      .sort()
      .join('|');

    return oldSignature !== newSignature;
  }

  function handleRefresh() {
    refreshAll();
    addLog('SYSTEM', 'Environment refreshed.', 'IO_OK');
  }

  // === HANDLE UPLOAD ===
  async function handleFileUpload(e: Event) {
    const target = e.target as HTMLInputElement;
    if (!target.files || target.files.length === 0) return;

    const file = target.files[0];
    isUploading = true;
    addLog('SYSTEM', `Uploading ${file.name} to library...`, 'IO_UP');

    try {
      await uploadFile(file);
      addLog('SYSTEM', 'Upload complete.', 'IO_OK');
      await refreshLibrary();
    } catch (err) {
      addLog('SYSTEM', `Upload failed: ${err}`, 'IO_ERR');
    } finally {
      isUploading = false;
      target.value = '';
    }
  }

  function triggerUpload() {
    fileInput?.click();
  }

  // === ARTIFACT HANDLING ===
  let filteredArtifacts = $derived(filterArtifacts(artifacts, artifactFilter));

  function filterArtifacts(all: ArtifactMetadata[], filter: string): ArtifactMetadata[] {
    if (filter === 'recent') {
      const oneDayAgo = Date.now() - 24 * 60 * 60 * 1000;
      return all.filter(a => new Date(a.created_at).getTime() > oneDayAgo);
    }
    return all;
  }

  function getTimeAgo(isoDate: string): string {
    const ms = Date.now() - new Date(isoDate).getTime();
    const mins = Math.floor(ms / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  }

  function getFileIcon(contentType: string): string {
    if (contentType.includes('image')) return '📊';
    if (contentType.includes('json')) return '📋';
    if (contentType.includes('csv')) return '📈';
    if (contentType.includes('markdown')) return '📝';
    if (contentType.includes('pdf')) return '📄';
    return '📄';
  }

  async function handleDeleteRun(runId: string, e: Event) {
    e.stopPropagation();
    if (!confirm('Delete all artifacts from this run?')) return;

    try {
      await deleteArtifactRun(runId);
      addLog('SYSTEM', 'Artifact run deleted.', 'IO_OK');

      // Immediately update UI by removing the run
      artifacts = artifacts.filter(a => a.run_id !== runId);
      lastRefreshTime = Date.now();
    } catch (err) {
      addLog('SYSTEM', 'Failed to delete artifacts.', 'IO_ERR');
    }
  }

  function handleArtifactClick(artifact: ArtifactFile, runMetadata: ArtifactMetadata, e: Event) {
    e.preventDefault();
    selectedArtifact = artifact;
    selectedRunMetadata = runMetadata;
  }

  function closeViewer() {
    selectedArtifact = null;
    selectedRunMetadata = null;
  }

  // Calculate total file count
  let totalFileCount = $derived($libraryFiles.length + artifacts.reduce((sum, a) => sum + a.artifacts.length, 0));
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
          {#if isRefreshing || isUploading}<Spinner />{/if}
        </div>
        {#if totalFileCount > 0}
          <div class="count-badge">{totalFileCount}</div>
        {/if}
      </div>

      <!-- EXPANDED STATE -->
      <div class="expanded-view" style="opacity: {hovered ? 1 : 0}; pointer-events: {hovered ? 'auto' : 'none'}">

        <!-- LIBRARY FILES SECTION -->
        <section class="file-section">
          <div class="panel-header">
            📚 LIBRARY FILES
            <button class="btn-icon" onclick={handleRefresh} disabled={isRefreshing || isUploading} title="Refresh">
              {#if isRefreshing}⟳{:else}↻{/if}
            </button>
          </div>

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
                {#if isRefreshing}SCANNING...{:else}NO FILES{/if}
              </div>
            {/if}
          </div>

          <div class="actions">
            <input
              type="file"
              bind:this={fileInput}
              onchange={handleFileUpload}
              style="display:none"
            />

            <button class="btn-action upload" onclick={triggerUpload} disabled={isRefreshing || isUploading}>
              {#if isUploading}UPLOADING...{:else}↑ UPLOAD{/if}
            </button>
          </div>
        </section>

        <div class="divider"></div>

        <!-- GENERATED ARTIFACTS SECTION -->
        <section class="artifact-section">
          <div class="panel-header">
            🎨 ARTIFACTS
            {#if hasNewArtifacts}
              <span class="new-badge">NEW</span>
            {/if}
          </div>

          <div class="controls">
            <select bind:value={artifactFilter} class="filter-select">
              <option value="all">All Runs</option>
              <option value="recent">Last 24h</option>
            </select>
          </div>

          <div class="artifact-list">
            {#each filteredArtifacts as run}
              <div class="run-group">
                <div class="run-header">
                  <span class="run-id" title={run.run_id}>{run.run_id.slice(0, 8)}...</span>
                  <span class="run-time">{getTimeAgo(run.created_at)}</span>
                  <button class="btn-delete" onclick={(e) => handleDeleteRun(run.run_id, e)} title="Delete">
                    🗑️
                  </button>
                </div>

                {#each run.artifacts as artifact}
                  <button
                    class="artifact-item"
                    onclick={(e) => handleArtifactClick(artifact, run, e)}
                  >
                    <span class="icon">{getFileIcon(artifact.content_type)}</span>
                    <div class="artifact-info">
                      <div class="artifact-name">{artifact.filename}</div>
                      <div class="artifact-meta">by {artifact.agent_id}</div>
                    </div>
                    <span class="preview-icon">👁</span>
                  </button>
                {/each}
              </div>
            {/each}

            {#if filteredArtifacts.length === 0}
              <div class="empty-state">No artifacts yet</div>
            {/if}
          </div>
        </section>

      </div>

    </div>

    <!-- BOTTOM: DECOR -->
    <div class="sector bottom">
      <div class="micro-bolt"></div>
      <div class="label-vertical">IO</div>
    </div>
  </div>
</div>

<!-- Artifact Viewer Overlay -->
<ArtifactViewer
  artifact={selectedArtifact}
  runMetadata={selectedRunMetadata}
  onClose={closeViewer}
/>

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
    width: 260px;
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
    display: flex; flex-direction: column; gap: 12px;
    overflow-y: auto;
  }

  .panel-header {
    font-family: var(--font-code); font-size: 10px; font-weight: 700;
    color: var(--paper-ink); border-bottom: 1px solid var(--paper-line);
    padding-bottom: 8px; letter-spacing: 1px;
    display: flex; align-items: center; justify-content: space-between;
    gap: 8px;
  }

  .new-badge {
    font-size: 8px;
    font-weight: 700;
    color: var(--paper-bg);
    background: var(--alert-amber);
    padding: 2px 6px;
    border-radius: 2px;
    animation: pulse-badge 0.5s ease-in-out;
  }

  @keyframes pulse-badge {
    0% {
      opacity: 0;
      transform: scale(0.8);
    }
    50% {
      opacity: 1;
      transform: scale(1.1);
    }
    100% {
      opacity: 1;
      transform: scale(1);
    }
  }

  .btn-icon {
    background: transparent; border: none;
    color: var(--paper-ink); font-size: 14px;
    cursor: pointer; padding: 0 4px;
  }
  .btn-icon:hover { opacity: 0.7; }
  .btn-icon:disabled { opacity: 0.3; cursor: wait; }

  /* LIBRARY FILES SECTION */
  .file-section {
    flex-shrink: 0;
  }

  .file-list {
    max-height: 180px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px;
    margin-bottom: 8px;
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

  .actions {
    display: flex; flex-direction: column; gap: 8px;
  }

  .btn-action {
    background: var(--paper-ink); color: var(--paper-bg);
    border: none; padding: 8px; font-family: var(--font-code);
    font-size: 10px; font-weight: 700; cursor: pointer; letter-spacing: 1px;
    height: 32px; display: flex; align-items: center; justify-content: center;
  }

  .btn-action:hover { opacity: 0.9; }
  .btn-action:disabled { opacity: 0.5; cursor: wait; }

  .divider {
    height: 1px; background: var(--paper-line); margin: 8px 0;
  }

  /* ARTIFACTS SECTION */
  .artifact-section {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .controls {
    margin-bottom: 8px;
  }

  .filter-select {
    width: 100%;
    padding: 6px 8px;
    background: var(--paper-bg);
    color: var(--paper-ink);
    border: 1px solid var(--paper-line);
    font-family: var(--font-code);
    font-size: 9px;
    border-radius: 2px;
    cursor: pointer;
  }

  .artifact-list {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .run-group {
    border-left: 2px solid var(--paper-line);
    padding-left: 8px;
  }

  .run-header {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 9px;
    color: var(--paper-line);
    margin-bottom: 6px;
  }

  .run-id {
    font-family: var(--font-code);
    flex: 1;
  }

  .run-time {
    font-size: 8px;
  }

  .btn-delete {
    background: transparent;
    border: none;
    cursor: pointer;
    font-size: 10px;
    padding: 0 2px;
    opacity: 0.5;
  }
  .btn-delete:hover { opacity: 1; }

  .artifact-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    background: var(--paper-bg);
    border: 1px solid var(--paper-line);
    border-radius: 2px;
    margin-bottom: 4px;
    text-decoration: none;
    color: inherit;
    transition: all 0.2s;
    width: 100%;
    cursor: pointer;
    text-align: left;
  }

  .artifact-item:hover {
    background: var(--paper-surface);
    border-color: var(--paper-ink);
  }

  .icon {
    font-size: 14px;
    flex-shrink: 0;
  }

  .artifact-info {
    flex: 1;
    min-width: 0;
  }

  .artifact-name {
    font-size: 10px;
    font-family: var(--font-code);
    color: var(--paper-ink);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .artifact-meta {
    font-size: 8px;
    color: var(--paper-line);
  }

  .preview-icon {
    font-size: 12px;
    opacity: 0.5;
  }

  .artifact-item:hover .preview-icon {
    opacity: 1;
  }

  .empty-state {
    font-family: var(--font-code); font-size: 10px; color: var(--paper-line);
    text-align: center; margin-top: 20px; opacity: 0.5;
  }
</style>
