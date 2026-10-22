FROM node:22.23.4-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d

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
