"""Quick check: the critic's new `nature` dimension must reject document
machinery (fill instructions, codification legends, evaluation criteria) while
keeping real system requirements. ~9 LLM calls, ~30s. NOT a full e2e."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.agents.pipelines.critique import critique_item
from backend.agents.pipelines.extraction import RawRequirement

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)  # silencioso

# (statement, source_span, section, expected_nature_category)
# expected: "non_req" si debe caer (meta/definition/evaluation), "req" si debe quedar
CASES = [
    # --- boilerplate: esperado nature != requirement -> REJECTED ---
    ("El oferente debe marcar con una \"X\" si la solución que ofrece cubre cada "
     "necesidad detallada por TCC, de acuerdo a las siguientes opciones.",
     "Marque con una \"X\", si la solución que Ud. ofrece, cubre cada necesidad "
     "detallada por TCC, de acuerdo a las siguientes opciones:",
     "Instrucciones", "non_req"),
    ("Tipo de soporte A significa que la funcionalidad es soportada al "
     "entregarse \"listo para utilizarse\".",
     "Tipo de soporte A significa que la funcionalidad es soportada al "
     "entregarse \"listo para utilizarse\" (ready to use).",
     "Leyenda", "non_req"),
    ("Tipo de soporte NS significa que la funcionalidad no es soportada.",
     "Tipo de soporte NS significa que la funcionalidad no es soportada.",
     "Leyenda", "non_req"),
    ("Este costo deberá ser tenido en cuenta en la propuesta económica.",
     "Este costo deberá ser tenido en cuenta en la propuesta económica.",
     "Evaluación", "non_req"),
    ("La propuesta debe incluir Arquitectura - Backend, Arquitectura - "
     "Frontend, Modelo de Soporte, Infraestructura, General y Ciberseguridad.",
     "Arquitectura - Backend. Arquitectura - Frontend. Modelo de Soporte. "
     "Infraestructura. General. Ciberseguridad.",
     "Evaluación", "non_req"),
    # --- requerimientos reales: esperado nature == requirement -> KEPT ---
    ("La aplicación debe permitir que múltiples usuarios inicien sesión y "
     "operen simultáneamente.",
     "La aplicación debe permitir que múltiples usuarios inicien sesión y "
     "operen simultáneamente.",
     "Funcional", "req"),
    ("Se debe implementar cifrado para datos sensibles como datos bancarios.",
     "Se debe implementar cifrado para datos sensibles como datos bancarios.",
     "Seguridad", "req"),
    ("Los códigos de lectura pueden ser de barras o QR.",
     "Los códigos de lectura pueden ser de barras o QR.",
     "Funcional", "req"),
    ("El comprobante digital debe permitir la visualización desde el portal "
     "web para clientes (remitente y destinatario).",
     "El comprobante digital debe permitir la visualización desde el portal "
     "web para clientes (remitente y destinatario).",
     "Funcional", "req"),
]


async def main():
    print(f"{'#':>2}  {'expected':8}  {'nature':16}  {'fidelity':8}  {'landed':8}  statement")
    pass_n = 0
    fail_n = 0
    for i, (stmt, span, section, expected) in enumerate(CASES, 1):
        item = RawRequirement(
            id=f"tc-{i:02d}",
            statement=stmt,
            source_span=span,
            section=section,
            page=None,
            explicit=True,
            confidence=0.9,
        )
        kept, v = await critique_item(item)
        landed = "REJECTED" if kept is None else "KEPT"
        ok = (expected == "non_req" and kept is None) or (expected == "req" and kept is not None)
        print(f"{i:>2}  {expected:8}  {v.nature:16}  {v.fidelity:8}  {landed:8}  {'OK' if ok else 'FAIL'}  {stmt[:60]}")
        if ok:
            pass_n += 1
        else:
            fail_n += 1
    print(f"\n{pass_n}/{pass_n+fail_n} casos como esperado")
    sys.exit(0 if fail_n == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
