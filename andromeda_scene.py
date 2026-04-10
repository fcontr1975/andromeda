#!/usr/bin/env python3
"""Scene-management mixin for BTG display app.

Holds and manages the live scene: loading BTG/STG files, composing the
scene mesh, persisting viewer config and rebuilding the scene from cache.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

import pygame

from andromeda_backend import (
    BTGMesh,
    ObjectCatalogEntry,
    STGModelInstance,
    ValidationStats,
    build_object_catalog,
    compose_scene_mesh,
    ecef_to_geodetic,
    ecef_to_enu_matrix,
    load_associated_stg_objects,
    load_stg_objects_from_entries,
    merge_meshes,
    parse_stg_file,
    resolve_stg_object_path,
    rotate3_inv,
    save_viewer_config,
    stg_associated_path,
    validate_and_load_btg,
)

if TYPE_CHECKING:
    from andromeda import BTGDisplayApp


class SceneMixin:
    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def set_status(self: "BTGDisplayApp", text: str) -> None:
        self.status_text = text

    # ------------------------------------------------------------------
    # GL texture management
    # ------------------------------------------------------------------

    def _clear_gl_textures(self: "BTGDisplayApp") -> None:
        try:
            self._purge_gl_text_cache()
        except Exception:
            pass
        if not self.texture_id_by_path:
            self.texture_has_alpha_by_id.clear()
            self.texture_alpha_by_path.clear()
            return
        if self.opengl_enabled and self.GL is not None:
            ids = list(self.texture_id_by_path.values())
            if ids:
                self.GL.glDeleteTextures(ids)
        self.texture_id_by_path.clear()
        self.texture_has_alpha_by_id.clear()
        self.texture_alpha_by_path.clear()

    # ------------------------------------------------------------------
    # Persistent config
    # ------------------------------------------------------------------

    def _persist_viewer_config(self: "BTGDisplayApp") -> None:
        save_viewer_config(
            self.flightgear_root,
            self.material_map_path,
            self.textured_mode,
            self.camera_frame_distance_factor,
            self.camera_frame_height_factor,
            self.preview_panel_width_px,
            self.preview_panel_height_px,
            self.preview_panel_x_px,
            self.preview_panel_y_px,
            self.preview_panel_render_mode,
            self.model_angle_step_index,
            self.object_nudge_step_m,
            self.object_nudge_camera_relative,
            self.object_nudge_repeat_delay_s,
            self.object_nudge_repeat_interval_s,
            self.missing_material_color_rgb,
            self.custom_scenery_paths,
            self.grid_size_units,
            self.grid_spacing_units,
            self.grid_z_height,
            (self.camera_pos[0], self.camera_pos[1], self.camera_pos[2]),
            self.yaw,
            self.pitch,
            self.near_plane,
            self.far_clip_distance,
            self.last_browse_dir,
            self.last_add_object_category,
            self.help_text_file_path,
            self.menu_text_file_path,
        )

    # ------------------------------------------------------------------
    # Object catalog
    # ------------------------------------------------------------------

    def _refresh_object_catalog(self: "BTGDisplayApp") -> None:
        try:
            self.object_catalog = build_object_catalog(
                self.flightgear_root,
                self.custom_scenery_paths,
                include_btg=self.object_catalog_show_btg,
            )
            self.object_catalog_last_error = ""
        except Exception as exc:
            self.object_catalog = []
            self.object_catalog_last_error = str(exc)

    def _build_add_object_categories(self: "BTGDisplayApp") -> None:
        """Re-group self.object_catalog by category for the Add Object browser."""
        by_cat: Dict[str, List[ObjectCatalogEntry]] = {}
        for entry in self.object_catalog:
            by_cat.setdefault(entry.category, []).append(entry)
        self.add_object_by_category = dict(sorted(by_cat.items()))
        self.add_object_category_list = list(self.add_object_by_category.keys())

    def _scene_is_loaded(self: "BTGDisplayApp") -> bool:
        return self.mesh is not None and bool(self.mesh.vertices)

    def _clear_loaded_scene(self: "BTGDisplayApp") -> None:
        self._clear_gl_textures()
        self._clear_add_object_preview()
        self.mesh = None
        self.stats = None
        self.current_file = None
        self.scene_base_mesh = None
        self.scene_static_stg_meshes = []
        self.scene_static_merged_mesh = None
        self.scene_model_instances = []
        self.loaded_stg_entry_source_path = ""
        self.loaded_stg_model_entry_indices = set()
        self.crosshair_hover_model_index = None
        self.selected_model_instance_index = None
        self.clipboard_instance = None
        self.mouse_edit_drag_active = False
        self.mouse_edit_drag_started = False
        self.mouse_edit_drag_accum_px_x = 0.0
        self.mouse_edit_drag_accum_px_y = 0.0
        self.fast_model_preview_pending = False
        self.fast_model_preview_deadline_ms = 0
        self.mesh_bounds = None
        self.face_data = []
        self.gl_vertex_buffer = None
        self.gl_normal_buffer = None
        self.gl_color_buffer = None
        self.gl_index_buffer = None
        self.gl_index_count = 0
        self.gl_index_buffer_base = None
        self.gl_index_count_base = 0
        self.gl_textured_batches = []
        self.gl_textured_static_batches = []
        self.gl_textured_model_batches = []
        self.gl_textured_model_batches_by_instance = {}
        self.last_stg_debug = {
            "stg_found": False,
            "entries": 0,
            "btg_objects_loaded": 0,
            "model_objects_loaded": 0,
            "proxy_objects_loaded": 0,
            "skipped": 0,
        }
        self._set_status_t(
            "status.renderer_no_scene",
            "Renderer: {renderer} | No scene loaded",
            renderer=self.renderer_name,
        )

    def _prepare_scene_switch(self: "BTGDisplayApp", target_path: str, prompt_on_switch: bool = True) -> bool:
        if not self._scene_is_loaded():
            return True

        target_abs = os.path.abspath(target_path)
        current_abs = os.path.abspath(self.current_file) if self.current_file else ""
        switching_to_different_scene = bool(current_abs) and target_abs != current_abs

        if not switching_to_different_scene:
            return True

        if prompt_on_switch:
            action = self._ask_save_before_scene_switch(self.current_file, target_path)
            if action == "cancel":
                self._set_status_t("status.load_cancelled", "Load cancelled")
                return False
            if action == "save" and not self.save_stg_file():
                self._set_status_t(
                    "status.load_cancelled_unsaved",
                    "Load cancelled: current scene was not saved",
                )
                return False

        self._clear_loaded_scene()
        return True

    # ------------------------------------------------------------------
    # Loading / saving scene files
    # ------------------------------------------------------------------

    def load_file(self: "BTGDisplayApp", path: str, prompt_on_switch: bool = True) -> None:
        if not self._prepare_scene_switch(path, prompt_on_switch=prompt_on_switch):
            return
        try:
            mesh, stats = validate_and_load_btg(path)
        except Exception as exc:
            self._set_status_t("status.load_failed_fmt", "Load failed: {error}", error=exc)
            print(f"Failed to load {path}: {exc}")
            return

        stg_static_meshes, stg_model_instances, stg_debug = load_associated_stg_objects(
            path,
            mesh,
            self.flightgear_root,
            self.shared_model_mesh_cache,
        )
        self.last_stg_debug = stg_debug

        self.scene_base_mesh = mesh
        self.scene_static_stg_meshes = stg_static_meshes
        self.scene_static_merged_mesh = merge_meshes(mesh, self.scene_static_stg_meshes)
        self.scene_model_instances = stg_model_instances
        associated_stg_path = stg_associated_path(path)
        if os.path.isfile(associated_stg_path):
            self.loaded_stg_entry_source_path = os.path.abspath(associated_stg_path)
            self.loaded_stg_model_entry_indices = {
                int(inst.stg_entry_index)
                for inst in stg_model_instances
                if inst.stg_entry_index is not None
            }
        else:
            self.loaded_stg_entry_source_path = ""
            self.loaded_stg_model_entry_indices = set()
        self._clear_add_object_preview()
        self.selected_model_instance_index = None
        self.crosshair_hover_model_index = None

        scene_mesh = compose_scene_mesh(
            self.scene_static_merged_mesh,
            [],
            self.scene_model_instances,
            0.0,
            0.0,
            0.0,
        )

        self.mesh = scene_mesh
        self.stats = stats
        self.current_file = path
        self.mesh_bounds = self._mesh_bounds(scene_mesh)
        self._build_face_data(scene_mesh)
        self._set_status_t(
            "status.loaded_scene_summary_fmt",
            "Loaded {name} | vtx={vtx} tri={tri} | STG btg={btg} model={model} proxy={proxy}",
            name=os.path.basename(path),
            vtx=len(scene_mesh.vertices),
            tri=len(scene_mesh.faces),
            btg=stg_debug["btg_objects_loaded"],
            model=stg_debug["model_objects_loaded"],
            proxy=stg_debug["proxy_objects_loaded"],
        )
        print(f"Loaded BTG: {path}")
        print(f"Warnings: {len(stats.warnings)} | Errors: {len(stats.errors)}")

    def load_stg_file(self: "BTGDisplayApp", stg_path: str, prompt_on_switch: bool = True) -> None:
        if not self._prepare_scene_switch(stg_path, prompt_on_switch=prompt_on_switch):
            return
        entries = parse_stg_file(stg_path)
        if not entries:
            self._set_status_t("status.load_failed_no_stg_entries", "Load failed: STG has no object entries")
            return

        base_entry = None
        for entry in entries:
            ext = os.path.splitext(entry.object_path.lower())[1]
            if entry.directive in {"OBJECT_BASE", "OBJECT"} and ext in {".btg", ".gz"}:
                base_entry = entry
                break
        if base_entry is None:
            for entry in entries:
                ext = os.path.splitext(entry.object_path.lower())[1]
                if ext in {".btg", ".gz"}:
                    base_entry = entry
                    break

        if base_entry is None:
            self._set_status_t(
                "status.load_failed_no_btg_reference",
                "Load failed: STG has no BTG base/object tile reference",
            )
            return

        base_btg_path = resolve_stg_object_path(base_entry.object_path, stg_path, self.flightgear_root)
        if not base_btg_path:
            self._set_status_t(
                "status.load_failed_resolve_base_fmt",
                "Load failed: could not resolve STG base BTG '{base_path}'",
                base_path=base_entry.object_path,
            )
            return

        try:
            mesh, stats = validate_and_load_btg(base_btg_path)
        except Exception as exc:
            self._set_status_t("status.load_failed_fmt", "Load failed: {error}", error=exc)
            print(f"Failed to load STG base BTG {base_btg_path}: {exc}")
            return

        stg_static_meshes, stg_model_instances, stg_debug = load_stg_objects_from_entries(
            stg_path,
            mesh,
            entries,
            base_btg_path,
            self.flightgear_root,
            self.shared_model_mesh_cache,
        )
        self.last_stg_debug = stg_debug

        self.scene_base_mesh = mesh
        self.scene_static_stg_meshes = stg_static_meshes
        self.scene_static_merged_mesh = merge_meshes(mesh, self.scene_static_stg_meshes)
        self.scene_model_instances = stg_model_instances
        self.loaded_stg_entry_source_path = os.path.abspath(stg_path)
        self.loaded_stg_model_entry_indices = {
            int(inst.stg_entry_index)
            for inst in stg_model_instances
            if inst.stg_entry_index is not None
        }
        self._clear_add_object_preview()
        self.selected_model_instance_index = None
        self.crosshair_hover_model_index = None

        scene_mesh = compose_scene_mesh(
            self.scene_static_merged_mesh,
            [],
            self.scene_model_instances,
            0.0,
            0.0,
            0.0,
        )

        self.mesh = scene_mesh
        self.stats = stats
        self.current_file = stg_path
        self.mesh_bounds = self._mesh_bounds(scene_mesh)
        self._build_face_data(scene_mesh)
        self._set_status_t(
            "status.loaded_scene_summary_fmt",
            "Loaded {name} | vtx={vtx} tri={tri} | STG btg={btg} model={model} proxy={proxy}",
            name=os.path.basename(stg_path),
            vtx=len(scene_mesh.vertices),
            tri=len(scene_mesh.faces),
            btg=stg_debug["btg_objects_loaded"],
            model=stg_debug["model_objects_loaded"],
            proxy=stg_debug["proxy_objects_loaded"],
        )
        print(f"Loaded STG: {stg_path} (base BTG: {base_btg_path})")
        print(f"Warnings: {len(stats.warnings)} | Errors: {len(stats.errors)}")

    def _resolve_active_stg_path(self: "BTGDisplayApp", require_exists: bool = True) -> str:
        stg_path = ""
        if self.current_file and self.current_file.lower().endswith(".stg"):
            stg_path = self.current_file
        elif self.current_file:
            candidate = stg_associated_path(self.current_file)
            if candidate:
                stg_path = candidate

        if not stg_path:
            return ""
        if require_exists and not os.path.isfile(stg_path):
            return ""
        return os.path.abspath(stg_path)

    def _save_stg_to_path(
        self: "BTGDisplayApp",
        target_stg_path: str,
        source_stg_path: str,
        update_current_file: bool,
    ) -> bool:
        if self.scene_base_mesh is None or self.scene_base_mesh.center_ecef == (0.0, 0.0, 0.0):
            self._set_status_t("status.save_failed_no_base_scene", "Save failed: no base scene loaded")
            return False

        target_path = os.path.abspath(target_stg_path)
        source_path = os.path.abspath(source_stg_path)
        if not os.path.isfile(source_path):
            self._set_status_t("status.save_failed_no_scene_stg", "Save failed: no STG file associated with current scene")
            return False

        base_center = self.scene_base_mesh.center_ecef
        base_rot = ecef_to_enu_matrix(*base_center)

        try:
            with open(source_path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except Exception as exc:
            self._set_status_t("status.save_failed_fmt", "Save failed: {error}", error=exc)
            return False

        def _format_instance_line(instance: STGModelInstance) -> str:
            enu = (
                instance.origin_enu[0] + instance.offset_x_m,
                instance.origin_enu[1] + instance.offset_y_m,
                instance.origin_enu[2] + instance.offset_z_m,
            )
            rel_ecef = rotate3_inv(enu, base_rot)
            ecef = (
                base_center[0] + rel_ecef[0],
                base_center[1] + rel_ecef[1],
                base_center[2] + rel_ecef[2],
            )
            lon_deg, lat_deg, elev_m = ecef_to_geodetic(*ecef)
            heading = instance.heading_deg + instance.offset_yaw_deg
            pitch = instance.pitch_deg + instance.offset_pitch_deg
            roll = instance.roll_deg + instance.offset_roll_deg
            directive = (instance.stg_directive or "OBJECT_SHARED").upper()
            if not directive.startswith("OBJECT"):
                directive = "OBJECT_SHARED"
            return (
                f"{directive} {instance.source_path} {lon_deg:.8f} {lat_deg:.8f} "
                f"{elev_m:.3f} {heading:.2f} {pitch:.2f} {roll:.2f}\n"
            )

        replacement_by_entry_index: Dict[int, str] = {}
        append_lines: List[str] = []
        for instance in self.scene_model_instances:
            if not instance.source_path:
                continue
            line = _format_instance_line(instance)
            if instance.stg_entry_index is not None and instance.stg_entry_index >= 0:
                replacement_by_entry_index[int(instance.stg_entry_index)] = line
            else:
                append_lines.append(line)

        loaded_entry_indices: Set[int] = set()
        if source_path == self.loaded_stg_entry_source_path:
            loaded_entry_indices = set(self.loaded_stg_model_entry_indices)

        rewritten: List[str] = []
        entry_index = 0
        replaced_count = 0
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                rewritten.append(line)
                continue

            parts = stripped.split()
            if len(parts) < 2:
                rewritten.append(line)
                continue

            directive = parts[0].upper()
            if not directive.startswith("OBJECT"):
                rewritten.append(line)
                continue

            object_path = parts[1]
            ext = os.path.splitext(object_path.lower())[1]
            if ext in {".btg", ".gz"}:
                rewritten.append(line)
                entry_index += 1
                continue

            replacement = replacement_by_entry_index.pop(entry_index, None)
            if replacement is not None:
                rewritten.append(replacement)
                replaced_count += 1
            elif entry_index in loaded_entry_indices:
                # This editable line was deleted from scene_model_instances; drop it.
                pass
            else:
                # Keep unresolved/non-editable OBJECT lines exactly as they were.
                rewritten.append(line)
            entry_index += 1

        for idx in sorted(replacement_by_entry_index):
            rewritten.append(replacement_by_entry_index[idx])
        rewritten.extend(append_lines)

        written_models = replaced_count + len(replacement_by_entry_index) + len(append_lines)

        try:
            parent_dir = os.path.dirname(target_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as handle:
                handle.writelines(rewritten)
        except Exception as exc:
            self._set_status_t("status.save_failed_fmt", "Save failed: {error}", error=exc)
            return False

        if update_current_file:
            self.current_file = target_path
            self.loaded_stg_entry_source_path = target_path
            self.loaded_stg_model_entry_indices = {
                int(inst.stg_entry_index)
                for inst in self.scene_model_instances
                if inst.stg_entry_index is not None and int(inst.stg_entry_index) >= 0
            }

        try:
            self._remember_browse_path(target_path)
        except Exception:
            pass

        target_name = os.path.basename(target_path)
        self._set_status_t(
            "status.saved_stg_fmt",
            "Saved STG: {filename} | wrote {count} scene objects",
            filename=target_name,
            count=written_models,
        )
        self._open_save_confirm_dialog(
            self._menu_t("dialog.stg_saved_title", "STG Saved"),
            self._menu_tf(
                "dialog.stg_saved_message_fmt",
                "Saved {filename} with {count} scene objects.",
                filename=target_name,
                count=written_models,
            ),
        )
        return True

    def save_stg_file(self: "BTGDisplayApp") -> bool:
        source_path = self._resolve_active_stg_path(require_exists=True)
        if not source_path:
            self._set_status_t("status.save_failed_no_scene_stg", "Save failed: no STG file associated with current scene")
            return False
        return self._save_stg_to_path(source_path, source_path, update_current_file=False)

    def save_stg_as(self: "BTGDisplayApp", target_stg_path: str) -> bool:
        source_path = self._resolve_active_stg_path(require_exists=True)
        if not source_path:
            self._set_status_t("status.save_failed_no_scene_stg", "Save failed: no STG file associated with current scene")
            return False
        if not target_stg_path:
            self._set_status_t("status.save_failed_no_target", "Save failed: no target path provided")
            return False
        return self._save_stg_to_path(target_stg_path, source_path, update_current_file=True)

    # ------------------------------------------------------------------
    # Scene rebuild helpers
    # ------------------------------------------------------------------

    def _reload_current_scene(self: "BTGDisplayApp") -> None:
        if not self.current_file:
            return
        path = self.current_file
        if path.lower().endswith(".stg"):
            self.load_stg_file(path, prompt_on_switch=False)
        else:
            self.load_file(path, prompt_on_switch=False)

    def _rebuild_scene_from_cache(
        self: "BTGDisplayApp",
        keep_camera: bool = True,
        build_textures: bool = True,
        perf_tag: str = "",
    ) -> bool:
        if self.scene_static_merged_mesh is None:
            return False

        t0 = time.perf_counter()
        scene_mesh = compose_scene_mesh(
            self.scene_static_merged_mesh,
            [],
            self.scene_model_instances,
            0.0,
            0.0,
            0.0,
        )
        t1 = time.perf_counter()

        old_camera = list(self.camera_pos) if keep_camera else None
        old_yaw = self.yaw if keep_camera else 0.0
        old_pitch = self.pitch if keep_camera else 0.0

        self.mesh = scene_mesh
        self.mesh_bounds = self._mesh_bounds(scene_mesh)
        self._build_face_data(scene_mesh, build_textures=build_textures)
        t2 = time.perf_counter()
        if not keep_camera:
            self._reset_camera_to_mesh(scene_mesh)
        t3 = time.perf_counter()

        if keep_camera and old_camera is not None:
            self.camera_pos = old_camera
            self.yaw = old_yaw
            self.pitch = old_pitch

        if perf_tag:
            self._record_rotation_perf(f"{perf_tag}.compose", (t1 - t0) * 1000.0)
            self._record_rotation_perf(f"{perf_tag}.build_face_data", (t2 - t1) * 1000.0)
            if not keep_camera:
                self._record_rotation_perf(f"{perf_tag}.reset_camera", (t3 - t2) * 1000.0)
            self._record_rotation_perf(f"{perf_tag}.total", (t3 - t0) * 1000.0)
            self._maybe_report_rotation_perf()
        return True
