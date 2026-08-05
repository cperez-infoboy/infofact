"""Capa de retrieval semantico sobre los chunks cacheados (Fase D).

Vector store liviano sobre SQLite (sin FAISS/Milvus); reutiliza los embeddings
de ``consolidation``. Ver ``store.py``.
"""
