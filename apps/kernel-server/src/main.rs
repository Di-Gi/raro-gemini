// [[RARO]]/apps/kernel-server/src/main.rs
// Purpose: Entry point. Invokes state hydration before starting the server.
// Architecture: Application Boot
// Dependencies: Axum, Tower, Tokio

mod dag;
mod models;
mod server;
mod runtime;
mod observability;
mod events;
mod registry;

use axum::{
    Router,
    routing::{get, post},
    http::Method,
};
use std::sync::Arc;
use tower_http::cors::{CorsLayer, Any};
use tracing_subscriber;

use crate::runtime::RARORuntime;
use crate::server::handlers;

#[tokio::main]
async fn main() {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive("raro_kernel=debug".parse().unwrap())
                .add_directive("tower_http=trace".parse().unwrap()),
        )
        .init();

    tracing::info!("Initializing RARO Kernel...");

    let runtime = Arc::new(RARORuntime::new());

    // === PERSISTENCE RECOVERY ===
    // Attempt to load previous run states from Redis into memory
    runtime.rehydrate_from_redis().await;

    // === CORTEX: Pattern Engine ===
    // Subscribe to the event bus and spawn background pattern matcher
    let mut rx = runtime.event_bus.subscribe();
    let runtime_ref = runtime.clone();

    tokio::spawn(async move {
        tracing::info!("Cortex Pattern Engine started");
        loop {
            if let Ok(event) = rx.recv().await {
                // 1. Find matching patterns
                let patterns = runtime_ref.pattern_registry.get_patterns_for_trigger(&format!("{:?}", event.event_type));

                for pattern in patterns {
                    // 2. Evaluate Condition (Simple string match for MVP)
                    // In Phase 4, we use a real JSONPath engine here.
                    let condition_met = if pattern.condition == "*" {
                        true
                    } else {
                        // Very basic check: Does payload string contain the condition keyword?
                        event.payload.to_string().contains(&pattern.condition)
                    };

                    if condition_met {
                        tracing::info!("⚠️  Pattern Triggered: {} on Agent {}", pattern.name, event.agent_id.as_deref().unwrap_or("?"));

                        // 3. Execute Action
                        match pattern.action {
                            crate::registry::PatternAction::Interrupt { reason } => {
                                if let Some(agent) = &event.agent_id {
                                    // Direct call to fail_run (simulating interrupt)
                                    runtime_ref.fail_run(&event.run_id, agent, &reason).await;
                                }
                            }
                            crate::registry::PatternAction::RequestApproval { reason } => {
                                tracing::warn!("✋ Safety Pattern Triggered: Approval Required - {}", reason);

                                // CALL THE NEW PAUSE METHOD
                                if let Some(agent) = &event.agent_id {
                                    runtime_ref.request_approval(&event.run_id, Some(agent), &reason).await;
                                } else {
                                    runtime_ref.request_approval(&event.run_id, None, &reason).await;
                                }
                            }
                            crate::registry::PatternAction::SpawnAgent { .. } => {
                                tracing::warn!("SpawnAgent action not yet implemented in Cortex");
                            }
                        }
                    }
                }
            }
        }
    });

    // Configure CORS
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods([Method::GET, Method::POST])
        .allow_headers(Any);

    // Build router
    let app = Router::new()
        .route("/health", get(handlers::health))
        .route("/runtime/start", post(handlers::start_workflow))
        .route("/runtime/state", get(handlers::get_runtime_state))
        .route("/runtime/:run_id/agent/:agent_id/invoke", post(handlers::invoke_agent))
        .route("/runtime/signatures", get(handlers::get_signatures))
        .route("/runtime/:run_id/artifact/:agent_id", get(handlers::get_artifact))
        .route("/runtime/:run_id/resume", post(handlers::resume_run))
        .route("/runtime/:run_id/stop", post(handlers::stop_run))
        .route("/ws/runtime/:run_id", axum::routing::get(handlers::ws_runtime_stream))
        .layer(cors)
        .with_state(runtime);

    let port = std::env::var("KERNEL_PORT").unwrap_or_else(|_| "3000".to_string());
    let addr = format!("0.0.0.0:{}", port);
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .expect("Failed to bind to port");

    tracing::info!("RARO Kernel Server listening on http://{}", addr);

    axum::serve(listener, app)
        .await
        .expect("Server error");
}