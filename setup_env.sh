#!/bin/bash

echo "Initializing RARO Environment Configuration..."

if [ -f .env ]; then
    echo ".env file already exists."
else
    echo "Creating .env from template..."
    cat > .env << EOL
# === RARO SECURITY CONFIGURATION ===

# [REQUIRED] Gemini 3 API Key from Google AI Studio
# Get one here: https://aistudio.google.com/app/apikey
GEMINI_API_KEY=

# === INFRASTRUCTURE CONFIGURATION ===

# Kernel Settings
KERNEL_PORT=3000
RUST_LOG=debug

# Agent Settings
AGENT_PORT=8000

# Web Settings
VITE_API_URL=http://localhost:3000
EOL
    echo "Created .env file."
    echo "⚠️  ACTION REQUIRED: Please open .env and paste your GEMINI_API_KEY."
fi