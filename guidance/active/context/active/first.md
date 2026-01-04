# Part 1: RFS Verification Proposal

### 1. Architectural Objective
To implement a **Hybrid File System** that segregates persistent user assets ("The Library") from ephemeral execution environments ("The Sandbox"). This ensures that agents can manipulate files safely without risking the integrity of the master data or the host system.

### 2. The RFS Standard
We define two distinct storage scopes mounted to `/app/storage` within the Docker containers:

| Scope | Path | Persistence | Access Level |
| :--- | :--- | :--- | :--- |
| **Vault** | `/app/storage/library` | **High** | **User:** R/W (via UI)<br>**Kernel:** R/O (Copy source)<br>**Agents:** No Access |
| **Sandbox** | `/app/storage/sessions/{run_id}` | **Ephemeral** | **User:** R/O (Artifact view)<br>**Kernel:** Admin<br>**Agents:** R/W (Scoped) |

### 3. Workflow Logic
1.  **Ingestion:** User uploads file -> Kernel saves to `library/`.
2.  **Context:** User links file to Directive -> UI sends `attached_files` list to Kernel.
3.  **Initialization:** Kernel generates `run_id`, creates `sessions/{run_id}/input`, and copies linked files from `library/`.
4.  **Execution:** Agent receives `run_id`. Tool calls (`read_file`, `write_file`) are logically anchored to `sessions/{run_id}/`.
5.  **Security:** Path traversal (`../`) is strictly blocked by the `WorkspaceManager` in Python.

### 4. Verification Checklist (Before Implementation)
*   [ ] **Volume Mounts:** Can both `kernel` and `agents` containers see the same host directory?
*   [ ] **Permissions:** Does the container user (UID 1000 or root) have write access to the host `storage/` folder?
*   [ ] **Latency:** Is the copying of files from Library to Session efficient enough for the expected file size (MBs vs GBs)?
*   [ ] **Cleanup Strategy:** Do we have a cron job or Kernel trigger to delete old `sessions/` to prevent disk bloat?

---

# Part 2: Implementation Guidelines

Follow this execution order to minimize dependency errors.

## Phase 1: Infrastructure & Safety
**Target:** Root Directory & Docker

1.  **File System Prep (Host Machine)**
    ```bash
    mkdir -p storage/library
    mkdir -p storage/sessions
    chmod -R 777 storage # Local dev only; ensures container write access
    ```

2.  **Git Safety**
    Update `.gitignore` to prevent committing user data.
    ```text
    # RFS
    storage/library/*
    !storage/library/.keep
    storage/sessions/*
    !storage/sessions/.keep
    ```

3.  **Docker Volume Mounting**
    Update `docker-compose.yml`. Both services need the exact same mount point.
    ```yaml
    services:
      kernel:
        volumes:
          - ./storage:/app/storage
      agents:
        volumes:
          - ./storage:/app/storage
    ```

---

## Phase 2: Agent Service (Python)
**Target:** `apps/agent-service/`
**Goal:** Make the agent "Workspace Aware".

1.  **Update Data Model**
    Modify `src/domain/protocol.py` to accept `run_id`.
    ```python
    class AgentRequest(BaseModel):
        # ... existing ...
        run_id: str = Field(..., description="The sandbox session ID")
        # ... existing ...
    ```

2.  **Implement Workspace Logic**
    Update `src/intelligence/tools.py`. This is the security layer.
    *   **Root Anchor:** Hardcode `RFS_BASE = "/app/storage/sessions"`.
    *   **Path Validation:** Ensure `os.path.commonpath` or strictly `os.path.basename` is used to prevent `../` attacks.
    *   **Logic:**
        *   `read_file(filename)`: Look in `{run_id}/output` first, then `{run_id}/input`.
        *   `write_file(filename)`: Always write to `{run_id}/output`.
    *   **Tool Definitions:** Update `get_tool_declarations` to include `list_files`.

3.  **Propagate Context**
    *   Update `src/core/llm.py` -> `call_gemini_with_context`: Add `run_id` argument. Pass it to `execute_tool_call(..., run_id=run_id)`.
    *   Update `src/main.py` -> `_execute_agent_logic`: Extract `run_id` from request and pass to LLM.

---

## Phase 3: Kernel Service (Rust)
**Target:** `apps/kernel-server/`
**Goal:** Manage the "Physical" environment.

1.  **Create File System Manager**
    Create `src/fs_manager.rs`.
    *   Implement `init_run_session(run_id, attached_files)`.
    *   Logic: `fs::create_dir_all` for input/output folders.
    *   Logic: Iterate `attached_files`, verify existence in `library/`, `fs::copy` to `sessions/{run_id}/input/`.

2.  **Update Runtime Initialization**
    In `src/runtime.rs` -> `start_workflow`:
    *   Call `fs_manager::init_run_session` **before** spawning the execution task.
    *   If FS init fails, the run should fail immediately.

3.  **Library API**
    In `src/server/handlers.rs`:
    *   Add `list_library_files` handler to read `/app/storage/library`.
    *   Register route `GET /runtime/library` in `main.rs`.

---

## Phase 4: Web Console (Svelte)
**Target:** `apps/web-console/`
**Goal:** The "Environment Rail" UX.

1.  **API & Stores**
    *   `src/lib/api.ts`: Add `getLibraryFiles()` fetching from `/runtime/library`.
    *   `src/lib/stores.ts`: Add `libraryFiles` (available) and `attachedFiles` (selected) stores.

2.  **Environment Rail Component**
    Create `src/components/EnvironmentRail.svelte`.
    *   **Left Sidebar**: mirrors `SettingsRail`.
    *   **Upload Button**: `POST` to Kernel upload endpoint (ensure Kernel supports this or use manual placement for MVP).
    *   **File List**: Iterate `$libraryFiles`. Click toggles presence in `$attachedFiles`.
    *   **Visuals**: Amber LED when file is "Linked" (in `$attachedFiles`).

3.  **Layout Integration**
    *   `App.svelte`: Add `<EnvironmentRail />` to the left of the workspace.
    *   `ControlDeck.svelte`: When clicking "RUN", read `$attachedFiles` store and inject into the `WorkflowConfig` payload sent to Kernel.

### Implementation Sequence Summary
1.  **Docker Config** (Volume mapping) -> *Restart Containers*.
2.  **Kernel API** (List files) -> *Verify with Curl*.
3.  **Agent Logic** (Tool security) -> *Unit Test*.
4.  **Frontend** (Rail & Linking) -> *Visual Check*.
5.  **Kernel Runtime** (Copy logic) -> *End-to-End Test*.