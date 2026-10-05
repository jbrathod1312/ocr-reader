"""A lottery document in, every page read as one of the lottery's tables."""

from __future__ import annotations

from ..document import PageReading, Recogniser, read_document
from ..pdf_text import DPI
from .assemble import assemble_lottery


def read_lottery(
    data: bytes,
    recognise: Recogniser | None = None,
    dpi: int = DPI,
    row_overlap_ratio: float = 0.5,
) -> list[PageReading]:
    """
    Every page of `data`, read as the lottery paperwork it is.

    Pages come from the same places as for any document — a PDF's text layer, or
    the recogniser for a photograph or a scan — and leave in the same shape, so
    the viewer, the edits and the export need nothing of their own.
    """
    return read_document(data, recognise, dpi, row_overlap_ratio, assemble=assemble_lottery)
