"""Smoke test for the vision client (backend/agents/vision.py).

Usage:
    python scripts/smoke_vision.py [<image> ...]

With no arguments it generates two synthetic images (a flow diagram and a UI
login mockup) with PIL and runs the vision pipeline on them. With arguments it
runs on the given image paths instead.

Checks:
    - settings.supports_vision is True (LLM_API_KEY + a vision model configured)
    - classify_image_kind returns a valid ImageKind for each image
    - describe_image returns non-empty text for each image
    - prints detected kind + a truncated description per image

Exit 1 if vision is not configured or any image yields an empty description.
First run hits the Z.ai vision API (glm-4.6v by default); network + key needed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make the repo root importable so `backend...` resolves when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.agents.vision import (  # noqa: E402
    classify_image_kind,
    describe_image,
)

_VALID_KINDS = {"diagram", "mockup", "generic"}


def _font(size: int):
    """Best-effort TrueType font; fall back to PIL's default bitmap font."""
    from PIL import ImageFont

    for candidate in ("DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _make_diagram(path: Path) -> None:
    """Draw a simple three-node flow: Cliente -> Web -> Base de datos."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 260), "white")
    draw = ImageDraw.Draw(img)
    font = _font(18)
    boxes = [(40, 100, 200, 160, "Cliente"),
             (260, 100, 380, 160, "Web"),
             (440, 100, 620, 160, "Base de datos")]
    for x0, y0, x1, y1, label in boxes:
        draw.rectangle([x0, y0, x1, y1], outline="black", width=2)
        draw.text((x0 + 10, y0 + 22), label, fill="black", font=font)
    draw.line([(200, 130), (260, 130)], fill="black", width=2)
    draw.line([(380, 130), (440, 130)], fill="black", width=2)
    # Arrowheads
    draw.polygon([(260, 130), (250, 124), (250, 136)], fill="black")
    draw.polygon([(440, 130), (430, 124), (430, 136)], fill="black")
    img.save(path, format="PNG")


def _make_mockup(path: Path) -> None:
    """Draw a simple login form mockup."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (480, 360), "#f5f5f5")
    draw = ImageDraw.Draw(img)
    font = _font(18)
    title_font = _font(24)
    draw.rectangle([120, 30, 360, 70], outline="black", width=2)
    draw.text((150, 38), "Iniciar sesion", fill="black", font=title_font)
    draw.text((120, 110), "Usuario:", fill="black", font=font)
    draw.rectangle([120, 140, 360, 175], outline="black", width=2)
    draw.text((120, 200), "Clave:", fill="black", font=font)
    draw.rectangle([120, 230, 360, 265], outline="black", width=2)
    draw.rectangle([180, 290, 300, 330], fill="#1a73e8", outline="black", width=2)
    draw.text((205, 299), "Ingresar", fill="white", font=font)
    img.save(path, format="PNG")


def main(argv: list[str]) -> int:
    if not settings.supports_vision:
        print(
            "vision is not configured: set LLM_API_KEY and LLM_VISION_MODEL.",
            file=sys.stderr,
        )
        return 1

    images: list[Path] = []
    cleanup: list[Path] = []
    if len(argv) > 1:
        for raw in argv[1:]:
            p = Path(raw).expanduser().resolve()
            if not p.is_file():
                print(f"not a file: {p}", file=sys.stderr)
                return 1
            images.append(p)
    else:
        tmpdir = Path(tempfile.mkdtemp(prefix="smoke_vision_"))
        diagram = tmpdir / "diagrama.png"
        mockup = tmpdir / "mockup.png"
        _make_diagram(diagram)
        _make_mockup(mockup)
        images = [diagram, mockup]
        cleanup = [diagram, mockup, tmpdir]

    rc = 0
    for img_path in images:
        print(f"\n=== {img_path.name} ===")
        try:
            kind = classify_image_kind(img_path)
        except Exception as exc:  # noqa: BLE001 - surface any failure clearly
            print(f"  classify_image_kind FAILED: {exc}", file=sys.stderr)
            rc = 1
            continue
        print(f"  kind: {kind}")
        if kind not in _VALID_KINDS:
            print(f"  unexpected kind: {kind!r}", file=sys.stderr)
            rc = 1
        try:
            description = describe_image(img_path, kind=kind)
        except Exception as exc:  # noqa: BLE001
            print(f"  describe_image FAILED: {exc}", file=sys.stderr)
            rc = 1
            continue
        if not description or not description.strip():
            print("  describe_image returned EMPTY text", file=sys.stderr)
            rc = 1
            continue
        preview = description.strip().replace("\n", " ")
        if len(preview) > 400:
            preview = preview[:400] + "..."
        print(f"  description: {preview}")

    for p in cleanup:
        try:
            if p.is_dir():
                p.rmdir()
            else:
                p.unlink()
        except OSError:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
