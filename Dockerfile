FROM ghcr.io/astral-sh/uv:0.9.27 AS uv

FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY skills/ ./skills/
COPY examples/fjord_demo.yaml ./examples/fjord_demo.yaml
RUN uv sync --locked --no-dev --no-editable --no-cache

FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 tripwright \
    && useradd --uid 10001 --gid 10001 --create-home tripwright \
    && install -d -o 10001 -g 10001 -m 700 /data
# Live travel data by default (set TRIPWRIGHT_PROVIDERS=synthetic only for a demo); the route-search counter lives on
# the data volume because the root file system is read-only and /tmp forgets it on restart.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TRIPWRIGHT_PROVIDERS=live \
    TRIPWRIGHT_TRANSITOUS_USAGE=/data/transitous-routing.log
WORKDIR /app
COPY --from=build /app/.venv/ /app/.venv/
COPY deploy/healthcheck.py /app/healthcheck.py
COPY deploy/entrypoint.py /app/entrypoint.py
# The image starts as root only so the entrypoint can give a named or platform volume to UID 10001; it then drops to
# that user. A hardened deployment may instead start as 10001 because /data already belongs to that user.
EXPOSE 8000
ENTRYPOINT ["python", "/app/entrypoint.py"]
# Listens on $PORT when the platform sets it (Railway), else 8000.
CMD ["serve", "--http", "--host", "0.0.0.0"]
