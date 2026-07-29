"""Contract: the vision converter rasterizes PDF pictures at a configurable
scale so vector diagrams are legible to the vision model.

Regression: sigsa_srs.pdf image 2 is a vector DB schema (a PowerDesigner
export -- "DIAGRAMA DE LA BASE DE DATOS SIGSA"). With Docling's default
images_scale=1.0 it rasterized to 317x594 and became illegible, so glm-4.6v
hallucinated TABLA/CAMPO placeholders. At images_scale=4.0 (the config default)
the same picture rasterizes to ~1270x2376 and glm-4.6v reads the real tables
(NOVEDADES, INCIDENCIAS, DETALLE_INCIDENCIA, ...), fields, PK/FK and the 1:N
relationships. The model was never the problem -- the image resolution was.
"""
from docling.datamodel.base_models import InputFormat

from backend.agents.pipelines.ingestion import _make_vision_converter
from backend.config import settings


def test_vision_converter_sets_images_scale_for_legible_pictures():
    converter = _make_vision_converter()
    opts = converter.format_to_options[InputFormat.PDF].pipeline_options
    assert opts.generate_picture_images is True
    # Must override the Docling default (1.0) with the configured scale so
    # vector diagrams rasterize at a readable resolution (~72 * scale DPI).
    assert opts.images_scale == settings.vision_images_scale
    assert settings.vision_images_scale >= 2.0
