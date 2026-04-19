FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ ./src/

# Non-root user. Pre-create /assets so Docker copies the ownership onto
# a fresh `assets` named volume at first mount — prevents a permission
# trap on volume creation where root owns the mount point and the
# scanner (UID 1001) can't write portraits.
RUN useradd -m -u 1001 scanner \
    && mkdir -p /assets /assets/politicians \
    && chown -R scanner:scanner /app /assets
USER scanner

ENTRYPOINT ["python", "-m", "src"]
CMD ["--help"]
