# ═══════════════════════════════════════════════════════════════════════════
# SovereignWatch embedding service — BGE-M3 + BGE-reranker on GPU (CUDA 12.4)
# ═══════════════════════════════════════════════════════════════════════════
# Uses the official pytorch/pytorch runtime image so torch + CUDA + cuDNN
# are pre-installed and match versions. BGE-M3 runs in fp16 on the GPU,
# which cuts VRAM roughly in half and 2x's throughput vs fp32 with no
# measurable retrieval-quality loss.
#
# The container REQUIRES a GPU at runtime — compose requests it via
# `deploy.resources.reservations.devices`. To run the service back on CPU
# (no GPU host), swap this image for the python:3.11-slim variant kept in
# VCS history and flip use_fp16 back to False.
# ═══════════════════════════════════════════════════════════════════════════

FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models \
    TRANSFORMERS_OFFLINE=0 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# libgomp1 is already included; we just need curl for the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Torch comes from the base image (torch 2.5.1 + CUDA 12.4). We install
# FlagEmbedding + the HTTP layer on top; pip's resolver won't touch torch
# since requirements.txt doesn't pin it.
RUN pip install -r requirements.txt

COPY server.py ./

VOLUME ["/models"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD curl -fs http://localhost:8000/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
