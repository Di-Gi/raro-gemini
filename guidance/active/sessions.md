Here is a complete, **State-of-the-Art (SOTA)** implementation guide to add Client-Side Session Scoping to RARO.

This approach uses **Axum Extractors** for clean middleware-like injection in Rust and **Local Storage Persistence** in Svelte. It ensures strict data isolation while maintaining access to shared "Golden Dataset" scenario files.

---

### **Architecture Overview**

1.  **Frontend**: Generates a persistent UUID (`X-RARO-CLIENT-ID`) and attaches it to every API request.
2.  **Kernel (API)**: An Axum Extractor intercepts this header, validating it and passing it to handlers.
3.  **Kernel (FS)**: The FileSystem Manager treats storage as a layered system:
    *   **Read**: `Layer 1 (Private)` -> `Layer 2 (Public/Shared)` -> `404`.
    *   **Write**: Always `Layer 1 (Private)`.

---

### **Phase 1: Frontend Implementation**

We will create a centralized fetch wrapper to ensure the header is never missed.

#### 1. Modify `apps/web-console/src/lib/api.ts`

Replace the top section of your file with this logic.

```typescript
// [[RARO]]/apps/web-console/src/lib/api.ts

// --- 1. SESSION IDENTITY LOGIC ---
const STORAGE_KEY = 'raro_session_id';

function getClientId(): string {
    if (typeof localStorage === 'undefined') return 'cli-mode'; // SSR safety
    let id = localStorage.getItem(STORAGE_KEY);
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem(STORAGE_KEY, id);
        console.log('[RARO] New Session Identity Created:', id);
    }
    return id;
}

const SESSION_ID = getClientId();

// --- 2. AUTHENTICATED FETCH WRAPPER ---
async function secureFetch(url: string, options: RequestInit = {}): Promise<Response> {
    const headers = new Headers(options.headers || {});
    
    // Inject the Session ID
    headers.set('X-RARO-CLIENT-ID', SESSION_ID);
    
    // Ensure JSON content type if not set (convenience)
    if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
        headers.set('Content-Type', 'application/json');
    }

    return fetch(url, { ...options, headers });
}

// ... EXISTING IMPORTS & CONSTANTS ...
const KERNEL_API = import.meta.env.VITE_KERNEL_URL || '/api';
const AGENT_API = import.meta.env.VITE_AGENT_URL || '/agent-api';
export const USE_MOCK = import.meta.env.VITE_USE_MOCK_API === 'true';

// ... (WorkflowConfig interfaces remain the same) ...

// --- 3. UPDATE API CALLS TO USE secureFetch ---

export async function startRun(config: WorkflowConfig): Promise<{ success: boolean; run_id: string }> {
  if (USE_MOCK) return mockStartRun(config);

  try {
    // UPDATED: Use secureFetch
    const res = await secureFetch(`${KERNEL_API}/runtime/start`, {
      method: 'POST',
      body: JSON.stringify(config), // Headers handled by wrapper
    });

    if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
    return await res.json();
  } catch (e) {
    console.error('Failed to start run:', e);
    throw e;
  }
}

// Update getLibraryFiles
export async function getLibraryFiles(): Promise<string[]> {
    if (USE_MOCK) return mockGetLibraryFiles();

    try {
        const res = await secureFetch(`${KERNEL_API}/runtime/library`); // UPDATED
        if (!res.ok) throw new Error('Failed to fetch library');
        const data = await res.json();
        return data.files || [];
    } catch (e) {
        console.error('Library fetch failed:', e);
        return [];
    }
}

// Update uploadFile (Special case: FormData)
export async function uploadFile(file: File): Promise<string> {
    if (USE_MOCK) { /* ... */ return "success"; }

    const formData = new FormData();
    formData.append('file', file);

    try {
        // secureFetch handles the auth header, but we let browser set Content-Type for FormData
        const res = await secureFetch(`${KERNEL_API}/runtime/library/upload`, {
            method: 'POST',
            body: formData, 
        });

        if (!res.ok) throw new Error(`Upload failed: ${res.statusText}`);
        return "success";
    } catch (e) {
        console.error('Upload API Error:', e);
        throw e;
    }
}

// ... (Update getAllArtifacts, deleteArtifactRun, promoteArtifactToLibrary similarly) ...
```

---

### **Phase 2: Kernel Infrastructure (Rust)**

We will implement a clean "Extractor" pattern to handle the identity parsing.

#### 1. Create `apps/kernel-server/src/security.rs`

Create a new file to handle the extractor logic.

```rust
// apps/kernel-server/src/security.rs
use axum::{
    async_trait,
    extract::FromRequestParts,
    http::{request::Parts, StatusCode},
};

pub struct ClientSession(pub String);

#[async_trait]
impl<S> FromRequestParts<S> for ClientSession
where
    S: Send + Sync,
{
    type Rejection = StatusCode;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        // Extract header
        let client_id = parts
            .headers
            .get("X-RARO-CLIENT-ID")
            .and_then(|h| h.to_str().ok())
            .unwrap_or("public"); // Default to public/anon if missing (e.g. Health checks)

        // Basic Sanitization (Alphanumeric + dashes only) to prevent directory traversal attacks
        if !client_id.chars().all(|c| c.is_alphanumeric() || c == '-') {
            tracing::warn!("Invalid Client ID rejected: {}", client_id);
            return Err(StatusCode::BAD_REQUEST);
        }

        Ok(ClientSession(client_id.to_string()))
    }
}
```

#### 2. Register Module in `main.rs`

```rust
// apps/kernel-server/src/main.rs
mod security; // Add this line
// ... existing mods ...
```

---

### **Phase 3: File System Logic (Layered Access)**

We update the FS Manager to look in specific folders based on the session ID.

#### 1. Update `apps/kernel-server/src/fs_manager.rs`

```rust
use std::path::{Path, PathBuf}; // Use PathBuf for safety
// ... imports ...

const STORAGE_ROOT: &str = "/app/storage";

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
    // Updated signature to accept client_id
    pub fn init_run_session(run_id: &str, library_files: Vec<String>, client_id: &str) -> io::Result<()> {
        let session_path = format!("{}/sessions/{}", STORAGE_ROOT, run_id);
        let input_path = format!("{}/input", session_path);
        let output_path = format!("{}/output", session_path);

        fs::create_dir_all(&input_path)?;
        fs::create_dir_all(&output_path)?;
        
        tracing::info!("Initializing workspace for Client: {}", client_id);

        for filename in library_files {
            let dest = format!("{}/{}", input_path, filename);
            
            // Use the layered resolver
            if let Some(src_path) = Self::resolve_library_path(client_id, &filename) {
                match fs::copy(&src_path, &dest) {
                    Ok(_) => tracing::info!("Attached {:?} to run {}", src_path, run_id),
                    Err(e) => tracing::error!("Failed to copy {}: {}", filename, e),
                }
            } else {
                tracing::warn!("File '{}' not found in Private or Public library.", filename);
            }
        }

        Ok(())
    }

    // === 3. SCOPED UPLOAD ===
    pub async fn save_to_library(client_id: &str, filename: &str, data: &[u8]) -> io::Result<()> {
        let safe_name = Path::new(filename).file_name()
            .ok_or(io::Error::new(io::ErrorKind::InvalidInput, "Invalid filename"))?
            .to_string_lossy();

        if safe_name.contains("..") { return Err(io::Error::new(io::ErrorKind::PermissionDenied, "Invalid path")); }

        // Save SPECIFICALLY to the client's folder
        let user_lib_path = format!("{}/library/{}", STORAGE_ROOT, client_id);
        fs::create_dir_all(&user_lib_path)?;

        let target_path = format!("{}/{}", user_lib_path, safe_name);
        let mut file = fs::File::create(&target_path)?;
        file.write_all(data)?;
        
        tracing::info!("File uploaded to private scope: {}", target_path);
        Ok(())
    }

    // === 4. LISTING ===
    pub async fn list_scoped_files(client_id: &str) -> io::Result<Vec<String>> {
        let mut file_set = std::collections::HashSet::new();

        // Helper to read a dir and insert into set
        let mut read_dir = |path: String| {
            if let Ok(entries) = fs::read_dir(path) {
                for entry in entries.flatten() {
                    if let Ok(name) = entry.file_name().into_string() {
                        if !name.starts_with('.') { file_set.insert(name); }
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
    
    // ... Existing artifact methods ...
}
```

---

### **Phase 4: Update Handlers & Runtime**

Connect the extractor to the logic.

#### 1. Update `apps/kernel-server/src/server/handlers.rs`

```rust
use crate::security::ClientSession; // Import extractor

// GET /runtime/library
pub async fn list_library_files(
    ClientSession(client_id): ClientSession // <--- Auto-extracted
) -> Result<Json<serde_json::Value>, StatusCode> {
    
    let files = WorkspaceInitializer::list_scoped_files(&client_id)
        .await
        .map_err(|e| {
            tracing::error!("Failed to list files: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    Ok(Json(serde_json::json!({ "files": files })))
}

// POST /runtime/library/upload
pub async fn upload_library_file(
    ClientSession(client_id): ClientSession, // <--- Auto-extracted
    mut multipart: Multipart
) -> Result<Json<serde_json::Value>, StatusCode> {
    
    while let Some(field) = multipart.next_field().await.map_err(|_| StatusCode::BAD_REQUEST)? {
        let name = field.file_name().unwrap_or("unknown").to_string();
        let data = field.bytes().await.map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

        // Pass client_id to save function
        WorkspaceInitializer::save_to_library(&client_id, &name, &data)
            .await
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    }

    Ok(Json(serde_json::json!({ "success": true })))
}

// POST /runtime/start
pub async fn start_workflow(
    State(runtime): State<Arc<RARORuntime>>,
    ClientSession(client_id): ClientSession, // <--- Capture who is starting the run
    Json(config): Json<WorkflowConfig>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    
    // Pass client_id to runtime
    match runtime.start_workflow(config, &client_id) {
        Ok(run_id) => Ok(Json(json!({ "success": true, "run_id": run_id }))),
        Err(e) => Err(StatusCode::BAD_REQUEST)
    }
}
```

#### 2. Update `apps/kernel-server/src/runtime.rs`

Update the `start_workflow` signature to propagate the Client ID to the workspace initializer.

```rust
// In impl RARORuntime
pub fn start_workflow(self: &Arc<Self>, config: WorkflowConfig, client_id: &str) -> Result<String, String> {
    // ... existing DAG validation ...

    let run_id = Uuid::new_v4().to_string();

    // === UPDATED: RFS INITIALIZATION ===
    if let Err(e) = fs_manager::WorkspaceInitializer::init_run_session(
        &run_id, 
        config.attached_files.clone(),
        client_id // <--- PASS DOWN
    ) {
         tracing::error!("Workspace init failed: {}", e);
         return Err(format!("FS Error: {}", e));
    }

    // ... rest of function ...
}
```

---

### **Phase 5: Docker & Seed Data**

We ensure the `public` folder exists and contains the Hackathon scenario files.

#### 1. Create a Seed Script: `apps/kernel-server/scripts/entrypoint.sh`

```bash
#!/bin/bash
set -e

# 1. Create Public Directory
mkdir -p /app/storage/library/public

# 2. Seed Files (If they don't exist)
# In a real build, you might copy these from the source code into the image
if [ ! -f "/app/storage/library/public/legacy_script.py" ]; then
    echo "print('Seeding Legacy Script...')" > /app/storage/library/public/legacy_script.py
fi

if [ ! -f "/app/storage/library/public/financials.csv" ]; then
    echo "id,amount,variance\n1,500,0.2" > /app/storage/library/public/financials.csv
fi

# 3. Start Application
exec "$@"
```

#### 2. Update `apps/kernel-server/Dockerfile`

```dockerfile
# ... build stages ...

FROM debian:bookworm-slim

# ... installs ...

# Copy script
COPY scripts/entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh

# ... copy binary ...

ENTRYPOINT ["entrypoint.sh"]
CMD ["raro-kernel"]
```

---

### **Verification**

1.  **Open Browser A (Incognito):**
    *   Upload `secret_plans.txt`.
    *   It appears in the list.
    *   Verify on disk: It is in `/app/storage/library/<UUID-A>/`.

2.  **Open Browser B (Normal):**
    *   File list shows only `legacy_script.py` (Public).
    *   `secret_plans.txt` is **not visible**.
    *   Upload `dog_photos.zip`.
    *   Verify on disk: It is in `/app/storage/library/<UUID-B>/`.

3.  **Run Workflow:**
    *   Select `legacy_script.py` in Browser A.
    *   Agent starts.
    *   Kernel successfully copies from `/app/storage/library/public/` to the session.

You now have a multi-tenant-capable system suitable for a robust Hackathon demo without the pain of user management.