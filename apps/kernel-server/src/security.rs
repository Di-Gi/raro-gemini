// apps/kernel-server/src/security.rs
use axum::{
    async_trait,
    extract::FromRequestParts,
    http::{request::Parts, StatusCode},
};

pub struct ClientSession(pub String);

#[async_trait]
impl<S> FromRequestParts<S> for ClientSession
where
    S: Send + Sync,
{
    type Rejection = StatusCode;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        // Extract header
        let client_id = parts
            .headers
            .get("X-RARO-CLIENT-ID")
            .and_then(|h| h.to_str().ok())
            .unwrap_or("public"); // Default to public/anon if missing (e.g. Health checks)

        // Basic Sanitization (Alphanumeric + dashes only) to prevent directory traversal attacks
        if !client_id.chars().all(|c| c.is_alphanumeric() || c == '-') {
            tracing::warn!("Invalid Client ID rejected: {}", client_id);
            return Err(StatusCode::BAD_REQUEST);
        }

        Ok(ClientSession(client_id.to_string()))
    }
}
