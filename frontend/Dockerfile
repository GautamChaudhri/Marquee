# syntax=docker/dockerfile:1
#
# The web app's image: the built SvelteKit server under Node, nothing else.
#
#   build    npm ci and `npm run build` (the API types are generated from openapi.json)
#   runtime  the build output and the production dependencies, run as the image's `node` user
#
# No key or address is read at build time: the server reads MARQUEE_API_URL,
# MARQUEE_API_KEY, ORIGIN, HOST, PORT, and BODY_SIZE_LIMIT from its environment when it
# starts, so the image holds no secret.
#
#   docker build -t marquee-web:latest frontend

# Node 26, the version in .node-version, on Debian 13. Digest when pinned (2026-11-26):
# sha256:abe7e5a82b61a8ef67b315b85497c9e65173387ccf2f4a029248184b8e020be5
ARG NODE_IMAGE=node:26.13.1-trixie-slim

# ── build ───────────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
# `prepare` generates the API types from openapi.json, which is not here yet; the build
# below generates them itself (`prebuild`).
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts --no-audit --no-fund
COPY . .
RUN npm run build

# ── runtime ─────────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY --from=build /app/build ./build
USER node
EXPOSE 3000
CMD ["node", "build"]
