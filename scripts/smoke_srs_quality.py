"""Smoke test de los pre-checks programáticos de calidad del SRS (Fase A).

Sin LLM, sin DB: ``programmatic_findings`` es una función pura sobre un
RequirementItem. Verifica que cada regla determinista dispara (o no) sobre
enunciados diseñados a propósito:

  - smell.vague_term   : término vago ("rápido")
  - smell.negation     : requerimiento negativo ("no debe")
  - smell.combinator   : combinador "y/o" o "/"
  - smell.absolute     : absoluto no verificable ("100%", "siempre")
  - incose.too_long    : enunciado > 220 caracteres
  - incose.unmeasurable_nfr: PERFORMANCE/RELIABILITY sin target numérico
  - incose.modal_missing: sin verbo de obligación
  - (limpio)            : enunciado EARS bien formado -> 0 hallazgos

Run: .venv/bin/python scripts/smoke_srs_quality.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from backend.models.requirement import ReqType
from backend.services.srs_quality import programmatic_findings

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def rule_ids(item) -> set[str]:
    return {f["rule_id"] for f in programmatic_findings(item)}


def item(stmt: str, rtype: ReqType = ReqType.FUNCTIONAL) -> SimpleNamespace:
    return SimpleNamespace(id=1, statement=stmt, type=rtype)


def main() -> int:
    print("== vague term ==")
    ids = rule_ids(item("El sistema debe ser rápido al cargar el listado."))
    check("vague_term dispara", "smell.vague_term" in ids, str(ids))

    print("\n== negation ==")
    ids = rule_ids(item("El sistema no debe permitir accesos anónimos."))
    check("negation dispara", "smell.negation" in ids, str(ids))

    print("\n== combinator (y/o) ==")
    ids = rule_ids(item("El sistema debe exportar a PDF y/o DOCX."))
    check("combinator dispara", "smell.combinator" in ids, str(ids))

    print("\n== combinator (slash) ==")
    ids = rule_ids(item("El sistema debe soportar módulo admin/usuario."))
    check("combinator dispara (slash)", "smell.combinator" in ids, str(ids))

    print("\n== absolute ==")
    ids = rule_ids(item("El sistema debe estar disponible el 100% del tiempo."))
    check("absolute dispara", "smell.absolute" in ids, str(ids))

    print("\n== too long ==")
    long_stmt = (
        "El sistema debe permitir al usuario administrador gestionar de manera "
        "completa todos los aspectos relacionados con la facturación electrónica "
        "incluyendo la creación, edición, eliminación, envío al ente recaudador, "
        "descarga en formato PDF firmado digitalmente, reenvío por correo a los "
        "destinatarios configurados y auditoría completa de cada operación "
        "realizada sobre el comprobante durante todo su ciclo de vida completo."
    )
    ids = rule_ids(item(long_stmt))
    check("too_long dispara (>220 chars)",
          "incose.too_long" in ids, f"len={len(long_stmt)} ids={ids}")

    print("\n== unmeasurable NFR (performance sin número) ==")
    ids = rule_ids(item("El sistema debe procesar conciliaciones bancarias.",
                        ReqType.PERFORMANCE))
    check("unmeasurable_nfr dispara",
          "incose.unmeasurable_nfr" in ids, str(ids))

    print("\n== measurable NFR (performance CON número) ==")
    ids = rule_ids(item("El sistema debe responder el login en menos de 200 ms.",
                        ReqType.PERFORMANCE))
    check("unmeasurable_nfr NO dispara con número",
          "incose.unmeasurable_nfr" not in ids, str(ids))

    print("\n== modal missing ==")
    ids = rule_ids(item("Autenticación de usuarios vía OAuth."))
    check("modal_missing dispara", "incose.modal_missing" in ids, str(ids))

    print("\n== clean EARS (0 hallazgos) ==")
    ids = rule_ids(item(
        "Cuando el usuario presiona el botón de guardar, el sistema "
        "debe persistir el formulario validado."))
    check("EARS limpio -> 0 hallazgos", ids == set(), str(ids))

    print(f"\n{'='*40}\nquality smoke: {PASS} pass, {FAIL} fail")
    return FAIL


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)
