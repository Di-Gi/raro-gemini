# Artifact Storage & EnvironmentRail Separation Proposal

**Status**: Approved for Implementation
**Date**: 2026-01-09
**Supersedes**: `library-patch.md` (original proposal - never implemented)

---

## Executive Summary

**Finding**: The original `library-patch.md` proposal was **never implemented**. The codebase has no auto-promotion logic for agent-generated artifacts.

**Key Insight**: Mixing user uploads with agent-generated artifacts in one view creates poor UX. They serve different purposes:
- **Library Files**: User-curated, persistent knowledge base (CSVs, PDFs, docs)
- **Agent Artifacts**: Ephemeral outputs from workflow runs (plots, analyses, generated reports)

**Recommendation**: Implement a **dual-storage architecture** with separate UI sections.

---

## Current State Analysis

### 1. Artifact Flow (As Implemented)
```
Agent generates file → E2B Sandbox → tools.py saves to /sessions/{run_id}/output/
                                    ↓
                            metadata: files_generated array
                                    ↓
                            Redis artifact (1-hour TTL)
                                    ↓
                            OutputPane displays it
                                    ↓
                            [SESSION CLEANUP] → File deleted forever
```

**Problem**: Artifacts die with the session. No persistence, no visibility in EnvironmentRail.

### 2. Library Flow (As Implemented)
```
User uploads → /app/storage/library/
             ↓
     EnvironmentRail fetches via GET /runtime/library
             ↓
     Displayed in left rail with attachment toggles
             ↓
     Copied to /sessions/{run_id}/input/ on workflow start
```

**Works well** for user-curated files.

### 3. What's Missing
- **fs_manager.rs**: No `promote_artifact_to_library()` function
- **runtime.rs**: No promotion logic in execution loop (around line 473-483)
- **handlers.rs**: No `serve_library_file()` endpoint
- **EnvironmentRail**: No separate view for artifacts vs library

---

## Problems with Original Patch Approach

The original patch proposed copying agent outputs directly into `/library/`:

**Issues**:
1. **Namespace Collision**: User uploads mixed with agent-generated files
2. **No Context**: Can't tell which agent/run created a file
3. **No Lifecycle Management**: Library is meant for persistent curation, not ephemeral outputs
4. **Clutter**: Successful multi-agent runs could dump dozens of intermediate plots
5. **No Filtering**: Users can't distinguish "my data" from "system artifacts"

---

## New Proposal: Dual-Storage Architecture

### Design Philosophy
> **"Library is Curated Knowledge, Artifacts are Workflow Outputs"**

### Storage Strategy

```
/app/storage/
├── library/              # User uploads (persistent, curated)
│   ├── financials.csv
│   └── policy.pdf
│
├── artifacts/            # Agent outputs (organized by run, auto-expire)
│   ├── {run_id}/
│   │   ├── metadata.json      # {"run_id", "workflow_id", "created_at", "agents"}
│   │   ├── plot_abc_0.png     # from image_analyzer
│   │   ├── analysis.csv       # from data_processor
│   │   └── report.md          # from document_writer
│   │
│   └── {another_run_id}/
│       └── ...
```

### Metadata Schema
Each artifact directory contains `metadata.json`:
```json
{
  "run_id": "f3b2a...",
  "workflow_id": "data_analysis_v2",
  "user_directive": "Analyze Q3 financials and generate report",
  "created_at": "2026-01-09T10:30:00Z",
  "expires_at": "2026-01-16T10:30:00Z",
  "artifacts": [
    {
      "filename": "plot_f3b2a_0.png",
      "agent_id": "visualization_agent",
      "generated_at": "2026-01-09T10:32:15Z",
      "size_bytes": 45120,
      "content_type": "image/png"
    },
    {
      "filename": "insights.json",
      "agent_id": "analysis_agent",
      "generated_at": "2026-01-09T10:31:42Z",
      "size_bytes": 2048,
      "content_type": "application/json"
    }
  ],
  "status": "completed"
}
```

---

## EnvironmentRail UI Redesign

### Proposed Layout (Expanded View)

```
┌─────────────────────────────┐
│ ENVIRONMENT RAIL            │
├─────────────────────────────┤
│                             │
│ [📚 LIBRARY FILES]          │  ← Collapsible Section
│   ○ financials_Q3.csv      │    (IDLE = gray, LINKED = amber)
│   ○ safety_policy.json     │
│   [↑ Upload]  [🔄 Refresh]  │
│                             │
├─────────────────────────────┤
│                             │
│ [🎨 GENERATED ARTIFACTS]    │  ← NEW Section
│   Filter: [All Runs ▼]     │    ← Dropdown: All/Current/Recent
│   Sort: [Date ▼]           │    ← Date/Agent/Type
│                             │
│   Run: f3b2a (2min ago)     │    ← Grouped by run
│   ├─ 📊 plot_f3b2a_0.png   │      Shows agent icon
│   │   by visualization      │      Hover: metadata
│   ├─ 📄 analysis.csv       │
│   │   by data_processor    │
│   └─ 📝 report.md          │
│       by writer            │
│                             │
│   Run: a8d4c (1hr ago)      │
│   ├─ 📊 chart.png          │
│   └─ ...                   │
│                             │
│   [🗑️ Clear Old Runs]       │  ← Batch cleanup
│                             │
└─────────────────────────────┘
```

### Key UI Features
1. **Dual Sections**: Clear visual separation
2. **Contextual Info**: Show which agent/run created each artifact
3. **Filtering**: "Current Run Only" vs "All Runs" vs "Last 24h"
4. **Actions**:
   - **Download**: Individual file download
   - **Promote to Library**: Convert artifact → curated library file
   - **Delete**: Remove specific artifact
   - **View**: Preview (for images/text)
5. **Status Indicators**:
   - 🟢 Current run artifacts (live)
   - 🟡 Recent artifacts (< 24h)
   - 🔴 Expiring soon (< 1 day until auto-delete)

---

## Implementation Plan

### Phase 1: Backend Storage & Auto-Promotion

#### **1.1 Update `fs_manager.rs`**

Add the promotion function with metadata tracking:

```rust
use serde::{Serialize, Deserialize};
use chrono::{DateTime, Utc};

#[derive(Serialize, Deserialize)]
pub struct ArtifactMetadata {
    pub run_id: String,
    pub workflow_id: String,
    pub user_directive: String,
    pub created_at: String,
    pub expires_at: String,
    pub artifacts: Vec<ArtifactFile>,
    pub status: String,
}

#[derive(Serialize, Deserialize)]
pub struct ArtifactFile {
    pub filename: String,
    pub agent_id: String,
    pub generated_at: String,
    pub size_bytes: u64,
    pub content_type: String,
}

impl WorkspaceInitializer {
    /// Promotes agent-generated file from session output to persistent artifacts storage
    pub async fn promote_artifact_to_storage(
        run_id: &str,
        workflow_id: &str,
        agent_id: &str,
        filename: &str,
        user_directive: &str,
    ) -> io::Result<()> {
        // 1. Source: Session output
        let src_path = format!("{}/sessions/{}/output/{}", STORAGE_ROOT, run_id, filename);

        // 2. Destination: Artifacts directory (organized by run)
        let artifacts_dir = format!("{}/artifacts/{}", STORAGE_ROOT, run_id);
        fs::create_dir_all(&artifacts_dir)?;

        let dest_path = format!("{}/{}", artifacts_dir, filename);

        if !Path::new(&src_path).exists() {
            return Err(io::Error::new(io::ErrorKind::NotFound,
                format!("Artifact {} not found in session output", filename)));
        }

        // 3. Copy file (keep session copy for integrity)
        fs::copy(&src_path, &dest_path)?;
        tracing::info!("Promoted artifact: {} → {}", src_path, dest_path);

        // 4. Update/Create Metadata
        let metadata_path = format!("{}/metadata.json", artifacts_dir);
        let mut metadata = if Path::new(&metadata_path).exists() {
            let data = fs::read_to_string(&metadata_path)?;
            serde_json::from_str::<ArtifactMetadata>(&data)
                .unwrap_or_else(|_| Self::create_new_metadata(run_id, workflow_id, user_directive))
        } else {
            Self::create_new_metadata(run_id, workflow_id, user_directive)
        };

        // 5. Add file entry
        let file_meta = fs::metadata(&dest_path)?;
        metadata.artifacts.push(ArtifactFile {
            filename: filename.to_string(),
            agent_id: agent_id.to_string(),
            generated_at: Utc::now().to_rfc3339(),
            size_bytes: file_meta.len(),
            content_type: Self::guess_content_type(filename),
        });

        // 6. Write metadata
        let json = serde_json::to_string_pretty(&metadata)?;
        let mut meta_file = fs::File::create(&metadata_path)?;
        meta_file.write_all(json.as_bytes())?;

        Ok(())
    }

    fn create_new_metadata(run_id: &str, workflow_id: &str, user_directive: &str) -> ArtifactMetadata {
        let now = Utc::now();
        let expires = now + chrono::Duration::days(7); // 7-day retention

        ArtifactMetadata {
            run_id: run_id.to_string(),
            workflow_id: workflow_id.to_string(),
            user_directive: user_directive.to_string(),
            created_at: now.to_rfc3339(),
            expires_at: expires.to_rfc3339(),
            artifacts: Vec::new(),
            status: "active".to_string(),
        }
    }

    fn guess_content_type(filename: &str) -> String {
        if filename.ends_with(".png") { "image/png" }
        else if filename.ends_with(".jpg") { "image/jpeg" }
        else if filename.ends_with(".csv") { "text/csv" }
        else if filename.ends_with(".json") { "application/json" }
        else if filename.ends_with(".md") { "text/markdown" }
        else { "application/octet-stream" }
        .to_string()
    }

    /// List all artifact runs
    pub async fn list_artifact_runs() -> io::Result<Vec<String>> {
        let artifacts_root = format!("{}/artifacts", STORAGE_ROOT);
        if !Path::new(&artifacts_root).exists() {
            return Ok(Vec::new());
        }

        let entries = fs::read_dir(&artifacts_root)?;
        let mut runs = Vec::new();

        for entry in entries {
            if let Ok(entry) = entry {
                if entry.file_type()?.is_dir() {
                    if let Ok(name) = entry.file_name().into_string() {
                        runs.push(name);
                    }
                }
            }
        }

        Ok(runs)
    }

    /// Get metadata for a specific run
    pub async fn get_artifact_metadata(run_id: &str) -> io::Result<ArtifactMetadata> {
        let path = format!("{}/artifacts/{}/metadata.json", STORAGE_ROOT, run_id);
        let data = fs::read_to_string(&path)?;
        serde_json::from_str(&data)
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
    }
}
```

#### **1.2 Update `runtime.rs`**

Hook into the execution loop (around line 472-483):

```rust
// Store Artifact
let artifact_id = if let Some(output_data) = &res.output {

    // === AUTO-PROMOTE TO ARTIFACTS STORAGE ===
    if let Some(files_array) = output_data.get("files_generated").and_then(|v| v.as_array()) {
        let workflow_id = self.runtime_states.get(&run_id)
            .map(|s| s.workflow_id.clone())
            .unwrap_or_default();

        let user_directive = self.workflows.get(&workflow_id)
            .and_then(|w| w.agents.iter().find(|a| a.id == agent_id))
            .map(|a| a.user_directive.clone())
            .unwrap_or_default();

        for file_val in files_array {
            if let Some(filename) = file_val.as_str() {
                let rid = run_id.clone();
                let wid = workflow_id.clone();
                let aid = agent_id.clone();
                let fname = filename.to_string();
                let directive = user_directive.clone();

                // Fire-and-forget promotion
                tokio::spawn(async move {
                    match fs_manager::WorkspaceInitializer::promote_artifact_to_storage(
                        &rid, &wid, &aid, &fname, &directive
                    ).await {
                        Ok(_) => tracing::info!("Artifact '{}' promoted to persistent storage", fname),
                        Err(e) => tracing::error!("Failed to promote artifact '{}': {}", fname, e),
                    }
                });
            }
        }
    }
    // ============================================

    let agent_stored_flag = output_data.get("artifact_stored")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    // ... rest of existing logic
```

#### **1.3 Update `handlers.rs`**

Add new endpoints:

```rust
// GET /runtime/artifacts
pub async fn list_all_artifacts() -> Result<Json<serde_json::Value>, StatusCode> {
    let runs = WorkspaceInitializer::list_artifact_runs()
        .await
        .map_err(|e| {
            tracing::error!("Failed to list artifact runs: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    let mut artifacts = Vec::new();

    for run_id in runs {
        if let Ok(metadata) = WorkspaceInitializer::get_artifact_metadata(&run_id).await {
            artifacts.push(json!({
                "run_id": run_id,
                "metadata": metadata
            }));
        }
    }

    Ok(Json(json!({ "artifacts": artifacts })))
}

// GET /runtime/artifacts/:run_id
pub async fn get_run_artifacts(
    Path(run_id): Path<String>,
) -> Result<Json<ArtifactMetadata>, StatusCode> {
    WorkspaceInitializer::get_artifact_metadata(&run_id)
        .await
        .map(Json)
        .map_err(|_| StatusCode::NOT_FOUND)
}

// GET /runtime/artifacts/:run_id/files/:filename
pub async fn serve_artifact_file(
    Path((run_id, filename)): Path<(String, String)>,
) -> Result<impl IntoResponse, StatusCode> {
    // Sanitize
    if filename.contains("..") || filename.starts_with("/") {
        return Err(StatusCode::FORBIDDEN);
    }

    let file_path = format!("/app/storage/artifacts/{}/{}", run_id, filename);
    let path = std::path::Path::new(&file_path);

    if !path.exists() {
        return Err(StatusCode::NOT_FOUND);
    }

    let file = tokio::fs::File::open(path).await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    let stream = ReaderStream::new(file);
    let body = Body::from_stream(stream);

    let content_type = WorkspaceInitializer::guess_content_type(&filename);
    let headers = [
        ("Content-Type", content_type.as_str()),
        ("Cache-Control", "public, max-age=86400"),
    ];

    Ok((headers, body))
}

// DELETE /runtime/artifacts/:run_id
pub async fn delete_artifact_run(
    Path(run_id): Path<String>,
) -> Result<StatusCode, StatusCode> {
    let path = format!("/app/storage/artifacts/{}", run_id);
    tokio::fs::remove_dir_all(&path)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    tracing::info!("Deleted artifact run: {}", run_id);
    Ok(StatusCode::NO_CONTENT)
}
```

#### **1.4 Register Routes in `main.rs`**

```rust
.route("/runtime/artifacts", get(handlers::list_all_artifacts))
.route("/runtime/artifacts/:run_id", get(handlers::get_run_artifacts))
.route("/runtime/artifacts/:run_id", delete(handlers::delete_artifact_run))
.route("/runtime/artifacts/:run_id/files/:filename", get(handlers::serve_artifact_file))
```

---

### Phase 2: Frontend UI Updates

#### **2.1 Update `api.ts`**

```typescript
export interface ArtifactFile {
  filename: string;
  agent_id: string;
  generated_at: string;
  size_bytes: number;
  content_type: string;
}

export interface ArtifactMetadata {
  run_id: string;
  workflow_id: string;
  user_directive: string;
  created_at: string;
  expires_at: string;
  artifacts: ArtifactFile[];
  status: string;
}

export async function getAllArtifacts(): Promise<ArtifactMetadata[]> {
  const res = await fetch(`${KERNEL_API}/runtime/artifacts`);
  const data = await res.json();
  return data.artifacts.map((a: any) => a.metadata);
}

export async function getRunArtifacts(runId: string): Promise<ArtifactMetadata> {
  const res = await fetch(`${KERNEL_API}/runtime/artifacts/${runId}`);
  return await res.json();
}

export async function deleteArtifactRun(runId: string): Promise<void> {
  await fetch(`${KERNEL_API}/runtime/artifacts/${runId}`, { method: 'DELETE' });
}
```

#### **2.2 Update `EnvironmentRail.svelte`**

```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  import { getLibraryFiles, getAllArtifacts, deleteArtifactRun, type ArtifactMetadata } from '$lib/api';

  let libraryFiles: string[] = [];
  let artifacts: ArtifactMetadata[] = [];
  let artifactFilter: 'all' | 'current' | 'recent' = 'all';
  let isExpanded = false;
  let isLoading = false;

  export let attachedFiles: Set<string>;
  export let currentRunId: string | null = null;

  onMount(async () => {
    await refreshAll();
  });

  async function refreshAll() {
    isLoading = true;
    libraryFiles = await getLibraryFiles();
    artifacts = await getAllArtifacts();
    isLoading = false;
  }

  $: filteredArtifacts = filterArtifacts(artifacts, artifactFilter, currentRunId);

  function filterArtifacts(
    all: ArtifactMetadata[],
    filter: string,
    runId: string | null
  ): ArtifactMetadata[] {
    if (filter === 'current' && runId) {
      return all.filter(a => a.run_id === runId);
    }
    if (filter === 'recent') {
      const oneDayAgo = Date.now() - 24 * 60 * 60 * 1000;
      return all.filter(a => new Date(a.created_at).getTime() > oneDayAgo);
    }
    return all;
  }

  function getTimeAgo(isoDate: string): string {
    const ms = Date.now() - new Date(isoDate).getTime();
    const mins = Math.floor(ms / 60000);
    if (mins < 60) return `${mins}min ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}hr ago`;
    return `${Math.floor(hours / 24)}d ago`;
  }
</script>

<div class="environment-rail" class:expanded={isExpanded}
     on:mouseenter={() => isExpanded = true}
     on:mouseleave={() => isExpanded = false}>

  {#if !isExpanded}
    <!-- Collapsed View -->
    <div class="collapsed-indicator">
      <div class="led" class:active={isLoading}></div>
      <span class="vertical-label">ENV</span>
      <div class="badge">{libraryFiles.length + artifacts.reduce((sum, a) => sum + a.artifacts.length, 0)}</div>
      <span class="vertical-label">IO</span>
    </div>
  {:else}
    <!-- Expanded View -->
    <div class="expanded-panel">

      <!-- Library Section -->
      <section class="file-section">
        <div class="section-header">
          <h3>📚 LIBRARY FILES</h3>
          <button on:click={refreshAll}>🔄</button>
        </div>

        <div class="file-list">
          {#each libraryFiles as file}
            <div class="file-item" class:attached={attachedFiles.has(file)}>
              <span class="status-led" class:linked={attachedFiles.has(file)}></span>
              <span class="filename">{file}</span>
            </div>
          {/each}
        </div>

        <button class="upload-btn">↑ Upload</button>
      </section>

      <div class="divider"></div>

      <!-- Artifacts Section (NEW) -->
      <section class="artifact-section">
        <div class="section-header">
          <h3>🎨 GENERATED ARTIFACTS</h3>
        </div>

        <div class="controls">
          <select bind:value={artifactFilter}>
            <option value="all">All Runs</option>
            <option value="current" disabled={!currentRunId}>Current Run</option>
            <option value="recent">Last 24h</option>
          </select>
        </div>

        <div class="artifact-list">
          {#each filteredArtifacts as run}
            <div class="run-group">
              <div class="run-header">
                <span class="run-id">{run.run_id.slice(0, 6)}...</span>
                <span class="run-time">{getTimeAgo(run.created_at)}</span>
              </div>

              {#each run.artifacts as artifact}
                <div class="artifact-item">
                  <span class="icon">{artifact.content_type.includes('image') ? '📊' : '📄'}</span>
                  <div class="artifact-info">
                    <div class="artifact-name">{artifact.filename}</div>
                    <div class="artifact-meta">by {artifact.agent_id}</div>
                  </div>
                  <button class="download-btn"
                          on:click={() => window.open(`/api/runtime/artifacts/${run.run_id}/files/${artifact.filename}`)}>
                    ⬇
                  </button>
                </div>
              {/each}
            </div>
          {/each}
        </div>

        {#if filteredArtifacts.length === 0}
          <div class="empty-state">No artifacts yet</div>
        {/if}
      </section>

    </div>
  {/if}
</div>

<style>
  /* ... existing styles ... */

  .artifact-section {
    padding: 12px;
    max-height: 400px;
    overflow-y: auto;
  }

  .controls {
    margin-bottom: 8px;
  }

  .controls select {
    width: 100%;
    padding: 6px;
    background: #1a1a1a;
    color: #e0e0e0;
    border: 1px solid #333;
    border-radius: 4px;
  }

  .run-group {
    margin-bottom: 16px;
    border-left: 2px solid #444;
    padding-left: 8px;
  }

  .run-header {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: #888;
    margin-bottom: 6px;
  }

  .artifact-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px;
    background: #1a1a1a;
    border-radius: 4px;
    margin-bottom: 4px;
  }

  .artifact-item:hover {
    background: #252525;
  }

  .artifact-info {
    flex: 1;
    min-width: 0;
  }

  .artifact-name {
    font-size: 12px;
    color: #e0e0e0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .artifact-meta {
    font-size: 10px;
    color: #666;
  }

  .download-btn {
    padding: 4px 8px;
    background: #333;
    border: none;
    cursor: pointer;
    border-radius: 3px;
  }

  .download-btn:hover {
    background: #444;
  }

  .empty-state {
    text-align: center;
    color: #666;
    padding: 24px;
    font-size: 13px;
  }

  .divider {
    height: 1px;
    background: #333;
    margin: 8px 0;
  }
</style>
```

---

## Migration & Cleanup Strategy

### 1. Artifact Expiration (Cron Job)
Add a cleanup service that runs daily:

```rust
// In a separate cron module or background task
pub async fn cleanup_expired_artifacts() -> io::Result<()> {
    let artifacts_root = "/app/storage/artifacts";
    let entries = fs::read_dir(artifacts_root)?;
    let now = Utc::now();

    for entry in entries {
        let entry = entry?;
        let metadata_path = entry.path().join("metadata.json");

        if let Ok(data) = fs::read_to_string(&metadata_path) {
            if let Ok(meta) = serde_json::from_str::<ArtifactMetadata>(&data) {
                let expires = DateTime::parse_from_rfc3339(&meta.expires_at)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or(now);

                if expires < now {
                    fs::remove_dir_all(entry.path())?;
                    tracing::info!("Cleaned up expired artifacts: {}", meta.run_id);
                }
            }
        }
    }

    Ok(())
}
```

### 2. Promotion to Library (Optional Feature)
Allow users to "promote" an artifact to permanent library storage:

```rust
// POST /runtime/artifacts/:run_id/files/:filename/promote
pub async fn promote_artifact_to_library(
    Path((run_id, filename)): Path<(String, String)>,
) -> Result<StatusCode, StatusCode> {
    let src = format!("/app/storage/artifacts/{}/{}", run_id, filename);
    let dst = format!("/app/storage/library/{}", filename);

    tokio::fs::copy(&src, &dst)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    tracing::info!("Promoted artifact {} to library", filename);
    Ok(StatusCode::CREATED)
}
```

Add a "⭐ Save to Library" button in the UI.

---

## Summary

### ✅ What This Achieves

1. **Persistent Artifacts**: Agent outputs survive session cleanup
2. **Clear Separation**: Library (curated) vs Artifacts (generated)
3. **Rich Context**: Know which agent/run created each file
4. **Lifecycle Management**: Auto-expire after 7 days, manual cleanup
5. **Better UX**: Separate sections in EnvironmentRail, filtering, sorting
6. **Scalability**: Organized by run_id, prevents namespace collision

### 📊 Comparison

| Aspect | Current | Original Patch | This Proposal |
|--------|---------|----------------|---------------|
| Artifact Persistence | ❌ Redis only (1hr) | ✅ Library copy | ✅ Dedicated storage |
| User Upload Separation | N/A | ❌ Mixed together | ✅ Separate sections |
| Context/Metadata | ❌ None | ❌ None | ✅ Run/agent tracking |
| UI Organization | ❌ No visibility | ❌ Single list | ✅ Grouped by run |
| Lifecycle Management | ❌ No control | ❌ Manual cleanup | ✅ Auto-expire + manual |
| Namespace Collision | N/A | ⚠️ Possible | ✅ Isolated by run_id |

---

## Implementation Priority

**Recommended Order**:
1. **Phase 1.1-1.4**: Backend storage + auto-promotion + API endpoints
2. **Phase 2.1-2.2**: Frontend UI with dual sections
3. **Optional**: Cleanup cron job + promotion to library feature

**Estimated Effort**:
- Phase 1: Backend implementation
- Phase 2: Frontend UI updates
- Testing & Integration

---

## Design Decisions

### Storage Architecture
- **Selected**: `/app/storage/artifacts/{run_id}/` (organized by run)
- **Rationale**: Clear separation, easy cleanup, prevents collision

### Retention Policy
- **Selected**: 7-day auto-expiration
- **Configurable**: Can be adjusted per deployment or per-workflow in future

### EnvironmentRail Layout
- **Selected**: Two collapsible sections (Library + Artifacts)
- **Rationale**: Clear visual separation, distinct use cases

### Artifact Filtering
- **Included**: "All Runs", "Current Run", "Last 24h"
- **Future**: By agent, by file type, by workflow (if needed)

### Promotion to Library
- **Included**: Optional "promote" feature
- **Use Case**: User wants to convert a generated report into permanent knowledge base

---

## Notes

- The original `library-patch.md` can be marked as superseded/archived
- This proposal maintains backward compatibility with existing library functionality
- Session `/output/` directories continue to work for OutputPane display
- The dual storage approach scales better for multi-agent workflows
- Metadata tracking enables future analytics (most-used agents, file types, etc.)
