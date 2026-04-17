"""SovereignWatch embedding service.

Two HTTP endpoints the scanner calls:

- POST /embed    → BGE-M3 dense embeddings (1024-dim, cosine-normalised)
- POST /rerank   → BGE-reranker-v2-m3 cross-encoder scores

Both models are lazy-loaded on first call so the container starts in
seconds; first request pays the download + load cost (~2 GB, ~60 s on
a fresh volume). Subsequent calls hit the in-process cache.

Design notes:
- Single worker. BGE-M3 keeps model weights + KV state in process; two
  workers means ~4 GB RAM for no throughput gain on CPU.
- Requests batch internally inside FlagEmbedding — caller batches by
  sending an array per call (up to `MAX_BATCH` items).
- Model cache lives under HF_HOME=/models which docker-compose mounts as
  a named volume so rebuilds don't re-download.
- Token-counting is exposed on /embed so the caller doesn't need to
  duplicate BGE-M3's tokenizer.
"""

from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# On GPU builds, torch thread caps are irrelevant — inference happens
# on device. On the CPU fallback build (python:3.11-slim base) we used
# to bound OMP / torch threads to the cgroup quota so torch wouldn't
# over-fan against the limit. Leaving that honoured for the CPU path
# but skipping it when CUDA is visible.
def _apply_thread_caps():
    try:
        import torch
        if torch.cuda.is_available():
            # Keep CPU threads modest — tokeniser prep + data movement is
            # all that runs on CPU in GPU mode.
            torch.set_num_threads(2)
            return
        n = int(os.environ.get("TORCH_NUM_THREADS", os.environ.get("OMP_NUM_THREADS", "4")))
        torch.set_num_threads(n)
        torch.set_num_interop_threads(max(1, n // 2))
    except Exception:
        pass


_apply_thread_caps()

# fp16 halves VRAM use and roughly 2x's throughput on CUDA with no
# measurable retrieval-quality loss for BGE-M3 / BGE-reranker. Falls
# back to fp32 on CPU where fp16 is slow or unsupported.
try:
    import torch as _torch_probe
    USE_FP16 = _torch_probe.cuda.is_available()
except Exception:
    USE_FP16 = False

# FlagEmbedding imports are deferred into the lazy loaders below to keep
# the process startup fast.

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("embed")

EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
MAX_BATCH = int(os.environ.get("MAX_BATCH", "64"))
MAX_INPUT_LEN = int(os.environ.get("MAX_INPUT_LEN", "8192"))
DIM = 1024

app = FastAPI(title="SovereignWatch embed", version="0.1.0")

# ── Lazy model holders ─────────────────────────────────────────────
_embed_model = None
_embed_lock = Lock()
_rerank_model = None
_rerank_lock = Lock()


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                log.info("loading embed model %s (fp16=%s)", EMBED_MODEL, USE_FP16)
                t0 = time.monotonic()
                from FlagEmbedding import BGEM3FlagModel
                _embed_model = BGEM3FlagModel(EMBED_MODEL, use_fp16=USE_FP16)
                log.info("embed model loaded in %.1fs", time.monotonic() - t0)
    return _embed_model


def _get_rerank_model():
    global _rerank_model
    if _rerank_model is None:
        with _rerank_lock:
            if _rerank_model is None:
                log.info("loading rerank model %s (fp16=%s)", RERANK_MODEL, USE_FP16)
                t0 = time.monotonic()
                from FlagEmbedding import FlagReranker
                _rerank_model = FlagReranker(RERANK_MODEL, use_fp16=USE_FP16)
                log.info("rerank model loaded in %.1fs", time.monotonic() - t0)
    return _rerank_model


# ── Request / response shapes ─────────────────────────────────────

class EmbedRequest(BaseModel):
    texts: List[str] = Field(..., min_length=1)
    # Optional: return token counts (useful for chunking decisions upstream)
    return_tokens: bool = False


class EmbedItem(BaseModel):
    embedding: List[float]
    token_count: Optional[int] = None


class EmbedResponse(BaseModel):
    model: str
    dim: int
    items: List[EmbedItem]
    elapsed_ms: int


class RerankPair(BaseModel):
    query: str
    document: str


class RerankRequest(BaseModel):
    pairs: List[RerankPair] = Field(..., min_length=1)


class RerankResponse(BaseModel):
    model: str
    scores: List[float]
    elapsed_ms: int


# ── Endpoints ─────────────────────────────────────────────────────

@app.get("/health")
def health():
    # Models are NOT preloaded — the health check just confirms the
    # process is alive. Preloading in a healthcheck would block startup
    # for minutes on first boot.
    device = "cpu"
    device_name = None
    try:
        import torch
        if torch.cuda.is_available():
            device = "cuda"
            device_name = torch.cuda.get_device_name(0)
    except Exception:
        pass
    return {
        "ok": True,
        "device": device,
        "device_name": device_name,
        "fp16": USE_FP16,
        "embed_model": EMBED_MODEL,
        "rerank_model": RERANK_MODEL,
        "embed_loaded": _embed_model is not None,
        "rerank_loaded": _rerank_model is not None,
    }


def _release_gpu_cache():
    """Return PyTorch's CUDA caching allocator to the OS.

    Exposed via POST /flush-cache for the "I need the GPU right now"
    case. Do NOT call this from inside inference handlers: on a 6 GiB
    consumer GPU (RTX 4050 Mobile), forcing the caching allocator to
    release on every batch churns cudaMalloc/cudaFree at a cadence the
    driver's allocator can't sustain — observed 2026-04-17 as a ~100x
    regression in run duration (71k chunks → 448 chunks before the
    first 'unspecified launch failure'). PyTorch's allocator is
    explicitly designed to hold freed blocks for reuse; defeating it
    per-request accelerates driver-level fragmentation.
    """
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass


def _fatal_on_cuda_error(exc: BaseException) -> None:
    """If the exception looks like a poisoned CUDA context, exit the
    process so Docker restarts the container with a fresh context.

    Observed in production during a 240k-chunk overnight run: once
    `CUDA error: unspecified launch failure` fires, every subsequent
    /embed call returns 500 with the same error for the life of the
    process — the model's CUDA context is permanently toast. Before
    this guard, the container would keep serving healthy 200s on
    /health while every inference failed, silently losing hours of
    scheduler time. Fail-fast restores auto-recovery: the caller
    retries on next batch, the fresh container has a clean context,
    and the ingest pipeline picks up where it left off because
    speech_chunks.embedding is still NULL for unprocessed rows.

    We only exit on messages clearly indicating CUDA context damage
    to avoid restarting on benign errors (empty batches, tokenizer
    edge cases, etc.).
    """
    import os
    msg = str(exc).lower()
    fatal_markers = (
        "cuda error",
        "cublas",
        "cudnn",
        "device-side assert",
        "illegal memory access",
    )
    if any(m in msg for m in fatal_markers):
        log.error("fatal CUDA error detected; exiting for container restart: %s", exc)
        # Flush loggers, then hard exit (os._exit bypasses uvicorn's
        # graceful shutdown — intentional, since CUDA is hung).
        import sys
        sys.stderr.flush()
        sys.stdout.flush()
        os._exit(42)


@app.post("/flush-cache")
def flush_cache():
    """Manually release the CUDA caching allocator back to the OS.

    The inference handlers already call this after every request, so in
    normal operation you never need to hit this endpoint. It exists for
    the "I want the GPU for something else RIGHT NOW without restarting
    the container" case. Model weights stay resident — only activation
    caches are released.
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return {"ok": True, "device": "cpu", "note": "no cuda to flush"}
        before = torch.cuda.memory_reserved(0)
        _release_gpu_cache()
        after = torch.cuda.memory_reserved(0)
        return {
            "ok": True,
            "device": "cuda",
            "reserved_before_bytes": before,
            "reserved_after_bytes": after,
            "freed_bytes": max(0, before - after),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    if len(req.texts) > MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"batch size {len(req.texts)} exceeds MAX_BATCH={MAX_BATCH}",
        )
    # BGE-M3 crashes on empty strings; guard at the edge.
    texts = [t if t.strip() else " " for t in req.texts]

    model = _get_embed_model()
    t0 = time.monotonic()
    try:
        # `encode` returns {'dense_vecs': np.ndarray[B, 1024], ...}
        out = model.encode(
            texts,
            max_length=MAX_INPUT_LEN,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
    except RuntimeError as exc:
        _fatal_on_cuda_error(exc)
        raise
    dense = out["dense_vecs"]  # shape (B, 1024)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    token_counts: Optional[List[int]] = None
    if req.return_tokens:
        tok = model.tokenizer(texts, add_special_tokens=True)
        token_counts = [len(ids) for ids in tok["input_ids"]]

    items = [
        EmbedItem(
            embedding=list(map(float, vec)),
            token_count=(token_counts[i] if token_counts else None),
        )
        for i, vec in enumerate(dense)
    ]
    return EmbedResponse(
        model=EMBED_MODEL,
        dim=DIM,
        items=items,
        elapsed_ms=elapsed_ms,
    )


@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    if len(req.pairs) > MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"batch size {len(req.pairs)} exceeds MAX_BATCH={MAX_BATCH}",
        )
    pairs = [[p.query, p.document] for p in req.pairs]
    model = _get_rerank_model()
    t0 = time.monotonic()
    try:
        scores = model.compute_score(pairs, max_length=MAX_INPUT_LEN, normalize=True)
    except RuntimeError as exc:
        _fatal_on_cuda_error(exc)
        raise
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    # FlagEmbedding returns a scalar when len==1, list otherwise.
    if isinstance(scores, (int, float)):
        scores = [float(scores)]
    else:
        scores = [float(s) for s in scores]
    return RerankResponse(
        model=RERANK_MODEL,
        scores=scores,
        elapsed_ms=elapsed_ms,
    )
