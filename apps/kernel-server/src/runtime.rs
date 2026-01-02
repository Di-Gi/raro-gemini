// [[RARO]]/apps/kernel-server/src/runtime.rs
// Purpose: Core orchestration logic. Manages DAG execution, state updates, and remote agent invocation.
// Architecture: Domain Logic Layer
// Dependencies: reqwest, dashmap, tokio

use crate::dag::DAG;
use crate::models::*;
use chrono::Utc;
use dashmap::DashMap;
use uuid::Uuid;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::env;
use redis::AsyncCommands;

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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteAgentResponse {
    pub agent_id: String,
    pub success: bool,
    pub output: Option<serde_json::Value>,
    pub error: Option<String>,
    pub tokens_used: usize,
    pub thought_signature: Option<String>,
    pub input_tokens: usize,
    pub output_tokens: usize,
    pub cache_hit: bool,
    pub latency_ms: f64,
}

pub struct RARORuntime {
    workflows: DashMap<String, WorkflowConfig>,
    runtime_states: DashMap<String, RuntimeState>,
    thought_signatures: DashMap<String, ThoughtSignatureStore>,
    dag_store: DashMap<String, DAG>,
    cache_resources: DashMap<String, String>, // run_id -> cached_content_id
    http_client: reqwest::Client,
    pub redis_client: Option<redis::Client>,
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
                        tracing::warn!("Failed to create Redis client: {}. Artifacts will not be stored.", e);
                        None
                    }
                }
            }
            Err(_) => {
                tracing::warn!("REDIS_URL not set. Running without artifact storage.");
                None
            }
        };

        RARORuntime {
            workflows: DashMap::new(),
            runtime_states: DashMap::new(),
            thought_signatures: DashMap::new(),
            dag_store: DashMap::new(),
            cache_resources: DashMap::new(),
            http_client: reqwest::Client::new(),
            redis_client,
        }
    }

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
        let execution_order = dag
            .topological_sort()
            .map_err(|e| format!("Invalid workflow: {}", e))?;

        let workflow_id = config.id.clone();
        let run_id = Uuid::new_v4().to_string();

        // Store workflow and DAG
        self.workflows.insert(workflow_id.clone(), config.clone());
        self.dag_store.insert(run_id.clone(), dag);

        // Initialize runtime state
        let state = RuntimeState {
            run_id: run_id.clone(),
            workflow_id: workflow_id.clone(),
            status: RuntimeStatus::Running, // Mark as running immediately
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
        let order_clone = execution_order.clone();

        tokio::spawn(async move {
            runtime_clone.execute_dag(run_id_clone, order_clone).await;
        });

        Ok(run_id)
    }

    /// The core execution loop running in a background task
    async fn execute_dag(&self, run_id: String, execution_order: Vec<String>) {
        tracing::info!("Starting DAG execution for run_id: {}", run_id);

        for agent_id in execution_order {
            // 1. Prepare Invocation
            let payload_result = self.prepare_invocation_payload(&run_id, &agent_id).await;
            
            let payload = match payload_result {
                Ok(p) => p,
                Err(e) => {
                    tracing::error!("Failed to prepare payload for agent {}: {}", agent_id, e);
                    self.fail_run(&run_id, &agent_id, &e);
                    return;
                }
            };

            // 2. Update State: Agent Running
            self.update_agent_status(&run_id, &agent_id, InvocationStatus::Running);

            // 3. Invoke Remote Agent Service
            tracing::info!("Invoking remote agent: {}", agent_id);
            let response = self.invoke_remote_agent(&payload).await;

            // 4. Handle Response
            match response {
                Ok(res) => {
                    if res.success {
                        // Store signature
                        if let Some(sig) = res.thought_signature.clone() {
                            let _ = self.set_thought_signature(&run_id, &agent_id, sig);
                        }

                        // === FIX START: PREVENT ARTIFACT OVERWRITE ===
                        // Check if the agent service explicitly signaled that it stored the artifact
                        let artifact_id = if let Some(output_data) = &res.output {
                            let agent_stored_flag = output_data.get("artifact_stored")
                                .and_then(|v| v.as_bool())
                                .unwrap_or(false);

                            if agent_stored_flag {
                                tracing::debug!("Agent {} already stored artifact, skipping overwrite", agent_id);
                                Some(format!("run:{}:agent:{}:output", run_id, agent_id))
                            } else {
                                self.store_artifact(&run_id, &agent_id, output_data).await
                            }
                        } else {
                            None
                        };
                        // === FIX END ===

                        // Record metrics
                        let invocation_record = AgentInvocation {
                            id: Uuid::new_v4().to_string(),
                            agent_id: agent_id.clone(),
                            model_variant: match payload.model.as_str() {
                                "gemini-2.5-flash" => ModelVariant::GeminiFlash,
                                "gemini-2.5-flash" => ModelVariant::GeminiDeepThink,
                                _ => ModelVariant::GeminiPro,
                            },
                            thought_signature: res.thought_signature,
                            tools_used: payload.tools.clone(),
                            tokens_used: res.tokens_used,
                            latency_ms: res.latency_ms as u64,
                            status: InvocationStatus::Success,
                            timestamp: Utc::now().to_rfc3339(),
                            artifact_id,
                            // ADD THIS:
                            error_message: None, 
                        };

                        let _ = self.record_invocation(&run_id, invocation_record);
                        tracing::info!("Agent {} completed successfully", agent_id);
                    } else {
                        let error_msg = res.error.unwrap_or_else(|| "Unknown error".to_string());
                        tracing::error!("Agent {} failed: {}", agent_id, error_msg);
                        self.fail_run(&run_id, &agent_id, &error_msg);
                        return; // Stop execution on failure
                    }
                }
                Err(e) => {
                    tracing::error!("Network error invoking agent {}: {}", agent_id, e);
                    self.fail_run(&run_id, &agent_id, &e.to_string());
                    return;
                }
            }
        }

        // 5. Complete Run
        if let Some(mut state) = self.runtime_states.get_mut(&run_id) {
            state.status = RuntimeStatus::Completed;
            state.end_time = Some(Utc::now().to_rfc3339());
            tracing::info!("Workflow run {} completed successfully", run_id);
        }
    }

    /// Helper to fail the run and update state
    fn fail_run(&self, run_id: &str, agent_id: &str, error: &str) {
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
                model_variant: ModelVariant::GeminiPro, // Fallback
                thought_signature: None,
                tools_used: vec![],
                tokens_used: 0,
                latency_ms: 0,
                status: InvocationStatus::Failed,
                timestamp: Utc::now().to_rfc3339(),
                artifact_id: None,
                // ADD THIS: Capture the error string
                error_message: Some(error.to_string()), 
            });
        }
        tracing::error!("Run {} failed at agent {}: {}", run_id, agent_id, error);
    }

    /// Helper to update status to Running
    fn update_agent_status(&self, run_id: &str, agent_id: &str, status: InvocationStatus) {
         if let Some(mut state) = self.runtime_states.get_mut(run_id) {
            match status {
                InvocationStatus::Running => {
                     if !state.active_agents.contains(&agent_id.to_string()) {
                         state.active_agents.push(agent_id.to_string());
                     }
                },
                _ => {}
            }
         }
    }

    /// Perform the actual HTTP request to the Agent Service
    async fn invoke_remote_agent(&self, payload: &InvocationPayload) -> Result<RemoteAgentResponse, reqwest::Error> {
        // Resolve Agent Host from Env or Default
        let host = env::var("AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
        let port = env::var("AGENT_PORT").unwrap_or_else(|_| "8000".to_string());
        let scheme = if host.contains("localhost") || host == "127.0.0.1" { "http" } else { "http" };
        
        // Handle docker service names that don't need http:// prefix if already there, 
        // but robustly constructing url is safer.
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
    /// Returns the artifact_id if successful, None otherwise
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

    /// Record an agent invocation (Internal helper used by logic above)
    pub fn record_invocation(&self, run_id: &str, invocation: AgentInvocation) -> Result<(), String> {
        let mut state = self
            .runtime_states
            .get_mut(run_id)
            .ok_or_else(|| "Run not found".to_string())?;

        state.invocations.push(invocation.clone());
        state.total_tokens_used += invocation.tokens_used;

        match invocation.status {
            InvocationStatus::Running => {
                if !state.active_agents.contains(&invocation.agent_id) {
                    state.active_agents.push(invocation.agent_id);
                }
            }
            InvocationStatus::Success => {
                state.active_agents.retain(|a| a != &invocation.agent_id);
                state.completed_agents.push(invocation.agent_id);
            }
            InvocationStatus::Failed => {
                state.active_agents.retain(|a| a != &invocation.agent_id);
                state.failed_agents.push(invocation.agent_id);
            }
            _ => {}
        }

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

    /// Prepare invocation payload with signature routing
    /// Prepare invocation payload with signature routing AND artifact context
    /// CHANGED: Now async to allow Redis fetching
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
        let model = match agent_config.model {
            ModelVariant::GeminiFlash => "gemini-2.5-flash",
            ModelVariant::GeminiPro => "gemini-2.5-flash-lite",
            ModelVariant::GeminiDeepThink => "gemini-2.5-flash",
        }
        .to_string();

        let thinking_level = if matches!(agent_config.model, ModelVariant::GeminiDeepThink) {
            Some(5)
        } else {
            None
        };

        Ok(InvocationPayload {
            run_id: run_id.to_string(),
            agent_id: agent_id.to_string(),
            model,
            prompt: final_prompt, // Updated with context
            input_data: serde_json::Value::Object(input_data_map), // Updated with structured data
            parent_signature,
            cached_content_id,
            thinking_level,
            file_paths: Vec::new(),
            tools: agent_config.tools.clone(),
        })
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