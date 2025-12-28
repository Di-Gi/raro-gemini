use crate::dag::DAG;
use crate::models::*;
use chrono::Utc;
use dashmap::DashMap;
use std::sync::Arc;
use uuid::Uuid;

pub struct RARORuntime {
    workflows: DashMap<String, WorkflowConfig>,
    runtime_states: DashMap<String, RuntimeState>,
    thought_signatures: DashMap<String, ThoughtSignatureStore>,
}

impl RARORuntime {
    pub fn new() -> Self {
        RARORuntime {
            workflows: DashMap::new(),
            runtime_states: DashMap::new(),
            thought_signatures: DashMap::new(),
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

        // Store workflow
        self.workflows.insert(workflow_id.clone(), config);

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
}

impl Clone for RuntimeState {
    fn clone(&self) -> Self {
        RuntimeState {
            run_id: self.run_id.clone(),
            workflow_id: self.workflow_id.clone(),
            status: self.status.clone(),
            active_agents: self.active_agents.clone(),
            completed_agents: self.completed_agents.clone(),
            failed_agents: self.failed_agents.clone(),
            invocations: self.invocations.clone(),
            total_tokens_used: self.total_tokens_used,
            start_time: self.start_time.clone(),
            end_time: self.end_time.clone(),
        }
    }
}

impl Clone for ThoughtSignatureStore {
    fn clone(&self) -> Self {
        ThoughtSignatureStore {
            signatures: self.signatures.clone(),
        }
    }
}
