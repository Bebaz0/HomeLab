FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*

# Pinned from `pip freeze` after the first build on arroz, 2026-09-08.
RUN pip install --no-cache-dir \
        requests==2.34.2 \
        PyYAML==6.0.3 \
        ruff==0.16.6 \
        bandit==1.9.4 \
        semgrep==1.176.1

WORKDIR /app
COPY reviewer/ /app/reviewer/

# HOME must be writable: the container runs as uid 1000 with no /etc/passwd entry,
# so HOME would otherwise resolve to / and semgrep's rule cache would fail.
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    HOME=/tmp \
    RUFF_CACHE_DIR=/tmp/ruff \
    DATA_DIR=/data \
    CONFIG_FILE=/config/config.yaml

# compose sets this too, but an image that only runs unprivileged by accident is
# one `docker run` away from running as root.
USER 1000:1000

CMD ["python", "-m", "reviewer.main"]
