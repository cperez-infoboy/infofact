"""RAG search tool: semantic search over captured project documents."""
from __future__ import annotations

from langchain_core.tools import tool


def make_rag_search_tool(project_id: int) -> list:
    """Factory: returns a list with a search_documents tool bound to project_id."""

    @tool
    async def search_documents(query: str, top_k: int = 5) -> dict:
        """Busca semanticamente en los documentos capturados del proyecto.

        Retorna los fragmentos mas relevantes con su texto, seccion, pagina y score.
        Util para verificar si una entidad o relacion del modelo aparece en el texto fuente.
        """
        try:
            from backend.agents.retrieval.store import search

            hits = await search(
                project_id, query, top_k=top_k, used_in_capture_only=True
            )
            return {
                "results": [
                    {
                        "text": h.text[:500],
                        "section": h.section_path,
                        "page": h.page,
                        "score": round(h.score, 3),
                    }
                    for h in hits
                ],
                "count": len(hits),
            }
        except Exception as exc:  # noqa: BLE001 — surface to the model
            return {"error": f"search_documents failed: {exc}"}

    return [search_documents]
