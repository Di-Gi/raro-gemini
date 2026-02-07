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
use axum::body::Body;
use tokio_util::io::ReaderStream; // You might need: cargo add tokio-util
use redis::AsyncCommands;

use crate::models::*;
use crate::runtime::{RARORuntime, InvocationPayload};
use crate::fs_manager::{WorkspaceInitializer, ArtifactMetadata}; // Import the manager and metadata
use crate::security::ClientSession; // Import extractor

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

// GET /runtime/:run_id/files/:filename
pub async fn serve_session_file(
    Path((run_id, filename)): Path<(String, String)>,
) -> Result<impl IntoResponse, StatusCode> {
    // 1. Sanitize (Basic security)
    if filename.contains("..") || filename.starts_with("/") {
        return Err(StatusCode::FORBIDDEN);
    }

    // 2. Construct Path (Targeting the RFS Output directory)
    let file_path = format!("/app/storage/sessions/{}/output/{}", run_id, filename);
    let path = std::path::Path::new(&file_path);

    // 3. Verify Existence
    if !path.exists() {
        return Err(StatusCode::NOT_FOUND);
    }

    // 4. Open and Stream
    let file = match tokio::fs::File::open(path).await {
        Ok(file) => file,
        Err(_) => return Err(StatusCode::INTERNAL_SERVER_ERROR),
    };

    let stream = ReaderStream::new(file);
    let body = Body::from_stream(stream);

    // 5. Determine Content Type (Simple guess)
    let content_type = if filename.ends_with(".png") { "image/png" }
    else if filename.ends_with(".jpg") { "image/jpeg" }
    else if filename.ends_with(".csv") { "text/csv" }
    else if filename.ends_with(".txt") { "text/plain" }
    else { "application/octet-stream" };

    let headers = [
        ("Content-Type", content_type),
        ("Cache-Control", "public, max-age=3600"),
    ];

    Ok((headers, body))
}

// === NEW HANDLER: LIST LIBRARY FILES ===
// GET /runtime/library
pub async fn list_library_files(
    ClientSession(client_id): ClientSession // <--- Auto-extracted
) -> Result<Json<serde_json::Value>, StatusCode> {
    let files = WorkspaceInitializer::list_scoped_files(&client_id)
        .await
        .map_err(|e| {
            tracing::error!("Failed to list files for client {}: {}", client_id, e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    Ok(Json(serde_json::json!({ "files": files })))
}

// === NEW HANDLER: UPLOAD FILE ===
// POST /runtime/library/upload
pub async fn upload_library_file(
    ClientSession(client_id): ClientSession, // <--- Auto-extracted
    mut multipart: Multipart
) -> Result<Json<serde_json::Value>, StatusCode> {
    while let Some(field) = multipart.next_field().await.map_err(|_| StatusCode::BAD_REQUEST)? {
        let name = field.file_name().unwrap_or("unknown").to_string();
        let data = field.bytes().await.map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

        // Pass client_id to save function
        WorkspaceInitializer::save_to_library(&client_id, &name, &data)
            .await
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    }

    Ok(Json(serde_json::json!({ "success": true })))
}

// POST /runtime/start
pub async fn start_workflow(
    State(runtime): State<Arc<RARORuntime>>,
    ClientSession(client_id): ClientSession, // <--- Capture who is starting the run
    Json(config): Json<WorkflowConfig>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    // Pass client_id to runtime
    match runtime.start_workflow(config, &client_id) {
        Ok(run_id) => Ok(Json(json!({ "success": true, "run_id": run_id }))),
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
    // 0. Fail fast if structural integrity is lost (DAG missing from memory)
    if !runtime.has_dag(&run_id) {
        tracing::error!("Cannot resume run {}: DAG structure missing from memory.", run_id);
        return StatusCode::NOT_FOUND;
    }

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

    // Subscribe to event bus for real-time logs
    let mut bus_rx = runtime.event_bus.subscribe();

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

            // Forward real-time events from event bus
            Ok(event) = bus_rx.recv() => {
                // Only forward events for THIS run
                if event.run_id == run_id {
                    // Event whitelist: Forward time-critical events for real-time UI updates
                    // (Other events are still available via state polling)
                    let should_forward = matches!(
                        event.event_type,
                        crate::events::EventType::IntermediateLog |
                        crate::events::EventType::SystemIntervention |
                        crate::events::EventType::AgentStarted |
                        crate::events::EventType::AgentCompleted |
                        crate::events::EventType::AgentFailed
                    );

                    if should_forward {
                        let event_type_name = match event.event_type {
                            crate::events::EventType::IntermediateLog => "log_event",
                            crate::events::EventType::SystemIntervention => "intervention_event",
                            crate::events::EventType::AgentStarted => "agent_started",
                            crate::events::EventType::AgentCompleted => "agent_completed",
                            crate::events::EventType::AgentFailed => "agent_failed",
                            _ => "unknown_event",
                        };

                        let ws_msg = json!({
                            "type": event_type_name,
                            "agent_id": event.agent_id,
                            "payload": event.payload,
                            "timestamp": event.timestamp
                        });

                        if sender.send(Message::Text(ws_msg.to_string())).await.is_err() {
                            tracing::info!("Failed to send event, client disconnected");
                            break;
                        }
                    }
                }
            }
        }
    }
}

// === ARTIFACT STORAGE HANDLERS ===

/// GET /runtime/artifacts
/// Lists all artifact runs with their metadata
pub async fn list_all_artifacts() -> Result<Json<serde_json::Value>, StatusCode> {
    let runs = WorkspaceInitializer::list_artifact_runs()
        .await
        .map_err(|e| {
            tracing::error!("Failed to list artifact runs: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    let mut artifacts = Vec::new();

    for run_id in runs {
        if let Ok(metadata) = WorkspaceInitializer::get_artifact_metadata(&run_id).await {
            artifacts.push(json!({
                "run_id": run_id,
                "metadata": metadata
            }));
        }
    }

    Ok(Json(json!({ "artifacts": artifacts })))
}

/// GET /runtime/artifacts/:run_id
/// Gets metadata for a specific run's artifacts
pub async fn get_run_artifacts(
    Path(run_id): Path<String>,
) -> Result<Json<ArtifactMetadata>, StatusCode> {
    WorkspaceInitializer::get_artifact_metadata(&run_id)
        .await
        .map(Json)
        .map_err(|e| {
            tracing::warn!("Artifact metadata not found for run {}: {}", run_id, e);
            StatusCode::NOT_FOUND
        })
}

/// GET /runtime/artifacts/:run_id/files/:filename
/// Serves a specific artifact file from persistent storage
pub async fn serve_artifact_file(
    Path((run_id, filename)): Path<(String, String)>,
) -> Result<impl IntoResponse, StatusCode> {
    // 1. Sanitize (prevent path traversal)
    if filename.contains("..") || filename.starts_with("/") {
        tracing::warn!("Blocked suspicious artifact filename: {}", filename);
        return Err(StatusCode::FORBIDDEN);
    }

    // 2. Construct path to artifacts storage
    let file_path = format!("/app/storage/artifacts/{}/{}", run_id, filename);
    let path = std::path::Path::new(&file_path);

    // 3. Verify existence
    if !path.exists() {
        tracing::debug!("Artifact file not found: {}", file_path);
        return Err(StatusCode::NOT_FOUND);
    }

    // 4. Open and stream
    let file = tokio::fs::File::open(path).await
        .map_err(|e| {
            tracing::error!("Failed to open artifact file {}: {}", file_path, e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    let stream = ReaderStream::new(file);
    let body = Body::from_stream(stream);

    // 5. Determine content type
    let content_type = if filename.ends_with(".png") { "image/png" }
    else if filename.ends_with(".jpg") || filename.ends_with(".jpeg") { "image/jpeg" }
    else if filename.ends_with(".csv") { "text/csv" }
    else if filename.ends_with(".json") { "application/json" }
    else if filename.ends_with(".md") { "text/markdown" }
    else if filename.ends_with(".txt") { "text/plain" }
    else if filename.ends_with(".json") { "application/json" }
    else { "application/octet-stream" };


    let headers = [
        ("Content-Type", content_type),
        ("Cache-Control", "public, max-age=86400"), // 24-hour cache
    ];

    Ok((headers, body))
}

/// DELETE /runtime/artifacts/:run_id
/// Deletes all artifacts for a specific run
pub async fn delete_artifact_run(
    Path(run_id): Path<String>,
) -> Result<StatusCode, StatusCode> {
    let path = format!("/app/storage/artifacts/{}", run_id);

    tokio::fs::remove_dir_all(&path)
        .await
        .map_err(|e| {
            tracing::error!("Failed to delete artifact run {}: {}", run_id, e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    tracing::info!("Deleted artifact run: {}", run_id);
    Ok(StatusCode::NO_CONTENT)
}

/// POST /runtime/artifacts/:run_id/files/:filename/promote
/// Promotes an artifact to permanent library storage (scoped to client)
pub async fn promote_artifact_to_library(
    ClientSession(client_id): ClientSession, // <--- Auto-extracted
    Path((run_id, filename)): Path<(String, String)>,
) -> Result<StatusCode, StatusCode> {
    // Sanitize filename
    if filename.contains("..") || filename.starts_with("/") {
        return Err(StatusCode::FORBIDDEN);
    }

    let src = format!("/app/storage/artifacts/{}/{}", run_id, filename);

    // Check if source exists
    if !std::path::Path::new(&src).exists() {
        return Err(StatusCode::NOT_FOUND);
    }

    // Read the file
    let data = tokio::fs::read(&src)
        .await
        .map_err(|e| {
            tracing::error!("Failed to read artifact {} for promotion: {}", filename, e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    // Save to client-scoped library using fs_manager
    WorkspaceInitializer::save_to_library(&client_id, &filename, &data)
        .await
        .map_err(|e| {
            tracing::error!("Failed to promote artifact {} to client {} library: {}", filename, client_id, e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    tracing::info!("Promoted artifact {} from run {} to client {} library", filename, run_id, client_id);
    Ok(StatusCode::CREATED)
}