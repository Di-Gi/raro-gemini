# PROJECT DESIGN SPECIFICATION

## Project Identity
**Project Name:** RARO File System (RFS) Integration
**Core Purpose:** Implement a secure, hybrid file system architecture that bridges persistent user assets ("The Library") with ephemeral agent execution environments ("The Sandbox").
**Vision Statement:** To transform RARO from a text-based reasoning engine into a full-fledged Operating System where agents can securely read, analyze, and generate files within a visually immersive "Cockpit" interface.

## System Overview
**Primary Function:** Manages the lifecycle of files from user ingestion (Library) to active workflow usage (Session Sandbox).
**Key Innovation:** **"Contextual Sandboxing"** — Agents execute in isolated sub-directories based on the `run_id`, preventing data pollution and security risks, while the UI provides a "Cockpit" experience with symmetric control rails (Settings Right, Environment Left).
**Target Users:** Operators needing to perform file-based tasks (financial analysis, code generation, report synthesis) using the RARO multi-agent swarm.

## Architectural Foundation
**Core Components:**
- **Docker Infrastructure:** Shared volumes mapping host `storage/` to `/app/storage` in both Kernel and Agent containers.
- **Agent Service (Intelligence):** "Workspace Aware" tools (`tools.py`) that strictly scope I/O operations to `storage/sessions/{run_id}`.
- **Kernel Service (Orchestration):** A new `fs_manager` module responsible for initializing session environments and copying requested files from the Library to the Sandbox before execution begins.
- **Web Console (Interface):** A new **Environment Rail** component for visualizing the Library, managing attachments, and uploading files.

**System Flow:**
1.  **Ingestion:** User uploads file → Kernel saves to `storage/library`.
2.  **Configuration:** User toggles "Link" on file in UI → UI updates `attached_files` in store.
3.  **Initialization:** User executes Run → Kernel generates `run_id`, creates `storage/sessions/{run_id}/input`, and copies linked files there.
4.  **Execution:** Kernel invokes Agent with `run_id`.
5.  **Operation:** Agent calls `read_file("data.txt")`. `WorkspaceManager` resolves this to `storage/sessions/{run_id}/input/data.txt`.
6.  **Output:** Agent calls `write_file("report.md")`. `WorkspaceManager` writes to `storage/sessions/{run_id}/output/report.md`.

## Technical Requirements
**Functional Requirements:**
- **Session Isolation:** Every workflow run must have a unique, isolated directory.
- **Path Security:** Agents must be strictly prevented from accessing files outside their session folder (Path Traversal Protection).
- **Context Propagation:** The `run_id` must be propagated from the API request down to the tool execution layer.
- **Visual Feedback:** The UI must visually indicate which files are "linked" to the current context.

**Non-Functional Requirements:**
- **Performance:** File copying (Library -> Session) must add negligible latency (<100ms for typical text files).
- **Reliability:** File IO errors (missing files, permissions) must return clear error messages to the Agent, not crash the service.
- **Security:** `../` traversal attempts by the LLM must be caught and sanitized.
- **Usability:** The "Environment Rail" must follow the existing industrial UX aesthetic (collapsible, milled background, status LEDs).

## Implementation Strategy
**Component Build Priority:**
1.  **Infrastructure (Level 0):** Update `docker-compose.yml` and `.gitignore` to establish the physical storage layer.
2.  **Agent Service (Level 1):** Update `protocol.py` (Data Model) and `tools.py` (Security Logic) to handle `run_id` and sandboxing.
3.  **Kernel Service (Level 2):** Implement `fs_manager.rs` for session hydration and update `handlers.rs` to expose Library contents.
4.  **Web Console (Level 3):** Implement `stores.ts` logic and the `EnvironmentRail.svelte` component.

**Core Implementation Features:**
- Shared Docker Volume `storage/`.
- `WorkspaceManager` class in Python.
- `init_run_session` function in Rust.
- `GET /runtime/library` endpoint.
- Left-side Navigation Rail in Svelte.

**Advanced Features (Future/Phase 4):**
- **Artifact Preview:** Allow users to click generated files in the UI to preview content.
- **Garbage Collection:** Kernel background task to clean up old session folders after X hours.
- **Pattern Matching on IO:** "Cortex" rules to prevent agents from writing dangerous file types (e.g., `.exe`, `.sh`).

**Technology Stack:**
- **Backend:** Rust (Axum, Tokio), Python (FastAPI, Google GenAI SDK).
- **Frontend:** Svelte 5 (Runes), TypeScript, CSS Variables.
- **Infra:** Docker Compose.