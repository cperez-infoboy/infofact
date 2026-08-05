"""Smoke de Fase A (ingesta): helpers puros de document_service.

No requiere DB ni container: ejercita prepare_document (validación de
extensión, sha256, mime, size, defaults) y las funciones puras de hash/path.
La vía DB+container (register_uploaded/scan/delete) se verifica E2E desde
el FileExplorer (drag-drop) contra el stack levantado.

Uso:
  .venv/bin/python scripts/smoke_documents_upload.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services import document_service as ds

# --- _ext / _guess_mime ---
assert ds._ext("Spec.PDF") == ".pdf", ds._ext("Spec.PDF")
assert ds._ext("noext") == ""
assert ds._guess_mime("spec.pdf") == "application/pdf"
assert ds._guess_mime("photo.JPG") == "image/jpeg"
print("_ext + _guess_mime OK")

# --- sha256 determinista ---
a = ds._sha256_bytes(b"hola")
b2 = ds._sha256_bytes(b"hola")
c = ds._sha256_bytes(b"chau")
assert a == b2, "sha256 debe ser determinista"
assert a != c, "distinto contenido -> distinto sha256"
assert len(a) == 64
print("_sha256_bytes OK (determinista, 64 hex chars)")

# --- default rel path: raíz del workspace (sin carpeta documents/) ---
assert ds._default_rel_path("spec.pdf") == "spec.pdf"
print("_default_rel_path OK (raíz del workspace, sin documents/)")

# --- prepare_document: happy path con rel_path explícito ---
doc = ds.prepare_document(42, "RFP-2024.pdf", b"%PDF-1.7 fake", rel_path="docs/rfp.pdf")
assert doc.project_id == 42
assert doc.rel_path == "docs/rfp.pdf", doc.rel_path  # respeta rel_path explícito
assert doc.filename == "RFP-2024.pdf"
assert doc.extension == ".pdf"
assert doc.mime == "application/pdf"
assert doc.size_bytes == len(b"%PDF-1.7 fake")
assert doc.sha256 == ds._sha256_bytes(b"%PDF-1.7 fake")
assert doc.page_count is None
assert doc.parse_status == "pending"
print("prepare_document OK (rel_path explícito, campos calculados, pending)")

# --- prepare_document: default rel_path cuando no se pasa (raíz) ---
doc2 = ds.prepare_document(1, "notas.md", b"# title")
assert doc2.rel_path == "notas.md", doc2.rel_path
assert doc2.extension == ".md"
print("prepare_document OK (default rel_path = filename en raíz)")

# --- prepare_document: extensión no admitida ---
for bad in ("archivo.exe", "datos.bin", "script.sh", "sinext"):
    try:
        ds.prepare_document(1, bad, b"x")
    except ds.UnsupportedFileError:
        continue
    raise AssertionError(f"{bad!r} debió rechazarse")
print("prepare_document OK (rechaza extensiones no admitidas)")

# --- _ALLOWED_EXT cubre los formatos fuente esperados ---
for ok in ("spec.pdf", "req.docx", "notas.txt", "readme.md", "diagrama.png", "tabla.xlsx"):
    ds.prepare_document(1, ok, b"x")  # no debe lanzar
print("_ALLOWED_EXT cubre pdf/docx/txt/md/png/xlsx")

print("\nsmoke_documents_upload OK")
