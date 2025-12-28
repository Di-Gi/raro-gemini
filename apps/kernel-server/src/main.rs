mod dag;
mod models;
mod server;
mod runtime;
mod observability;

use axum::{
    Router,
    routing::{get, post},
    Json,
};
use std::sync::Arc;
use tower_http::cors::CorsLayer;
use tracing_subscriber;

use crate::runtime::RARORuntime;
use crate::server::handlers;

#[tokio::main]
async fn main() {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive("raro_kernel=debug".parse().unwrap()),
        )
        .init();

    let runtime = Arc::new(RARORuntime::new());

    // Build router
    let app = Router::new()
        .route("/health", get(handlers::health))
        .route("/runtime/start", post(handlers::start_workflow))
        .route("/runtime/state", get(handlers::get_runtime_state))
        .route("/runtime/agent/:id/invoke", post(handlers::invoke_agent))
        .route("/runtime/signatures", get(handlers::get_signatures))
        .layer(CorsLayer::permissive())
        .with_state(runtime);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:3000")
        .await
        .expect("Failed to bind to port 3000");

    tracing::info!("RARO Kernel Server listening on http://127.0.0.1:3000");

    axum::serve(listener, app)
        .await
        .expect("Server error");
}
