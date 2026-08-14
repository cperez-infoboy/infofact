"""Plaintext chunking: a large .md/.txt must not reach the extractor as one
giant chunk (structured-output truncation + lost-in-the-middle recall risk).
"""

from pathlib import Path

from backend.agents.pipelines.ingestion import (
    _PLAINTEXT_CHUNK_CHARS,
    _chunk_plaintext,
    _plaintext_sections,
    _split_plaintext,
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_small_file_is_one_chunk(tmp_path):
    p = _write(tmp_path, "doc.md", "# Título\n\nEl sistema debe validar el login.\n")
    chunks = _chunk_plaintext(p, str(p))
    assert len(chunks) == 1
    assert "El sistema debe validar el login." in chunks[0].text
    assert chunks[0].section_path == "Título"


def test_empty_file_returns_no_chunks(tmp_path):
    p = _write(tmp_path, "empty.md", "   \n\n")
    assert _chunk_plaintext(p, str(p)) == []


def test_large_md_splits_under_budget(tmp_path):
    # ~50 KB markdown: many sections, well over the 32K char budget.
    parts = []
    for i in range(60):
        parts.append(f"# Capítulo {i}\n\n")
        for j in range(10):
            parts.append(f"## Sección {i}.{j}\n\n")
            parts.append("El sistema debe validar el registro " + "x" * 500 + f" {i}.{j}.\n\n")
    text = "".join(parts)
    assert len(text) > 50_000

    chunks = _split_plaintext(text, "big.md")
    assert len(chunks) > 1
    # Every chunk bounded by the budget (small slack for join separators).
    assert all(len(t) <= _PLAINTEXT_CHUNK_CHARS + 200 for _, t in chunks)
    # No content lost: every requirement sentence survives in some chunk.
    joined = "\n".join(t for _, t in chunks)
    for i in (0, 30, 59):
        assert f"El sistema debe validar el registro " in joined
    # Headings become breadcrumbs.
    assert any("Capítulo 3" in c for c, _ in chunks)


def test_fenced_code_hash_lines_are_not_headings(tmp_path):
    text = (
        "# Especificación\n\n"
        "Texto inicial.\n\n"
        "```bash\n"
        "# este no es un título\n"
        "make build\n"
        "```\n\n"
        "Cierre.\n"
    )
    p = _write(tmp_path, "doc.md", text)
    chunks = _chunk_plaintext(p, str(p))
    assert len(chunks) == 1
    assert "# este no es un título" in chunks[0].text

    sections = _plaintext_sections(text, "doc.md")
    titles = [s.title for s in sections]
    assert titles == ["Especificación"]


def test_csv_without_blank_lines_hard_splits_by_line(tmp_path):
    # A CSV has no blank lines -> one oversized piece -> hard line split.
    lines = [f"col1,col2,valor-{i},{i}" for i in range(2000)]  # ~24 bytes/line
    text = "\n".join(lines)
    assert len(text) > _PLAINTEXT_CHUNK_CHARS

    chunks = _split_plaintext(text, "tabla.csv")
    assert len(chunks) > 1
    assert all(len(t) <= _PLAINTEXT_CHUNK_CHARS + 200 for _, t in chunks)
    joined = "\n".join(t for _, t in chunks)
    assert lines[0] in joined
    assert lines[-1] in joined
    # No headings in a CSV -> fallback breadcrumb everywhere.
    assert all(crumb == "tabla.csv" for crumb, _ in chunks)
    # Tabular piece -> the header row is repeated on every continuation chunk.
    header = lines[0]
    assert all(t.splitlines()[0] == header for _, t in chunks[1:])


def test_oversized_markdown_pipe_table_repeats_header(tmp_path):
    rows = [f"| REQ-{i} | descripción " + "x" * 60 + f" | {i} |" for i in range(500)]
    text = "# Tabla\n\n" + "| ID | descripción | prio |\n" + "\n".join(rows) + "\n"
    p = _write(tmp_path, "tabla.md", text)
    chunks = _chunk_plaintext(p, str(p))
    assert len(chunks) > 1
    assert all(c.text.splitlines()[0] == "| ID | descripción | prio |" for c in chunks[1:])


def test_oversized_plain_paragraph_does_not_repeat_first_line(tmp_path):
    # A non-tabular oversized block (hard-wrapped lines, no blanks) must NOT
    # duplicate its first line on continuation chunks.
    body = "\n".join(
        "El sistema debe procesar registros de forma segura y auditable "
        f"en el lote {i}." for i in range(600)
    )
    text = "# Bloque\n\n" + body + "\n"
    assert len(text) > _PLAINTEXT_CHUNK_CHARS
    chunks = _split_plaintext(text, "bloque.txt")
    assert len(chunks) > 1
    first_line = chunks[0][1].splitlines()[0]
    for _, t in chunks[1:]:
        assert not t.startswith(first_line)
    # No content lost.
    joined = "\n".join(t for _, t in chunks)
    assert f"en el lote 0." in joined
    assert f"en el lote 599." in joined


def test_chunk_never_ends_on_a_heading(monkeypatch):
    import backend.agents.pipelines.ingestion as ing

    # Small budget forces boundaries everywhere; a naive packer would
    # frequently close a chunk right after a heading line.
    monkeypatch.setattr(ing, "_PLAINTEXT_CHUNK_CHARS", 200)
    parts = []
    for i in range(30):
        parts.append(f"## Sección {i}\n\n" + "El sistema debe hacer algo.\n\n" * 12)
    text = "".join(parts)

    chunks = ing._split_plaintext(text, "doc.md")
    assert len(chunks) > 1
    for chunk_text in (t for _, t in chunks):
        lines = [l for l in chunk_text.splitlines() if l.strip()]
        assert lines, "no empty chunks expected"
        assert not ing._ATX_HEADING_RE.match(lines[-1].strip()), (
            f"chunk ends on heading: {lines[-1]}"
        )
    # Content still fully preserved.
    joined = "\n".join(t for _, t in chunks)
    assert joined.count("El sistema debe hacer algo.") == 30 * 12


def test_sections_fallback_without_headings(tmp_path):
    sections = _plaintext_sections("Solo texto plano.\n", "notas.txt")
    assert len(sections) == 1
    assert sections[0].title == "notas.txt"
    assert sections[0].level == 1
