FROM node:22-slim
RUN npm install -g pnpm@10.11.0
WORKDIR /app
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY frontend/tsconfig.json frontend/vitest.config.ts frontend/vite.config.mjs ./
COPY frontend/tests ./tests
COPY frontend/src ./src
RUN chown -R node:node /app
USER node
