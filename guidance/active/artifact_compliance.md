To extend user-scoping to **Artifacts**, we must transition from a global artifact store (`/app/storage/artifacts/{run_id}`) to a multi-tenant structure (`/app/storage/artifacts/{client_id}/{run_id}`).

This ensures that even if someone guesses a `run_id`, they cannot access the artifacts unless they provide the correct `client_id` session key.

### Phase 1: Update Domain Models
The Kernel must "remember" which user owns which run.

**File:** `apps/kernel-server/src/models.rs`
Add `client_id` to the `RuntimeState` so it persists in Redis and is available during background promotion tasks.

```rust
// In models.rs -> RuntimeState struct
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeState {
    pub run_id: String,
    pub workflow_id: String,
    pub client_id: String, // <--- ADD THIS
    pub status: RuntimeStatus,
    // ... rest of fields
}
```

---

### Phase 2: Refactor `fs_manager.rs`
We need to update the path resolution logic to include the `client_id`.

**File:** `apps/kernel-server/src/fs_manager.rs`
Update all artifact-related functions to accept `client_id`.

```rust
impl WorkspaceInitializer {
    // Update promotion logic to use nested client folders
    pub async fn promote_artifact_to_storage(
        client_id: &str, // <--- ADDED
        run_id: &str,
        workflow_id: &str,
        agent_id: &str,
        filename: &str,
        user_directive: &str,
    ) -> io::Result<()> {
        let src_path = format!("{}/sessions/{}/output/{}", STORAGE_ROOT, run_id, filename);
        
        // NEW PATH: artifacts/{client_id}/{run_id}
        let artifacts_dir = format!("{}/artifacts/{}/{}", STORAGE_ROOT, client_id, run_id);
        fs::create_dir_all(&artifacts_dir)?;

        let dest_path = format!("{}/{}", artifacts_dir, filename);
        if !Path::new(&src_path).exists() { return Err(io::ErrorKind::NotFound.into()); }

        fs::copy(&src_path, &dest_path)?;

        // Update metadata storage path
        let metadata_path = format!("{}/metadata.json", artifacts_dir);
        // ... (rest of logic remains same, just uses the new metadata_path)
    }

    pub async fn list_artifact_runs(client_id: &str) -> io::Result<Vec<String>> {
        let path = format!("{}/artifacts/{}", STORAGE_ROOT, client_id);
        if !Path::new(&path).exists() { return Ok(Vec::new()); }
        // ... list directories in the client-specific folder
    }

    pub async fn get_artifact_metadata(client_id: &str, run_id: &str) -> io::Result<ArtifactMetadata> {
        let path = format!("{}/artifacts/{}/{}/metadata.json", STORAGE_ROOT, client_id, run_id);
        let data = fs::read_to_string(&path)?;
        serde_json::from_str::<ArtifactMetadata>(&data).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
    }
}
```

---

### Phase 3: Update Orchestration Logic
When an agent finishes, the Kernel spawns a background task to promote files. This task now needs the `client_id` from the runtime state.

**File:** `apps/kernel-server/src/runtime.rs`

```rust
// Inside start_workflow method
let state = RuntimeState {
    run_id: run_id.clone(),
    workflow_id: workflow_id.clone(),
    client_id: client_id.to_string(), // <--- Store the owner
    status: RuntimeStatus::Running,
    // ...
};

// Inside the execute_dynamic_dag loop, where promotion happens:
if let Some(files_array) = output_data.get("files_generated").and_then(|v| v.as_array()) {
    // Get client_id from state
    let client_id = self.runtime_states.get(&run_id).map(|s| s.client_id.clone()).unwrap_or_default();
    
    for file_val in files_array {
        if let Some(filename) = file_val.as_str() {
            let cid = client_id.clone(); // <--- Capture for move
            let rid = run_id.clone();
            // ... capture other vars
            tokio::spawn(async move {
                let _ = fs_manager::WorkspaceInitializer::promote_artifact_to_storage(
                    &cid, &rid, &wid, &aid, &fname, &directive
                ).await;
            });
        }
    }
}
```

---

### Phase 4: Secure the API Handlers
Finally, update the HTTP endpoints to enforce the `client_id` check.

**File:** `apps/kernel-server/src/server/handlers.rs`

```rust
/// GET /runtime/artifacts
pub async fn list_all_artifacts(
    ClientSession(client_id): ClientSession // <--- Use the session key
) -> Result<Json<serde_json::Value>, StatusCode> {
    let runs = WorkspaceInitializer::list_artifact_runs(&client_id).await.map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let mut artifacts = Vec::new();

    for run_id in runs {
        // Only fetch metadata for this client's runs
        if let Ok(metadata) = WorkspaceInitializer::get_artifact_metadata(&client_id, &run_id).await {
            artifacts.push(json!({ "run_id": run_id, "metadata": metadata }));
        }
    }
    Ok(Json(json!({ "artifacts": artifacts })))
}

/// GET /runtime/artifacts/:run_id/files/:filename
pub async fn serve_artifact_file(
    ClientSession(client_id): ClientSession, // <--- Enforce session
    Path((run_id, filename)): Path<(String, String)>,
) -> Result<impl IntoResponse, StatusCode> {
    // Sanitize...
    
    // Construct scoped path
    let file_path = format!("/app/storage/artifacts/{}/{}/{}", client_id, run_id, filename);
    let path = std::path::Path::new(&file_path);

    if !path.exists() { return Err(StatusCode::NOT_FOUND); }
    
    // ... rest of streaming logic
}
```

---

### Phase 5: Verification & Cleanup
1.  **Identity Contract:** The `web-console` already generates a `raro_session_id` in `api.ts` and sends it as `X-RARO-CLIENT-ID`. No changes are needed on the frontend as long as the header is present.
2.  **Migration Note:** Any artifacts generated *before* this patch will be in `/app/storage/artifacts/{run_id}` and will appear "lost" to the UI. To fix this, manually move them to `/app/storage/artifacts/public/{run_id}` if you wish them to be visible to anonymous users.
3.  **Security Win:** Users can now only delete or promote artifacts belonging to their own session ID, preventing cross-tenant data leakage.

### Summary of Changes
| Component | Change Description |
| :--- | :--- |
| **Model** | Added `client_id` to `RuntimeState`. |
| **Storage** | Changed path to `artifacts/{client_id}/{run_id}/`. |
| **Runtime** | Passed `client_id` to background promotion tasks. |
| **Handlers** | Injected `ClientSession` extractor into all `/artifacts` routes. |
| **Security** | Hardened `serve_artifact_file` to prevent cross-user file access. |