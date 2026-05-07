#!/usr/bin/env python3
"""BTG validator and interactive viewer."""
# Boy that name is outtadate baby lol! Enjoy anyway!


from __future__ import annotations

import argparse
import ctypes
import os
import sys
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:
    import pygame
except Exception as exc:  # pragma: no cover - import error path
    print("Missing dependency: pygame")
    print("Install with: python3 -m pip install pygame")
    print(f"Import error: {exc}")
    raise SystemExit(1)

try:
    from OpenGL import GL, GLU
except Exception:
    GL = None
    GLU = None

from andromeda_backend import (
    BTGMesh,
    KeyBinding,
    ObjectCatalogEntry,
    STGModelInstance,
    ValidationStats,
    default_material_map_path,
    discover_flightgear_root,
    load_viewer_config,
    resolve_fgdata_root,
)
from andromeda_controls import ControlsMixin
from andromeda_menu import MenuMixin
from andromeda_render import RenderMixin
from andromeda_scene import SceneMixin


class BTGDisplayApp(MenuMixin, RenderMixin, ControlsMixin, SceneMixin):
    def __init__(
        self,
        initial_file: Optional[str] = None,
        renderer_preference: str = "auto",
        flightgear_root: str = "",
        material_map_path: str = "",
    ) -> None:
        pygame.init()
        pygame.font.init()

        self.GL = GL
        self.GLU = GLU

        self.size = (1280, 800)
        has_opengl = self.GL is not None and self.GLU is not None
        if renderer_preference == "software":
            self.opengl_enabled = False
        elif renderer_preference == "opengl":
            if not has_opengl:
                raise RuntimeError("OpenGL renderer requested but PyOpenGL is unavailable")
            self.opengl_enabled = True
        else:
            self.opengl_enabled = has_opengl

        self.renderer_name = "OpenGL" if self.opengl_enabled else "Software"
        self._display_flags = pygame.RESIZABLE
        if self.opengl_enabled:
            self._display_flags |= pygame.OPENGL | pygame.DOUBLEBUF

        self.screen = pygame.display.set_mode(self.size, self._display_flags)
        pygame.display.set_caption("FlightGear Andromeda STG Editor")

        self.font_small = pygame.font.SysFont("DejaVu Sans", 16)
        self.font_large = pygame.font.SysFont("DejaVu Sans", 26)
        self.font_mono = pygame.font.SysFont("DejaVu Sans Mono", 18)

        self.running = True
        self.mouse_captured = False
        self.mouse_captured_before_menu = False
        self.show_menu = False
        self.menu_mode = "main"
        self.main_menu_items = [
            "File",
            "Add Object",
            "Options",
            "Exit",
        ]
        self.main_menu_index = 0
        self.main_menu_scroll_start: int = 0
        self.custom_scenery_menu_index = 0
        self.custom_scenery_scroll_start: int = 0
        self.add_object_by_category: Dict[str, List[ObjectCatalogEntry]] = {}
        self.add_object_category_list: List[str] = []
        self.add_object_category_index: int = 0
        self.add_object_category_scroll_start: int = 0
        self.add_object_selected_category: str = ""
        self.add_object_file_index: int = 0
        self.add_object_file_scroll_start: int = 0
        self.add_object_parent_row_hovered: bool = False
        self.menu_hover_scroll_accum: float = 0.0
        self.menu_hover_scroll_speed_scale: float = 0.20
        self.menu_hover_hotspot_fraction: float = 0.10
        self.last_add_object_category: str = ""
        self.add_object_preview_mesh: Optional[BTGMesh] = None
        self.add_object_preview_path: str = ""
        self.add_object_preview_norm_vertices: List[Tuple[float, float, float]] = []
        self.add_object_preview_material_paths: Dict[str, Optional[str]] = {}
        self.add_object_preview_face_step: int = 1
        self.add_object_preview_max_faces: int = 3500
        self.add_object_preview_face_indices: List[int] = []
        self.add_object_preview_textured_face_meta: Dict[int, Tuple[str, int, int, int]] = {}
        self.add_object_preview_face_cache_ready: bool = False
        self.file_browser_mode: str = ""
        self.file_browser_return_mode: str = "main"
        self.file_browser_directory_action: str = ""
        self.file_browser_dir: str = ""
        self.file_browser_entries: List[Dict[str, object]] = []
        self.file_browser_index: int = 0
        self.file_browser_scroll_start: int = 0
        self.file_browser_save_name: str = ""
        self.file_browser_new_folder_name: str = ""
        self.file_browser_overwrite_target: str = ""
        self.file_browser_last_click_index: int = -1
        self.file_browser_last_click_path: str = ""
        self.file_browser_last_click_ms: int = 0
        self.file_browser_double_click_interval_ms: int = 350
        self.save_confirm_title: str = ""
        self.save_confirm_message: str = ""
        self.save_confirm_return_mode: str = ""
        self.package_summary_title: str = ""
        self.package_summary_lines: List[str] = []
        self.package_summary_return_mode: str = ""
        self.scene_switch_confirm_message: str = ""
        self.frame_projected_model_labels: Optional[List[Tuple[float, float, float, str, bool, int]]] = None

        self.binding_actions = [
            "forward",
            "backward",
            "left",
            "right",
            "up",
            "down",
            "speed_down",
            "speed_up",
            "model_yaw_down",
            "model_yaw_up",
            "model_roll_down",
            "model_roll_up",
            "model_pitch_up",
            "model_pitch_down",
            "toggle_labels",
            "toggle_perf_debug",
            "toggle_mouse",
            "save_stg",
            "copy_object",
            "paste_object",
            "delete_object",
            "cycle_object_prev",
            "cycle_object_next",
        ]
        self.binding_labels: Dict[str, str] = {
            "forward": "camera +Z",
            "backward": "camera -Z",
            "left": "left",
            "right": "right",
            "up": "up",
            "down": "down",
            "speed_down": "speed down",
            "speed_up": "speed up",
            "model_yaw_down": "model yaw -",
            "model_yaw_up": "model yaw +",
            "model_roll_down": "model roll -",
            "model_roll_up": "model roll +",
            "model_pitch_up": "model pitch +",
            "model_pitch_down": "model pitch -",
            "toggle_labels": "toggle labels",
            "toggle_perf_debug": "toggle perf debug",
            "toggle_mouse": "toggle mouse",
            "save_stg": "save STG",
            "copy_object": "copy selected object",
            "paste_object": "paste copied object",
            "delete_object": "delete selected object",
            "cycle_object_prev": "prev catalog object",
            "cycle_object_next": "next catalog object",
        }
        self.binding_menu_index = 0
        self.binding_capture_action: Optional[str] = None

        self.bindings: Dict[str, KeyBinding] = {
            "forward": KeyBinding(pygame.K_w),
            "backward": KeyBinding(pygame.K_s),
            "left": KeyBinding(pygame.K_a),
            "right": KeyBinding(pygame.K_d),
            "up": KeyBinding(pygame.K_r),
            "down": KeyBinding(pygame.K_f),
            "speed_down": KeyBinding(pygame.K_MINUS),
            "speed_up": KeyBinding(pygame.K_EQUALS),
            "model_yaw_down": KeyBinding(pygame.K_KP4, pygame.KMOD_SHIFT),
            "model_yaw_up": KeyBinding(pygame.K_KP6, pygame.KMOD_SHIFT),
            "model_roll_down": KeyBinding(pygame.K_KP4),
            "model_roll_up": KeyBinding(pygame.K_KP6),
            "model_pitch_up": KeyBinding(pygame.K_KP8),
            "model_pitch_down": KeyBinding(pygame.K_KP2),
            "toggle_labels": KeyBinding(pygame.K_l),
            "toggle_perf_debug": KeyBinding(pygame.K_p),
            "toggle_mouse": KeyBinding(pygame.K_m),
            "save_stg": KeyBinding(pygame.K_s, pygame.KMOD_CTRL),
            "copy_object": KeyBinding(pygame.K_c, pygame.KMOD_CTRL),
            "paste_object": KeyBinding(pygame.K_v, pygame.KMOD_CTRL),
            "delete_object": KeyBinding(pygame.K_DELETE),
            "cycle_object_prev": KeyBinding(pygame.K_PAGEUP),
            "cycle_object_next": KeyBinding(pygame.K_PAGEDOWN),
        }

        self.camera_pos = [0.0, -120.0, 60.0]
        self.yaw = 90.0
        self.pitch = 0.0
        self.base_speed = 35.0
        self.speed_scale = 50.0
        self.mouse_sensitivity = 0.13
        self.near_plane = 0.5
        self.far_clip_distance = 0.0
        self.fov_deg = 68.0
        self.show_help = False
        self.wireframe_mode = False
        self.textured_mode = True
        self.show_debug = False
        self.show_model_labels = True
        self.camera_frame_distance_factor = 0.72
        self.camera_frame_height_factor = 0.18
        default_preview_w = max(260.0, min(430.0, self.size[0] * 0.30))
        default_preview_h = max(220.0, min(360.0, self.size[1] * 0.36))
        self.preview_panel_width_px = default_preview_w
        self.preview_panel_height_px = default_preview_h
        self.preview_panel_x_px = self.size[0] - default_preview_w - 20.0
        self.preview_panel_y_px = (self.size[1] - default_preview_h) * 0.5
        self.preview_panel_render_mode = "textured"
        self.preview_panel_field_labels = [
            "Panel width (px)",
            "Panel height (px)",
            "Panel X (px)",
            "Panel Y (px)",
        ]
        self.preview_panel_fields = ["", "", "", ""]
        self.preview_panel_field_index = 0
        self.camera_view_field_labels = ["Distance factor", "Height factor"]
        self.camera_view_fields = ["0.72", "0.18"]
        self.camera_view_field_index = 0
        self.camera_clipping_field_labels = ["Near clip (m)", "Far clip (m, 0=auto)"]
        self.camera_clipping_fields = ["0.50", "0"]
        self.camera_clipping_field_index = 0
        self.grid_field_labels = ["Grid size per side (m)", "Grid line spacing (m)", "Grid Z height (m)"]
        self.grid_fields = ["10000", "100", "0"]
        self.grid_field_index = 0
        self.grid_size_units = 10000.0
        self.grid_spacing_units = 100.0
        self.grid_z_height = 0.0
        self.model_angle_step_adjust_options = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 45.0]
        self.model_angle_step_index = 0
        self.model_angle_step_deg = self.model_angle_step_adjust_options[self.model_angle_step_index]
        
        self.object_nudge_step_m = 1.0
        self.object_nudge_camera_relative = True
        self.object_nudge_field_labels = ["Nudge distance (m)"]
        self.object_nudge_fields = ["1.0"]
        self.object_nudge_field_index = 0
        self.object_nudge_repeat_delay_s = 0.25
        self.object_nudge_repeat_interval_s = 0.06
        self.object_nudge_repeat_field_labels = ["Repeat delay (s)", "Repeat interval (s)"]
        self.object_nudge_repeat_fields = ["0.25", "0.06"]
        self.object_nudge_repeat_field_index = 0
        self.missing_material_color_rgb = (255, 0, 255)
        self.missing_material_color_field_labels = ["Missing color R", "Missing color G", "Missing color B"]
        self.missing_material_color_fields = ["255", "0", "255"]
        self.missing_material_color_field_index = 0
        self.object_nudge_hold_elapsed_s = 0.0
        self.object_nudge_hold_fired = False
        self.mouse_edit_drag_active = False
        self.mouse_edit_drag_started = False
        self.mouse_edit_drag_accum_px_x = 0.0
        self.mouse_edit_drag_accum_px_y = 0.0
        self.mouse_edit_drag_threshold_px = 4.0
        self.mouse_edit_pixels_per_move_step = 14.0
        self.mouse_edit_pixels_per_yaw_step = 18.0
        self.mouse_edit_pending_dx_m = 0.0
        self.mouse_edit_pending_dy_m = 0.0
        self.mouse_edit_pending_dz_m = 0.0
        self.mouse_edit_pending_dyaw_deg = 0.0
        self.fast_model_preview_pending = False
        self.fast_model_preview_deadline_ms = 0
        self._shadow_candidate_cache_key = None
        self._shadow_candidate_cache_indices: List[int] = []
        self.rotation_perf_stats: Dict[str, Dict[str, float]] = {}
        self.rotation_perf_last_report_ms = 0
        self.rotation_perf_report_interval_ms = 2500
        self.rotation_perf_debug_enabled = False
        self.is_windows = os.name == "nt"
        self.shadow_skip_rebuild_while_fast_preview = self.is_windows
        self.shadow_rebuild_interval_ms = 160 if self.is_windows else 0
        self.shadow_last_rebuild_ms = 0
        self.render_perf_sample_every_n_frames = 10 if self.is_windows else 0
        self.render_perf_frame_counter = 0
        self.last_browse_dir: str = ""
        self.help_text_file_path: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "onscreen_help_english.txt"))
        self.help_overlay_text_lines: List[str] = []
        self.menu_text_file_path: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "onsreen_ui_english.txt"))
        self.menu_text_map: Dict[str, str] = {}

        self.mesh: Optional[BTGMesh] = None
        self.stats: Optional[ValidationStats] = None
        self.current_file: Optional[str] = None
        config = load_viewer_config()

        cli_root = os.path.abspath(flightgear_root) if flightgear_root else ""
        config_root = os.path.abspath(config.get("flightgear_root", "")) if config.get("flightgear_root") else ""
        cli_data_root = resolve_fgdata_root(cli_root) if cli_root else ""
        config_data_root = resolve_fgdata_root(config_root) if config_root else ""
        if cli_data_root:
            resolved_root = cli_data_root
        elif config_data_root:
            resolved_root = config_data_root
        else:
            resolved_root = discover_flightgear_root()
        self.flightgear_root: str = resolved_root

        cli_material_map = os.path.abspath(material_map_path) if material_map_path else ""
        config_material_map = os.path.abspath(config.get("material_map_path", "")) if config.get("material_map_path") else ""
        if cli_material_map and os.path.isfile(cli_material_map):
            resolved_material_map = cli_material_map
        elif config_material_map and os.path.isfile(config_material_map):
            resolved_material_map = config_material_map
        else:
            resolved_material_map = default_material_map_path()
        self.material_map_path: str = resolved_material_map

        if isinstance(config.get("textured_mode"), bool):
            self.textured_mode = bool(config["textured_mode"])
        if isinstance(config.get("camera_frame_distance_factor"), (int, float)):
            self.camera_frame_distance_factor = float(config["camera_frame_distance_factor"])
        if isinstance(config.get("camera_frame_height_factor"), (int, float)):
            self.camera_frame_height_factor = float(config["camera_frame_height_factor"])
        if isinstance(config.get("preview_panel_width_px"), (int, float)):
            self.preview_panel_width_px = float(config["preview_panel_width_px"])
        if isinstance(config.get("preview_panel_height_px"), (int, float)):
            self.preview_panel_height_px = float(config["preview_panel_height_px"])
        if isinstance(config.get("preview_panel_x_px"), (int, float)):
            self.preview_panel_x_px = float(config["preview_panel_x_px"])
        if isinstance(config.get("preview_panel_y_px"), (int, float)):
            self.preview_panel_y_px = float(config["preview_panel_y_px"])
        if isinstance(config.get("preview_panel_render_mode"), str):
            mode = str(config["preview_panel_render_mode"]).strip().lower()
            if mode in {"textured", "shaded", "wireframe"}:
                self.preview_panel_render_mode = mode
        if isinstance(config.get("model_angle_step_index"), int):
            idx = int(config["model_angle_step_index"])
            self.model_angle_step_index = max(0, min(len(self.model_angle_step_adjust_options) - 1, idx))
        elif isinstance(config.get("model_angle_step_adjust_deg"), (int, float)):
            loaded_step = float(config["model_angle_step_adjust_deg"])
            self.model_angle_step_index = min(
                range(len(self.model_angle_step_adjust_options)),
                key=lambda i: abs(self.model_angle_step_adjust_options[i] - loaded_step),
            )
        self.model_angle_step_deg = self.model_angle_step_adjust_options[self.model_angle_step_index]
        if isinstance(config.get("object_nudge_step_m"), (int, float)):
            self.object_nudge_step_m = max(0.01, float(config["object_nudge_step_m"]))
        if isinstance(config.get("object_nudge_camera_relative"), bool):
            self.object_nudge_camera_relative = bool(config["object_nudge_camera_relative"])
        if isinstance(config.get("object_nudge_repeat_delay_s"), (int, float)):
            self.object_nudge_repeat_delay_s = max(0.0, float(config["object_nudge_repeat_delay_s"]))
        if isinstance(config.get("object_nudge_repeat_interval_s"), (int, float)):
            self.object_nudge_repeat_interval_s = max(0.01, float(config["object_nudge_repeat_interval_s"]))
        if isinstance(config.get("grid_size_units"), (int, float)):
            self.grid_size_units = max(1.0, float(config["grid_size_units"]))
        if isinstance(config.get("grid_spacing_units"), (int, float)):
            self.grid_spacing_units = max(0.01, float(config["grid_spacing_units"]))
        if isinstance(config.get("grid_z_height"), (int, float)):
            self.grid_z_height = float(config["grid_z_height"])
        loaded_camera_pos = config.get("camera_pos_enu")
        if (
            isinstance(loaded_camera_pos, list)
            and len(loaded_camera_pos) == 3
            and all(isinstance(v, (int, float)) for v in loaded_camera_pos)
        ):
            self.camera_pos = [float(loaded_camera_pos[0]), float(loaded_camera_pos[1]), float(loaded_camera_pos[2])]
        if isinstance(config.get("camera_yaw_deg"), (int, float)):
            self.yaw = float(config["camera_yaw_deg"])
        if isinstance(config.get("camera_pitch_deg"), (int, float)):
            self.pitch = float(config["camera_pitch_deg"])
        if isinstance(config.get("near_clip_m"), (int, float)):
            self.near_plane = max(0.01, float(config["near_clip_m"]))
        if isinstance(config.get("far_clip_m"), (int, float)):
            self.far_clip_distance = max(0.0, float(config["far_clip_m"]))
        missing_rgb = config.get("missing_material_color_rgb")
        if (
            isinstance(missing_rgb, list)
            and len(missing_rgb) == 3
            and all(isinstance(v, (int, float)) for v in missing_rgb)
        ):
            self.missing_material_color_rgb = tuple(max(0, min(255, int(v))) for v in missing_rgb)
        custom_paths = config.get("custom_scenery_paths")
        if isinstance(custom_paths, list):
            self.custom_scenery_paths = [path for path in custom_paths if isinstance(path, str) and path.strip()]
        else:
            self.custom_scenery_paths = []
        if isinstance(config.get("last_browse_dir"), str) and config.get("last_browse_dir"):
            self.last_browse_dir = os.path.abspath(str(config.get("last_browse_dir")))
        elif self.current_file:
            self.last_browse_dir = os.path.dirname(self.current_file)
        else:
            self.last_browse_dir = os.getcwd()
        if isinstance(config.get("last_add_object_category"), str):
            self.last_add_object_category = str(config.get("last_add_object_category") or "")
        loaded_help_text_path = config.get("help_text_file_path")
        if isinstance(loaded_help_text_path, str) and loaded_help_text_path.strip():
            self.help_text_file_path = os.path.abspath(loaded_help_text_path.strip())
        loaded_menu_text_path = config.get("menu_text_file_path")
        if isinstance(loaded_menu_text_path, str) and loaded_menu_text_path.strip():
            self.menu_text_file_path = os.path.abspath(loaded_menu_text_path.strip())

        if not self._load_help_text_file(self.help_text_file_path, persist=False, silent=True):
            fallback_help_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "onscreen_help_english.txt"))
            if self.help_text_file_path != fallback_help_path:
                self.help_text_file_path = fallback_help_path
                self._load_help_text_file(self.help_text_file_path, persist=False, silent=True)
            if not self.help_overlay_text_lines:
                self.help_overlay_text_lines = self._default_help_overlay_lines()

        if not self._load_menu_text_file(self.menu_text_file_path, persist=False, silent=True):
            fallback_menu_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "onsreen_ui_english.txt"))
            if self.menu_text_file_path != fallback_menu_path:
                self.menu_text_file_path = fallback_menu_path
                self._load_menu_text_file(self.menu_text_file_path, persist=False, silent=True)

        try:
            pygame.display.set_caption(self._menu_t("app.window_title", "FlightGear Andromeda STG Editor"))
        except Exception:
            pass

        self.binding_labels = {
            "forward": self._menu_t("binding.camera_plus_z", "camera +Z"),
            "backward": self._menu_t("binding.camera_minus_z", "camera -Z"),
            "left": self._menu_t("binding.left", "left"),
            "right": self._menu_t("binding.right", "right"),
            "up": self._menu_t("binding.up", "up"),
            "down": self._menu_t("binding.down", "down"),
            "speed_down": self._menu_t("binding.speed_down", "speed down"),
            "speed_up": self._menu_t("binding.speed_up", "speed up"),
            "model_yaw_down": self._menu_t("binding.model_yaw_down", "model yaw -"),
            "model_yaw_up": self._menu_t("binding.model_yaw_up", "model yaw +"),
            "model_roll_down": self._menu_t("binding.model_roll_down", "model roll -"),
            "model_roll_up": self._menu_t("binding.model_roll_up", "model roll +"),
            "model_pitch_up": self._menu_t("binding.model_pitch_up", "model pitch +"),
            "model_pitch_down": self._menu_t("binding.model_pitch_down", "model pitch -"),
            "toggle_labels": self._menu_t("binding.toggle_labels", "toggle labels"),
            "toggle_perf_debug": self._menu_t("binding.toggle_perf_debug", "toggle perf debug"),
            "toggle_mouse": self._menu_t("binding.toggle_mouse", "toggle mouse"),
            "save_stg": self._menu_t("binding.save_stg", "save STG"),
            "copy_object": self._menu_t("binding.copy_object", "copy selected object"),
            "paste_object": self._menu_t("binding.paste_object", "paste copied object"),
            "delete_object": self._menu_t("binding.delete_object", "delete selected object"),
            "cycle_object_prev": self._menu_t("binding.cycle_object_prev", "prev catalog object"),
            "cycle_object_next": self._menu_t("binding.cycle_object_next", "next catalog object"),
        }

        self.preview_panel_field_labels = [
            self._menu_t("field.preview_panel_width_px", "Panel width (px)"),
            self._menu_t("field.preview_panel_height_px", "Panel height (px)"),
            self._menu_t("field.preview_panel_x_px", "Panel X (px)"),
            self._menu_t("field.preview_panel_y_px", "Panel Y (px)"),
        ]
        self.camera_view_field_labels = [
            self._menu_t("field.camera_distance_factor", "Distance factor"),
            self._menu_t("field.camera_height_factor", "Height factor"),
        ]
        self.camera_clipping_field_labels = [
            self._menu_t("field.camera_near_clip", "Near clip (m)"),
            self._menu_t("field.camera_far_clip", "Far clip (m, 0=auto)"),
        ]
        self.grid_field_labels = [
            self._menu_t("field.grid_size", "Grid size per side (m)"),
            self._menu_t("field.grid_spacing", "Grid line spacing (m)"),
            self._menu_t("field.grid_z", "Grid Z height (m)"),
        ]
        self.object_nudge_field_labels = [
            self._menu_t("field.object_nudge_distance", "Nudge distance (m)")
        ]
        self.object_nudge_repeat_field_labels = [
            self._menu_t("field.object_nudge_repeat_delay", "Repeat delay (s)"),
            self._menu_t("field.object_nudge_repeat_interval", "Repeat interval (s)"),
        ]
        self.missing_material_color_field_labels = [
            self._menu_t("field.missing_color_r", "Missing color R"),
            self._menu_t("field.missing_color_g", "Missing color G"),
            self._menu_t("field.missing_color_b", "Missing color B"),
        ]

        self._sync_camera_view_fields_from_settings()
        self._sync_camera_clipping_fields_from_settings()
        self._sync_preview_panel_fields_from_settings()
        self._sync_object_nudge_fields_from_settings()
        self._sync_object_nudge_repeat_fields_from_settings()
        self._sync_missing_material_color_fields_from_settings()
        self._sync_grid_fields_from_settings()

        self.object_catalog_show_btg: bool = False
        self.object_catalog: List[ObjectCatalogEntry] = []
        self.object_catalog_last_error = ""
        self._refresh_object_catalog()

        self.mesh_bounds: Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = None
        self.face_data: List[Tuple[Tuple[int, int, int], Tuple[int, int, int], Tuple[float, float, float]]] = []
        self.texture_id_by_path: Dict[str, int] = {}
        self.texture_has_alpha_by_id: Dict[int, bool] = {}
        self.texture_alpha_by_path: Dict[str, bool] = {}
        self.shared_model_mesh_cache: Dict[str, Optional[BTGMesh]] = {}
        self.scene_base_mesh: Optional[BTGMesh] = None
        self.scene_static_stg_meshes: List[BTGMesh] = []
        self.scene_static_merged_mesh: Optional[BTGMesh] = None
        self.static_surface_cell_size_m: float = 96.0 if self.is_windows else 160.0
        self.static_surface_triangles: List[Tuple[float, float, float, float, float, float, float, float, float, float, float, float, float, float]] = []
        self.static_surface_grid_index: Dict[Tuple[int, int], List[int]] = {}
        self.static_surface_grid_overflow: List[int] = []
        self.scene_model_instances: List[STGModelInstance] = []
        self.loaded_stg_entry_source_path: str = ""
        self.loaded_stg_model_entry_indices: Set[int] = set()
        self.crosshair_hover_model_index: Optional[int] = None
        self.selected_model_instance_index: Optional[int] = None
        self.clipboard_instance: Optional[STGModelInstance] = None
        self.gl_textured_batches: List[Tuple[int, ctypes.Array, ctypes.Array, ctypes.Array, int]] = []
        self.gl_textured_static_batches: List[Tuple[int, ctypes.Array, ctypes.Array, ctypes.Array, int]] = []
        self.gl_textured_model_batches: List[Tuple[int, ctypes.Array, ctypes.Array, ctypes.Array, int]] = []
        self.gl_textured_model_batches_by_instance: Dict[int, List[Tuple[int, ctypes.Array, ctypes.Array, ctypes.Array, int]]] = {}
        self.last_debug: Dict[str, object] = {
            "faces_with_material": 0,
            "faces_with_uv": 0,
            "resolved_faces": 0,
            "unique_materials": 0,
            "resolved_materials": 0,
            "missing_materials": [],
            "textures_loaded": 0,
        }
        self.last_uv_debug: Dict[str, object] = {
            "materials_analyzed": 0,
            "faces_analyzed": 0,
            "top_dense": [],
            "top_sparse": [],
        }
        self.last_stg_debug: Dict[str, object] = {
            "stg_found": False,
            "entries": 0,
            "btg_objects_loaded": 0,
            "model_objects_loaded": 0,
            "proxy_objects_loaded": 0,
            "skipped": 0,
        }
        self.gl_vertex_buffer = None
        self.gl_normal_buffer = None
        self.gl_color_buffer = None
        self.gl_index_buffer = None
        self.gl_index_count = 0
        self.gl_index_buffer_base = None
        self.gl_index_count_base = 0
        self.gl_texture_anisotropy_supported = False
        self.gl_texture_max_anisotropy = 1.0
        self.gl_texture_anisotropy = 1.0

        self.status_text = self._menu_tf(
            "status.renderer_no_scene",
            "Renderer: {renderer} | No scene loaded",
            renderer=self.renderer_name,
        )

        if self.opengl_enabled:
            self._init_gl()

        if initial_file:
            if initial_file.lower().endswith(".stg"):
                self.load_stg_file(initial_file)
            else:
                self.load_file(initial_file)

        try:
            self._persist_viewer_config()
        except Exception:
            pass

    def shutdown(self) -> None:
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        if self.stats:
            self.stats.print_summary()
        else:
            print(self._menu_t("status.no_btg_loaded", "No BTG was loaded; no validation statistics to print."))
        pygame.quit()

    def run(self) -> None:
        clock = pygame.time.Clock()
        while self.running:
            dt = min(0.05, clock.tick(120) / 1000.0)
            self._handle_events(dt)
            self._update_movement(dt)
            self.render()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and display FlightGear terrain scenes")
    parser.add_argument("scene", nargs="?", help="Path to .stg, .btg, or .btg.gz")
    parser.add_argument(
        "--flightgear",
        default="",
        help="FlightGear root (or texture root) used for terrain texture lookup",
    )
    parser.add_argument(
        "--material-map",
        default="",
        help="Path to JSON file with material-to-texture overrides",
    )
    renderer_group = parser.add_mutually_exclusive_group()
    renderer_group.add_argument("--software", action="store_true", help="Force software renderer")
    renderer_group.add_argument("--opengl", action="store_true", help="Force OpenGL renderer")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    renderer_preference = "auto"
    if args.software:
        renderer_preference = "software"
    elif args.opengl:
        renderer_preference = "opengl"

    app = BTGDisplayApp(
        initial_file=args.scene,
        renderer_preference=renderer_preference,
        flightgear_root=args.flightgear,
        material_map_path=args.material_map,
    )
    app.run()
    app.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
