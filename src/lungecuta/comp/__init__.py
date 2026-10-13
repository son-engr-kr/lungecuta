"""A comp resolved to frames and samples: its beat grid from its music layers, each
footage layer's visible frames and the source frames they show, each audio layer's
samples, and its markers (docs/design/03-project-format.md).

`loading` reads comp and style files; `parts` holds what a comp resolves to; `grid` makes
the beat grid; `resolve` makes the rest."""
from .grid import beat_grid, literals_past
from .loading import COMPS_DIR, MIX_RATE, STYLES_DIR, load_comp, load_style
from .parts import Cut, MarkerAt, Nest, Overlay, Placement, Resolved, Sound
from .resolve import anchor_samples, resolve

__all__ = ["COMPS_DIR", "MIX_RATE", "STYLES_DIR", "Cut", "MarkerAt", "Nest", "Overlay", "Placement", "Resolved",
           "Sound", "anchor_samples", "beat_grid", "literals_past", "load_comp", "load_style", "resolve"]
