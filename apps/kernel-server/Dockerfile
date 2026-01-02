FROM rust:latest AS builder

WORKDIR /usr/src/raro

COPY Cargo.toml Cargo.toml
# Copy lock file if it exists, otherwise cargo will generate a new one
COPY Cargo.lock* Cargo.lock
COPY src src

RUN cargo build --release

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y ca-certificates curl openssl && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/src/raro/target/release/raro-kernel /usr/local/bin/

EXPOSE 3000

CMD ["raro-kernel"]
