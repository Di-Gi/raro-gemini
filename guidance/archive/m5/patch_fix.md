Here is the investigation into the pipeline failure. The breakdown identifies two critical "disconnects" in the architecture that caused the missing artifacts in the Environment Rail and the final crash.

### Root Cause Analysis

1.  **Missing Artifacts in Rail (The "Silent Drop"):**
    *   **Mechanism:** The Python `Agent Service` executes code and generates files. It detects these files correctly (`Captured 1 file(s)...`).
    *   **The Bug:** In `apps/agent-service/src/main.py`, the `_execute_agent_logic` function constructs an `AgentResponse` to send back to the Rust Kernel. **It fails to include `files_generated` in the output payload.**
    *   **Result:** The Rust Kernel receives the success signal but sees *zero* files in the output. Consequently, the `promote_artifact_to_storage` logic in `runtime.rs` (which populates the Rail/Database) is never triggered. The files sit in the temporary session folder but are never indexed.

2.  **Terminal Crash `400 INVALID_ARGUMENT` (The "Duplicate Bomb"):**
    *   **Mechanism:** The `trend_analyzer` agent ran its Python tool multiple times (likely due to internal retries or the multi-step nature of the script). The `llm.py` logic accumulated the file list simply by extending a list: `all_files_generated.extend(files)`.
    *   **The Bug:** The file list contained duplicates (e.g., 3 copies of `combined_stock_data.csv`).
    *   **The Crash:** When the next agent (`report_generator`) started, the Rust Kernel fetched this context. It blindly mounted *every* file in that list to the Gemini context window. Gemini rejected the request because it received multiple inline data parts with the exact same filename/content, or the duplicate images blew the context limits.

---

### Solution Patch

Apply these changes to fix the pipeline.

#### 1. Fix Agent Service (Python) - `apps/agent-service/src/main.py`
We need to pass the generated file list back to the Kernel so it knows what to promote to the Rail.

```python
# Locate the _execute_agent_logic function
# Around line 408 in the provided snippet context

        # ... existing code ...
        # 6. Build Response
        return AgentResponse(
            agent_id=request.agent_id,
            success=True,
            output={
                "result": response_text,
                "artifact_stored": artifact_stored,
                "files_generated": files_generated  # <--- ADD THIS LINE
            },
            delegation=delegation_request,
            input_tokens=result["input_tokens"],
            # ... existing code ...
        )
```

#### 2. Fix LLM Core (Python) - `apps/agent-service/src/core/llm.py`
Prevent duplicate files from being reported if an agent loops or retries.

```python
# Locate call_gemini_with_context function
# Around line 290

        # ... existing code ...
        response = None
        content_text = ""
        # CHANGE: Use a set to prevent duplicates, convert to list at end
        all_files_generated = [] 
        seen_files = set() 
        # ... existing code ...

            # Inside the tool execution loop:
            # ...
            # Execute
            result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)

            duration_ms = (datetime.now() - start_time).total_seconds() * 1000

            # --- FIX START: Capture generated files with deduplication ---
            if isinstance(result_dict, dict) and "files_generated" in result_dict:
                files = result_dict["files_generated"]
                if isinstance(files, list):
                    for f in files:
                        if f not in seen_files:
                            seen_files.add(f)
                            all_files_generated.append(f)
                    logger.debug(f"Captured files from {tool_name}: {files}")
            # --- FIX END ---
```

#### 3. Fix Kernel Runtime (Rust) - `apps/kernel-server/src/runtime.rs`
Ensure that even if bad data comes in, we don't crash the LLM context construction by mounting the same file twice.

```rust
// Locate prepare_invocation_payload function
// Around line 650

                                    // ... existing code ...
                                    // C. Dynamic File Mounting (Manifest Pattern)
                                    if let Some(files_array) = val.get("files_generated").and_then(|v| v.as_array()) {
                                        for file_val in files_array {
                                            if let Some(filename) = file_val.as_str() {
                                                // Construct absolute path to the RFS session output
                                                let mount_path = format!("/app/storage/sessions/{}/output/{}", run_id, filename);
                                                
                                                // === FIX: DEDUPLICATION CHECK ===
                                                if !dynamic_file_mounts.contains(&mount_path) {
                                                    dynamic_file_mounts.push(mount_path);
                                                }
                                            }
                                        }
                                    }
                                    // ... existing code ...
```

#### 4. Fix Docker Compose (Frontend Config)
The frontend `Dockerfile` hardcodes `VITE_USE_MOCK_API=false`, but the `docker-compose` passes it as an ENV var. However, the Frontend logs show `ERR_LOAD_FAILED // 404`.

The frontend is trying to load artifacts immediately via:
`/api/runtime/{runId}/files/{filename}`

If the "Fix 1" above is applied, the artifacts will be promoted to `/api/runtime/artifacts/...`. However, the immediate view in the chat relies on the *session* file serving.

Ensure `apps/kernel-server/src/server/handlers.rs` logic for `serve_session_file` correctly handles the path.
The current code looks correct:
```rust
let file_path = format!("/app/storage/sessions/{}/output/{}", run_id, filename);
```
**Verify:** Check if the filename in `filename` matches the case on disk. The logs show `INTEL_STOCK_DATA.CSV` in the frontend console but `intel_stock_data.csv` in the backend logs.

**Fix Frontend CSS/Logic:**
The frontend forces uppercase in CSS, but `ArtifactCard.svelte` might be receiving uppercase if it's parsing the log message incorrectly or if the backend sent it uppercase.

Check `OutputPane.svelte`:
```javascript
// Function strips tags
function extractImageFilename(msg: string): string | null {
    const sysMatch = msg.match(/\[SYSTEM: Generated .*? saved to '([^']+)'\]/);
    if (sysMatch) return sysMatch[1];
    // ...
}
```
The logs show: `[SYSTEM: Generated Image saved to 'trend_analysis_plots.png']`. This extracts exactly as written (lowercase).

The issue is likely strictly the **missing file generation** signal in Fix 1. Without that, the file might be getting cleaned up or not properly synced from the E2B sandbox to the `output` directory in time for the frontend fetch.

**Final Action:** Apply the 3 code patches above and rebuild. This will restore the `files_generated` payload, trigger the Rail population, and prevent the duplicate file crash.

USER:
secondary notes (instead of deduplication should we just add *-1 etc. so we dont have data loss? given in a retry the first saved artifact could be in error or missing data?)

You raise a valid point regarding data loss during retries. However, in an autonomous agent pipeline, **deduplication (keeping the latest version) is actually safer than renaming (versioning)** for two critical reasons:

1.  **The Agent Contract:** Downstream agents (e.g., `Report Generator`) are prompted to look for specific filenames (e.g., `combined_stock_data.csv`). If a retry loop renames the valid output to `combined_stock_data-1.csv`, the next agent will fail because it can't find the file it expects.
2.  **The Crash Cause:** The Gemini API `400` error wasn't caused by overwriting data; it was caused by **sending the exact same file path twice** in the API request context.

**Recommendation:** We should **overwrite on disk** (to ensure the filename matches the contract) but **deduplicate in the list** (to prevent the API crash). This ensures the agent always gets the *latest, most corrected version* of the file without confusing the LLM with duplicate inputs.

Here are the specific fixes to repair the pipeline, populate the Artifact Rail, and prevent the crash.

### 1. Fix Agent Service (Python)
**File:** `apps/agent-service/src/main.py`

**The Bug:** The `files_generated` list was calculated but never actually added to the final response object sent back to the Kernel. This is why your Environment Rail was empty.

```python
# In function: _execute_agent_logic
# ... (around line 408)

        # 6. Build Response
        return AgentResponse(
            agent_id=request.agent_id,
            success=True,
            output={
                "result": response_text,
                "artifact_stored": artifact_stored,
                # [FIX] Pass the files back so Kernel can index them
                "files_generated": files_generated 
            },
            delegation=delegation_request,
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            # ...
        )
```

### 2. Fix LLM Core (Python)
**File:** `apps/agent-service/src/core/llm.py`

**The Bug:** The `all_files_generated` list was blindly extending on every loop. If an agent retried a tool 3 times, the list contained 3 copies of the filename.

```python
# In function: call_gemini_with_context
# ... (around line 290)

        response = None
        content_text = ""
        
        # [FIX] Use a set for tracking to prevent duplicates in the list
        all_files_generated = []
        _seen_files = set()

        # ... inside the while loop, inside the tool execution block ...

                # Execute
                result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)

                duration_ms = (datetime.now() - start_time).total_seconds() * 1000

                # [FIX] Deduplicate before adding
                if isinstance(result_dict, dict) and "files_generated" in result_dict:
                    files = result_dict["files_generated"]
                    if isinstance(files, list):
                        for f in files:
                            if f not in _seen_files:
                                _seen_files.add(f)
                                all_files_generated.append(f)
                        logger.debug(f"Captured files from {tool_name}: {files}")
```

### 3. Fix Kernel Runtime (Rust)
**File:** `apps/kernel-server/src/runtime.rs`

**The Bug:** The Kernel was not validating if a file path was already mounted before adding it again. This acts as a safety net if the Python service fails to deduplicate.

```rust
// In function: prepare_invocation_payload
// ... (around line 650)

                                    // C. Dynamic File Mounting (Manifest Pattern)
                                    if let Some(files_array) = val.get("files_generated").and_then(|v| v.as_array()) {
                                        for file_val in files_array {
                                            if let Some(filename) = file_val.as_str() {
                                                // Construct absolute path to the RFS session output
                                                let mount_path = format!("/app/storage/sessions/{}/output/{}", run_id, filename);
                                                
                                                // [FIX] Check if path is already in the list before pushing
                                                if !dynamic_file_mounts.contains(&mount_path) {
                                                    dynamic_file_mounts.push(mount_path);
                                                }
                                            }
                                        }
                                    }
```

### 4. Verify Frontend Data
**File:** `apps/web-console/src/components/OutputPane.svelte`

The log extraction logic in the frontend is correct, but relies on the log message containing specific tags. Since we are fixing the backend to properly populate the `Artifact Rail`, the "missing file" error in the frontend chat will resolve itself because the link will point to a valid file on the backend.

**Summary of Impact:**
1.  **Crash Resolved:** Deduplication prevents sending 12 duplicate images to Gemini.
2.  **Artifacts Visible:** Adding `files_generated` to the Python response triggers the Rust `promote_artifact_to_storage` function, making files appear in the Environment Rail.
3.  **Data Integrity:** Overwriting files during retries ensures the final agent always reads the most current, error-free version of the data.