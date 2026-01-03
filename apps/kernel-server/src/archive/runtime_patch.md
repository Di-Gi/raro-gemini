This is **Phase 2: The Splicer**.

We are upgrading the Kernel from a **Static List Executor** to a **Dynamic Graph Engine**.

This involves two major refactors:
1.  **`dag.rs`**: Adding structural mutation methods (`remove_edge`, `get_children`) to allow graph surgery.
2.  **`runtime.rs`**: Completely rewriting the execution loop. Instead of iterating a fixed vector, it now:
    *   Maintains a dynamic `queue`.
    *   Handles `DelegationRequest` payloads.
    *   Performs "Graph Rewiring" (Splicing) when an agent forks.
    *   Re-calculates the execution path in real-time.

---

### 1. Update `apps/kernel-server/src/dag.rs`

We add the necessary graph theory primitives to safely modify the topology during execution.

```rust
// [[RARO]]/apps/kernel-server/src/dag.rs
// Purpose: DAG Data Structure. Updated with mutation methods for dynamic graph splicing.
// Architecture: Core Data Structure
// Dependencies: std, thiserror

use std::collections::{HashMap, HashSet, VecDeque};
use thiserror::Error;

#[derive(Error, Debug)]
pub enum DAGError {
    #[error("Cycle detected in DAG")]
    CycleDetected,
    #[error("Invalid node: {0}")]
    InvalidNode(String),
    #[error("Dependency not found: {0}")]
    DependencyNotFound(String),
    #[error("Edge not found: {0} -> {1}")]
    EdgeNotFound(String, String),
}

#[derive(Clone, Debug)] // Added Clone/Debug for easier state management
pub struct DAG {
    nodes: HashSet<String>,
    edges: HashMap<String, Vec<String>>, // Adjacency list: Source -> [Targets]
}

impl DAG {
    pub fn new() -> Self {
        DAG {
            nodes: HashSet::new(),
            edges: HashMap::new(),
        }
    }

    /// Add a node to the DAG
    pub fn add_node(&mut self, node_id: String) -> Result<(), DAGError> {
        self.nodes.insert(node_id);
        Ok(())
    }

    /// Add an edge from source to target
    pub fn add_edge(&mut self, from: String, to: String) -> Result<(), DAGError> {
        if !self.nodes.contains(&from) {
            return Err(DAGError::InvalidNode(from));
        }
        if !self.nodes.contains(&to) {
            return Err(DAGError::InvalidNode(to));
        }

        // Check for cycle before adding
        if self.would_create_cycle(&from, &to) {
            return Err(DAGError::CycleDetected);
        }

        self.edges.entry(from).or_insert_with(Vec::new).push(to);
        Ok(())
    }

    /// Remove an edge from source to target (Required for splicing)
    pub fn remove_edge(&mut self, from: &str, to: &str) -> Result<(), DAGError> {
        if let Some(targets) = self.edges.get_mut(from) {
            if let Some(pos) = targets.iter().position(|x| x == to) {
                targets.remove(pos);
                return Ok(());
            }
        }
        Err(DAGError::EdgeNotFound(from.to_string(), to.to_string()))
    }

    /// Get all direct children (dependents) of a node
    pub fn get_children(&self, node_id: &str) -> Vec<String> {
        self.edges.get(node_id).cloned().unwrap_or_default()
    }

    /// Check if adding edge would create a cycle
    fn would_create_cycle(&self, from: &str, to: &str) -> bool {
        // DFS from 'to' to see if we can reach 'from'
        let mut visited = HashSet::new();
        self.has_path_dfs(to, from, &mut visited)
    }

    fn has_path_dfs(
        &self,
        current: &str,
        target: &str,
        visited: &mut HashSet<String>,
    ) -> bool {
        if current == target {
            return true;
        }

        if visited.contains(current) {
            return false;
        }

        visited.insert(current.to_string());

        if let Some(neighbors) = self.edges.get(current) {
            for neighbor in neighbors {
                if self.has_path_dfs(neighbor, target, visited) {
                    return true;
                }
            }
        }

        false
    }

    /// Compute topological order for execution
    /// This is now used dynamically to recalculate the path after mutation
    pub fn topological_sort(&self) -> Result<Vec<String>, DAGError> {
        let mut in_degree: HashMap<String, usize> = self.nodes.iter().map(|n| (n.clone(), 0)).collect();

        for neighbors in self.edges.values() {
            for neighbor in neighbors {
                *in_degree.get_mut(neighbor).unwrap() += 1;
            }
        }

        let mut queue: VecDeque<String> = in_degree
            .iter()
            .filter(|(_, &degree)| degree == 0)
            .map(|(node, _)| node.clone())
            .collect();

        let mut result = Vec::new();

        while let Some(node) = queue.pop_front() {
            result.push(node.clone());

            if let Some(neighbors) = self.edges.get(&node) {
                for neighbor in neighbors {
                    let degree = in_degree.get_mut(neighbor).unwrap();
                    *degree -= 1;
                    if *degree == 0 {
                        queue.push_back(neighbor.clone());
                    }
                }
            }
        }

        if result.len() != self.nodes.len() {
            return Err(DAGError::CycleDetected);
        }

        Ok(result)
    }

    /// Get dependencies for a given node (Reverse lookup)
    pub fn get_dependencies(&self, node_id: &str) -> Vec<String> {
        let mut deps = Vec::new();
        for (source, targets) in &self.edges {
            if targets.contains(&node_id.to_string()) {
                deps.push(source.clone());
            }
        }
        deps
    }
}
```

---

### 2. Update `apps/kernel-server/src/runtime.rs`

This is the logic core. We implement the **Event-Driven Execution Loop**.
Key changes:
*   `execute_dag`: Now uses a `VecDeque` and recalculates the plan if a `DelegationRequest` occurs.
*   `handle_delegation`: A new async helper that modifies the DAG, updates `WorkflowConfig`, and rewires edges.

```rust
// [[RARO]]/apps/kernel-server/src/runtime.rs
// Purpose: Core orchestration logic with Dynamic Graph Splicing.
// Architecture: Domain Logic Layer
// Dependencies: reqwest, dashmap, tokio, redis, serde_json

use crate::dag::DAG;
use crate::models::*;
use chrono::Utc;
use dashmap::DashMap;
use uuid::Uuid;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::env;
use redis::AsyncCommands;
use std::collections::VecDeque;

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

    // ... (Persistence methods: persist_state, rehydrate_from_redis remain unchanged) ...
    // Keeping existing persistence logic for brevity, assume persist_state/rehydrate are present as in Phase 1
    
    async fn persist_state(&self, run_id: &str) {
        if let Some(client) = &self.redis_client {
            if let Some(state) = self.runtime_states.get(run_id) {
                let state_key = format!("run:{}:state", run_id);
                let active_set_key = "sys:active_runs";
                
                match serde_json::to_string(&*state) {
                    Ok(json) => {
                        match client.get_async_connection().await {
                            Ok(mut con) => {
                                let _: redis::RedisResult<()> = con.set(&state_key, json).await;
                                if state.status == RuntimeStatus::Completed || state.status == RuntimeStatus::Failed {
                                    let _: redis::RedisResult<()> = con.srem(active_set_key, run_id).await;
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

    pub async fn rehydrate_from_redis(&self) {
        if let Some(client) = &self.redis_client {
            tracing::info!("Attempting to rehydrate state from Redis...");
            match client.get_async_connection().await {
                Ok(mut con) => {
                    let active_ids: Vec<String> = con.smembers("sys:active_runs").await.unwrap_or_default();
                    tracing::info!("Found {} active runs.", active_ids.len());

                    for run_id in active_ids {
                        let state_key = format!("run:{}:state", run_id);
                        let state_json: Option<String> = con.get(&state_key).await.unwrap_or(None);

                        if let Some(json) = state_json {
                            if let Ok(mut state) = serde_json::from_str::<RuntimeState>(&json) {
                                if state.status == RuntimeStatus::Running {
                                    state.status = RuntimeStatus::Failed; 
                                    state.invocations.push(AgentInvocation {
                                         id: Uuid::new_v4().to_string(),
                                         agent_id: "KERNEL".to_string(),
                                         model_variant: ModelVariant::GeminiPro,
                                         thought_signature: None,
                                         tools_used: vec![],
                                         tokens_used: 0,
                                         latency_ms: 0,
                                         status: InvocationStatus::Failed,
                                         timestamp: Utc::now().to_rfc3339(),
                                         artifact_id: None,
                                         error_message: Some("Kernel restarted unexpectedly.".to_string()),
                                    });
                                }
                                self.runtime_states.insert(run_id.clone(), state);
                            }
                        }
                    }
                },
                Err(e) => tracing::error!("Failed to connect to Redis for rehydration: {}", e),
            }
        }
    }

    // === EXECUTION LOGIC ===

    pub fn start_workflow(self: &Arc<Self>, config: WorkflowConfig) -> Result<String, String> {
        let mut dag = DAG::new();

        for agent in &config.agents {
            dag.add_node(agent.id.clone())
                .map_err(|e| format!("Failed to add node: {}", e))?;
        }

        for agent in &config.agents {
            for dep in &agent.depends_on {
                dag.add_edge(dep.clone(), agent.id.clone())
                    .map_err(|e| format!("Failed to add edge: {}", e))?;
            }
        }

        let _ = dag.topological_sort()
            .map_err(|e| format!("Invalid workflow: {}", e))?;

        let workflow_id = config.id.clone();
        let run_id = Uuid::new_v4().to_string();

        self.workflows.insert(workflow_id.clone(), config.clone());
        self.dag_store.insert(run_id.clone(), dag);

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
        self.thought_signatures.insert(run_id.clone(), ThoughtSignatureStore { signatures: Default::default() });

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
    async fn execute_dynamic_dag(&self, run_id: String) {
        tracing::info!("Starting DYNAMIC DAG execution for run_id: {}", run_id);

        // We use a simplified loop: Re-calculate topology, filter for uncompleted, take the next one.
        // In a real high-throughput system, we'd use a proper ready-queue, but re-calculating topology 
        // on a small graph (<100 nodes) is negligible and safer for consistency.
        loop {
            // 1. Check if Run is still valid/active
            let is_failed = self.runtime_states.get(&run_id)
                .map(|s| s.status == RuntimeStatus::Failed)
                .unwrap_or(true);
            
            if is_failed { break; }

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
                                "gemini-2.5-flash" => ModelVariant::GeminiFlash,
                                _ => ModelVariant::GeminiPro,
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
                    } else {
                        // Failure
                        let error = res.error.unwrap_or_else(|| "Unknown error".to_string());
                        self.fail_run(&run_id, &agent_id, &error).await;
                    }
                }
                Err(e) => {
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

    // ... (fail_run, update_agent_status, invoke_remote_agent, store_artifact, etc. match previous Phase 1 code) ...
    
    async fn fail_run(&self, run_id: &str, agent_id: &str, error: &str) {
        if let Some(mut state) = self.runtime_states.get_mut(run_id) {
            state.status = RuntimeStatus::Failed;
            state.end_time = Some(Utc::now().to_rfc3339());
            state.failed_agents.push(agent_id.to_string());
            state.active_agents.retain(|a| a != agent_id);
            
            state.invocations.push(AgentInvocation {
                id: Uuid::new_v4().to_string(),
                agent_id: agent_id.to_string(),
                model_variant: ModelVariant::GeminiPro,
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
         if changed { self.persist_state(run_id).await; }
    }

    async fn invoke_remote_agent(&self, payload: &InvocationPayload) -> Result<RemoteAgentResponse, reqwest::Error> {
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

    async fn store_artifact(&self, run_id: &str, agent_id: &str, output: &serde_json::Value) -> Option<String> {
        let artifact_id = format!("run:{}:agent:{}:output", run_id, agent_id);
        let json_str = serde_json::to_string(output).ok()?;

        if let Some(client) = &self.redis_client {
            if let Ok(mut con) = client.get_async_connection().await {
                let _: redis::RedisResult<()> = con.set_ex(&artifact_id, json_str, 3600).await;
                return Some(artifact_id);
            }
        }
        None
    }

    pub async fn record_invocation(&self, run_id: &str, invocation: AgentInvocation) -> Result<(), String> {
        {
            let mut state = self.runtime_states.get_mut(run_id).ok_or("Run not found")?;
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

    pub fn set_thought_signature(&self, run_id: &str, agent_id: &str, signature: String) -> Result<(), String> {
        let mut store = self.thought_signatures.get_mut(run_id).ok_or("Run not found")?;
        store.signatures.insert(agent_id.to_string(), signature);
        Ok(())
    }

    pub fn get_thought_signature(&self, run_id: &str, agent_id: &str) -> Option<String> {
        self.thought_signatures.get(run_id).and_then(|store| store.signatures.get(agent_id).cloned())
    }

    pub fn get_all_signatures(&self, run_id: &str) -> Option<ThoughtSignatureStore> {
        self.thought_signatures.get(run_id).map(|s| (*s).clone())
    }

    pub async fn prepare_invocation_payload(&self, run_id: &str, agent_id: &str) -> Result<InvocationPayload, String> {
        let state = self.runtime_states.get(run_id).ok_or("Run not found")?;
        let workflow = self.workflows.get(&state.workflow_id).ok_or("Workflow not found")?;
        
        let agent_config = workflow.agents.iter().find(|a| a.id == agent_id).ok_or(format!("Agent {} not found", agent_id))?;

        let parent_signature = if !agent_config.depends_on.is_empty() {
            agent_config.depends_on.iter().find_map(|parent_id| self.get_thought_signature(run_id, parent_id))
        } else { None };

        let mut context_prompt = String::new();
        let mut input_data = serde_json::Map::new();

        if !agent_config.depends_on.is_empty() {
            if let Some(client) = &self.redis_client {
                if let Ok(mut con) = client.get_async_connection().await {
                    for parent_id in &agent_config.depends_on {
                        let key = format!("run:{}:agent:{}:output", run_id, parent_id);
                        if let Ok(Some(data)) = con.get::<_, Option<String>>(&key).await {
                            if let Ok(val) = serde_json::from_str::<serde_json::Value>(&data) {
                                input_data.insert(parent_id.clone(), val.clone());
                                let txt = val.get("result").and_then(|v| v.as_str()).unwrap_or("");
                                context_prompt.push_str(&format!("\n\n=== CONTEXT {} ===\n{}\n", parent_id, txt));
                            }
                        }
                    }
                }
            }
        }

        let mut final_prompt = agent_config.prompt.clone();
        final_prompt.push_str(&context_prompt);

        let cached_content_id = self.cache_resources.get(run_id).map(|c| (*c).clone());
        let model = match agent_config.model {
            ModelVariant::GeminiFlash => "gemini-2.5-flash",
            ModelVariant::GeminiPro => "gemini-2.5-flash-lite",
            ModelVariant::GeminiDeepThink => "gemini-2.5-flash",
        }.to_string();

        let thinking_level = if matches!(agent_config.model, ModelVariant::GeminiDeepThink) { Some(5) } else { None };

        Ok(InvocationPayload {
            run_id: run_id.to_string(),
            agent_id: agent_id.to_string(),
            model,
            prompt: final_prompt,
            input_data: serde_json::Value::Object(input_data),
            parent_signature,
            cached_content_id,
            thinking_level,
            file_paths: vec![],
            tools: agent_config.tools.clone(),
        })
    }

    pub fn get_state(&self, run_id: &str) -> Option<RuntimeState> {
        self.runtime_states.get(run_id).map(|r| (*r).clone())
    }
}
```