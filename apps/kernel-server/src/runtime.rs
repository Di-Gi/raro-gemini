use crate::dag::DAG;
use crate::models::*;
use chrono::Utc;
use dashmap::DashMap;
use std::sync::Arc;
use uuid::Uuid;
use serde::{Deserialize, Serialize};

/// Payload for invoking an agent with signature routing and caching
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvocationPayload {
    pub agent_id: String,
    pub model: String,
    pub prompt: String,
    pub parent_signature: Option<String>,
    pub cached_content_id: Option<String>,
    pub thinking_level: Option<i32>,
    pub file_paths: Vec<String>,
    pub tools: Vec<String>,
}

pub struct RARORuntime {
    workflows: DashMap<String, WorkflowConfig>,
    runtime_states: DashMap<String, RuntimeState>,
    thought_signatures: DashMap<String, ThoughtSignatureStore>,
    dag_store: DashMap<String, DAG>,
    cache_resources: DashMap<String, String>, // run_id -> cached_content_id
}

impl RARORuntime {
    pub fn new() -> Self {
        RARORuntime {
            workflows: DashMap::new(),
            runtime_states: DashMap::new(),
            thought_signatures: DashMap::new(),
            dag_store: DashMap::new(),
            cache_resources: DashMap::new(),
        }
    }

    /// Start a new workflow execution
    pub fn start_workflow(&self, config: WorkflowConfig) -> Result<String, String> {
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

        // Store workflow and DAG
        self.workflows.insert(workflow_id.clone(), config.clone());
        self.dag_store.insert(run_id.clone(), dag);

        // Initialize runtime state
        let state = RuntimeState {
            run_id: run_id.clone(),
            workflow_id: workflow_id.clone(),
            status: RuntimeStatus::Idle,
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

        Ok(run_id)
    }

    /// Get current runtime state
    pub fn get_state(&self, run_id: &str) -> Option<RuntimeState> {
        self.runtime_states.get(run_id).map(|r| r.clone())
    }

    /// Record an agent invocation
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
        self.thought_signatures.get(run_id).map(|s| s.clone())
    }

    /// Prepare invocation payload with signature routing
    /// This implements the core RARO pattern: passing parent's signature to child
    pub fn prepare_invocation_payload(
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

        // Get the DAG to find dependencies
        let dag = self
            .dag_store
            .get(run_id)
            .ok_or_else(|| "DAG not found for run".to_string())?;

        // Fetch parent's signature for continuity
        // The immediate parent's signature becomes this agent's input context
        let parent_signature = if !agent_config.depends_on.is_empty() {
            agent_config
                .depends_on
                .iter()
                .find_map(|parent_id| self.get_thought_signature(run_id, parent_id))
        } else {
            None
        };

        // Get cached content if available
        let cached_content_id = self.cache_resources.get(run_id).map(|c| c.clone());

        // Determine model variant and thinking level
        let model = match agent_config.model {
            ModelVariant::GeminiFlash => "gemini-3-flash",
            ModelVariant::GeminiPro => "gemini-3-pro",
            ModelVariant::GeminiDeepThink => "gemini-3-deep-think",
        }
        .to_string();

        // Deep Think agents get thinking budget (default 5, can be 1-10)
        let thinking_level = if matches!(agent_config.model, ModelVariant::GeminiDeepThink) {
            Some(5) // Default thinking depth for deep-think model
        } else {
            None
        };

        Ok(InvocationPayload {
            agent_id: agent_id.to_string(),
            model,
            prompt: agent_config.prompt.clone(),
            parent_signature,
            cached_content_id,
            thinking_level,
            file_paths: Vec::new(), // Set by upstream (e.g., from research project)
            tools: agent_config.tools.clone(),
        })
    }

    /// Store a cache resource ID for this run (e.g., from orchestrator's PDF uploads)
    pub fn set_cache_resource(&self, run_id: &str, cached_content_id: String) -> Result<(), String> {
        self.cache_resources.insert(run_id.to_string(), cached_content_id);
        Ok(())
    }

    /// Retrieve the cache resource ID for cost-optimized subsequent invocations
    pub fn get_cache_resource(&self, run_id: &str) -> Option<String> {
        self.cache_resources.get(run_id).map(|c| c.clone())
    }

    /// Get the DAG for a workflow run (for debugging/visualization)
    pub fn has_dag(&self, run_id: &str) -> bool {
        self.dag_store.contains_key(run_id)
    }
}
