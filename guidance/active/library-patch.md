To align artifact storage with your workspace library pattern, we need to treat the **Library (`/app/storage/library`)** as the "Persistent Truth" and the **Session (`/app/storage/sessions`)** as the "Scratchpad".

Currently, your system works like this:
1.  **Uploads:** Go straight to `Library`.
2.  **Artifacts:** Stay in `Session Output` (and die when the session is cleaned up).

To fix this, we will implement an **"Auto-Promote"** strategy: When an Agent successfully generates a file, the Kernel will immediately copy it to the `Library`. This makes it instantly visible in the `EnvironmentRail` for future runs.

Here is the implementation plan across the Kernel.

### 1. Update `fs_manager.rs`
Add a function to copy a file from a specific run's output directory back to the global library.

**File:** `apps/kernel-server/src/fs_manager.rs`

```rust
// ... imports ...

impl WorkspaceInitializer {
    // ... existing init_run_session ...
    // ... existing save_to_library ...

    /// Promotes a file from a session's output directory to the global library.
    /// This makes the artifact available for future runs and visible in the UI Rail.
    pub async fn promote_artifact_to_library(run_id: &str, filename: &str) -> io::Result<()> {
        // 1. Source: The ephemeral session output
        let src_path = format!("{}/sessions/{}/output/{}", STORAGE_ROOT, run_id, filename);
        
        // 2. Destination: The persistent library
        // Note: The filename from tools.py usually includes a UUID (plot_1234_0.png), 
        // so collision risk is low.
        let dest_path = format!("{}/library/{}", STORAGE_ROOT, filename);

        if Path::new(&src_path).exists() {
            // We use copy, not move, to keep the run integrity valid
            fs::copy(&src_path, &dest_path)?;
            tracing::info!("Promoted artifact to library: {} -> {}", src_path, dest_path);
            Ok(())
        } else {
            Err(io::Error::new(io::ErrorKind::NotFound, "Artifact not found in session output"))
        }
    }

    // ... existing cleanup_run ...
}
```

### 2. Update `runtime.rs`
Modify the execution loop. When the Agent returns `files_generated` metadata (which we added in the previous step), trigger the promotion logic.

**File:** `apps/kernel-server/src/runtime.rs`

```rust
// ... inside execute_dynamic_dag loop ...

                        // Store Artifact
                        let artifact_id = if let Some(output_data) = &res.output {
                            
                            // === NEW: AUTO-PROMOTE ARTIFACTS TO LIBRARY ===
                            // Check if the agent reported generated files
                            if let Some(files_array) = output_data.get("files_generated").and_then(|v| v.as_array()) {
                                for file_val in files_array {
                                    if let Some(filename) = file_val.as_str() {
                                        // Fire and forget promotion (don't crash flow if IO fails)
                                        let rid = run_id.clone();
                                        let fname = filename.to_string();
                                        tokio::spawn(async move {
                                            match fs_manager::WorkspaceInitializer::promote_artifact_to_library(&rid, &fname).await {
                                                Ok(_) => tracing::info!("Artifact '{}' saved to Workspace Library", fname),
                                                Err(e) => tracing::error!("Failed to promote artifact '{}': {}", fname, e),
                                            }
                                        });
                                    }
                                }
                            }
                            // ==============================================

                            let agent_stored_flag = output_data.get("artifact_stored")
// ...
```

### 3. Update `handlers.rs` (Optional but Recommended)
To allow the UI to preview files *directly* from the Library (since they now live there too), add a route or ensure the logic supports it.

Currently, you have:
*   `serve_session_file` -> Serves from `sessions/{run_id}/output`
*   `list_library_files` -> Lists `/library`

You should add a handler to **serve** library files so the user can download them from the rail if they want.

**File:** `apps/kernel-server/src/server/handlers.rs`

```rust
// Add this new function
// GET /runtime/library/files/:filename
pub async fn serve_library_file(
    Path(filename): Path<String>,
) -> Result<impl IntoResponse, StatusCode> {
    // 1. Sanitize
    if filename.contains("..") || filename.starts_with("/") {
        return Err(StatusCode::FORBIDDEN);
    }

    // 2. Path to Global Library
    let file_path = format!("/app/storage/library/{}", filename);
    let path = std::path::Path::new(&file_path);

    // 3. Verify
    if !path.exists() {
        return Err(StatusCode::NOT_FOUND);
    }

    // 4. Stream
    let file = match tokio::fs::File::open(path).await {
        Ok(file) => file,
        Err(_) => return Err(StatusCode::INTERNAL_SERVER_ERROR),
    };

    let stream = ReaderStream::new(file);
    let body = Body::from_stream(stream);

    // Simple mime type guess
    let content_type = if filename.ends_with(".png") { "image/png" }
    else if filename.ends_with(".csv") { "text/csv" }
    else { "application/octet-stream" };

    let headers = [
        ("Content-Type", content_type),
        ("Cache-Control", "public, max-age=3600"),
    ];

    Ok((headers, body))
}
```

**File:** `apps/kernel-server/src/main.rs` (Register the route)

```rust
// ...
    .route("/runtime/library/upload", post(handlers::upload_library_file))
    .route("/runtime/library/files/:filename", get(handlers::serve_library_file)) // <--- Register here
    .route("/runtime/:run_id/files/:filename", get(handlers::serve_session_file))
// ...
```

### Summary of the Flow with these changes:

1.  **Agent Executes:** Python generates `plot_123.png` in `session/output`.
2.  **Metadata:** Agent returns `files_generated: ["plot_123.png"]`.
3.  **Kernel Runtime:**
    *   Reads metadata.
    *   Copies `plot_123.png` from `session/output` -> `library/`.
4.  **UI:**
    *   The `OutputPane` shows the artifact (fetching from session).
    *   The **`EnvironmentRail`** (left sidebar) will update (on next refresh) to show `plot_123.png` alongside your uploaded CSVs.
    *   This effectively "saves" the work automatically.