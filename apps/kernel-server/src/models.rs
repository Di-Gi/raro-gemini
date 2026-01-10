// [[RARO]]/apps/kernel-server/src/models.rs
// Purpose: Core data models. Updated with attached_files support for RFS.
// Architecture: Shared Data Layer
// Dependencies: Serde

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "lowercase")] // Serializes to "fast", "reasoning", etc.
pub enum ModelVariant {
    Fast,       // Cheap, quick
    Reasoning,  // Standard "Pro" level
    Thinking,   // Deep think / o1-style
    
    // Allow an escape hatch for specific IDs if absolutely needed
    #[serde(untagged)] 
    Custom(String), 
}
impl Default for ModelVariant {
    fn default() -> Self {
        ModelVariant::Fast
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum AgentRole {
    #[serde(rename = "orchestrator")]
    Orchestrator,
    #[serde(rename = "worker")]
    Worker,
    #[serde(rename = "observer")]
    Observer,
}

/// Configuration for a single agent node.
/// Used in both static workflow definitions and dynamic delegations.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentNodeConfig {
    pub id: String,
    pub role: AgentRole,
    pub model: ModelVariant,
    pub tools: Vec<String>,
    #[serde(default)]
    pub input_schema: serde_json::Value,
    #[serde(default)]
    pub output_schema: serde_json::Value,
    #[serde(default = "default_cache_policy")]
    pub cache_policy: String,
    // Dependencies relative to the context (Workflow or Subgraph)
    #[serde(default)]
    pub depends_on: Vec<String>,
    pub prompt: String,
    pub position: Option<Position>,
    #[serde(default)]
    pub accepts_directive: bool,
    #[serde(default)]
    pub user_directive: String,  // Runtime task from operator

    // [[NEW FIELD]]
    #[serde(default)]
    pub allow_delegation: bool,
}

fn default_cache_policy() -> String {
    "ephemeral".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Position {
    pub x: f64,
    pub y: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowConfig {
    pub id: String,
    pub name: String,
    pub agents: Vec<AgentNodeConfig>,
    pub max_token_budget: usize,
    pub timeout_ms: u64,
    
    // === RFS Integration ===
    // List of filenames from the Library to attach to this run's context
    #[serde(default)]
    pub attached_files: Vec<String>, 
}

// === NEW: DYNAMIC GRAPH STRUCTURES ===

/// A request from an active agent to spawn new sub-agents.
/// This supports Flow B (Recursive Fork).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DelegationRequest {
    /// The intent/reason for this delegation (for logging/patterns)
    pub reason: String,
    
    /// The new nodes to inject into the graph
    pub new_nodes: Vec<AgentNodeConfig>,
    
    /// How these nodes relate to the delegating agent.
    /// Default: "child" (Parent -> New Nodes -> Original Children)
    #[serde(default = "default_strategy")]
    pub strategy: DelegationStrategy,
}

fn default_strategy() -> DelegationStrategy {
    DelegationStrategy::Child
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum DelegationStrategy {
    /// New nodes become children of the current node. 
    /// Current node's original children are re-parented to these new nodes.
    Child,
    /// New nodes are siblings (parallel execution), not blocking dependent flow.
    Sibling,
}

/// The standardized response from the Remote Agent Service.
/// Moved here from runtime.rs to centralize the contract.
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
    pub cached_content_id: Option<String>, 
    
    // === NEW: The payload for dynamic graph changes ===
    pub delegation: Option<DelegationRequest>,
}

// === RUNTIME STATE ===

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentInvocation {
    pub id: String,
    pub agent_id: String,
    pub model_variant: ModelVariant,
    pub thought_signature: Option<String>,
    pub tools_used: Vec<String>,
    pub tokens_used: usize,
    pub latency_ms: u64,
    pub status: InvocationStatus,
    pub timestamp: String,
    pub artifact_id: Option<String>,
    pub error_message: Option<String>, 
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum InvocationStatus {
    Pending,
    Running,
    Success,
    Failed,
    Paused, // Added for Human-in-the-Loop or Delegation pauses
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeState {
    pub run_id: String,
    pub workflow_id: String,
    pub status: RuntimeStatus,
    pub active_agents: Vec<String>,
    pub completed_agents: Vec<String>,
    pub failed_agents: Vec<String>,
    pub invocations: Vec<AgentInvocation>,
    pub total_tokens_used: usize,
    pub start_time: String,
    pub end_time: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum RuntimeStatus {
    Idle,
    Running,
    Completed,
    Failed,
    AwaitingApproval, // Added for Flow C (Safety)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ThoughtSignatureStore {
    pub signatures: HashMap<String, String>,
}