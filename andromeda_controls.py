#!/usr/bin/env python3
"""Control and movement helpers for BTG display app."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

import pygame

from andromeda_backend import (
    BTGMesh,
    KeyBinding,
    ObjectCatalogEntry,
    STGModelInstance,
    build_model_instance_mesh,
    compose_scene_mesh,
    load_catalog_object_template,
    v_norm,
)
from andromeda_render import rotate_camera_to_world

if TYPE_CHECKING:
    from andromeda import BTGDisplayApp


class ControlsMixin:
    def _fast_remove_model_instance_from_mesh(self: "BTGDisplayApp", removed_index: int, removed: STGModelInstance) -> bool:
        mesh = self.mesh
        if mesh is None:
            return False

        v_start = int(removed.mesh_vertex_start)
        v_count = int(removed.mesh_vertex_count)
        f_start = int(removed.mesh_face_start)
        f_count = int(removed.mesh_face_count)
        if v_start < 0 or v_count <= 0 or f_start < 0 or f_count <= 0:
            return False

        v_end = v_start + v_count
        f_end = f_start + f_count
        if v_end > len(mesh.vertices) or f_end > len(mesh.faces):
            return False

        t0 = time.perf_counter()

        new_vertices = list(mesh.vertices[:v_start])
        new_vertices.extend(mesh.vertices[v_end:])

        adjusted_faces: List[Tuple[int, int, int]] = []
        for a, b, c in list(mesh.faces[:f_start]) + list(mesh.faces[f_end:]):
            if a >= v_end:
                a -= v_count
            if b >= v_end:
                b -= v_count
            if c >= v_end:
                c -= v_count
            adjusted_faces.append((a, b, c))

        new_face_texcoords = list(mesh.face_texcoords[:f_start]) + list(mesh.face_texcoords[f_end:])
        new_face_materials = list(mesh.face_materials[:f_start]) + list(mesh.face_materials[f_end:])
        new_face_colors = list(mesh.face_colors[:f_start]) + list(mesh.face_colors[f_end:])

        if not (
            len(adjusted_faces) == len(new_face_texcoords)
            and len(adjusted_faces) == len(new_face_materials)
            and len(adjusted_faces) == len(new_face_colors)
        ):
            return False

        for instance in self.scene_model_instances[removed_index:]:
            if instance.mesh_vertex_start >= 0:
                instance.mesh_vertex_start -= v_count
            if instance.mesh_face_start >= 0:
                instance.mesh_face_start -= f_count

        self.mesh = BTGMesh(
            vertices=new_vertices,
            faces=adjusted_faces,
            texcoords=list(mesh.texcoords),
            face_texcoords=new_face_texcoords,
            face_materials=new_face_materials,
            face_colors=new_face_colors,
            center_ecef=mesh.center_ecef,
            radius=mesh.radius,
        )
        self.mesh_bounds = self._mesh_bounds(self.mesh)
        self._build_face_data(self.mesh)
        self.shadow_last_rebuild_ms = 0
        self._shadow_pose = None

        t1 = time.perf_counter()
        self._record_rotation_perf("delete.fast.total", (t1 - t0) * 1000.0)
        self._maybe_report_rotation_perf()
        return True


    def _format_angle_step_deg(self: "BTGDisplayApp", value: float) -> str:
        if abs(value) < 1.0:
            return f"{value:.2f}"
        return f"{value:.1f}"

    def _cycle_selected_object_catalog(self: "BTGDisplayApp", step: int) -> None:
        idx = self.selected_model_instance_index
        if idx is None or not (0 <= idx < len(self.scene_model_instances)):
            self._set_status_t("status.select_model_first", "Select a model first (crosshair + left click)")
            return
        if self.scene_static_merged_mesh is None:
            self._set_status_t("status.cycle_failed_no_scene", "Cycle failed: no scene loaded")
            return

        self._build_add_object_categories()
        if not self.add_object_by_category:
            self._set_status_t("status.cycle_failed_catalog_empty", "Cycle failed: object catalog is empty")
            return

        instance = self.scene_model_instances[idx]
        current_category = ""
        current_index: Optional[int] = None

        for category, entries in self.add_object_by_category.items():
            for entry_idx, entry in enumerate(entries):
                if entry.object_path == instance.source_path:
                    current_category = category
                    current_index = entry_idx
                    break
            if current_index is not None:
                break

        if current_index is None:
            if self.add_object_selected_category in self.add_object_by_category:
                current_category = self.add_object_selected_category
            else:
                current_category = next(iter(self.add_object_by_category.keys()))
            entries = self.add_object_by_category.get(current_category, [])
            if not entries:
                self._set_status_t("status.cycle_failed_category_empty", "Cycle failed: selected category has no entries")
                return
            if 0 <= self.add_object_file_index < len(entries):
                current_index = self.add_object_file_index
            else:
                current_index = 0

        entries = self.add_object_by_category.get(current_category, [])
        if not entries:
            self._set_status_t("status.cycle_failed_category_empty", "Cycle failed: selected category has no entries")
            return

        target_index = (current_index + step) % len(entries)
        target_entry = entries[target_index]

        template_mesh, is_ac_model, _resolved_model_path = load_catalog_object_template(
            target_entry,
            self.scene_static_merged_mesh.center_ecef,
            self.flightgear_root,
            self.shared_model_mesh_cache,
        )
        if template_mesh is None:
            self._set_status_t(
                "status.cycle_failed_load_fmt",
                "Cycle failed: could not load '{object_path}'",
                object_path=target_entry.object_path,
            )
            return

        instance.template_mesh = template_mesh
        instance.is_ac_model = is_ac_model
        instance.source_path = target_entry.object_path

        self.add_object_selected_category = current_category
        self.add_object_file_index = target_index
        if self.show_menu and self.menu_mode == "add_object_files":
            self._update_add_object_preview(target_entry)

        if not self._rebuild_scene_from_cache(keep_camera=True):
            self._reload_current_scene()
            return

        self.selected_model_instance_index = idx
        kind = "ac" if instance.is_ac_model else "model"
        self._set_status_t(
            "status.cycled_object_fmt",
            "Switched M{index} [{kind}] -> {path} ({position}/{total} in {category})",
            index=f"{idx:03d}",
            kind=kind,
            path=target_entry.object_path,
            position=target_index + 1,
            total=len(entries),
            category=current_category,
        )

    def _clear_add_object_preview(self: "BTGDisplayApp") -> None:
        self.add_object_preview_mesh = None
        self.add_object_preview_path = ""
        self.add_object_preview_norm_vertices = []
        self.add_object_preview_material_paths = {}
        self.add_object_preview_face_step = 1
        self.add_object_preview_face_indices = []
        self.add_object_preview_textured_face_meta = {}
        self.add_object_preview_face_cache_ready = False

    def _update_add_object_preview(self: "BTGDisplayApp", entry: ObjectCatalogEntry) -> bool:
        if self.scene_static_merged_mesh is None:
            self._clear_add_object_preview()
            return False

        template_mesh, is_ac_model, _resolved_model_path = load_catalog_object_template(
            entry,
            self.scene_static_merged_mesh.center_ecef,
            self.flightgear_root,
            self.shared_model_mesh_cache,
        )
        if template_mesh is None:
            self._clear_add_object_preview()
            return False

        target_z = 0.0
        idx = self.selected_model_instance_index
        if idx is not None and 0 <= idx < len(self.scene_model_instances):
            selected = self.scene_model_instances[idx]
            target_z = selected.origin_enu[2] + selected.offset_z_m

        hit_point = self._crosshair_hit_static_scene()
        if hit_point is not None:
            place_x, place_y, target_z = hit_point
        else:
            place_x, place_y = self._crosshair_target_xy_at_elevation(target_z)

        preview_instance = STGModelInstance(
            template_mesh=template_mesh,
            origin_enu=(place_x, place_y, target_z),
            heading_deg=0.0,
            pitch_deg=0.0,
            roll_deg=0.0,
            is_ac_model=is_ac_model,
            source_path=entry.object_path,
            stg_directive="",
            stg_entry_index=None,
        )
        preview_mesh = build_model_instance_mesh(
            preview_instance,
            self.scene_static_merged_mesh.center_ecef,
            0.0,
            0.0,
            0.0,
        )
        if not preview_mesh.vertices or not preview_mesh.faces:
            self._clear_add_object_preview()
            return False

        xs = [v[0] for v in preview_mesh.vertices]
        ys = [v[1] for v in preview_mesh.vertices]
        zs = [v[2] for v in preview_mesh.vertices]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        cz = (min(zs) + max(zs)) * 0.5
        radius = max(
            1.0,
            max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) * 0.5,
        )
        inv_radius = 1.0 / radius
        self.add_object_preview_norm_vertices = [
            ((vx - cx) * inv_radius, (vy - cy) * inv_radius, (vz - cz) * inv_radius)
            for vx, vy, vz in preview_mesh.vertices
        ]

        max_faces = max(250, int(self.add_object_preview_max_faces))
        face_count = len(preview_mesh.faces)
        self.add_object_preview_face_step = max(1, int(math.ceil(face_count / max_faces))) if face_count > max_faces else 1
        self.add_object_preview_material_paths = {}
        self.add_object_preview_face_indices = []
        self.add_object_preview_textured_face_meta = {}
        self.add_object_preview_face_cache_ready = False

        self.add_object_preview_mesh = preview_mesh
        self.add_object_preview_path = entry.object_path
        return True

    def set_mouse_capture(self: "BTGDisplayApp", capture: bool) -> None:
        self._reset_mouse_edit_drag_state()
        self.mouse_captured = capture
        pygame.event.set_grab(capture)
        pygame.mouse.set_visible(not capture)

    def _reset_mouse_edit_drag_state(self: "BTGDisplayApp") -> None:
        self.mouse_edit_drag_active = False
        self.mouse_edit_drag_started = False
        self.mouse_edit_drag_accum_px_x = 0.0
        self.mouse_edit_drag_accum_px_y = 0.0
        self.mouse_edit_pending_dx_m = 0.0
        self.mouse_edit_pending_dy_m = 0.0
        self.mouse_edit_pending_dz_m = 0.0
        self.mouse_edit_pending_dyaw_deg = 0.0

    def _queue_selected_model_mouse_drag(
        self: "BTGDisplayApp",
        rel_x_px: float,
        rel_y_px: float,
        shift_mode: bool,
    ) -> None:
        if rel_x_px == 0.0 and rel_y_px == 0.0:
            return

        move_per_px = self.object_nudge_step_m / max(1.0, self.mouse_edit_pixels_per_move_step)
        yaw_per_px = self.model_angle_step_deg / max(1.0, self.mouse_edit_pixels_per_yaw_step)

        if shift_mode:
            self.mouse_edit_pending_dz_m += -rel_y_px * move_per_px
            self.mouse_edit_pending_dyaw_deg += rel_x_px * yaw_per_px
            return

        right_axis, forward_axis = self._nudge_planar_axes()
        move_right_m = rel_x_px * move_per_px
        move_forward_m = -rel_y_px * move_per_px
        self.mouse_edit_pending_dx_m += right_axis[0] * move_right_m + forward_axis[0] * move_forward_m
        self.mouse_edit_pending_dy_m += right_axis[1] * move_right_m + forward_axis[1] * move_forward_m

    def _flush_pending_mouse_drag_transform(self: "BTGDisplayApp", emit_status: bool = False) -> None:
        dx_m = self.mouse_edit_pending_dx_m
        dy_m = self.mouse_edit_pending_dy_m
        dz_m = self.mouse_edit_pending_dz_m
        dyaw_deg = self.mouse_edit_pending_dyaw_deg
        if (
            abs(dx_m) < 1e-9
            and abs(dy_m) < 1e-9
            and abs(dz_m) < 1e-9
            and abs(dyaw_deg) < 1e-9
        ):
            return

        self.mouse_edit_pending_dx_m = 0.0
        self.mouse_edit_pending_dy_m = 0.0
        self.mouse_edit_pending_dz_m = 0.0
        self.mouse_edit_pending_dyaw_deg = 0.0

        self._adjust_selected_model_transform(
            dx_m=dx_m,
            dy_m=dy_m,
            dz_m=dz_m,
            dyaw_deg=dyaw_deg,
            update_status=emit_status,
            full_draw=True,
        )

    def _apply_selected_model_mouse_drag(
        self: "BTGDisplayApp",
        rel_x_px: float,
        rel_y_px: float,
        shift_mode: bool,
    ) -> None:
        self._queue_selected_model_mouse_drag(rel_x_px, rel_y_px, shift_mode)

    def _normalized_mods(self: "BTGDisplayApp", mods: int) -> int:
        normalized = 0
        if mods & pygame.KMOD_SHIFT:
            normalized |= pygame.KMOD_SHIFT
        if mods & pygame.KMOD_CTRL:
            normalized |= pygame.KMOD_CTRL
        if mods & pygame.KMOD_ALT:
            normalized |= pygame.KMOD_ALT
        return normalized

    def _is_modifier_key(self: "BTGDisplayApp", key_code: int) -> bool:
        return key_code in {
            pygame.K_LSHIFT,
            pygame.K_RSHIFT,
            pygame.K_LCTRL,
            pygame.K_RCTRL,
            pygame.K_LALT,
            pygame.K_RALT,
            pygame.K_LMETA,
            pygame.K_RMETA,
        }

    def format_key(self: "BTGDisplayApp", key_code: int) -> str:
        return pygame.key.name(key_code)

    def _target_screen_xy(self: "BTGDisplayApp") -> Tuple[float, float]:
        width = max(1, int(self.size[0]))
        height = max(1, int(self.size[1]))
        if self.mouse_captured:
            return (0.5 * width, 0.5 * height)

        mx, my = pygame.mouse.get_pos()
        clamped_x = max(0.0, min(float(width - 1), float(mx)))
        clamped_y = max(0.0, min(float(height - 1), float(my)))
        return (clamped_x, clamped_y)

    def _ray_from_screen_xy(
        self: "BTGDisplayApp",
        sx: float,
        sy: float,
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        width = max(1.0, float(self.size[0]))
        height = max(1.0, float(self.size[1]))
        focal = (0.5 * height) / math.tan(math.radians(self.fov_deg) * 0.5)

        cam_x = (float(sx) - (0.5 * width)) / focal
        cam_y = 1.0
        cam_z = ((0.5 * height) - float(sy)) / focal
        ray_dir = rotate_camera_to_world((cam_x, cam_y, cam_z), self.yaw, self.pitch)
        return self.camera_pos, v_norm(ray_dir)

    def _hover_model_index_at_screen(self: "BTGDisplayApp", sx: float, sy: float) -> Optional[int]:
        labels = self._collect_projected_model_labels()
        if not labels:
            return None

        hover_radius_sq = 12.0 * 12.0
        best_idx: Optional[int] = None
        best_dist_sq = float("inf")
        for _depth, label_x, label_y, _text, _is_ac_model, model_idx in labels:
            dx = label_x - float(sx)
            dy = label_y - float(sy)
            dist_sq = dx * dx + dy * dy
            if dist_sq > hover_radius_sq:
                continue
            if dist_sq < best_dist_sq:
                best_dist_sq = dist_sq
                best_idx = model_idx
        return best_idx

    def format_binding(self: "BTGDisplayApp", binding: KeyBinding) -> str:
        if binding.key_code < 0:
            return "unbound"
        parts: List[str] = []
        if binding.mods & pygame.KMOD_CTRL:
            parts.append("ctrl")
        if binding.mods & pygame.KMOD_ALT:
            parts.append("alt")
        if binding.mods & pygame.KMOD_SHIFT:
            parts.append("shift")
        parts.append(self.format_key(binding.key_code))
        return "+".join(parts)

    def find_binding_action(self: "BTGDisplayApp", binding: KeyBinding) -> Optional[str]:
        for action, bound in self.bindings.items():
            if bound == binding:
                return action
        return None

    def is_binding_active(self: "BTGDisplayApp", action: str, pressed: Sequence[bool], mods: int) -> bool:
        binding = self.bindings[action]
        if binding.key_code < 0:
            return False
        return bool(pressed[binding.key_code]) and (mods & binding.mods) == binding.mods

    def binding_matches_event(self: "BTGDisplayApp", action: str, event: pygame.event.Event) -> bool:
        binding = self.bindings[action]
        if binding.key_code < 0:
            return False
        event_mods = self._normalized_mods(getattr(event, "mod", 0))
        return event.key == binding.key_code and event_mods == binding.mods

    def _crosshair_ray(self: "BTGDisplayApp") -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        target_x, target_y = self._target_screen_xy()
        return self._ray_from_screen_xy(target_x, target_y)

    def _crosshair_hit_static_scene(self: "BTGDisplayApp") -> Optional[Tuple[float, float, float]]:
        """Return nearest hit point on static scene under crosshair, if any."""
        static_mesh = self.scene_static_merged_mesh
        if static_mesh is None or not static_mesh.vertices or not static_mesh.faces:
            return None

        ray_origin, ray_dir = self._crosshair_ray()
        ox, oy, oz = ray_origin
        dx, dy, dz = ray_dir

        # Moller-Trumbore intersection against all static triangles.
        eps = 1e-7
        closest_t: Optional[float] = None
        closest_hit: Optional[Tuple[float, float, float]] = None
        verts = static_mesh.vertices
        for ia, ib, ic in static_mesh.faces:
            ax, ay, az = verts[ia]
            bx, by, bz = verts[ib]
            cx, cy, cz = verts[ic]

            e1x, e1y, e1z = (bx - ax, by - ay, bz - az)
            e2x, e2y, e2z = (cx - ax, cy - ay, cz - az)

            px = dy * e2z - dz * e2y
            py = dz * e2x - dx * e2z
            pz = dx * e2y - dy * e2x
            det = e1x * px + e1y * py + e1z * pz
            if -eps < det < eps:
                continue
            inv_det = 1.0 / det

            tx, ty, tz = (ox - ax, oy - ay, oz - az)
            u = (tx * px + ty * py + tz * pz) * inv_det
            if u < 0.0 or u > 1.0:
                continue

            qx = ty * e1z - tz * e1y
            qy = tz * e1x - tx * e1z
            qz = tx * e1y - ty * e1x
            v = (dx * qx + dy * qy + dz * qz) * inv_det
            if v < 0.0 or (u + v) > 1.0:
                continue

            t = (e2x * qx + e2y * qy + e2z * qz) * inv_det
            if t <= eps:
                continue
            if closest_t is None or t < closest_t:
                closest_t = t
                closest_hit = (ox + dx * t, oy + dy * t, oz + dz * t)

        return closest_hit

    def _crosshair_target_xy_at_elevation(self: "BTGDisplayApp", target_z: float) -> Tuple[float, float]:
        ray_origin, ray_dir = self._crosshair_ray()
        cx, cy, cz = ray_origin
        fx, fy, fz = ray_dir

        if abs(fz) > 1e-6:
            t = (target_z - cz) / fz
            if t <= 0.0:
                t = 200.0  # looking away from plane — use a fixed forward distance
        else:
            t = 200.0  # horizontal look — project forward 200 m

        return (cx + fx * t, cy + fy * t)

    def _place_catalog_object_at_crosshair(self: "BTGDisplayApp", entry: "ObjectCatalogEntry") -> bool:
        if self.scene_static_merged_mesh is None:
            self._set_status_t("status.add_object_failed_no_scene", "Add object failed: no scene loaded")
            return False

        template_mesh, is_ac_model, _resolved_model_path = load_catalog_object_template(
            entry,
            self.scene_static_merged_mesh.center_ecef,
            self.flightgear_root,
            self.shared_model_mesh_cache,
        )
        if template_mesh is None:
            self._set_status_t(
                "status.add_object_failed_load_fmt",
                "Add object failed: could not load '{object_path}'",
                object_path=entry.object_path,
            )
            return False

        target_z = 0.0
        idx = self.selected_model_instance_index
        if idx is not None and 0 <= idx < len(self.scene_model_instances):
            selected = self.scene_model_instances[idx]
            target_z = selected.origin_enu[2] + selected.offset_z_m

        hit_point = self._crosshair_hit_static_scene()
        if hit_point is not None:
            place_x, place_y, target_z = hit_point
        else:
            place_x, place_y = self._crosshair_target_xy_at_elevation(target_z)

        new_inst = STGModelInstance(
            template_mesh=template_mesh,
            origin_enu=(place_x, place_y, target_z),
            heading_deg=0.0,
            pitch_deg=0.0,
            roll_deg=0.0,
            is_ac_model=is_ac_model,
            source_path=entry.object_path,
            stg_directive="OBJECT_SHARED",
            stg_entry_index=None,
            offset_yaw_deg=0.0,
            offset_pitch_deg=0.0,
            offset_roll_deg=0.0,
        )
        self.scene_model_instances.append(new_inst)
        new_idx = len(self.scene_model_instances) - 1

        if not self._rebuild_scene_from_cache(keep_camera=True):
            self._reload_current_scene()

        self.selected_model_instance_index = new_idx
        kind = "ac" if new_inst.is_ac_model else "model"
        self._set_status_t(
            "status.added_object_fmt",
            "Added M{index} [{kind}] {path} at ENU ({x:.1f}, {y:.1f}, {z:.1f})",
            index=f"{new_idx:03d}",
            kind=kind,
            path=new_inst.source_path,
            x=place_x,
            y=place_y,
            z=target_z,
        )
        return True

    def _adjust_selected_model_transform(
        self: "BTGDisplayApp",
        dx_m: float = 0.0,
        dy_m: float = 0.0,
        dz_m: float = 0.0,
        dyaw_deg: float = 0.0,
        update_status: bool = True,
        full_draw: bool = False,
    ) -> None:
        idx = self.selected_model_instance_index
        if idx is None or not (0 <= idx < len(self.scene_model_instances)):
            self._set_status_t("status.select_model_first", "Select a model first (crosshair + left click)")
            return

        instance = self.scene_model_instances[idx]
        instance.offset_x_m += dx_m
        instance.offset_y_m += dy_m
        instance.offset_z_m += dz_m
        instance.offset_yaw_deg += dyaw_deg

        if self._update_selected_model_preview_fast(idx):
            if full_draw and self._rebuild_model_textured_batches_only(selected_idx=idx):
                self.fast_model_preview_pending = False
                self.fast_model_preview_deadline_ms = 0
            else:
                self.fast_model_preview_pending = True
                self.fast_model_preview_deadline_ms = pygame.time.get_ticks() + 100
        elif not self._rebuild_scene_from_cache(
            keep_camera=True,
            build_textures=False,
            perf_tag="preview_fallback",
        ):
            self._reload_current_scene()
        else:
            self.fast_model_preview_pending = True
            self.fast_model_preview_deadline_ms = pygame.time.get_ticks() + 100

        if update_status:
            self._set_status_t(
                "status.selected_transform_fmt",
                "Selected M{index} transform | x={x:.2f} m y={y:.2f} m z={z:.2f} m yaw={yaw:.1f} deg",
                index=f"{idx:03d}",
                x=instance.offset_x_m,
                y=instance.offset_y_m,
                z=instance.offset_z_m,
                yaw=instance.offset_yaw_deg,
            )

    def _paste_copied_object(self: "BTGDisplayApp") -> None:
        clipboard = self.clipboard_instance
        if clipboard is None:
            self._set_status_t(
                "status.clipboard_empty",
                "Nothing in clipboard - Ctrl+C to copy a selected model first",
            )
            return
        if self.scene_static_merged_mesh is None:
            self._set_status_t("status.paste_failed_no_scene", "Paste failed: no scene loaded")
            return

        paste_x, paste_y, target_z = clipboard.origin_enu

        new_inst = STGModelInstance(
            template_mesh=clipboard.template_mesh,
            origin_enu=(paste_x, paste_y, target_z),
            heading_deg=clipboard.heading_deg,
            pitch_deg=clipboard.pitch_deg,
            roll_deg=clipboard.roll_deg,
            is_ac_model=clipboard.is_ac_model,
            source_path=clipboard.source_path,
            stg_directive=clipboard.stg_directive,
            stg_entry_index=None,
            offset_yaw_deg=clipboard.offset_yaw_deg,
            offset_pitch_deg=clipboard.offset_pitch_deg,
            offset_roll_deg=clipboard.offset_roll_deg,
            offset_x_m=clipboard.offset_x_m,
            offset_y_m=clipboard.offset_y_m,
            offset_z_m=clipboard.offset_z_m,
        )
        self.scene_model_instances.append(new_inst)
        new_idx = len(self.scene_model_instances) - 1

        if not self._rebuild_scene_from_cache(keep_camera=True):
            self._reload_current_scene()

        self.selected_model_instance_index = new_idx
        kind = "ac" if new_inst.is_ac_model else "model"
        self._set_status_t(
            "status.pasted_object_fmt",
            "Pasted M{index} [{kind}] {path} at copied ENU ({x:.1f}, {y:.1f}, {z:.1f})",
            index=f"{new_idx:03d}",
            kind=kind,
            path=new_inst.source_path,
            x=paste_x,
            y=paste_y,
            z=target_z,
        )

    def _delete_selected_object(self: "BTGDisplayApp") -> None:
        idx = self.selected_model_instance_index
        if idx is None or not (0 <= idx < len(self.scene_model_instances)):
            self._set_status_t("status.no_model_selected_delete", "No model selected to delete")
            return

        removed = self.scene_model_instances.pop(idx)
        if self.crosshair_hover_model_index is not None:
            if self.crosshair_hover_model_index == idx:
                self.crosshair_hover_model_index = None
            elif self.crosshair_hover_model_index > idx:
                self.crosshair_hover_model_index -= 1

        self.selected_model_instance_index = None
        if not self._fast_remove_model_instance_from_mesh(idx, removed):
            t0 = time.perf_counter()
            if not self._rebuild_scene_from_cache(keep_camera=True, perf_tag="delete_fallback"):
                self._reload_current_scene()
            t1 = time.perf_counter()
            self._record_rotation_perf("delete.fallback.total", (t1 - t0) * 1000.0)
            self._maybe_report_rotation_perf()

        kind = "ac" if removed.is_ac_model else "model"
        self._set_status_t(
            "status.deleted_object_fmt",
            "Deleted [{kind}] {path}",
            kind=kind,
            path=removed.source_path,
        )

    def _record_rotation_perf(self: "BTGDisplayApp", key: str, duration_ms: float) -> None:
        stats = self.rotation_perf_stats.setdefault(key, {"count": 0.0, "total": 0.0, "max": 0.0})
        stats["count"] += 1.0
        stats["total"] += duration_ms
        if duration_ms > stats["max"]:
            stats["max"] = duration_ms

    def _maybe_report_rotation_perf(self: "BTGDisplayApp") -> None:
        if not self.rotation_perf_debug_enabled:
            return
        now = pygame.time.get_ticks()
        if now - self.rotation_perf_last_report_ms < self.rotation_perf_report_interval_ms:
            return
        if not self.rotation_perf_stats:
            return

        parts = []
        for key in sorted(self.rotation_perf_stats.keys()):
            stats = self.rotation_perf_stats[key]
            count = max(1.0, stats["count"])
            avg = stats["total"] / count
            parts.append(f"{key}: avg={avg:.2f}ms max={stats['max']:.2f}ms n={int(stats['count'])}")

        print("[rotation-perf] " + " | ".join(parts))
        self.rotation_perf_last_report_ms = now

    def _update_selected_model_preview_fast(self: "BTGDisplayApp", idx: int) -> bool:
        if self.mesh is None or self.scene_static_merged_mesh is None:
            return False
        if not (0 <= idx < len(self.scene_model_instances)):
            return False

        instance = self.scene_model_instances[idx]
        if instance.mesh_vertex_start < 0 or instance.mesh_vertex_count <= 0:
            return False

        t0 = time.perf_counter()
        updated_mesh = build_model_instance_mesh(
            instance,
            self.scene_static_merged_mesh.center_ecef,
            0.0,
            0.0,
            0.0,
        )
        t1 = time.perf_counter()

        if len(updated_mesh.vertices) != instance.mesh_vertex_count:
            return False

        start = instance.mesh_vertex_start
        end = start + instance.mesh_vertex_count
        self.mesh.vertices[start:end] = updated_mesh.vertices
        t2 = time.perf_counter()

        if self.opengl_enabled and self.gl_vertex_buffer is not None:
            flat_vertices = [coord for vertex in updated_mesh.vertices for coord in vertex]
            base = start * 3
            self.gl_vertex_buffer[base : base + len(flat_vertices)] = flat_vertices
        t3 = time.perf_counter()

        instance.render_anchor_enu = (
            instance.origin_enu[0] + instance.offset_x_m,
            instance.origin_enu[1] + instance.offset_y_m,
            instance.origin_enu[2] + instance.offset_z_m,
        )
        self._record_rotation_perf("fast.build_instance_mesh", (t1 - t0) * 1000.0)
        self._record_rotation_perf("fast.patch_vertices", (t2 - t1) * 1000.0)
        self._record_rotation_perf("fast.patch_gl_buffer", (t3 - t2) * 1000.0)
        self._record_rotation_perf("fast.total", (t3 - t0) * 1000.0)
        self._maybe_report_rotation_perf()
        return True

    def _camera_z_normal(self: "BTGDisplayApp") -> Tuple[float, float, float]:
        return v_norm(rotate_camera_to_world((0.0, 0.0, 1.0), self.yaw, self.pitch))

    def _right_vector(self: "BTGDisplayApp") -> Tuple[float, float, float]:
        return v_norm(rotate_camera_to_world((1.0, 0.0, 0.0), self.yaw, self.pitch))

    def _camera_planar_nudge_axes(self: "BTGDisplayApp") -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        """Return (right, forward) axes projected onto the ENU horizontal plane."""
        center_x = 0.5 * float(self.size[0])
        center_y = 0.5 * float(self.size[1])
        _origin, view_dir = self._ray_from_screen_xy(center_x, center_y)
        forward_xy = (view_dir[0], view_dir[1], 0.0)
        if abs(forward_xy[0]) < 1e-6 and abs(forward_xy[1]) < 1e-6:
            forward_xy = (0.0, 1.0, 0.0)
        forward_xy = v_norm(forward_xy)

        # Build a planar-right vector from planar-forward so arrow directions
        # match screen-space intuition regardless of camera yaw/pitch.
        right_xy = (forward_xy[1], -forward_xy[0], 0.0)
        if abs(right_xy[0]) < 1e-6 and abs(right_xy[1]) < 1e-6:
            right_xy = (1.0, 0.0, 0.0)
        right_xy = v_norm(right_xy)
        return right_xy, forward_xy

    def _nudge_planar_axes(self: "BTGDisplayApp") -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        if self.object_nudge_camera_relative:
            return self._camera_planar_nudge_axes()
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)

    def _change_speed(self: "BTGDisplayApp", scale_mult: float) -> None:
        self.speed_scale = max(0.05, self.speed_scale * scale_mult)
        self._set_status_t(
            "status.speed_scale_fmt",
            "Speed scale: {value}",
            value=f"{self.speed_scale:.3f}",
        )

    def _set_model_angle_step_index(self: "BTGDisplayApp", new_index: int) -> None:
        if not self.model_angle_step_adjust_options:
            return
        max_index = len(self.model_angle_step_adjust_options) - 1
        self.model_angle_step_index = max(0, min(max_index, int(new_index)))
        self.model_angle_step_deg = self.model_angle_step_adjust_options[self.model_angle_step_index]

    def _adjust_model_angle_step_index(self: "BTGDisplayApp", delta_index: int) -> None:
        if not self.model_angle_step_adjust_options:
            self._set_status_t("status.no_angle_step_presets", "No angle step presets configured")
            return
        self._set_model_angle_step_index(self.model_angle_step_index + int(delta_index))
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        display_idx = self.model_angle_step_index + 1
        self._set_status_t(
            "status.model_angle_step_fmt",
            "Model angle step: {step} deg (preset {index}/{total})",
            step=self._format_angle_step_deg(self.model_angle_step_deg),
            index=display_idx,
            total=len(self.model_angle_step_adjust_options),
        )

    def _adjust_model_offsets(
        self: "BTGDisplayApp",
        pitch_delta: float = 0.0,
        roll_delta: float = 0.0,
        yaw_delta: float = 0.0,
    ) -> None:
        idx = self.selected_model_instance_index
        if idx is None or not (0 <= idx < len(self.scene_model_instances)):
            self._set_status_t("status.select_model_first", "Select a model first (crosshair + left click)")
            return

        instance = self.scene_model_instances[idx]
        instance.offset_yaw_deg += yaw_delta
        instance.offset_pitch_deg += pitch_delta
        instance.offset_roll_deg += roll_delta

        if self._update_selected_model_preview_fast(idx):
            self.fast_model_preview_pending = True
            self.fast_model_preview_deadline_ms = pygame.time.get_ticks() + 100
        elif not self._rebuild_scene_from_cache(
            keep_camera=True,
            build_textures=False,
            perf_tag="preview_fallback",
        ):
            self._reload_current_scene()
        else:
            self.fast_model_preview_pending = True
            self.fast_model_preview_deadline_ms = pygame.time.get_ticks() + 100
        self._set_status_t(
            "status.selected_offsets_fmt",
            "Selected M{index} offsets | yaw={yaw:.1f} deg pitch={pitch:.1f} deg roll={roll:.1f} deg",
            index=f"{idx:03d}",
            yaw=instance.offset_yaw_deg,
            pitch=instance.offset_pitch_deg,
            roll=instance.offset_roll_deg,
        )

    def _maybe_finalize_fast_model_preview(self: "BTGDisplayApp") -> None:
        if not self.fast_model_preview_pending:
            return
        if pygame.time.get_ticks() < self.fast_model_preview_deadline_ms:
            return
        self.fast_model_preview_pending = False
        self.fast_model_preview_deadline_ms = 0
        self.shadow_last_rebuild_ms = 0
        self._shadow_pose = None

        t0 = time.perf_counter()
        if self._rebuild_model_textured_batches_only(selected_idx=self.selected_model_instance_index):
            t1 = time.perf_counter()
            self._record_rotation_perf("preview_finalize.model_textures_only", (t1 - t0) * 1000.0)
            self._maybe_report_rotation_perf()
            return

        if not self._rebuild_scene_from_cache(
            keep_camera=True,
            build_textures=True,
            perf_tag="preview_finalize",
        ):
            self._reload_current_scene()

    def _sync_camera_view_fields_from_settings(self: "BTGDisplayApp") -> None:
        self.camera_view_fields = [
            f"{self.camera_frame_distance_factor:.2f}",
            f"{self.camera_frame_height_factor:.2f}",
        ]

    def _sync_camera_clipping_fields_from_settings(self: "BTGDisplayApp") -> None:
        far_value = self.far_clip_distance if self.far_clip_distance > 0.0 else 0.0
        self.camera_clipping_fields = [
            f"{self.near_plane:.3f}",
            f"{far_value:g}",
        ]

    def _sync_preview_panel_fields_from_settings(self: "BTGDisplayApp") -> None:
        self.preview_panel_fields = [
            str(int(round(self.preview_panel_width_px))),
            str(int(round(self.preview_panel_height_px))),
            str(int(round(self.preview_panel_x_px))),
            str(int(round(self.preview_panel_y_px))),
        ]

    def _sync_object_nudge_fields_from_settings(self: "BTGDisplayApp") -> None:
        self.object_nudge_fields = [f"{self.object_nudge_step_m:.2f}"]

    def _sync_object_nudge_repeat_fields_from_settings(self: "BTGDisplayApp") -> None:
        self.object_nudge_repeat_fields = [
            f"{self.object_nudge_repeat_delay_s:.2f}",
            f"{self.object_nudge_repeat_interval_s:.2f}",
        ]

    def _sync_missing_material_color_fields_from_settings(self: "BTGDisplayApp") -> None:
        r, g, b = self.missing_material_color_rgb
        self.missing_material_color_fields = [str(r), str(g), str(b)]

    def _sync_grid_fields_from_settings(self: "BTGDisplayApp") -> None:
        self.grid_fields = [
            f"{self.grid_size_units:g}",
            f"{self.grid_spacing_units:g}",
            f"{self.grid_z_height:g}",
        ]

    def _apply_object_nudge_fields(self: "BTGDisplayApp") -> bool:
        raw = self.object_nudge_fields[0].strip()
        if not raw:
            self._set_status_t("status.nudge_distance_empty", "Object nudge distance cannot be empty")
            return False
        try:
            value = float(raw)
        except ValueError:
            self._set_status_t(
                "status.nudge_distance_invalid_fmt",
                "Invalid object nudge value: '{value}'",
                value=self.object_nudge_fields[0],
            )
            return False

        if value <= 0.0:
            self._set_status_t("status.nudge_distance_positive", "Object nudge distance must be > 0")
            return False

        self.object_nudge_step_m = max(0.01, value)
        self.object_nudge_hold_elapsed_s = 0.0
        self.object_nudge_hold_fired = False
        self._sync_object_nudge_fields_from_settings()
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        self._set_status_t("status.nudge_step_fmt", "Object nudge step: {value} m", value=f"{self.object_nudge_step_m:.2f}")
        return True

    def _apply_object_nudge_repeat_fields(self: "BTGDisplayApp") -> bool:
        parsed: List[float] = []
        for raw in self.object_nudge_repeat_fields:
            value_text = raw.strip()
            if not value_text:
                self._set_status_t(
                    "status.nudge_repeat_fields_empty",
                    "Object nudge repeat fields cannot be empty",
                )
                return False
            try:
                parsed.append(float(value_text))
            except ValueError:
                self._set_status_t(
                    "status.nudge_repeat_invalid_fmt",
                    "Invalid object nudge repeat value: '{value}'",
                    value=raw,
                )
                return False

        if parsed[0] < 0.0 or parsed[1] <= 0.0:
            self._set_status_t(
                "status.nudge_repeat_invalid_range",
                "Repeat delay must be >= 0 and interval must be > 0",
            )
            return False

        self.object_nudge_repeat_delay_s = max(0.0, parsed[0])
        self.object_nudge_repeat_interval_s = max(0.01, parsed[1])
        self.object_nudge_hold_elapsed_s = 0.0
        self.object_nudge_hold_fired = False
        self._sync_object_nudge_repeat_fields_from_settings()
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        self._set_status_t(
            "status.nudge_repeat_applied_fmt",
            "Object nudge repeat: delay={delay:.2f}s interval={interval:.2f}s",
            delay=self.object_nudge_repeat_delay_s,
            interval=self.object_nudge_repeat_interval_s,
        )
        return True

    def _apply_missing_material_color_fields(self: "BTGDisplayApp") -> bool:
        parsed: List[int] = []
        for raw in self.missing_material_color_fields:
            value_text = raw.strip()
            if not value_text:
                self._set_status_t(
                    "status.missing_color_fields_empty",
                    "Missing material color fields cannot be empty",
                )
                return False
            try:
                value = int(value_text)
            except ValueError:
                self._set_status_t(
                    "status.missing_color_invalid_fmt",
                    "Invalid missing material color value: '{value}'",
                    value=raw,
                )
                return False
            if value < 0 or value > 255:
                self._set_status_t(
                    "status.missing_color_range",
                    "Missing material color values must be between 0 and 255",
                )
                return False
            parsed.append(value)

        self.missing_material_color_rgb = (parsed[0], parsed[1], parsed[2])
        self._sync_missing_material_color_fields_from_settings()
        if self.mesh is not None:
            self._build_face_data(self.mesh)
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        self._set_status_t(
            "status.missing_color_applied_fmt",
            "Missing material color: RGB({r}, {g}, {b})",
            r=self.missing_material_color_rgb[0],
            g=self.missing_material_color_rgb[1],
            b=self.missing_material_color_rgb[2],
        )
        return True

    def _adjust_object_nudge_step(self: "BTGDisplayApp", delta_m: float) -> None:
        new_value = max(0.01, self.object_nudge_step_m + delta_m)
        self.object_nudge_step_m = new_value
        self.object_nudge_hold_elapsed_s = 0.0
        self.object_nudge_hold_fired = False
        self._sync_object_nudge_fields_from_settings()
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        self._set_status_t("status.nudge_step_fmt", "Object nudge step: {value} m", value=f"{self.object_nudge_step_m:.2f}")

    def _apply_camera_view_fields(self: "BTGDisplayApp") -> bool:
        parsed: List[float] = []
        for raw in self.camera_view_fields:
            value_text = raw.strip()
            if not value_text:
                self._set_status_t("status.camera_view_fields_empty", "Camera view fields cannot be empty")
                return False
            try:
                parsed.append(float(value_text))
            except ValueError:
                self._set_status_t(
                    "status.camera_view_invalid_fmt",
                    "Invalid camera view value: '{value}'",
                    value=raw,
                )
                return False

        if parsed[0] <= 0.05 or parsed[1] < 0.0:
            self._set_status_t(
                "status.camera_view_invalid_range",
                "Camera distance must be > 0.05 and height must be >= 0.0",
            )
            return False

        self.camera_frame_distance_factor = parsed[0]
        self.camera_frame_height_factor = parsed[1]
        if self.mesh:
            self._reset_camera_to_mesh(self.mesh)
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        self._set_status_t(
            "status.camera_view_applied_fmt",
            "Camera start view | distance={distance:.2f} height={height:.2f}",
            distance=self.camera_frame_distance_factor,
            height=self.camera_frame_height_factor,
        )
        return True

    def _apply_camera_clipping_fields(self: "BTGDisplayApp") -> bool:
        parsed: List[float] = []
        for raw in self.camera_clipping_fields:
            value_text = raw.strip()
            if not value_text:
                self._set_status_t(
                    "status.camera_clipping_fields_empty",
                    "Camera clipping fields cannot be empty",
                )
                return False
            try:
                parsed.append(float(value_text))
            except ValueError:
                self._set_status_t(
                    "status.camera_clipping_invalid_fmt",
                    "Invalid camera clipping value: '{value}'",
                    value=raw,
                )
                return False

        near_clip = parsed[0]
        far_clip = parsed[1]
        if near_clip <= 0.0:
            self._set_status_t("status.camera_near_positive", "Near clip must be > 0")
            return False
        if far_clip < 0.0:
            self._set_status_t("status.camera_far_non_negative", "Far clip must be >= 0 (use 0 for auto)")
            return False
        if far_clip > 0.0 and far_clip <= near_clip:
            self._set_status_t("status.camera_far_gt_near", "Far clip must be greater than near clip")
            return False

        self.near_plane = max(0.01, near_clip)
        self.far_clip_distance = max(0.0, far_clip)
        self._sync_camera_clipping_fields_from_settings()
        try:
            self._persist_viewer_config()
        except Exception:
            pass

        far_text = f"{self.far_clip_distance:g}m" if self.far_clip_distance > 0.0 else "auto"
        self._set_status_t(
            "status.camera_clipping_applied_fmt",
            "Camera clipping | near={near}m far={far}",
            near=f"{self.near_plane:.3f}",
            far=far_text,
        )
        return True

    def _apply_preview_panel_fields(self: "BTGDisplayApp") -> bool:
        parsed: List[float] = []
        for raw in self.preview_panel_fields:
            value_text = raw.strip()
            if not value_text:
                self._set_status_t("status.preview_panel_fields_empty", "Preview panel fields cannot be empty")
                return False
            try:
                parsed.append(float(value_text))
            except ValueError:
                self._set_status_t(
                    "status.preview_panel_invalid_fmt",
                    "Invalid preview panel value: '{value}'",
                    value=raw,
                )
                return False

        width, height, pos_x, pos_y = parsed
        if width < 120.0 or height < 100.0:
            self._set_status_t(
                "status.preview_panel_min_size",
                "Preview panel width must be >= 120 and height must be >= 100",
            )
            return False
        if pos_x < 0.0 or pos_y < 0.0:
            self._set_status_t("status.preview_panel_xy_non_negative", "Preview panel X/Y must be >= 0")
            return False

        self.preview_panel_width_px = width
        self.preview_panel_height_px = height
        self.preview_panel_x_px = pos_x
        self.preview_panel_y_px = pos_y
        self._sync_preview_panel_fields_from_settings()
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        self._set_status_t(
            "status.preview_panel_applied_fmt",
            "Preview panel updated | size=({width}x{height}) pos=({x},{y})",
            width=int(round(self.preview_panel_width_px)),
            height=int(round(self.preview_panel_height_px)),
            x=int(round(self.preview_panel_x_px)),
            y=int(round(self.preview_panel_y_px)),
        )
        return True

    def _apply_grid_fields(self: "BTGDisplayApp") -> bool:
        parsed: List[float] = []
        for raw in self.grid_fields:
            value_text = raw.strip()
            if not value_text:
                self._set_status_t("status.grid_fields_empty", "Grid settings fields cannot be empty")
                return False
            try:
                parsed.append(float(value_text))
            except ValueError:
                self._set_status_t(
                    "status.grid_invalid_fmt",
                    "Invalid grid value: '{value}'",
                    value=raw,
                )
                return False

        size_units, spacing_units, z_height = parsed
        if size_units <= 0.0:
            self._set_status_t("status.grid_size_positive", "Grid size must be > 0")
            return False
        if spacing_units <= 0.0:
            self._set_status_t("status.grid_spacing_positive", "Grid spacing must be > 0")
            return False

        self.grid_size_units = max(1.0, size_units)
        self.grid_spacing_units = max(0.01, spacing_units)
        self.grid_z_height = z_height
        self._sync_grid_fields_from_settings()
        try:
            self._persist_viewer_config()
        except Exception:
            pass
        self._set_status_t(
            "status.grid_applied_fmt",
            "Grid updated | size={size}m spacing={spacing}m z={z}m",
            size=f"{self.grid_size_units:g}",
            spacing=f"{self.grid_spacing_units:g}",
            z=f"{self.grid_z_height:g}",
        )
        return True

    def _handle_events(self: "BTGDisplayApp", dt: float) -> None:
        del dt
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            elif event.type == pygame.VIDEORESIZE:
                self.size = (max(320, event.w), max(240, event.h))
                self.screen = pygame.display.set_mode(self.size, self._display_flags)

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE or (
                    event.key == pygame.K_BACKSPACE and self.menu_mode != "file_browser_create_folder"
                ):
                    if not self.show_menu:
                        self._open_menu()
                    else:
                        if self.menu_mode == "bindings" and self.binding_capture_action:
                            self.binding_capture_action = None
                            self._set_status_t("status.binding_update_cancelled", "Binding update cancelled")
                        elif self.menu_mode == "save_confirm":
                            self._dismiss_save_confirm_dialog()
                        elif self.menu_mode == "package_summary":
                            self._dismiss_package_summary_dialog()
                        elif self.menu_mode == "file_browser_create_folder":
                            self._dismiss_file_browser_create_folder_prompt()
                        elif self.menu_mode == "file_browser_overwrite_confirm":
                            self.file_browser_overwrite_target = ""
                            self.menu_mode = "file_browser"
                        elif self.menu_mode == "add_object_files":
                            self._clear_add_object_preview()
                            self.menu_mode = "add_object_cats"
                        elif self.menu_mode == "file_browser" and self.file_browser_mode == "directory_select":
                            self.file_browser_mode = ""
                            self.file_browser_directory_action = ""
                            self.menu_mode = self.file_browser_return_mode or "main"
                        elif self._navigate_main_menu_back():
                            pass
                        elif self.menu_mode in (
                            "bindings",
                            "camera_view",
                            "camera_clipping",
                            "grid_settings",
                            "preview_panel_location",
                            "object_nudge",
                            "object_nudge_repeat",
                            "missing_material_color",
                            "custom_scenery",
                            "add_object_cats",
                            "file_browser",
                        ):
                            self.menu_mode = "main"
                        else:
                            self._close_menu()
                    continue

                normalized_mods = self._normalized_mods(getattr(event, "mod", 0))
                if event.key == pygame.K_SPACE and not self.show_menu and normalized_mods == 0:
                    self._open_add_object_menu()
                    continue

                if self.show_menu:
                    self._handle_menu_keydown(event)
                    continue

                if self.binding_matches_event("speed_down", event) or event.key == pygame.K_KP_MINUS:
                    self._change_speed(0.8)
                elif self.binding_matches_event("speed_up", event) or event.key in (pygame.K_PLUS, pygame.K_KP_PLUS):
                    self._change_speed(1.25)
                elif self.binding_matches_event("model_yaw_down", event):
                    self._adjust_model_offsets(yaw_delta=-self.model_angle_step_deg)
                elif self.binding_matches_event("model_yaw_up", event):
                    self._adjust_model_offsets(yaw_delta=self.model_angle_step_deg)
                elif self.binding_matches_event("model_roll_down", event):
                    self._adjust_model_offsets(roll_delta=-self.model_angle_step_deg)
                elif self.binding_matches_event("model_roll_up", event):
                    self._adjust_model_offsets(roll_delta=self.model_angle_step_deg)
                elif self.binding_matches_event("model_pitch_up", event):
                    self._adjust_model_offsets(pitch_delta=self.model_angle_step_deg)
                elif self.binding_matches_event("model_pitch_down", event):
                    self._adjust_model_offsets(pitch_delta=-self.model_angle_step_deg)
                elif event.key == pygame.K_PERIOD:
                    if self.mesh:
                        self._reset_camera_to_mesh(self.mesh)
                        self._set_status_t("status.reframed_tile", "Reframed tile in view")
                    else:
                        self._set_status_t("status.reframe_no_tile", "No loaded tile to reframe")
                elif event.key == pygame.K_LEFTBRACKET:
                    if normalized_mods & pygame.KMOD_SHIFT:
                        self._adjust_model_angle_step_index(-1)
                    else:
                        self._adjust_object_nudge_step(-0.25)
                elif event.key == pygame.K_RIGHTBRACKET:
                    if normalized_mods & pygame.KMOD_SHIFT:
                        self._adjust_model_angle_step_index(1)
                    else:
                        self._adjust_object_nudge_step(0.25)

                if event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
                    move_step_m = self.object_nudge_step_m
                    yaw_step_deg = self.model_angle_step_deg
                    if normalized_mods & pygame.KMOD_SHIFT:
                        if event.key == pygame.K_UP:
                            self._adjust_selected_model_transform(dz_m=move_step_m)
                        elif event.key == pygame.K_DOWN:
                            self._adjust_selected_model_transform(dz_m=-move_step_m)
                        elif event.key == pygame.K_LEFT:
                            self._adjust_selected_model_transform(dyaw_deg=-yaw_step_deg)
                        elif event.key == pygame.K_RIGHT:
                            self._adjust_selected_model_transform(dyaw_deg=yaw_step_deg)
                    else:
                        right_axis, forward_axis = self._nudge_planar_axes()
                        if event.key == pygame.K_LEFT:
                            self._adjust_selected_model_transform(
                                dx_m=-right_axis[0] * move_step_m,
                                dy_m=-right_axis[1] * move_step_m,
                            )
                        elif event.key == pygame.K_RIGHT:
                            self._adjust_selected_model_transform(
                                dx_m=right_axis[0] * move_step_m,
                                dy_m=right_axis[1] * move_step_m,
                            )
                        elif event.key == pygame.K_UP:
                            self._adjust_selected_model_transform(
                                dx_m=forward_axis[0] * move_step_m,
                                dy_m=forward_axis[1] * move_step_m,
                            )
                        elif event.key == pygame.K_DOWN:
                            self._adjust_selected_model_transform(
                                dx_m=-forward_axis[0] * move_step_m,
                                dy_m=-forward_axis[1] * move_step_m,
                            )

                if event.key == pygame.K_h:
                    self.show_help = not self.show_help
                    state = self._menu_t("state.on", "ON") if self.show_help else self._menu_t("state.off", "OFF")
                    self._set_status_t("status.help_overlay_fmt", "Help overlay: {state}", state=state)
                elif event.key == pygame.K_t:
                    self.show_debug = not self.show_debug
                    state = self._menu_t("state.on", "ON") if self.show_debug else self._menu_t("state.off", "OFF")
                    self._set_status_t("status.debug_overlay_fmt", "Debug overlay: {state}", state=state)
                elif event.key == pygame.K_y:
                    self.textured_mode = not self.textured_mode
                    try:
                        self._persist_viewer_config()
                    except Exception:
                        pass
                    state = self._menu_t("state.on", "ON") if self.textured_mode else self._menu_t("state.off", "OFF")
                    self._set_status_t("status.textured_view_fmt", "Textured view: {state}", state=state)
                elif event.key == pygame.K_TAB:
                    self.wireframe_mode = not self.wireframe_mode
                    mode = self._menu_t("render.wireframe", "Wireframe") if self.wireframe_mode else self._menu_t("render.solid", "Solid")
                    self._set_status_t("status.render_mode_fmt", "Render mode: {mode}", mode=mode)

                if self.binding_matches_event("toggle_labels", event):
                    self.show_model_labels = not self.show_model_labels
                    state = self._menu_t("state.on", "ON") if self.show_model_labels else self._menu_t("state.off", "OFF")
                    self._set_status_t("status.model_labels_fmt", "Model labels: {state}", state=state)

                if self.binding_matches_event("toggle_perf_debug", event):
                    self.rotation_perf_debug_enabled = not self.rotation_perf_debug_enabled
                    state = self._menu_t("state.on", "ON") if self.rotation_perf_debug_enabled else self._menu_t("state.off", "OFF")
                    self._set_status_t("status.rotation_perf_logging_fmt", "Rotation perf logging: {state}", state=state)

                if self.binding_matches_event("toggle_mouse", event):
                    self.set_mouse_capture(not self.mouse_captured)
                    state = self._menu_t("state.on", "ON") if self.mouse_captured else self._menu_t("state.off", "OFF")
                    self._set_status_t("status.mouse_capture_fmt", "Mouse capture: {state}", state=state)

                if self.binding_matches_event("save_stg", event):
                    self.save_stg_file()

                if self.binding_matches_event("copy_object", event):
                    idx = self.selected_model_instance_index
                    if idx is not None and 0 <= idx < len(self.scene_model_instances):
                        inst = self.scene_model_instances[idx]
                        self.clipboard_instance = STGModelInstance(
                            template_mesh=inst.template_mesh,
                            origin_enu=inst.origin_enu,
                            heading_deg=inst.heading_deg,
                            pitch_deg=inst.pitch_deg,
                            roll_deg=inst.roll_deg,
                            is_ac_model=inst.is_ac_model,
                            source_path=inst.source_path,
                            stg_directive=inst.stg_directive,
                            stg_entry_index=inst.stg_entry_index,
                            render_anchor_enu=inst.render_anchor_enu,
                            offset_yaw_deg=inst.offset_yaw_deg,
                            offset_pitch_deg=inst.offset_pitch_deg,
                            offset_roll_deg=inst.offset_roll_deg,
                            offset_x_m=inst.offset_x_m,
                            offset_y_m=inst.offset_y_m,
                            offset_z_m=inst.offset_z_m,
                        )
                        kind = "ac" if inst.is_ac_model else "model"
                        self._set_status_t(
                            "status.copied_object_fmt",
                            "Copied M{index} [{kind}] {path}",
                            index=f"{idx:03d}",
                            kind=kind,
                            path=inst.source_path,
                        )
                    else:
                        self._set_status_t("status.no_model_selected_copy", "No model selected to copy")

                if self.binding_matches_event("paste_object", event):
                    self._paste_copied_object()

                if self.binding_matches_event("delete_object", event):
                    self._delete_selected_object()

                if self.binding_matches_event("cycle_object_prev", event):
                    self._cycle_selected_object_catalog(-1)

                if self.binding_matches_event("cycle_object_next", event):
                    self._cycle_selected_object_catalog(1)

            elif event.type == pygame.MOUSEMOTION and self.mouse_captured and not self.show_menu:
                dx, dy = event.rel
                self.yaw -= dx * self.mouse_sensitivity
                self.pitch -= dy * self.mouse_sensitivity

            elif event.type == pygame.MOUSEMOTION and self.show_menu:
                self._handle_menu_mousemotion(event)

            elif event.type == pygame.MOUSEMOTION and not self.mouse_captured and not self.show_menu:
                if not self.mouse_edit_drag_active:
                    continue

                buttons = getattr(event, "buttons", None)
                left_pressed = False
                if isinstance(buttons, (tuple, list)) and len(buttons) > 0:
                    left_pressed = bool(buttons[0])
                if not left_pressed:
                    pressed_now = pygame.mouse.get_pressed()
                    left_pressed = bool(pressed_now[0]) if pressed_now else False
                if not left_pressed:
                    self._reset_mouse_edit_drag_state()
                    continue

                rel_x, rel_y = event.rel
                if rel_x == 0 and rel_y == 0:
                    continue

                apply_x = float(rel_x)
                apply_y = float(rel_y)
                if not self.mouse_edit_drag_started:
                    self.mouse_edit_drag_accum_px_x += apply_x
                    self.mouse_edit_drag_accum_px_y += apply_y
                    drag_amount = abs(self.mouse_edit_drag_accum_px_x) + abs(self.mouse_edit_drag_accum_px_y)
                    if drag_amount < self.mouse_edit_drag_threshold_px:
                        continue
                    self.mouse_edit_drag_started = True
                    apply_x = self.mouse_edit_drag_accum_px_x
                    apply_y = self.mouse_edit_drag_accum_px_y
                    self.mouse_edit_drag_accum_px_x = 0.0
                    self.mouse_edit_drag_accum_px_y = 0.0

                shift_mode = bool(self._normalized_mods(pygame.key.get_mods()) & pygame.KMOD_SHIFT)
                self._apply_selected_model_mouse_drag(apply_x, apply_y, shift_mode)

            elif event.type == pygame.MOUSEWHEEL and not self.show_menu:
                if event.y > 0:
                    for _ in range(int(event.y)):
                        self._change_speed(1.25)
                elif event.y < 0:
                    for _ in range(int(-event.y)):
                        self._change_speed(0.8)

            elif event.type == pygame.MOUSEBUTTONDOWN and self.show_menu:
                self._handle_menu_mousebuttondown(event)

            elif event.type == pygame.MOUSEBUTTONDOWN and not self.show_menu:
                if event.button == 1:
                    idx: Optional[int] = None
                    if not self.mouse_captured:
                        click_x, click_y = getattr(event, "pos", pygame.mouse.get_pos())
                        idx = self._hover_model_index_at_screen(float(click_x), float(click_y))
                    if idx is None:
                        idx = self.crosshair_hover_model_index
                    if idx is not None and 0 <= idx < len(self.scene_model_instances):
                        self.selected_model_instance_index = idx
                        inst = self.scene_model_instances[idx]
                        kind = "ac" if inst.is_ac_model else "model"
                        self._set_status_t(
                            "status.selected_object_fmt",
                            "Selected model M{index} [{kind}] {path}",
                            index=f"{idx:03d}",
                            kind=kind,
                            path=inst.source_path,
                        )
                        if not self.mouse_captured:
                            self.mouse_edit_drag_active = True
                            self.mouse_edit_drag_started = False
                            self.mouse_edit_drag_accum_px_x = 0.0
                            self.mouse_edit_drag_accum_px_y = 0.0
                    elif (
                        not self.mouse_captured
                        and self.selected_model_instance_index is not None
                        and 0 <= self.selected_model_instance_index < len(self.scene_model_instances)
                    ):
                        # Allow drag-to-edit on the currently selected model even when the
                        # click starts away from the label anchor.
                        self.mouse_edit_drag_active = True
                        self.mouse_edit_drag_started = False
                        self.mouse_edit_drag_accum_px_x = 0.0
                        self.mouse_edit_drag_accum_px_y = 0.0
                    else:
                        self.selected_model_instance_index = None
                        self._set_status_t("status.no_model_under_crosshair", "No model under crosshair")
                        self._reset_mouse_edit_drag_state()
                elif event.button == 3:
                    self._reset_mouse_edit_drag_state()
                    self.set_mouse_capture(not self.mouse_captured)
                    state = self._menu_t("state.on", "ON") if self.mouse_captured else self._menu_t("state.off", "OFF")
                    self._set_status_t("status.mouse_capture_fmt", "Mouse capture: {state}", state=state)
                elif event.button == 4:
                    self._change_speed(1.25)
                elif event.button == 5:
                    self._change_speed(0.8)

            elif event.type == pygame.MOUSEBUTTONUP and not self.show_menu:
                if event.button == 1:
                    self._flush_pending_mouse_drag_transform(emit_status=True)
                    self._reset_mouse_edit_drag_state()

    def _update_movement(self: "BTGDisplayApp", dt: float) -> None:
        self._maybe_finalize_fast_model_preview()
        if self.mouse_edit_drag_active:
            self._flush_pending_mouse_drag_transform(emit_status=False)
        elif (
            abs(self.mouse_edit_pending_dx_m) > 1e-9
            or abs(self.mouse_edit_pending_dy_m) > 1e-9
            or abs(self.mouse_edit_pending_dz_m) > 1e-9
            or abs(self.mouse_edit_pending_dyaw_deg) > 1e-9
        ):
            self._flush_pending_mouse_drag_transform(emit_status=False)

        if self.show_menu:
            return

        pressed = pygame.key.get_pressed()
        mods = pygame.key.get_mods()
        normalized_mods = self._normalized_mods(mods)
        speed = self.base_speed * self.speed_scale

        forward = self._camera_z_normal()
        right = self._right_vector()

        dx = dy = dz = 0.0
        if self.is_binding_active("forward", pressed, normalized_mods):
            dx -= forward[0]
            dy -= forward[1]
            dz -= forward[2]
        if self.is_binding_active("backward", pressed, normalized_mods):
            dx += forward[0]
            dy += forward[1]
            dz += forward[2]
        if self.is_binding_active("left", pressed, normalized_mods):
            dx -= right[0]
            dy -= right[1]
        if self.is_binding_active("right", pressed, normalized_mods):
            dx += right[0]
            dy += right[1]
        if self.is_binding_active("up", pressed, normalized_mods):
            dz += 1.0
        if self.is_binding_active("down", pressed, normalized_mods):
            dz -= 1.0

        move = v_norm((dx, dy, dz)) if (dx or dy or dz) else (0.0, 0.0, 0.0)
        self.camera_pos[0] += move[0] * speed * dt
        self.camera_pos[1] += move[1] * speed * dt
        self.camera_pos[2] += move[2] * speed * dt

        if self.selected_model_instance_index is None:
            self.object_nudge_hold_elapsed_s = 0.0
            self.object_nudge_hold_fired = False
            return

        arrow_left = bool(pressed[pygame.K_LEFT])
        arrow_right = bool(pressed[pygame.K_RIGHT])
        arrow_up = bool(pressed[pygame.K_UP])
        arrow_down = bool(pressed[pygame.K_DOWN])
        any_arrow = arrow_left or arrow_right or arrow_up or arrow_down
        if not any_arrow:
            self.object_nudge_hold_elapsed_s = 0.0
            self.object_nudge_hold_fired = False
            return

        self.object_nudge_hold_elapsed_s += dt
        threshold = (
            self.object_nudge_repeat_interval_s
            if self.object_nudge_hold_fired
            else self.object_nudge_repeat_delay_s
        )
        if self.object_nudge_hold_elapsed_s < threshold:
            return

        self.object_nudge_hold_elapsed_s = 0.0
        self.object_nudge_hold_fired = True

        move_step_m = self.object_nudge_step_m
        yaw_step_deg = self.model_angle_step_deg
        if normalized_mods & pygame.KMOD_SHIFT:
            if arrow_up:
                self._adjust_selected_model_transform(dz_m=move_step_m)
            if arrow_down:
                self._adjust_selected_model_transform(dz_m=-move_step_m)
            if arrow_left:
                self._adjust_selected_model_transform(dyaw_deg=-yaw_step_deg)
            if arrow_right:
                self._adjust_selected_model_transform(dyaw_deg=yaw_step_deg)
        else:
            right_axis, forward_axis = self._nudge_planar_axes()
            if arrow_left:
                self._adjust_selected_model_transform(
                    dx_m=-right_axis[0] * move_step_m,
                    dy_m=-right_axis[1] * move_step_m,
                )
            if arrow_right:
                self._adjust_selected_model_transform(
                    dx_m=right_axis[0] * move_step_m,
                    dy_m=right_axis[1] * move_step_m,
                )
            if arrow_up:
                self._adjust_selected_model_transform(
                    dx_m=forward_axis[0] * move_step_m,
                    dy_m=forward_axis[1] * move_step_m,
                )
            if arrow_down:
                self._adjust_selected_model_transform(
                    dx_m=-forward_axis[0] * move_step_m,
                    dy_m=-forward_axis[1] * move_step_m,
                )
