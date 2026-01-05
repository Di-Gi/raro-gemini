// [[RARO]]/apps/kernel-server/src/fs_manager.rs
// Purpose: Manages file system operations for RFS (Raro File System).
// Architecture: Infrastructure Helper Layer.
// Dependencies: std::fs, std::path

use std::fs;
use std::path::Path;
use std::io;
use std::io::Write; 

// Hard anchor to prevent escaping the storage volume
const STORAGE_ROOT: &str = "/app/storage";

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

    /// Optional: Cleanup routine for old sessions
    pub fn cleanup_run(run_id: &str) -> io::Result<()> {
        let path = format!("{}/sessions/{}", STORAGE_ROOT, run_id);
        if Path::new(&path).exists() {
             fs::remove_dir_all(path)?;
             tracing::info!("Cleaned up workspace for run {}", run_id);
        }
        Ok(())
    }
}