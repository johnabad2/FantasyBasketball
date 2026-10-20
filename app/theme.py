"""Light and dark colour themes for the window and its charts.

The choice lives in data/ui.json (dark by default). Every module reads its colours from `C` when
it's imported, so switching themes restarts the window. Chart data colours (above/below average,
the two compared players) are validated colour-blind-safe pairs in both themes."""
import json

from stats import DATA

PREF = DATA / "ui.json"

LIGHT = {
    # window chrome
    "header": "#14213d", "header_text": "#ffffff", "header_sub": "#9fb3cf", "header_status": "#c7d3e3",
    "accent": "#f26b1d", "accent_dark": "#d4560b", "accent_disabled": "#f5b48f",
    "page": "#e9eef5", "card": "#ffffff", "border": "#cfd8e3",
    "text": "#111827", "text_2": "#374151", "muted": "#4b5563",
    "stripe": "#f3f6fa", "me_bg": "#ffe9d9",
    "heading_bg": "#1f3a5f", "heading_active": "#2b4c7e", "heading_text": "#ffffff",
    "select_bg": "#1f3a5f", "select_fg": "#ffffff",
    "tab_bg": "#d7dfea", "tab_active": "#e6ecf4", "tab_selected": "#ffffff", "tab_selected_fg": "#14213d",
    "button_bg": "#dfe6ef", "button_active": "#cfd9e6", "toggle_on": "#1f3a5f",
    "field_bg": "#ffffff", "photo_bg": "#e9ecf1",
    "red": "#c62828", "amber": "#b26a00", "green": "#2e7d32",
    # charts
    "chart_surface": "#fcfcfb", "chart_ink": "#0b0b0b", "chart_ink2": "#52514e", "chart_muted": "#66655f",
    "chart_grid": "#e1e0d9", "chart_axis": "#c3c2b7", "chart_wash": "#eef3fb", "chart_other": "#c3c2b7",
    "chart_border": "#e3e3e0", "tooltip_bg": "#ffffff", "tooltip_border": "#d6d5cf",
    "above": "#2a78d6", "below": "#d9493f", "series": ["#2a78d6", "#eb6834"],
}

DARK = {
    "header": "#0a0f1a", "header_text": "#f3f4f6", "header_sub": "#8ea2c0", "header_status": "#b7c4d8",
    "accent": "#f26b1d", "accent_dark": "#d4560b", "accent_disabled": "#7a3f1d",
    "page": "#111722", "card": "#1a2230", "border": "#2c3747",
    "text": "#e8edf4", "text_2": "#c4cdd9", "muted": "#9aa7b8",
    "stripe": "#202a3a", "me_bg": "#4a2a14",
    "heading_bg": "#26364f", "heading_active": "#31476a", "heading_text": "#f3f4f6",
    "select_bg": "#2f5a9e", "select_fg": "#ffffff",
    "tab_bg": "#1d2635", "tab_active": "#263245", "tab_selected": "#1a2230", "tab_selected_fg": "#ffffff",
    "button_bg": "#26324a", "button_active": "#31405c", "toggle_on": "#2f5a9e",
    "field_bg": "#1a2230", "photo_bg": "#232d3d",
    "red": "#f87171", "amber": "#fbbf24", "green": "#4ade80",
    "chart_surface": "#1a1a19", "chart_ink": "#f0efec", "chart_ink2": "#c3c2b7", "chart_muted": "#a8a69d",
    "chart_grid": "#2c2c2a", "chart_axis": "#46463f", "chart_wash": "#26303d", "chart_other": "#5c5b55",
    "chart_border": "#2c3747", "tooltip_bg": "#262624", "tooltip_border": "#46463f",
    "above": "#3987e5", "below": "#e66767", "series": ["#3987e5", "#d95926"],
}


def current():
    try:
        return json.loads(PREF.read_text()).get("theme", "dark")
    except (OSError, ValueError):
        return "dark"


def save(name):
    DATA.mkdir(exist_ok=True)
    try:
        prefs = json.loads(PREF.read_text())
    except (OSError, ValueError):
        prefs = {}
    prefs["theme"] = name
    PREF.write_text(json.dumps(prefs))


NAME = current()
C = DARK if NAME == "dark" else LIGHT
