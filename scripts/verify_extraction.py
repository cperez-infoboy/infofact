"""Structural verification for step 3 (no LLM key needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.pipelines.extraction import (
    ChunkExtraction,
    ImplicitExtraction,
    RawRequirement,
    StructureMapAnnotation,
    verify_spans,
)

DOC = (
    "El sistema debera permitir la autenticacion de usuarios mediante "
    "usuario y clave. El sistema sera rapido y seguro."
)

items = [
    # valid verbatim span -> verified
    RawRequirement(
        statement="El sistema debe autenticar con usuario y clave.",
        source_span="El sistema debera permitir la autenticacion de usuarios mediante usuario y clave.",
        section="Seguridad",
        confidence=0.9,
        document_id="doc-A",
    ),
    # hallucinated span -> unverified
    RawRequirement(
        statement="El sistema debe soportar SSO con SAML.",
        source_span="El sistema debe soportar single sign-on con SAML 2.0.",
        section="Seguridad",
        confidence=0.4,
        document_id="doc-A",
    ),
    # span from a different doc (document_id mismatch) -> unverified
    RawRequirement(
        statement="Req de otro documento.",
        source_span="El sistema debera permitir la autenticacion",
        section="X",
        confidence=0.5,
        document_id="doc-B",
    ),
]

verify_spans(items, {"doc-A": DOC})

assert items[0].span_verified is True, "verbatim span must verify"
assert items[0].status == "draft"
assert items[1].span_verified is False, "hallucinated span must fail"
assert items[1].status == "unverified"
assert items[2].span_verified is False, "wrong-document span must fail"
assert items[2].status == "unverified"

print("verify_spans OK: verbatim=verified, hallucinated=unverified, cross-doc=unverified")
print(f"schemas importable: {ChunkExtraction.__name__}, {ImplicitExtraction.__name__}, "
      f"{StructureMapAnnotation.__name__}, {RawRequirement.__name__}")
