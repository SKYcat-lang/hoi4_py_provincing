"""Load political map colors from common/countries/colors.txt."""
from __future__ import annotations

import colorsys
from pathlib import Path
import re


def load_country_colors(path: str) -> dict[str, list[int]]:
    try:
        source = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError:
        source = Path(path).read_text(encoding="cp1252")
    clean = re.sub(r"#[^\r\n]*", "", source)
    country_colors: dict[str, list[int]] = {}
    # A country contains color/color_ui blocks; select only the map's color.
    for country in re.finditer(r"\b([A-Za-z0-9_]+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", clean):
        match = re.search(r"\bcolor\s*=\s*(rgb|hsv)?\s*\{([^{}]*)\}", country[2], re.I)
        if match is None:
            continue
        try:
            channels = [float(value) for value in match[2].split()]
        except ValueError:
            continue
        maximum = 1 if (match[1] or "rgb").lower() == "hsv" else 255
        if len(channels) != 3 or not all(0 <= value <= maximum for value in channels):
            continue
        if maximum == 1:
            channels = [value * 255 for value in colorsys.hsv_to_rgb(*channels)]
        country_colors[country[1]] = [int(value + 0.5) for value in channels]
    return country_colors
