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
    pub input_data: serde_json::Value,
    pub parent_signature: Option<String>,
    pub cached_content_id: Option<String>,
    pub thinking_level: Option<i32>,
    pub file_paths: Vec<String>,
    pub tools: Vec<String>,
}

// #[derive(Debug, Clone, Serialize, Deserialize)]
// pub struct RemoteAgentResponse {
//     pub agent_id: String,
//     pub success: bool,
//     pub output: Option<serde_json::Value>,
//     pub error: Option<String>,
//     pub tokens_used: usize,
//     pub thought_signature: Option<String>,
//     pub input_tokens: usize,
//     pub output_tokens: usize,
//     pub cache_hit: bool,
//     pub latency_ms: f64,
// }
// moved to models


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
            http_client: reqwest::Client::new(),
            redis_client,
            event_bus: tx,
            pattern_registry: Arc::new(PatternRegistry::new()),
        }
    }

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

    // === EVENT EMISSION ===

    /// Emit an event to the event bus for Cortex pattern matching
    pub(crate) fn emit_event(&self, event: RuntimeEvent) {
        // Broadcast to subscribers (Observers, WebSocket, PatternEngine)
        let _ = self.event_bus.send(event);
    }

    // === APPROVAL CONTROL ===

    /// Request approval from user, pausing execution
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

    // === EXECUTION LOGIC ===

    /// Start a new workflow execution
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
    /// Keeps pulling 'ready' nodes from the DAG until completion or failure.
    /// Handles graph mutations (delegation) mid-flight.
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

                let state = self.runtime_states.get(&run_id).unwrap(); // Safe due to check above

                // Find first node that isn't done and isn't currently running
                execution_order.into_iter().find(|agent_id| {
                    !state.completed_agents.contains(agent_id) &&
                    !state.failed_agents.contains(agent_id) &&
                    !state.active_agents.contains(agent_id)
                })
            };

            // 3. If no next agent, check if we are done
            let agent_id = match next_agent_opt {
                Some(id) => id,
                None => {
                    // No agents ready. Are any running?
                    let running_count = self.runtime_states.get(&run_id)
                        .map(|s| s.active_agents.len())
                        .unwrap_or(0);

                    if running_count > 0 {
                        // Wait for them to finish (simple polling for this implementation)
                        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                        continue;
                    } else {
                        // Nothing running, nothing ready -> We are done!
                        if let Some(mut state) = self.runtime_states.get_mut(&run_id) {
                            state.status = RuntimeStatus::Completed;
                            state.end_time = Some(Utc::now().to_rfc3339());
                        }
                        self.persist_state(&run_id).await;
                        tracing::info!("Workflow run {} completed successfully (Dynamic)", run_id);
                        break;
                    }
                }
            };

            // 4. Verify Dependencies
            // The topo sort gives us order, but we must ensure parents are actually *completed*.
            let can_run = {
                let dag = self.dag_store.get(&run_id).unwrap();
                let deps = dag.get_dependencies(&agent_id);
                let state = self.runtime_states.get(&run_id).unwrap();
                deps.iter().all(|d| state.completed_agents.contains(d))
            };

            if !can_run {
                // If dependencies aren't met, but topological sort put us here,
                // it means dependencies are still running or failed.
                // We wait.
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                continue;
            }

            // 5. Execute Agent
            tracing::info!("Processing agent: {}", agent_id);
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
                continue;
            }
            let payload = payload_res.unwrap();

            let response = self.invoke_remote_agent(&payload).await;

            // 6. Handle Result & Potential Delegation
            match response {
                Ok(res) => {
                    if res.success {
                        // A. Check for Delegation (Dynamic Splicing)
                        if let Some(delegation) = res.delegation {
                            tracing::info!("Agent {} requested delegation: {}", agent_id, delegation.reason);

                            // Splice the graph
                            match self.handle_delegation(&run_id, &agent_id, delegation).await {
                                Ok(_) => {
                                    // Delegation successful.
                                    // Mark current agent as complete (it successfully delegated).
                                    // The loop will pick up the new nodes next.
                                    tracing::info!("Delegation processed. Graph updated.");
                                }
                                Err(e) => {
                                    tracing::error!("Delegation failed: {}", e);
                                    self.fail_run(&run_id, &agent_id, &format!("Delegation error: {}", e)).await;
                                    continue;
                                }
                            }
                        }

                        // B. Standard Completion Logic
                        if let Some(sig) = res.thought_signature {
                            let _ = self.set_thought_signature(&run_id, &agent_id, sig);
                        }

                        // Store Artifact
                        let artifact_id = if let Some(output_data) = &res.output {
                            let agent_stored_flag = output_data.get("artifact_stored")
                                .and_then(|v| v.as_bool())
                                .unwrap_or(false);

                            if agent_stored_flag {
                                Some(format!("run:{}:agent:{}:output", run_id, agent_id))
                            } else {
                                self.store_artifact(&run_id, &agent_id, output_data).await
                            }
                        } else { None };

                        // Record Metrics
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

                        // Emit AgentCompleted event
                        self.emit_event(RuntimeEvent::new(
                            &run_id,
                            EventType::AgentCompleted,
                            Some(agent_id.clone()),
                            serde_json::json!({"agent_id": agent_id, "tokens_used": res.tokens_used}),
                        ));
                    } else {
                        // Failure
                        let error = res.error.unwrap_or_else(|| "Unknown error".to_string());

                        // Emit AgentFailed event
                        self.emit_event(RuntimeEvent::new(
                            &run_id,
                            EventType::AgentFailed,
                            Some(agent_id.clone()),
                            serde_json::json!({"agent_id": agent_id, "error": error}),
                        ));

                        self.fail_run(&run_id, &agent_id, &error).await;
                    }
                }
                Err(e) => {
                    // Emit AgentFailed event for network errors
                    self.emit_event(RuntimeEvent::new(
                        &run_id,
                        EventType::AgentFailed,
                        Some(agent_id.clone()),
                        serde_json::json!({"agent_id": agent_id, "error": e.to_string()}),
                    ));

                    self.fail_run(&run_id, &agent_id, &e.to_string()).await;
                }
            }
        }
    }

    /// Handles the "Graph Surgery" when an agent requests delegation
    async fn handle_delegation(&self, run_id: &str, parent_id: &str, req: DelegationRequest) -> Result<(), String> {
        // 1. Get lock on Workflow Config (to register new agents)
        // 2. Get lock on DAG (to rewire edges)

        let state = self.runtime_states.get(run_id).ok_or("Run not found")?;
        let workflow_id = state.workflow_id.clone();
        drop(state); // Drop read lock

        // Mutate Workflow Config
        // We need to add the new agent configs so `prepare_invocation_payload` can find them later
        if let Some(mut workflow) = self.workflows.get_mut(&workflow_id) {
            for node in &req.new_nodes {
                // Ensure unique IDs if not provided? Assuming agent provides unique IDs or we should prefix them.
                // For safety, let's prefix if they don't look unique, but trust agent for now.
                workflow.agents.push(node.clone());
            }
        } else {
            return Err("Workflow config not found".to_string());
        }

        // Mutate DAG
        if let Some(mut dag) = self.dag_store.get_mut(run_id) {
            // A. Get existing children of the Parent (Dependents)
            // e.g. Parent -> Child1. We want Parent -> [NewNodes] -> Child1
            let existing_dependents = dag.get_children(parent_id);

            // B. Add New Nodes & Edges from Parent
            for node in &req.new_nodes {
                dag.add_node(node.id.clone()).map_err(|e| e.to_string())?;

                // Parent -> New Node (so New Node can see Parent's context)
                dag.add_edge(parent_id.to_string(), node.id.clone()).map_err(|e| e.to_string())?;

                // C. Rewire Dependents
                // If strategy is Child (default), new nodes block the original dependents.
                if req.strategy == DelegationStrategy::Child {
                    for dep in &existing_dependents {
                        // Add edge New Node -> Dependent
                        dag.add_edge(node.id.clone(), dep.clone()).map_err(|e| e.to_string())?;
                    }
                }
            }

            // D. Remove Old Edges (Parent -> Dependents)
            // Only if we successfully inserted the intermediaries.
            if req.strategy == DelegationStrategy::Child {
                for dep in &existing_dependents {
                    // It's okay if this fails (edge might not exist), but logic says it should.
                    let _ = dag.remove_edge(parent_id, dep);
                }
            }

            // Validate Cycle (Rollback is hard, so we just check and error if bad)
            if dag.topological_sort().is_err() {
                // If we broke the graph, we are in trouble.
                // In production, we'd clone DAG, test mutation, then apply.
                // For prototype, we fail the run.
                return Err("Delegation created a cycle".to_string());
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
            }
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
        } // Drop write lock before persisting

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

    /// Prepare invocation payload with signature routing AND artifact context
    pub async fn prepare_invocation_payload(
        &self,
        run_id: &str,
        agent_id: &str,
    ) -> Result<InvocationPayload, String> {
        // Get the workflow and agent config
        let state = self
            .runtime_states
            .get(run_id)
            .ok_or_else(|| "Run not found".to_string())?;

        let workflow = self
            .workflows
            .get(&state.workflow_id)
            .ok_or_else(|| "Workflow not found".to_string())?;

        // Find the agent config
        let agent_config = workflow
            .agents
            .iter()
            .find(|a| a.id == agent_id)
            .ok_or_else(|| format!("Agent {} not found", agent_id))?;

        // 1. Fetch Parent Signatures (Reasoning Continuity)
        let parent_signature = if !agent_config.depends_on.is_empty() {
            agent_config
                .depends_on
                .iter()
                .find_map(|parent_id| self.get_thought_signature(run_id, parent_id))
        } else {
            None
        };

        // 2. Fetch Parent Artifacts (Data Context)
        let mut context_prompt_appendix = String::new();
        let mut input_data_map = serde_json::Map::new();

        if !agent_config.depends_on.is_empty() {
            if let Some(client) = &self.redis_client {
                // We use a separate connection for this fetch to avoid borrowing issues
                match client.get_async_connection().await {
                    Ok(mut con) => {
                        for parent_id in &agent_config.depends_on {
                            let key = format!("run:{}:agent:{}:output", run_id, parent_id);
                            
                            // Try to get artifact from Redis
                            let data: Option<String> = con.get(&key).await.unwrap_or(None);

                            if let Some(json_str) = data {
                                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&json_str) {
                                    
                                    // Add to structured input data
                                    input_data_map.insert(parent_id.clone(), val.clone());

                                    // Extract text result for the prompt
                                    let content = val.get("result")
                                        .and_then(|v| v.as_str())
                                        .or_else(|| val.get("output").and_then(|v| v.as_str()))
                                        .unwrap_or("No text output");

                                    context_prompt_appendix.push_str(&format!("\n\n=== CONTEXT FROM AGENT {} ===\n{}\n", parent_id, content));
                                }
                            }
                        }
                    },
                    Err(e) => tracing::warn!("Could not connect to Redis for context fetching: {}", e),
                }
            }
        }

        // 3. Construct Final Prompt
        let mut final_prompt = agent_config.prompt.clone();
        if !context_prompt_appendix.is_empty() {
            final_prompt.push_str(&context_prompt_appendix);
        }

        // Get cached content if available
        let cached_content_id = self.cache_resources.get(run_id).map(|c| (*c).clone());

        // Determine model variant
        // let model = match agent_config.model {
        //     ModelVariant::GeminiFlash => "gemini-2.5-flash",
        //     ModelVariant::GeminiPro => "gemini-2.5-flash-lite",
        //     ModelVariant::GeminiDeepThink => "gemini-2.5-flash",
        // }
        // .to_string();

        // let thinking_level = if matches!(agent_config.model, ModelVariant::GeminiDeepThink) {
        //     Some(5)
        // } else {
        //     None
        // };
        let model_string = match &agent_config.model {
            ModelVariant::Fast => "fast".to_string(),
            ModelVariant::Reasoning => "reasoning".to_string(),
            ModelVariant::Thinking => "thinking".to_string(),
            ModelVariant::Custom(s) => s.clone(),
        };

        // 2. Logic is now based on Semantic Type, not string matching!
        let thinking_level = if matches!(agent_config.model, ModelVariant::Thinking) {
            Some(5) // Default budget for Thinking tier
        } else {
            None
        };



        Ok(InvocationPayload {
            run_id: run_id.to_string(),
            agent_id: agent_id.to_string(),
            model: model_string,
            prompt: final_prompt, // Updated with context
            input_data: serde_json::Value::Object(input_data_map), // Updated with structured data
            parent_signature,
            cached_content_id,
            thinking_level,
            file_paths: Vec::new(),
            tools: agent_config.tools.clone(),
        })
    }

    pub fn set_run_status(&self, run_id: &str, status: RuntimeStatus) {
        if let Some(mut state) = self.runtime_states.get_mut(run_id) {
            state.status = status;
            // Trigger async persistence here
        }
    }
    
    /// Returns the current topology (nodes and edges) for visualization
    pub fn get_topology_snapshot(&self, run_id: &str) -> Option<serde_json::Value> {
        if let Some(dag) = self.dag_store.get(run_id) {
            let edges = dag.export_edges();
            let nodes = dag.export_nodes();
            
            // Convert to the JSON structure the frontend expects
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