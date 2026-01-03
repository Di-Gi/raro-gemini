// [[RARO]]/apps/kernel-server/src/main.rs
// Purpose: Entry point. Invokes state hydration before starting the server.
// Architecture: Application Boot
// Dependencies: Axum, Tower, Tokio

mod dag;
mod models;
mod server;
mod runtime;
mod observability;

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