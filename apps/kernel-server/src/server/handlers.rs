// [[RARO]]/apps/kernel-server/src/server/handlers.rs
// Purpose: API Handlers. updated to allow async spawning of workflows.
// Architecture: API Layer
// Dependencies: Axum, Runtime

use axum::{
    extract::{Path, State, Json, Query, Multipart, ws::{WebSocket, WebSocketUpgrade}},
    http::StatusCode,
    response::IntoResponse,
};
use serde_json::json;
use std::sync::Arc;
use futures::{sink::SinkExt, stream::StreamExt};
use axum::extract::ws::Message;
use redis::AsyncCommands;

use crate::models::*;
use crate::runtime::{RARORuntime, InvocationPayload};
use crate::fs_manager::WorkspaceInitializer; // Import the manager

use tokio::fs; // For listing library files

#[derive(serde::Deserialize)]
pub struct RunQuery {
    run_id: Option<String>,
}

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

// === NEW HANDLER: LIST LIBRARY FILES ===
// GET /runtime/library
pub async fn list_library_files() -> Result<Json<serde_json::Value>, StatusCode> {
    // Hardcoded path to the library volume
    let path = "/app/storage/library";
    // Check if directory exists, if not, create it
    if !std::path::Path::new(path).exists() {
        tracing::info!("Library directory missing. Creating: {}", path);
        fs::create_dir_all(path).await.map_err(|e| {
            tracing::error!("Failed to create library dir: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;
    }
    // Read dir
    let mut entries = fs::read_dir(path).await.map_err(|e| {
        tracing::error!("Failed to read library dir: {}", e);
        // If dir doesn't exist, try to create it silently or return empty
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    let mut files = Vec::new();

    while let Ok(Some(entry)) = entries.next_entry().await {
        if let Ok(file_type) = entry.file_type().await {
            if file_type.is_file() {
                if let Ok(name) = entry.file_name().into_string() {
                    // Filter out hidden files like .keep
                    if !name.starts_with('.') {
                        files.push(name);
                    }
                }
            }
        }
    }

    Ok(Json(serde_json::json!({
        "files": files
    })))
}

// === NEW HANDLER: UPLOAD FILE ===
// POST /runtime/library/upload
pub async fn upload_library_file(
    mut multipart: Multipart
) -> Result<Json<serde_json::Value>, StatusCode> {
    while let Some(field) = multipart.next_field().await.map_err(|_| StatusCode::BAD_REQUEST)? {
        let name = field.file_name().unwrap_or("unknown_file").to_string();
        
        // Read the bytes
        let data = field.bytes().await.map_err(|e| {
            tracing::error!("Failed to read upload bytes: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

        // Save using fs_manager
        if let Err(e) = WorkspaceInitializer::save_to_library(&name, &data).await {
            tracing::error!("Failed to write file to disk: {}", e);
            return Err(StatusCode::INTERNAL_SERVER_ERROR);
        }
    }

    Ok(Json(serde_json::json!({
        "success": true,
        "message": "Upload complete"
    })))
}

pub async fn start_workflow(
    State(runtime): State<Arc<RARORuntime>>,
    Json(config): Json<WorkflowConfig>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    // start_workflow now spawns the task internally and returns the run_id immediately
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

pub async fn resume_run(
    State(runtime): State<Arc<RARORuntime>>,
    Path(run_id): Path<String>
) -> StatusCode {
    // 1. Verify currently paused
    let is_paused = runtime.get_state(&run_id)
        .map(|s| s.status == RuntimeStatus::AwaitingApproval)
        .unwrap_or(false);

    if !is_paused {
        tracing::warn!("Resume called on non-paused run: {}", run_id);
        return StatusCode::BAD_REQUEST;
    }

    // 2. Flip to Running
    runtime.set_run_status(&run_id, RuntimeStatus::Running);

    // 3. RESPAWN THE EXECUTION LOOP
    // This is the critical piece. We fire the engine again.
    let rt_clone = runtime.clone();
    let rid_clone = run_id.clone();
    tokio::spawn(async move {
        rt_clone.execute_dynamic_dag(rid_clone).await;
    });

    // 4. Emit event for UI to update logs
    runtime.emit_event(crate::events::RuntimeEvent::new(
        &run_id,
        crate::events::EventType::SystemIntervention,
        None,
        serde_json::json!({ "action": "resume", "reason": "User approved execution" })
    ));

    tracing::info!("Run {} resumed by user", run_id);
    StatusCode::OK
}

pub async fn stop_run(
    State(runtime): State<Arc<RARORuntime>>, 
    Path(run_id): Path<String>
) -> StatusCode {
    runtime.fail_run(&run_id, "OPERATOR", "Manual Stop").await;
    StatusCode::OK
}


pub async fn get_runtime_state(
    State(runtime): State<Arc<RARORuntime>>,
    Query(query): Query<RunQuery>,
) -> Result<Json<RuntimeState>, StatusCode> {
    let run_id = query.run_id.ok_or(StatusCode::BAD_REQUEST)?;

    runtime
        .get_state(&run_id)
        .ok_or(StatusCode::NOT_FOUND)
        .map(Json)
}

pub async fn invoke_agent(
    State(runtime): State<Arc<RARORuntime>>,
    Path((run_id, agent_id)): Path<(String, String)>,
) -> Result<Json<InvocationPayload>, StatusCode> {
    tracing::info!("Preparing invocation for agent: {} in run: {}", agent_id, run_id);

    // CHANGE: Added .await
    runtime
        .prepare_invocation_payload(&run_id, &agent_id)
        .await 
        .map(Json)
        .map_err(|e| {
            tracing::error!("Failed to prepare invocation: {}", e);
            StatusCode::NOT_FOUND
        })
}

pub async fn get_signatures(
    State(runtime): State<Arc<RARORuntime>>,
    Query(query): Query<RunQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let run_id = query.run_id.ok_or(StatusCode::BAD_REQUEST)?;

    let signatures = runtime
        .get_all_signatures(&run_id)
        .ok_or(StatusCode::NOT_FOUND)?;

    Ok(Json(json!({
        "run_id": run_id,
        "signatures": signatures.signatures
    })))
}

pub async fn get_artifact(
    State(runtime): State<Arc<RARORuntime>>,
    Path((run_id, agent_id)): Path<(String, String)>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    tracing::debug!("Fetching artifact for run={}, agent={}", run_id, agent_id);

    let client = runtime
        .redis_client
        .as_ref()
        .ok_or(StatusCode::SERVICE_UNAVAILABLE)?;

    let key = format!("run:{}:agent:{}:output", run_id, agent_id);

    let mut con = client
        .get_async_connection()
        .await
        .map_err(|e| {
            tracing::error!("Redis connection failed: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    let data: String = con.get(&key).await.map_err(|e| {
        tracing::warn!("Artifact not found in Redis: {} ({})", key, e);
        StatusCode::NOT_FOUND
    })?;

    let json_val: serde_json::Value = serde_json::from_str(&data).map_err(|e| {
        tracing::error!("Failed to parse artifact JSON: {}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    Ok(Json(json_val))
}

pub async fn ws_runtime_stream(
    State(runtime): State<Arc<RARORuntime>>,
    Path(run_id): Path<String>,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_runtime_stream(socket, runtime, run_id))
}

async fn handle_runtime_stream(
    socket: WebSocket,
    runtime: Arc<RARORuntime>,
    run_id: String,
) {
    let (mut sender, mut receiver) = socket.split();

    // Wait briefly for state to be initialized if called immediately after start
    if runtime.get_state(&run_id).is_none() {
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }

    // Verify run exists
    if runtime.get_state(&run_id).is_none() {
        let _ = sender
            .send(Message::Text(
                json!({"error": "Run not found"}).to_string(),
            ))
            .await;
        return;
    }

    // Send initial state
    if let Some(state) = runtime.get_state(&run_id) {
        let _ = sender
            .send(Message::Text(
                serde_json::to_string(&json!({
                    "type": "state_update",
                    "state": state,
                    "timestamp": chrono::Utc::now().to_rfc3339()
                }))
                .unwrap(),
            ))
            .await;
    }

    // Stream updates
    let mut interval = tokio::time::interval(std::time::Duration::from_millis(250));

    loop {
        tokio::select! {
            // Check for client disconnect
            msg = receiver.next() => {
                if msg.is_none() {
                    tracing::info!("Client disconnected from runtime stream: {}", run_id);
                    break;
                }
            }

            // Send periodic updates
            _ = interval.tick() => {
                if let Some(state) = runtime.get_state(&run_id) {
                    
                    // === NEW: Fetch Topology ===
                    let topology = runtime.get_topology_snapshot(&run_id);
                    
                    let update = json!({
                        "type": "state_update",
                        "state": state,
                        "signatures": runtime.get_all_signatures(&run_id).map(|s| s.signatures),
                        "topology": topology, // <--- THE BRIDGE
                        "timestamp": chrono::Utc::now().to_rfc3339()
                    });

                    if sender.send(Message::Text(update.to_string())).await.is_err() {
                        tracing::info!("Failed to send state update, client disconnected");
                        break;
                    }
                    
                    // === FIX START ===
                    // Check for terminal states to auto-close connection
                    if state.status == RuntimeStatus::Completed || state.status == RuntimeStatus::Failed {
                        tracing::info!("Run {} reached terminal state: {:?}. Closing stream.", run_id, state.status);
                        
                        // Optional: Small delay to ensure client processes the final message before close frame
                        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                        
                        // Send a Close frame explicitly (optional, breaking loop also works)
                        let _ = sender.close().await;
                        break;
                    }
                    // === FIX END ===
                }
            }
        }
    }
}