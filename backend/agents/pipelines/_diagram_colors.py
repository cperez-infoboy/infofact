"""Shared semantic color palette for Mermaid diagram renderers.

All colors are designed for dark backgrounds (the app's default theme).
Each diagram renderer imports these constants and emits ``classDef`` lines
so that nodes, entities, and states are visually grouped by their semantic
role — making diagrams self-explanatory at a glance.
"""

from __future__ import annotations

TEXT_COLOR = "#e2e8f0"

# ---------------------------------------------------------------------------
# Architecture diagrams — by layer
# ---------------------------------------------------------------------------
LAYER_COLORS: dict[str, tuple[str, str]] = {
    "presentation": ("#1e3a5f", "#3b82f6"),  # blue
    "application": ("#1a4d3a", "#14b8a6"),  # teal/green
    "data": ("#4a3a1a", "#d97706"),  # amber
    "external": ("#3a3a3a", "#6b7280"),  # gray
}

# Component-type overrides (more specific than layer).
TYPE_COLORS: dict[str, tuple[str, str]] = {
    "database": ("#4a3a1a", "#d97706"),  # amber
    "auth": ("#3a1a4a", "#7c3aed"),  # violet
    "cache": ("#1a4a4a", "#06b6d4"),  # cyan
    "messaging": ("#4a1a3a", "#ec4899"),  # magenta
    "agent": ("#4a2a1a", "#ea580c"),  # orange
}

# ---------------------------------------------------------------------------
# ER diagrams — by bounded context
# ---------------------------------------------------------------------------
BC_PALETTE: list[tuple[str, str]] = [
    ("#1e3a5f", "#3b82f6"),  # blue
    ("#1a4d3a", "#14b8a6"),  # teal
    ("#4a3a1a", "#d97706"),  # amber
    ("#3a1a4a", "#7c3aed"),  # violet
    ("#4a1a3a", "#ec4899"),  # magenta
    ("#1a4a4a", "#06b6d4"),  # cyan
    ("#4a2a1a", "#ea580c"),  # orange
    ("#2a2a4a", "#6366f1"),  # indigo
]

AGGREGATE_ROOT_STROKE = "#fbbf24"  # gold

# ---------------------------------------------------------------------------
# State diagrams — by role
# ---------------------------------------------------------------------------
STATE_INITIAL: tuple[str, str] = ("#1a4d3a", "#14b8a6")  # green
STATE_FINAL: tuple[str, str] = ("#4a1a1a", "#dc2626")  # red
STATE_NORMAL: tuple[str, str] = ("#1e3a5f", "#3b82f6")  # blue
STATE_ERROR: tuple[str, str] = ("#5a1a1a", "#ef4444")  # bright red

# ---------------------------------------------------------------------------
# Infrastructure — by network zone
# ---------------------------------------------------------------------------
NETWORK_COLORS: dict[str, tuple[str, str]] = {
    "frontend": ("#1e3a5f", "#3b82f6"),  # blue
    "dmz": ("#1e3a5f", "#3b82f6"),
    "backend": ("#1a4d3a", "#14b8a6"),  # green
    "internal": ("#1a4d3a", "#14b8a6"),
    "database": ("#4a3a1a", "#d97706"),  # amber
    "data": ("#4a3a1a", "#d97706"),
    "monitoring": ("#1a4a4a", "#06b6d4"),  # cyan
    "external": ("#3a3a3a", "#6b7280"),  # gray
}

# Default color for unknown networks/zones.
NETWORK_DEFAULT: tuple[str, str] = ("#3a3a3a", "#6b7280")


def class_def(
    name: str,
    fill: str,
    stroke: str,
    text: str = TEXT_COLOR,
    sw: int = 2,
) -> str:
    """Generate a Mermaid ``classDef`` line.

    Example::

        >>> class_def("L_presentation", "#1e3a5f", "#3b82f6")
        'classDef L_presentation fill:#1e3a5f,stroke:#3b82f6,stroke-width:2px,color:#e2e8f0;'
    """
    return (
        f"classDef {name} fill:{fill},stroke:{stroke},"
        f"stroke-width:{sw}px,color:{text}"
    )


def network_color(network: str) -> tuple[str, str]:
    """Return (fill, stroke) for a network name, matching known zones.

    Falls back to substring matching (e.g. ``"frontend-net"`` → blue)
    and ultimately to :data:`NETWORK_DEFAULT`.
    """
    net_lower = network.lower()
    # Exact match first.
    if net_lower in NETWORK_COLORS:
        return NETWORK_COLORS[net_lower]
    # Substring match.
    for key, color in NETWORK_COLORS.items():
        if key in net_lower:
            return color
    return NETWORK_DEFAULT
