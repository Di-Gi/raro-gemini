FROM node:20-alpine AS builder

WORKDIR /app

# 1. Accept the Argument (Default to false for production container safety)
ARG VITE_USE_MOCK_API=false

# 2. Set it as an ENV for the build process
ENV VITE_USE_MOCK_API=$VITE_USE_MOCK_API

# Copy package management files
COPY package.json package-lock.json* ./

# Install dependencies
# Old: RUN npm ci
# New:
RUN npm install

# Copy source code
COPY . .

# Build the application
RUN npm run build

# --- Runtime Stage ---
FROM node:20-alpine

WORKDIR /app

# Copy built assets and package files
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules

# Expose Vite's default port
EXPOSE 5173

# Bind to 0.0.0.0 to ensure accessibility outside container
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0"]