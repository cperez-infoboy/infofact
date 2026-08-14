"""Tests de _RelayAccumulator: topes anti-veneno del relay SSE (sesión 44).

Casos del plan:
  (a) deltas chicos se emiten todos como frames ``token``;
  (b) UN delta de 1.1M chars se PARTE en frames de <= max_delta hasta el
      techo del turno + EXACTAMENTE UN ``relay.truncated`` con el resto;
  (c) superado el techo del turno deja de emitir tokens y ``result()``
      devuelve el acumulado capado + marcador con la cuenta correcta;
  (d) ``result()`` sin overflow no tiene marcador.
"""
from __future__ import annotations

import json

from backend.routers.chat import _RelayAccumulator


def _parse(frame: str) -> tuple[str, dict]:
    """``'event: x\\ndata: {...}\\n\\n'`` -> ``('x', {...})``."""
    event_line, data_line = frame.strip().split("\n")
    assert event_line.startswith("event: ")
    assert data_line.startswith("data: ")
    return event_line[len("event: "):], json.loads(data_line[len("data: "):])


def test_small_deltas_all_emitted_as_token_frames():
    acc = _RelayAccumulator(max_delta=2_000, max_total=30_000)
    frames = acc.add("Hol") + acc.add("a")

    parsed = [_parse(f) for f in frames]
    assert parsed == [
        ("token", {"delta": "Hol"}),
        ("token", {"delta": "a"}),
    ]
    assert acc.shown == 4
    assert acc.omitted == 0


def test_giant_delta_is_split_and_total_cap_truncates_once():
    acc = _RelayAccumulator(max_delta=2_000, max_total=30_000)
    frames = acc.add("x" * 1_100_000)

    parsed = [_parse(f) for f in frames]
    tokens = [data for event, data in parsed if event == "token"]
    truncated = [data for event, data in parsed if event == "relay.truncated"]

    # (b) el delta se parte en frames de <= max_delta hasta llenar el
    # presupuesto del turno (30.000 = 15 frames de 2.000)...
    assert len(tokens) == 15
    assert all(t["delta"] == "x" * 2_000 for t in tokens)
    # ...y EXACTAMENTE UN relay.truncated con el resto descartado.
    assert truncated == [
        {"shown": 30_000, "omitted": 1_070_000, "limit": 30_000}
    ]
    assert acc.omitted == 1_070_000
    assert acc.result().startswith("x" * 30_000)


def test_big_legit_delta_is_split_not_dropped():
    """Regresión sesión 44: un delta legítimo de 3.619 chars perdía 1.619
    chars reales; ahora se parte en frames sin descartar nada."""
    acc = _RelayAccumulator(max_delta=2_000, max_total=30_000)
    frames = acc.add("y" * 3_619)

    parsed = [_parse(f) for f in frames]
    assert [event for event, _ in parsed] == ["token", "token"]
    deltas = [data["delta"] for event, data in parsed if event == "token"]
    assert "".join(deltas) == "y" * 3_619
    assert acc.omitted == 0
    assert acc.result() == "y" * 3_619


def test_total_cap_stops_tokens_and_result_has_marker_with_count():
    acc = _RelayAccumulator(max_delta=2_000, max_total=30_000)

    frames: list[str] = []
    for _ in range(15):
        frames.extend(acc.add("a" * 2_000))
    # 15 * 2_000 = 30_000 exactos: todo emitido, sin descartes todavía.
    assert acc.shown == 30_000
    assert acc.omitted == 0
    assert all(event == "token" for event, _ in map(_parse, frames))

    # 16º delta: no queda room -> se descarta entero y notifica UNA vez.
    more = acc.add("b" * 500)
    parsed = [_parse(f) for f in more]
    assert [event for event, _ in parsed] == ["relay.truncated"]
    assert parsed[0][1] == {"shown": 30_000, "omitted": 500, "limit": 30_000}

    # 17º delta: ya notificado -> no emite NADA.
    assert acc.add("c" * 100) == []

    # (c) result(): acumulado capado + marcador con la cuenta final (500+100).
    assert acc.result() == "a" * 30_000 + (
        "\n\n[Salida truncada: se omitieron 600 caracteres (limite 30000).]"
    )


def test_result_without_overflow_has_no_marker():
    acc = _RelayAccumulator(max_delta=2_000, max_total=30_000)
    acc.add("hola")

    assert acc.result() == "hola"
    assert "Salida truncada" not in acc.result()
