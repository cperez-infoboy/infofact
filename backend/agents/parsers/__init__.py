"""Subsistema de parsers (Fase C): router que elige Docling vs GLM-OCR por documento.

``router.parse_document`` despacha; ``ingestion.ingest_document`` delega aca.
El cache (``parse_cache``) usa ``choose_parser_name`` para armar la version key.
"""
