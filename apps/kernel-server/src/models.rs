use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum ModelVariant {
    #[serde(rename = "gemini-3-flash")]
    GeminiFlash,
    #[serde(rename = "gemini-3-pro")]
    GeminiPro,
    #[serde(rename = "gemini-3-deep-think")]
    GeminiDeepThink,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentNodeConfig {
    pub id: String,
    pub role: AgentRole,
    pub model: ModelVariant,
    pub tools: Vec<String>,
    pub input_schema: serde_json::Value,
    pub output_schema: serde_json::Value,
    pub cache_policy: String,
    pub depends_on: Vec<String>,
    pub prompt: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowConfig {
    pub id: String,
    pub name: String,
    pub agents: Vec<AgentNodeConfig>,
    pub max_token_budget: usize,
    pub timeout_ms: u64,
}

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
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum InvocationStatus {
    Pending,
    Running,
    Success,
    Failed,
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
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ThoughtSignatureStore {
    pub signatures: HashMap<String, String>,
}