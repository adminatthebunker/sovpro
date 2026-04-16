# ═══════════════════════════════════════════════════════════════════════════
# SovereignWatch database image
# ═══════════════════════════════════════════════════════════════════════════
# postgis/postgis:16-3.4 with pgvector added. pgvector powers the
# semantic-search layer (see docs/plans/semantic-layer.md) — the base
# image does not ship it, so we install the Debian package on top.
#
# Keep this image small — no app code, no data, just extensions. The
# pgdata volume from docker-compose is what persists across rebuilds.
# ═══════════════════════════════════════════════════════════════════════════

FROM postgis/postgis:16-3.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
