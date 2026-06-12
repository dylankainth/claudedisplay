FROM node:20-alpine

# Install Python (Claude Code needs it)
RUN apk add --no-cache python3 curl

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Create app directory
WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm install

# Copy API server
COPY api_server.js .

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Start the server
CMD ["node", "api_server.js"]
