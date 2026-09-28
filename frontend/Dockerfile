# syntax=docker/dockerfile:1
#
# Production image for the Next.js frontend: the BFF (src/app/api/bff), the
# auth routes and every server-rendered page. Node server from Next's
# `output: "standalone"` (next.config.ts) — never `next dev`.
#
# Base image pinned by digest, not just tag (same supply-chain reason as
# backend/Dockerfile). Node 24 is the active LTS line; Node 20 reached end of
# life on 2026-04-30. Verified 2026-09-24: node:24-bookworm-slim, Node v24.21.0.
ARG NODE_IMAGE=node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
# Exactly the lockfile (fails if it is out of sync with package.json). Dev
# dependencies are needed to build (TypeScript, Tailwind); none reach the
# runtime stage below, which copies only the standalone trace.
RUN npm ci --no-audit --no-fund

FROM ${NODE_IMAGE} AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app

# OS security patches for the runtime layer only (same practice as
# backend/Dockerfile: the base image can lag behind Debian security updates).
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
    /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Only the traced server, its node_modules subset and the static assets.
# Runtime configuration (API_BASE_URL, BFF_PROXY_SECRET, SESSION_COOKIE_SECURE,
# TRUSTED_PROXY_COUNT) is supplied by the orchestrator at run time and checked
# at start-up (src/instrumentation.ts) — nothing secret is in any layer.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

# The official image's unprivileged user (uid 1000).
USER node

EXPOSE 3000

# Liveness only (src/app/api/health/route.ts): no dependency on the API.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.js"]
