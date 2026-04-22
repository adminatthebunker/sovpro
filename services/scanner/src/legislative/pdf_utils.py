"""Shared PDF → text primitive.

Wraps Poppler's ``pdftotext`` via subprocess so multiple pipelines
(AB Hansard, MB billstatus, potentially future QC committee reports)
can extract PDF content without each duplicating the subprocess
plumbing.

Two modes:

* ``layout=False`` (default) — reading-order text. Correct for
  two-column prose like AB Hansard, where ``-layout`` would interleave
  the columns into nonsense.
* ``layout=True`` — column-aligned text. Correct for tabular PDFs
  like MB's billstatus.pdf, where column position carries meaning.

Poppler (``poppler-utils``) is installed in the scanner Dockerfile.
"""
from __future__ import annotations

import subprocess


def pdftotext(
    pdf_bytes: bytes, *, layout: bool = False, raw: bool = False,
    timeout: int = 120,
) -> str:
    """Pipe PDF bytes through ``pdftotext`` and return decoded text.

    - ``layout=True`` passes ``-layout`` (column-aligned, good for
      visual tables).
    - ``raw=True`` passes ``-raw`` (content-stream order — good for
      tables where column wrapping splits cell values across lines,
      as in MB's billstatus.pdf).
    - Neither flag: default reading-order mode (good for two-column
      prose like AB Hansard).

    ``layout`` and ``raw`` are mutually exclusive at the CLI level;
    if both are passed the last one wins, matching Poppler's
    behaviour.
    """
    cmd = ["pdftotext", "-enc", "UTF-8"]
    if layout:
        cmd.append("-layout")
    if raw:
        cmd.append("-raw")
    cmd.extend(["-", "-"])
    try:
        result = subprocess.run(
            cmd,
            input=pdf_bytes,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "pdftotext not on PATH — ensure poppler-utils is installed "
            "in the scanner image"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"pdftotext failed (rc={result.returncode}): "
            f"{result.stderr.decode('utf-8', 'replace')[:300]}"
        )
    return result.stdout.decode("utf-8", "replace")
