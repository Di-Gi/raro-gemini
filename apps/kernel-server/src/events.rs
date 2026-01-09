// [[RARO]]/apps/kernel-server/src/events.rs
// Purpose: Event definitions for the Nervous System (Pattern Engine).
// Architecture: Domain Event Layer
// Dependencies: Serde, Chrono, Uuid

use serde::{Deserialize, Serialize};
use serde_json::Value;
use chrono::Utc;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EventType {
    /// A new agent node has been added to the DAG (static or dynamic)
    NodeCreated,
    /// An agent started execution
    AgentStarted,
    /// An agent completed successfully
    AgentCompleted,
    /// An agent failed
    AgentFailed,
    /// An agent requested a tool (e.g., shell, python)
    ToolCall,
    /// A human/system intervention
    SystemIntervention,
    /// Real-time intermediate log from agent (tool calls, thoughts)
    IntermediateLog,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeEvent {
    pub id: String,
    pub run_id: String,
    pub event_type: EventType,
    pub agent_id: Option<String>,
    pub timestamp: String,
    pub payload: Value,
}

impl RuntimeEvent {
    pub fn new(run_id: &str, event_type: EventType, agent_id: Option<String>, payload: Value) -> Self {
        Self {
            id: uuid::Uuid::new_v4().to_string(),
            run_id: run_id.to_string(),
            event_type,
            agent_id,
            timestamp: Utc::now().to_rfc3339(),
            payload,
        }
    }
}