<!-- [[RARO]]/apps/web-console/src/components/sub/ArtifactCard.svelte -->
<script lang="ts">
  import { fade } from 'svelte/transition';
  import { USE_MOCK } from '$lib/api';
  import { getMockGeneratedFile } from '$lib/mock-api';

  let { filenames, runId }: { filenames: string[], runId: string } = $props();

  let currentIndex = $state(0);

  // Derived state based on the current index
  let currentFilename = $derived(filenames[currentIndex]);
  
  // Regex to check file types
  let isImage = $derived(/\.(png|jpg|jpeg|svg|gif|webp)$/i.test(currentFilename));
  
  let src = $derived(
    USE_MOCK
      ? (getMockGeneratedFile(currentFilename) || `/api/runtime/${runId}/files/${currentFilename}`)
      : `/api/runtime/${runId}/files/${currentFilename}`
  );

  let isLoading = $state(true);
  let hasError = $state(false);
  let textContent = $state<string | null>(null);

  // Reset and Load Content when file changes
  $effect(() => {
    // Dependency tracking
    const _f = currentFilename; 
    const _s = src;

    hasError = false;
    textContent = null;
    isLoading = true; // Start loading

    if (!isImage) {
        fetchTextContent(_s);
    }
    // Note: If it IS an image, the <img> tag in the markup handles the loading trigger
  });

  async function fetchTextContent(url: string) {
      try {
          const res = await fetch(url);
          if (!res.ok) throw new Error('Fetch failed');
          
          let text = await res.text();

          // Pretty Print JSON if applicable
          if (currentFilename.endsWith('.json')) {
              try {
                  const json = JSON.parse(text);
                  text = JSON.stringify(json, null, 2);
              } catch (e) {
                  // Keep original text if parse fails
              }
          }
          
          textContent = text;
      } catch (e) {
          console.error(e);
          hasError = true;
      } finally {
          isLoading = false;
      }
  }

  function handleImageLoad() {
    isLoading = false;
  }

  function handleImageError() {
    isLoading = false;
    hasError = true;
  }

  function nextFile() {
    currentIndex = (currentIndex + 1) % filenames.length;
  }

  function prevFile() {
    currentIndex = (currentIndex - 1 + filenames.length) % filenames.length;
  }
</script>

<div class="artifact-card" transition:fade={{ duration: 200 }}>
  <!-- Header -->
  <div class="card-header">
    <div class="header-left">
      <div class="header-title">
        <span class="icon">▚</span>
        <span>ARTIFACT_DECK</span>
      </div>
      <!-- File Counter -->
      {#if filenames.length > 1}
        <div class="counter-badge">
          {currentIndex + 1} / {filenames.length}
        </div>
      {/if}
    </div>
    
    <div class="meta-tag" title={currentFilename}>{currentFilename}</div>
  </div>

  <!-- Viewport -->
  <div class="card-viewport" class:text-mode={!isImage}>
    
    <!-- NAVIGATION CONTROLS (Overlay) -->
    {#if filenames.length > 1}
      <button class="nav-btn prev" onclick={prevFile} title="Previous Asset">‹</button>
      <button class="nav-btn next" onclick={nextFile} title="Next Asset">›</button>
    {/if}

    <!-- CONTENT RENDERER -->
    {#key currentFilename} 
      
      <!-- 1. LOADING OVERLAY (Independent) -->
      {#if isLoading}
          <div class="state-msg">
            <div class="spinner"></div>
            <span>FETCHING STREAM...</span>
          </div>
      {/if}

      <!-- 2. ERROR STATE -->
      {#if hasError}
          <div class="error-state">
            <span>ERR_LOAD_FAILED // 404</span>
          </div>
      
      <!-- 3. IMAGE RENDERER -->
      {:else if isImage}
          <!-- 
             CRITICAL FIX: The image must be rendered immediately so the browser fetches it.
             We hide it via CSS class until 'isLoading' is false.
          -->
          <img 
            {src} 
            alt="Agent Output" 
            onload={handleImageLoad} 
            onerror={handleImageError}
            class:hidden={isLoading}
          />

      <!-- 4. TEXT RENDERER -->
      {:else if textContent !== null}
          <div class="code-viewer">
            <pre><code>{textContent}</code></pre>
          </div>
      {/if}

    {/key}
  </div>
  
  <!-- Footer / Actions -->
  <div class="card-footer">
    <div class="footer-info">
      {#if filenames.length > 1}
        <span class="nav-hint">USE ARROWS TO NAVIGATE</span>
      {/if}
    </div>
    <a href={src} target="_blank" download={currentFilename} class="action-btn">
      DOWNLOAD [↓]
    </a>
  </div>
</div>

<style>
  .artifact-card {
    margin: 16px 0;
    border: 1px solid var(--paper-line);
    background: var(--paper-bg);
    border-radius: 2px;
    font-family: var(--font-code);
    overflow: hidden;
    max-width: 100%;
    display: flex;
    flex-direction: column;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    position: relative;
  }

  /* HEADER */
  .card-header {
    background: var(--paper-surface);
    border-bottom: 1px solid var(--paper-line);
    padding: 8px 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    height: 32px;
  }

  .header-left { display: flex; align-items: center; gap: 12px; }

  .header-title {
    font-size: 10px; font-weight: 700; color: var(--paper-ink);
    display: flex; align-items: center; gap: 8px; letter-spacing: 1px;
  }

  .counter-badge {
    font-size: 9px; font-weight: 700; color: var(--paper-bg);
    background: var(--paper-ink); padding: 1px 6px; border-radius: 2px;
  }

  .icon { color: var(--arctic-cyan); }

  .meta-tag {
    font-size: 9px; color: var(--paper-line); text-transform: uppercase;
    max-width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  /* VIEWPORT */
  .card-viewport {
    position: relative;
    min-height: 200px;
    max-height: 400px; /* Cap height specifically for code scrolling */
    
    /* Technical Grid Background */
    background-image: 
        linear-gradient(var(--paper-line) 1px, transparent 1px),
        linear-gradient(90deg, var(--paper-line) 1px, transparent 1px);
    background-size: 20px 20px;
    background-position: center;
    background-color: color-mix(in srgb, var(--paper-bg), var(--paper-surface) 50%);
    
    display: flex; justify-content: center; align-items: center;
    overflow: hidden;
  }

  /* Special mode for text to align content top-left and allow scrolling */
  .card-viewport.text-mode {
      display: block; 
      overflow: auto;
      align-items: flex-start;
      justify-content: flex-start;
  }

  img {
    max-width: 100%; max-height: 300px; display: block;
    border: 1px solid var(--paper-line);
    box-shadow: 0 8px 24px rgba(0,0,0,0.1);
    background: white; /* Transparency checkerboard substitute */
    margin: 16px; /* Spacing from edges in flex mode */
  }
  
  img.hidden { opacity: 0; position: absolute; }

  /* CODE / TEXT VIEWER */
  .code-viewer {
      padding: 16px;
      width: 100%;
      height: 100%;
      box-sizing: border-box;
  }

  pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-all;
      font-family: var(--font-code);
      font-size: 11px;
      line-height: 1.5;
      color: var(--paper-ink);
  }

  /* NAVIGATION BUTTONS */
  .nav-btn {
    position: absolute; top: 50%; transform: translateY(-50%);
    width: 32px; height: 32px;
    background: var(--paper-surface); border: 1px solid var(--paper-line);
    color: var(--paper-ink); font-size: 18px; line-height: 1;
    cursor: pointer; z-index: 10;
    display: flex; align-items: center; justify-content: center;
    transition: all 0.2s; opacity: 0.7;
  }
  .nav-btn:hover { opacity: 1; background: var(--paper-ink); color: var(--paper-bg); }
  .nav-btn.prev { left: 8px; }
  .nav-btn.next { right: 8px; }

  /* STATES */
  .state-msg {
    display: flex; flex-direction: column; align-items: center; gap: 8px;
    color: var(--paper-line); font-size: 9px; font-weight: 700; letter-spacing: 1px;
    padding-top: 80px; /* Center generic state vertically roughly */
    position: absolute; /* Overlay on top */
    top: 0; left: 0; width: 100%; height: 100%;
    justify-content: center; padding-top: 0;
    background: color-mix(in srgb, var(--paper-bg), transparent 20%);
    z-index: 5;
  }

  .error-state {
    color: #d32f2f; font-size: 10px; font-weight: 700;
    border: 1px dashed #d32f2f; padding: 8px 16px;
    background: rgba(211, 47, 47, 0.05);
    margin: auto;
  }

  .spinner {
    width: 16px; height: 16px;
    border: 2px solid var(--paper-line); border-top-color: var(--paper-ink);
    border-radius: 50%; animation: spin 1s linear infinite;
  }

  /* FOOTER */
  .card-footer {
    padding: 8px 12px;
    border-top: 1px solid var(--paper-line);
    background: var(--paper-surface);
    display: flex; justify-content: space-between; align-items: center;
  }

  .nav-hint { font-size: 8px; color: var(--paper-line); opacity: 0.8; letter-spacing: 0.5px; }

  .action-btn {
    font-size: 9px; font-weight: 700; color: var(--paper-ink);
    text-decoration: none; padding: 6px 12px;
    border: 1px solid var(--paper-line); background: var(--paper-bg);
    transition: all 0.2s; text-transform: uppercase; letter-spacing: 0.5px;
  }
  .action-btn:hover { border-color: var(--paper-ink); background: var(--paper-ink); color: var(--paper-bg); }

  @keyframes spin { to { transform: rotate(360deg); } }
</style>