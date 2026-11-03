FROM node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205

ENV HOME=/home/node

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --include=dev --no-audit --no-fund

COPY tsconfig.json README.md LICENSE ./
COPY src/ ./src/
RUN npm run build \
    && npm prune --omit=dev --no-audit --no-fund \
    && chmod +x /app/dist/cli.js \
    && ln -s /app/dist/cli.js /usr/local/bin/x402-gateway

ENV NODE_ENV=production

RUN mkdir -p /app/providers /home/node \
    && chown -R node:node /app /home/node

USER node

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:8080/__402/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["x402-gateway", "--providers", "/app/providers", "--host", "0.0.0.0", "--port", "8080"]
