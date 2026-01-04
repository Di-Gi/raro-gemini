// [[RARO]]/apps/kernel-server/src/registry.rs
// Purpose: Pattern Registry. Stores active Event-Condition-Action rules.
// Architecture: Cortex Layer
// Dependencies: DashMap, Models

use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use std::fs; // Import FS
use crate::models::AgentNodeConfig;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Pattern {
    pub id: String,
    pub name: String,
    pub trigger_event: String, 
    pub condition: String,
    pub action: PatternAction,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum PatternAction {
    // Serde will automatically handle the JSON structure {"Interrupt": {"reason": "..."}}
    Interrupt { reason: String },
    RequestApproval { reason: String },
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
        
        // CHANGED: Load from file instead of hardcoded function
        registry.load_patterns_from_disk("config/cortex_patterns.json");
        
        registry
    }

    pub fn register(&self, pattern: Pattern) {
        tracing::info!("Registering Safety Pattern: [{}] {}", pattern.id, pattern.name);
        self.patterns.insert(pattern.id.clone(), pattern);
    }

    pub fn get_patterns_for_trigger(&self, event_type: &str) -> Vec<Pattern> {
        self.patterns
            .iter()
            .filter(|p| {
                // Loose string matching against EventType enum output (e.g., "ToolCall")
                p.trigger_event == event_type || 
                // Handle Rust enum debug formatting which might be "ToolCall" or "EventType::ToolCall"
                event_type.contains(&p.trigger_event) 
            })
            .map(|p| p.value().clone())
            .collect()
    }

    /// NEW: Hydration Logic
    fn load_patterns_from_disk(&self, path: &str) {
        match fs::read_to_string(path) {
            Ok(data) => {
                match serde_json::from_str::<Vec<Pattern>>(&data) {
                    Ok(patterns) => {
                        for p in patterns {
                            self.register(p);
                        }
                    },
                    Err(e) => tracing::error!("Failed to parse patterns file: {}", e),
                }
            },
            Err(_) => {
                tracing::warn!("Pattern file not found at '{}'. Loading fallback defaults.", path);
                self.register_fallback_patterns();
            }
        }
    }

    /// Keep fallbacks just in case file is missing
    fn register_fallback_patterns(&self) {
        self.register(Pattern {
            id: "guard_fs_delete".to_string(),
            name: "Prevent File Deletion (Fallback)".to_string(),
            trigger_event: "ToolCall".to_string(),
            condition: "fs_delete".to_string(), 
            action: PatternAction::Interrupt { 
                reason: "Safety Violation: File deletion is prohibited.".to_string() 
            },
        });
    }
}