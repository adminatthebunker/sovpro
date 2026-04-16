# ═══════════════════════════════════════════════════════════════════════════
# SovereignWatch embedding service — BGE-M3 + BGE-reranker, CPU inference
# ═══════════════════════════════════════════════════════════════════════════
# HTTP service the scanner calls to get dense embeddings and rerank scores.
# Self-hosted on CPU: no OpenAI / Cohere / Voyage in the critical path.
# Model cache lives on a named volume so rebuilds don't re-download ~2 GB.
# ═══════════════════════════════════════════════════════════════════════════

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models \
    TRANSFORMERS_OFFLINE=0 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# libgomp is required by torch / ONNX kernels. curl is for the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install CPU-only PyTorch explicitly to avoid pulling the ~2 GB CUDA wheels.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt

COPY server.py ./

# Models live on a bind-mounted volume; the first /embed or /rerank call
# pulls them from HF and caches them in /models. Subsequent boots are fast.
VOLUME ["/models"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD curl -fs http://localhost:8000/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
