FROM rust:latest AS builder

WORKDIR /usr/src/raro

COPY Cargo.toml Cargo.toml
# Copy lock file if it exists, otherwise cargo will generate a new one
COPY Cargo.lock* Cargo.lock
COPY src src

RUN cargo build --release

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y ca-certificates curl openssl && rm -rf /var/lib/apt/lists/*

# Copy entrypoint script
COPY scripts/entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY --from=builder /usr/src/raro/target/release/raro-kernel /usr/local/bin/

EXPOSE 3000

ENTRYPOINT ["entrypoint.sh"]
CMD ["raro-kernel"]
