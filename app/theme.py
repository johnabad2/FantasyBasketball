"""The app's colours (dark theme) for the window and its charts, in one place.

Every module reads its colours from `C`. Chart data colours (above/below average, the two compared
players) are colour-blind-safe pairs validated against the dark chart surface."""

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
    "red": "#f87171", "amber": "#fbbf24", "green": "#4ade80", "chat": "#93c5fd",
    "chart_surface": "#1a1a19", "chart_ink": "#f0efec", "chart_ink2": "#c3c2b7", "chart_muted": "#a8a69d",
    "chart_grid": "#2c2c2a", "chart_axis": "#46463f", "chart_wash": "#26303d", "chart_other": "#5c5b55",
    "chart_border": "#2c3747", "tooltip_bg": "#262624", "tooltip_border": "#46463f",
    "above": "#3987e5", "below": "#e66767", "series": ["#3987e5", "#d95926"],
}


C = DARK
NAME = "dark"


def blend(color, toward, t):
    """Mix two #rrggbb colours: t=0 gives `color`, t=1 gives `toward`. Tk has no transparency, so
    glows are drawn as rings of colours fading into the background."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(toward[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


GLOW = True  # subtle chart glow (dark backgrounds only - which is all the app uses)
