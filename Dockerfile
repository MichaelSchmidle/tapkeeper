FROM ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 AS uv
FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY tapkeeper ./tapkeeper
RUN uv sync --frozen --no-dev --no-editable --no-cache --python /usr/local/bin/python

FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f
LABEL org.opencontainers.image.source="https://github.com/MichaelSchmidle/tapkeeper"
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /app/.venv /app/.venv
RUN mkdir /data && chown 10001:10001 /data
USER 10001:10001
WORKDIR /data
ENTRYPOINT ["tapkeeper"]
CMD ["--help"]
