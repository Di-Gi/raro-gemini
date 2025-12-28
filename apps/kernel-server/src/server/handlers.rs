use axum::{
    extract::{Path, State, Json},
    http::StatusCode,
};
use serde_json::json;
use std::sync::Arc;

use crate::models::*;
use crate::runtime::RARORuntime;

#[derive(serde::Serialize)]
pub struct HealthResponse {
    status: String,
    message: String,
}

pub async fn health() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok".to_string(),
        message: "RARO Kernel Server is running".to_string(),
    })
}

pub async fn start_workflow(
    State(runtime): State<Arc<RARORuntime>>,
    Json(config): Json<WorkflowConfig>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match runtime.start_workflow(config) {
        Ok(run_id) => Ok(Json(json!({
            "success": true,
            "run_id": run_id
        }))),
        Err(e) => {
            tracing::error!("Failed to start workflow: {}", e);
            Err(StatusCode::BAD_REQUEST)
        }
    }
}

pub async fn get_runtime_state(
    State(runtime): State<Arc<RARORuntime>>,
) -> Result<Json<RuntimeState>, StatusCode> {
    // For demo, return empty state
    // In real implementation, this would accept run_id as query param
    Err(StatusCode::NOT_IMPLEMENTED)
}

pub async fn invoke_agent(
    State(runtime): State<Arc<RARORuntime>>,
    Path(agent_id): Path<String>,
    Json(invocation): Json<AgentInvocation>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    tracing::info!("Invoking agent: {}", agent_id);
    Ok(Json(json!({
        "success": true,
        "agent_id": agent_id,
        "invocation_id": invocation.id
    })))
}

pub async fn get_signatures(
    State(runtime): State<Arc<RARORuntime>>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    Ok(Json(json!({
        "signatures": {}
    })))
}
