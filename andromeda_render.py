#!/usr/bin/env python3
"""Rendering helpers for BTG display app."""

from __future__ import annotations

from collections import OrderedDict
import math
import os
import re
import struct
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import pygame

from andromeda_backend import (
    BTGMesh,
    material_color_from_face,
    resolve_texture_path,
    v_cross,
    v_dot,
    v_norm,
    v_sub,
)

if TYPE_CHECKING:
    from andromeda import BTGDisplayApp


def rotate_world_to_camera(v: Tuple[float, float, float], yaw_deg: float, pitch_deg: float) -> Tuple[float, float, float]:
    yaw = math.radians(-yaw_deg)
    pitch = math.radians(-pitch_deg)

    x, y, z = v

    cy = math.cos(yaw)
    sy = math.sin(yaw)
    x1 = x * cy - y * sy
    y1 = x * sy + y * cy
    z1 = z

    cp = math.cos(pitch)
    sp = math.sin(pitch)
    x2 = x1
    y2 = y1 * cp - z1 * sp
    z2 = y1 * sp + z1 * cp

    return (x2, y2, z2)


def rotate_camera_to_world(v_cam: Tuple[float, float, float], yaw_deg: float, pitch_deg: float) -> Tuple[float, float, float]:
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)

    x, y, z = v_cam

    cp = math.cos(pitch)
    sp = math.sin(pitch)
    x1 = x
    y1 = y * cp - z * sp
    z1 = y * sp + z * cp

    cy = math.cos(yaw)
    sy = math.sin(yaw)
    x2 = x1 * cy - y1 * sy
    y2 = x1 * sy + y1 * cy
    z2 = z1

    return (x2, y2, z2)


def draw_gradient_background(screen: pygame.Surface) -> None:
    w, h = screen.get_size()
    top = (10, 15, 24)
    bottom = (28, 36, 52)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] * (1.0 - t) + bottom[0] * t)
        g = int(top[1] * (1.0 - t) + bottom[1] * t)
        b = int(top[2] * (1.0 - t) + bottom[2] * t)
        pygame.draw.line(screen, (r, g, b), (0, y), (w, y))


def _sgi_unpack_rle_row_8(data: bytes, start: int, expected_len: int) -> Optional[bytes]:
    out = bytearray()
    i = start
    n = len(data)
    while i < n and len(out) < expected_len:
        ctrl = data[i]
        i += 1
        count = ctrl & 0x7F
        if count == 0:
            break
        if ctrl & 0x80:
            if i + count > n:
                return None
            out.extend(data[i : i + count])
            i += count
        else:
            if i >= n:
                return None
            out.extend([data[i]] * count)
            i += 1
    if len(out) < expected_len:
        out.extend(b"\x00" * (expected_len - len(out)))
    return bytes(out[:expected_len])

def _sgi_to_rgba(path: str) -> Optional[Tuple[int, int, bytes]]:
    """Decode SGI RGB/RGBA (.rgb/.rgba/.sgi) into RGBA bytes.

    Returns (width, height, rgba_bytes_flipped_for_gl) or None on decode failure.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except Exception:
        return None

    if len(raw) < 512:
        return None

    magic = struct.unpack(">H", raw[0:2])[0]
    if magic != 474:  # 0x01DA
        return None

    storage = raw[2]
    bpc = raw[3]
    if bpc != 1:
        return None

    _dimension, xsize, ysize, zsize = struct.unpack(">HHHH", raw[4:12])
    if xsize <= 0 or ysize <= 0 or zsize <= 0:
        return None

    channels = int(zsize)
    row_count = int(ysize)
    width = int(xsize)
    plane_rows = [bytearray(width) for _ in range(row_count * channels)]

    if storage == 0:
        offset = 512
        row_len = width
        total_needed = row_count * channels * row_len
        if offset + total_needed > len(raw):
            return None
        for z in range(channels):
            for y in range(row_count):
                idx = z * row_count + y
                plane_rows[idx][:] = raw[offset : offset + row_len]
                offset += row_len
    elif storage == 1:
        table_len = row_count * channels
        starts_off = 512
        sizes_off = starts_off + table_len * 4
        data_off = sizes_off + table_len * 4
        if data_off > len(raw):
            return None
        starts = struct.unpack(f">{table_len}I", raw[starts_off:sizes_off])
        sizes = struct.unpack(f">{table_len}I", raw[sizes_off:data_off])

        for idx in range(table_len):
            start = int(starts[idx])
            size = int(sizes[idx])
            if start <= 0 or size <= 0 or start + size > len(raw):
                return None
            unpacked = _sgi_unpack_rle_row_8(raw, start, width)
            if unpacked is None:
                return None
            plane_rows[idx][:] = unpacked
    else:
        return None

    rgba = bytearray(width * row_count * 4)
    for y in range(row_count):
        r_row = plane_rows[0 * row_count + y] if channels >= 1 else None
        g_row = plane_rows[1 * row_count + y] if channels >= 2 else r_row
        b_row = plane_rows[2 * row_count + y] if channels >= 3 else r_row
        a_row = plane_rows[3 * row_count + y] if channels >= 4 else None
        for x in range(width):
            dst = (y * width + x) * 4
            r = r_row[x] if r_row is not None else 0
            g = g_row[x] if g_row is not None else r
            b = b_row[x] if b_row is not None else r
            a = a_row[x] if a_row is not None else 255
            rgba[dst] = r
            rgba[dst + 1] = g
            rgba[dst + 2] = b
            rgba[dst + 3] = a

    # Match pygame.image.tostring(..., 'RGBA', True): vertically flipped for GL upload.
    row_stride = width * 4
    flipped = bytearray(len(rgba))
    for y in range(row_count):
        src = y * row_stride
        dst = (row_count - 1 - y) * row_stride
        flipped[dst : dst + row_stride] = rgba[src : src + row_stride]

    return width, row_count, bytes(flipped)


class RenderMixin:
    def _ensure_gl_text_cache(self: "BTGDisplayApp") -> None:
        if not hasattr(self, "_gl_text_cache"):
            self._gl_text_cache: "OrderedDict[Tuple[int, str, int, int, int], Tuple[int, int, int]]" = OrderedDict()
        if not hasattr(self, "_gl_text_cache_max_entries"):
            self._gl_text_cache_max_entries = 512

    def _purge_gl_text_cache(self: "BTGDisplayApp") -> None:
        if not hasattr(self, "_gl_text_cache"):
            return
        GLM = self.GL
        if GLM is None:
            self._gl_text_cache.clear()
            return
        for tex_id, _w, _h in self._gl_text_cache.values():
            try:
                GLM.glDeleteTextures([int(tex_id)])
            except Exception:
                pass
        self._gl_text_cache.clear()

    def _gl_upload_text_texture(
        self: "BTGDisplayApp",
        text: str,
        color: Tuple[int, int, int],
        font: pygame.font.Font,
    ) -> Optional[Tuple[int, int, int]]:
        GLM = self.GL
        if GLM is None:
            return None

        surf = font.render(text, True, color)
        w, h = surf.get_size()
        if w <= 0 or h <= 0:
            return None
        data = pygame.image.tostring(surf, "RGBA", True)

        tex_id = GLM.glGenTextures(1)
        GLM.glBindTexture(GLM.GL_TEXTURE_2D, tex_id)
        GLM.glTexParameteri(GLM.GL_TEXTURE_2D, GLM.GL_TEXTURE_MIN_FILTER, GLM.GL_LINEAR)
        GLM.glTexParameteri(GLM.GL_TEXTURE_2D, GLM.GL_TEXTURE_MAG_FILTER, GLM.GL_LINEAR)
        if hasattr(GLM, "GL_CLAMP_TO_EDGE"):
            GLM.glTexParameteri(GLM.GL_TEXTURE_2D, GLM.GL_TEXTURE_WRAP_S, GLM.GL_CLAMP_TO_EDGE)
            GLM.glTexParameteri(GLM.GL_TEXTURE_2D, GLM.GL_TEXTURE_WRAP_T, GLM.GL_CLAMP_TO_EDGE)
        GLM.glTexImage2D(
            GLM.GL_TEXTURE_2D,
            0,
            GLM.GL_RGBA,
            w,
            h,
            0,
            GLM.GL_RGBA,
            GLM.GL_UNSIGNED_BYTE,
            data,
        )
        GLM.glBindTexture(GLM.GL_TEXTURE_2D, 0)
        return (int(tex_id), int(w), int(h))

    def _gl_draw_textured_quad(
        self: "BTGDisplayApp",
        tex_id: int,
        x: float,
        y_top: float,
        w: int,
        h: int,
    ) -> None:
        GLM = self.GL
        if GLM is None:
            return
        y = self.size[1] - y_top - h
        GLM.glEnable(GLM.GL_TEXTURE_2D)
        GLM.glBindTexture(GLM.GL_TEXTURE_2D, tex_id)
        GLM.glColor4f(1.0, 1.0, 1.0, 1.0)
        GLM.glBegin(GLM.GL_QUADS)
        GLM.glTexCoord2f(0.0, 0.0)
        GLM.glVertex2f(x, y)
        GLM.glTexCoord2f(1.0, 0.0)
        GLM.glVertex2f(x + w, y)
        GLM.glTexCoord2f(1.0, 1.0)
        GLM.glVertex2f(x + w, y + h)
        GLM.glTexCoord2f(0.0, 1.0)
        GLM.glVertex2f(x, y + h)
        GLM.glEnd()
        GLM.glBindTexture(GLM.GL_TEXTURE_2D, 0)
        GLM.glDisable(GLM.GL_TEXTURE_2D)

    def _default_help_overlay_lines(self: "BTGDisplayApp") -> List[str]:
        return [
            "Controls",
            "{bind:forward}: camera +Z normal",
            "{bind:backward}: camera -Z normal",
            "{bind:left}: left",
            "{bind:right}: right",
            "{bind:up}: up",
            "{bind:down}: down",
            "{bind:speed_down}: speed down",
            "{bind:speed_up}/plus: speed up",
            "mouse wheel up/down: speed up/down",
            "{bind:model_yaw_down}/{bind:model_yaw_up}: model yaw -/+",
            "{bind:model_roll_down}/{bind:model_roll_up}: model roll -/+",
            "{bind:model_pitch_down}/{bind:model_pitch_up}: model pitch -/+",
            "{bind:toggle_labels}: labels on/off",
            "{bind:toggle_perf_debug}: perf logging on/off",
            "right mouse button: toggle mouse capture (fly/no-fly)",
            "{bind:toggle_mouse}: toggle mouse capture (keyboard)",
            "{bind:save_stg}: save STG",
            "{bind:copy_object}: copy selected object",
            "{bind:paste_object}: paste copied object",
            "{bind:delete_object}: delete selected object",
            "{bind:cycle_object_prev}/{bind:cycle_object_next}: cycle catalog item",
            "ctrl+pgup / ctrl+pgdn: angle step preset -/+",
            "[: nudge step -0.25 m",
            "]: nudge step +0.25 m",
            "shift+[ / shift+]: angle step preset -/+",
            "arrow keys: move selected object X/Y",
            "shift+up/down: move selected object Z",
            "shift+left/right: selected object yaw -/+",
            "mouse mode (capture OFF): crosshair follows mouse cursor",
            "left drag (mouse mode): move selected object X/Y",
            "shift+left drag: mouse Y -> object Z, mouse X -> object yaw",
            "space: add object menu",
            "menu > Flightgear Location: set texture root",
            "esc: menu",
            ".: zoom/reframe tile",
            "tab: wireframe toggle",
            "h: help overlay toggle",
            "y: textured view toggle",
            "t: texture debug overlay",
        ]

    def _help_line_with_bindings(self: "BTGDisplayApp", line: str) -> str:
        def replace(match: "re.Match[str]") -> str:
            action = match.group(1)
            binding = self.bindings.get(action)
            if binding is None:
                return match.group(0)
            return self.format_binding(binding)

        return re.sub(r"\{bind:([a-zA-Z0-9_]+)\}", replace, line)

    def _load_help_text_file(
        self: "BTGDisplayApp",
        file_path: str,
        *,
        persist: bool = True,
        silent: bool = False,
    ) -> bool:
        candidate = os.path.abspath(file_path)
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                loaded_lines = [line.rstrip("\r\n") for line in handle]
        except Exception as exc:
            if not silent:
                self._set_status_t(
                    "status.help_text_load_failed_fmt",
                    "Help text load failed: {error}",
                    error=exc,
                )
            return False

        parsed = [line.strip() for line in loaded_lines if line.strip() and not line.lstrip().startswith("#")]
        if not parsed:
            if not silent:
                self._set_status_t(
                    "status.help_text_empty",
                    "Help text load failed: file has no usable lines",
                )
            return False

        self.help_text_file_path = candidate
        self.help_overlay_text_lines = parsed
        if persist:
            try:
                self._persist_viewer_config()
            except Exception:
                pass
        if not silent:
            self._set_status_t(
                "status.help_text_loaded_fmt",
                "Help text file loaded: {path}",
                path=candidate,
            )
        return True

    def _preview_panel_rect(self: "BTGDisplayApp") -> pygame.Rect:
        max_w = max(120, self.size[0] - 20)
        max_h = max(100, self.size[1] - 20)
        panel_w = int(max(120, min(float(self.preview_panel_width_px), float(max_w))))
        panel_h = int(max(100, min(float(self.preview_panel_height_px), float(max_h))))
        x = int(max(0, min(float(self.preview_panel_x_px), float(self.size[0] - panel_w))))
        y = int(max(0, min(float(self.preview_panel_y_px), float(self.size[1] - panel_h))))
        return pygame.Rect(x, y, panel_w, panel_h)

    def _project_preview_panel_vertices(
        self: "BTGDisplayApp",
        preview_mesh: BTGMesh,
        panel: pygame.Rect,
    ) -> Tuple[List[Optional[Tuple[float, float]]], List[float]]:
        if not preview_mesh.vertices:
            return [], []

        local_vertices = self.add_object_preview_norm_vertices
        if len(local_vertices) != len(preview_mesh.vertices):
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
            local_vertices = [
                ((vx - cx) * inv_radius, (vy - cy) * inv_radius, (vz - cz) * inv_radius)
                for vx, vy, vz in preview_mesh.vertices
            ]
            self.add_object_preview_norm_vertices = local_vertices

        spin_deg = (pygame.time.get_ticks() * 0.035) % 360.0
        pitch_deg = -18.0
        depth_offset = 3.0
        focal = min(panel.w, panel.h) * 0.66

        screen_pts: List[Optional[Tuple[float, float]]] = []
        depths: List[float] = []
        pcx = panel.x + panel.w * 0.5
        pcy = panel.y + panel.h * 0.56

        for local in local_vertices:
            cam = rotate_world_to_camera(local, spin_deg, pitch_deg)
            depth = cam[1] + depth_offset
            depths.append(depth)
            if depth <= 0.05:
                screen_pts.append(None)
                continue

            sx = pcx + (cam[0] * focal / depth)
            sy = pcy - (cam[2] * focal / depth)
            screen_pts.append((sx, sy))

        return screen_pts, depths

    def _draw_add_object_preview(self: "BTGDisplayApp") -> None:
        if not self.show_menu or self.menu_mode != "add_object_files":
            return
        preview_mesh = self.add_object_preview_mesh
        if preview_mesh is None or not preview_mesh.vertices or not preview_mesh.faces:
            return

        panel = self._preview_panel_rect()
        points, depths = self._project_preview_panel_vertices(preview_mesh, panel)
        if not points:
            return

        self._ensure_add_object_preview_face_cache(preview_mesh)

        if self.opengl_enabled:
            self._draw_add_object_preview_gl(panel, points, depths, preview_mesh)
            return

        pygame.draw.rect(self.screen, (6, 10, 14), panel, border_radius=8)
        pygame.draw.rect(self.screen, (110, 152, 128), panel, 2, border_radius=8)

        title = self._menu_t("preview.panel_title", "Placement Preview")
        title_surf = self.font_small.render(title, True, (190, 238, 206))
        self.screen.blit(title_surf, (panel.x + 12, panel.y + 10))

        path_text = self.add_object_preview_path or "(unknown)"
        max_chars = max(10, (panel.w - 24) // 8)
        if len(path_text) > max_chars:
            path_text = "..." + path_text[-(max_chars - 3):]
        path_surf = self.font_small.render(path_text, True, (170, 215, 185))
        self.screen.blit(path_surf, (panel.x + 12, panel.y + 30))

        mode_label = self._menu_tf(
            "preview.style_fmt",
            "Style: {style}",
            style=self.preview_panel_render_mode,
        )
        mode_surf = self.font_small.render(mode_label, True, (165, 205, 180))
        self.screen.blit(mode_surf, (panel.x + 12, panel.y + panel.h - 22))

        style = self.preview_panel_render_mode
        draw_fill = style in {"textured", "shaded"}
        draw_wire = style == "wireframe"
        face_indices = list(self.add_object_preview_face_indices)
        if draw_fill:
            face_indices.sort(
                key=lambda fi: (
                    depths[preview_mesh.faces[fi][0]]
                    + depths[preview_mesh.faces[fi][1]]
                    + depths[preview_mesh.faces[fi][2]]
                ) / 3.0,
                reverse=True,
            )

        def _preview_face_rgb(face_index: int) -> Tuple[int, int, int]:
            if 0 <= face_index < len(preview_mesh.face_colors):
                return preview_mesh.face_colors[face_index]
            return (40, 128, 78)

        for fi in face_indices:
            a, b, c = preview_mesh.faces[fi]
            pa = points[a]
            pb = points[b]
            pc = points[c]
            if pa is None or pb is None or pc is None:
                continue
            if draw_fill:
                pygame.draw.polygon(self.screen, _preview_face_rgb(fi), (pa, pb, pc), 0)
            if draw_wire:
                pygame.draw.line(self.screen, (100, 255, 145), (pa[0], pa[1]), (pb[0], pb[1]), 1)
                pygame.draw.line(self.screen, (100, 255, 145), (pb[0], pb[1]), (pc[0], pc[1]), 1)
                pygame.draw.line(self.screen, (100, 255, 145), (pc[0], pc[1]), (pa[0], pa[1]), 1)

    def _ensure_add_object_preview_face_cache(self: "BTGDisplayApp", preview_mesh: BTGMesh) -> None:
        if self.add_object_preview_face_cache_ready:
            return

        face_step = max(1, int(self.add_object_preview_face_step))
        face_indices = list(range(0, len(preview_mesh.faces), face_step))
        textured_meta: Dict[int, Tuple[str, int, int, int]] = {}

        if preview_mesh.texcoords and preview_mesh.face_materials and preview_mesh.face_texcoords:
            material_to_path = self.add_object_preview_material_paths
            for fi in face_indices:
                if fi >= len(preview_mesh.face_materials) or fi >= len(preview_mesh.face_texcoords):
                    continue
                material = preview_mesh.face_materials[fi]
                ta, tb, tc = preview_mesh.face_texcoords[fi]
                if (
                    not material
                    or ta is None
                    or tb is None
                    or tc is None
                    or not (0 <= ta < len(preview_mesh.texcoords))
                    or not (0 <= tb < len(preview_mesh.texcoords))
                    or not (0 <= tc < len(preview_mesh.texcoords))
                ):
                    continue

                if material not in material_to_path:
                    material_to_path[material] = resolve_texture_path(material, self.flightgear_root, self.material_map_path)
                texture_path = material_to_path[material]
                if not texture_path:
                    continue

                textured_meta[fi] = (material, ta, tb, tc)

        self.add_object_preview_face_indices = face_indices
        self.add_object_preview_textured_face_meta = textured_meta
        self.add_object_preview_face_cache_ready = True

    def _draw_add_object_preview_gl(
        self: "BTGDisplayApp",
        panel: pygame.Rect,
        points: List[Optional[Tuple[float, float]]],
        depths: List[float],
        preview_mesh: BTGMesh,
    ) -> None:
        GL = self.GL
        if GL is None:
            return
        style = self.preview_panel_render_mode

        self._gl_begin_2d()
        self._gl_draw_rect(panel.x, panel.y, panel.w, panel.h, (6 / 255.0, 10 / 255.0, 14 / 255.0, 0.96))
        border = (110 / 255.0, 152 / 255.0, 128 / 255.0, 1.0)
        self._gl_draw_rect(panel.x, panel.y, panel.w, 2, border)
        self._gl_draw_rect(panel.x, panel.y + panel.h - 2, panel.w, 2, border)
        self._gl_draw_rect(panel.x, panel.y, 2, panel.h, border)
        self._gl_draw_rect(panel.x + panel.w - 2, panel.y, 2, panel.h, border)

        self._gl_draw_text(
            self._menu_t("preview.panel_title", "Placement Preview"),
            panel.x + 12,
            panel.y + 10,
            (190, 238, 206),
            self.font_small,
        )
        path_text = self.add_object_preview_path or "(unknown)"
        max_chars = max(10, (panel.w - 24) // 8)
        if len(path_text) > max_chars:
            path_text = "..." + path_text[-(max_chars - 3):]
        self._gl_draw_text(path_text, panel.x + 12, panel.y + 30, (170, 215, 185), self.font_small)
        self._gl_end_2d()

        if style == "textured":
            self._draw_add_object_preview_gl_textured_3d(panel, preview_mesh)
            self._gl_begin_2d()
            self._gl_draw_text(
                self._menu_tf("preview.style_fmt", "Style: {style}", style=style),
                panel.x + 12,
                panel.y + panel.h - 22,
                (165, 205, 180),
                self.font_small,
            )
            self._gl_end_2d()
            return

        GL.glPushAttrib(GL.GL_ENABLE_BIT | GL.GL_CURRENT_BIT | GL.GL_LINE_BIT | GL.GL_SCISSOR_BIT)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glEnable(GL.GL_SCISSOR_TEST)
        GL.glScissor(panel.x, self.size[1] - panel.y - panel.h, panel.w, panel.h)
        self._gl_begin_2d()
        self._ensure_add_object_preview_face_cache(preview_mesh)
        face_indices = list(self.add_object_preview_face_indices)
        if style != "wireframe":
            face_indices.sort(
                key=lambda fi: (
                    depths[preview_mesh.faces[fi][0]]
                    + depths[preview_mesh.faces[fi][1]]
                    + depths[preview_mesh.faces[fi][2]]
                ) / 3.0,
                reverse=True,
            )

        fallback_faces: List[Tuple[int, int, int, int]] = []
        material_to_path = self.add_object_preview_material_paths
        can_draw_textured = bool(
            style == "textured"
            and preview_mesh.texcoords
            and preview_mesh.face_materials
            and preview_mesh.face_texcoords
        )

        if style in {"shaded", "wireframe"}:
            can_draw_textured = False

        if can_draw_textured:
            GL.glEnable(GL.GL_TEXTURE_2D)
            GL.glTexEnvi(GL.GL_TEXTURE_ENV, GL.GL_TEXTURE_ENV_MODE, GL.GL_MODULATE)
            GL.glColor4f(1.0, 1.0, 1.0, 0.95)
            textured_batches: Dict[int, Dict[str, List[float]]] = {}
            textured_meta = self.add_object_preview_textured_face_meta
            for fi in face_indices:
                a, b, c = preview_mesh.faces[fi]
                pa = points[a]
                pb = points[b]
                pc = points[c]
                if pa is None or pb is None or pc is None:
                    continue

                cached = textured_meta.get(fi)
                if cached is None:
                    fallback_faces.append((fi, a, b, c))
                    continue

                material, ta, tb, tc = cached
                texture_path = material_to_path.get(material)
                tex_id = self._load_gl_texture(texture_path) if texture_path else None
                if tex_id is None:
                    fallback_faces.append((fi, a, b, c))
                    continue

                u1, v1 = preview_mesh.texcoords[ta]
                u2, v2 = preview_mesh.texcoords[tb]
                u3, v3 = preview_mesh.texcoords[tc]

                batch = textured_batches.setdefault(tex_id, {"v": [], "t": []})
                batch["v"].extend(
                    (
                        pa[0], self.size[1] - pa[1],
                        pb[0], self.size[1] - pb[1],
                        pc[0], self.size[1] - pc[1],
                    )
                )
                batch["t"].extend((u1, v1, u2, v2, u3, v3))

            for tex_id, batch in textured_batches.items():
                verts = batch["v"]
                tex = batch["t"]
                if not verts or not tex:
                    continue
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
                GL.glBegin(GL.GL_TRIANGLES)
                for i in range(0, len(verts), 6):
                    ti = (i // 6) * 6
                    GL.glTexCoord2f(tex[ti], tex[ti + 1])
                    GL.glVertex2f(verts[i], verts[i + 1])
                    GL.glTexCoord2f(tex[ti + 2], tex[ti + 3])
                    GL.glVertex2f(verts[i + 2], verts[i + 3])
                    GL.glTexCoord2f(tex[ti + 4], tex[ti + 5])
                    GL.glVertex2f(verts[i + 4], verts[i + 5])
                GL.glEnd()
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            GL.glDisable(GL.GL_TEXTURE_2D)
        else:
            fallback_faces = [(fi, *preview_mesh.faces[fi]) for fi in face_indices]

        if fallback_faces and style != "wireframe":
            GL.glDisable(GL.GL_TEXTURE_2D)
            GL.glBegin(GL.GL_TRIANGLES)
            for fi, a, b, c in fallback_faces:
                pa = points[a]
                pb = points[b]
                pc = points[c]
                if pa is None or pb is None or pc is None:
                    continue

                if 0 <= fi < len(preview_mesh.face_colors):
                    r, g, bcol = preview_mesh.face_colors[fi]
                else:
                    r, g, bcol = (40, 128, 78)
                GL.glColor4f(r / 255.0, g / 255.0, bcol / 255.0, 0.65)

                GL.glVertex2f(pa[0], self.size[1] - pa[1])
                GL.glVertex2f(pb[0], self.size[1] - pb[1])
                GL.glVertex2f(pc[0], self.size[1] - pc[1])
            GL.glEnd()

        if style == "wireframe":
            GL.glLineWidth(1.0)
            GL.glColor4f(100 / 255.0, 1.0, 145 / 255.0, 1.0)
            GL.glBegin(GL.GL_LINES)
            for fi in face_indices:
                a, b, c = preview_mesh.faces[fi]
                pa = points[a]
                pb = points[b]
                pc = points[c]
                if pa is None or pb is None or pc is None:
                    continue
                GL.glVertex2f(pa[0], self.size[1] - pa[1])
                GL.glVertex2f(pb[0], self.size[1] - pb[1])
                GL.glVertex2f(pb[0], self.size[1] - pb[1])
                GL.glVertex2f(pc[0], self.size[1] - pc[1])
                GL.glVertex2f(pc[0], self.size[1] - pc[1])
                GL.glVertex2f(pa[0], self.size[1] - pa[1])
            GL.glEnd()
        self._gl_draw_text(
            self._menu_tf("preview.style_fmt", "Style: {style}", style=style),
            panel.x + 12,
            panel.y + panel.h - 22,
            (165, 205, 180),
            self.font_small,
        )
        self._gl_end_2d()
        GL.glPopAttrib()

    def _draw_add_object_preview_gl_textured_3d(self: "BTGDisplayApp", panel: pygame.Rect, preview_mesh: BTGMesh) -> None:
        GL = self.GL
        GLU = self.GLU
        if GL is None or GLU is None:
            return

        self._ensure_add_object_preview_face_cache(preview_mesh)

        local_vertices = self.add_object_preview_norm_vertices
        if len(local_vertices) != len(preview_mesh.vertices):
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
            local_vertices = [
                ((vx - cx) * inv_radius, (vy - cy) * inv_radius, (vz - cz) * inv_radius)
                for vx, vy, vz in preview_mesh.vertices
            ]
            self.add_object_preview_norm_vertices = local_vertices

        inner_margin = 8
        top_pad = 54
        bottom_pad = 30
        vp_x = panel.x + inner_margin
        vp_y_top = panel.y + top_pad
        vp_w = panel.w - inner_margin * 2
        vp_h = panel.h - top_pad - bottom_pad
        if vp_w <= 8 or vp_h <= 8:
            return

        vp_y = self.size[1] - vp_y_top - vp_h
        spin_deg = (pygame.time.get_ticks() * 0.035) % 360.0
        pitch_deg = -18.0
        depth_offset = 3.0

        GL.glPushAttrib(
            GL.GL_ENABLE_BIT
            | GL.GL_CURRENT_BIT
            | GL.GL_DEPTH_BUFFER_BIT
            | GL.GL_SCISSOR_BIT
            | GL.GL_VIEWPORT_BIT
            | GL.GL_TEXTURE_BIT
        )

        GL.glEnable(GL.GL_SCISSOR_TEST)
        GL.glScissor(vp_x, vp_y, vp_w, vp_h)
        GL.glViewport(vp_x, vp_y, vp_w, vp_h)

        GL.glClearDepth(1.0)
        GL.glClear(GL.GL_DEPTH_BUFFER_BIT)

        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LEQUAL)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glDisable(GL.GL_BLEND)

        if hasattr(GL, "GL_PERSPECTIVE_CORRECTION_HINT") and hasattr(GL, "GL_NICEST"):
            GL.glHint(GL.GL_PERSPECTIVE_CORRECTION_HINT, GL.GL_NICEST)

        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GLU.gluPerspective(52.0, float(vp_w) / float(max(1, vp_h)), 0.05, 40.0)

        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glTranslatef(0.0, 0.0, -depth_offset)
        # Match legacy preview convention:
        # - model spin is yaw around +Z with inverse sign
        # - pitch uses inverse sign
        # - +Y depth / +Z up are remapped to OpenGL camera axes
        GL.glRotatef(-90.0, 1.0, 0.0, 0.0)
        GL.glRotatef(-pitch_deg, 1.0, 0.0, 0.0)
        GL.glRotatef(-spin_deg, 0.0, 0.0, 1.0)

        face_indices = list(self.add_object_preview_face_indices)
        textured_meta = self.add_object_preview_textured_face_meta
        material_to_path = self.add_object_preview_material_paths

        textured_batches: Dict[int, Dict[str, List[float]]] = {}
        fallback_faces: List[int] = []
        for fi in face_indices:
            a, b, c = preview_mesh.faces[fi]

            cached = textured_meta.get(fi)
            if cached is None:
                fallback_faces.append(fi)
                continue

            material, ta, tb, tc = cached
            texture_path = material_to_path.get(material)
            tex_id = self._load_gl_texture(texture_path) if texture_path else None
            if tex_id is None:
                fallback_faces.append(fi)
                continue

            u1, v1 = preview_mesh.texcoords[ta]
            u2, v2 = preview_mesh.texcoords[tb]
            u3, v3 = preview_mesh.texcoords[tc]
            va = local_vertices[a]
            vb = local_vertices[b]
            vc = local_vertices[c]

            batch = textured_batches.setdefault(tex_id, {"v": [], "t": []})
            batch["v"].extend((va[0], va[1], va[2], vb[0], vb[1], vb[2], vc[0], vc[1], vc[2]))
            batch["t"].extend((u1, v1, u2, v2, u3, v3))

        GL.glEnable(GL.GL_TEXTURE_2D)
        GL.glTexEnvi(GL.GL_TEXTURE_ENV, GL.GL_TEXTURE_ENV_MODE, GL.GL_MODULATE)
        GL.glColor4f(1.0, 1.0, 1.0, 1.0)
        for tex_id, batch in textured_batches.items():
            verts = batch["v"]
            tex = batch["t"]
            if not verts or not tex:
                continue
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
            GL.glBegin(GL.GL_TRIANGLES)
            for i in range(0, len(verts), 9):
                ti = (i // 9) * 6
                GL.glTexCoord2f(tex[ti], tex[ti + 1])
                GL.glVertex3f(verts[i], verts[i + 1], verts[i + 2])
                GL.glTexCoord2f(tex[ti + 2], tex[ti + 3])
                GL.glVertex3f(verts[i + 3], verts[i + 4], verts[i + 5])
                GL.glTexCoord2f(tex[ti + 4], tex[ti + 5])
                GL.glVertex3f(verts[i + 6], verts[i + 7], verts[i + 8])
            GL.glEnd()

        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

        if fallback_faces:
            GL.glBegin(GL.GL_TRIANGLES)
            for fi in fallback_faces:
                a, b, c = preview_mesh.faces[fi]
                if 0 <= fi < len(preview_mesh.face_colors):
                    r, g, bcol = preview_mesh.face_colors[fi]
                else:
                    r, g, bcol = (40, 128, 78)
                GL.glColor4f(r / 255.0, g / 255.0, bcol / 255.0, 1.0)
                va = local_vertices[a]
                vb = local_vertices[b]
                vc = local_vertices[c]
                GL.glVertex3f(va[0], va[1], va[2])
                GL.glVertex3f(vb[0], vb[1], vb[2])
                GL.glVertex3f(vc[0], vc[1], vc[2])
            GL.glEnd()

        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPopMatrix()
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPopMatrix()
        GL.glPopAttrib()

    def _init_gl(self: "BTGDisplayApp") -> None:
        GLM = self.GL
        GLUM = self.GLU
        assert GLM is not None
        assert GLUM is not None
        del GLUM

        GLM.glEnable(GLM.GL_DEPTH_TEST)
        GLM.glEnable(GLM.GL_CULL_FACE)
        GLM.glCullFace(GLM.GL_BACK)

        GLM.glEnable(GLM.GL_LIGHTING)
        GLM.glEnable(GLM.GL_LIGHT0)
        light_pos = (GLM.GLfloat * 4)(0.3, 0.4, 1.0, 0.0)
        light_ambient = (GLM.GLfloat * 4)(0.25, 0.25, 0.25, 1.0)
        light_diffuse = (GLM.GLfloat * 4)(0.90, 0.90, 0.90, 1.0)
        GLM.glLightfv(GLM.GL_LIGHT0, GLM.GL_POSITION, light_pos)
        GLM.glLightfv(GLM.GL_LIGHT0, GLM.GL_AMBIENT, light_ambient)
        GLM.glLightfv(GLM.GL_LIGHT0, GLM.GL_DIFFUSE, light_diffuse)

        GLM.glEnable(GLM.GL_COLOR_MATERIAL)
        GLM.glColorMaterial(GLM.GL_FRONT_AND_BACK, GLM.GL_AMBIENT_AND_DIFFUSE)
        GLM.glShadeModel(GLM.GL_SMOOTH)

        GLM.glEnable(GLM.GL_BLEND)
        GLM.glBlendFunc(GLM.GL_SRC_ALPHA, GLM.GL_ONE_MINUS_SRC_ALPHA)

        self.gl_texture_anisotropy_supported = False
        self.gl_texture_max_anisotropy = 1.0
        self.gl_texture_anisotropy = 1.0
        try:
            ext_raw = GLM.glGetString(GLM.GL_EXTENSIONS)
            if isinstance(ext_raw, bytes):
                ext_text = ext_raw.decode("ascii", "ignore")
            elif isinstance(ext_raw, str):
                ext_text = ext_raw
            else:
                ext_text = ""

            if "GL_EXT_texture_filter_anisotropic" in ext_text:
                # GL_EXT_texture_filter_anisotropic constants:
                # 0x84FF = GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT
                # 0x84FE = GL_TEXTURE_MAX_ANISOTROPY_EXT
                max_aniso = float(GLM.glGetFloatv(0x84FF))
                if max_aniso > 1.0:
                    self.gl_texture_anisotropy_supported = True
                    self.gl_texture_max_anisotropy = max_aniso
                    # Keep preview smooth but avoid forcing extreme levels.
                    self.gl_texture_anisotropy = min(8.0, max_aniso)
        except Exception:
            self.gl_texture_anisotropy_supported = False
            self.gl_texture_max_anisotropy = 1.0
            self.gl_texture_anisotropy = 1.0

    def _gl_begin_2d(self: "BTGDisplayApp") -> None:
        GLM = self.GL
        assert GLM is not None
        w, h = self.size
        GLM.glMatrixMode(GLM.GL_PROJECTION)
        GLM.glPushMatrix()
        GLM.glLoadIdentity()
        GLM.glOrtho(0, w, 0, h, -1, 1)

        GLM.glMatrixMode(GLM.GL_MODELVIEW)
        GLM.glPushMatrix()
        GLM.glLoadIdentity()

        GLM.glDisable(GLM.GL_DEPTH_TEST)
        GLM.glDisable(GLM.GL_LIGHTING)

    def _gl_end_2d(self: "BTGDisplayApp") -> None:
        GLM = self.GL
        assert GLM is not None
        GLM.glEnable(GLM.GL_DEPTH_TEST)
        GLM.glEnable(GLM.GL_LIGHTING)

        GLM.glMatrixMode(GLM.GL_MODELVIEW)
        GLM.glPopMatrix()
        GLM.glMatrixMode(GLM.GL_PROJECTION)
        GLM.glPopMatrix()

    def _gl_draw_rect(self: "BTGDisplayApp", x: float, y_top: float, w: float, h: float, color: Tuple[float, float, float, float]) -> None:
        GLM = self.GL
        assert GLM is not None
        y = self.size[1] - y_top - h
        GLM.glColor4f(*color)
        GLM.glBegin(GLM.GL_QUADS)
        GLM.glVertex2f(x, y)
        GLM.glVertex2f(x + w, y)
        GLM.glVertex2f(x + w, y + h)
        GLM.glVertex2f(x, y + h)
        GLM.glEnd()

    def _gl_draw_text(
        self: "BTGDisplayApp",
        text: str,
        x: float,
        y_top: float,
        color: Tuple[int, int, int],
        font: Optional[pygame.font.Font] = None,
        cache: bool = True,
    ) -> None:
        font = font or self.font_small
        if not text:
            return
        self._ensure_gl_text_cache()

        font_h = font.get_height()
        # Highly dynamic lines (e.g. camera pose) can churn the cache; draw transiently.
        if cache and any(ch.isdigit() for ch in text) and len(text) >= max(24, font_h):
            cache = False

        key = (id(font), text, int(color[0]), int(color[1]), int(color[2]))
        if cache:
            cached = self._gl_text_cache.get(key)
            if cached is not None:
                tex_id, w, h = cached
                self._gl_text_cache.move_to_end(key)
                self._gl_draw_textured_quad(tex_id, x, y_top, w, h)
                return

        uploaded = self._gl_upload_text_texture(text, color, font)
        if uploaded is None:
            return
        tex_id, w, h = uploaded
        self._gl_draw_textured_quad(tex_id, x, y_top, w, h)

        if not cache:
            GLM = self.GL
            if GLM is not None:
                try:
                    GLM.glDeleteTextures([int(tex_id)])
                except Exception:
                    pass
            return

        self._gl_text_cache[key] = (tex_id, w, h)
        self._gl_text_cache.move_to_end(key)
        while len(self._gl_text_cache) > int(self._gl_text_cache_max_entries):
            _old_key, (old_tex_id, _old_w, _old_h) = self._gl_text_cache.popitem(last=False)
            GLM = self.GL
            if GLM is not None:
                try:
                    GLM.glDeleteTextures([int(old_tex_id)])
                except Exception:
                    pass

    def _compute_uv_density_debug(self: "BTGDisplayApp", mesh: BTGMesh) -> None:
        if not mesh.vertices or not mesh.faces or not mesh.texcoords:
            self.last_uv_debug = {
                "materials_analyzed": 0,
                "faces_analyzed": 0,
                "top_dense": [],
                "top_sparse": [],
            }
            return

        per_material: Dict[str, Dict[str, float]] = {}
        valid_faces = 0

        for fi, (a, b, c) in enumerate(mesh.faces):
            if fi >= len(mesh.face_materials) or fi >= len(mesh.face_texcoords):
                continue

            material = mesh.face_materials[fi]
            ta, tb, tc = mesh.face_texcoords[fi]
            if not material or ta is None or tb is None or tc is None:
                continue
            if not (0 <= ta < len(mesh.texcoords) and 0 <= tb < len(mesh.texcoords) and 0 <= tc < len(mesh.texcoords)):
                continue

            va = mesh.vertices[a]
            vb = mesh.vertices[b]
            vc = mesh.vertices[c]
            world_cross = v_cross(v_sub(vb, va), v_sub(vc, va))
            world_area = 0.5 * math.sqrt(v_dot(world_cross, world_cross))
            if world_area <= 1e-9:
                continue

            u1, v1 = mesh.texcoords[ta]
            u2, v2 = mesh.texcoords[tb]
            u3, v3 = mesh.texcoords[tc]
            uv_area = 0.5 * abs((u2 - u1) * (v3 - v1) - (u3 - u1) * (v2 - v1))
            if uv_area <= 1e-12:
                continue

            # UV area per world area: larger values imply higher texture repetition density.
            density = uv_area / world_area
            repeat_per_meter = math.sqrt(density)

            bucket = per_material.setdefault(material, {"sum": 0.0, "count": 0.0})
            bucket["sum"] += repeat_per_meter
            bucket["count"] += 1.0
            valid_faces += 1

        summaries: List[Tuple[str, float, int]] = []
        for mat, data in per_material.items():
            count = int(data["count"])
            if count <= 0:
                continue
            avg_repeat = data["sum"] / data["count"]
            summaries.append((mat, avg_repeat, count))

        summaries.sort(key=lambda item: item[1], reverse=True)

        dense = [f"{mat}:{avg:.3f}/m ({count})" for mat, avg, count in summaries[:3]]
        sparse = [f"{mat}:{avg:.3f}/m ({count})" for mat, avg, count in sorted(summaries, key=lambda item: item[1])[:3]]

        self.last_uv_debug = {
            "materials_analyzed": len(summaries),
            "faces_analyzed": valid_faces,
            "top_dense": dense,
            "top_sparse": sparse,
        }

    def _draw_crosshair(self: "BTGDisplayApp") -> None:
        size = 16
        half = size // 2
        target_x, target_y = self._target_screen_xy()
        cx = int(round(target_x))
        cy = int(round(target_y))

        if self.opengl_enabled:
            GL = self.GL
            if GL is None:
                return
            self._gl_begin_2d()
            GL.glDisable(GL.GL_TEXTURE_2D)

            # Black outline pass.
            GL.glLineWidth(3.0)
            GL.glColor4f(0.0, 0.0, 0.0, 1.0)
            GL.glBegin(GL.GL_LINES)
            GL.glVertex2f(cx - half, self.size[1] - cy)
            GL.glVertex2f(cx + half, self.size[1] - cy)
            GL.glVertex2f(cx, self.size[1] - (cy - half))
            GL.glVertex2f(cx, self.size[1] - (cy + half))
            GL.glEnd()

            # White center pass.
            GL.glLineWidth(1.0)
            GL.glColor4f(1.0, 1.0, 1.0, 1.0)
            GL.glBegin(GL.GL_LINES)
            GL.glVertex2f(cx - half, self.size[1] - cy)
            GL.glVertex2f(cx + half, self.size[1] - cy)
            GL.glVertex2f(cx, self.size[1] - (cy - half))
            GL.glVertex2f(cx, self.size[1] - (cy + half))
            GL.glEnd()

            GL.glLineWidth(1.0)
            self._gl_end_2d()
            return

        # Software renderer: black outline + white center.
        pygame.draw.line(self.screen, (0, 0, 0), (cx - half, cy), (cx + half, cy), 3)
        pygame.draw.line(self.screen, (0, 0, 0), (cx, cy - half), (cx, cy + half), 3)
        pygame.draw.line(self.screen, (255, 255, 255), (cx - half, cy), (cx + half, cy), 1)
        pygame.draw.line(self.screen, (255, 255, 255), (cx, cy - half), (cx, cy + half), 1)

    def _model_label_text(self: "BTGDisplayApp", instance_index: int, source_path: str, is_ac_model: bool) -> str:
        normalized = source_path.replace("\\", "/") if source_path else ""
        base_name = os.path.basename(normalized) if normalized else "<unknown>"
        kind = "ac" if is_ac_model else "model"
        return f"M{instance_index:03d} [{kind}] {base_name}"

    def _collect_projected_model_labels(self: "BTGDisplayApp") -> List[Tuple[float, float, float, str, bool, int]]:
        if not self.scene_model_instances:
            return []

        width, height = self.size
        cam = tuple(self.camera_pos)
        focal = (0.5 * float(height)) / math.tan(math.radians(self.fov_deg) * 0.5)
        labels: List[Tuple[float, float, float, str, bool, int]] = []

        for idx, instance in enumerate(self.scene_model_instances):
            anchor_world = instance.render_anchor_enu if instance.render_anchor_enu is not None else instance.origin_enu
            rel = (
                anchor_world[0] - cam[0],
                anchor_world[1] - cam[1],
                anchor_world[2] - cam[2],
            )
            v_cam = rotate_world_to_camera(rel, self.yaw, self.pitch)
            if self.opengl_enabled:
                depth = -v_cam[2]
                if depth <= self.near_plane:
                    continue
                sx = (width * 0.5) + (v_cam[0] * focal / depth)
                sy = (height * 0.5) - (v_cam[1] * focal / depth)
                depth_key = depth
            else:
                projected = self._project(v_cam, width, height)
                if projected is None:
                    continue
                sx, sy = projected
                depth_key = v_cam[1]

            if sx < -220 or sx > width + 220 or sy < -40 or sy > height + 40:
                continue

            label_text = self._model_label_text(idx, instance.source_path, instance.is_ac_model)
            labels.append((depth_key, sx, sy, label_text, instance.is_ac_model, idx))

        labels.sort(key=lambda item: item[0], reverse=True)
        return labels

    def _draw_single_label_chip(
        self: "BTGDisplayApp",
        sx: float,
        sy: float,
        text: str,
        selected: bool = False,
    ) -> None:
        marker = "*" if selected else ""
        display_text = f"{marker}{text}"
        if self.opengl_enabled:
            self._gl_begin_2d()
            text_w, _text_h = self.font_small.size(display_text)
            dot_color = (0.45, 1.0, 0.45, 0.95) if selected else (1.0, 0.85, 0.15, 0.95)
            self._gl_draw_rect(sx - 2, sy - 2, 5, 5, dot_color)
            self._gl_draw_rect(sx + 7, sy - 2, text_w + 14, 18, (0.04, 0.05, 0.08, 0.76))
            text_color = (175, 255, 175) if selected else (250, 245, 225)
            self._gl_draw_text(display_text, sx + 10, sy, text_color, self.font_small)
            self._gl_end_2d()
            return

        dot_color = (115, 255, 115) if selected else (255, 215, 70)
        pygame.draw.circle(self.screen, dot_color, (int(sx), int(sy)), 2)
        text_color = (175, 255, 175) if selected else (250, 245, 225)
        text_surf = self.font_small.render(display_text, True, text_color)
        tw, th = text_surf.get_size()
        bg = pygame.Rect(int(sx) + 7, int(sy) - 2, tw + 8, th + 4)
        pygame.draw.rect(self.screen, (8, 11, 18), bg)
        self.screen.blit(text_surf, (bg.x + 4, bg.y + 2))

    def _draw_crosshair_hover_label(self: "BTGDisplayApp") -> None:
        labels = self.frame_projected_model_labels
        if labels is None:
            labels = self._collect_projected_model_labels()
        self.crosshair_hover_model_index = None
        if not labels:
            return

        cx, cy = self._target_screen_xy()
        hover_radius_sq = 12.0 * 12.0

        best_any: Optional[Tuple[float, float, float, str, bool, int, float]] = None
        best_ac: Optional[Tuple[float, float, float, str, bool, int, float]] = None
        for depth, sx, sy, text, is_ac_model, model_idx in labels:
            dx = sx - cx
            dy = sy - cy
            dist_sq = dx * dx + dy * dy
            if dist_sq > hover_radius_sq:
                continue

            if best_any is None or dist_sq < best_any[6]:
                best_any = (depth, sx, sy, text, is_ac_model, model_idx, dist_sq)
            if is_ac_model and (best_ac is None or dist_sq < best_ac[6]):
                best_ac = (depth, sx, sy, text, is_ac_model, model_idx, dist_sq)

        if best_any is not None:
            self.crosshair_hover_model_index = best_any[5]

        if best_ac is None:
            return

        _depth, sx, sy, text, _is_ac_model, model_idx, _dist_sq = best_ac
        self._draw_single_label_chip(sx, sy, text, selected=(self.selected_model_instance_index == model_idx))

    def _draw_model_labels(self: "BTGDisplayApp") -> None:
        if not self.show_model_labels:
            return
        labels = self.frame_projected_model_labels
        if labels is None:
            labels = self._collect_projected_model_labels()
        if not labels:
            return

        if self.opengl_enabled:
            self._gl_begin_2d()
            for _depth, sx, sy, text, _is_ac_model, model_idx in labels:
                selected = self.selected_model_instance_index == model_idx
                display_text = f"*{text}" if selected else text
                text_w, _text_h = self.font_small.size(display_text)
                dot_color = (0.45, 1.0, 0.45, 0.95) if selected else (1.0, 0.85, 0.15, 0.95)
                self._gl_draw_rect(sx - 2, sy - 2, 5, 5, dot_color)
                self._gl_draw_rect(sx + 7, sy - 2, text_w + 14, 18, (0.04, 0.05, 0.08, 0.76))
                text_color = (175, 255, 175) if selected else (250, 245, 225)
                self._gl_draw_text(display_text, sx + 10, sy, text_color, self.font_small)
            self._gl_end_2d()
            return

        for _depth, sx, sy, text, _is_ac_model, model_idx in labels:
            selected = self.selected_model_instance_index == model_idx
            dot_color = (115, 255, 115) if selected else (255, 215, 70)
            pygame.draw.circle(self.screen, dot_color, (int(sx), int(sy)), 2)
            display_text = f"*{text}" if selected else text
            text_color = (175, 255, 175) if selected else (250, 245, 225)
            text_surf = self.font_small.render(display_text, True, text_color)
            tw, th = text_surf.get_size()
            bg = pygame.Rect(int(sx) + 7, int(sy) - 2, tw + 8, th + 4)
            pygame.draw.rect(self.screen, (8, 11, 18), bg)
            self.screen.blit(text_surf, (bg.x + 4, bg.y + 2))

    def _build_face_data(self: "BTGDisplayApp", mesh: BTGMesh, build_textures: bool = True) -> None:
        self.face_data = []
        if build_textures:
            self.gl_textured_batches = []
            self.gl_textured_static_batches = []
            self.gl_textured_model_batches = []
            self.gl_textured_model_batches_by_instance = {}
            self.last_debug = {
                "faces_with_material": 0,
                "faces_with_uv": 0,
                "resolved_faces": 0,
                "unique_materials": 0,
                "resolved_materials": 0,
                "missing_materials": [],
                "textures_loaded": 0,
            }
            self.last_uv_debug = {
                "materials_analyzed": 0,
                "faces_analyzed": 0,
                "top_dense": [],
                "top_sparse": [],
            }
        if not mesh.vertices or not mesh.faces:
            self._clear_gl_textures()
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
            return

        vertex_normal_sum = [(0.0, 0.0, 0.0) for _ in mesh.vertices]
        vertex_color_sum = [(0.0, 0.0, 0.0) for _ in mesh.vertices]
        vertex_color_count = [0 for _ in mesh.vertices]

        missing_texture_materials: set[str] = set()

        def _material_expects_texture(material_name: str) -> bool:
            if not material_name:
                return False
            if material_name == "ac_object":
                return False
            if material_name.startswith("__acmat__:"):
                return False
            return True

        if mesh.face_materials:
            material_to_texture: Dict[str, Optional[str]] = {}
            for material in {m for m in mesh.face_materials if m}:
                if not _material_expects_texture(material):
                    continue
                if material not in material_to_texture:
                    material_to_texture[material] = resolve_texture_path(material, self.flightgear_root, self.material_map_path)
                if not material_to_texture[material]:
                    missing_texture_materials.add(material)

        for fi, (a, b, c) in enumerate(mesh.faces):
            va = mesh.vertices[a]
            vb = mesh.vertices[b]
            vc = mesh.vertices[c]
            normal = v_norm(v_cross(v_sub(vb, va), v_sub(vc, va)))
            material = mesh.face_materials[fi] if fi < len(mesh.face_materials) else ""
            if material and material in missing_texture_materials:
                color = self.missing_material_color_rgb
            elif fi < len(mesh.face_colors):
                color = mesh.face_colors[fi]
            else:
                color = material_color_from_face(fi)
            self.face_data.append(((a, b, c), color, normal))

            for idx in (a, b, c):
                nx, ny, nz = vertex_normal_sum[idx]
                vertex_normal_sum[idx] = (nx + normal[0], ny + normal[1], nz + normal[2])

                cr, cg, cb = vertex_color_sum[idx]
                vertex_color_sum[idx] = (
                    cr + color[0] / 255.0,
                    cg + color[1] / 255.0,
                    cb + color[2] / 255.0,
                )
                vertex_color_count[idx] += 1

        normals = [v_norm(n) for n in vertex_normal_sum]
        colors: List[Tuple[float, float, float]] = []
        for i in range(len(mesh.vertices)):
            count = max(1, vertex_color_count[i])
            cr, cg, cb = vertex_color_sum[i]
            colors.append((cr / count, cg / count, cb / count))

        # Tracks faces that will be rendered in the textured pass, so we can
        # omit them from the solid base pass and avoid "solid under alpha".
        textured_face_mask = [False for _ in mesh.faces]

        if not build_textures:
            return

        if not self.opengl_enabled or not mesh.texcoords:
            return

        GL = self.GL

        textured_batch_data_static: Dict[int, Dict[str, List[float]]] = {}
        textured_batch_data_model: Dict[int, Dict[str, List[float]]] = {}
        textured_batch_data_model_by_instance: Dict[int, Dict[int, Dict[str, List[float]]]] = {}
        material_to_texture: Dict[str, Optional[str]] = {}
        missing_materials: set[str] = set()
        unique_materials = {m for m in mesh.face_materials if m}
        faces_with_material = 0
        faces_with_uv = 0
        resolved_faces = 0
        static_face_limit = len(self.scene_static_merged_mesh.faces) if self.scene_static_merged_mesh is not None else len(mesh.faces)

        for fi, (a, b, c) in enumerate(mesh.faces):
            if fi >= len(mesh.face_materials) or fi >= len(mesh.face_texcoords):
                continue

            material = mesh.face_materials[fi]
            ta, tb, tc = mesh.face_texcoords[fi]
            if material:
                faces_with_material += 1
            if ta is not None and tb is not None and tc is not None:
                faces_with_uv += 1

            if not material or ta is None or tb is None or tc is None:
                continue
            if not (0 <= ta < len(mesh.texcoords) and 0 <= tb < len(mesh.texcoords) and 0 <= tc < len(mesh.texcoords)):
                continue
            if not _material_expects_texture(material):
                continue

            if material not in material_to_texture:
                material_to_texture[material] = resolve_texture_path(material, self.flightgear_root, self.material_map_path)
            texture_path = material_to_texture[material]
            if not texture_path:
                missing_materials.add(material)
                continue
            texture_id = self._load_gl_texture(texture_path)
            if texture_id is None:
                missing_materials.add(material)
                continue

            resolved_faces += 1
            textured_face_mask[fi] = True

            if fi < static_face_limit:
                target_batches = textured_batch_data_static
            else:
                target_batches = textured_batch_data_model
            batch = target_batches.setdefault(texture_id, {"v": [], "n": [], "t": []})
            for vidx, tidx in ((a, ta), (b, tb), (c, tc)):
                vx, vy, vz = mesh.vertices[vidx]
                nx, ny, nz = normals[vidx]
                u, v = mesh.texcoords[tidx]
                batch["v"].extend((vx, vy, vz))
                batch["n"].extend((nx, ny, nz))
                batch["t"].extend((u, v))

        # Build per-instance model batches from recorded face ranges.
        for instance_idx, instance in enumerate(self.scene_model_instances):
            if instance.mesh_face_start < 0 or instance.mesh_face_count <= 0:
                continue
            start_fi = instance.mesh_face_start
            end_fi = start_fi + instance.mesh_face_count
            if start_fi >= len(mesh.faces):
                continue
            end_fi = min(end_fi, len(mesh.faces))

            per_instance = textured_batch_data_model_by_instance.setdefault(instance_idx, {})
            for fi in range(start_fi, end_fi):
                if fi >= len(mesh.face_materials) or fi >= len(mesh.face_texcoords):
                    continue
                material = mesh.face_materials[fi]
                ta, tb, tc = mesh.face_texcoords[fi]
                if not material or ta is None or tb is None or tc is None:
                    continue
                if not _material_expects_texture(material):
                    continue
                if not (0 <= ta < len(mesh.texcoords) and 0 <= tb < len(mesh.texcoords) and 0 <= tc < len(mesh.texcoords)):
                    continue

                if material not in material_to_texture:
                    material_to_texture[material] = resolve_texture_path(material, self.flightgear_root, self.material_map_path)
                texture_path = material_to_texture[material]
                if not texture_path:
                    continue
                texture_id = self._load_gl_texture(texture_path)
                if texture_id is None:
                    continue

                a, b, c = mesh.faces[fi]
                batch = per_instance.setdefault(texture_id, {"v": [], "n": [], "t": []})
                for vidx, tidx in ((a, ta), (b, tb), (c, tc)):
                    vx, vy, vz = mesh.vertices[vidx]
                    nx, ny, nz = normals[vidx]
                    u, v = mesh.texcoords[tidx]
                    batch["v"].extend((vx, vy, vz))
                    batch["n"].extend((nx, ny, nz))
                    batch["t"].extend((u, v))

        def _batches_from_dict(batch_dict: Dict[int, Dict[str, List[float]]]) -> List[Tuple[int, object, object, object, int]]:
            built: List[Tuple[int, object, object, object, int]] = []
            for texture_id, batch in batch_dict.items():
                vertex_count = len(batch["v"]) // 3
                if vertex_count <= 0:
                    continue
                vbuf = (GL.GLfloat * len(batch["v"]))(*batch["v"])
                nbuf = (GL.GLfloat * len(batch["n"]))(*batch["n"])
                tbuf = (GL.GLfloat * len(batch["t"]))(*batch["t"])
                built.append((texture_id, vbuf, nbuf, tbuf, vertex_count))
            return built

        self.gl_textured_static_batches = _batches_from_dict(textured_batch_data_static)
        self.gl_textured_model_batches = _batches_from_dict(textured_batch_data_model)
        self.gl_textured_model_batches_by_instance = {
            idx: _batches_from_dict(batch_dict)
            for idx, batch_dict in textured_batch_data_model_by_instance.items()
        }
        self.gl_textured_batches = self.gl_textured_static_batches + self.gl_textured_model_batches

        resolved_materials = sum(1 for _m, path in material_to_texture.items() if path)
        self.last_debug = {
            "faces_with_material": faces_with_material,
            "faces_with_uv": faces_with_uv,
            "resolved_faces": resolved_faces,
            "unique_materials": len(unique_materials),
            "resolved_materials": resolved_materials,
            "missing_materials": sorted(missing_materials)[:8],
            "textures_loaded": len(self.texture_id_by_path),
        }
        self._compute_uv_density_debug(mesh)

        flat_vertices: List[float] = []
        flat_normals: List[float] = []
        flat_colors: List[float] = []
        flat_indices_all: List[int] = []
        flat_indices_base: List[int] = []

        for vx, vy, vz in mesh.vertices:
            flat_vertices.extend((vx, vy, vz))
        for nx, ny, nz in normals:
            flat_normals.extend((nx, ny, nz))
        for cr, cg, cb in colors:
            flat_colors.extend((cr, cg, cb))
        for fi, (a, b, c) in enumerate(mesh.faces):
            flat_indices_all.extend((a, b, c))
            if not textured_face_mask[fi]:
                flat_indices_base.extend((a, b, c))

        self.gl_vertex_buffer = (GL.GLfloat * len(flat_vertices))(*flat_vertices) if self.opengl_enabled else None
        self.gl_normal_buffer = (GL.GLfloat * len(flat_normals))(*flat_normals) if self.opengl_enabled else None
        self.gl_color_buffer = (GL.GLfloat * len(flat_colors))(*flat_colors) if self.opengl_enabled else None
        self.gl_index_buffer = (GL.GLuint * len(flat_indices_all))(*flat_indices_all) if self.opengl_enabled else None
        self.gl_index_count = len(flat_indices_all)
        self.gl_index_buffer_base = (GL.GLuint * len(flat_indices_base))(*flat_indices_base) if self.opengl_enabled else None
        self.gl_index_count_base = len(flat_indices_base)

    def _rebuild_model_textured_batches_only(self: "BTGDisplayApp", selected_idx: Optional[int] = None) -> bool:
        if not self.opengl_enabled:
            return False
        if self.mesh is None or not self.mesh.vertices or not self.mesh.faces or not self.mesh.texcoords:
            return False
        if self.scene_static_merged_mesh is None:
            return False

        GL = self.GL
        mesh = self.mesh
        static_face_limit = len(self.scene_static_merged_mesh.faces)
        if static_face_limit >= len(mesh.faces):
            self.gl_textured_model_batches = []
            self.gl_textured_batches = self.gl_textured_static_batches
            return True

        if selected_idx is not None and not (0 <= selected_idx < len(self.scene_model_instances)):
            return False

        textured_batch_data_model: Dict[int, Dict[str, List[float]]] = {}
        material_to_texture: Dict[str, Optional[str]] = {}

        if selected_idx is None:
            face_ranges = [(static_face_limit, len(mesh.faces))]
        else:
            inst = self.scene_model_instances[selected_idx]
            if inst.mesh_face_start < 0 or inst.mesh_face_count <= 0:
                return False
            start = max(static_face_limit, inst.mesh_face_start)
            end = min(len(mesh.faces), inst.mesh_face_start + inst.mesh_face_count)
            face_ranges = [(start, end)]

        for range_start, range_end in face_ranges:
            for fi in range(range_start, range_end):
                if fi >= len(mesh.face_materials) or fi >= len(mesh.face_texcoords):
                    continue
                material = mesh.face_materials[fi]
                ta, tb, tc = mesh.face_texcoords[fi]
                if not material or ta is None or tb is None or tc is None:
                    continue
                if material == "ac_object" or material.startswith("__acmat__:"):
                    continue

                if not (0 <= ta < len(mesh.texcoords) and 0 <= tb < len(mesh.texcoords) and 0 <= tc < len(mesh.texcoords)):
                    continue

                if material not in material_to_texture:
                    material_to_texture[material] = resolve_texture_path(material, self.flightgear_root, self.material_map_path)
                texture_path = material_to_texture[material]
                if not texture_path:
                    continue
                texture_id = self._load_gl_texture(texture_path)
                if texture_id is None:
                    continue

                a, b, c = mesh.faces[fi]
                va = mesh.vertices[a]
                vb = mesh.vertices[b]
                vc = mesh.vertices[c]
                normal = v_norm(v_cross(v_sub(vb, va), v_sub(vc, va)))

                batch = textured_batch_data_model.setdefault(texture_id, {"v": [], "n": [], "t": []})
                for vidx, tidx in ((a, ta), (b, tb), (c, tc)):
                    vx, vy, vz = mesh.vertices[vidx]
                    u, v = mesh.texcoords[tidx]
                    batch["v"].extend((vx, vy, vz))
                    batch["n"].extend((normal[0], normal[1], normal[2]))
                    batch["t"].extend((u, v))

        built_model: List[Tuple[int, object, object, object, int]] = []
        for texture_id, batch in textured_batch_data_model.items():
            vertex_count = len(batch["v"]) // 3
            if vertex_count <= 0:
                continue
            vbuf = (GL.GLfloat * len(batch["v"]))(*batch["v"])
            nbuf = (GL.GLfloat * len(batch["n"]))(*batch["n"])
            tbuf = (GL.GLfloat * len(batch["t"]))(*batch["t"])
            built_model.append((texture_id, vbuf, nbuf, tbuf, vertex_count))

        if selected_idx is None:
            self.gl_textured_model_batches = built_model
        else:
            self.gl_textured_model_batches_by_instance[selected_idx] = built_model
            merged_model_batches: List[Tuple[int, object, object, object, int]] = []
            for idx in sorted(self.gl_textured_model_batches_by_instance.keys()):
                merged_model_batches.extend(self.gl_textured_model_batches_by_instance[idx])
            self.gl_textured_model_batches = merged_model_batches
        self.gl_textured_batches = self.gl_textured_static_batches + self.gl_textured_model_batches
        return True

    def _load_gl_texture(self: "BTGDisplayApp", texture_path: str) -> Optional[int]:
        if not self.opengl_enabled:
            return None
        cached = self.texture_id_by_path.get(texture_path)
        if cached is not None:
            if cached not in self.texture_has_alpha_by_id:
                self.texture_has_alpha_by_id[cached] = bool(self.texture_alpha_by_path.get(texture_path, False))
            return cached

        GL = self.GL
        try:
            surface = pygame.image.load(texture_path)
            surface = surface.convert_alpha()
            width, height = surface.get_size()
            rgba = pygame.image.tostring(surface, "RGBA", True)
        except Exception:
            ext = os.path.splitext(texture_path)[1].lower()
            if ext in {".rgb", ".rgba", ".sgi", ".bw"}:
                decoded = _sgi_to_rgba(texture_path)
                if decoded is None:
                    return None
                width, height, rgba = decoded
            else:
                return None

        # RGBA bytes are tightly packed; mark textures with any non-opaque
        # alpha so we can render them in a separate blended pass.
        has_alpha = False
        for i in range(3, len(rgba), 4):
            if rgba[i] != 255:
                has_alpha = True
                break

        tex_id = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
        anisotropy = float(getattr(self, "gl_texture_anisotropy", 1.0))
        if anisotropy > 1.0:
            try:
                # 0x84FE = GL_TEXTURE_MAX_ANISOTROPY_EXT
                GL.glTexParameterf(GL.GL_TEXTURE_2D, 0x84FE, anisotropy)
            except Exception:
                pass
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGBA,
            width,
            height,
            0,
            GL.GL_RGBA,
            GL.GL_UNSIGNED_BYTE,
            rgba,
        )
        if hasattr(GL, "glGenerateMipmap"):
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

        self.texture_id_by_path[texture_path] = tex_id
        self.texture_has_alpha_by_id[tex_id] = has_alpha
        self.texture_alpha_by_path[texture_path] = has_alpha
        return tex_id

    def _mesh_bounds(self: "BTGDisplayApp", mesh: BTGMesh) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
        if not mesh.vertices:
            return None
        xs = [v[0] for v in mesh.vertices]
        ys = [v[1] for v in mesh.vertices]
        zs = [v[2] for v in mesh.vertices]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def _reset_camera_to_mesh(self: "BTGDisplayApp", mesh: BTGMesh) -> None:
        bounds = self._mesh_bounds(mesh)
        self.mesh_bounds = bounds
        if bounds is None:
            self.camera_pos = [0.0, -120.0, 60.0]
            self.yaw = 0.0
            self.pitch = -15.0
            return

        (min_x, min_y, min_z), (max_x, max_y, max_z) = bounds
        cx = (min_x + max_x) * 0.5
        cy = (min_y + max_y) * 0.5
        cz = (min_z + max_z) * 0.5

        span = max(10.0, max_x - min_x, max_y - min_y, max_z - min_z)
        camera_y = cy - span * self.camera_frame_distance_factor
        camera_z = cz + span * self.camera_frame_height_factor
        self.camera_pos = [cx, camera_y, camera_z]
        self.yaw = 0.0
        rel_y = cy - camera_y
        rel_z = cz - camera_z
        if self.opengl_enabled:
            # OpenGL projection path uses -Z as forward depth. Reframe pitch must
            # keep the target centered in that basis (without changing movement/labels math).
            self.pitch = math.degrees(math.atan2(rel_y, -rel_z))
        else:
            self.pitch = math.degrees(math.atan2(rel_z, rel_y))

    def _current_far_clip(self: "BTGDisplayApp") -> float:
        manual_far = float(getattr(self, "far_clip_distance", 0.0))
        if manual_far > 0.0:
            return max(self.near_plane * 4.0, manual_far)

        if self.mesh_bounds is None:
            return 2000.0

        (min_x, min_y, min_z), (max_x, max_y, max_z) = self.mesh_bounds
        corners = [
            (min_x, min_y, min_z),
            (min_x, min_y, max_z),
            (min_x, max_y, min_z),
            (min_x, max_y, max_z),
            (max_x, min_y, min_z),
            (max_x, min_y, max_z),
            (max_x, max_y, min_z),
            (max_x, max_y, max_z),
        ]

        cx, cy, cz = self.camera_pos
        max_dist = 0.0
        for x, y, z in corners:
            dx = x - cx
            dy = y - cy
            dz = z - cz
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if dist > max_dist:
                max_dist = dist

        return max(self.near_plane * 4.0, max_dist * 1.25)

    def _project(self: "BTGDisplayApp", v_cam: Tuple[float, float, float], width: int, height: int) -> Optional[Tuple[float, float]]:
        x, y, z = v_cam
        if y <= self.near_plane:
            return None
        focal = (0.5 * height) / math.tan(math.radians(self.fov_deg) * 0.5)
        sx = (width * 0.5) + (x * focal / y)
        sy = (height * 0.5) - (z * focal / y)
        return (sx, sy)

    def _grid_anchor_z(self: "BTGDisplayApp") -> float:
        return float(self.grid_z_height)

    def _clip_segment_to_near_plane(
        self: "BTGDisplayApp",
        a_cam: Tuple[float, float, float],
        b_cam: Tuple[float, float, float],
    ) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
        near = self.near_plane
        ay = a_cam[1]
        by = b_cam[1]
        if ay <= near and by <= near:
            return None
        if ay > near and by > near:
            return a_cam, b_cam
        denom = by - ay
        if abs(denom) < 1e-9:
            return None
        t = (near - ay) / denom
        ix = a_cam[0] + (b_cam[0] - a_cam[0]) * t
        iy = near + 1e-4
        iz = a_cam[2] + (b_cam[2] - a_cam[2]) * t
        if ay <= near:
            return (ix, iy, iz), b_cam
        return a_cam, (ix, iy, iz)

    def _draw_xy_grid(self: "BTGDisplayApp") -> None:
        spacing_m = max(0.01, float(self.grid_spacing_units))
        grid_size = max(1.0, float(self.grid_size_units))
        half_extent = grid_size * 0.5
        max_index = int(math.floor(half_extent / spacing_m))
        major_every = 5

        grid_z = self._grid_anchor_z()
        center_x = 0.0
        center_y = 0.0

        line_positions = [idx * spacing_m for idx in range(-max_index, max_index + 1)]
        if -half_extent not in line_positions:
            line_positions.append(-half_extent)
        if half_extent not in line_positions:
            line_positions.append(half_extent)
        line_positions = sorted(set(line_positions))

        if self.opengl_enabled:
            self._draw_xy_grid_gl(center_x, center_y, grid_z, spacing_m, major_every, line_positions, half_extent)
            return

        width, height = self.size
        cam = tuple(self.camera_pos)

        for value in line_positions:
            x = center_x + value
            y = center_y + value

            x_line_a = (x, center_y - half_extent, grid_z)
            x_line_b = (x, center_y + half_extent, grid_z)
            y_line_a = (center_x - half_extent, y, grid_z)
            y_line_b = (center_x + half_extent, y, grid_z)

            is_axis = abs(value) < 1e-9
            is_major = bool(spacing_m > 0.0 and (abs(value / spacing_m) % major_every) < 1e-9)
            if is_axis:
                color_x = (205, 90, 90)
                color_y = (95, 185, 110)
            elif is_major:
                color_x = (88, 108, 132)
                color_y = (88, 108, 132)
            else:
                color_x = (58, 74, 92)
                color_y = (58, 74, 92)

            for (a_world, b_world, color) in (
                (x_line_a, x_line_b, color_x),
                (y_line_a, y_line_b, color_y),
            ):
                a_rel = (a_world[0] - cam[0], a_world[1] - cam[1], a_world[2] - cam[2])
                b_rel = (b_world[0] - cam[0], b_world[1] - cam[1], b_world[2] - cam[2])
                a_cam = rotate_world_to_camera(a_rel, self.yaw, self.pitch)
                b_cam = rotate_world_to_camera(b_rel, self.yaw, self.pitch)
                clipped = self._clip_segment_to_near_plane(a_cam, b_cam)
                if clipped is None:
                    continue
                a_clip, b_clip = clipped
                pa = self._project(a_clip, width, height)
                pb = self._project(b_clip, width, height)
                if pa is None or pb is None:
                    continue
                pygame.draw.line(self.screen, color, (pa[0], pa[1]), (pb[0], pb[1]), 1)

    def _draw_xy_grid_gl(
        self: "BTGDisplayApp",
        center_x: float,
        center_y: float,
        grid_z: float,
        spacing_m: float,
        major_every: int,
        line_positions: List[float],
        half_extent: float,
    ) -> None:
        GL = self.GL
        if GL is None:
            return

        GL.glPushAttrib(GL.GL_ENABLE_BIT | GL.GL_CURRENT_BIT | GL.GL_LINE_BIT)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glDisable(GL.GL_DEPTH_TEST)

        GL.glLineWidth(1.0)
        GL.glBegin(GL.GL_LINES)
        for value in line_positions:
            if abs(value) < 1e-9:
                continue
            x = center_x + value
            y = center_y + value
            is_major = bool(spacing_m > 0.0 and (abs(value / spacing_m) % major_every) < 1e-9)
            if is_major:
                GL.glColor4f(0.36, 0.42, 0.52, 0.65)
            else:
                GL.glColor4f(0.24, 0.30, 0.40, 0.45)
            GL.glVertex3f(x, center_y - half_extent, grid_z)
            GL.glVertex3f(x, center_y + half_extent, grid_z)
            GL.glVertex3f(center_x - half_extent, y, grid_z)
            GL.glVertex3f(center_x + half_extent, y, grid_z)
        GL.glEnd()

        GL.glLineWidth(2.0)
        GL.glBegin(GL.GL_LINES)
        GL.glColor4f(0.85, 0.38, 0.38, 0.9)
        GL.glVertex3f(center_x, center_y - half_extent, grid_z)
        GL.glVertex3f(center_x, center_y + half_extent, grid_z)
        GL.glColor4f(0.40, 0.80, 0.46, 0.9)
        GL.glVertex3f(center_x - half_extent, center_y, grid_z)
        GL.glVertex3f(center_x + half_extent, center_y, grid_z)
        GL.glEnd()

        GL.glLineWidth(1.0)
        GL.glPopAttrib()

    def _render_mesh(self: "BTGDisplayApp") -> None:
        if self.opengl_enabled:
            self._render_mesh_gl()
            return

        if not self.mesh or not self.mesh.vertices or not self.face_data:
            return

        width, height = self.size
        transformed: List[Tuple[float, float, float]] = []
        cam = tuple(self.camera_pos)
        for v in self.mesh.vertices:
            rel = (v[0] - cam[0], v[1] - cam[1], v[2] - cam[2])
            transformed.append(rotate_world_to_camera(rel, self.yaw, self.pitch))

        draw_list: List[Tuple[float, Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]], Tuple[int, int, int]]] = []
        light_dir = v_norm((0.25, 0.4, 1.0))

        for (a, b, c), base_color, normal in self.face_data:
            va = transformed[a]
            vb = transformed[b]
            vc = transformed[c]

            if va[1] <= self.near_plane and vb[1] <= self.near_plane and vc[1] <= self.near_plane:
                continue

            pa = self._project(va, width, height)
            pb = self._project(vb, width, height)
            pc = self._project(vc, width, height)
            if pa is None or pb is None or pc is None:
                continue

            depth = (va[1] + vb[1] + vc[1]) / 3.0
            shade = max(0.25, min(1.0, 0.25 + 0.75 * v_dot(v_norm(normal), light_dir)))
            color = (
                int(base_color[0] * shade),
                int(base_color[1] * shade),
                int(base_color[2] * shade),
            )

            draw_list.append((depth, (pa, pb, pc), color))

        draw_list.sort(key=lambda item: item[0], reverse=True)

        for _depth, poly, color in draw_list:
            if not self.wireframe_mode:
                pygame.draw.polygon(self.screen, color, poly)
            pygame.draw.polygon(self.screen, (22, 22, 22), poly, 1)

    def _render_mesh_gl(self: "BTGDisplayApp") -> None:
        if not self.mesh:
            return
        if self.gl_vertex_buffer is None or self.gl_normal_buffer is None or self.gl_color_buffer is None:
            return

        GL = self.GL

        GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE if self.wireframe_mode else GL.GL_FILL)

        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEnableClientState(GL.GL_NORMAL_ARRAY)
        GL.glEnableClientState(GL.GL_COLOR_ARRAY)

        GL.glVertexPointer(3, GL.GL_FLOAT, 0, self.gl_vertex_buffer)
        GL.glNormalPointer(GL.GL_FLOAT, 0, self.gl_normal_buffer)
        GL.glColorPointer(3, GL.GL_FLOAT, 0, self.gl_color_buffer)

        draw_count = self.gl_index_count
        draw_buffer = self.gl_index_buffer
        if self.textured_mode and not self.wireframe_mode:
            draw_count = self.gl_index_count_base
            draw_buffer = self.gl_index_buffer_base

        if draw_buffer is not None and draw_count > 0:
            GL.glDrawElements(GL.GL_TRIANGLES, draw_count, GL.GL_UNSIGNED_INT, draw_buffer)

        GL.glDisableClientState(GL.GL_COLOR_ARRAY)
        GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)

        if self.wireframe_mode or not self.textured_mode or not self.gl_textured_batches:
            return

        GL.glEnable(GL.GL_TEXTURE_2D)
        GL.glTexEnvi(GL.GL_TEXTURE_ENV, GL.GL_TEXTURE_ENV_MODE, GL.GL_MODULATE)
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEnableClientState(GL.GL_NORMAL_ARRAY)
        GL.glEnableClientState(GL.GL_TEXTURE_COORD_ARRAY)
        GL.glDepthFunc(GL.GL_LEQUAL)
        GL.glColor4f(1.0, 1.0, 1.0, 1.0)

        opaque_batches: List[Tuple[int, object, object, object, int]] = []
        alpha_batches: List[Tuple[int, object, object, object, int]] = []
        for batch in self.gl_textured_batches:
            texture_id = batch[0]
            if self.texture_has_alpha_by_id.get(texture_id, False):
                alpha_batches.append(batch)
            else:
                opaque_batches.append(batch)

        # Opaque textured geometry first, depth-writing enabled.
        GL.glDisable(GL.GL_BLEND)
        GL.glDepthMask(GL.GL_TRUE)
        for texture_id, vbuf, nbuf, tbuf, vertex_count in opaque_batches:
            GL.glBindTexture(GL.GL_TEXTURE_2D, texture_id)
            GL.glVertexPointer(3, GL.GL_FLOAT, 0, vbuf)
            GL.glNormalPointer(GL.GL_FLOAT, 0, nbuf)
            GL.glTexCoordPointer(2, GL.GL_FLOAT, 0, tbuf)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, vertex_count)

        # Alpha textured geometry next, depth-testing on but depth writes off.
        if alpha_batches:
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glDepthMask(GL.GL_FALSE)
            if hasattr(GL, "GL_ALPHA_TEST") and hasattr(GL, "glAlphaFunc"):
                GL.glEnable(GL.GL_ALPHA_TEST)
                GL.glAlphaFunc(GL.GL_GREATER, 0.01)

            for texture_id, vbuf, nbuf, tbuf, vertex_count in alpha_batches:
                GL.glBindTexture(GL.GL_TEXTURE_2D, texture_id)
                GL.glVertexPointer(3, GL.GL_FLOAT, 0, vbuf)
                GL.glNormalPointer(GL.GL_FLOAT, 0, nbuf)
                GL.glTexCoordPointer(2, GL.GL_FLOAT, 0, tbuf)
                GL.glDrawArrays(GL.GL_TRIANGLES, 0, vertex_count)

            if hasattr(GL, "GL_ALPHA_TEST"):
                GL.glDisable(GL.GL_ALPHA_TEST)

        GL.glDepthMask(GL.GL_TRUE)
        GL.glDepthFunc(GL.GL_LESS)
        GL.glEnable(GL.GL_BLEND)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glDisableClientState(GL.GL_TEXTURE_COORD_ARRAY)
        GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
        GL.glDisable(GL.GL_TEXTURE_2D)

    def _draw_status(self: "BTGDisplayApp") -> None:
        if self.show_debug:
            if self.opengl_enabled:
                self._draw_status_gl()
                return

            label = self.font_small.render(self.status_text, True, (230, 230, 230))
            self.screen.blit(label, (12, self.size[1] - 28))

            info = (
                f"Speed x{self.speed_scale:.2f} | Cam ({self.camera_pos[0]:.1f},"
                f" {self.camera_pos[1]:.1f}, {self.camera_pos[2]:.1f})"
            )
            info_label = self.font_small.render(info, True, (190, 205, 220))
            self.screen.blit(info_label, (12, 12))

            fg_loc = self.flightgear_root if self.flightgear_root else "<unset>"
            fg_label = self.font_small.render(f"FlightGear location: {fg_loc}", True, (165, 188, 206))
            self.screen.blit(fg_label, (12, 112))

            mode = f"Mode: {'Wireframe' if self.wireframe_mode else 'Solid'} | Help: {'ON' if self.show_help else 'OFF'}"
            mode_label = self.font_small.render(mode, True, (175, 195, 210))
            self.screen.blit(mode_label, (12, 32))
            texture_mode_label = self.font_small.render(
                f"Textured view: {'ON' if self.textured_mode else 'OFF'}",
                True,
                (175, 195, 210),
            )
            self.screen.blit(texture_mode_label, (12, 52))
            object_catalog_label = self.font_small.render(
                f"Object catalog entries: {len(self.object_catalog)}",
                True,
                (185, 205, 180),
            )
            self.screen.blit(object_catalog_label, (12, 132))
            nudge_label = self.font_small.render(
            (
                f"Object nudge: {self.object_nudge_step_m:.2f} m"
                f" | Repeat: {self.object_nudge_repeat_delay_s:.2f}s/{self.object_nudge_repeat_interval_s:.2f}s"
                " | [ / ] adjust"
            ),
            True,
            (200, 205, 170),
            )
            self.screen.blit(nudge_label, (12, 92))

        stg_line_y = 212
       
        td = self.last_debug
        ud = self.last_uv_debug
        y = 172
        line1 = (
            f"Tex faces resolved: {td['resolved_faces']}/{td['faces_with_uv']}"
            f" | Materials: {td['resolved_materials']}/{td['unique_materials']}"
        )
        line2 = f"Texture objects loaded: {td['textures_loaded']}"
        line3 = f"UV mats analyzed: {ud.get('materials_analyzed', 0)} | UV faces: {ud.get('faces_analyzed', 0)}"
        line1_surf = self.font_small.render(line1, True, (160, 210, 170))
        line2_surf = self.font_small.render(line2, True, (160, 210, 170))
        line3_surf = self.font_small.render(line3, True, (170, 210, 200))
        self.screen.blit(line1_surf, (12, y))
        y += 20
        self.screen.blit(line2_surf, (12, y))
        y += 20
        self.screen.blit(line3_surf, (12, y))
        y += 20

        missing = td.get("missing_materials", [])
        if missing:
            missing_text = self._menu_tf(
                "debug.missing_mats_fmt",
                "Missing mats: {materials}",
                materials=", ".join(missing),
            )
            missing_surf = self.font_small.render(missing_text, True, (220, 165, 165))
            self.screen.blit(missing_surf, (12, y))
            y += 20

        dense = ud.get("top_dense", [])
        sparse = ud.get("top_sparse", [])
        if dense:
            dense_label = self._menu_t("debug.uv_dense", "UV dense:")
            self.screen.blit(self.font_small.render(dense_label, True, (220, 190, 140)), (12, y))
            for i, row in enumerate(dense[:2]):
                self.screen.blit(self.font_small.render(str(row), True, (220, 190, 140)), (84, y + 20 * i))
            y += 20 * (1 + min(2, len(dense)))
        if sparse:
            sparse_label = self._menu_t("debug.uv_sparse", "UV sparse:")
            self.screen.blit(self.font_small.render(sparse_label, True, (165, 205, 220)), (12, y))
            for i, row in enumerate(sparse[:2]):
                 self.screen.blit(self.font_small.render(str(row), True, (165, 205, 220)), (90, y + 20 * i))
            y += 20 * (1 + min(2, len(sparse)))

            stg_line_y = y

        sd = self.last_stg_debug
        if sd.get("stg_found"):
            stg_line = (
                f"STG entries: {sd.get('entries', 0)} | BTG objs: {sd.get('btg_objects_loaded', 0)}"
                f" | Model objs: {sd.get('model_objects_loaded', 0)}"
                f" | Proxy objs: {sd.get('proxy_objects_loaded', 0)} | Skipped: {sd.get('skipped', 0)}"
            )
            stg_label = self.font_small.render(stg_line, True, (205, 190, 160))
            self.screen.blit(stg_label, (12, stg_line_y))

    def _draw_status_gl(self: "BTGDisplayApp") -> None:
        self._gl_begin_2d()
        self._gl_draw_text(self.status_text, 12, self.size[1] - 28, (230, 230, 230), self.font_small, cache=False)
        info = (
            f"Speed x{self.speed_scale:.2f} | Cam ({self.camera_pos[0]:.1f},"
            f" {self.camera_pos[1]:.1f}, {self.camera_pos[2]:.1f})"
        )
        self._gl_draw_text(info, 12, 12, (190, 205, 220), self.font_small, cache=False)
        fg_loc = self.flightgear_root if self.flightgear_root else "<unset>"
        self._gl_draw_text(f"FlightGear location: {fg_loc}", 12, 112, (165, 188, 206), self.font_small)
        mode = f"Mode: {'Wireframe' if self.wireframe_mode else 'Solid'} | Help: {'ON' if self.show_help else 'OFF'}"
        self._gl_draw_text(mode, 12, 32, (175, 195, 210), self.font_small)
        self._gl_draw_text(
            f"Textured view: {'ON' if self.textured_mode else 'OFF'}",
            12,
            52,
            (175, 195, 210),
            self.font_small,
        )
        self._gl_draw_text(
            f"Object catalog entries: {len(self.object_catalog)}",
            12,
            132,
            (185, 205, 180),
            self.font_small,
        )
        self._gl_draw_text(
            (
                f"Object nudge: {self.object_nudge_step_m:.2f} m"
                f" | Repeat: {self.object_nudge_repeat_delay_s:.2f}s/{self.object_nudge_repeat_interval_s:.2f}s"
                " | [ / ] adjust"
            ),
            12,
            92,
            (200, 205, 170),
            self.font_small,
        )

        stg_line_y = 212
        if self.show_debug:
            td = self.last_debug
            ud = self.last_uv_debug
            y = 172
            line1 = (
                f"Tex faces resolved: {td['resolved_faces']}/{td['faces_with_uv']}"
                f" | Materials: {td['resolved_materials']}/{td['unique_materials']}"
            )
            line2 = f"Texture objects loaded: {td['textures_loaded']}"
            line3 = f"UV mats analyzed: {ud.get('materials_analyzed', 0)} | UV faces: {ud.get('faces_analyzed', 0)}"
            self._gl_draw_text(line1, 12, y, (160, 210, 170), self.font_small)
            y += 20
            self._gl_draw_text(line2, 12, y, (160, 210, 170), self.font_small)
            y += 20
            self._gl_draw_text(line3, 12, y, (170, 210, 200), self.font_small)
            y += 20

            missing = td.get("missing_materials", [])
            if missing:
                self._gl_draw_text("Missing mats: " + ", ".join(missing), 12, y, (220, 165, 165), self.font_small)
                y += 20

            dense = ud.get("top_dense", [])
            sparse = ud.get("top_sparse", [])
            if dense:
                self._gl_draw_text("UV dense:", 12, y, (220, 190, 140), self.font_small)
                for i, row in enumerate(dense[:2]):
                    self._gl_draw_text(str(row), 84, y + 20 * i, (220, 190, 140), self.font_small)
                y += 20 * (1 + min(2, len(dense)))
            if sparse:
                self._gl_draw_text("UV sparse:", 12, y, (165, 205, 220), self.font_small)
                for i, row in enumerate(sparse[:2]):
                    self._gl_draw_text(str(row), 90, y + 20 * i, (165, 205, 220), self.font_small)
                y += 20 * (1 + min(2, len(sparse)))

            stg_line_y = y

        sd = self.last_stg_debug
        if sd.get("stg_found"):
            stg_line = (
                f"STG entries: {sd.get('entries', 0)} | BTG objs: {sd.get('btg_objects_loaded', 0)}"
                f" | Model objs: {sd.get('model_objects_loaded', 0)}"
                f" | Proxy objs: {sd.get('proxy_objects_loaded', 0)} | Skipped: {sd.get('skipped', 0)}"
            )
            self._gl_draw_text(stg_line, 12, stg_line_y, (205, 190, 160), self.font_small)
        self._gl_end_2d()

    def _help_overlay_lines(self: "BTGDisplayApp") -> List[str]:
        source_lines = self.help_overlay_text_lines if self.help_overlay_text_lines else self._default_help_overlay_lines()
        return [self._help_line_with_bindings(line) for line in source_lines]

    def _wrap_text_to_width(self: "BTGDisplayApp", text: str, max_width_px: int) -> List[str]:
        stripped = text.strip()
        if not stripped:
            return [""]
        if self.font_small.size(stripped)[0] <= max_width_px:
            return [stripped]

        words = stripped.split()
        if not words:
            return [stripped]

        wrapped: List[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if self.font_small.size(candidate)[0] <= max_width_px:
                current = candidate
            else:
                wrapped.append(current)
                current = word
        wrapped.append(current)
        return wrapped

    def _wrapped_help_overlay_lines(self: "BTGDisplayApp", max_width_px: int) -> List[str]:
        wrapped: List[str] = []
        for idx, line in enumerate(self._help_overlay_lines()):
            parts = self._wrap_text_to_width(line, max_width_px)
            if idx == 0:
                wrapped.extend(parts)
                continue
            if not parts:
                wrapped.append("")
                continue
            wrapped.append(parts[0])
            for continuation in parts[1:]:
                wrapped.append(f"  {continuation}")
        return wrapped

    def _draw_help_hint(self: "BTGDisplayApp") -> None:
        hint = "Press h for help"
        text_surf = self.font_small.render(hint, True, (205, 220, 230))
        tw, th = text_surf.get_size()
        x = 12
        y = self.size[1] - 56
        bg = pygame.Rect(x - 6, y - 4, tw + 12, th + 8)
        pygame.draw.rect(self.screen, (8, 12, 18), bg)
        pygame.draw.rect(self.screen, (88, 108, 130), bg, 1)
        self.screen.blit(text_surf, (x, y))

    def _draw_help_hint_gl(self: "BTGDisplayApp") -> None:
        hint = "Press h for help"
        tw, th = self.font_small.size(hint)
        x = 12
        y = self.size[1] - 56
        self._gl_begin_2d()
        self._gl_draw_rect(x - 6, y - 4, tw + 12, th + 8, (8 / 255.0, 12 / 255.0, 18 / 255.0, 0.86))
        border = (88 / 255.0, 108 / 255.0, 130 / 255.0, 1.0)
        self._gl_draw_rect(x - 6, y - 4, tw + 12, 1, border)
        self._gl_draw_rect(x - 6, y + th + 3, tw + 12, 1, border)
        self._gl_draw_rect(x - 6, y - 4, 1, th + 8, border)
        self._gl_draw_rect(x + tw + 5, y - 4, 1, th + 8, border)
        self._gl_draw_text(hint, x, y, (205, 220, 230), self.font_small)
        self._gl_end_2d()

    def _draw_help_overlay(self: "BTGDisplayApp") -> None:
        if self.show_menu:
            return

        if not self.show_help:
            if self.opengl_enabled:
                self._draw_help_hint_gl()
            else:
                self._draw_help_hint()
            return

        if self.opengl_enabled:
            self._draw_help_overlay_gl()
            return

        panel_pad = 16
        inner_pad = 14
        line_h = 22
        panel_x = panel_pad
        panel_y = panel_pad
        panel_w = max(280, self.size[0] - panel_pad * 2)
        panel_h = max(160, self.size[1] - panel_pad * 2)
        wrapped_lines = self._wrapped_help_overlay_lines(panel_w - inner_pad * 2)
        max_lines = max(1, (panel_h - inner_pad * 2) // line_h)
        if len(wrapped_lines) > max_lines:
            wrapped_lines = wrapped_lines[: max_lines - 1] + ["..."]

        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        panel.fill((8, 12, 18, 180))
        self.screen.blit(panel, (panel_x, panel_y))
        pygame.draw.rect(self.screen, (88, 108, 130), (panel_x, panel_y, panel_w, panel_h), 1)

        base_x = panel_x + inner_pad
        base_y = panel_y + inner_pad
        for i, text in enumerate(wrapped_lines):
            color = (245, 245, 245) if i == 0 else (210, 220, 230)
            surf = self.font_small.render(text, True, color)
            self.screen.blit(surf, (base_x, base_y + i * line_h))

    def _draw_help_overlay_gl(self: "BTGDisplayApp") -> None:
        panel_pad = 16
        inner_pad = 14
        line_h = 22
        panel_x = panel_pad
        panel_y = panel_pad
        panel_w = max(280, self.size[0] - panel_pad * 2)
        panel_h = max(160, self.size[1] - panel_pad * 2)
        wrapped_lines = self._wrapped_help_overlay_lines(panel_w - inner_pad * 2)
        max_lines = max(1, (panel_h - inner_pad * 2) // line_h)
        if len(wrapped_lines) > max_lines:
            wrapped_lines = wrapped_lines[: max_lines - 1] + ["..."]

        self._gl_begin_2d()
        self._gl_draw_rect(panel_x, panel_y, panel_w, panel_h, (8 / 255.0, 12 / 255.0, 18 / 255.0, 0.75))
        border = (88 / 255.0, 108 / 255.0, 130 / 255.0, 1.0)
        self._gl_draw_rect(panel_x, panel_y, panel_w, 1, border)
        self._gl_draw_rect(panel_x, panel_y + panel_h - 1, panel_w, 1, border)
        self._gl_draw_rect(panel_x, panel_y, 1, panel_h, border)
        self._gl_draw_rect(panel_x + panel_w - 1, panel_y, 1, panel_h, border)

        base_x = panel_x + inner_pad
        base_y = panel_y + inner_pad
        for i, text in enumerate(wrapped_lines):
            color = (245, 245, 245) if i == 0 else (210, 220, 230)
            self._gl_draw_text(text, base_x, base_y + i * line_h, color, self.font_small)
        self._gl_end_2d()

    def render(self: "BTGDisplayApp") -> None:
        if self.opengl_enabled:
            GL = self.GL
            GLU = self.GLU
            self._ensure_gl_text_cache()

            w, h = self.size
            GL.glViewport(0, 0, w, max(1, h))
            GL.glClearColor(0.08, 0.11, 0.16, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

            self._gl_begin_2d()
            GL.glBegin(GL.GL_QUADS)
            GL.glColor4f(10 / 255.0, 15 / 255.0, 24 / 255.0, 1.0)
            GL.glVertex2f(0, h)
            GL.glVertex2f(w, h)
            GL.glColor4f(28 / 255.0, 36 / 255.0, 52 / 255.0, 1.0)
            GL.glVertex2f(w, 0)
            GL.glVertex2f(0, 0)
            GL.glEnd()
            self._gl_end_2d()

            GL.glMatrixMode(GL.GL_PROJECTION)
            GL.glLoadIdentity()
            GLU.gluPerspective(self.fov_deg, w / max(1.0, float(h)), self.near_plane, self._current_far_clip())

            GL.glMatrixMode(GL.GL_MODELVIEW)
            GL.glLoadIdentity()
            GL.glRotatef(-self.pitch, 1.0, 0.0, 0.0)
            GL.glRotatef(-self.yaw, 0.0, 0.0, 1.0)
            GL.glTranslatef(-self.camera_pos[0], -self.camera_pos[1], -self.camera_pos[2])

            self._draw_xy_grid()
            self._render_mesh()
            self.frame_projected_model_labels = self._collect_projected_model_labels()
            self._draw_model_labels()
            self._draw_crosshair()
            self._draw_crosshair_hover_label()
            self.frame_projected_model_labels = None
            self._draw_help_overlay()
            if self.show_menu:
                self._draw_menu()
            self._draw_status()
            self._draw_add_object_preview()
            pygame.display.flip()
            return

        draw_gradient_background(self.screen)
        self._draw_xy_grid()
        self._render_mesh()
        self.frame_projected_model_labels = self._collect_projected_model_labels()
        self._draw_model_labels()
        self._draw_crosshair()
        self._draw_crosshair_hover_label()
        self.frame_projected_model_labels = None
        self._draw_help_overlay()

        if self.show_menu:
            self._draw_menu()

        self._draw_status()
        self._draw_add_object_preview()
        pygame.display.flip()
