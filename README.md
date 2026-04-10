# Andromeda

Andromeda is a FlightGear scenery editor focused on fast STG object placement, scene inspection, and save-back workflows.

It is designed for editing `.stg` tiles with immediate visual feedback, object catalog browsing, and precise transform controls.

## Highlights

- Open and inspect `.stg`, `.btg`, and `.btg.gz` scenes.
- Add objects from FlightGear `Objects/` and `Models/` catalogs.
- Select, move, rotate, copy, paste, delete, and cycle scene objects.
- Save back to STG while preserving non-object lines.
- Software and OpenGL render paths.
- Localized UI/help text files (English, Spanish, French, German).

## Requirements

- Python 3.10+
- `pygame`
- Optional but recommended: `PyOpenGL`

Install dependencies:

```bash
python3 -m pip install pygame PyOpenGL
```

## Quick Start

Launch with auto renderer:

```bash
python3 andromeda.py /path/to/tile.stg
```

Useful options:

```bash
python3 andromeda.py /path/to/tile.stg --software
python3 andromeda.py /path/to/tile.stg --opengl
python3 andromeda.py /path/to/tile.stg --flightgear /path/to/fgdata
python3 andromeda.py /path/to/tile.stg --material-map /path/to/material_map.json
```

## First Launch Checklist

1. Press `Esc` to open the menu.
2. Set **Flightgear Location** to your FG data root (must contain `Textures` and `Materials`).
3. Load your target `.stg` with **Load STG**.
4. Use **Add Object** to place models.
5. Save with **Save** (or **Save As STG**).

## Core Controls

- `Esc`: open/close menu
- Right mouse button: toggle fly/no-fly mouse capture
- `W/S/A/D/R/F`: move camera
- Mouse wheel or `-`/`+`: speed down/up
- `Space`: add object menu
- `Ctrl+S`: save STG
- `Ctrl+C`, `Ctrl+V`, `Delete`: copy/paste/delete selected object
- Arrow keys: move selected object X/Y
- `Shift+Up/Down`: move selected object Z
- `Shift+Left/Right`: selected object yaw
- `H`: help overlay
- `Y`: textured view toggle
- `Tab`: wireframe toggle

For full usage and troubleshooting, see [MANUAL.md](MANUAL.md).

## Localization

UI language files:

- `onsreen_ui_english.txt`
- `onsreen_ui_spanish.txt`
- `onsreen_ui_french.txt`
- `onsreen_ui_german.txt`

Help overlay files:

- `onscreen_help_english.txt`
- `onscreen_help_spanish.txt`
- `onscreen_help_french.txt`
- `onscreen_help_german.txt`

Select language/help files from the in-app menu.

## Config File

Andromeda persists settings in:

- `${XDG_CONFIG_HOME}/flightgear_btg_viewer.json` (if `XDG_CONFIG_HOME` is set), otherwise
- `~/.config/flightgear_btg_viewer.json`

## Notes

- If `PyOpenGL` is unavailable, Andromeda runs in software mode.
- If textures appear missing, verify **Flightgear Location** points to a valid FG data root.
