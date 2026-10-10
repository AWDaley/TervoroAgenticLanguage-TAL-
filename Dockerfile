# Pin both image digests in ExperimentFreeze after the certified build.
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12-slim

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

ENTRYPOINT ["/app/.venv/bin/tal"]
