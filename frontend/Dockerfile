FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app

# NEXT_PUBLIC_* values are baked into the browser bundle at build time, and the browser (not
# the container) calls these URLs, so they must be host-visible (localhost), never service names.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ARG NEXT_PUBLIC_USE_EMULATORS=true
ARG NEXT_PUBLIC_FIREBASE_API_KEY=fake-api-key
ARG NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=demo-study-companion.firebaseapp.com
ARG NEXT_PUBLIC_FIREBASE_PROJECT_ID=demo-study-companion
ARG NEXT_PUBLIC_FIREBASE_APP_ID=1:1234567890:web:abcdef
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_USE_EMULATORS=$NEXT_PUBLIC_USE_EMULATORS \
    NEXT_PUBLIC_FIREBASE_API_KEY=$NEXT_PUBLIC_FIREBASE_API_KEY \
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=$NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN \
    NEXT_PUBLIC_FIREBASE_PROJECT_ID=$NEXT_PUBLIC_FIREBASE_PROJECT_ID \
    NEXT_PUBLIC_FIREBASE_APP_ID=$NEXT_PUBLIC_FIREBASE_APP_ID \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
# Explicit paths instead of `COPY . .`: there is no .dockerignore, and copying the whole folder
# would overwrite node_modules with the host's and bake .env.local into the image.
COPY package.json package-lock.json next.config.ts tsconfig.json postcss.config.mjs components.json eslint.config.mjs ./
COPY app ./app
COPY components ./components
COPY lib ./lib
# public/ is empty and untracked in git, so a fresh clone does not have it
RUN mkdir -p public
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# The base image ships an unprivileged `node` user
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

USER node
EXPOSE 3000

CMD ["node", "server.js"]
