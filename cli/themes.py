THEMES = {
    "dark": {
        "bg-primary": "#1a1a2e",
        "bg-secondary": "#16213e",
        "bg-input": "#222222",
        "text-primary": "#cccccc",
        "text-secondary": "#aaaaaa",
        "text-muted": "#888888",
        "accent": "#5f87ff",
        "accent-green": "#87d787",
        "accent-red": "#ff5f5f",
        "border": "#444444",
        "folder-color": "#d4a5ff",
    },
    "light": {
        "bg-primary": "#f5f5f5",
        "bg-secondary": "#ffffff",
        "bg-input": "#ffffff",
        "text-primary": "#333333",
        "text-secondary": "#555555",
        "text-muted": "#757575",
        "accent": "#1a73e8",
        "accent-green": "#2e7d32",
        "accent-red": "#d32f2f",
        "border": "#cccccc",
        "folder-color": "#7b1fa2",
    },
    "default": {},
}


def get_theme_css(theme_name: str) -> str:
    return ""