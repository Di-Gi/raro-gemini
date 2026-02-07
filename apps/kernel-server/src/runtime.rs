// [[RARO]]/apps/kernel-server/src/runtime.rs
// Purpose: Core orchestration logic with Redis Persistence added.
// Architecture: Domain Logic Layer
// Dependencies: reqwest, dashmap, tokio, redis, serde_json

use crate::dag::DAG;
use crate::models::*;
use crate::events::{RuntimeEvent, EventType};
use crate::registry::PatternRegistry;
use chrono::Utc;
use dashmap::DashMap;
use uuid::Uuid;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::env;
use std::collections::HashMap; // Added for ID remapping
use redis::AsyncCommands;
use tokio::sync::broadcast;
use crate::fs_manager;

/// Payload for invoking an agent with signature routing and caching
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvocationPayload {
    pub run_id: String,
    pub agent_id: String,
    pub model: String,
    pub prompt: String,
    pub user_directive: String,  // Runtime task from operator
    pub input_data: serde_json::Value,
    pub parent_signature: Option<String>,
    pub cached_content_id: Option<String>,
    pub thinking_level: Option<i32>,
    pub file_paths: Vec<String>,
    pub tools: Vec<String>,

    // [[NEW FIELDS]]
    pub allow_delegation: bool,
    pub graph_view: String,
}

pub struct RARORuntime {
    workflows: DashMap<String, WorkflowConfig>,
    runtime_states: DashMap<String, RuntimeState>,
    thought_signatures: DashMap<String, ThoughtSignatureStore>,
    dag_store: DashMap<String, DAG>,
    cache_resources: DashMap<String, String>, // run_id -> cached_content_id
    http_client: reqwest::Client,
    pub redis_client: Option<redis::Client>,
    pub event_bus: broadcast::Sender<RuntimeEvent>,
    pub pattern_registry: Arc<PatternRegistry>,
}

impl RARORuntime {
    pub fn new() -> Self {
        // Initialize Redis Client (optional, non-blocking)
        let redis_client = match env::var("REDIS_URL") {
            Ok(url) => {
                match redis::Client::open(url.as_str()) {
                    Ok(client) => {
                        tracing::info!("Redis client initialized: {}", url);
                        Some(client)
                    }
                    Err(e) => {
                        tracing::warn!("Failed to create Redis client: {}. Persistence disabled.", e);
                        None
                    }
                }
            }
            Err(_) => {
                tracing::warn!("REDIS_URL not set. Running without persistence.");
                None
            }
        };

        // Initialize Event Bus for Cortex
        let (tx, _) = broadcast::channel(100); // Buffer 100 events

        RARORuntime {
            workflows: DashMap::new(),
            runtime_states: DashMap::new(),
            thought_signatures: DashMap::new(),
            dag_store: DashMap::new(),
            cache_resources: DashMap::new(),
            // http_client: reqwest::Client::new(),
            http_client: reqwest::Client::builder()
                .pool_max_idle_per_host(0) // Disable pooling
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
            redis_client,
            event_bus: tx,
            pattern_registry: Arc::new(PatternRegistry::new()),
        }
    }
    /// Saves the current state of a run to Redis and manages the active index

    // === PERSISTENCE LAYER ===

    /// Saves the current state of a run to Redis and manages the active index
    async fn persist_state(&self, run_id: &str) {
        if let Some(client) = &self.redis_client {
            if let Some(state) = self.runtime_states.get(run_id) {
                let state_key = format!("run:{}:state", run_id);
                let active_set_key = "sys:active_runs";
                
                match serde_json::to_string(&*state) {
                    Ok(json) => {
                        match client.get_async_connection().await {
                            Ok(mut con) => {
                                // 1. Save State JSON
                                let _: redis::RedisResult<()> = con.set(&state_key, json).await;
                                
                                // 2. Manage Index
                                // If Completed or Failed, remove from active set. Otherwise add.
                                if state.status == RuntimeStatus::Completed || state.status == RuntimeStatus::Failed {
                                    let _: redis::RedisResult<()> = con.srem(active_set_key, run_id).await;
                                    // Optional: Set expiry on the state key so old runs eventually clean up (e.g., 24 hours)
                                    let _: redis::RedisResult<()> = con.expire(&state_key, 86400).await;
                                } else {
                                    let _: redis::RedisResult<()> = con.sadd(active_set_key, run_id).await;
                                }
                            },
                            Err(e) => tracing::error!("Redis connection failed during persist: {}", e),
                        }
                    },
                    Err(e) => tracing::error!("Failed to serialize state for {}: {}", run_id, e),
                }
            }
        }
    }

    /// Rehydrate state from Redis on boot
    pub async fn rehydrate_from_redis(&self) {
        if let Some(client) = &self.redis_client {
            tracing::info!("Attempting to rehydrate state from Redis...");
            match client.get_async_connection().await {
                Ok(mut con) => {
                    // 1. Get all active run IDs
                    let active_ids: Vec<String> = con.smembers("sys:active_runs").await.unwrap_or_default();
                    tracing::info!("Found {} active runs in persistence layer.", active_ids.len());

                    for run_id in active_ids {
                        let state_key = format!("run:{}:state", run_id);
                        let state_json: Option<String> = con.get(&state_key).await.unwrap_or(None);

                        if let Some(json) = state_json {
                            match serde_json::from_str::<RuntimeState>(&json) {
                                Ok(mut state) => {
                                    // IMPORTANT: On recovery, we might find a run that was "Running"
                                    // when the server crashed. We should probably mark it as "Failed"
                                    // or "Interrupted" so the UI knows it's not actually processing anymore.
                                    // For now, we will leave it as is to allow for potential resume logic later,
                                    // but logging it is essential.
                                    tracing::warn!("Rehydrating run: {} (Status: {:?})", state.run_id, state.status);
                                    // Restore DAG store if possible (Note: DAG structure isn't currently persisted in this simple implementation,
                                    // so complex resume isn't possible without rebuilding DAG from workflow config.
                                    // We will mark orphan runs as Failed for safety in this iteration).
                                    
                                    // Handle crash recovery state
                                    if state.status == RuntimeStatus::Running {
                                        state.status = RuntimeStatus::Failed; 
                                        // We treat crash recovery as failure for now
                                        state.invocations.push(AgentInvocation {
                                             id: Uuid::new_v4().to_string(),
                                             agent_id: "KERNEL".to_string(),
                                             model_variant: ModelVariant::Fast,
                                             thought_signature: None,
                                             tools_used: vec![],
                                             tokens_used: 0,
                                             latency_ms: 0,
                                             status: InvocationStatus::Failed,
                                             timestamp: Utc::now().to_rfc3339(),
                                             artifact_id: None,
                                             error_message: Some("Kernel restarted unexpectedly. Workflow terminated.".to_string()),
                                        });
                                    }

                                    self.runtime_states.insert(run_id.clone(), state);
                                },
                                Err(e) => tracing::error!("Failed to deserialize state for {}: {}", run_id, e),
                            }
                        }
                    }
                },
                Err(e) => tracing::error!("Failed to connect to Redis for rehydration: {}", e),
            }
        }
    }

    /// Emit an event to the event bus for Cortex pattern matching
    // === EVENT EMISSION ===

    pub(crate) fn emit_event(&self, event: RuntimeEvent) {
        // Broadcast to subscribers (Observers, WebSocket, PatternEngine)
        let _ = self.event_bus.send(event);
    }

    // === RESOURCE CLEANUP ===

    /// Notify Agent Service to clean up resources (E2B Sandboxes)
    async fn trigger_remote_cleanup(&self, run_id: &str) {
        let host = env::var("AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
        let port = env::var("AGENT_PORT").unwrap_or_else(|_| "8000".to_string());
        let scheme = if host.contains("localhost") || host == "127.0.0.1" { "http" } else { "http" };

        let url = format!("{}://{}:{}/runtime/{}/cleanup", scheme, host, port, run_id);

        tracing::info!("Triggering resource cleanup for run: {}", run_id);
        // Fire and forget - we don't block the kernel if cleanup fails

        let client = self.http_client.clone();
        tokio::spawn(async move {
            match client.delete(&url).send().await {
                Ok(res) => {
                    if !res.status().is_success() {
                        tracing::warn!("Cleanup request failed: Status {}", res.status());
                    }
                },
                Err(e) => tracing::warn!("Failed to send cleanup request: {}", e),
            }
        });
    }
    /// Request approval from user, pausing execution

    // === APPROVAL CONTROL ===

    pub async fn request_approval(&self, run_id: &str, agent_id: Option<&str>, reason: &str) {
        if let Some(mut state) = self.runtime_states.get_mut(run_id) {
            state.status = RuntimeStatus::AwaitingApproval;
            // Log the intervention event

            self.emit_event(RuntimeEvent::new(
                run_id,
                EventType::SystemIntervention,
                agent_id.map(|s| s.to_string()),
                serde_json::json!({
                    "action": "pause",
                    "reason": reason
                }),
            ));
        }
        self.persist_state(run_id).await;
        tracing::info!("Run {} PAUSED for approval: {}", run_id, reason);
    }
    /// Start a new workflow execution

    // === EXECUTION LOGIC ===

    pub fn start_workflow(self: &Arc<Self>, config: WorkflowConfig) -> Result<String, String> {
        // Validate workflow structure
        let mut dag = DAG::new();
        // Add all nodes

        for agent in &config.agents {
            dag.add_node(agent.id.clone())
                .map_err(|e| format!("Failed to add node: {}", e))?;
        }
        // Add edges based on dependencies

        for agent in &config.agents {
            for dep in &agent.depends_on {
                dag.add_edge(dep.clone(), agent.id.clone())
                    .map_err(|e| format!("Failed to add edge: {}", e))?;
            }
        }
        // Verify topological sort (catches cycles)

        let _execution_order = dag
            .topological_sort()
            .map_err(|e| format!("Invalid workflow: {}", e))?;

        let workflow_id = config.id.clone();
        let run_id = Uuid::new_v4().to_string();
        // === RFS INITIALIZATION ===
        // Create the session folder and copy files

        if let Err(e) = fs_manager::WorkspaceInitializer::init_run_session(&run_id, config.attached_files.clone()) {
             tracing::error!("Failed to initialize workspace for {}: {}", run_id, e);
             return Err(format!("FileSystem Initialization Error: {}", e));
        }
        // Store workflow and DAG

        self.workflows.insert(workflow_id.clone(), config.clone());
        self.dag_store.insert(run_id.clone(), dag);
        // Initialize runtime state

        let state = RuntimeState {
            run_id: run_id.clone(),
            workflow_id: workflow_id.clone(),
            status: RuntimeStatus::Running,
            active_agents: Vec::new(),
            completed_agents: Vec::new(),
            failed_agents: Vec::new(),
            invocations: Vec::new(),
            total_tokens_used: 0,
            start_time: Utc::now().to_rfc3339(),
            end_time: None,
        };

        self.runtime_states.insert(run_id.clone(), state);
        // Initialize thought signature store

        self.thought_signatures.insert(
            run_id.clone(),
            ThoughtSignatureStore {
                signatures: Default::default(),
            },
        );
        // Spawn the execution task (Fire and Forget)

        let runtime_clone = self.clone();
        let run_id_clone = run_id.clone();

        tokio::spawn(async move {
            runtime_clone.persist_state(&run_id_clone).await;
            runtime_clone.execute_dynamic_dag(run_id_clone).await;
        });

        Ok(run_id)
    }

    /// DYNAMIC EXECUTION LOOP
    pub(crate) async fn execute_dynamic_dag(&self, run_id: String) {
        tracing::info!("Starting DYNAMIC DAG execution for run_id: {}", run_id);
        // We use a simplified loop: Re-calculate topology, filter for uncompleted, take the next one.
        // In a real high-throughput system, we'd use a proper ready-queue, but re-calculating topology
        // on a small graph (<100 nodes) is negligible and safer for consistency.

        loop {
            // 1. Check if Run is still valid/active or paused
            if let Some(state) = self.runtime_states.get(&run_id) {
                // Check for pause state
                if state.status == RuntimeStatus::AwaitingApproval {
                    tracing::info!("Execution loop for {} suspending (Awaiting Approval).", run_id);
                    break;
                }
                // Check for terminal states
                if state.status == RuntimeStatus::Failed || state.status == RuntimeStatus::Completed {
                    break;
                }
            } else {
                // Run vanished
                break;
            }
            // 2. Determine Next Agent(s)
            // We get the full topological sort, then find the first node that is NOT complete and NOT running.

            // 2. Determine Next Agent(s) - FIX: INTEGRATED DEPENDENCY CHECK
            // We search for the first node that is pending AND has all dependencies satisfied.
            // This prevents head-of-line blocking where a waiting node prevents independent siblings from running.
            let next_agent_opt = {
                // Scope for locks
                let dag = match self.dag_store.get(&run_id) {
                    Some(d) => d,
                    None => {
                        tracing::error!("DAG not found for run {}", run_id);
                        break;
                    }
                };

                let execution_order = match dag.topological_sort() {
                    Ok(order) => order,
                    Err(e) => {
                        self.fail_run(&run_id, "SYSTEM", &format!("DAG cycle detected during execution: {}", e)).await;
                        break;
                    }
                };

                let state = self.runtime_states.get(&run_id).unwrap();  // Safe due to check above
                // Find first node that isn't done and isn't currently running

                // FIX: Filter for nodes that are NOT complete, NOT running, AND have dependencies met
                execution_order.into_iter().find(|agent_id| {
                    let is_pending = !state.completed_agents.contains(agent_id) &&
                                     !state.failed_agents.contains(agent_id) &&
                                     !state.active_agents.contains(agent_id);
                    
                    if !is_pending { return false; }

                    // Immediate dependency check
                    let deps = dag.get_dependencies(agent_id);
                    deps.iter().all(|d| state.completed_agents.contains(d))
                })
            };
            // 3. If no next agent, check if we are done

            // 3. Process Selection or Wait
            let agent_id = match next_agent_opt {
                Some(id) => id,
                None => {
                    // No agents ready. Are any running?
                    let running_count = self.runtime_states.get(&run_id)
                        .map(|s| s.active_agents.len())
                        .unwrap_or(0);

                    if running_count > 0 {
                        // Wait for them to finish (simple polling for this implementation)
                        // Wait for active agents to finish
                        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                        continue;
                    } else {
                        // Nothing running, nothing ready -> We are done!
                        if let Some(mut state) = self.runtime_states.get_mut(&run_id) {
                            state.status = RuntimeStatus::Completed;
                            state.end_time = Some(Utc::now().to_rfc3339());
                        }
                        self.persist_state(&run_id).await;
                        // Trigger Cleanup
                        self.trigger_remote_cleanup(&run_id).await;

                        tracing::info!("Workflow run {} completed successfully (Dynamic)", run_id);
                        break;
                    }
                }
            };

            // 5. Execute Agent
            tracing::info!("Processing agent: {}", agent_id);

            // === PUPPET MODE: PAUSE FOR INSPECTION ===
            let puppet_mode = env::var("PUPPET_MODE")
                .unwrap_or_else(|_| "false".to_string())
                .to_lowercase() == "true";

            if puppet_mode {
                tracing::info!("🎭 PUPPET MODE: Pausing execution for agent {}", agent_id);

                if let Some(client) = &self.redis_client {
                    // Gather context for puppet UI
                    let workflow_id = self.runtime_states.get(&run_id)
                        .map(|s| s.workflow_id.clone())
                        .unwrap_or_default();

                    let dependencies = self.dag_store.get(&run_id)
                        .map(|dag| dag.get_dependencies(&agent_id))
                        .unwrap_or_default();

                    let agent_context = serde_json::json!({
                        "run_id": run_id,
                        "agent_id": agent_id,
                        "workflow_id": workflow_id,
                        "dependencies": dependencies,
                        "timestamp": Utc::now().to_rfc3339(),
                        "status": "awaiting_decision"
                    });

                    // Publish to puppet channel
                    match client.get_async_connection().await {
                        Ok(mut con) => {
                            let _: Result<(), _> = con.publish(
                                "puppet:channel",
                                agent_context.to_string()
                            ).await;

                            // BLOCKING WAIT for puppet response (60s timeout)
                            let response_key = format!("puppet:response:{}:{}", run_id, agent_id);
                            let timeout = std::time::Duration::from_secs(60);

                            let wait_result = tokio::time::timeout(timeout, async {
                                loop {
                                    match con.get::<_, Option<String>>(&response_key).await {
                                        Ok(Some(response)) => {
                                            // Delete response key
                                            let _: Result<(), _> = con.del(&response_key).await;
                                            return Some(response);
                                        }
                                        Ok(None) => {
                                            // Not ready yet, wait a bit
                                            tokio::time::sleep(std::time::Duration::from_millis(200)).await;
                                        }
                                        Err(e) => {
                                            tracing::error!("Redis error waiting for puppet: {}", e);
                                            return None;
                                        }
                                    }
                                }
                            }).await;

                            match wait_result {
                                Ok(Some(response)) => {
                                    tracing::info!("🎭 Puppet response for {}: {}", agent_id, response);
                                    // Continue execution (mock may be set in Redis by puppet service)
                                }
                                Ok(None) | Err(_) => {
                                    tracing::warn!("🎭 Puppet timeout for {} - proceeding with normal execution", agent_id);
                                }
                            }
                        }
                        Err(e) => {
                            tracing::error!("Failed to connect to Redis for puppet mode: {}", e);
                        }
                    }
                } else {
                    tracing::warn!("🎭 PUPPET_MODE enabled but Redis unavailable");
                }
            }
            // ==========================================

            self.update_agent_status(&run_id, &agent_id, InvocationStatus::Running).await;
            // Emit AgentStarted event

            self.emit_event(RuntimeEvent::new(
                &run_id,
                EventType::AgentStarted,
                Some(agent_id.clone()),
                serde_json::json!({"agent_id": agent_id}),
            ));

            let payload_res = self.prepare_invocation_payload(&run_id, &agent_id).await;
            if let Err(e) = payload_res {
                self.fail_run(&run_id, &agent_id, &e).await;
                self.trigger_remote_cleanup(&run_id).await;
                continue;
            }
            let payload = payload_res.unwrap();

            let response = self.invoke_remote_agent(&payload).await;

            // 6. Handle Result & Potential Delegation
            match response {
                Ok(res) => {
                    // === POST-FLIGHT: PROTOCOL VALIDATOR & SEMANTIC CHECK ===
                    let text = res.output.as_ref()
                        .and_then(|o| o.get("result"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("");

                    // 1. Analyze Signals
                    let is_semantic_null = text.contains("[STATUS: NULL]");
                    let is_bypassed = text.contains("[BYPASS:");

                    // 2. Check Tool Evidence (Did the response include tool usage logs?)
                    let used_python = text.contains("execute_python") || text.contains("Tool 'execute_python' Result");
                    let used_search = text.contains("web_search") || text.contains("Tool 'web_search' Result");

                    // 3. Protocol Validation Logic
                    let mut protocol_violation = None;

                    if !is_bypassed {
                        if agent_id.starts_with("research_") && !used_search {
                            protocol_violation = Some("Protocol Violation: 'research_' agent did not use web_search (Hallucination Risk).");
                        } else if (agent_id.starts_with("analyze_") || agent_id.starts_with("coder_")) && !used_python {
                            protocol_violation = Some("Protocol Violation: 'analyze_'/'coder_' agent did not use execute_python (Integrity Risk).");
                        }
                    }

                    // 4. Circuit Breaker Decision
                    if res.success && !is_semantic_null && protocol_violation.is_none() {
                        // [[CONTEXT CACHING PERSISTENCE]]
                        // If the agent returned a cache ID (either created new or refreshed),
                        // update the runtime store so subsequent agents reuse it.
                        if let Some(cache_id) = &res.cached_content_id {
                            if let Err(e) = self.set_cache_resource(&run_id, cache_id.clone()) {
                                tracing::warn!("Failed to update cache resource for run {}: {}", run_id, e);
                            } else {
                                tracing::debug!("Updated Context Cache for run {}: {}", run_id, cache_id);
                            }
                        }

                        // A. Check for Delegation (Dynamic Splicing)
                        if let Some(delegation) = res.delegation {
                            tracing::info!("Agent {} requested delegation: {}", agent_id, delegation.reason);

                            // === FIX: SEPARATE LOCK SCOPES TO PREVENT DEADLOCK ===
                            // === FIX: DEADLOCK PREVENTION ===
                            // Separate lock scopes. 
                            // 1. Acquire Read Lock to check permission, then DROP IT immediately.
                            let can_delegate = if let Some(state) = self.runtime_states.get(&run_id) {
                                let wf_id = state.workflow_id.clone();
                                if let Some(workflow) = self.workflows.get(&wf_id) {
                                    workflow.agents.iter()
                                        .find(|a| a.id == agent_id)
                                        .map(|a| a.allow_delegation)
                                        .unwrap_or(false)
                                } else { false }
                            } else { false };

                            // 2. Acquire Write Lock inside handle_delegation (if permitted)
                            if !can_delegate {
                                tracing::warn!("Agent {} attempted delegation without permission. Ignoring.", agent_id);
                            } else {
                                match self.handle_delegation(&run_id, &agent_id, delegation).await {
                                    Ok(_) => {
                                        tracing::info!("Delegation processed. Graph updated.");
                                    }
                                    Err(e) => {
                                        tracing::error!("Delegation failed: {}", e);
                                        self.fail_run(&run_id, &agent_id, &format!("Delegation error: {}", e)).await;
                                        continue;
                                    }
                                }
                            }
                        }

                        // B. Standard Completion Logic
                        if let Some(sig) = res.thought_signature {
                            let _ = self.set_thought_signature(&run_id, &agent_id, sig);
                        }

                        let artifact_id = if let Some(output_data) = &res.output {
                            // File Promotion Logic
                            if let Some(files_array) = output_data.get("files_generated").and_then(|v| v.as_array()) {
                                let workflow_id = self.runtime_states.get(&run_id)
                                    .map(|s| s.workflow_id.clone())
                                    .unwrap_or_default();

                                let user_directive = {
                                    if let Some(workflow) = self.workflows.get(&workflow_id) {
                                        workflow.agents.iter()
                                            .find(|a| a.id == agent_id)
                                            .map(|a| a.user_directive.clone())
                                            .unwrap_or_default()
                                    } else { String::new() }
                                };

                                for file_val in files_array {
                                    if let Some(filename) = file_val.as_str() {
                                        let rid = run_id.clone();
                                        let wid = workflow_id.clone();
                                        let aid = agent_id.clone();
                                        let fname = filename.to_string();
                                        let directive = user_directive.clone();

                                        tokio::spawn(async move {
                                            match fs_manager::WorkspaceInitializer::promote_artifact_to_storage(
                                                &rid, &wid, &aid, &fname, &directive
                                            ).await {
                                                Ok(_) => tracing::info!("✓ Artifact '{}' promoted to persistent storage", fname),
                                                Err(e) => tracing::error!("✗ Failed to promote artifact '{}': {}", fname, e),
                                            }
                                        });
                                    }
                                }
                            }

                            let agent_stored_flag = output_data.get("artifact_stored")
                                .and_then(|v| v.as_bool())
                                .unwrap_or(false);

                            if agent_stored_flag {
                                Some(format!("run:{}:agent:{}:output", run_id, agent_id))
                            } else {
                                self.store_artifact(&run_id, &agent_id, output_data).await
                            }
                        } else { None };

                        let invocation = AgentInvocation {
                            id: Uuid::new_v4().to_string(),
                            agent_id: agent_id.clone(),
                            model_variant: match payload.model.as_str() {
                                "gemini-2.5-flash" => ModelVariant::Fast,
                                _ => ModelVariant::Reasoning,
                            },
                            thought_signature: None,
                            tools_used: payload.tools.clone(),
                            tokens_used: res.tokens_used,
                            latency_ms: res.latency_ms as u64,
                            status: InvocationStatus::Success,
                            timestamp: Utc::now().to_rfc3339(),
                            artifact_id,
                            error_message: None,
                        };

                        let _ = self.record_invocation(&run_id, invocation).await;

                        self.emit_event(RuntimeEvent::new(
                            &run_id,
                            EventType::AgentCompleted,
                            Some(agent_id.clone()),
                            serde_json::json!({"agent_id": agent_id, "tokens_used": res.tokens_used}),
                        ));
                    } else {
                        // === CIRCUIT BREAKER: FAILURE / PAUSE LOGIC ===
                        let pause_reason = if is_semantic_null {
                            format!("Agent '{}' reported a Semantic Null (found no data).", agent_id)
                        } else if let Some(violation) = protocol_violation {
                            violation.to_string()
                        } else {
                            res.error.unwrap_or_else(|| "Unknown Execution Error".to_string())
                        };

                        tracing::error!("Circuit Breaker Triggered for {}: {}", agent_id, pause_reason);

                        // A. Pause the Run
                        self.request_approval(&run_id, Some(&agent_id), &pause_reason).await;

                        // B. Emit Event for Cortex/UI
                        self.emit_event(RuntimeEvent::new(
                            &run_id,
                            EventType::AgentFailed, // This triggers the Config Pattern
                            Some(agent_id.clone()),
                            serde_json::json!({
                                "error": pause_reason,
                                "recovery_hint": "Check Prompt or Data Sources"
                            }),
                        ));

                        // C. Break Execution Loop
                        break;
                    }
                }
                Err(e) => {
                    self.emit_event(RuntimeEvent::new(
                        &run_id,
                        EventType::AgentFailed,
                        Some(agent_id.clone()),
                        serde_json::json!({"agent_id": agent_id, "error": e.to_string()}),
                    ));
                    self.fail_run(&run_id, &agent_id, &e.to_string()).await;
                    self.trigger_remote_cleanup(&run_id).await;
                }
            }
        }
    }

    /// Handles the "Graph Surgery" when an agent requests delegation
    async fn handle_delegation(&self, run_id: &str, parent_id: &str, mut req: DelegationRequest) -> Result<(), String> {
        let state = self.runtime_states.get(run_id).ok_or("Run not found")?;
        let workflow_id = state.workflow_id.clone();
        drop(state);  // Drop read lock

        // 2. PRE-FETCH DEPENDENTS & SANITIZE IDs
        let (existing_dependents, existing_node_ids) = if let Some(dag) = self.dag_store.get(run_id) {
            (dag.get_children(parent_id), dag.export_nodes())
        } else {
            return Err("DAG not found for pre-fetch".to_string());
        };

        // FIX: ID Collision Remapping with Ghost Prevention
        // If a new node has an ID that already exists:
        // - If the existing node is PENDING (not started), adopt it (overwrite)
        // - If the existing node is ACTIVE/COMPLETED, rename to avoid corruption
        let mut id_map: HashMap<String, String> = HashMap::new();

        for node in &mut req.new_nodes {
            if existing_node_ids.contains(&node.id) {
                // CHECK STATUS OF EXISTING NODE
                let is_pending = if let Some(state) = self.runtime_states.get(run_id) {
                    !state.active_agents.contains(&node.id) &&
                    !state.completed_agents.contains(&node.id) &&
                    !state.failed_agents.contains(&node.id)
                } else {
                    false
                };

                if is_pending {
                    tracing::info!("Delegation UPDATE: Adopting/Overwriting pending node '{}'.", node.id);

                    // UPDATE LOGIC: Remove old definition so we can replace it
                    if let Some(mut workflow) = self.workflows.get_mut(&workflow_id) {
                        if let Some(pos) = workflow.agents.iter().position(|a| a.id == node.id) {
                            workflow.agents.remove(pos);
                        }
                    }

                    // DAG LOGIC: Clear old incoming edges to allow "Rewiring"
                    // This allows the agent to change dependencies for an existing pending node
                    if let Some(mut dag) = self.dag_store.get_mut(run_id) {
                        dag.clear_incoming_edges(&node.id);
                    }

                } else {
                    // Node is already running/done, we MUST rename to avoid history corruption
                    let old_id = node.id.clone();
                    let suffix = Uuid::new_v4().to_string().split('-').next().unwrap().to_string();
                    let new_id = format!("{}_{}", old_id, suffix);
                    tracing::warn!("Delegation ID Collision: Renaming '{}' to '{}' (node already active/completed)", old_id, new_id);

                    node.id = new_id.clone();
                    id_map.insert(old_id, new_id);
                }
            }
        }

        // Apply rewiring to new nodes' dependency lists
        for node in &mut req.new_nodes {
            node.depends_on = node.depends_on.iter().map(|dep| {
                // If dependency is in our map, update it. Otherwise keep original.
                id_map.get(dep).cloned().unwrap_or(dep.clone())
            }).collect();
        }

        // 3. MUTATE WORKFLOW CONFIG
        if let Some(mut workflow) = self.workflows.get_mut(&workflow_id) {
            for node in &req.new_nodes {
                workflow.agents.push(node.clone());
            }

            if req.strategy == DelegationStrategy::Child {
                for dep_id in &existing_dependents {
                    if let Some(dep_agent) = workflow.agents.iter_mut().find(|a| a.id == *dep_id) {
                        dep_agent.depends_on.retain(|p| p != parent_id);
                        for new_node in &req.new_nodes {
                            if !dep_agent.depends_on.contains(&new_node.id) {
                                dep_agent.depends_on.push(new_node.id.clone());
                            }
                        }
                        tracing::info!("Rewired Config: Agent {} now depends on {:?}", dep_id, dep_agent.depends_on);
                    }
                }
            }
        } else {
            return Err("Workflow config not found".to_string());
        }

        // 4. MUTATE DAG TOPOLOGY
        if let Some(mut dag) = self.dag_store.get_mut(run_id) {
            
            for node in &req.new_nodes {
                dag.add_node(node.id.clone()).map_err(|e| e.to_string())?;

                // Add explicit edges from config
                // This covers internal dependencies (B depends on A) inside the delegated cluster
                for dep in &node.depends_on {
                    if let Err(e) = dag.add_edge(dep.clone(), node.id.clone()) {
                        // If it's a new node dependency, it should work. 
                        // If it's the parent, we handle it below explicitly, but adding here is fine if safe.
                        // However, we must ensure we don't double add or cause issues if logic below handles parent.
                        // For SAFETY: Let's trust the explicit parent logic below for the "Root" connection,
                        // and use this loop only for internal or other external dependencies.
                        // Actually, `dag.add_edge` is safe (idempotent logic usually handled or error if cycle).
                        // Let's just log warning if it fails but continue for parent connection.
                        tracing::debug!("Adding dependency edge {} -> {}: {:?}", dep, node.id, e);
                    }
                }

                // If node has NO dependencies in the new set, attach to Parent (if Child strategy)
                // Or if it explicitly depends on Parent (which we might have remapped?)
                // Simplified Logic: If strategy is Child, we FORCE parent connection if not already present?
                // Actually, the Architect/LLM usually adds parent to `depends_on`.
                // If LLM output `depends_on: []`, we must attach to parent.
                if node.depends_on.is_empty() || node.depends_on.contains(&parent_id.to_string()) {
                     let _ = dag.add_edge(parent_id.to_string(), node.id.clone());
                }

                // B. Connect New Nodes -> Existing Dependents (Splicing)
                if req.strategy == DelegationStrategy::Child {
                    for dep in &existing_dependents {
                        dag.add_edge(node.id.clone(), dep.clone()).map_err(|e| e.to_string())?;
                    }
                }
            }

            // C. Remove Old Edges (Parent -> Dependents)
            if req.strategy == DelegationStrategy::Child {
                for dep in &existing_dependents {
                    let _ = dag.remove_edge(parent_id, dep);
                }
            }
            // Validate Cycle

            if let Err(e) = dag.topological_sort() {
                tracing::error!("Delegation created a cycle: {:?}", e);
                return Err("Delegation created a cycle in DAG".to_string());
            }
        } else {
            return Err("DAG not found".to_string());
        }

        Ok(())
    }

    /// Helper to fail the run and update state (Async + Persistent)
    pub async fn fail_run(&self, run_id: &str, agent_id: &str, error: &str) {
        if let Some(mut state) = self.runtime_states.get_mut(run_id) {
            state.status = RuntimeStatus::Failed;
            state.end_time = Some(Utc::now().to_rfc3339());
            state.failed_agents.push(agent_id.to_string());
            
            // Remove from active if present
            state.active_agents.retain(|a| a != agent_id);
            
            // Record failed invocation
            state.invocations.push(AgentInvocation {
                id: Uuid::new_v4().to_string(),
                agent_id: agent_id.to_string(),
                model_variant: ModelVariant::Fast, // Fallback
                thought_signature: None,
                tools_used: vec![],
                tokens_used: 0,
                latency_ms: 0,
                status: InvocationStatus::Failed,
                timestamp: Utc::now().to_rfc3339(),
                artifact_id: None,
                error_message: Some(error.to_string()), 
            });
        }
        
        self.persist_state(run_id).await;
        tracing::error!("Run {} failed at agent {}: {}", run_id, agent_id, error);
    }

    /// Helper to update status to Running (Async + Persistent)
    async fn update_agent_status(&self, run_id: &str, agent_id: &str, status: InvocationStatus) {
         let mut changed = false;
         if let Some(mut state) = self.runtime_states.get_mut(run_id) {
            match status {
                InvocationStatus::Running => {
                     if !state.active_agents.contains(&agent_id.to_string()) {
                         state.active_agents.push(agent_id.to_string());
                         changed = true;
                     }
                },
                _ => {}
            }  // Drop write lock before persisting
         }
         
         if changed {
             self.persist_state(run_id).await;
         }
    }

    /// Perform the actual HTTP request to the Agent Service
    async fn invoke_remote_agent(&self, payload: &InvocationPayload) -> Result<RemoteAgentResponse, reqwest::Error> {
        // Resolve Agent Host from Env or Default
        let host = env::var("AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
        let port = env::var("AGENT_PORT").unwrap_or_else(|_| "8000".to_string());
        let scheme = if host.contains("localhost") || host == "127.0.0.1" { "http" } else { "http" };
        
        let url = format!("{}://{}:{}/invoke", scheme, host, port);

        tracing::debug!("Sending invocation request to: {}", url);

        let response = self.http_client
            .post(&url)
            .json(payload)
            .send()
            .await?;

        response.json::<RemoteAgentResponse>().await
    }

    /// Store agent output to Redis with TTL
    async fn store_artifact(
        &self,
        run_id: &str,
        agent_id: &str,
        output: &serde_json::Value,
    ) -> Option<String> {
        let artifact_id = format!("run:{}:agent:{}:output", run_id, agent_id);

        let json_str = match serde_json::to_string(output) {
            Ok(s) => s,
            Err(e) => {
                tracing::error!("Failed to serialize artifact for {}: {}", agent_id, e);
                return None;
            }
        };

        if let Some(client) = &self.redis_client {
            match client.get_async_connection().await {
                Ok(mut con) => {
                    match con.set_ex::<_, _, ()>(&artifact_id, json_str, 3600).await {
                        Ok(_) => {
                            tracing::debug!("Stored artifact: {}", artifact_id);
                            return Some(artifact_id);
                        }
                        Err(e) => {
                            tracing::error!("Failed to write artifact to Redis: {}", e);
                        }
                    }
                }
                Err(e) => {
                    tracing::error!("Failed to get Redis connection: {}", e);
                }
            }
        } else {
            tracing::debug!("No Redis client available, artifact not stored");
        }

        None
    }
    /// Get current runtime state

    pub fn get_state(&self, run_id: &str) -> Option<RuntimeState> {
        self.runtime_states.get(run_id).map(|r| (*r).clone())
    }
    /// Record an agent invocation (Async + Persistent)

    pub async fn record_invocation(&self, run_id: &str, invocation: AgentInvocation) -> Result<(), String> {
        {
            let mut state = self
                .runtime_states
                .get_mut(run_id)
                .ok_or_else(|| "Run not found".to_string())?;

            state.invocations.push(invocation.clone());
            state.total_tokens_used += invocation.tokens_used;

            match invocation.status {
                InvocationStatus::Running => {
                    if !state.active_agents.contains(&invocation.agent_id) {
                        state.active_agents.push(invocation.agent_id.clone());
                    }
                }
                InvocationStatus::Success => {
                    state.active_agents.retain(|a| a != &invocation.agent_id);
                    state.completed_agents.push(invocation.agent_id.clone());
                }
                InvocationStatus::Failed => {
                    state.active_agents.retain(|a| a != &invocation.agent_id);
                    state.failed_agents.push(invocation.agent_id.clone());
                }
                _ => {}
            }
        } 

        self.persist_state(run_id).await;

        Ok(())
    }
    /// Store or retrieve thought signature

    pub fn set_thought_signature(&self, run_id: &str, agent_id: &str, signature: String) -> Result<(), String> {
        let mut store = self
            .thought_signatures
            .get_mut(run_id)
            .ok_or_else(|| "Run not found".to_string())?;

        store.signatures.insert(agent_id.to_string(), signature);
        Ok(())
    }

    pub fn get_thought_signature(&self, run_id: &str, agent_id: &str) -> Option<String> {
        self.thought_signatures
            .get(run_id)
            .and_then(|store| store.signatures.get(agent_id).cloned())
    }

    pub fn get_all_signatures(&self, run_id: &str) -> Option<ThoughtSignatureStore> {
        self.thought_signatures.get(run_id).map(|s| (*s).clone())
    }

    /// Generate a contextual graph view based on agent's delegation privilege.
    /// Includes "Optics" (specialty info) for Pending nodes to facilitate updates.
    ///
    /// - **detailed=true**: Returns JSON array with full topology (for orchestrators)
    /// - **detailed=false**: Returns linear text view (for workers)
    fn generate_graph_context(&self, run_id: &str, current_agent_id: &str, detailed: bool) -> String {
        let state = match self.runtime_states.get(run_id) {
            Some(s) => s,
            None => return "Graph state unavailable.".to_string(),
        };

        let dag = match self.dag_store.get(run_id) {
            Some(d) => d,
            None => return "Graph topology unavailable.".to_string(),
        };

        let workflow_id = state.workflow_id.clone();

        // Helper to get specialty for a node
        let get_node_info = |node_id: &str| -> String {
            if let Some(workflow) = self.workflows.get(&workflow_id) {
                if let Some(agent) = workflow.agents.iter().find(|a| a.id == node_id) {
                    return agent.prompt.chars().take(50).collect::<String>();
                }
            }
            "Unknown Specialty".to_string()
        };

        if detailed {
            let nodes: Vec<serde_json::Value> = dag.export_nodes().iter().map(|node_id| {
                let status = if state.completed_agents.contains(node_id) { "completed" }
                else if state.failed_agents.contains(node_id) { "failed" }
                else if state.active_agents.contains(node_id) { "running" }
                else { "pending" };

                let specialty = if status == "pending" {
                    get_node_info(node_id)
                } else {
                    "".to_string()
                };

                serde_json::json!({
                    "id": node_id,
                    "status": status,
                    "is_you": node_id == current_agent_id,
                    "dependencies": dag.get_dependencies(node_id),
                    "specialty_preview": specialty
                })
            }).collect();

            return serde_json::to_string_pretty(&nodes).unwrap_or_default();
        } else {
            match dag.topological_sort() {
                Ok(order) => {
                    let parts: Vec<String> = order.iter().map(|node_id| {
                        let status = if state.completed_agents.contains(node_id) { "COMPLETE" }
                        else if state.failed_agents.contains(node_id) { "FAILED" }
                        else if state.active_agents.contains(node_id) { "RUNNING" }
                        else { "PENDING" };

                        if node_id == current_agent_id {
                            format!("[{}:{}(YOU)]", node_id, status)
                        } else if status == "PENDING" {
                            // OPTICS: Show specialty for pending nodes
                            format!("[{}:{} ({})]", node_id, status, get_node_info(node_id))
                        } else {
                            format!("[{}:{}]", node_id, status)
                        }
                    }).collect();
                    return parts.join(" -> ");
                },
                Err(_) => return "Cycle detected in graph view.".to_string()
            }
        }
    }

    pub async fn prepare_invocation_payload(
        &self,
        run_id: &str,
        agent_id: &str,
    ) -> Result<InvocationPayload, String> {
        let state = self
            .runtime_states
            .get(run_id)
            .ok_or_else(|| "Run not found".to_string())?;

        let workflow = self
            .workflows
            .get(&state.workflow_id)
            .ok_or_else(|| "Workflow not found".to_string())?;

        let agent_config = workflow
            .agents
            .iter()
            .find(|a| a.id == agent_id)
            .ok_or_else(|| format!("Agent {} not found", agent_id))?;

        let parent_signature = if !agent_config.depends_on.is_empty() {
            agent_config
                .depends_on
                .iter()
                .find_map(|parent_id| self.get_thought_signature(run_id, parent_id))
        } else {
            None
        };

        let mut context_prompt_appendix = String::new();
        let mut input_data_map = serde_json::Map::new();
        let mut dynamic_file_mounts: Vec<String> = Vec::new();

        if !agent_config.depends_on.is_empty() {
            if let Some(client) = &self.redis_client {
                match client.get_async_connection().await {
                    Ok(mut con) => {
                        for parent_id in &agent_config.depends_on {
                            let key = format!("run:{}:agent:{}:output", run_id, parent_id);
                            
                            let data: Option<String> = con.get(&key).await.unwrap_or(None);

                            if let Some(json_str) = data {
                                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&json_str) {
                                    input_data_map.insert(parent_id.clone(), val.clone());

                                    let content = val.get("result")
                                        .and_then(|v| v.as_str())
                                        .or_else(|| val.get("output").and_then(|v| v.as_str()))
                                        .unwrap_or("No text output");

                                    context_prompt_appendix.push_str(&format!("\n\n=== CONTEXT FROM AGENT {} ===\n{}\n", parent_id, content));

                                    if let Some(files_array) = val.get("files_generated").and_then(|v| v.as_array()) {
                                        for file_val in files_array {
                                            if let Some(filename) = file_val.as_str() {
                                                let mount_path = format!("/app/storage/sessions/{}/output/{}", run_id, filename);
                                                if !dynamic_file_mounts.contains(&mount_path) {
                                                    dynamic_file_mounts.push(mount_path);
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    },
                    Err(e) => tracing::warn!("Could not connect to Redis for context fetching: {}", e),
                }
            } else {
                tracing::warn!("Redis client unavailable. Context fetching skipped.");
            }
        }

        // === PRE-FLIGHT: CONTEXT DROUGHT PREVENTION ===
        // Check if upstream agents provided any usable data
        let has_null_signal = context_prompt_appendix.contains("[STATUS: NULL]");
        let has_files = !dynamic_file_mounts.is_empty();
        let is_root_node = agent_config.depends_on.is_empty();

        // If we depend on others, and they gave us nothing but NULLs or empty text, pause.
        if !is_root_node && (context_prompt_appendix.trim().is_empty() || (has_null_signal && !has_files)) {
            let drought_msg = format!("Pre-Execution Halt: Agent '{}' is facing a Context Drought. Upstream nodes provided no usable data.", agent_id);
            tracing::warn!("{}", drought_msg);

            // Trigger the Circuit Breaker
            self.request_approval(run_id, Some(agent_id), &drought_msg).await;

            return Err("Halted: Contextual Data Drought".to_string());
        }

        let mut final_prompt = agent_config.prompt.clone();
        if !context_prompt_appendix.is_empty() {
            final_prompt.push_str(&context_prompt_appendix);
        }

        let cached_content_id = self.get_cache_resource(run_id);

        let model_string = match &agent_config.model {
            ModelVariant::Fast => "fast".to_string(),
            ModelVariant::Reasoning => "reasoning".to_string(),
            ModelVariant::Thinking => "thinking".to_string(),
            ModelVariant::Custom(s) => s.clone(),
        };

        let thinking_level = if matches!(agent_config.model, ModelVariant::Thinking) {
            Some(5)  // Default budget level for Thinking models
        } else {
            None
        };

        let mut full_file_paths: Vec<String> = workflow.attached_files.iter()
            .map(|f| format!("/app/storage/sessions/{}/input/{}", run_id, f))
            .collect();

        let has_dynamic_artifacts = !dynamic_file_mounts.is_empty();
        let dynamic_artifact_count = dynamic_file_mounts.len();

        if has_dynamic_artifacts {
            tracing::info!("Mounting {} dynamic artifacts for agent {}", dynamic_artifact_count, agent_id);
            full_file_paths.extend(dynamic_file_mounts);
        }

        // === AUTHORITATIVE IDENTITY PROVISIONING (MERGE & VALIDATE) ===
        // STRATEGY: Start with user configuration, then enforce identity mandates
        // This allows manual additions while preventing capability removal

        // 1. Start with tools defined in the Configuration (from UI/Architect)
        let mut tools = agent_config.tools.clone();

        // 2. UNIVERSAL BASELINE (Always ensure these exist)
        if !tools.contains(&"read_file".to_string()) { tools.push("read_file".to_string()); }
        if !tools.contains(&"list_files".to_string()) { tools.push("list_files".to_string()); }

        let id_lower = agent_id.to_lowercase();

        // 3. MANDATORY IDENTITY GRANTS (Enforce identity contract)
        // Even if user removed them in UI, identity demands them.

        // Research Class (Broader matching: research_, researcher, web_)
        if id_lower.contains("research") || id_lower.starts_with("web_") {
            if !tools.contains(&"web_search".to_string()) {
                tools.push("web_search".to_string());
            }
        }

        // Logic/Math Class (analyze_, analyst, coder, math)
        if id_lower.contains("analy") || id_lower.contains("code") || id_lower.contains("math") {
            if !tools.contains(&"execute_python".to_string()) {
                tools.push("execute_python".to_string());
            }
        }

        // Output/I-O Class (writer, coder, logger)
        if id_lower.contains("code") || id_lower.contains("writ") {
            if !tools.contains(&"write_file".to_string()) {
                tools.push("write_file".to_string());
            }
        }

        // Admin Class (master_, orchestrator)
        if id_lower.starts_with("master_") || id_lower.starts_with("orchestrator") {
            for t in ["web_search", "execute_python", "write_file"] {
                if !tools.contains(&t.to_string()) {
                    tools.push(t.to_string());
                }
            }
        }

        // Dynamic artifacts require python (Special Case)
        if has_dynamic_artifacts && !tools.contains(&"execute_python".to_string()) {
            tools.push("execute_python".to_string());
            tracing::info!("Agent {}: Provisioned 'execute_python' for dynamic artifact handling", agent_id);
        }

        tracing::info!("Final provisioned tools for {}: {:?}", agent_id, tools);

        let graph_view = self.generate_graph_context(
            run_id,
            agent_id,
            agent_config.allow_delegation
        );

        Ok(InvocationPayload {
            run_id: run_id.to_string(),
            agent_id: agent_id.to_string(),
            model: model_string,
            prompt: final_prompt,
            user_directive: agent_config.user_directive.clone(),  // Pass operator directive
            input_data: serde_json::Value::Object(input_data_map),
            parent_signature,
            cached_content_id,
            thinking_level,
            file_paths: full_file_paths,
            tools,  // Now contains Architect's choices + smart baseline guarantees
            allow_delegation: agent_config.allow_delegation,
            graph_view,
        })
    }

    pub fn set_run_status(&self, run_id: &str, status: RuntimeStatus) {
        if let Some(mut state) = self.runtime_states.get_mut(run_id) {
            state.status = status;
        }
    }
    
    pub fn get_topology_snapshot(&self, run_id: &str) -> Option<serde_json::Value> {
        if let Some(dag) = self.dag_store.get(run_id) {
            let edges = dag.export_edges();
            let nodes = dag.export_nodes();
            
            Some(serde_json::json!({
                "nodes": nodes,
                "edges": edges.into_iter().map(|(from, to)| {
                    serde_json::json!({ "from": from, "to": to })
                }).collect::<Vec<_>>()
            }))
        } else {
            None
        }
    }

    pub fn set_cache_resource(&self, run_id: &str, cached_content_id: String) -> Result<(), String> {
        self.cache_resources.insert(run_id.to_string(), cached_content_id);
        Ok(())
    }

    pub fn get_cache_resource(&self, run_id: &str) -> Option<String> {
        self.cache_resources.get(run_id).map(|c| c.clone())
    }

    pub fn has_dag(&self, run_id: &str) -> bool {
        self.dag_store.contains_key(run_id)
    }
}
