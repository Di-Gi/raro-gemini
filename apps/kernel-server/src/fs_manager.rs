// [[RARO]]/apps/kernel-server/src/fs_manager.rs
// Purpose: Manages file system operations for RFS (Raro File System).
// Architecture: Infrastructure Helper Layer.
// Dependencies: std::fs, std::path

use std::fs;
use std::path::{Path, PathBuf};
use std::io;
use std::io::Write;
use serde::{Serialize, Deserialize};
use chrono::Utc; 

// Hard anchor to prevent escaping the storage volume
const STORAGE_ROOT: &str = "/app/storage";

/// Metadata for artifact storage - tracks all files generated during a workflow run
#[derive(Serialize, Deserialize, Clone)]
pub struct ArtifactMetadata {
    pub run_id: String,
    pub workflow_id: String,
    pub user_directive: String,
    pub created_at: String,
    pub expires_at: String,
    pub artifacts: Vec<ArtifactFile>,
    pub status: String,
}

/// Individual file metadata within an artifact collection
#[derive(Serialize, Deserialize, Clone)]
pub struct ArtifactFile {
    pub filename: String,
    pub agent_id: String,
    pub generated_at: String,
    pub size_bytes: u64,
    pub content_type: String,
}

pub struct WorkspaceInitializer;

impl WorkspaceInitializer {
    // === 1. LAYERED PATH RESOLUTION ===
    /// Resolves a filename by checking Private Storage first, then Public Library.
    fn resolve_library_path(client_id: &str, filename: &str) -> Option<PathBuf> {
        // Sanitize input
        let safe_name = Path::new(filename).file_name()?;

        // Path A: User Private Storage
        let private_path = PathBuf::from(format!("{}/library/{}/{}", STORAGE_ROOT, client_id, safe_name.to_string_lossy()));
        if private_path.exists() {
            return Some(private_path);
        }

        // Path B: Public Shared Storage
        let public_path = PathBuf::from(format!("{}/library/public/{}", STORAGE_ROOT, safe_name.to_string_lossy()));
        if public_path.exists() {
            return Some(public_path);
        }

        None
    }

    // === 2. INITIALIZE SESSION ===
    /// Initializes a new session workspace for a given run_id.
    /// Creates directory structure and copies requested files from the library.
    /// Updated signature to accept client_id for scoped file resolution.
    pub fn init_run_session(run_id: &str, library_files: Vec<String>, client_id: &str) -> io::Result<()> {
        let session_path = format!("{}/sessions/{}", STORAGE_ROOT, run_id);
        let input_path = format!("{}/input", session_path);
        let output_path = format!("{}/output", session_path);

        // 1. Create Directories (Idempotent)
        fs::create_dir_all(&input_path)?;
        fs::create_dir_all(&output_path)?;

        tracing::info!("Initializing workspace for run {} (Client: {})", run_id, client_id);

        // 2. Copy requested files from Library -> Session Input using layered resolver
        for filename in library_files {
            let dest = format!("{}/{}", input_path, filename);

            // Use the layered resolver
            if let Some(src_path) = Self::resolve_library_path(client_id, &filename) {
                match fs::copy(&src_path, &dest) {
                    Ok(_) => tracing::info!("Attached {:?} to run {}", src_path, run_id),
                    Err(e) => tracing::error!("Failed to copy {}: {}", filename, e),
                }
            } else {
                tracing::warn!("File '{}' not found in Private or Public library for client {}", filename, client_id);
            }
        }

        Ok(())
    }
    
    // === 3. SCOPED UPLOAD ===
    /// Securely saves a byte buffer to the client-scoped Library folder.
    pub async fn save_to_library(client_id: &str, filename: &str, data: &[u8]) -> io::Result<()> {
        let safe_name = Path::new(filename).file_name()
            .ok_or(io::Error::new(io::ErrorKind::InvalidInput, "Invalid filename"))?
            .to_string_lossy();

        if safe_name.contains("..") {
            return Err(io::Error::new(io::ErrorKind::PermissionDenied, "Invalid path"));
        }

        // Save SPECIFICALLY to the client's folder
        let user_lib_path = format!("{}/library/{}", STORAGE_ROOT, client_id);
        fs::create_dir_all(&user_lib_path)?;

        let target_path = format!("{}/{}", user_lib_path, safe_name);
        let mut file = fs::File::create(&target_path)?;
        file.write_all(data)?;

        tracing::info!("File uploaded to private scope ({}): {}", client_id, target_path);
        Ok(())
    }

    // === 4. LISTING ===
    /// Lists all files accessible to a client (private + public merged)
    pub async fn list_scoped_files(client_id: &str) -> io::Result<Vec<String>> {
        let mut file_set = std::collections::HashSet::new();

        // Helper to read a dir and insert into set
        let mut read_dir = |path: String| {
            if let Ok(entries) = fs::read_dir(path) {
                for entry in entries.flatten() {
                    if let Ok(name) = entry.file_name().into_string() {
                        if !name.starts_with('.') {
                            file_set.insert(name);
                        }
                    }
                }
            }
        };

        // 1. Read Public
        read_dir(format!("{}/library/public", STORAGE_ROOT));

        // 2. Read Private (overwrites duplicates in set, effectively merging)
        read_dir(format!("{}/library/{}", STORAGE_ROOT, client_id));

        let mut files: Vec<String> = file_set.into_iter().collect();
        files.sort();
        Ok(files)
    }

    /// Optional: Cleanup routine for old sessions (commented until used)
    // pub fn cleanup_run(run_id: &str) -> io::Result<()> {
    //     let path = format!("{}/sessions/{}", STORAGE_ROOT, run_id);
    //     if Path::new(&path).exists() {
    //          fs::remove_dir_all(path)?;
    //          tracing::info!("Cleaned up workspace for run {}", run_id);
    //     }
    //     Ok(())
    // }

    /// Promotes agent-generated file from session output to persistent artifacts storage
    pub async fn promote_artifact_to_storage(
        client_id: &str,
        run_id: &str,
        workflow_id: &str,
        agent_id: &str,
        filename: &str,
        user_directive: &str,
    ) -> io::Result<()> {
        // 1. Source: Session output
        let src_path = format!("{}/sessions/{}/output/{}", STORAGE_ROOT, run_id, filename);

        // 2. Destination: Artifacts directory (organized by client and run)
        let artifacts_dir = format!("{}/artifacts/{}/{}", STORAGE_ROOT, client_id, run_id);
        fs::create_dir_all(&artifacts_dir)?;

        let dest_path = format!("{}/{}", artifacts_dir, filename);

        if !Path::new(&src_path).exists() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("Artifact {} not found in session output", filename)
            ));
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

    /// Creates new artifact metadata for a workflow run
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

    /// Guesses MIME type from file extension
    fn guess_content_type(filename: &str) -> String {
        if filename.ends_with(".png") { "image/png" }
        else if filename.ends_with(".jpg") || filename.ends_with(".jpeg") { "image/jpeg" }
        else if filename.ends_with(".csv") { "text/csv" }
        else if filename.ends_with(".json") { "application/json" }
        else if filename.ends_with(".md") { "text/markdown" }
        else if filename.ends_with(".txt") { "text/plain" }
        else { "application/octet-stream" }
        .to_string()
    }

    /// List all artifact runs for a specific client
    pub async fn list_artifact_runs(client_id: &str) -> io::Result<Vec<String>> {
        let artifacts_root = format!("{}/artifacts/{}", STORAGE_ROOT, client_id);
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

    /// Get metadata for a specific run's artifacts
    pub async fn get_artifact_metadata(client_id: &str, run_id: &str) -> io::Result<ArtifactMetadata> {
        let path = format!("{}/artifacts/{}/{}/metadata.json", STORAGE_ROOT, client_id, run_id);
        let data = fs::read_to_string(&path)?;
        serde_json::from_str(&data)
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
    }
}