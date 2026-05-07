# Andromeda

Andromeda is a FlightGear scenery editor focused on fast STG object placement, scene inspection, and save-back workflows.

It is designed for editing `.stg` tiles with immediate visual feedback, object catalog browsing, and precise transform controls.

## Highlights

- Open and inspect `.stg`, `.btg`, and `.btg.gz` scenes.
- Add objects from FlightGear `Objects/` and `Models/` catalogs.
- Select, move, rotate, copy, paste, delete, and cycle scene objects.
- Mouse edit mode for fast drag-based object transforms.
- Selected-object terrain shadow footprint (OpenGL path).
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

Windows one-time setup:

1. Double-click `install.bat` in the project folder.
2. After it finishes, double-click `andromeda_launcher.bat` to run.
3. Optional: drag and drop a `.stg` file onto `andromeda_launcher.bat`.

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
2. Go to **Options -> Configuration -> Flightgear Location** and set your FG data root (must contain `Textures` and `Materials`).
3. Go to **File -> Load** and open your target `.stg`.
4. Use **Add Object** to place models.
5. Save with **File -> Save** (or **File -> Save As**).

## Scenery Packaging

You can create a shareable scenery package directly from the current STG scene:

1. Open **File -> Create Scenery Package**.
2. In the folder requester, navigate to the destination.
3. Optional: use **[Create Folder]** to create a new distribution folder and enter its name.
4. Choose **[Select This Folder]**.
5. Andromeda copies discovered dependencies (STG references plus related XML/AC/texture assets), writes `scenery_readme.txt` from the distribution template, and creates `<selected_folder>.zip`.
6. Review the summary dialog (**Zip Location**, **File contents**, **File Size**) and click **OK** to return to the menu.

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
- Mouse capture OFF: crosshair follows mouse cursor
- Left drag (mouse capture OFF): move selected object X/Y
- `Shift` + left drag: mouse Y moves object Z, mouse X yaws object
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

## Selection Aids

- In OpenGL mode, the selected object can display a projected footprint shadow
	on the terrain to make placement easier.
- Label chips and crosshair-hover labels help identify dense object scenes.

## Recent Improvements

- Scenery packaging workflow: **File -> Create Scenery Package** now builds a distributable zip from current STG dependencies and shows a post-build summary dialog.
- Package requester quality-of-life: added **[Create Folder]** with in-app folder-name prompt, so you can make a distribution folder without leaving Andromeda.
- Windows path picker update: when browsing upward to filesystem root, drive letters are now listed so you can switch drives while selecting FlightGear paths.
- Windows editing performance update: object move/rotate operations now include faster preview behavior, shadow rebuild throttling during active edits, and render-stage timing metrics.
- Shadow sampling acceleration: static-scene terrain now uses a spatial index with adaptive cell sizing and cached footprint candidates for faster projected-shadow updates.
- STG save dedupe fix: save now skips writing duplicate object entries at the same object path and position, preventing repeated duplication across saves.
- Object deletion acceleration: delete now attempts a fast in-memory mesh patch path before falling back to a full scene rebuild.

## Config File

Andromeda persists settings in:

- `${XDG_CONFIG_HOME}/flightgear_btg_viewer.json` (if `XDG_CONFIG_HOME` is set), otherwise
- `~/.config/flightgear_btg_viewer.json`

## Notes

- If `PyOpenGL` is unavailable, Andromeda runs in software mode.
- If textures appear missing, verify **Flightgear Location** points to a valid FG data root.
