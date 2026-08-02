# --- stage 1: build the React portal -------------------------------------
FROM node:22-alpine AS web
WORKDIR /w
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

# --- stage 2: python runtime ----------------------------------------------
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev
COPY --from=web /w/dist ./web/dist

EXPOSE 8756
CMD ["uv", "run", "--no-sync", "uvicorn", "specula.server:app", \
     "--host", "0.0.0.0", "--port", "8756"]
