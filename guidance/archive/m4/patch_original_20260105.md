redis    | 2026-01-05 20:28:12.944 | 1:C 06 Jan 2026 01:28:12.943 * oO0OoO0OoO0Oo Redis is starting oO0OoO0OoO0Oo
redis    | 2026-01-05 20:28:12.944 | 1:C 06 Jan 2026 01:28:12.943 * Redis version=7.4.7, bits=64, commit=00000000, modified=0, pid=1, just started
redis    | 2026-01-05 20:28:12.944 | 1:C 06 Jan 2026 01:28:12.943 # Warning: no config file specified, using the default config. In order to specify a config file use redis-server /path/to/redis.conf
redis    | 2026-01-05 20:28:12.944 | 1:M 06 Jan 2026 01:28:12.944 * monotonic clock: POSIX clock_gettime
redis    | 2026-01-05 20:28:12.947 | 1:M 06 Jan 2026 01:28:12.946 * Running mode=standalone, port=6379.
redis    | 2026-01-05 20:28:12.959 | 1:M 06 Jan 2026 01:28:12.959 * Server initialized
redis    | 2026-01-05 20:28:12.959 | 1:M 06 Jan 2026 01:28:12.959 * Ready to accept connections tcp
postgres | 2026-01-05 20:28:13.012 | 
postgres | 2026-01-05 20:28:13.012 | PostgreSQL Database directory appears to contain a database; Skipping initialization
postgres | 2026-01-05 20:28:13.012 | 
postgres | 2026-01-05 20:28:13.052 | 2026-01-06 01:28:13.052 UTC [1] LOG:  starting PostgreSQL 16.11 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit
postgres | 2026-01-05 20:28:13.053 | 2026-01-06 01:28:13.053 UTC [1] LOG:  listening on IPv4 address "0.0.0.0", port 5432
postgres | 2026-01-05 20:28:13.064 | 2026-01-06 01:28:13.064 UTC [1] LOG:  listening on IPv6 address "::", port 5432
postgres | 2026-01-05 20:28:13.075 | 2026-01-06 01:28:13.074 UTC [1] LOG:  listening on Unix socket "/var/run/postgresql/.s.PGSQL.5432"
postgres | 2026-01-05 20:28:13.082 | 2026-01-06 01:28:13.082 UTC [29] LOG:  database system was shut down at 2026-01-06 01:27:53 UTC
postgres | 2026-01-05 20:28:13.090 | 2026-01-06 01:28:13.090 UTC [1] LOG:  database system is ready to accept connections
kernel   | 2026-01-05 20:28:25.113 | 2026-01-06T01:28:25.112992Z  INFO raro_kernel: Initializing RARO Kernel...
kernel   | 2026-01-05 20:28:25.113 | 2026-01-06T01:28:25.113065Z  INFO raro_kernel::runtime: Redis client initialized: redis://redis:6379
kernel   | 2026-01-05 20:28:25.160 | 2026-01-06T01:28:25.160177Z  WARN raro_kernel::registry: Pattern file not found at 'config/cortex_patterns.json'. Loading fallback defaults.
kernel   | 2026-01-05 20:28:25.160 | 2026-01-06T01:28:25.160214Z  INFO raro_kernel::registry: Registering Safety Pattern: [guard_fs_delete] Prevent File Deletion (Fallback)
kernel   | 2026-01-05 20:28:25.160 | 2026-01-06T01:28:25.160231Z  INFO raro_kernel::runtime: Attempting to rehydrate state from Redis...
kernel   | 2026-01-05 20:28:25.162 | 2026-01-06T01:28:25.162037Z  INFO raro_kernel::runtime: Found 0 active runs in persistence layer.
kernel   | 2026-01-05 20:28:25.162 | 2026-01-06T01:28:25.162199Z  INFO raro_kernel: Cortex Pattern Engine started
kernel   | 2026-01-05 20:28:25.163 | 2026-01-06T01:28:25.163042Z  INFO raro_kernel: RARO Kernel Server listening on http://0.0.0.0:3000
agents   | 2026-01-05 20:28:25.602 | 2026-01-06 01:28:25,602 - raro.agent - INFO - Connected to Redis at redis://redis:6379
agents   | 2026-01-05 20:28:25.912 | INFO:     Will watch for changes in these directories: ['/app']
agents   | 2026-01-05 20:28:25.913 | INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
agents   | 2026-01-05 20:28:25.913 | INFO:     Started reloader process [1] using StatReload
agents   | 2026-01-05 20:28:27.097 | 2026-01-06 01:28:27,096 - raro.agent - INFO - Connected to Redis at redis://redis:6379
agents   | 2026-01-05 20:28:27.282 | INFO:     Started server process [8]
agents   | 2026-01-05 20:28:27.282 | INFO:     Waiting for application startup.
agents   | 2026-01-05 20:28:27.282 | INFO:     Application startup complete.
web      | 2026-01-05 20:28:31.488 | 
web      | 2026-01-05 20:28:31.488 | > raro-web-console@0.1.0 dev
web      | 2026-01-05 20:28:31.488 | > vite --host 0.0.0.0
web      | 2026-01-05 20:28:31.488 | 
web      | 2026-01-05 20:28:32.227 | Forced re-optimization of dependencies
web      | 2026-01-05 20:28:32.256 | 
web      | 2026-01-05 20:28:32.256 |   VITE v5.4.21  ready in 723 ms
web      | 2026-01-05 20:28:32.256 | 
web      | 2026-01-05 20:28:32.256 |   ➜  Local:   http://localhost:5173/
web      | 2026-01-05 20:28:32.256 |   ➜  Network: http://172.19.0.6:5173/
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:209:8 Visible, non-interactive elements with a click event must be accompanied by a keyboard event handler. Consider whether an interactive element such as `<button type="button">` or `<a>` might be more appropriate
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_click_events_have_key_events
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:209:8 `<div>` with a click handler must have an ARIA role
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_no_static_element_interactions
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:211:8 Visible, non-interactive elements with a click event must be accompanied by a keyboard event handler. Consider whether an interactive element such as `<button type="button">` or `<a>` might be more appropriate
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_click_events_have_key_events
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:211:8 `<div>` with a click handler must have an ARIA role
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_no_static_element_interactions
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:212:8 Visible, non-interactive elements with a click event must be accompanied by a keyboard event handler. Consider whether an interactive element such as `<button type="button">` or `<a>` might be more appropriate
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_click_events_have_key_events
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:212:8 `<div>` with a click handler must have an ARIA role
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_no_static_element_interactions
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:213:8 Visible, non-interactive elements with a click event must be accompanied by a keyboard event handler. Consider whether an interactive element such as `<button type="button">` or `<a>` might be more appropriate
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_click_events_have_key_events
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:213:8 `<div>` with a click handler must have an ARIA role
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_no_static_element_interactions
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:306:12 A form label must be associated with a control
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_label_has_associated_control
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:310:12 A form label must be associated with a control
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_label_has_associated_control
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:319:10 A form label must be associated with a control
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_label_has_associated_control
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:331:16 A form label must be associated with a control
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_label_has_associated_control
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:355:34 A form label must be associated with a control
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_label_has_associated_control
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:356:34 A form label must be associated with a control
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_label_has_associated_control
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:357:34 A form label must be associated with a control
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_label_has_associated_control
web      | 2026-01-05 20:28:38.645 | 1:28:38 AM [vite-plugin-svelte] src/components/ControlDeck.svelte:359:12 A form label must be associated with a control
web      | 2026-01-05 20:28:38.645 | https://svelte.dev/e/a11y_label_has_associated_control
agents   | 2026-01-05 20:29:14.595 | 2026-01-06 01:29:14,595 - raro.agent - INFO - Generating workflow plan for query: have the first agent output fictional data, the se...
agents   | 2026-01-05 20:29:14.596 | 2026-01-06 01:29:14,595 - google_genai.models - INFO - AFC is enabled with max remote calls: 10.
agents   | 2026-01-05 20:29:16.207 | 2026-01-06 01:29:16,206 - httpx - INFO - HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent "HTTP/1.1 200 OK"
agents   | 2026-01-05 20:29:16.208 | INFO:     172.19.0.6:45686 - "POST /plan HTTP/1.1" 200 OK
kernel   | 2026-01-05 20:29:18.624 | 2026-01-06T01:29:18.624051Z  INFO raro_kernel::fs_manager: Created workspace for run 28b93f16-d3e2-4730-8850-d87204fceb72: /app/storage/sessions/28b93f16-d3e2-4730-8850-d87204fceb72
kernel   | 2026-01-05 20:29:18.628 | 2026-01-06T01:29:18.628110Z  INFO raro_kernel::runtime: Starting DYNAMIC DAG execution for run_id: 28b93f16-d3e2-4730-8850-d87204fceb72
kernel   | 2026-01-05 20:29:18.628 | 2026-01-06T01:29:18.628165Z  INFO raro_kernel::runtime: Processing agent: fictional_data_generator
kernel   | 2026-01-05 20:29:18.629 | 2026-01-06T01:29:18.629701Z DEBUG raro_kernel::runtime: Sending invocation request to: http://agents:8000/invoke
agents   | 2026-01-05 20:29:20.315 | 2026-01-06 01:29:20,315 - httpx - INFO - HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
agents   | 2026-01-05 20:29:20.317 | 2026-01-06 01:29:20,316 - raro.agent - INFO - Agent fictional_data_generator [Turn 1] RAW CANDIDATE:
agents   | 2026-01-05 20:29:20.317 | content=Content(
agents   | 2026-01-05 20:29:20.317 |   parts=[
agents   | 2026-01-05 20:29:20.317 |     Part(
agents   | 2026-01-05 20:29:20.317 |       text="""```json
agents   | 2026-01-05 20:29:20.317 | [
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 1,
agents   | 2026-01-05 20:29:20.317 |     "y": 5
agents   | 2026-01-05 20:29:20.317 |   },
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 2,
agents   | 2026-01-05 20:29:20.317 |     "y": 7
agents   | 2026-01-05 20:29:20.317 |   },
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 3,
agents   | 2026-01-05 20:29:20.317 |     "y": 6
agents   | 2026-01-05 20:29:20.317 |   },
agents   | 2026-01-05 20:29:20.317 | INFO:     172.19.0.5:42608 - "POST /invoke HTTP/1.1" 200 OK
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 4,
agents   | 2026-01-05 20:29:20.317 |     "y": 8
agents   | 2026-01-05 20:29:20.317 |   },
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 5,
agents   | 2026-01-05 20:29:20.317 |     "y": 10
agents   | 2026-01-05 20:29:20.317 |   },
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 6,
agents   | 2026-01-05 20:29:20.317 |     "y": 9
agents   | 2026-01-05 20:29:20.317 |   },
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 7,
agents   | 2026-01-05 20:29:20.317 |     "y": 12
agents   | 2026-01-05 20:29:20.317 |   },
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 8,
agents   | 2026-01-05 20:29:20.317 |     "y": 11
agents   | 2026-01-05 20:29:20.317 |   },
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 9,
agents   | 2026-01-05 20:29:20.317 |     "y": 13
agents   | 2026-01-05 20:29:20.317 |   },
agents   | 2026-01-05 20:29:20.317 |   {
agents   | 2026-01-05 20:29:20.317 |     "x": 10,
agents   | 2026-01-05 20:29:20.317 |     "y": 15
agents   | 2026-01-05 20:29:20.317 |   }
agents   | 2026-01-05 20:29:20.317 | ]
agents   | 2026-01-05 20:29:20.317 | ```"""
agents   | 2026-01-05 20:29:20.317 |     ),
agents   | 2026-01-05 20:29:20.317 |   ],
agents   | 2026-01-05 20:29:20.317 |   role='model'
agents   | 2026-01-05 20:29:20.317 | ) citation_metadata=None finish_message=None token_count=None finish_reason=<FinishReason.STOP: 'STOP'> avg_logprobs=None grounding_metadata=None index=0 logprobs_result=None safety_ratings=None url_context_metadata=None
kernel   | 2026-01-05 20:29:20.319 | 2026-01-06T01:29:20.319209Z  INFO raro_kernel::runtime: Processing agent: graph_generator
kernel   | 2026-01-05 20:29:20.320 | 2026-01-06T01:29:20.320772Z DEBUG raro_kernel::runtime: Sending invocation request to: http://agents:8000/invoke
kernel   | 2026-01-05 20:29:20.763 | 2026-01-06T01:29:20.762941Z DEBUG raro_kernel::server::handlers: Fetching artifact for run=28b93f16-d3e2-4730-8850-d87204fceb72, agent=fictional_data_generator
agents   | 2026-01-05 20:29:22.972 | 2026-01-06 01:29:22,972 - httpx - INFO - HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
agents   | 2026-01-05 20:29:22.974 | INFO:     172.19.0.5:42608 - "POST /invoke HTTP/1.1" 200 OK
agents   | 2026-01-05 20:29:22.974 | 2026-01-06 01:29:22,973 - raro.agent - INFO - Agent graph_generator [Turn 1] RAW CANDIDATE:
agents   | 2026-01-05 20:29:22.974 | content=None citation_metadata=None finish_message=None token_count=None finish_reason=<FinishReason.UNEXPECTED_TOOL_CALL: 'UNEXPECTED_TOOL_CALL'> avg_logprobs=None grounding_metadata=None index=0 logprobs_result=None safety_ratings=None url_context_metadata=None
agents   | 2026-01-05 20:29:22.974 | 2026-01-06 01:29:22,973 - raro.agent - INFO - Agent graph_generator: Received UNEXPECTED_TOOL_CALL with Empty Content.
kernel   | 2026-01-05 20:29:23.018 | 2026-01-06T01:29:23.017878Z DEBUG raro_kernel::runtime: Stored artifact: run:28b93f16-d3e2-4730-8850-d87204fceb72:agent:graph_generator:output
kernel   | 2026-01-05 20:29:23.020 | 2026-01-06T01:29:23.020148Z  INFO raro_kernel::runtime: Workflow run 28b93f16-d3e2-4730-8850-d87204fceb72 completed successfully (Dynamic)
kernel   | 2026-01-05 20:29:23.186 | 2026-01-06T01:29:23.186710Z  INFO raro_kernel::server::handlers: Run 28b93f16-d3e2-4730-8850-d87204fceb72 reached terminal state: Completed. Closing stream.
kernel   | 2026-01-05 20:29:23.200 | 2026-01-06T01:29:23.200678Z DEBUG raro_kernel::server::handlers: Fetching artifact for run=28b93f16-d3e2-4730-8850-d87204fceb72, agent=graph_generator
postgres | 2026-01-05 20:33:13.170 | 2026-01-06 01:33:13.170 UTC [27] LOG:  checkpoint starting: time
postgres | 2026-01-05 20:33:13.187 | 2026-01-06 01:33:13.186 UTC [27] LOG:  checkpoint complete: wrote 3 buffers (0.0%); 0 WAL file(s) added, 0 removed, 0 recycled; write=0.004 s, sync=0.002 s, total=0.018 s; sync files=2, longest=0.001 s, average=0.001 s; distance=0 kB, estimate=0 kB; lsn=0/197F758, redo lsn=0/197F720

===
SOLUTION:
The root cause of `UNEXPECTED_TOOL_CALL` is a conflict between the Architect's strict planning (which assigns specific tools to agents) and the Agent's runtime behavior (which often hallucinates or validly attempts to use tools it wasn't assigned). Additionally, the configuration for `automatic_function_calling` in the `google-genai` SDK is being passed incorrectly for the `generate_content` method, confusing the API gateway.

Here are the three necessary changes.

### 1. Kernel-Server: Force Global Tool Access
We need to override the Architect's restrictions. Even if the Architect didn't assign `execute_python` to a node, the node should have access to it if it decides it needs it.

**File:** `apps/kernel-server/src/runtime.rs`
**Function:** `prepare_invocation_payload`

```rust
// ... existing code ...

        // 10. Construct File Paths List
        let mut full_file_paths: Vec<String> = workflow.attached_files.iter()
            .map(|f| format!("/app/storage/sessions/{}/input/{}", run_id, f))
            .collect();
            
        // Append files generated by parent agents (Output Dir)
        if !dynamic_file_mounts.is_empty() {
            tracing::info!("Mounting {} dynamic artifacts for agent {}", dynamic_file_mounts.len(), agent_id);
            full_file_paths.extend(dynamic_file_mounts);
        }

        // === FIX START: FORCE GLOBAL TOOL ACCESS ===
        // We ignore agent_config.tools (what the Architect planned) 
        // and give every agent the full kit. This prevents UNEXPECTED_TOOL_CALL
        // when a "Worker" decides it needs to write a file or run code.
        let universal_tools = vec![
            "web_search".to_string(),
            "execute_python".to_string(),
            "read_file".to_string(),
            "write_file".to_string(),
            "list_files".to_string()
        ];
        // === FIX END ===

        // 11. Return Payload
        Ok(InvocationPayload {
            run_id: run_id.to_string(),
            agent_id: agent_id.to_string(),
            model: model_string,
            prompt: final_prompt,
            input_data: serde_json::Value::Object(input_data_map),
            parent_signature,
            cached_content_id,
            thinking_level,
            file_paths: full_file_paths,
            tools: universal_tools, // <--- CHANGED FROM agent_config.tools.clone()
        })
    }
```

### 2. Agent-Service: Fix Configuration Structure
The `automatic_function_calling` dictionary is a high-level client wrapper setting, but you are passing it into the low-level `GenerateContentConfig` object, which confuses the validation.

For manual tool execution (the "Tool Loop"), we simply provide the `tools` and set the `tool_config` to `AUTO`. We do **not** use the `automatic_function_calling` key here, as our python `while` loop handles the execution manually.

**File:** `apps/agent-service/src/core/llm.py`
**Function:** `_prepare_gemini_request`

```python
    # ... existing code ...
    
    # 2. Build Generation Config
    config_params: Dict[str, Any] = {
        "temperature": 1.0,
        # REMOVED: "automatic_function_calling": { "disable": True },
        # REASON: This key is invalid inside the 'config' dict for generate_content in v1beta.
        # The while-loop in call_gemini_with_context handles manual execution by default 
        # because generate_content() is stateless.
        "system_instruction": system_instruction 
    }

    # Add Deep Think configuration
    if "deep-think" in model and thinking_level:
        # ... (keep existing deep think logic)

    # 3. Prepare Tools (Inject into config)
    if tools:
        declarations = get_tool_declarations(tools)
        if declarations:
            tool_obj = types.Tool(function_declarations=declarations)
            config_params["tools"] = [tool_obj] 
            
            # === FIX: EXPLICITLY SET TOOL CONFIG TO AUTO ===
            # This tells the model "You can call tools if you want, or generate text."
            # It prevents the model from feeling restricted.
            config_params["tool_config"] = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.AUTO
                )
            )
            logger.debug(f"Tools enabled: {tools}")
```

### 3. Agent-Service: Robust Loop Extraction
The previous logic for detecting `UNEXPECTED_TOOL_CALL` was slightly flawed because it checked `if not content` before checking `function_call`. When Gemini outputs a tool call, `content.parts[0].text` is often None/Empty, which triggers your error logic improperly.

**File:** `apps/agent-service/src/core/llm.py`
**Function:** `call_gemini_with_context`

```python
            # ... inside the while loop ...
            
            candidate = response.candidates[0]

            # === DEBUG: RAW CANDIDATE DUMP ===
            logger.info(f"Agent {agent_id} [Turn {turn_count}] RAW CANDIDATE:\n{candidate}")
            
            content = candidate.content
            
            # === FIX START: ROBUST EXTRACTION ===
            function_calls = []
            
            # 1. Extract function calls FIRST
            if content and content.parts:
                for part in content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)
            
            # 2. Check for "Stop due to Tool Call" 
            # (Sometimes finish_reason says tool_call but parts are malformed, handling that edge case)
            finish_reason = candidate.finish_reason.name if candidate.finish_reason else "UNKNOWN"
            
            if not function_calls:
                # If no function calls, and no text, AND finish reason is STOP/MAX_TOKENS, we are done.
                # If finish reason is UNEXPECTED_TOOL_CALL, it means the Kernel provided tools the Model didn't expect (or vice versa).
                if finish_reason == "UNEXPECTED_TOOL_CALL":
                    logger.warning(f"Agent {agent_id} triggered UNEXPECTED_TOOL_CALL. Retrying without tools isn't implemented, returning raw response.")
                
                final_response = response
                break

            logger.info(f"Agent {agent_id} triggered {len(function_calls)} tool calls (Turn {turn_count})")
            
            current_contents.append(candidate.content)
            
            # ... continue with execution loop ...
```

### Summary of Faults Identified
1.  **Permission Siloing:** The Kernel was strictly adhering to the Architect's JSON plan (`agent_config.tools`). If the Architect (LLM) forgot to give `graph_generator` the `execute_python` tool, the model would hallucinate the tool call, resulting in an API error because the tool definition wasn't sent in that specific request.
2.  **Config Structure:** The `automatic_function_calling: { disable: True }` dict is syntactic sugar for the high-level `ChatSession` wrapper. When using the low-level `generate_content` (which is necessary for the stateless Agentic pattern), passing this inside the config dict is invalid or ignored, leading to undefined behavior.
3.  **Extraction Logic:** The condition `if not content and not is_tool_call` was evaluating `content` (an object) as false-y in some contexts or failing to extract parts correctly before checking the finish reason.