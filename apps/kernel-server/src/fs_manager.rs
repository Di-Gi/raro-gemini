// [[RARO]]/apps/kernel-server/src/fs_manager.rs
// Purpose: Manages file system operations for RFS (Raro File System).
// Architecture: Infrastructure Helper Layer.
// Dependencies: std::fs, std::path

use std::fs;
use std::path::Path;
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
    /// Initializes a new session workspace for a given run_id.
    /// Creates directory structure and copies requested files from the library.
    pub fn init_run_session(run_id: &str, library_files: Vec<String>) -> io::Result<()> {
        let session_path = format!("{}/sessions/{}", STORAGE_ROOT, run_id);
        let input_path = format!("{}/input", session_path);
        let output_path = format!("{}/output", session_path);

        // 1. Create Directories (Idempotent)
        fs::create_dir_all(&input_path)?;
        fs::create_dir_all(&output_path)?;
        
        tracing::info!("Created workspace for run {}: {}", run_id, session_path);

        // 2. Copy requested files from Library -> Session Input
        for filename in library_files {
            let src = format!("{}/library/{}", STORAGE_ROOT, filename);
            let dest = format!("{}/{}", input_path, filename);
            
            if Path::new(&src).exists() {
                // We copy to ensure the run is an isolated snapshot
                // Changes in session don't affect library
                match fs::copy(&src, &dest) {
                    Ok(_) => tracing::info!("Attached file {} to run {}", filename, run_id),
                    Err(e) => tracing::error!("Failed to copy {}: {}", filename, e),
                }
            } else {
                tracing::warn!("Requested file {} not found in library", filename);
                // We log warning but don't fail the run; agent might handle missing file gracefully
            }
        }

        Ok(())
    }
    
    /// Securely saves a byte buffer to the Library folder.
    pub async fn save_to_library(filename: &str, data: &[u8]) -> io::Result<()> {
        // 1. Sanitize Filename (Basic)
        let safe_name = Path::new(filename).file_name()
            .ok_or(io::Error::new(io::ErrorKind::InvalidInput, "Invalid filename"))?
            .to_string_lossy();

        if safe_name.contains("..") || safe_name.starts_with("/") {
             return Err(io::Error::new(io::ErrorKind::PermissionDenied, "Invalid path"));
        }

        // 2. Ensure Library Dir Exists
        let lib_path = format!("{}/library", STORAGE_ROOT);
        fs::create_dir_all(&lib_path)?;

        // 3. Write File
        let target_path = format!("{}/{}", lib_path, safe_name);
        let mut file = fs::File::create(&target_path)?;
        file.write_all(data)?;
        
        tracing::info!("Uploaded file to library: {}", target_path);
        Ok(())
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

    /// Get metadata for a specific run's artifacts
    pub async fn get_artifact_metadata(run_id: &str) -> io::Result<ArtifactMetadata> {
        let path = format!("{}/artifacts/{}/metadata.json", STORAGE_ROOT, run_id);
        let data = fs::read_to_string(&path)?;
        serde_json::from_str(&data)
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
    }
}