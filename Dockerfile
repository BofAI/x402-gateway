FROM node:20-slim

ENV HOME=/home/node

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --include=dev

COPY tsconfig.json README.md LICENSE ./
COPY src/ ./src/
RUN npm run build \
    && npm prune --omit=dev \
    && chmod +x /app/dist/cli.js \
    && ln -s /app/dist/cli.js /usr/local/bin/x402-gateway

ENV NODE_ENV=production

RUN mkdir -p /app/providers /home/node \
    && chown -R node:node /app /home/node

USER node

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:8080/__402/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "dist/cli.js", "--providers", "/app/providers", "--host", "0.0.0.0", "--port", "8080"]
