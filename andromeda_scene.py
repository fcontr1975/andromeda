#!/usr/bin/env python3
"""Scene-management mixin for BTG display app.

Holds and manages the live scene: loading BTG/STG files, composing the
scene mesh, persisting viewer config and rebuilding the scene from cache.
"""

from __future__ import annotations

import math
import os
import shlex
import shutil
import time
import xml.etree.ElementTree as ET
import zipfile
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
    resolve_fgdata_root,
    rotate3_inv,
    save_viewer_config,
    stg_associated_path,
    validate_and_load_btg,
)

if TYPE_CHECKING:
    from andromeda import BTGDisplayApp


class SceneMixin:
    def _rebuild_static_surface_index(self: "BTGDisplayApp") -> None:
        mesh = self.scene_static_merged_mesh
        self.static_surface_triangles = []
        self.static_surface_grid_index = {}
        self.static_surface_grid_overflow = []

        if mesh is None or not mesh.vertices or not mesh.faces:
            return

        default_cell_size = max(1.0, float(getattr(self, "static_surface_cell_size_m", 128.0)))
        xs = [v[0] for v in mesh.vertices]
        ys = [v[1] for v in mesh.vertices]
        span_x = max(1.0, max(xs) - min(xs))
        span_y = max(1.0, max(ys) - min(ys))
        area_xy = max(1.0, span_x * span_y)
        tri_count_est = max(1, len(mesh.faces))
        tri_density = tri_count_est / area_xy
        target_tris_per_cell = 22.0 if getattr(self, "is_windows", False) else 36.0
        if tri_density > 1e-12:
            adaptive_cell = math.sqrt(target_tris_per_cell / tri_density)
        else:
            adaptive_cell = default_cell_size
        min_cell = 28.0 if getattr(self, "is_windows", False) else 48.0
        max_cell = 192.0 if getattr(self, "is_windows", False) else 320.0
        cell_size = max(min_cell, min(max_cell, adaptive_cell))
        self.static_surface_cell_size_m = float(cell_size)
        inv_cell = 1.0 / cell_size
        max_cells_per_axis = 72 if getattr(self, "is_windows", False) else 96

        verts = mesh.vertices
        eps = 1e-9
        t0 = time.perf_counter()
        inserted = 0

        for ia, ib, ic in mesh.faces:
            ax, ay, az = verts[ia]
            bx, by, bz = verts[ib]
            cx, cy, cz = verts[ic]

            min_x = min(ax, bx, cx)
            max_x = max(ax, bx, cx)
            min_y = min(ay, by, cy)
            max_y = max(ay, by, cy)
            den = ((by - cy) * (ax - cx)) + ((cx - bx) * (ay - cy))
            if abs(den) <= eps:
                continue

            tri_index = len(self.static_surface_triangles)
            self.static_surface_triangles.append(
                (
                    ax,
                    ay,
                    az,
                    bx,
                    by,
                    bz,
                    cx,
                    cy,
                    cz,
                    min_x,
                    max_x,
                    min_y,
                    max_y,
                    den,
                )
            )

            ix0 = int(min_x * inv_cell)
            ix1 = int(max_x * inv_cell)
            iy0 = int(min_y * inv_cell)
            iy1 = int(max_y * inv_cell)
            span_x = ix1 - ix0 + 1
            span_y = iy1 - iy0 + 1
            if span_x > max_cells_per_axis or span_y > max_cells_per_axis:
                self.static_surface_grid_overflow.append(tri_index)
                continue

            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    self.static_surface_grid_index.setdefault((ix, iy), []).append(tri_index)
                    inserted += 1

        if self.rotation_perf_debug_enabled:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._record_rotation_perf("surface_index.build", elapsed_ms)
            self._record_rotation_perf("surface_index.triangles", float(len(self.static_surface_triangles)))
            self._record_rotation_perf("surface_index.cells", float(len(self.static_surface_grid_index)))
            self._record_rotation_perf("surface_index.inserted", float(inserted))
            self._record_rotation_perf("surface_index.cell_size", float(cell_size))

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
        try:
            self._clear_selection_shadow()
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
        self.static_surface_triangles = []
        self.static_surface_grid_index = {}
        self.static_surface_grid_overflow = []
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
        self._rebuild_static_surface_index()
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
        self._rebuild_static_surface_index()
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

        def _object_position_signature(stg_line: str) -> Optional[Tuple[str, str, str, str, str]]:
            stripped = stg_line.strip()
            if not stripped or stripped.startswith("#"):
                return None
            parts = stripped.split()
            if len(parts) < 5:
                return None

            directive = parts[0].upper()
            if not directive.startswith("OBJECT"):
                return None

            object_path = parts[1]
            ext = os.path.splitext(object_path.lower())[1]
            if ext in {".btg", ".gz"}:
                return None

            try:
                lon = float(parts[2])
                lat = float(parts[3])
                elev = float(parts[4])
            except Exception:
                return None

            return (
                directive,
                object_path.replace("\\", "/").lower(),
                f"{lon:.8f}",
                f"{lat:.8f}",
                f"{elev:.3f}",
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

        existing_signatures: Set[Tuple[str, str, str, str, str]] = set()
        for existing_line in rewritten:
            signature = _object_position_signature(existing_line)
            if signature is not None:
                existing_signatures.add(signature)

        appended_unique_count = 0
        pending_append_lines: List[str] = [replacement_by_entry_index[idx] for idx in sorted(replacement_by_entry_index)]
        pending_append_lines.extend(append_lines)
        for candidate_line in pending_append_lines:
            signature = _object_position_signature(candidate_line)
            if signature is not None and signature in existing_signatures:
                continue
            rewritten.append(candidate_line)
            if signature is not None:
                existing_signatures.add(signature)
            appended_unique_count += 1

        written_models = replaced_count + appended_unique_count

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

    def _format_file_size_bytes(self: "BTGDisplayApp", size_bytes: int) -> str:
        size = float(max(0, int(size_bytes)))
        units = ["B", "KB", "MB", "GB", "TB"]
        unit_index = 0
        while size >= 1024.0 and unit_index < (len(units) - 1):
            size /= 1024.0
            unit_index += 1
        if unit_index == 0:
            return f"{int(size)} {units[unit_index]}"
        return f"{size:.2f} {units[unit_index]}"

    def _collect_stg_reference_paths(self: "BTGDisplayApp", stg_path: str) -> List[str]:
        references: List[str] = []
        seen: Set[str] = set()
        path_directives = {
            "OBJECT_BASE",
            "OBJECT",
            "OBJECT_STATIC",
            "OBJECT_STATIC_AGL",
            "OBJECT_SHARED",
            "OBJECT_SHARED_AGL",
            "OBJECT_SIGN",
            "OBJECT_SIGN_AGL",
            "OBJECT_BUILDING_MESH_ROUGH",
            "OBJECT_BUILDING_MESH_DETAILED",
            "OBJECT_ROAD_ROUGH",
            "OBJECT_ROAD_DETAILED",
            "OBJECT_RAILWAY_ROUGH",
            "OBJECT_RAILWAY_DETAILED",
            "BUILDING_LIST",
            "TREE_LIST",
            "LINE_FEATURE_LIST",
            "LIGHT_LIST",
        }

        try:
            with open(stg_path, "r", encoding="utf-8") as handle:
                raw_lines = handle.readlines()
        except Exception:
            return references

        for raw in raw_lines:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                parts = shlex.split(line)
            except Exception:
                parts = line.split()
            if len(parts) < 2:
                continue

            directive = parts[0].upper()
            candidates: List[str] = []
            if directive in path_directives:
                candidates.append(parts[1])
            elif directive == "OBJECT_INSTANCED":
                candidates.append(parts[1])
                if len(parts) >= 3:
                    candidates.append(parts[2])

            for candidate in candidates:
                normalized = candidate.strip().strip('"').replace("\\", "/")
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                references.append(normalized)

        return references

    def _resolve_reference_path(self: "BTGDisplayApp", reference_path: str, context_file: str) -> str:
        candidate = reference_path.strip().strip('"')
        if not candidate:
            return ""

        if os.path.isabs(candidate) and os.path.isfile(candidate):
            return os.path.abspath(candidate)

        resolved = resolve_stg_object_path(candidate, context_file, self.flightgear_root)
        if resolved:
            return os.path.abspath(resolved)

        local = os.path.abspath(os.path.join(os.path.dirname(context_file), candidate))
        if os.path.isfile(local):
            return local
        return ""

    def _collect_xml_model_refs(self: "BTGDisplayApp", xml_path: str) -> List[str]:
        refs: List[str] = []
        seen: Set[str] = set()
        try:
            tree = ET.parse(xml_path)
        except Exception:
            return refs

        for element in tree.iter():
            text = (element.text or "").strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered.endswith((".ac", ".xml", ".btg", ".btg.gz", ".osg", ".osgb")):
                normalized = text.replace("\\", "/")
                if normalized not in seen:
                    seen.add(normalized)
                    refs.append(normalized)
        return refs

    def _collect_ac_texture_refs(self: "BTGDisplayApp", ac_path: str) -> List[str]:
        refs: List[str] = []
        seen: Set[str] = set()
        try:
            with open(ac_path, "r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()
        except Exception:
            return refs

        for raw in lines:
            line = raw.strip()
            if not line.lower().startswith("texture"):
                continue
            try:
                parts = shlex.split(line)
            except Exception:
                parts = line.split()
            if len(parts) < 2:
                continue
            texture_ref = parts[1].strip().strip('"').replace("\\", "/")
            if texture_ref and texture_ref not in seen:
                seen.add(texture_ref)
                refs.append(texture_ref)
        return refs

    def _resolve_texture_path(self: "BTGDisplayApp", texture_ref: str, model_path: str) -> str:
        candidate = texture_ref.strip().strip('"')
        if not candidate:
            return ""
        if os.path.isabs(candidate) and os.path.isfile(candidate):
            return os.path.abspath(candidate)

        model_dir = os.path.dirname(model_path)
        candidates = [
            os.path.abspath(os.path.join(model_dir, candidate)),
            os.path.abspath(os.path.join(model_dir, "Textures", candidate)),
        ]

        data_root = resolve_fgdata_root(self.flightgear_root)
        if data_root:
            candidates.extend(
                [
                    os.path.abspath(os.path.join(data_root, "Textures", candidate)),
                    os.path.abspath(os.path.join(data_root, "Textures", "Terrain", candidate)),
                    os.path.abspath(os.path.join(data_root, "Textures", "Runway", candidate)),
                ]
            )

        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    def _package_relpath_for_source(self: "BTGDisplayApp", source_path: str) -> str:
        normalized = os.path.abspath(source_path)
        pieces = normalized.split(os.sep)
        lowered = [piece.lower() for piece in pieces]

        scenery_idx = -1
        for idx, piece in enumerate(lowered):
            if piece == "scenery":
                scenery_idx = idx
                break
        if scenery_idx >= 0:
            rel_pieces = pieces[scenery_idx:]
            return os.path.join(*rel_pieces)

        suffixes = {
            "terrain",
            "objects",
            "models",
            "airports",
            "navdata",
            "orthophotos",
            "vpb",
            "buildings",
            "roads",
            "pylons",
            "details",
            "trees",
        }
        for idx, piece in enumerate(lowered):
            if piece in suffixes:
                rel_pieces = ["Scenery"] + pieces[idx:]
                return os.path.join(*rel_pieces)

        fallback_name = os.path.basename(normalized)
        return os.path.join("Scenery", "Objects", "external", fallback_name)

    def create_scenery_package(self: "BTGDisplayApp", target_directory: str) -> bool:
        stg_path = self._resolve_active_stg_path(require_exists=True)
        if not stg_path:
            self._set_status_t(
                "status.package_failed_no_stg",
                "Create package failed: no STG file associated with current scene",
            )
            return False

        target_dir = os.path.abspath(target_directory)
        if not os.path.isdir(target_dir):
            self._set_status_t(
                "status.package_failed_invalid_target",
                "Create package failed: invalid target folder",
            )
            return False

        source_files: Set[str] = {stg_path}
        pending: List[str] = []
        for reference in self._collect_stg_reference_paths(stg_path):
            resolved = self._resolve_reference_path(reference, stg_path)
            if resolved and os.path.isfile(resolved) and resolved not in source_files:
                source_files.add(resolved)
                pending.append(resolved)

        processed: Set[str] = set()
        while pending:
            current = pending.pop()
            if current in processed:
                continue
            processed.add(current)

            ext = os.path.splitext(current.lower())[1]
            if ext == ".xml":
                for reference in self._collect_xml_model_refs(current):
                    resolved = self._resolve_reference_path(reference, current)
                    if resolved and os.path.isfile(resolved) and resolved not in source_files:
                        source_files.add(resolved)
                        pending.append(resolved)
            elif ext == ".ac":
                for texture_ref in self._collect_ac_texture_refs(current):
                    resolved = self._resolve_texture_path(texture_ref, current)
                    if resolved and os.path.isfile(resolved) and resolved not in source_files:
                        source_files.add(resolved)

        rel_to_source: Dict[str, str] = {}
        for source in sorted(source_files):
            rel_path = self._package_relpath_for_source(source)
            base_rel, ext = os.path.splitext(rel_path)
            unique_rel = rel_path
            counter = 2
            while unique_rel in rel_to_source and rel_to_source[unique_rel] != source:
                unique_rel = f"{base_rel}_{counter}{ext}"
                counter += 1
            rel_to_source[unique_rel] = source

        copied_rel_paths: List[str] = []
        copy_errors: List[str] = []
        for rel_path, source in rel_to_source.items():
            destination = os.path.abspath(os.path.join(target_dir, rel_path))
            try:
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copy2(source, destination)
                copied_rel_paths.append(rel_path)
            except Exception as exc:
                copy_errors.append(f"{source}: {exc}")

        template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "dist_scenery_readme_template.txt"))
        readme_rel_path = "scenery_readme.txt"
        readme_destination = os.path.abspath(os.path.join(target_dir, readme_rel_path))
        try:
            with open(template_path, "r", encoding="utf-8") as handle:
                readme_lines = handle.readlines()
        except Exception:
            readme_lines = []

        if readme_lines:
            location_text = ""
            if self.scene_base_mesh and self.scene_base_mesh.center_ecef != (0.0, 0.0, 0.0):
                lon_deg, lat_deg, _elev_m = ecef_to_geodetic(*self.scene_base_mesh.center_ecef)
                location_text = f"{lat_deg:.6f}, {lon_deg:.6f}"

            patched_lines: List[str] = []
            for raw in readme_lines:
                if raw.strip().upper().startswith("SCENERY LOCATION:"):
                    existing = raw.split(":", 1)[1].strip() if ":" in raw else ""
                    if not existing and location_text:
                        patched_lines.append(f"SCENERY LOCATION: {location_text}\n")
                        continue
                patched_lines.append(raw)

            try:
                with open(readme_destination, "w", encoding="utf-8") as handle:
                    handle.writelines(patched_lines)
                copied_rel_paths.append(readme_rel_path)
            except Exception as exc:
                copy_errors.append(f"{readme_destination}: {exc}")

        trimmed_target = target_dir.rstrip(os.sep)
        if not trimmed_target:
            trimmed_target = target_dir
        zip_path = os.path.abspath(f"{trimmed_target}.zip")

        zip_entries = sorted(set(copied_rel_paths))
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for rel_path in zip_entries:
                    full_path = os.path.join(target_dir, rel_path)
                    if os.path.isfile(full_path):
                        archive.write(full_path, arcname=rel_path.replace(os.sep, "/"))
        except Exception as exc:
            self._set_status_t(
                "status.package_failed_zip_fmt",
                "Create package failed while writing zip: {error}",
                error=exc,
            )
            return False

        try:
            zip_size = os.path.getsize(zip_path)
        except Exception:
            zip_size = 0

        summary_lines: List[str] = [
            f"Zip Location: {zip_path}",
            "File contents:",
        ]
        max_listed = 36
        display_entries = sorted(path.replace(os.sep, "/") for path in zip_entries)
        for rel_path in display_entries[:max_listed]:
            summary_lines.append(f"- {rel_path}")
        if len(display_entries) > max_listed:
            summary_lines.append(f"- ... ({len(display_entries) - max_listed} more)")
        summary_lines.append(f"File Size: {self._format_file_size_bytes(zip_size)}")

        if copy_errors:
            summary_lines.append("")
            summary_lines.append("Warnings:")
            for err in copy_errors[:8]:
                summary_lines.append(f"- {err}")
            if len(copy_errors) > 8:
                summary_lines.append(f"- ... ({len(copy_errors) - 8} more)")

        self._set_status_t(
            "status.package_created_fmt",
            "Scenery package created: {zip_path}",
            zip_path=zip_path,
        )
        self._open_package_summary_dialog(
            self._menu_t("dialog.package_created_title", "Scenery Package Created"),
            summary_lines,
        )
        return True

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
