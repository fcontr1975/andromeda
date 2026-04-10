# Andromeda Manual

This manual is a practical guide for first-time users and release testers.

## 1. What Andromeda Does

Andromeda edits FlightGear scenery object placement in `.stg` tiles.

Typical use cases:

- Place new static objects (models, AC/XML assets)
- Adjust object transforms (position/yaw/pitch/roll)
- Clean up scene composition
- Save updated `.stg` files quickly

## 2. Before You Launch

Prepare:

1. A FlightGear data root (FGDATA) with `Textures/` and `Materials/`.
2. A target tile `.stg` (or `.btg/.btg.gz`) you want to edit.
3. Python dependencies installed.

Install dependencies:

```bash
python3 -m pip install pygame PyOpenGL
```

## 3. Launching

Basic:

```bash
python3 andromeda.py /path/to/tile.stg
```

Renderer forcing:

```bash
python3 andromeda.py /path/to/tile.stg --software
python3 andromeda.py /path/to/tile.stg --opengl
```

Path overrides:

```bash
python3 andromeda.py /path/to/tile.stg --flightgear /path/to/fgdata
python3 andromeda.py /path/to/tile.stg --material-map /path/to/material_map.json
```

## 4. First 5 Minutes Workflow

1. Press `Esc` and open the main menu.
2. Go to **Flightgear Location** and choose FGDATA root.
3. Use **Load STG** to open your target tile.
4. Press `Space` for **Add Object**.
5. Pick a category and model.
6. Place/adjust object with keyboard or mouse drag.
7. Save with `Ctrl+S` or menu **Save**.

## 5. Camera and Navigation

- `W/S`: camera +Z / -Z
- `A/D`: camera left / right
- `R/F`: camera up / down
- `-` and `+` (or wheel): speed down/up
- Right mouse button: toggle capture (fly vs no-fly mode)
- `.`: reframe tile in view

Tip: no-fly mode is useful for precision object editing.

## 6. Object Selection and Editing

Selection:

- In no-fly mode, move cursor/crosshair over object and left-click to select.

Transform:

- Arrow keys: selected object X/Y move
- `Shift+Up/Down`: selected object Z move
- `Shift+Left/Right`: selected object yaw
- Numpad model angle controls (yaw/roll/pitch), with configurable angle step presets

Clipboard:

- `Ctrl+C`: copy selected object
- `Ctrl+V`: paste copied object
- `Delete`: delete selected object
- `PageUp/PageDown`: cycle catalog object candidates

Nudge tuning:

- `[` / `]`: nudge step down/up
- `Shift+[` / `Shift+]`: angle step preset down/up
- Menu has fields for nudge distance and repeat behavior.

## 7. Mouse Precision Mode

When capture is OFF:

- Left drag: move selected object in camera-projected X/Y
- `Shift` + left drag:
  - mouse Y controls object Z
  - mouse X controls object yaw

This is the fastest way to do creative fine placement.

## 8. Menu Features You Should Know

Main menu includes:

- Load STG
- Save / Save As STG
- Menu Language
- Help Text File / Reload Help Text
- Flightgear Location
- Custom Scenery Paths
- Grid Settings
- Add Object
- Preview Panel Location
- Set Missing Material Color
- Toggle Textured View
- Nudge and camera clipping/view setup
- Change Keyboard bindings

## 9. Localization Files

UI files:

- `onsreen_ui_english.txt`
- `onsreen_ui_spanish.txt`
- `onsreen_ui_french.txt`
- `onsreen_ui_german.txt`

Help files:

- `onscreen_help_english.txt`
- `onscreen_help_spanish.txt`
- `onscreen_help_french.txt`
- `onscreen_help_german.txt`

You can select these in-app from the menu.

## 10. Troubleshooting

### Textures are missing or magenta fallback is visible

- Set **Flightgear Location** to the correct FG data root.
- Confirm the root has `Textures/` and `Materials/`.
- Check model material names and any custom material map overrides.

### OpenGL is unavailable or unstable

- Install `PyOpenGL`, or run with `--software`.

### Add Object list is incomplete

- Verify object files exist under scanned `Models/` path.
- Add scenery roots in **Custom Scenery Paths**, then refresh catalog.

### I changed settings and want to inspect/reset persisted state

Config file path:

- `${XDG_CONFIG_HOME}/flightgear_btg_viewer.json`, or
- `~/.config/flightgear_btg_viewer.json`

## 11. Safe Release Testing Checklist

1. Launch with `--opengl` and `--software` once each.
2. Load a real `.stg` and verify static + model objects appear.
3. Add, move, copy/paste, delete one object.
4. Save and diff resulting `.stg`.
5. Switch each UI language file and verify menu/help render.
6. Reopen app and verify config persistence.
