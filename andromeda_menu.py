#!/usr/bin/env python3
"""Menu and dialog helpers for BTG display app."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import pygame

from andromeda_backend import KeyBinding, clear_texture_caches, resolve_fgdata_root

if TYPE_CHECKING:
    from andromeda import BTGDisplayApp


class MenuMixin:
    _WINDOWS_DRIVES_ROOT = "::windows-drives-root::"

    def _menu_t(self: "BTGDisplayApp", key: str, default: str) -> str:
        value = self.menu_text_map.get(key)
        if isinstance(value, str) and value.strip():
            return value
        return default

    def _menu_tf(self: "BTGDisplayApp", key: str, default: str, **fmt_values: object) -> str:
        text = self._menu_t(key, default)
        if not fmt_values:
            return text
        try:
            return text.format(**fmt_values)
        except Exception:
            return default.format(**fmt_values) if "{" in default else default

    def _set_status_t(self: "BTGDisplayApp", key: str, default: str, **fmt_values: object) -> None:
        self.set_status(self._menu_tf(key, default, **fmt_values))

    def _main_menu_item_label(self: "BTGDisplayApp", item: str) -> str:
        key_map = {
            "Load STG": "main.load_stg",
            "Save": "main.save",
            "Save As STG": "main.save_as_stg",
            "Menu Language": "main.menu_language",
            "Help Text File": "main.help_text_file",
            "Reload Help Text": "main.reload_help_text",
            "Flightgear Location": "main.flightgear_location",
            "Custom Scenery Paths": "main.custom_scenery_paths",
            "Grid Settings": "main.grid_settings",
            "Add Object": "main.add_object",
            "Preview Panel Location": "main.preview_panel_location",
            "Set Missing Material Color": "main.set_missing_material_color",
            "Toggle Textured View": "main.toggle_textured_view",
            "Set Object Nudge Distance": "main.set_object_nudge_distance",
            "Set Object Nudge Repeat": "main.set_object_nudge_repeat",
            "Toggle Nudge Mode": "main.toggle_nudge_mode",
            "Set Camera Start View": "main.set_camera_start_view",
            "Set Camera Clipping": "main.set_camera_clipping",
            "Change Keyboard bindings": "main.change_keyboard_bindings",
            "Exit": "main.exit",
        }

        if item == "Toggle Textured View":
            state = self._menu_t("state.on", "ON") if self.textured_mode else self._menu_t("state.off", "OFF")
            template = self._menu_t("main.toggle_textured_view_fmt", "Toggle Textured View ({state})")
            try:
                return template.format(state=state)
            except Exception:
                return f"Toggle Textured View ({state})"

        if item == "Toggle Nudge Mode":
            mode = (
                self._menu_t("mode.camera_relative", "Camera Relative")
                if self.object_nudge_camera_relative
                else self._menu_t("mode.world_relative", "World Relative")
            )
            template = self._menu_t("main.toggle_nudge_mode_fmt", "Toggle Nudge Mode ({mode})")
            try:
                return template.format(mode=mode)
            except Exception:
                return f"Toggle Nudge Mode ({mode})"

        key = key_map.get(item)
        if key is None:
            return item
        return self._menu_t(key, item)

    def _resolve_menu_text_path(self: "BTGDisplayApp", file_path: str) -> str:
        candidate = os.path.abspath(file_path)
        if os.path.isfile(candidate):
            return candidate

        directory, name = os.path.split(candidate)
        match = re.match(r"^(onsreen_ui|onscreen_ui|onscreen_menus|_menu)_([A-Za-z0-9_-]+)\.txt$", name, re.IGNORECASE)
        if match is None:
            return candidate

        language = match.group(2)
        for prefix in ("onsreen_ui", "onscreen_ui", "onscreen_menus", "_menu"):
            alias_path = os.path.join(directory, f"{prefix}_{language}.txt")
            if os.path.isfile(alias_path):
                return os.path.abspath(alias_path)
        return candidate

    def _available_menu_language_entries(self: "BTGDisplayApp") -> List[Dict[str, object]]:
        root = os.path.abspath(os.path.dirname(__file__))
        candidates: List[Dict[str, object]] = []
        try:
            names = sorted(os.listdir(root), key=lambda value: value.lower())
        except Exception:
            return []

        pattern = re.compile(r"^(onsreen_ui|onscreen_ui|onscreen_menus|_menu)_([A-Za-z0-9_-]+)\.txt$", re.IGNORECASE)
        active = os.path.abspath(self.menu_text_file_path) if self.menu_text_file_path else ""
        active_resolved = self._resolve_menu_text_path(active) if active else ""

        prefix_priority = {
            "onsreen_ui": 0,
            "onscreen_ui": 1,
            "onscreen_menus": 2,
            "_menu": 3,
        }
        by_language: Dict[str, Dict[str, object]] = {}

        for name in names:
            match = pattern.match(name)
            if match is None:
                continue
            full_path = os.path.join(root, name)
            if not os.path.isfile(full_path):
                continue

            prefix_raw = match.group(1).lower()
            language_raw = match.group(2)
            rank = prefix_priority.get(prefix_raw, 99)
            existing = by_language.get(language_raw.lower())
            if existing is not None and int(existing.get("_rank", 99)) <= rank:
                continue

            language_label = language_raw.replace("_", " ").replace("-", " ").title()
            by_language[language_raw.lower()] = {
                "_rank": rank,
                "language_label": language_label,
                "path": full_path,
                "name": name,
            }

        for data in sorted(by_language.values(), key=lambda item: str(item["language_label"]).lower()):
            full_path = os.path.abspath(str(data["path"]))
            name = str(data["name"])
            language_label = str(data["language_label"])
            is_selected = full_path == active or full_path == active_resolved
            prefix = "[*] " if is_selected else "[ ] "
            display_name = f"{prefix}{language_label} ({name})"
            candidates.append(
                {
                    "name": display_name,
                    "path": full_path,
                    "is_dir": False,
                    "is_menu_language": True,
                    "color": (190, 240, 190) if is_selected else (220, 220, 220),
                }
            )

        return candidates

    def _load_menu_text_file(
        self: "BTGDisplayApp",
        file_path: str,
        *,
        persist: bool = True,
        silent: bool = False,
    ) -> bool:
        candidate = self._resolve_menu_text_path(file_path)
        parsed: Dict[str, str] = {}

        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                raw_lines = [line.rstrip("\r\n") for line in handle]
        except Exception as exc:
            if not silent:
                self._set_status_t(
                    "status.menu_text_load_failed_fmt",
                    "Menu text load failed: {error}",
                    error=exc,
                )
            return False

        for raw in raw_lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key:
                parsed[key] = value

        if not parsed:
            if not silent:
                self._set_status_t(
                    "status.menu_text_empty",
                    "Menu text load failed: file has no usable key=value lines",
                )
            return False

        self.menu_text_file_path = candidate
        self.menu_text_map = parsed
        if persist:
            try:
                self._persist_viewer_config()
            except Exception:
                pass
        if not silent:
            self._set_status_t(
                "status.menu_text_loaded_fmt",
                "Menu text file loaded: {path}",
                path=candidate,
            )
        return True

    def _menu_row_hit(self: "BTGDisplayApp", mouse_y: float, row_y: float, row_h: float, row_count: int) -> Optional[int]:
        if row_count <= 0:
            return None
        rel = float(mouse_y) - float(row_y)
        if rel < 0.0 or rel >= float(row_h) * float(row_count):
            return None
        idx = int(rel // float(row_h))
        if 0 <= idx < row_count:
            return idx
        return None

    def _reset_menu_hover_scroll(self: "BTGDisplayApp") -> None:
        self.menu_hover_scroll_accum = 0.0

    def _edge_hotspot_scroll_index(
        self: "BTGDisplayApp",
        mouse_y: float,
        panel_y: float,
        panel_h: float,
        current_index: int,
        item_count: int,
    ) -> Tuple[int, bool]:
        if item_count <= 0 or panel_h <= 1.0:
            self._reset_menu_hover_scroll()
            return current_index, False

        y = float(mouse_y)
        panel_top = float(panel_y)
        panel_bottom = panel_top + float(panel_h)
        if y < panel_top or y > panel_bottom:
            self._reset_menu_hover_scroll()
            return current_index, False

        hotspot_frac = max(0.02, min(0.45, float(getattr(self, "menu_hover_hotspot_fraction", 0.10))))
        top_limit = panel_top + panel_h * hotspot_frac
        bottom_limit = panel_top + panel_h * (1.0 - hotspot_frac)

        current = max(0, min(int(current_index), item_count - 1))
        direction = 0
        if y <= top_limit and current > 0:
            direction = -1
            depth = (top_limit - y) / max(1.0, panel_h * hotspot_frac)
        elif y >= bottom_limit and current < (item_count - 1):
            direction = 1
            depth = (y - bottom_limit) / max(1.0, panel_h * hotspot_frac)
        else:
            self._reset_menu_hover_scroll()
            return current, False

        strength = max(0.0, min(1.0, depth))
        speed = max(0.01, float(getattr(self, "menu_hover_scroll_speed_scale", 0.20)))
        self.menu_hover_scroll_accum += direction * speed * (0.5 + 0.5 * strength)

        step = 0
        if self.menu_hover_scroll_accum >= 1.0:
            step = int(self.menu_hover_scroll_accum)
        elif self.menu_hover_scroll_accum <= -1.0:
            step = int(self.menu_hover_scroll_accum)

        if step == 0:
            return current, True

        self.menu_hover_scroll_accum -= float(step)
        next_index = max(0, min(item_count - 1, current + step))
        return next_index, True

    def _menu_field_hit_index(
        self: "BTGDisplayApp",
        mouse_x: float,
        mouse_y: float,
        box_x: float,
        box_w: float,
        row_start: float,
        row_gap: float,
        box_h: float,
        field_count: int,
    ) -> Optional[int]:
        if mouse_x < box_x or mouse_x > (box_x + box_w):
            return None
        for i in range(field_count):
            row_y = row_start + i * row_gap
            if row_y <= mouse_y <= (row_y + box_h):
                return i
        return None

    def _wrap_menu_hint_lines(self: "BTGDisplayApp", text: str, max_width_px: int) -> List[str]:
        source = (text or "").strip()
        if not source:
            return []

        normalized = source.replace("\n", " ").replace("\t", " ")
        words = [w for w in normalized.split(" ") if w]
        if not words:
            return []

        lines: List[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if self.font_small.size(candidate)[0] <= max_width_px:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _draw_wrapped_menu_hint(self: "BTGDisplayApp", text: str) -> None:
        lines = self._wrap_menu_hint_lines(text, max(120, self.size[0] - 36))
        if not lines:
            return

        line_h = max(16, self.font_small.get_linesize())
        pad_x = 18
        pad_y = 7
        total_h = len(lines) * line_h + pad_y * 2
        box_y = self.size[1] - total_h - 8

        hint_bg = pygame.Surface((self.size[0], total_h), pygame.SRCALPHA)
        hint_bg.fill((6, 10, 14, 228))
        self.screen.blit(hint_bg, (0, box_y))

        for i, line in enumerate(lines):
            surf = self.font_small.render(line, True, (176, 176, 176))
            self.screen.blit(surf, (pad_x, box_y + pad_y + i * line_h))

    def _draw_wrapped_menu_hint_gl(self: "BTGDisplayApp", text: str) -> None:
        lines = self._wrap_menu_hint_lines(text, max(120, self.size[0] - 36))
        if not lines:
            return

        line_h = max(16, self.font_small.get_linesize())
        pad_x = 18
        pad_y = 7
        total_h = len(lines) * line_h + pad_y * 2
        box_y = self.size[1] - total_h - 8

        self._gl_draw_rect(0, box_y, self.size[0], total_h, (6 / 255.0, 10 / 255.0, 14 / 255.0, 0.90))
        for i, line in enumerate(lines):
            self._gl_draw_text(line, pad_x, box_y + pad_y + i * line_h, (176, 176, 176), self.font_small)

    def _menu_bottom_hint_text(self: "BTGDisplayApp") -> str:
        if self.menu_mode == "main":
            return self._menu_t(
                "hint.main",
                "Mouse: hover + click | Up/Down/PgUp/PgDn + Enter | ESC closes menu.",
            )

        if self.menu_mode == "bindings":
            if self.binding_capture_action:
                return f"Press new key for '{self.binding_labels[self.binding_capture_action]}' (ESC cancels)"
            return "Enter: rebind selected | ESC: back"

        if self.menu_mode == "file_browser":
            if self.file_browser_mode == "load":
                return self._menu_t(
                    "file_browser.hint_load",
                    "Mouse: single-click select, double-click open | Enter opens selected | Up/Down/PgUp/PgDn: navigate | ESC: back",
                )
            if self.file_browser_mode == "help_text":
                return self._menu_t(
                    "file_browser.hint_help_text",
                    "Mouse: single-click select, double-click apply | Enter applies selected file | Up/Down/PgUp/PgDn: navigate | ESC: back",
                )
            if self.file_browser_mode == "menu_language":
                return self._menu_t(
                    "file_browser.hint_menu_language",
                    "Mouse: single-click select, double-click apply language | Enter applies selected language file | Up/Down/PgUp/PgDn: navigate | ESC: back",
                )
            if self.file_browser_mode == "directory_select":
                return (
                    "Open folders to navigate | Select [Select This Folder] to confirm | "
                    "Enter: activate selected row | Up/Down/PgUp/PgDn: navigate | ESC: back"
                )
            return self._menu_t(
                "file_browser.hint_save_as",
                "Mouse: single-click select, double-click open/save | Enter opens/saves selected | Type to edit save name | Backspace/Delete: edit | ESC: back",
            )

        if self.menu_mode == "file_browser_overwrite_confirm":
            return "Y/Enter: overwrite | N/ESC: cancel"
        if self.menu_mode == "save_confirm":
            return "Click, Enter, Space, or ESC to continue"
        if self.menu_mode == "scene_switch_confirm":
            return "Y/Enter: save | N: discard | ESC/C: cancel"
        if self.menu_mode == "camera_view":
            return "Type numbers, '-' and '.'. Up/Down or Tab changes field. Enter applies and reframes."
        if self.menu_mode == "camera_clipping":
            return "Type numbers. Near must be > 0. Far 0 = auto. Up/Down or Tab changes field. Enter applies."
        if self.menu_mode == "grid_settings":
            return "Type numbers, '-' and '.'. Up/Down or Tab changes field. Enter applies. ESC goes back."
        if self.menu_mode == "preview_panel_location":
            return "Type numbers. Up/Down or Tab changes field. Left/Right changes style. Enter applies. ESC goes back."
        if self.menu_mode == "object_nudge":
            return "Type numbers, '-' and '.'. Enter applies. ESC goes back."
        if self.menu_mode == "object_nudge_repeat":
            return "Type numbers, '-' and '.'. Up/Down or Tab changes field. Enter applies. ESC goes back."
        if self.menu_mode == "missing_material_color":
            return "Type integers 0-255. Up/Down or Tab changes field. Enter applies. ESC goes back."
        if self.menu_mode == "custom_scenery":
            return "Enter/A: add path | Del/Backspace/X: remove selected | R: refresh catalog | Up/Down: select | ESC: back"
        if self.menu_mode == "add_object_cats":
            return "Mouse hover/click: select/open | Up/Down/PgUp/PgDn: navigate | Enter: open category | A: add path | ESC: back"
        if self.menu_mode == "add_object_files":
            return (
                "Mouse hover: preview model (slower scroll) | Click [..]: parent folder | Mouse click/Enter: place model | "
                "Up/Down/PgUp/PgDn: model +/-1 | Ctrl+PgUp/PgDn: prev/next category | A: add path | ESC: back"
            )
        return ""

    def _ask_save_before_scene_switch(
        self: "BTGDisplayApp",
        current_path: Optional[str],
        target_path: Optional[str],
    ) -> str:
        """Return one of: 'save', 'discard', 'cancel'."""
        current_name = os.path.basename(current_path) if current_path else "current scene"
        target_name = os.path.basename(target_path) if target_path else "new scene"
        self.scene_switch_confirm_message = self._menu_tf(
            "dialog.load_unsaved_switch_fmt",
            "Save changes to '{source}' before loading '{target}'?",
            source=current_name,
            target=target_name,
        )

        prev_show_menu = bool(self.show_menu)
        prev_menu_mode = str(self.menu_mode)
        prev_mouse_captured = bool(self.mouse_captured)
        self.show_menu = True
        self.menu_mode = "scene_switch_confirm"
        self.set_mouse_capture(False)

        choice: Optional[str] = None
        while self.running and choice is None:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    choice = "cancel"
                    break

                if event.type == pygame.KEYDOWN:
                    key_code = event.key
                    if key_code in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                        choice = "save"
                        break
                    if key_code == pygame.K_n:
                        choice = "discard"
                        break
                    if key_code in (pygame.K_ESCAPE, pygame.K_BACKSPACE, pygame.K_c):
                        choice = "cancel"
                        break

                if event.type == pygame.MOUSEBUTTONDOWN and int(getattr(event, "button", 0)) == 1:
                    pos = getattr(event, "pos", pygame.mouse.get_pos())
                    mx, my = float(pos[0]), float(pos[1])
                    save_rect, discard_rect, cancel_rect = self._scene_switch_confirm_button_rects()

                    sx, sy, sw, sh = save_rect
                    if sx <= mx <= sx + sw and sy <= my <= sy + sh:
                        choice = "save"
                        break

                    dx, dy, dw, dh = discard_rect
                    if dx <= mx <= dx + dw and dy <= my <= dy + dh:
                        choice = "discard"
                        break

                    cx, cy, cw, ch = cancel_rect
                    if cx <= mx <= cx + cw and cy <= my <= cy + ch:
                        choice = "cancel"
                        break

            self.render()
            pygame.time.wait(16)

        self.scene_switch_confirm_message = ""
        self.show_menu = prev_show_menu
        self.menu_mode = prev_menu_mode
        if prev_mouse_captured:
            self.set_mouse_capture(True)
        else:
            self.set_mouse_capture(False)
        return choice or "cancel"

    def _scene_switch_confirm_button_rects(
        self: "BTGDisplayApp",
    ) -> Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int], Tuple[int, int, int, int]]:
        panel_w = 860
        panel_h = 230
        panel_x = self.size[0] // 2 - panel_w // 2
        panel_y = self.size[1] // 2 - panel_h // 2
        btn_w = 148
        btn_h = 40
        gap = 18
        total_w = btn_w * 3 + gap * 2
        row_x = panel_x + panel_w // 2 - total_w // 2
        btn_y = panel_y + panel_h - 64
        save_rect = (row_x, btn_y, btn_w, btn_h)
        discard_rect = (row_x + btn_w + gap, btn_y, btn_w, btn_h)
        cancel_rect = (row_x + (btn_w + gap) * 2, btn_y, btn_w, btn_h)
        return save_rect, discard_rect, cancel_rect

    def _remember_browse_path(self: "BTGDisplayApp", path: str) -> None:
        if not path:
            return
        candidate = path
        if os.path.isfile(candidate):
            candidate = os.path.dirname(candidate)
        candidate = os.path.abspath(candidate)
        if not os.path.isdir(candidate):
            return
        self.last_browse_dir = candidate
        try:
            self._persist_viewer_config()
        except Exception:
            pass

    def _remember_add_object_category(self: "BTGDisplayApp", category: str) -> None:
        if not category:
            return
        self.last_add_object_category = category
        try:
            self._persist_viewer_config()
        except Exception:
            pass

    def _apply_flightgear_root_directory(self: "BTGDisplayApp", selected_path: str) -> None:
        resolved_root = resolve_fgdata_root(selected_path)
        if not resolved_root:
            self._set_status_t(
                "status.directory_not_fg_root",
                "Selected directory is not a FlightGear data root (Textures + Materials required)",
            )
            return
        self.flightgear_root = resolved_root
        self._refresh_object_catalog()
        self._clear_gl_textures()
        clear_texture_caches()
        if self.mesh:
            self._build_face_data(self.mesh)
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        self._set_status_t(
            "status.flightgear_location_set_fmt",
            "FlightGear location set: {path} | object catalog entries: {entries}",
            path=self.flightgear_root,
            entries=len(self.object_catalog),
        )

    def _apply_custom_scenery_directory(
        self: "BTGDisplayApp",
        selected_path: str,
        source_mode: str,
    ) -> None:
        normalized = os.path.abspath(selected_path)
        if normalized in self.custom_scenery_paths:
            self._set_status_t(
                "status.custom_path_exists_fmt",
                "Custom scenery already listed: {path}",
                path=normalized,
            )
            return

        self.custom_scenery_paths.append(normalized)
        self.custom_scenery_menu_index = len(self.custom_scenery_paths) - 1
        self._refresh_object_catalog()
        try:
            self._persist_viewer_config()
        except Exception:
            pass

        if source_mode in ("add_object_cats", "add_object_files"):
            self._build_add_object_categories()
            preferred = self.last_add_object_category or self.add_object_selected_category
            if preferred in self.add_object_category_list:
                self.add_object_category_index = self.add_object_category_list.index(preferred)
            else:
                self.add_object_category_index = 0

            if self.add_object_category_list:
                self.add_object_selected_category = self.add_object_category_list[self.add_object_category_index]
                self.add_object_file_index = 0
                self.add_object_file_scroll_start = 0
                files = self.add_object_by_category.get(self.add_object_selected_category, [])
                if files:
                    selected_entry = files[self.add_object_file_index]
                    self._update_add_object_preview(selected_entry)
                    self._set_status_t(
                        "status.custom_path_added_fmt",
                        "Added custom scenery path: {path} | object catalog entries: {entries}",
                        path=normalized,
                        entries=len(self.object_catalog),
                    )
                else:
                    self._clear_add_object_preview()
                    self._set_status_t(
                        "status.custom_path_added_fmt",
                        "Added custom scenery path: {path} | object catalog entries: {entries}",
                        path=normalized,
                        entries=len(self.object_catalog),
                    )
            else:
                self._clear_add_object_preview()
                self._set_status_t(
                    "status.custom_path_added_fmt",
                    "Added custom scenery path: {path} | object catalog entries: {entries}",
                    path=normalized,
                    entries=len(self.object_catalog),
                )
            return

        self._set_status_t(
            "status.custom_path_added_fmt",
            "Added custom scenery path: {path} | object catalog entries: {entries}",
            path=normalized,
            entries=len(self.object_catalog),
        )

    def _handle_camera_view_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        if key_code in (pygame.K_TAB, pygame.K_DOWN):
            self.camera_view_field_index = (self.camera_view_field_index + 1) % 2
            return
        if key_code == pygame.K_UP:
            self.camera_view_field_index = (self.camera_view_field_index - 1) % 2
            return
        if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._apply_camera_view_fields()
            return
        if key_code == pygame.K_BACKSPACE:
            idx = self.camera_view_field_index
            if self.camera_view_fields[idx]:
                self.camera_view_fields[idx] = self.camera_view_fields[idx][:-1]
            return
        if key_code == pygame.K_DELETE:
            self.camera_view_fields[self.camera_view_field_index] = ""
            return

        ch = getattr(event, "unicode", "")
        if not ch or len(ch) != 1:
            return
        if ch in "0123456789.-":
            idx = self.camera_view_field_index
            current = self.camera_view_fields[idx]
            if ch == "." and "." in current:
                return
            if ch == "-" and current:
                return
            self.camera_view_fields[idx] = current + ch

    def _handle_camera_clipping_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        if key_code in (pygame.K_TAB, pygame.K_DOWN):
            self.camera_clipping_field_index = (self.camera_clipping_field_index + 1) % 2
            return
        if key_code == pygame.K_UP:
            self.camera_clipping_field_index = (self.camera_clipping_field_index - 1) % 2
            return
        if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._apply_camera_clipping_fields()
            return
        if key_code == pygame.K_BACKSPACE:
            idx = self.camera_clipping_field_index
            if self.camera_clipping_fields[idx]:
                self.camera_clipping_fields[idx] = self.camera_clipping_fields[idx][:-1]
            return
        if key_code == pygame.K_DELETE:
            self.camera_clipping_fields[self.camera_clipping_field_index] = ""
            return

        ch = getattr(event, "unicode", "")
        if not ch or len(ch) != 1:
            return
        if ch in "0123456789.-":
            idx = self.camera_clipping_field_index
            current = self.camera_clipping_fields[idx]
            if ch == "." and "." in current:
                return
            if ch == "-" and current:
                return
            self.camera_clipping_fields[idx] = current + ch

    def _handle_grid_settings_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        if key_code in (pygame.K_TAB, pygame.K_DOWN):
            self.grid_field_index = (self.grid_field_index + 1) % 3
            return
        if key_code == pygame.K_UP:
            self.grid_field_index = (self.grid_field_index - 1) % 3
            return
        if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._apply_grid_fields()
            return
        if key_code == pygame.K_BACKSPACE:
            idx = self.grid_field_index
            if self.grid_fields[idx]:
                self.grid_fields[idx] = self.grid_fields[idx][:-1]
            return
        if key_code == pygame.K_DELETE:
            self.grid_fields[self.grid_field_index] = ""
            return

        ch = getattr(event, "unicode", "")
        if not ch or len(ch) != 1:
            return
        if ch in "0123456789.-":
            idx = self.grid_field_index
            current = self.grid_fields[idx]
            if ch == "." and "." in current:
                return
            if ch == "-" and current:
                return
            self.grid_fields[idx] = current + ch

    def _handle_preview_panel_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        if key_code in (pygame.K_LEFT, pygame.K_RIGHT):
            modes = ["textured", "shaded", "wireframe"]
            try:
                idx_mode = modes.index(self.preview_panel_render_mode)
            except ValueError:
                idx_mode = 0
            if key_code == pygame.K_LEFT:
                idx_mode = (idx_mode - 1) % len(modes)
            else:
                idx_mode = (idx_mode + 1) % len(modes)
            self.preview_panel_render_mode = modes[idx_mode]
            try:
                self._persist_viewer_config()
            except Exception:
                pass
            self._set_status_t(
                "status.preview_render_mode_fmt",
                "Preview render mode: {mode}",
                mode=self.preview_panel_render_mode,
            )
            return
        if key_code in (pygame.K_TAB, pygame.K_DOWN):
            self.preview_panel_field_index = (self.preview_panel_field_index + 1) % 4
            return
        if key_code == pygame.K_UP:
            self.preview_panel_field_index = (self.preview_panel_field_index - 1) % 4
            return
        if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._apply_preview_panel_fields()
            return
        if key_code == pygame.K_BACKSPACE:
            idx = self.preview_panel_field_index
            if self.preview_panel_fields[idx]:
                self.preview_panel_fields[idx] = self.preview_panel_fields[idx][:-1]
            return
        if key_code == pygame.K_DELETE:
            self.preview_panel_fields[self.preview_panel_field_index] = ""
            return

        ch = getattr(event, "unicode", "")
        if not ch or len(ch) != 1:
            return
        if ch in "0123456789.-":
            idx = self.preview_panel_field_index
            current = self.preview_panel_fields[idx]
            if ch == "." and "." in current:
                return
            if ch == "-" and current:
                return
            self.preview_panel_fields[idx] = current + ch

    def _handle_object_nudge_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._apply_object_nudge_fields()
            return
        if key_code == pygame.K_BACKSPACE:
            if self.object_nudge_fields[0]:
                self.object_nudge_fields[0] = self.object_nudge_fields[0][:-1]
            return
        if key_code == pygame.K_DELETE:
            self.object_nudge_fields[0] = ""
            return

        ch = getattr(event, "unicode", "")
        if not ch or len(ch) != 1:
            return
        if ch in "0123456789.-":
            current = self.object_nudge_fields[0]
            if ch == "." and "." in current:
                return
            if ch == "-" and current:
                return
            self.object_nudge_fields[0] = current + ch

    def _handle_object_nudge_repeat_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        if key_code in (pygame.K_TAB, pygame.K_DOWN):
            self.object_nudge_repeat_field_index = (self.object_nudge_repeat_field_index + 1) % 2
            return
        if key_code == pygame.K_UP:
            self.object_nudge_repeat_field_index = (self.object_nudge_repeat_field_index - 1) % 2
            return
        if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._apply_object_nudge_repeat_fields()
            return
        if key_code == pygame.K_BACKSPACE:
            idx = self.object_nudge_repeat_field_index
            if self.object_nudge_repeat_fields[idx]:
                self.object_nudge_repeat_fields[idx] = self.object_nudge_repeat_fields[idx][:-1]
            return
        if key_code == pygame.K_DELETE:
            self.object_nudge_repeat_fields[self.object_nudge_repeat_field_index] = ""
            return

        ch = getattr(event, "unicode", "")
        if not ch or len(ch) != 1:
            return
        if ch in "0123456789.-":
            idx = self.object_nudge_repeat_field_index
            current = self.object_nudge_repeat_fields[idx]
            if ch == "." and "." in current:
                return
            if ch == "-" and current:
                return
            self.object_nudge_repeat_fields[idx] = current + ch

    def _handle_missing_material_color_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        if key_code in (pygame.K_TAB, pygame.K_DOWN):
            self.missing_material_color_field_index = (self.missing_material_color_field_index + 1) % 3
            return
        if key_code == pygame.K_UP:
            self.missing_material_color_field_index = (self.missing_material_color_field_index - 1) % 3
            return
        if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._apply_missing_material_color_fields()
            return
        if key_code == pygame.K_BACKSPACE:
            idx = self.missing_material_color_field_index
            if self.missing_material_color_fields[idx]:
                self.missing_material_color_fields[idx] = self.missing_material_color_fields[idx][:-1]
            return
        if key_code == pygame.K_DELETE:
            self.missing_material_color_fields[self.missing_material_color_field_index] = ""
            return

        ch = getattr(event, "unicode", "")
        if not ch or len(ch) != 1:
            return
        if ch in "0123456789":
            idx = self.missing_material_color_field_index
            current = self.missing_material_color_fields[idx]
            if len(current) >= 3:
                return
            self.missing_material_color_fields[idx] = current + ch

    def _handle_custom_scenery_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        path_count = len(self.custom_scenery_paths)
        if path_count <= 0:
            self.custom_scenery_menu_index = 0
        else:
            self.custom_scenery_menu_index = max(0, min(self.custom_scenery_menu_index, path_count - 1))

        if key_code == pygame.K_UP and path_count > 0:
            self.custom_scenery_menu_index = (self.custom_scenery_menu_index - 1) % path_count
            return
        if key_code == pygame.K_DOWN and path_count > 0:
            self.custom_scenery_menu_index = (self.custom_scenery_menu_index + 1) % path_count
            return

        if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_a):
            initial_dir = self.flightgear_root or self.file_browser_dir or os.getcwd()
            self._open_file_browser(
                "directory_select",
                start_dir=initial_dir,
                return_mode="custom_scenery",
                directory_action="custom_scenery_add",
            )
            return

        if key_code in (pygame.K_DELETE, pygame.K_BACKSPACE, pygame.K_x):
            if not self.custom_scenery_paths:
                self._set_status_t(
                    "status.no_custom_paths_to_remove",
                    "No custom scenery paths to remove",
                )
                return
            removed = self.custom_scenery_paths.pop(self.custom_scenery_menu_index)
            if self.custom_scenery_paths:
                self.custom_scenery_menu_index = min(self.custom_scenery_menu_index, len(self.custom_scenery_paths) - 1)
            else:
                self.custom_scenery_menu_index = 0
            self._refresh_object_catalog()
            try:
                self._persist_viewer_config()
            except Exception:
                pass
            self._set_status_t(
                "status.custom_path_removed_fmt",
                "Removed custom scenery path: {path} | object catalog entries: {entries}",
                path=removed,
                entries=len(self.object_catalog),
            )
            return

        if key_code == pygame.K_r:
            self._refresh_object_catalog()
            self._set_status_t(
                "status.catalog_refreshed_fmt",
                "Object catalog refreshed | entries: {entries}",
                entries=len(self.object_catalog),
            )

    def _file_browser_default_save_name(self: "BTGDisplayApp") -> str:
        if self.current_file and self.current_file.lower().endswith(".stg"):
            name = os.path.basename(self.current_file)
            if name:
                return name
        if self.current_file:
            base = os.path.splitext(os.path.basename(self.current_file))[0]
            if base:
                return f"{base}.stg"
        return "scene.stg"

    def _file_browser_windows_drive_list(self: "BTGDisplayApp") -> List[str]:
        if os.name != "nt":
            return []
        drives: List[str] = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            candidate = f"{letter}:\\"
            if os.path.exists(candidate):
                drives.append(candidate)
        return drives

    def _file_browser_parent_directory(self: "BTGDisplayApp", directory: str) -> Optional[str]:
        if os.name == "nt":
            normalized = os.path.abspath(directory)
            drive, tail = os.path.splitdrive(normalized)
            if drive and tail in {"\\", "/"}:
                return self._WINDOWS_DRIVES_ROOT

        parent = os.path.dirname(directory)
        if parent and parent != directory:
            return parent
        return None

    def _open_file_browser(
        self: "BTGDisplayApp",
        mode: str,
        start_dir: Optional[str] = None,
        *,
        return_mode: str = "main",
        directory_action: str = "",
    ) -> None:
        self.file_browser_mode = mode
        self.file_browser_return_mode = return_mode
        self.file_browser_directory_action = directory_action
        default_dir = start_dir or self.last_browse_dir or os.getcwd()
        self.file_browser_dir = os.path.abspath(default_dir)
        if not os.path.isdir(self.file_browser_dir):
            self.file_browser_dir = os.getcwd()
        self.file_browser_index = 0
        self.file_browser_last_click_index = -1
        self.file_browser_last_click_path = ""
        self.file_browser_last_click_ms = 0
        self.file_browser_scroll_start = 0
        if mode == "save_as":
            self.file_browser_save_name = self._file_browser_default_save_name()
        else:
            self.file_browser_save_name = ""
        self.file_browser_overwrite_target = ""
        self.menu_mode = "file_browser"
        self._refresh_file_browser_entries()

    def _refresh_file_browser_entries(self: "BTGDisplayApp") -> None:
        if self.file_browser_mode == "menu_language":
            entries = self._available_menu_language_entries()
            self.file_browser_entries = entries
            if not entries:
                self.file_browser_index = 0
            else:
                self.file_browser_index = max(0, min(self.file_browser_index, len(entries) - 1))
            return

        if os.name == "nt" and self.file_browser_dir == self._WINDOWS_DRIVES_ROOT:
            entries: List[Dict[str, object]] = []
            for drive in self._file_browser_windows_drive_list():
                entries.append(
                    {
                        "name": f"[DRV] {drive}",
                        "path": drive,
                        "is_dir": True,
                        "color": (120, 215, 255),
                    }
                )

            self.file_browser_entries = entries
            if not entries:
                self.file_browser_index = 0
            else:
                self.file_browser_index = max(0, min(self.file_browser_index, len(entries) - 1))
            return

        directory = os.path.abspath(self.file_browser_dir or os.getcwd())
        if not os.path.isdir(directory):
            directory = os.getcwd()
        self.file_browser_dir = directory

        entries: List[Dict[str, object]] = []
        if self.file_browser_mode == "directory_select":
            entries.append(
                {
                    "name": "[Select This Folder]",
                    "path": directory,
                    "is_dir": False,
                    "is_action": True,
                    "color": (190, 240, 190),
                }
            )

            parent = self._file_browser_parent_directory(directory)
            if parent:
                entries.append(
                    {
                        "name": "[..] Parent Folder",
                        "path": parent,
                        "is_dir": True,
                        "is_parent": True,
                        "color": (150, 210, 255),
                    }
                )

            try:
                names = sorted(os.listdir(directory), key=lambda item: item.lower())
            except Exception as exc:
                self.file_browser_entries = entries
                self.file_browser_index = 0
                self._set_status_t(
                    "status.file_browser_failed_fmt",
                    "File browser failed: {error}",
                    error=exc,
                )
                return

            for name in names:
                full_path = os.path.join(directory, name)
                if os.path.isdir(full_path):
                    entries.append(
                        {
                            "name": f"[DIR] {name}",
                            "path": full_path,
                            "is_dir": True,
                            "color": (120, 215, 255),
                        }
                    )

            self.file_browser_entries = entries
            if not entries:
                self.file_browser_index = 0
            else:
                self.file_browser_index = max(0, min(self.file_browser_index, len(entries) - 1))

            try:
                self._remember_browse_path(directory)
            except Exception:
                pass
            return

        if self.file_browser_mode == "save_as":
            save_name = (self.file_browser_save_name or self._file_browser_default_save_name()).strip()
            if not save_name.lower().endswith(".stg"):
                save_name = f"{save_name}.stg"
            self.file_browser_save_name = save_name
            save_target = os.path.join(directory, save_name)
            entries.append(
                {
                    "name": f"[Save as {save_name}]",
                    "path": save_target,
                    "is_dir": False,
                    "is_action": True,
                    "color": (120, 220, 255),
                }
            )

        parent = self._file_browser_parent_directory(directory)
        if parent:
            entries.append(
                {
                    "name": "[..] Parent folder",
                    "path": parent,
                    "is_dir": True,
                    "is_parent": True,
                    "color": (150, 210, 255),
                }
            )

        try:
            names = sorted(os.listdir(directory), key=lambda item: item.lower())
        except Exception as exc:
            self.file_browser_entries = entries
            self.file_browser_index = 0
            self._set_status_t(
                "status.file_browser_failed_fmt",
                "File browser failed: {error}",
                error=exc,
            )
            return

        folder_entries: List[Dict[str, object]] = []
        file_entries: List[Dict[str, object]] = []
        for name in names:
            full_path = os.path.join(directory, name)
            if os.path.isdir(full_path):
                folder_entries.append(
                    {
                        "name": f"[DIR] {name}",
                        "path": full_path,
                        "is_dir": True,
                        "color": (120, 215, 255),
                    }
                )
            elif os.path.isfile(full_path):
                ext = os.path.splitext(name)[1].lower()
                color = (220, 220, 220)
                if self.file_browser_mode == "load" and ext == ".stg":
                    color = (190, 240, 190)
                elif self.file_browser_mode == "help_text":
                    if ext in {".txt", ".text", ".md", ".markdown", ".ini", ".cfg", ".conf", ".json"}:
                        color = (190, 240, 190)
                    else:
                        color = (200, 200, 200)
                file_entries.append(
                    {
                        "name": name,
                        "path": full_path,
                        "is_dir": False,
                        "color": color,
                    }
                )

        entries.extend(folder_entries)
        entries.extend(file_entries)
        self.file_browser_entries = entries
        if not entries:
            self.file_browser_index = 0
        else:
            self.file_browser_index = max(0, min(self.file_browser_index, len(entries) - 1))
        self.file_browser_scroll_start = self._stable_list_start(
            self.file_browser_scroll_start,
            self.file_browser_index,
            len(entries),
            max(1, (min(500, self.size[1] - 180) - 146) // 28),
        )

        try:
            self._remember_browse_path(directory)
        except Exception:
            pass

    def _file_browser_row_layout(self: "BTGDisplayApp") -> Tuple[int, int, int, int, int, int, int, int, int]:
        panel_w = min(1100, self.size[0] - 60)
        panel_h = min(500, self.size[1] - 180)
        panel_x = self.size[0] // 2 - panel_w // 2
        panel_y = 120
        row_h = 28
        row_y = panel_y + 92
        max_rows = max(1, (panel_h - 146) // row_h)
        idx = self.file_browser_index
        n = len(self.file_browser_entries)
        self.file_browser_scroll_start = self._stable_list_start(
            self.file_browser_scroll_start,
            idx,
            n,
            max_rows,
        )
        start = self.file_browser_scroll_start
        return panel_x, panel_y, panel_w, panel_h, row_y, row_h, max_rows, start, n

    def _stable_list_start(
        self: "BTGDisplayApp",
        current_start: int,
        selected_index: int,
        item_count: int,
        max_rows: int,
    ) -> int:
        if item_count <= 0:
            return 0
        rows = max(1, max_rows)
        selected = max(0, min(selected_index, item_count - 1))
        max_start = max(0, item_count - rows)
        start = max(0, min(current_start, max_start))
        if selected < start:
            start = selected
        elif selected >= start + rows:
            start = selected - rows + 1
        return max(0, min(start, max_start))

    def _main_menu_row_layout(self: "BTGDisplayApp") -> Tuple[int, int, int, int, int]:
        y0 = 170
        row_h = 44
        n = len(self.main_menu_items)
        available_px = max(220, self.size[1] - 290)
        max_rows = max(5, min(n if n > 0 else 5, available_px // row_h))
        idx = max(0, min(self.main_menu_index, max(0, n - 1)))
        self.main_menu_scroll_start = self._stable_list_start(
            self.main_menu_scroll_start,
            idx,
            n,
            max_rows,
        )
        start = self.main_menu_scroll_start
        visible_count = min(max_rows, max(0, n - start))
        return y0, row_h, start, visible_count, n

    def _custom_scenery_row_layout(self: "BTGDisplayApp") -> Tuple[int, int, int, int]:
        row_y = 202
        row_h = 30
        max_rows = 10
        n = len(self.custom_scenery_paths)
        idx = max(0, min(self.custom_scenery_menu_index, max(0, n - 1)))
        self.custom_scenery_scroll_start = self._stable_list_start(
            self.custom_scenery_scroll_start,
            idx,
            n,
            max_rows,
        )
        return row_y, row_h, max_rows, self.custom_scenery_scroll_start

    def _add_object_cats_row_layout(self: "BTGDisplayApp") -> Tuple[int, int, int, int]:
        panel_h = min(500, self.size[1] - 180)
        row_h = 30
        max_rows = max(1, (panel_h - 60) // row_h)
        n = len(self.add_object_category_list)
        idx = max(0, min(self.add_object_category_index, max(0, n - 1)))
        self.add_object_category_scroll_start = self._stable_list_start(
            self.add_object_category_scroll_start,
            idx,
            n,
            max_rows,
        )
        return row_h, max_rows, self.add_object_category_scroll_start, n

    def _reset_add_object_hover_state(self: "BTGDisplayApp") -> None:
        self.add_object_parent_row_hovered = False
        self._reset_menu_hover_scroll()

    def _add_object_files_row_layout(self: "BTGDisplayApp", file_count: int) -> Tuple[int, int, int, int, int, int]:
        panel_h = min(500, self.size[1] - 180)
        panel_y = 120
        row_h = 26
        row_y = panel_y + int(panel_h * 0.10)
        max_rows_total = max(2, (panel_h - int(panel_h * 0.10) - 10) // row_h)
        file_rows = max(1, max_rows_total - 1)  # reserve first row for parent-folder item
        idx = self.add_object_file_index
        self.add_object_file_scroll_start = self._stable_list_start(
            self.add_object_file_scroll_start,
            idx,
            file_count,
            file_rows,
        )
        start = self.add_object_file_scroll_start
        visible_files = min(file_rows, max(0, file_count - start))
        total_rows = 1 + visible_files
        return row_y, row_h, file_rows, start, visible_files, total_rows

    def _file_browser_register_click(self: "BTGDisplayApp", entry_index: int, entry_path: str) -> bool:
        now_ms = int(pygame.time.get_ticks())
        is_double = (
            entry_index == self.file_browser_last_click_index
            and entry_path == self.file_browser_last_click_path
            and (now_ms - self.file_browser_last_click_ms) <= int(self.file_browser_double_click_interval_ms)
        )
        self.file_browser_last_click_index = int(entry_index)
        self.file_browser_last_click_path = str(entry_path)
        self.file_browser_last_click_ms = now_ms
        return is_double

    def _file_browser_activate_selected(self: "BTGDisplayApp") -> None:
        if not self.file_browser_entries:
            return
        if not (0 <= self.file_browser_index < len(self.file_browser_entries)):
            return

        entry = self.file_browser_entries[self.file_browser_index]
        entry_path = str(entry.get("path", ""))
        is_dir = bool(entry.get("is_dir", False))
        is_action = bool(entry.get("is_action", False))
        if not entry_path:
            return

        if is_dir:
            if os.name == "nt" and entry_path == self._WINDOWS_DRIVES_ROOT:
                self.file_browser_dir = self._WINDOWS_DRIVES_ROOT
            else:
                self.file_browser_dir = os.path.abspath(entry_path)
            self.file_browser_index = 0
            self._refresh_file_browser_entries()
            return

        if self.file_browser_mode == "menu_language":
            if self._load_menu_text_file(entry_path, persist=True, silent=False):
                self._close_menu()
            return

        if self.file_browser_mode == "directory_select":
            if not is_action:
                return
            selected_dir = os.path.abspath(entry_path)
            action = self.file_browser_directory_action
            return_mode = self.file_browser_return_mode or "main"
            if action == "flightgear_root":
                self._apply_flightgear_root_directory(selected_dir)
            elif action == "custom_scenery_add":
                self._apply_custom_scenery_directory(selected_dir, return_mode)
            self.file_browser_directory_action = ""
            self.file_browser_mode = ""
            self.menu_mode = return_mode
            return

        if self.file_browser_mode == "load":
            if not entry_path.lower().endswith(".stg"):
                self._set_status_t("status.load_select_stg", "Load failed: select a .stg file")
                return
            self._close_menu()
            self.load_stg_file(entry_path)
            return

        if self.file_browser_mode == "help_text":
            if self._load_help_text_file(entry_path, persist=True, silent=False):
                self._close_menu()
            return

        if self.file_browser_mode == "save_as":
            target_path = entry_path
            if not target_path.lower().endswith(".stg"):
                target_path = f"{target_path}.stg"
            if is_action:
                self.file_browser_save_name = os.path.basename(target_path)
            if os.path.exists(target_path):
                self.file_browser_overwrite_target = target_path
                self.menu_mode = "file_browser_overwrite_confirm"
                return
            if self.save_stg_as(target_path):
                self._close_menu()
            return

    def _handle_file_browser_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        count = len(self.file_browser_entries)

        if key_code == pygame.K_UP and count > 0:
            self.file_browser_index = (self.file_browser_index - 1) % count
            return
        if key_code == pygame.K_DOWN and count > 0:
            self.file_browser_index = (self.file_browser_index + 1) % count
            return
        if key_code == pygame.K_PAGEUP and count > 0:
            self.file_browser_index = max(0, self.file_browser_index - 10)
            return
        if key_code == pygame.K_PAGEDOWN and count > 0:
            self.file_browser_index = min(count - 1, self.file_browser_index + 10)
            return
        if key_code == pygame.K_HOME and count > 0:
            self.file_browser_index = 0
            return
        if key_code == pygame.K_END and count > 0:
            self.file_browser_index = count - 1
            return
        if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._file_browser_activate_selected()
            return

        if self.file_browser_mode == "save_as":
            if key_code == pygame.K_BACKSPACE:
                if self.file_browser_save_name:
                    self.file_browser_save_name = self.file_browser_save_name[:-1]
                    self._refresh_file_browser_entries()
                return
            if key_code == pygame.K_DELETE:
                self.file_browser_save_name = ""
                self._refresh_file_browser_entries()
                return

            ch = getattr(event, "unicode", "")
            if ch and len(ch) == 1 and ch not in {"/", "\\"} and ord(ch) >= 32:
                self.file_browser_save_name += ch
                self._refresh_file_browser_entries()

    def _handle_file_browser_overwrite_confirm_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        if key_code in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
            target = self.file_browser_overwrite_target
            self.file_browser_overwrite_target = ""
            if target and self.save_stg_as(target):
                self._close_menu()
            else:
                self.menu_mode = "file_browser"
            return
        if key_code in (pygame.K_n, pygame.K_ESCAPE, pygame.K_BACKSPACE):
            self.file_browser_overwrite_target = ""
            self.menu_mode = "file_browser"

    def _open_save_confirm_dialog(self: "BTGDisplayApp", title: str, message: str) -> None:
        if self.show_menu:
            self.save_confirm_return_mode = self.menu_mode if self.menu_mode != "save_confirm" else "main"
        else:
            self.save_confirm_return_mode = ""
            self.mouse_captured_before_menu = self.mouse_captured
            self.show_menu = True
            self.set_mouse_capture(False)

        self.binding_capture_action = None
        self.save_confirm_title = title
        self.save_confirm_message = message
        self.menu_mode = "save_confirm"

    def _dismiss_save_confirm_dialog(self: "BTGDisplayApp") -> None:
        self.save_confirm_title = ""
        self.save_confirm_message = ""
        return_mode = self.save_confirm_return_mode
        self.save_confirm_return_mode = ""
        if return_mode:
            self.menu_mode = return_mode
        else:
            self._close_menu()

    def _open_menu(self: "BTGDisplayApp") -> None:
        self.mouse_captured_before_menu = self.mouse_captured
        self.show_menu = True
        self.menu_mode = "main"
        self.main_menu_scroll_start = 0
        self.binding_capture_action = None
        self.save_confirm_title = ""
        self.save_confirm_message = ""
        self.save_confirm_return_mode = ""
        self.set_mouse_capture(False)

    def _close_menu(self: "BTGDisplayApp") -> None:
        self._clear_add_object_preview()
        self._reset_add_object_hover_state()
        self.show_menu = False
        self.menu_mode = "main"
        self.file_browser_mode = ""
        self.file_browser_return_mode = "main"
        self.file_browser_directory_action = ""
        self.binding_capture_action = None
        self.save_confirm_title = ""
        self.save_confirm_message = ""
        self.save_confirm_return_mode = ""
        if self.mouse_captured_before_menu:
            self.set_mouse_capture(True)

    def _open_add_object_menu(self: "BTGDisplayApp") -> None:
        self._open_menu()
        self._build_add_object_categories()
        self.add_object_category_scroll_start = 0
        self.add_object_file_scroll_start = 0
        if not self.add_object_category_list:
            self.add_object_category_index = 0
            self._reset_add_object_hover_state()
            self.menu_mode = "add_object_cats"
            return

        preferred = self.last_add_object_category or self.add_object_selected_category
        if preferred in self.add_object_category_list:
            self.add_object_category_index = self.add_object_category_list.index(preferred)
        else:
            self.add_object_category_index = 0

        self.add_object_selected_category = self.add_object_category_list[self.add_object_category_index]
        self.add_object_file_index = 0
        self._reset_add_object_hover_state()
        self.menu_mode = "add_object_files"
        self._remember_add_object_category(self.add_object_selected_category)
        files = self.add_object_by_category.get(self.add_object_selected_category, [])
        if files:
            selected_entry = files[self.add_object_file_index]
            self._update_add_object_preview(selected_entry)
            self._set_status_t(
                "status.preview_object_fmt",
                "Preview object: {path}",
                path=selected_entry.object_path,
            )
        else:
            self._clear_add_object_preview()

    def _run_main_menu_action(self: "BTGDisplayApp") -> None:
        item = self.main_menu_items[self.main_menu_index]
        if item == "Load STG":
            if self.last_browse_dir and os.path.isdir(self.last_browse_dir):
                start_dir = self.last_browse_dir
            elif self.current_file:
                start_dir = os.path.dirname(os.path.abspath(self.current_file))
            else:
                start_dir = os.getcwd()
            self._open_file_browser("load", start_dir=start_dir)
        elif item == "Save":
            self.save_stg_file()
        elif item == "Save As STG":
            source_stg_path = self._resolve_active_stg_path(require_exists=True)
            if not source_stg_path:
                self._set_status_t(
                    "status.save_as_no_scene_stg",
                    "Save As failed: no STG file associated with current scene",
                )
                return
            start_dir = os.path.dirname(source_stg_path) if source_stg_path else (self.last_browse_dir or os.getcwd())
            self._open_file_browser("save_as", start_dir=start_dir)
        elif item == "Menu Language":
            self._open_file_browser("menu_language", start_dir=os.path.dirname(__file__))
        elif item == "Help Text File":
            if self.help_text_file_path:
                preferred_dir = os.path.dirname(os.path.abspath(self.help_text_file_path))
            elif self.last_browse_dir:
                preferred_dir = self.last_browse_dir
            else:
                preferred_dir = os.getcwd()
            self._open_file_browser("help_text", start_dir=preferred_dir)
        elif item == "Reload Help Text":
            target = (self.help_text_file_path or "").strip()
            if not target:
                self._set_status_t(
                    "status.reload_help_no_file",
                    "Reload failed: no help text file selected",
                )
                return

            if self._load_help_text_file(target, persist=False, silent=False):
                return

            fallback = os.path.abspath(os.path.join(os.path.dirname(__file__), "onscreen_help_english.txt"))
            if target != fallback and self._load_help_text_file(fallback, persist=True, silent=False):
                self._set_status_t(
                    "status.reload_help_fallback_fmt",
                    "Reload failed for selected file; loaded fallback: {path}",
                    path=fallback,
                )
                return

            self._set_status_t(
                "status.reload_help_failed",
                "Reload failed: unable to load selected help text file",
            )
        elif item == "Flightgear Location":
            self._open_file_browser(
                "directory_select",
                start_dir=self.flightgear_root or self.file_browser_dir or os.getcwd(),
                return_mode="main",
                directory_action="flightgear_root",
            )
        elif item == "Custom Scenery Paths":
            self.custom_scenery_menu_index = 0
            self.custom_scenery_scroll_start = 0
            self.menu_mode = "custom_scenery"
        elif item == "Add Object":
            self._open_add_object_menu()
        elif item == "Set Missing Material Color":
            self._sync_missing_material_color_fields_from_settings()
            self.missing_material_color_field_index = 0
            self.menu_mode = "missing_material_color"
        elif item == "Toggle Textured View":
            self.textured_mode = not self.textured_mode
            try:
                self._persist_viewer_config()
            except Exception:
                pass
            state = self._menu_t("state.on", "ON") if self.textured_mode else self._menu_t("state.off", "OFF")
            self._set_status_t(
                "status.textured_view_fmt",
                "Textured view: {state}",
                state=state,
            )
        elif item == "Set Object Nudge Distance":
            self._sync_object_nudge_fields_from_settings()
            self.object_nudge_field_index = 0
            self.menu_mode = "object_nudge"
        elif item == "Set Object Nudge Repeat":
            self._sync_object_nudge_repeat_fields_from_settings()
            self.object_nudge_repeat_field_index = 0
            self.menu_mode = "object_nudge_repeat"
        elif item == "Toggle Nudge Mode":
            self.object_nudge_camera_relative = not self.object_nudge_camera_relative
            try:
                self._persist_viewer_config()
            except Exception:
                pass
            mode = (
                self._menu_t("mode.camera_relative", "Camera Relative")
                if self.object_nudge_camera_relative
                else self._menu_t("mode.world_relative", "World Relative")
            )
            self._set_status_t(
                "status.object_nudge_mode_fmt",
                "Object nudge mode: {mode}",
                mode=mode,
            )
        elif item == "Set Camera Start View":
            self._sync_camera_view_fields_from_settings()
            self.camera_view_field_index = 0
            self.menu_mode = "camera_view"
        elif item == "Set Camera Clipping":
            self._sync_camera_clipping_fields_from_settings()
            self.camera_clipping_field_index = 0
            self.menu_mode = "camera_clipping"
        elif item == "Grid Settings":
            self._sync_grid_fields_from_settings()
            self.grid_field_index = 0
            self.menu_mode = "grid_settings"
        elif item == "Preview Panel Location":
            self._sync_preview_panel_fields_from_settings()
            self.preview_panel_field_index = 0
            self.menu_mode = "preview_panel_location"
        elif item == "Change Keyboard bindings":
            self.menu_mode = "bindings"
            self.binding_capture_action = None
        elif item == "Exit":
            self.running = False

    def _handle_menu_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key

        if self.menu_mode == "main":
            if key_code == pygame.K_UP:
                self.main_menu_index = (self.main_menu_index - 1) % len(self.main_menu_items)
            elif key_code == pygame.K_DOWN:
                self.main_menu_index = (self.main_menu_index + 1) % len(self.main_menu_items)
            elif key_code == pygame.K_PAGEUP:
                _y0, _row_h, _start, visible_count, _n = self._main_menu_row_layout()
                step = max(1, visible_count - 1)
                self.main_menu_index = max(0, self.main_menu_index - step)
            elif key_code == pygame.K_PAGEDOWN:
                _y0, _row_h, _start, visible_count, n = self._main_menu_row_layout()
                step = max(1, visible_count - 1)
                self.main_menu_index = min(max(0, n - 1), self.main_menu_index + step)
            elif key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._run_main_menu_action()
            return

        if self.menu_mode == "bindings":
            if self.binding_capture_action:
                if key_code == pygame.K_ESCAPE:
                    self.binding_capture_action = None
                    self._set_status_t("status.binding_update_cancelled", "Binding update cancelled")
                    return
                if self._is_modifier_key(key_code):
                    return
                action = self.binding_capture_action
                self.binding_capture_action = None
                captured = KeyBinding(key_code, self._normalized_mods(event.mod))
                existing = self.find_binding_action(captured)
                if existing and existing != action:
                    self.bindings[existing], self.bindings[action] = self.bindings[action], captured
                else:
                    self.bindings[action] = captured
                self._set_status_t(
                    "status.bound_action_fmt",
                    "Bound {action_label} -> {binding}",
                    action_label=self.binding_labels[action],
                    binding=self.format_binding(captured),
                )
                return

            if key_code == pygame.K_UP:
                self.binding_menu_index = (self.binding_menu_index - 1) % len(self.binding_actions)
            elif key_code == pygame.K_DOWN:
                self.binding_menu_index = (self.binding_menu_index + 1) % len(self.binding_actions)
            elif key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.binding_capture_action = self.binding_actions[self.binding_menu_index]
                self._set_status_t(
                    "status.press_key_bind_fmt",
                    "Press a key to bind '{action_label}'",
                    action_label=self.binding_labels[self.binding_capture_action],
                )
            return

        if self.menu_mode == "camera_view":
            self._handle_camera_view_keydown(event)
            return

        if self.menu_mode == "camera_clipping":
            self._handle_camera_clipping_keydown(event)
            return

        if self.menu_mode == "grid_settings":
            self._handle_grid_settings_keydown(event)
            return

        if self.menu_mode == "preview_panel_location":
            self._handle_preview_panel_keydown(event)
            return

        if self.menu_mode == "object_nudge":
            self._handle_object_nudge_keydown(event)
            return

        if self.menu_mode == "object_nudge_repeat":
            self._handle_object_nudge_repeat_keydown(event)
            return

        if self.menu_mode == "missing_material_color":
            self._handle_missing_material_color_keydown(event)
            return

        if self.menu_mode == "custom_scenery":
            self._handle_custom_scenery_keydown(event)
            return

        if self.menu_mode == "save_confirm":
            if key_code in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE, pygame.K_BACKSPACE, pygame.K_SPACE):
                self._dismiss_save_confirm_dialog()
            return

        if self.menu_mode == "file_browser":
            self._handle_file_browser_keydown(event)
            return

        if self.menu_mode == "file_browser_overwrite_confirm":
            self._handle_file_browser_overwrite_confirm_keydown(event)
            return

        if self.menu_mode == "add_object_cats":
            self._handle_add_object_cats_keydown(event)
            return

        if self.menu_mode == "add_object_files":
            self._handle_add_object_files_keydown(event)

    def _handle_add_object_cats_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        # Allow adding a custom scenery path from the Add Object category view.
        if key_code == pygame.K_a:
            initial_dir = self.flightgear_root or self.file_browser_dir or os.getcwd()
            self._open_file_browser(
                "directory_select",
                start_dir=initial_dir,
                return_mode="add_object_cats",
                directory_action="custom_scenery_add",
            )
            return
        cat_count = len(self.add_object_category_list)
        if cat_count == 0:
            return
        if key_code == pygame.K_UP:
            self.add_object_category_index = (self.add_object_category_index - 1) % cat_count
        elif key_code == pygame.K_DOWN:
            self.add_object_category_index = (self.add_object_category_index + 1) % cat_count
        elif key_code == pygame.K_PAGEUP:
            self.add_object_category_index = max(0, self.add_object_category_index - 8)
        elif key_code == pygame.K_PAGEDOWN:
            self.add_object_category_index = min(cat_count - 1, self.add_object_category_index + 8)
        elif key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            cat = self.add_object_category_list[self.add_object_category_index]
            self.add_object_selected_category = cat
            self._remember_add_object_category(cat)
            self.add_object_file_index = 0
            self.add_object_file_scroll_start = 0
            self._reset_add_object_hover_state()
            self.menu_mode = "add_object_files"
            files = self.add_object_by_category.get(self.add_object_selected_category, [])
            if files:
                selected_entry = files[self.add_object_file_index]
                self._update_add_object_preview(selected_entry)
                self._set_status_t(
                    "status.preview_object_fmt",
                    "Preview object: {path}",
                    path=selected_entry.object_path,
                )
            else:
                self._clear_add_object_preview()

    def _handle_add_object_files_keydown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        key_code = event.key
        mods = self._normalized_mods(getattr(event, "mod", 0))
        ctrl_held = bool(mods & pygame.KMOD_CTRL)
        # Allow adding a custom scenery path from the Add Object files view.
        if key_code == pygame.K_a:
            initial_dir = self.flightgear_root or self.file_browser_dir or os.getcwd()
            self._open_file_browser(
                "directory_select",
                start_dir=initial_dir,
                return_mode="add_object_files",
                directory_action="custom_scenery_add",
            )
            return
        if key_code == pygame.K_ESCAPE or key_code == pygame.K_BACKSPACE:
            self._clear_add_object_preview()
            self._reset_add_object_hover_state()
            self.menu_mode = "add_object_cats"
            return
        if key_code == pygame.K_LEFT and not ctrl_held:
            self._clear_add_object_preview()
            self._reset_add_object_hover_state()
            self.menu_mode = "add_object_cats"
            return
        files = self.add_object_by_category.get(self.add_object_selected_category, [])
        file_count = len(files)
        if file_count == 0:
            self._clear_add_object_preview()
            return
        changed_index = False
        if key_code == pygame.K_UP:
            self.add_object_file_index = (self.add_object_file_index - 1) % file_count
            changed_index = True
        elif key_code == pygame.K_DOWN:
            self.add_object_file_index = (self.add_object_file_index + 1) % file_count
            changed_index = True
        elif key_code == pygame.K_PAGEUP:
            if ctrl_held:
                cat_count = len(self.add_object_category_list)
                if cat_count > 0:
                    current_cat_index = self.add_object_category_index
                    if not (0 <= current_cat_index < cat_count):
                        try:
                            current_cat_index = self.add_object_category_list.index(self.add_object_selected_category)
                        except ValueError:
                            current_cat_index = 0
                    self.add_object_category_index = (current_cat_index - 1) % cat_count
                    self.add_object_selected_category = self.add_object_category_list[self.add_object_category_index]
                    self._remember_add_object_category(self.add_object_selected_category)
                    self.add_object_file_index = 0
                    self.add_object_file_scroll_start = 0
                    self._reset_add_object_hover_state()
                    files = self.add_object_by_category.get(self.add_object_selected_category, [])
                    changed_index = bool(files)
            else:
                self.add_object_file_index = (self.add_object_file_index - 1) % file_count
                changed_index = True
        elif key_code == pygame.K_PAGEDOWN:
            if ctrl_held:
                cat_count = len(self.add_object_category_list)
                if cat_count > 0:
                    current_cat_index = self.add_object_category_index
                    if not (0 <= current_cat_index < cat_count):
                        try:
                            current_cat_index = self.add_object_category_list.index(self.add_object_selected_category)
                        except ValueError:
                            current_cat_index = 0
                    self.add_object_category_index = (current_cat_index + 1) % cat_count
                    self.add_object_selected_category = self.add_object_category_list[self.add_object_category_index]
                    self._remember_add_object_category(self.add_object_selected_category)
                    self.add_object_file_index = 0
                    self.add_object_file_scroll_start = 0
                    self._reset_add_object_hover_state()
                    files = self.add_object_by_category.get(self.add_object_selected_category, [])
                    changed_index = bool(files)
            else:
                self.add_object_file_index = (self.add_object_file_index + 1) % file_count
                changed_index = True
        elif key_code in (pygame.K_RETURN, pygame.K_KP_ENTER):
            entry = files[self.add_object_file_index]
            if self._place_catalog_object_at_crosshair(entry):
                self._close_menu()
            return

        if changed_index:
            self.add_object_parent_row_hovered = False
            selected_entry = files[self.add_object_file_index]
            self._update_add_object_preview(selected_entry)
            self._set_status_t(
                "status.preview_object_fmt",
                "Preview object: {path}",
                path=selected_entry.object_path,
            )

    def _handle_menu_mousemotion(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        pos = getattr(event, "pos", pygame.mouse.get_pos())
        mx, my = float(pos[0]), float(pos[1])

        if self.menu_mode == "file_browser":
            _panel_x, panel_y, _panel_w, panel_h, row_y, row_h, max_rows, start, n = self._file_browser_row_layout()
            if n <= 0:
                return
            next_index, hotspot_active = self._edge_hotspot_scroll_index(my, float(panel_y), float(panel_h), self.file_browser_index, n)
            if hotspot_active:
                if next_index != self.file_browser_index:
                    self.file_browser_index = next_index
                return
            visible_count = min(max_rows, max(0, n - start))
            row_rel = self._menu_row_hit(my, float(row_y), float(row_h), visible_count)
            if row_rel is not None:
                abs_idx = start + row_rel
                if 0 <= abs_idx < n and abs_idx != self.file_browser_index:
                    self.file_browser_index = abs_idx
            else:
                self._reset_menu_hover_scroll()
            return

        if self.menu_mode == "main":
            y0, row_h, start, visible_count, n = self._main_menu_row_layout()
            if n <= 0:
                return
            panel_h = float(row_h * max(1, visible_count))
            next_index, hotspot_active = self._edge_hotspot_scroll_index(my, float(y0), panel_h, self.main_menu_index, n)
            if hotspot_active:
                if next_index != self.main_menu_index:
                    self.main_menu_index = next_index
                return
            row_rel = self._menu_row_hit(my, float(y0), float(row_h), visible_count)
            if row_rel is None:
                self._reset_menu_hover_scroll()
                return
            abs_idx = start + row_rel
            if 0 <= abs_idx < n and abs_idx != self.main_menu_index:
                self.main_menu_index = abs_idx
            return

        if self.menu_mode == "bindings" and not self.binding_capture_action:
            idx = self._menu_row_hit(my, 160.0, 34.0, len(self.binding_actions))
            if idx is not None and idx != self.binding_menu_index:
                self.binding_menu_index = idx
            return

        if self.menu_mode == "custom_scenery":
            path_count = len(self.custom_scenery_paths)
            panel_y = 150.0
            panel_h = 430.0
            next_index, hotspot_active = self._edge_hotspot_scroll_index(
                my,
                panel_y,
                panel_h,
                self.custom_scenery_menu_index,
                path_count,
            )
            if hotspot_active:
                if next_index != self.custom_scenery_menu_index:
                    self.custom_scenery_menu_index = next_index
                return
            row_y, row_h, max_rows, start = self._custom_scenery_row_layout()
            visible_count = min(max_rows, max(0, path_count - start))
            row_rel = self._menu_row_hit(my, float(row_y), float(row_h), visible_count)
            if row_rel is not None:
                abs_idx = start + row_rel
                if 0 <= abs_idx < path_count and abs_idx != self.custom_scenery_menu_index:
                    self.custom_scenery_menu_index = abs_idx
            else:
                self._reset_menu_hover_scroll()
            return

        if self.menu_mode == "add_object_cats":
            panel_y = 120.0
            panel_h = min(500, self.size[1] - 180)
            row_h, max_rows, start, n = self._add_object_cats_row_layout()
            if n <= 0:
                return
            next_index, hotspot_active = self._edge_hotspot_scroll_index(
                my,
                panel_y,
                float(panel_h),
                self.add_object_category_index,
                n,
            )
            if hotspot_active:
                if next_index != self.add_object_category_index:
                    self.add_object_category_index = next_index
                return
            visible_count = min(max_rows, max(0, n - start))
            row_rel = self._menu_row_hit(my, 168.0, float(row_h), visible_count)
            if row_rel is not None:
                abs_idx = start + row_rel
                if 0 <= abs_idx < n and abs_idx != self.add_object_category_index:
                    self.add_object_category_index = abs_idx
            else:
                self._reset_menu_hover_scroll()
            return

        if self.menu_mode == "add_object_files":
            files = self.add_object_by_category.get(self.add_object_selected_category, [])
            n = len(files)
            panel_y = 120.0
            panel_h = float(min(500, self.size[1] - 180))
            next_index, hotspot_active = self._edge_hotspot_scroll_index(my, panel_y, panel_h, self.add_object_file_index, n)
            if hotspot_active:
                self.add_object_parent_row_hovered = False
                if next_index != self.add_object_file_index:
                    self.add_object_file_index = next_index
                    selected_entry = files[self.add_object_file_index]
                    self._update_add_object_preview(selected_entry)
                    self._set_status_t(
                        "status.preview_object_fmt",
                        "Preview object: {path}",
                        path=selected_entry.object_path,
                    )
                return

            row_y, row_h, _file_rows, start, visible_files, total_rows = self._add_object_files_row_layout(n)
            row_rel = self._menu_row_hit(my, float(row_y), float(row_h), total_rows)
            if row_rel is None:
                self.add_object_parent_row_hovered = False
                self._reset_menu_hover_scroll()
                return

            if row_rel == 0:
                self.add_object_parent_row_hovered = True
                return

            self.add_object_parent_row_hovered = False
            abs_idx = start + (row_rel - 1)
            if not (0 <= abs_idx < n):
                return

            current_idx = self.add_object_file_index
            if abs_idx == current_idx:
                self._reset_menu_hover_scroll()
                return

            new_idx = abs_idx
            if new_idx != current_idx:
                self.add_object_file_index = new_idx
                selected_entry = files[self.add_object_file_index]
                self._update_add_object_preview(selected_entry)
                self._set_status_t(
                    "status.preview_object_fmt",
                    "Preview object: {path}",
                    path=selected_entry.object_path,
                )
            self._reset_menu_hover_scroll()
            return

        if self.menu_mode == "camera_view":
            panel_w = 640
            panel_x = self.size[0] // 2 - panel_w // 2
            field_idx = self._menu_field_hit_index(mx, my, panel_x + 38, panel_w - 76, 214.0, 66.0, 48.0, 2)
            if field_idx is not None:
                self.camera_view_field_index = field_idx
            return

        if self.menu_mode == "camera_clipping":
            panel_w = 640
            panel_x = self.size[0] // 2 - panel_w // 2
            field_idx = self._menu_field_hit_index(mx, my, panel_x + 38, panel_w - 76, 214.0, 66.0, 48.0, 2)
            if field_idx is not None:
                self.camera_clipping_field_index = field_idx
            return

        if self.menu_mode == "grid_settings":
            panel_w = 680
            panel_x = self.size[0] // 2 - panel_w // 2
            field_idx = self._menu_field_hit_index(mx, my, panel_x + 38, panel_w - 76, 184.0, 66.0, 48.0, 3)
            if field_idx is not None:
                self.grid_field_index = field_idx
            return

        if self.menu_mode == "preview_panel_location":
            panel_w = 700
            panel_x = self.size[0] // 2 - panel_w // 2
            field_idx = self._menu_field_hit_index(mx, my, panel_x + 38, panel_w - 76, 174.0, 66.0, 48.0, 4)
            if field_idx is not None:
                self.preview_panel_field_index = field_idx
            return

        if self.menu_mode == "object_nudge_repeat":
            panel_w = 640
            panel_x = self.size[0] // 2 - panel_w // 2
            field_idx = self._menu_field_hit_index(mx, my, panel_x + 38, panel_w - 76, 214.0, 66.0, 48.0, 2)
            if field_idx is not None:
                self.object_nudge_repeat_field_index = field_idx
            return

        if self.menu_mode == "missing_material_color":
            panel_w = 640
            panel_x = self.size[0] // 2 - panel_w // 2
            field_idx = self._menu_field_hit_index(mx, my, panel_x + 38, panel_w - 76, 184.0, 66.0, 48.0, 3)
            if field_idx is not None:
                self.missing_material_color_field_index = field_idx

    def _handle_menu_mousebuttondown(self: "BTGDisplayApp", event: pygame.event.Event) -> None:
        if event.button != 1:
            return

        pos = getattr(event, "pos", pygame.mouse.get_pos())
        mx, my = float(pos[0]), float(pos[1])
        self._handle_menu_mousemotion(event)

        if self.menu_mode == "save_confirm":
            self._dismiss_save_confirm_dialog()
            return

        if self.menu_mode == "file_browser":
            panel_x, panel_y, panel_w, panel_h, row_y, row_h, max_rows, start, n = self._file_browser_row_layout()
            if n <= 0:
                return
            visible_count = min(max_rows, max(0, n - start))
            row_rel = self._menu_row_hit(my, float(row_y), float(row_h), visible_count)
            if row_rel is None:
                return
            abs_idx = start + row_rel
            if not (0 <= abs_idx < n):
                return
            self.file_browser_index = abs_idx
            entry = self.file_browser_entries[abs_idx]
            entry_path = str(entry.get("path", ""))
            if not entry_path:
                return

            is_double = self._file_browser_register_click(abs_idx, entry_path)
            if is_double:
                self._file_browser_activate_selected()
                return

            # Save As convenience: single-clicking an existing file adopts its name.
            if self.file_browser_mode == "save_as":
                is_dir = bool(entry.get("is_dir", False))
                is_action = bool(entry.get("is_action", False))
                if not is_dir and not is_action:
                    self.file_browser_save_name = os.path.basename(entry_path)
                    self._refresh_file_browser_entries()
                    for i, refreshed in enumerate(self.file_browser_entries):
                        if str(refreshed.get("path", "")) == entry_path:
                            self.file_browser_index = i
                            break
            return

        if self.menu_mode == "file_browser_overwrite_confirm":
            panel_w = 760
            panel_h = 200
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = self.size[1] // 2 - panel_h // 2
            btn_w = 130
            btn_h = 38
            yes_x = panel_x + panel_w // 2 - btn_w - 18
            no_x = panel_x + panel_w // 2 + 18
            btn_y = panel_y + panel_h - 62
            if yes_x <= mx <= yes_x + btn_w and btn_y <= my <= btn_y + btn_h:
                target = self.file_browser_overwrite_target
                self.file_browser_overwrite_target = ""
                if target and self.save_stg_as(target):
                    self._close_menu()
                else:
                    self.menu_mode = "file_browser"
                return
            if no_x <= mx <= no_x + btn_w and btn_y <= my <= btn_y + btn_h:
                self.file_browser_overwrite_target = ""
                self.menu_mode = "file_browser"
            return

        if self.menu_mode == "main":
            y0, row_h, start, visible_count, n = self._main_menu_row_layout()
            if n <= 0:
                return
            row_rel = self._menu_row_hit(my, float(y0), float(row_h), visible_count)
            if row_rel is None:
                return
            abs_idx = start + row_rel
            if 0 <= abs_idx < n:
                self.main_menu_index = abs_idx
                self._run_main_menu_action()
            return

        if self.menu_mode == "bindings" and not self.binding_capture_action:
            idx = self._menu_row_hit(my, 160.0, 34.0, len(self.binding_actions))
            if idx is not None:
                self.binding_menu_index = idx
                self.binding_capture_action = self.binding_actions[self.binding_menu_index]
                self._set_status_t(
                    "status.press_key_bind_fmt",
                    "Press a key to bind '{action_label}'",
                    action_label=self.binding_labels[self.binding_capture_action],
                )
            return

        if self.menu_mode == "add_object_cats":
            row_h, max_rows, start, n = self._add_object_cats_row_layout()
            if n <= 0:
                return
            visible_count = min(max_rows, max(0, n - start))
            row_rel = self._menu_row_hit(my, 168.0, float(row_h), visible_count)
            if row_rel is None:
                return
            abs_idx = start + row_rel
            if not (0 <= abs_idx < n):
                return
            self.add_object_category_index = abs_idx
            cat = self.add_object_category_list[self.add_object_category_index]
            self.add_object_selected_category = cat
            self._remember_add_object_category(cat)
            self.add_object_file_index = 0
            self.add_object_file_scroll_start = 0
            self._reset_add_object_hover_state()
            self.menu_mode = "add_object_files"
            files = self.add_object_by_category.get(self.add_object_selected_category, [])
            if files:
                selected_entry = files[self.add_object_file_index]
                self._update_add_object_preview(selected_entry)
                self._set_status_t(
                    "status.preview_object_fmt",
                    "Preview object: {path}",
                    path=selected_entry.object_path,
                )
            else:
                self._clear_add_object_preview()
            return

        if self.menu_mode == "add_object_files":
            files = self.add_object_by_category.get(self.add_object_selected_category, [])
            n = len(files)
            row_y, row_h, _file_rows, start, visible_files, total_rows = self._add_object_files_row_layout(n)
            row_rel = self._menu_row_hit(my, float(row_y), float(row_h), total_rows)
            if row_rel is None:
                return

            if row_rel == 0:
                self._clear_add_object_preview()
                self._reset_add_object_hover_state()
                self.menu_mode = "add_object_cats"
                return

            abs_idx = start + (row_rel - 1)
            if not (0 <= abs_idx < n):
                return
            self.add_object_file_index = abs_idx
            self.add_object_parent_row_hovered = False
            selected_entry = files[self.add_object_file_index]
            self._update_add_object_preview(selected_entry)
            self._set_status_t(
                "status.preview_object_fmt",
                "Preview object: {path}",
                path=selected_entry.object_path,
            )
            if self._place_catalog_object_at_crosshair(selected_entry):
                self._close_menu()
            return

    def _draw_menu(self: "BTGDisplayApp") -> None:
        if self.opengl_enabled:
            self._draw_menu_gl()
            return

        overlay = pygame.Surface(self.size, pygame.SRCALPHA)
        overlay.fill((8, 12, 18, 190))
        self.screen.blit(overlay, (0, 0))

        if self.menu_mode == "main":
            title = self._menu_t("title.menu", "Menu")
        elif self.menu_mode == "bindings":
            title = self._menu_t("title.bindings", "Change Keyboard Bindings")
        elif self.menu_mode == "file_browser":
            if self.file_browser_mode == "load":
                title = self._menu_t("title.file_browser_load", "Load STG")
            elif self.file_browser_mode == "save_as":
                title = self._menu_t("title.file_browser_save_as", "Save As STG")
            elif self.file_browser_mode == "help_text":
                title = self._menu_t("title.file_browser_help_text", "Select Help Text File")
            elif self.file_browser_mode == "menu_language":
                title = self._menu_t("title.file_browser_menu_language", "Select Menu Language")
            elif self.file_browser_mode == "directory_select":
                if self.file_browser_directory_action == "flightgear_root":
                    title = "Select FlightGear Root"
                else:
                    title = "Select Custom Scenery Directory"
            else:
                title = self._menu_t("title.file_browser", "File Browser")
        elif self.menu_mode == "object_nudge":
            title = self._menu_t("title.object_nudge", "Set Object Nudge Distance")
        elif self.menu_mode == "object_nudge_repeat":
            title = self._menu_t("title.object_nudge_repeat", "Set Object Nudge Repeat")
        elif self.menu_mode == "missing_material_color":
            title = self._menu_t("title.missing_material_color", "Set Missing Material Color")
        elif self.menu_mode == "custom_scenery":
            title = self._menu_t("title.custom_scenery", "Custom Scenery Paths")
        elif self.menu_mode == "add_object_cats":
            title = self._menu_t("title.add_object_cats", "Add Object \u2014 Select Category")
        elif self.menu_mode == "add_object_files":
            title_template = self._menu_t("title.add_object_files", "Add Object \u2014 {category}")
            try:
                title = title_template.format(category=self.add_object_selected_category)
            except Exception:
                title = f"Add Object \u2014 {self.add_object_selected_category}"
        elif self.menu_mode == "preview_panel_location":
            title = self._menu_t("title.preview_panel_location", "Preview Panel Location")
        elif self.menu_mode == "grid_settings":
            title = self._menu_t("title.grid_settings", "Grid Settings")
        elif self.menu_mode == "scene_switch_confirm":
            title = "Switch Scene"
        elif self.menu_mode == "camera_clipping":
            title = self._menu_t("title.camera_clipping", "Set Camera Clipping")
        else:
            title = self._menu_t("title.camera_view", "Set Camera Start View")
        title_surf = self.font_large.render(title, True, (242, 242, 242))
        self.screen.blit(title_surf, (self.size[0] // 2 - title_surf.get_width() // 2, 80))

        if self.menu_mode == "main":
            y0, row_h, start, visible_count, n = self._main_menu_row_layout()
            visible_items = self.main_menu_items[start : start + visible_count]
            for i, item in enumerate(visible_items):
                abs_idx = start + i
                selected = abs_idx == self.main_menu_index
                color = (255, 220, 120) if selected else (220, 220, 220)
                prefix = "> " if selected else "  "
                label = self._main_menu_item_label(item)
                surf = self.font_mono.render(prefix + label, True, color)
                self.screen.blit(surf, (self.size[0] // 2 - surf.get_width() // 2, y0 + i * row_h))

            if n > visible_count and visible_count > 0:
                first_item = start + 1
                last_item = start + visible_count
                counter_template = self._menu_t("main.showing_range_fmt", "Showing {first}-{last} of {total}")
                try:
                    counter_text = counter_template.format(first=first_item, last=last_item, total=n)
                except Exception:
                    counter_text = f"Showing {first_item}-{last_item} of {n}"
                counter = self.font_small.render(counter_text, True, (170, 196, 212))
                self.screen.blit(counter, (self.size[0] // 2 - counter.get_width() // 2, 136))

        elif self.menu_mode == "bindings":
            y0 = 160
            for i, action in enumerate(self.binding_actions):
                selected = i == self.binding_menu_index
                color = (255, 220, 120) if selected else (220, 220, 220)
                prefix = "> " if selected else "  "
                key_name = self.format_binding(self.bindings[action])
                label = self.binding_labels[action]
                text = f"{prefix}{label:<12} : {key_name}"
                surf = self.font_mono.render(text, True, color)
                self.screen.blit(surf, (self.size[0] // 2 - surf.get_width() // 2, y0 + i * 34))

        elif self.menu_mode == "file_browser":
            panel_x, panel_y, panel_w, panel_h, row_y, row_h, max_rows, start, n = self._file_browser_row_layout()
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=8)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=8)

            if self.file_browser_mode == "load":
                mode_text = self._menu_t("file_browser.mode_load", "Load .stg file")
            elif self.file_browser_mode == "save_as":
                mode_text = self._menu_t("file_browser.mode_save_as", "Save As target")
            elif self.file_browser_mode == "help_text":
                mode_text = self._menu_t("file_browser.mode_help_text", "Select help text file")
            elif self.file_browser_mode == "menu_language":
                mode_text = self._menu_t("file_browser.mode_menu_language", "Select menu language file")
            elif self.file_browser_mode == "directory_select":
                if self.file_browser_directory_action == "flightgear_root":
                    mode_text = "Select FlightGear root directory"
                else:
                    mode_text = "Select a custom scenery directory"
            else:
                mode_text = self._menu_t("file_browser.mode_default", "File browser")
            mode_surf = self.font_small.render(mode_text, True, (180, 210, 190))
            self.screen.blit(mode_surf, (panel_x + 18, panel_y + 12))

            if os.name == "nt" and self.file_browser_dir == self._WINDOWS_DRIVES_ROOT:
                dir_text = "Computer (All Drives)"
            else:
                dir_text = self.file_browser_dir
            max_dir_chars = max(16, (panel_w - 36) // 8)
            if len(dir_text) > max_dir_chars:
                dir_text = "..." + dir_text[-(max_dir_chars - 3):]
            folder_label = self._menu_t("file_browser.folder_label", "Folder")
            dir_surf = self.font_small.render(f"{folder_label}: {dir_text}", True, (170, 200, 220))
            self.screen.blit(dir_surf, (panel_x + 18, panel_y + 34))

            if self.file_browser_mode == "save_as":
                name_label = self._menu_t("file_browser.name_label", "Name")
                save_surf = self.font_small.render(f"{name_label}: {self.file_browser_save_name}", True, (170, 225, 170))
                self.screen.blit(save_surf, (panel_x + 18, panel_y + 56))

            visible = self.file_browser_entries[start : start + max_rows]
            max_name_chars = max(10, (panel_w - 60) // 11)
            for i, entry in enumerate(visible):
                abs_idx = start + i
                selected = abs_idx == self.file_browser_index
                entry_name = str(entry.get("name", ""))
                if len(entry_name) > max_name_chars:
                    entry_name = "…" + entry_name[-(max_name_chars - 1):]
                base_color = entry.get("color", (220, 220, 220))
                color = (255, 220, 120) if selected else base_color
                prefix = "> " if selected else "  "
                surf = self.font_mono.render(prefix + entry_name, True, color)
                self.screen.blit(surf, (panel_x + 18, row_y + i * row_h))

            if not self.file_browser_entries:
                empty_surf = self.font_small.render(
                    self._menu_t("file_browser.empty_folder", "(No entries in this folder)"),
                    True,
                    (170, 170, 170),
                )
                self.screen.blit(empty_surf, (panel_x + 18, row_y))

        elif self.menu_mode == "file_browser_overwrite_confirm":
            panel_w = 760
            panel_h = 200
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = self.size[1] // 2 - panel_h // 2
            pygame.draw.rect(self.screen, (26, 26, 34), (panel_x, panel_y, panel_w, panel_h), border_radius=10)
            pygame.draw.rect(self.screen, (160, 130, 90), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=10)

            target_name = os.path.basename(self.file_browser_overwrite_target) or self.file_browser_overwrite_target
            msg1 = self.font_small.render(
                self._menu_t("dialog.overwrite_exists", "File already exists. Overwrite?"),
                True,
                (235, 220, 190),
            )
            msg2 = self.font_small.render(target_name, True, (180, 220, 255))
            self.screen.blit(msg1, (panel_x + 24, panel_y + 42))
            self.screen.blit(msg2, (panel_x + 24, panel_y + 72))

            btn_w = 130
            btn_h = 38
            yes_x = panel_x + panel_w // 2 - btn_w - 18
            no_x = panel_x + panel_w // 2 + 18
            btn_y = panel_y + panel_h - 62

            pygame.draw.rect(self.screen, (48, 104, 62), (yes_x, btn_y, btn_w, btn_h), border_radius=6)
            pygame.draw.rect(self.screen, (132, 210, 152), (yes_x, btn_y, btn_w, btn_h), 2, border_radius=6)
            pygame.draw.rect(self.screen, (104, 52, 52), (no_x, btn_y, btn_w, btn_h), border_radius=6)
            pygame.draw.rect(self.screen, (220, 140, 140), (no_x, btn_y, btn_w, btn_h), 2, border_radius=6)

            yes_surf = self.font_mono.render(self._menu_t("dialog.overwrite", "Overwrite"), True, (238, 248, 238))
            no_surf = self.font_mono.render(self._menu_t("dialog.cancel", "Cancel"), True, (248, 238, 238))
            self.screen.blit(yes_surf, (yes_x + btn_w // 2 - yes_surf.get_width() // 2, btn_y + 8))
            self.screen.blit(no_surf, (no_x + btn_w // 2 - no_surf.get_width() // 2, btn_y + 8))

        elif self.menu_mode == "save_confirm":
            panel_w = 760
            panel_h = 190
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = self.size[1] // 2 - panel_h // 2
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=10)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=10)

            message = self.save_confirm_message or self._menu_t("dialog.save_completed", "Save completed.")
            max_chars = max(24, (panel_w - 48) // 9)
            if len(message) > max_chars:
                message = message[: max_chars - 3] + "..."
            msg_surf = self.font_small.render(message, True, (190, 230, 200))
            self.screen.blit(msg_surf, (panel_x + 24, panel_y + 70))

            ok_w = 120
            ok_h = 36
            ok_x = panel_x + panel_w // 2 - ok_w // 2
            ok_y = panel_y + panel_h - 56
            pygame.draw.rect(self.screen, (48, 104, 62), (ok_x, ok_y, ok_w, ok_h), border_radius=6)
            pygame.draw.rect(self.screen, (132, 210, 152), (ok_x, ok_y, ok_w, ok_h), 2, border_radius=6)
            ok_surf = self.font_mono.render(self._menu_t("dialog.ok", "OK"), True, (238, 248, 238))
            self.screen.blit(ok_surf, (ok_x + ok_w // 2 - ok_surf.get_width() // 2, ok_y + 7))

        elif self.menu_mode == "scene_switch_confirm":
            panel_w = 860
            panel_h = 230
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = self.size[1] // 2 - panel_h // 2
            pygame.draw.rect(self.screen, (26, 26, 34), (panel_x, panel_y, panel_w, panel_h), border_radius=10)
            pygame.draw.rect(self.screen, (160, 130, 90), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=10)

            message = self.scene_switch_confirm_message or self._menu_t(
                "dialog.scene_switch_prompt",
                "Save changes before switching scenes?",
            )
            max_chars = max(30, (panel_w - 48) // 9)
            if len(message) > max_chars:
                message = message[: max_chars - 3] + "..."
            msg_surf = self.font_small.render(message, True, (235, 220, 190))
            self.screen.blit(msg_surf, (panel_x + 24, panel_y + 54))

            sub_surf = self.font_small.render(
                self._menu_t(
                    "dialog.scene_switch_subtitle",
                    "Save = keep changes | Discard = continue without saving | Cancel = stay here",
                ),
                True,
                (180, 220, 255),
            )
            self.screen.blit(sub_surf, (panel_x + 24, panel_y + 84))

            save_rect, discard_rect, cancel_rect = self._scene_switch_confirm_button_rects()
            sx, sy, sw, sh = save_rect
            dx, dy, dw, dh = discard_rect
            cx, cy, cw, ch = cancel_rect

            pygame.draw.rect(self.screen, (48, 104, 62), (sx, sy, sw, sh), border_radius=6)
            pygame.draw.rect(self.screen, (132, 210, 152), (sx, sy, sw, sh), 2, border_radius=6)
            pygame.draw.rect(self.screen, (128, 98, 44), (dx, dy, dw, dh), border_radius=6)
            pygame.draw.rect(self.screen, (220, 190, 132), (dx, dy, dw, dh), 2, border_radius=6)
            pygame.draw.rect(self.screen, (104, 52, 52), (cx, cy, cw, ch), border_radius=6)
            pygame.draw.rect(self.screen, (220, 140, 140), (cx, cy, cw, ch), 2, border_radius=6)

            save_surf = self.font_mono.render(self._menu_t("dialog.save", "Save"), True, (238, 248, 238))
            discard_surf = self.font_mono.render(self._menu_t("dialog.discard", "Discard"), True, (250, 244, 226))
            cancel_surf = self.font_mono.render(self._menu_t("dialog.cancel", "Cancel"), True, (248, 238, 238))
            self.screen.blit(save_surf, (sx + sw // 2 - save_surf.get_width() // 2, sy + 8))
            self.screen.blit(discard_surf, (dx + dw // 2 - discard_surf.get_width() // 2, dy + 8))
            self.screen.blit(cancel_surf, (cx + cw // 2 - cancel_surf.get_width() // 2, cy + 8))

        elif self.menu_mode == "camera_view":
            panel_w = 640
            panel_h = 210
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 180
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=8)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=8)

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.camera_view_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.camera_view_field_index
                fill = (40, 52, 70) if is_active else (30, 40, 56)
                border = (255, 220, 120) if is_active else (90, 108, 132)
                pygame.draw.rect(self.screen, fill, (box_x, y, box_w, box_h), border_radius=6)
                pygame.draw.rect(self.screen, border, (box_x, y, box_w, box_h), 2, border_radius=6)

                value = self.camera_view_fields[i] if self.camera_view_fields[i] else ""
                text = f"{label}: {value}"
                text_surf = self.font_mono.render(text, True, (236, 236, 236))
                self.screen.blit(text_surf, (box_x + 14, y + 12))

        elif self.menu_mode == "camera_clipping":
            panel_w = 640
            panel_h = 210
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 180
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=8)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=8)

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.camera_clipping_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.camera_clipping_field_index
                fill = (40, 52, 70) if is_active else (30, 40, 56)
                border = (255, 220, 120) if is_active else (90, 108, 132)
                pygame.draw.rect(self.screen, fill, (box_x, y, box_w, box_h), border_radius=6)
                pygame.draw.rect(self.screen, border, (box_x, y, box_w, box_h), 2, border_radius=6)

                value = self.camera_clipping_fields[i] if self.camera_clipping_fields[i] else ""
                text_surf = self.font_mono.render(f"{label}: {value}", True, (236, 236, 236))
                self.screen.blit(text_surf, (box_x + 14, y + 12))

        elif self.menu_mode == "grid_settings":
            panel_w = 680
            panel_h = 270
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 150
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=8)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=8)

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.grid_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.grid_field_index
                fill = (40, 52, 70) if is_active else (30, 40, 56)
                border = (255, 220, 120) if is_active else (90, 108, 132)
                pygame.draw.rect(self.screen, fill, (box_x, y, box_w, box_h), border_radius=6)
                pygame.draw.rect(self.screen, border, (box_x, y, box_w, box_h), 2, border_radius=6)

                value = self.grid_fields[i] if self.grid_fields[i] else ""
                text = f"{label}: {value}"
                text_surf = self.font_mono.render(text, True, (236, 236, 236))
                self.screen.blit(text_surf, (box_x + 14, y + 12))

        elif self.menu_mode == "preview_panel_location":
            panel_w = 700
            panel_h = 330
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 140
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=8)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=8)

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.preview_panel_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.preview_panel_field_index
                fill = (40, 52, 70) if is_active else (30, 40, 56)
                border = (255, 220, 120) if is_active else (90, 108, 132)
                pygame.draw.rect(self.screen, fill, (box_x, y, box_w, box_h), border_radius=6)
                pygame.draw.rect(self.screen, border, (box_x, y, box_w, box_h), 2, border_radius=6)

                value = self.preview_panel_fields[i] if self.preview_panel_fields[i] else ""
                text = f"{label}: {value}"
                text_surf = self.font_mono.render(text, True, (236, 236, 236))
                self.screen.blit(text_surf, (box_x + 14, y + 12))

            mode_text = f"Preview style: {self.preview_panel_render_mode} (Left/Right to change)"
            mode_surf = self.font_small.render(mode_text, True, (185, 230, 205))
            self.screen.blit(mode_surf, (panel_x + 38, panel_y + panel_h - 28))

        elif self.menu_mode == "object_nudge":
            panel_w = 640
            panel_h = 150
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 210
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=8)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=8)

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            y = panel_y + 44
            pygame.draw.rect(self.screen, (40, 52, 70), (box_x, y, box_w, box_h), border_radius=6)
            pygame.draw.rect(self.screen, (255, 220, 120), (box_x, y, box_w, box_h), 2, border_radius=6)

            value = self.object_nudge_fields[0] if self.object_nudge_fields[0] else ""
            text = f"{self.object_nudge_field_labels[0]}: {value}"
            text_surf = self.font_mono.render(text, True, (236, 236, 236))
            self.screen.blit(text_surf, (box_x + 14, y + 12))

        elif self.menu_mode == "object_nudge_repeat":
            panel_w = 640
            panel_h = 210
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 180
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=8)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=8)

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.object_nudge_repeat_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.object_nudge_repeat_field_index
                fill = (40, 52, 70) if is_active else (30, 40, 56)
                border = (255, 220, 120) if is_active else (90, 108, 132)
                pygame.draw.rect(self.screen, fill, (box_x, y, box_w, box_h), border_radius=6)
                pygame.draw.rect(self.screen, border, (box_x, y, box_w, box_h), 2, border_radius=6)

                value = self.object_nudge_repeat_fields[i] if self.object_nudge_repeat_fields[i] else ""
                text = f"{label}: {value}"
                text_surf = self.font_mono.render(text, True, (236, 236, 236))
                self.screen.blit(text_surf, (box_x + 14, y + 12))

        elif self.menu_mode == "custom_scenery":
            panel_w = 980
            panel_h = 430
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 150
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=8)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=8)

            info = (
                f"Catalog entries: {len(self.object_catalog)} | "
                f"Custom paths: {len(self.custom_scenery_paths)}"
            )
            info_surf = self.font_small.render(info, True, (180, 210, 190))
            self.screen.blit(info_surf, (panel_x + 18, panel_y + 16))

            row_y, row_h, max_rows, start = self._custom_scenery_row_layout()
            visible = self.custom_scenery_paths[start : start + max_rows]
            for i, path in enumerate(visible):
                absolute_idx = start + i
                selected = absolute_idx == self.custom_scenery_menu_index
                color = (255, 220, 120) if selected else (220, 220, 220)
                prefix = "> " if selected else "  "
                text = f"{prefix}{path}"
                row_surf = self.font_small.render(text, True, color)
                self.screen.blit(row_surf, (panel_x + 18, row_y + i * row_h))

            if not self.custom_scenery_paths:
                empty_surf = self.font_small.render(
                    self._menu_t("custom_scenery.empty_paths", "(No custom scenery paths configured)"),
                    True,
                    (170, 170, 170),
                )
                self.screen.blit(empty_surf, (panel_x + 18, row_y))

        elif self.menu_mode in ("add_object_cats", "add_object_files"):
            panel_w = min(1100, self.size[0] - 60)
            panel_h = min(500, self.size[1] - 180)
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 120
            pygame.draw.rect(self.screen, (20, 28, 40), (panel_x, panel_y, panel_w, panel_h), border_radius=8)
            pygame.draw.rect(self.screen, (78, 96, 120), (panel_x, panel_y, panel_w, panel_h), 2, border_radius=8)

            if self.menu_mode == "add_object_cats":
                hdr = f"Catalog: {len(self.object_catalog)} models  |  {len(self.add_object_category_list)} categories"
                hdr_surf = self.font_small.render(hdr, True, (180, 210, 190))
                self.screen.blit(hdr_surf, (panel_x + 18, panel_y + 14))

                row_h, max_rows, start, _n = self._add_object_cats_row_layout()
                idx = self.add_object_category_index
                visible_cats = self.add_object_category_list[start : start + max_rows]
                row_y = panel_y + 48
                for i, cat in enumerate(visible_cats):
                    abs_idx = start + i
                    selected = abs_idx == idx
                    count = len(self.add_object_by_category.get(cat, []))
                    color = (255, 220, 120) if selected else (220, 220, 220)
                    prefix = "> " if selected else "  "
                    text = f"{prefix}{cat:<24} {count:>5} models"
                    surf = self.font_mono.render(text, True, color)
                    self.screen.blit(surf, (panel_x + 18, row_y + i * row_h))

                if not self.add_object_category_list:
                    empty_surf = self.font_small.render(
                        "(No objects in catalog \u2014 set FlightGear location first)",
                        True,
                        (170, 170, 170),
                    )
                    self.screen.blit(empty_surf, (panel_x + 18, panel_y + 48))

            else:  # add_object_files
                files = self.add_object_by_category.get(self.add_object_selected_category, [])
                hdr = f"{self.add_object_selected_category}  \u2014  {len(files)} models"
                hdr_surf = self.font_small.render(hdr, True, (180, 210, 190))
                self.screen.blit(hdr_surf, (panel_x + 18, panel_y + 14))

                preview_path = self.add_object_preview_path if self.add_object_preview_path else "(no preview)"
                preview_label = self.font_small.render(f"Preview: {preview_path}", True, (170, 225, 170))
                self.screen.blit(preview_label, (panel_x + 18, panel_y + 34))

                idx = self.add_object_file_index
                n = len(files)
                row_y, row_h, _file_rows, start, visible_file_count, _total_rows = self._add_object_files_row_layout(n)
                visible_files = files[start : start + visible_file_count]
                max_chars = max(10, (panel_w - 60) // 11)

                parent_color = (255, 220, 120) if self.add_object_parent_row_hovered else (185, 205, 190)
                parent_prefix = "> " if self.add_object_parent_row_hovered else "  "
                parent_surf = self.font_mono.render(f"{parent_prefix}[..] Parent Folder", True, parent_color)
                self.screen.blit(parent_surf, (panel_x + 18, row_y))

                for i, entry in enumerate(visible_files):
                    abs_idx = start + i
                    selected = abs_idx == idx
                    color = (255, 220, 120) if selected else (220, 220, 220)
                    prefix = "> " if selected else "  "
                    name = entry.object_path
                    if len(name) > max_chars:
                        name = "\u2026" + name[-(max_chars - 1):]
                    surf = self.font_mono.render(prefix + name, True, color)
                    self.screen.blit(surf, (panel_x + 18, row_y + (i + 1) * row_h))

                if not files:
                    empty_surf = self.font_small.render(
                        "(No models in this category)", True, (170, 170, 170)
                    )
                    self.screen.blit(empty_surf, (panel_x + 18, panel_y + 48))

        bottom_hint_text = self._menu_bottom_hint_text()
        if bottom_hint_text:
            self._draw_wrapped_menu_hint(bottom_hint_text)

    def _draw_menu_gl(self: "BTGDisplayApp") -> None:
        self._gl_begin_2d()
        self._gl_draw_rect(0, 0, self.size[0], self.size[1], (8 / 255.0, 12 / 255.0, 18 / 255.0, 0.75))

        if self.menu_mode == "main":
            title = self._menu_t("title.menu", "Menu")
        elif self.menu_mode == "bindings":
            title = self._menu_t("title.bindings", "Change Keyboard Bindings")
        elif self.menu_mode == "file_browser":
            if self.file_browser_mode == "load":
                title = self._menu_t("title.file_browser_load", "Load STG")
            elif self.file_browser_mode == "save_as":
                title = self._menu_t("title.file_browser_save_as", "Save As STG")
            elif self.file_browser_mode == "help_text":
                title = self._menu_t("title.file_browser_help_text", "Select Help Text File")
            elif self.file_browser_mode == "menu_language":
                title = self._menu_t("title.file_browser_menu_language", "Select Menu Language")
            elif self.file_browser_mode == "directory_select":
                if self.file_browser_directory_action == "flightgear_root":
                    title = "Select FlightGear Root"
                else:
                    title = "Select Custom Scenery Directory"
            else:
                title = self._menu_t("title.file_browser", "File Browser")
        elif self.menu_mode == "object_nudge":
            title = self._menu_t("title.object_nudge", "Set Object Nudge Distance")
        elif self.menu_mode == "object_nudge_repeat":
            title = self._menu_t("title.object_nudge_repeat", "Set Object Nudge Repeat")
        elif self.menu_mode == "missing_material_color":
            title = self._menu_t("title.missing_material_color", "Set Missing Material Color")
        elif self.menu_mode == "custom_scenery":
            title = self._menu_t("title.custom_scenery", "Custom Scenery Paths")
        elif self.menu_mode == "add_object_cats":
            title = self._menu_t("title.add_object_cats", "Add Object \u2014 Select Category")
        elif self.menu_mode == "add_object_files":
            title_template = self._menu_t("title.add_object_files", "Add Object \u2014 {category}")
            try:
                title = title_template.format(category=self.add_object_selected_category)
            except Exception:
                title = f"Add Object \u2014 {self.add_object_selected_category}"
        elif self.menu_mode == "preview_panel_location":
            title = self._menu_t("title.preview_panel_location", "Preview Panel Location")
        elif self.menu_mode == "grid_settings":
            title = self._menu_t("title.grid_settings", "Grid Settings")
        elif self.menu_mode == "scene_switch_confirm":
            title = "Switch Scene"
        elif self.menu_mode == "camera_clipping":
            title = self._menu_t("title.camera_clipping", "Set Camera Clipping")
        else:
            title = self._menu_t("title.camera_view", "Set Camera Start View")
        title_x = self.size[0] // 2 - 180
        self._gl_draw_text(title, title_x, 80, (242, 242, 242), self.font_large)

        if self.menu_mode == "main":
            y0, row_h, start, visible_count, n = self._main_menu_row_layout()
            visible_items = self.main_menu_items[start : start + visible_count]
            for i, item in enumerate(visible_items):
                abs_idx = start + i
                selected = abs_idx == self.main_menu_index
                color = (255, 220, 120) if selected else (220, 220, 220)
                prefix = "> " if selected else "  "
                label = self._main_menu_item_label(item)
                self._gl_draw_text(prefix + label, self.size[0] // 2 - 190, y0 + i * row_h, color, self.font_mono)

            if n > visible_count and visible_count > 0:
                first_item = start + 1
                last_item = start + visible_count
                counter_template = self._menu_t("main.showing_range_fmt", "Showing {first}-{last} of {total}")
                try:
                    counter_text = counter_template.format(first=first_item, last=last_item, total=n)
                except Exception:
                    counter_text = f"Showing {first_item}-{last_item} of {n}"
                self._gl_draw_text(
                    counter_text,
                    self.size[0] // 2 - 92,
                    136,
                    (170, 196, 212),
                    self.font_small,
                )
        elif self.menu_mode == "bindings":
            y0 = 160
            for i, action in enumerate(self.binding_actions):
                selected = i == self.binding_menu_index
                color = (255, 220, 120) if selected else (220, 220, 220)
                prefix = "> " if selected else "  "
                key_name = self.format_binding(self.bindings[action])
                label = self.binding_labels[action]
                text = f"{prefix}{label:<12} : {key_name}"
                self._gl_draw_text(text, self.size[0] // 2 - 220, y0 + i * 34, color, self.font_mono)

        elif self.menu_mode == "file_browser":
            panel_x, panel_y, panel_w, panel_h, row_y, row_h, max_rows, start, n = self._file_browser_row_layout()
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            if self.file_browser_mode == "load":
                mode_text = self._menu_t("file_browser.mode_load", "Load .stg file")
            elif self.file_browser_mode == "save_as":
                mode_text = self._menu_t("file_browser.mode_save_as", "Save As target")
            elif self.file_browser_mode == "help_text":
                mode_text = self._menu_t("file_browser.mode_help_text", "Select help text file")
            elif self.file_browser_mode == "menu_language":
                mode_text = self._menu_t("file_browser.mode_menu_language", "Select menu language file")
            elif self.file_browser_mode == "directory_select":
                if self.file_browser_directory_action == "flightgear_root":
                    mode_text = "Select FlightGear root directory"
                else:
                    mode_text = "Select a custom scenery directory"
            else:
                mode_text = self._menu_t("file_browser.mode_default", "File browser")
            self._gl_draw_text(mode_text, panel_x + 18, panel_y + 12, (180, 210, 190), self.font_small)

            if os.name == "nt" and self.file_browser_dir == self._WINDOWS_DRIVES_ROOT:
                dir_text = "Computer (All Drives)"
            else:
                dir_text = self.file_browser_dir
            max_dir_chars = max(16, (panel_w - 36) // 8)
            if len(dir_text) > max_dir_chars:
                dir_text = "..." + dir_text[-(max_dir_chars - 3):]
            self._gl_draw_text(f"Folder: {dir_text}", panel_x + 18, panel_y + 34, (170, 200, 220), self.font_small)

            if self.file_browser_mode == "save_as":
                self._gl_draw_text(f"Name: {self.file_browser_save_name}", panel_x + 18, panel_y + 56, (170, 225, 170), self.font_small)

            visible = self.file_browser_entries[start : start + max_rows]
            max_name_chars = max(10, (panel_w - 60) // 11)
            for i, entry in enumerate(visible):
                abs_idx = start + i
                selected = abs_idx == self.file_browser_index
                entry_name = str(entry.get("name", ""))
                if len(entry_name) > max_name_chars:
                    entry_name = "…" + entry_name[-(max_name_chars - 1):]
                base_color = entry.get("color", (220, 220, 220))
                color = (255, 220, 120) if selected else base_color
                prefix = "> " if selected else "  "
                self._gl_draw_text(prefix + entry_name, panel_x + 18, row_y + i * row_h, color, self.font_mono)

            if not self.file_browser_entries:
                self._gl_draw_text("(No entries in this folder)", panel_x + 18, row_y, (170, 170, 170), self.font_small)

        elif self.menu_mode == "file_browser_overwrite_confirm":
            panel_w = 760
            panel_h = 200
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = self.size[1] // 2 - panel_h // 2
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (26 / 255.0, 26 / 255.0, 34 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (160 / 255.0, 130 / 255.0, 90 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (160 / 255.0, 130 / 255.0, 90 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (160 / 255.0, 130 / 255.0, 90 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (160 / 255.0, 130 / 255.0, 90 / 255.0, 1.0))

            target_name = os.path.basename(self.file_browser_overwrite_target) or self.file_browser_overwrite_target
            self._gl_draw_text("File already exists. Overwrite?", panel_x + 24, panel_y + 42, (235, 220, 190), self.font_small)
            self._gl_draw_text(target_name, panel_x + 24, panel_y + 72, (180, 220, 255), self.font_small)

            btn_w = 130
            btn_h = 38
            yes_x = panel_x + panel_w // 2 - btn_w - 18
            no_x = panel_x + panel_w // 2 + 18
            btn_y = panel_y + panel_h - 62
            self._gl_draw_rect(yes_x, btn_y, btn_w, btn_h, (48 / 255.0, 104 / 255.0, 62 / 255.0, 0.95))
            self._gl_draw_rect(no_x, btn_y, btn_w, btn_h, (104 / 255.0, 52 / 255.0, 52 / 255.0, 0.95))
            self._gl_draw_text("Overwrite", yes_x + 18, btn_y + 8, (238, 248, 238), self.font_mono)
            self._gl_draw_text("Cancel", no_x + 28, btn_y + 8, (248, 238, 238), self.font_mono)

        elif self.menu_mode == "save_confirm":
            panel_w = 760
            panel_h = 190
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = self.size[1] // 2 - panel_h // 2
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            message = self.save_confirm_message or "Save completed."
            max_chars = max(24, (panel_w - 48) // 9)
            if len(message) > max_chars:
                message = message[: max_chars - 3] + "..."
            self._gl_draw_text(message, panel_x + 24, panel_y + 70, (190, 230, 200), self.font_small)

            ok_w = 120
            ok_h = 36
            ok_x = panel_x + panel_w // 2 - ok_w // 2
            ok_y = panel_y + panel_h - 56
            self._gl_draw_rect(ok_x, ok_y, ok_w, ok_h, (48 / 255.0, 104 / 255.0, 62 / 255.0, 0.95))
            self._gl_draw_rect(ok_x, ok_y, ok_w, 2, (132 / 255.0, 210 / 255.0, 152 / 255.0, 1.0))
            self._gl_draw_rect(ok_x, ok_y + ok_h - 2, ok_w, 2, (132 / 255.0, 210 / 255.0, 152 / 255.0, 1.0))
            self._gl_draw_rect(ok_x, ok_y, 2, ok_h, (132 / 255.0, 210 / 255.0, 152 / 255.0, 1.0))
            self._gl_draw_rect(ok_x + ok_w - 2, ok_y, 2, ok_h, (132 / 255.0, 210 / 255.0, 152 / 255.0, 1.0))
            self._gl_draw_text("OK", ok_x + 46, ok_y + 8, (238, 248, 238), self.font_mono)

        elif self.menu_mode == "scene_switch_confirm":
            panel_w = 860
            panel_h = 230
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = self.size[1] // 2 - panel_h // 2
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (26 / 255.0, 26 / 255.0, 34 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (160 / 255.0, 130 / 255.0, 90 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (160 / 255.0, 130 / 255.0, 90 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (160 / 255.0, 130 / 255.0, 90 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (160 / 255.0, 130 / 255.0, 90 / 255.0, 1.0))

            message = self.scene_switch_confirm_message or "Save changes before switching scenes?"
            max_chars = max(30, (panel_w - 48) // 9)
            if len(message) > max_chars:
                message = message[: max_chars - 3] + "..."
            self._gl_draw_text(message, panel_x + 24, panel_y + 54, (235, 220, 190), self.font_small)
            self._gl_draw_text(
                "Save = keep changes | Discard = continue without saving | Cancel = stay here",
                panel_x + 24,
                panel_y + 84,
                (180, 220, 255),
                self.font_small,
            )

            save_rect, discard_rect, cancel_rect = self._scene_switch_confirm_button_rects()
            sx, sy, sw, sh = save_rect
            dx, dy, dw, dh = discard_rect
            cx, cy, cw, ch = cancel_rect
            self._gl_draw_rect(sx, sy, sw, sh, (48 / 255.0, 104 / 255.0, 62 / 255.0, 0.95))
            self._gl_draw_rect(dx, dy, dw, dh, (128 / 255.0, 98 / 255.0, 44 / 255.0, 0.95))
            self._gl_draw_rect(cx, cy, cw, ch, (104 / 255.0, 52 / 255.0, 52 / 255.0, 0.95))
            self._gl_draw_text("Save", sx + 48, sy + 8, (238, 248, 238), self.font_mono)
            self._gl_draw_text("Discard", dx + 25, dy + 8, (250, 244, 226), self.font_mono)
            self._gl_draw_text("Cancel", cx + 32, cy + 8, (248, 238, 238), self.font_mono)

        elif self.menu_mode == "missing_material_color":
            panel_w = 640
            panel_h = 270
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 150
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.missing_material_color_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.missing_material_color_field_index
                fill = (40 / 255.0, 52 / 255.0, 70 / 255.0, 0.95) if is_active else (30 / 255.0, 40 / 255.0, 56 / 255.0, 0.95)
                border = (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0) if is_active else (90 / 255.0, 108 / 255.0, 132 / 255.0, 1.0)
                self._gl_draw_rect(box_x, y, box_w, box_h, fill)
                self._gl_draw_rect(box_x, y, box_w, 2, border)
                self._gl_draw_rect(box_x, y + box_h - 2, box_w, 2, border)
                self._gl_draw_rect(box_x, y, 2, box_h, border)
                self._gl_draw_rect(box_x + box_w - 2, y, 2, box_h, border)

                value = self.missing_material_color_fields[i] if self.missing_material_color_fields[i] else ""
                self._gl_draw_text(f"{label}: {value}", box_x + 14, y + 12, (236, 236, 236), self.font_mono)

            preview_color = tuple(max(0, min(255, int(v or 0))) for v in self.missing_material_color_fields)
            self._gl_draw_rect(
                panel_x + panel_w - 110,
                panel_y + 16,
                72,
                24,
                (preview_color[0] / 255.0, preview_color[1] / 255.0, preview_color[2] / 255.0, 1.0),
            )

        elif self.menu_mode == "camera_view":
            panel_w = 640
            panel_h = 210
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 180
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.camera_view_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.camera_view_field_index
                fill = (40 / 255.0, 52 / 255.0, 70 / 255.0, 0.95) if is_active else (30 / 255.0, 40 / 255.0, 56 / 255.0, 0.95)
                border = (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0) if is_active else (90 / 255.0, 108 / 255.0, 132 / 255.0, 1.0)
                self._gl_draw_rect(box_x, y, box_w, box_h, fill)
                self._gl_draw_rect(box_x, y, box_w, 2, border)
                self._gl_draw_rect(box_x, y + box_h - 2, box_w, 2, border)
                self._gl_draw_rect(box_x, y, 2, box_h, border)
                self._gl_draw_rect(box_x + box_w - 2, y, 2, box_h, border)

                value = self.camera_view_fields[i] if self.camera_view_fields[i] else ""
                self._gl_draw_text(f"{label}: {value}", box_x + 14, y + 12, (236, 236, 236), self.font_mono)

        elif self.menu_mode == "camera_clipping":
            panel_w = 640
            panel_h = 210
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 180
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.camera_clipping_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.camera_clipping_field_index
                fill = (40 / 255.0, 52 / 255.0, 70 / 255.0, 0.95) if is_active else (30 / 255.0, 40 / 255.0, 56 / 255.0, 0.95)
                border = (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0) if is_active else (90 / 255.0, 108 / 255.0, 132 / 255.0, 1.0)
                self._gl_draw_rect(box_x, y, box_w, box_h, fill)
                self._gl_draw_rect(box_x, y, box_w, 2, border)
                self._gl_draw_rect(box_x, y + box_h - 2, box_w, 2, border)
                self._gl_draw_rect(box_x, y, 2, box_h, border)
                self._gl_draw_rect(box_x + box_w - 2, y, 2, box_h, border)

                value = self.camera_clipping_fields[i] if self.camera_clipping_fields[i] else ""
                self._gl_draw_text(f"{label}: {value}", box_x + 14, y + 12, (236, 236, 236), self.font_mono)

        elif self.menu_mode == "grid_settings":
            panel_w = 680
            panel_h = 270
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 150
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.grid_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.grid_field_index
                fill = (40 / 255.0, 52 / 255.0, 70 / 255.0, 0.95) if is_active else (30 / 255.0, 40 / 255.0, 56 / 255.0, 0.95)
                border = (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0) if is_active else (90 / 255.0, 108 / 255.0, 132 / 255.0, 1.0)
                self._gl_draw_rect(box_x, y, box_w, box_h, fill)
                self._gl_draw_rect(box_x, y, box_w, 2, border)
                self._gl_draw_rect(box_x, y + box_h - 2, box_w, 2, border)
                self._gl_draw_rect(box_x, y, 2, box_h, border)
                self._gl_draw_rect(box_x + box_w - 2, y, 2, box_h, border)

                value = self.grid_fields[i] if self.grid_fields[i] else ""
                self._gl_draw_text(f"{label}: {value}", box_x + 14, y + 12, (236, 236, 236), self.font_mono)

        elif self.menu_mode == "preview_panel_location":
            panel_w = 700
            panel_h = 330
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 140
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.preview_panel_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.preview_panel_field_index
                fill = (40 / 255.0, 52 / 255.0, 70 / 255.0, 0.95) if is_active else (30 / 255.0, 40 / 255.0, 56 / 255.0, 0.95)
                border = (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0) if is_active else (90 / 255.0, 108 / 255.0, 132 / 255.0, 1.0)
                self._gl_draw_rect(box_x, y, box_w, box_h, fill)
                self._gl_draw_rect(box_x, y, box_w, 2, border)
                self._gl_draw_rect(box_x, y + box_h - 2, box_w, 2, border)
                self._gl_draw_rect(box_x, y, 2, box_h, border)
                self._gl_draw_rect(box_x + box_w - 2, y, 2, box_h, border)

                value = self.preview_panel_fields[i] if self.preview_panel_fields[i] else ""
                self._gl_draw_text(f"{label}: {value}", box_x + 14, y + 12, (236, 236, 236), self.font_mono)

            self._gl_draw_text(
                f"Preview style: {self.preview_panel_render_mode} (Left/Right to change)",
                panel_x + 38,
                panel_y + panel_h - 28,
                (185, 230, 205),
                self.font_small,
            )

        elif self.menu_mode == "object_nudge":
            panel_w = 640
            panel_h = 150
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 210
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            y = panel_y + 44
            self._gl_draw_rect(box_x, y, box_w, box_h, (40 / 255.0, 52 / 255.0, 70 / 255.0, 0.95))
            self._gl_draw_rect(box_x, y, box_w, 2, (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(box_x, y + box_h - 2, box_w, 2, (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(box_x, y, 2, box_h, (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(box_x + box_w - 2, y, 2, box_h, (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0))

            value = self.object_nudge_fields[0] if self.object_nudge_fields[0] else ""
            self._gl_draw_text(
                f"{self.object_nudge_field_labels[0]}: {value}",
                box_x + 14,
                y + 12,
                (236, 236, 236),
                self.font_mono,
            )

        elif self.menu_mode == "object_nudge_repeat":
            panel_w = 640
            panel_h = 210
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 180
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            box_x = panel_x + 38
            box_w = panel_w - 76
            box_h = 48
            row_start = panel_y + 34
            row_gap = 66
            for i, label in enumerate(self.object_nudge_repeat_field_labels):
                y = row_start + i * row_gap
                is_active = i == self.object_nudge_repeat_field_index
                fill = (40 / 255.0, 52 / 255.0, 70 / 255.0, 0.95) if is_active else (30 / 255.0, 40 / 255.0, 56 / 255.0, 0.95)
                border = (255 / 255.0, 220 / 255.0, 120 / 255.0, 1.0) if is_active else (90 / 255.0, 108 / 255.0, 132 / 255.0, 1.0)
                self._gl_draw_rect(box_x, y, box_w, box_h, fill)
                self._gl_draw_rect(box_x, y, box_w, 2, border)
                self._gl_draw_rect(box_x, y + box_h - 2, box_w, 2, border)
                self._gl_draw_rect(box_x, y, 2, box_h, border)
                self._gl_draw_rect(box_x + box_w - 2, y, 2, box_h, border)

                value = self.object_nudge_repeat_fields[i] if self.object_nudge_repeat_fields[i] else ""
                self._gl_draw_text(f"{label}: {value}", box_x + 14, y + 12, (236, 236, 236), self.font_mono)

        elif self.menu_mode == "custom_scenery":
            panel_w = 980
            panel_h = 430
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 150
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            self._gl_draw_text(
                f"Catalog entries: {len(self.object_catalog)} | Custom paths: {len(self.custom_scenery_paths)}",
                panel_x + 18,
                panel_y + 16,
                (180, 210, 190),
                self.font_small,
            )

            row_y, row_h, max_rows, start = self._custom_scenery_row_layout()
            visible = self.custom_scenery_paths[start : start + max_rows]
            for i, path in enumerate(visible):
                absolute_idx = start + i
                selected = absolute_idx == self.custom_scenery_menu_index
                color = (255, 220, 120) if selected else (220, 220, 220)
                prefix = "> " if selected else "  "
                self._gl_draw_text(f"{prefix}{path}", panel_x + 18, row_y + i * row_h, color, self.font_small)

            if not self.custom_scenery_paths:
                self._gl_draw_text("(No custom scenery paths configured)", panel_x + 18, row_y, (170, 170, 170), self.font_small)

        elif self.menu_mode in ("add_object_cats", "add_object_files"):
            panel_w = min(1100, self.size[0] - 60)
            panel_h = min(500, self.size[1] - 180)
            panel_x = self.size[0] // 2 - panel_w // 2
            panel_y = 120
            self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (20 / 255.0, 28 / 255.0, 40 / 255.0, 0.95))
            self._gl_draw_rect(panel_x, panel_y, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y + panel_h - 2, panel_w, 2, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))
            self._gl_draw_rect(panel_x + panel_w - 2, panel_y, 2, panel_h, (78 / 255.0, 96 / 255.0, 120 / 255.0, 1.0))

            if self.menu_mode == "add_object_cats":
                hdr = f"Catalog: {len(self.object_catalog)} models  |  {len(self.add_object_category_list)} categories"
                self._gl_draw_text(hdr, panel_x + 18, panel_y + 14, (180, 210, 190), self.font_small)

                row_h, max_rows, start, _n = self._add_object_cats_row_layout()
                idx = self.add_object_category_index
                visible_cats = self.add_object_category_list[start : start + max_rows]
                row_y = panel_y + 48
                for i, cat in enumerate(visible_cats):
                    abs_idx = start + i
                    selected = abs_idx == idx
                    count = len(self.add_object_by_category.get(cat, []))
                    color = (255, 220, 120) if selected else (220, 220, 220)
                    prefix = "> " if selected else "  "
                    text = f"{prefix}{cat:<24} {count:>5} models"
                    self._gl_draw_text(text, panel_x + 18, row_y + i * row_h, color, self.font_mono)

                if not self.add_object_category_list:
                    self._gl_draw_text(
                        "(No objects in catalog \u2014 set FlightGear location first)",
                        panel_x + 18, panel_y + 48, (170, 170, 170), self.font_small,
                    )

            else:  # add_object_files
                files = self.add_object_by_category.get(self.add_object_selected_category, [])
                hdr = f"{self.add_object_selected_category}  \u2014  {len(files)} models"
                self._gl_draw_text(hdr, panel_x + 18, panel_y + 14, (180, 210, 190), self.font_small)

                preview_path = self.add_object_preview_path if self.add_object_preview_path else "(no preview)"
                self._gl_draw_text(f"Preview: {preview_path}", panel_x + 18, panel_y + 34, (170, 225, 170), self.font_small)

                idx = self.add_object_file_index
                n = len(files)
                row_y, row_h, _file_rows, start, visible_file_count, _total_rows = self._add_object_files_row_layout(n)
                visible_files = files[start : start + visible_file_count]
                max_chars = max(10, (panel_w - 60) // 11)

                parent_color = (255, 220, 120) if self.add_object_parent_row_hovered else (185, 205, 190)
                parent_prefix = "> " if self.add_object_parent_row_hovered else "  "
                self._gl_draw_text(f"{parent_prefix}[..] Parent Folder", panel_x + 18, row_y, parent_color, self.font_mono)

                for i, entry in enumerate(visible_files):
                    abs_idx = start + i
                    selected = abs_idx == idx
                    color = (255, 220, 120) if selected else (220, 220, 220)
                    prefix = "> " if selected else "  "
                    name = entry.object_path
                    if len(name) > max_chars:
                        name = "\u2026" + name[-(max_chars - 1):]
                    self._gl_draw_text(prefix + name, panel_x + 18, row_y + (i + 1) * row_h, color, self.font_mono)

                if not files:
                    self._gl_draw_text(
                        "(No models in this category)", panel_x + 18, panel_y + 48, (170, 170, 170), self.font_small,
                    )

        bottom_hint_text = self._menu_bottom_hint_text()
        if bottom_hint_text:
            self._draw_wrapped_menu_hint_gl(bottom_hint_text)

        self._gl_end_2d()
