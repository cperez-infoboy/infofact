"""Requirements-capture pipelines. Host-side; deterministic + LLM stages.

ingestion       — Docling parse + element-aware chunking + structural map
extraction      — (step 3) Pydantic schema, mandatory source_span, gap/implicit
consolidation   — (step 4) dedup + contradiction detection (embeddings)
critique        — (step 5) generator-critic loop, 5 checks, <=2 iterations
classification  — (step 6) type + priority + decomposition

These modules run in the FastAPI process (host-side), reading workspace files
from settings.workspaces_host_root. Heavy deps (docling/torch) are lazy-imported
so the backend starts without them installed locally.
"""
