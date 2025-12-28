use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Metrics {
    pub p99_latency_ms: u64,
    pub cache_hit_percentage: f64,
    pub cost_per_run: f64,
    pub total_errors: usize,
    pub average_tokens_per_invocation: usize,
}

impl Default for Metrics {
    fn default() -> Self {
        Metrics {
            p99_latency_ms: 0,
            cache_hit_percentage: 0.0,
            cost_per_run: 0.0,
            total_errors: 0,
            average_tokens_per_invocation: 0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceEvent {
    pub timestamp: String,
    pub level: String,
    pub message: String,
    pub agent_id: Option<String>,
    pub metadata: serde_json::Value,
}
