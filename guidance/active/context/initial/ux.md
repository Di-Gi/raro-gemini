This creates a symmetrical "Cockpit" layout. The **Settings Rail** controls the *System Reality* (Right), and the **Environment Rail** controls the *System Context* (Left).

Here is the implementation guide.

### Part 1: Kernel Updates (Rust)

We need an endpoint to list existing files in the library so the UI can populate the "Environment" panel on load.

#### 1. Update `src/server/handlers.rs`

Add a handler to list files in the library directory.

```rust
// [[RARO]]/apps/kernel-server/src/server/handlers.rs

// ... imports ...
use tokio::fs;

// GET /runtime/library
pub async fn list_library_files() -> Result<Json<serde_json::Value>, StatusCode> {
    let path = "/app/storage/library";
    let mut entries = fs::read_dir(path).await.map_err(|e| {
        tracing::error!("Failed to read library dir: {}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    let mut files = Vec::new();

    while let Ok(Some(entry)) = entries.next_entry().await {
        if let Ok(file_type) = entry.file_type().await {
            if file_type.is_file() {
                if let Ok(name) = entry.file_name().into_string() {
                    files.push(name);
                }
            }
        }
    }

    Ok(Json(serde_json::json!({
        "files": files
    })))
}
```

#### 2. Register Route in `src/main.rs`

```rust
// [[RARO]]/apps/kernel-server/src/main.rs

let app = Router::new()
    // ... existing ...
    .route("/runtime/upload", post(handlers::upload_file))
    .route("/runtime/library", get(handlers::list_library_files)) // <--- NEW
    // ...
```

---

### Part 2: Frontend Stores & API

We need to track the "Global Library" vs. "Active Session Attachments".

#### 1. Update `src/lib/api.ts`

```typescript
// [[RARO]]/apps/web-console/src/lib/api.ts

export async function getLibraryFiles(): Promise<string[]> {
    if (USE_MOCK) {
        return ['financials_2024.pdf', 'raw_data.csv', 'system_architecture.md'];
    }

    try {
        const res = await fetch(`${KERNEL_API}/runtime/library`);
        if (!res.ok) throw new Error('Failed to fetch library');
        const data = await res.json();
        return data.files;
    } catch (e) {
        console.error(e);
        return [];
    }
}
```

#### 2. Update `src/lib/stores.ts`

Add stores to manage the environment state.

```typescript
// [[RARO]]/apps/web-console/src/lib/stores.ts

// The list of all files available in /storage/library
export const libraryFiles = writable<string[]>([]);

// The subset of files currently linked to the active directive
export const attachedFiles = writable<string[]>([]);

// Helper to toggle attachment status
export function toggleAttachment(fileName: string) {
    attachedFiles.update(files => {
        if (files.includes(fileName)) {
            return files.filter(f => f !== fileName);
        } else {
            return [...files, fileName];
        }
    });
}
```

---

### Part 3: The Environment Rail Component

Create **`src/components/EnvironmentRail.svelte`**. This mirrors the SettingsRail but sits on the left.

```svelte
<!-- [[RARO]]/apps/web-console/src/components/EnvironmentRail.svelte -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { libraryFiles, attachedFiles, toggleAttachment, addLog } from '$lib/stores';
  import { uploadFile, getLibraryFiles } from '$lib/api';
  import Spinner from './sub/Spinner.svelte';

  let hovered = $state(false);
  let isUploading = $state(false);
  let fileInput: HTMLInputElement;

  // Initial Load
  onMount(async () => {
    const files = await getLibraryFiles();
    libraryFiles.set(files);
  });

  async function handleUpload(e: Event) {
    const input = e.target as HTMLInputElement;
    if (!input.files?.length) return;

    isUploading = true;
    try {
        const file = input.files[0];
        const serverName = await uploadFile(file);
        
        // Update Library Store
        libraryFiles.update(current => [...current, serverName]);
        
        // Auto-Link newly uploaded file
        toggleAttachment(serverName);
        
        addLog('SYSTEM', `Ingested: ${serverName}`, 'IO_OK');
    } catch (err) {
        addLog('SYSTEM', 'Upload Failed', 'IO_ERR');
    } finally {
        isUploading = false;
        input.value = '';
    }
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
            {#if isUploading}<Spinner />{/if}
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
                <div class="empty-state">NO_DATA</div>
            {/if}
        </div>

        <div class="actions">
            <input 
                type="file" 
                bind:this={fileInput} 
                style="display:none" 
                onchange={handleUpload} 
            />
            <button class="btn-upload" onclick={() => fileInput.click()} disabled={isUploading}>
                {#if isUploading}INGESTING...{:else}[+] UPLOAD{/if}
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

  .btn-upload {
    width: 100%; background: var(--paper-ink); color: var(--paper-bg);
    border: none; padding: 8px; font-family: var(--font-code);
    font-size: 10px; font-weight: 700; cursor: pointer; letter-spacing: 1px;
  }
  .btn-upload:hover { opacity: 0.9; }
  .btn-upload:disabled { opacity: 0.5; cursor: wait; }
</style>
```

---

### Part 4: Layout Integration

#### 1. Update `App.svelte`
Add the `EnvironmentRail` to the left side of the workspace.

```svelte
<!-- [[RARO]]/apps/web-console/src/App.svelte -->
<script lang="ts">
  import EnvironmentRail from '$components/EnvironmentRail.svelte';
  // ... existing imports
</script>

<main class="mode-{$themeStore.toLowerCase()}">
    <!-- ... -->
    {#if appState === 'HERO'}
      <Hero onenter={enterConsole} />
    {:else}
      <div class="workspace" in:fade={{ duration: 800, delay: 200 }}>
        
        <!-- NEW: Left Rail -->
        <EnvironmentRail />

        <div id="chassis" class={expanded ? 'expanded' : ''}>
          <!-- ... -->
        </div>

        <!-- Existing: Right Rail -->
        <SettingsRail />
        
      </div>
    {/if}
</main>
```

#### 2. Update `ControlDeck.svelte`
We no longer need the upload logic here. We just read from the store when submitting.

```svelte
<!-- [[RARO]]/apps/web-console/src/components/ControlDeck.svelte -->
<script lang="ts">
  import { attachedFiles } from '$lib/stores'; // Import store

  // REMOVE: handleFileSelect, isUploading, fileInput, removeFile
  // REMOVE: AttachmentChip import and usage

  async function submitRun() {
    // ... logic ...
    const config: WorkflowConfig = {
        // ...
        // READ DIRECTLY FROM STORE
        attached_files: $attachedFiles 
    };
    // ...
  }
</script>

<!-- Remove the file-tray div and btn-attach from the template -->
```

### Final UX Flow

1.  **Ingestion:** User hovers over Left Rail ("ENV").
2.  **Upload:** Clicks `[+] UPLOAD`, selects `financials_Q3.pdf`.
3.  **State:** The file appears in the list. It auto-highlights (Linked).
4.  **Context:** The user hovers away. The rail collapses. A small amber light on the rail indicates active context.
5.  **Execution:** User types "Summarize the Q3 risks" in the center console and hits Enter.
6.  **Backend:** `attached_files` array is populated with `['financials_Q3.pdf']`. Kernel mounts it. Agent reads it.