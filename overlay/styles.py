"""Constantes visuais do overlay."""

COLOR_BG = "rgba(15, 17, 20, 235)"
COLOR_BORDER = "rgba(255, 255, 255, 40)"
COLOR_TEXT = "#E8E8E8"
COLOR_TEXT_DIM = "#9AA0A6"
COLOR_POSITIVE = "#4CD787"
COLOR_NEGATIVE = "#E5534B"
COLOR_NEUTRAL = "#E8E8E8"
COLOR_WARNING = "#E8B339"

FONT_FAMILY = "Consolas, 'Cascadia Mono', 'Courier New', monospace"

BASE_STYLESHEET = f"""
QLabel#panel {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
    padding: 10px 16px;
    font-family: {FONT_FAMILY};
}}
"""


def delta_color(value: float) -> str:
    if value > 0:
        return COLOR_POSITIVE
    if value < 0:
        return COLOR_NEGATIVE
    return COLOR_NEUTRAL
