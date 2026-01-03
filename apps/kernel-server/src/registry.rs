// [[RARO]]/apps/kernel-server/src/registry.rs
// Purpose: Pattern Registry. Stores active Event-Condition-Action rules.
// Architecture: Cortex Layer
// Dependencies: DashMap, Models

use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use crate::models::AgentNodeConfig;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Pattern {
    pub id: String,
    pub name: String,
    /// The event type that wakes this pattern up (matched against EventType debug string)
    pub trigger_event: String, 
    /// JSONPath-like filter string (e.g., "$.payload.tool == 'fs_delete'")
    pub condition: String,
    pub action: PatternAction,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum PatternAction {
    /// Stop the agent immediately
    Interrupt { reason: String },
    /// Pause and ask for human approval
    RequestApproval { reason: String },
    /// Spawn a "fixer" agent to handle the error (Flow C / Self-Healing)
    SpawnAgent { config: AgentNodeConfig },
}

pub struct PatternRegistry {
    patterns: DashMap<String, Pattern>,
}

impl PatternRegistry {
    pub fn new() -> Self {
        let registry = Self {
            patterns: DashMap::new(),
        };
        
        // Initialize Default Safety Patterns (Proof of Concept)
        registry.register_default_patterns();
        
        registry
    }

    pub fn register(&self, pattern: Pattern) {
        self.patterns.insert(pattern.id.clone(), pattern);
    }

    /// Retrieve all patterns listening for a specific event type
    pub fn get_patterns_for_trigger(&self, event_type: &str) -> Vec<Pattern> {
        self.patterns
            .iter()
            .filter(|p| p.trigger_event == event_type)
            .map(|p| p.value().clone())
            .collect()
    }

    fn register_default_patterns(&self) {
        // 1. Safety Guard: Prevent file deletion
        // This corresponds to Atlas 'prevent_destructive_shell'
        self.register(Pattern {
            id: "guard_fs_delete".to_string(),
            name: "Prevent File Deletion".to_string(),
            trigger_event: "ToolCall".to_string(),
            condition: "fs_delete".to_string(), // Simplified matching for Phase 3 MVP
            action: PatternAction::Interrupt { 
                reason: "Safety Violation: File deletion is prohibited by system policy.".to_string() 
            },
        });

        // 2. Infinite Loop Detector (Heuristic)
        // If an agent fails 3 times, stop the run.
        // Note: Real implementation requires stateful counting, this is a stateless example.
        self.register(Pattern {
            id: "guard_max_failures".to_string(),
            name: "Max Failure Guard".to_string(),
            trigger_event: "AgentFailed".to_string(),
            condition: "*".to_string(), 
            action: PatternAction::RequestApproval { 
                reason: "Agent failed. Requesting human intervention before retry.".to_string() 
            },
        });
    }
}