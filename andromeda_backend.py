#!/usr/bin/env python3
"""BTG data loading, parsing and scene composition helpers."""

from __future__ import annotations

import gzip
import json
import math
import os
import re
import shlex
import statistics
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

BTG_MAGIC = 0x5347
SUPPORTED_OBJECT_TYPES = {0, 1, 2, 3, 4, 9, 10, 11, 12}
_TEXTURE_INDEX_CACHE: Dict[Tuple[str, ...], Dict[str, List[str]]] = {}
_MATERIAL_OVERRIDE_CACHE: Dict[str, Dict[str, str]] = {}
_FG_MATERIAL_TEXTURE_CACHE: Dict[str, Dict[str, List[str]]] = {}
CONFIG_FILENAME = "flightgear_btg_viewer.json"

TEXTURE_ALIASES: Dict[str, List[str]] = {
    "airport": ["airport"],
    "default": ["grass", "airport"],
    "grass": ["grass"],
    "grasscover": ["grass"],
    "grassland": ["grass"],
    "intermittentstream": ["waterlake"],
    "stream": ["waterlake"],
    "canal": ["waterlake"],
    "lake": ["waterlake", "frozenlake"],
    "road": ["asphalt", "gravel"],
    "freeway": ["asphalt"],
    "railroad": ["gravel", "darkgravel"],
    "drycrop": ["drycrop"],
    "mixedcrop": ["mixedcrop"],
    "irrcroppasturecover": ["irrcrop", "cropgrass"],
    "irrcrop": ["irrcrop"],
    "deciduousforest": ["deciduous", "forest"],
    "evergreenforest": ["evergreen", "coniferousforest"],
    "scrub": ["shrub", "scrub"],
    "scrubcover": ["shrub", "scrub"],
    "urban": ["industrial", "city"],
    "pctiedown": ["asphalt", "carpark"],
}


@dataclass
class ValidationStats:
    filepath: str = ""
    compressed: bool = False
    file_size_bytes: int = 0
    decompressed_size_bytes: int = 0
    version: int = 0
    object_count_header: int = 0
    object_count_parsed: int = 0
    object_type_counts: Dict[int, int] = field(default_factory=dict)
    vertex_count: int = 0
    texcoord_count: int = 0
    triangle_count: int = 0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def print_summary(self) -> None:
        print("=" * 72)
        print("BTG VALIDATION SUMMARY")
        print("=" * 72)
        print(f"File: {self.filepath}")
        print(f"Compressed (.gz): {self.compressed}")
        print(f"File size: {self.file_size_bytes} bytes")
        print(f"Payload size: {self.decompressed_size_bytes} bytes")
        print(f"Version: {self.version}")
        print(f"Object count (header): {self.object_count_header}")
        print(f"Object count (parsed): {self.object_count_parsed}")
        print(f"Vertices: {self.vertex_count}")
        print(f"Texcoords: {self.texcoord_count}")
        print(f"Triangles: {self.triangle_count}")

        print("Object types:")
        if self.object_type_counts:
            for obj_type in sorted(self.object_type_counts):
                print(f"  - {obj_type}: {self.object_type_counts[obj_type]}")
        else:
            print("  - none")

        print(f"Warnings: {len(self.warnings)}")
        for warning in self.warnings:
            print(f"  - {warning}")

        print(f"Errors: {len(self.errors)}")
        for error in self.errors:
            print(f"  - {error}")
        print("=" * 72)


@dataclass
class BTGMesh:
    vertices: List[Tuple[float, float, float]] = field(default_factory=list)
    faces: List[Tuple[int, int, int]] = field(default_factory=list)
    texcoords: List[Tuple[float, float]] = field(default_factory=list)
    face_texcoords: List[Tuple[Optional[int], Optional[int], Optional[int]]] = field(default_factory=list)
    face_materials: List[str] = field(default_factory=list)
    face_colors: List[Tuple[int, int, int]] = field(default_factory=list)
    center_ecef: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 0.0


@dataclass
class STGObjectEntry:
    directive: str
    object_path: str
    lon_deg: Optional[float] = None
    lat_deg: Optional[float] = None
    elev_m: Optional[float] = None
    heading_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0


@dataclass
class STGModelInstance:
    template_mesh: BTGMesh
    origin_enu: Tuple[float, float, float]
    heading_deg: float
    pitch_deg: float
    roll_deg: float
    is_ac_model: bool = False
    source_path: str = ""
    stg_directive: str = ""
    stg_entry_index: Optional[int] = None
    render_anchor_enu: Optional[Tuple[float, float, float]] = None
    offset_yaw_deg: float = 0.0
    offset_pitch_deg: float = 0.0
    offset_roll_deg: float = 0.0
    offset_x_m: float = 0.0
    offset_y_m: float = 0.0
    offset_z_m: float = 0.0
    mesh_vertex_start: int = -1
    mesh_vertex_count: int = 0
    mesh_face_start: int = -1
    mesh_face_count: int = 0


@dataclass(frozen=True)
class KeyBinding:
    key_code: int
    mods: int = 0


class BTGFormatError(ValueError):
    pass


def clear_texture_caches() -> None:
    _TEXTURE_INDEX_CACHE.clear()
    _FG_MATERIAL_TEXTURE_CACHE.clear()


def _normalize_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _material_candidates(material_name: str) -> List[str]:
    normalized = _normalize_key(material_name)
    candidates = [normalized]

    if normalized in TEXTURE_ALIASES:
        candidates.extend(_normalize_key(name) for name in TEXTURE_ALIASES[normalized])

    token: List[str] = []
    for ch in material_name:
        if ch.isupper() and token:
            piece = _normalize_key("".join(token))
            if len(piece) > 2:
                candidates.append(piece)
            token = [ch]
        elif ch.isalnum():
            token.append(ch)
        elif token:
            piece = _normalize_key("".join(token))
            if len(piece) > 2:
                candidates.append(piece)
            token = []
    if token:
        piece = _normalize_key("".join(token))
        if len(piece) > 2:
            candidates.append(piece)

    if normalized.startswith("pa"):
        candidates.extend(["airport", "asphalt"])

    deduped: List[str] = []
    seen = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def _is_primary_texture(filename: str) -> bool:
    lowered = filename.lower()
    blocked = ("mask", "overlay", "relief", "colors", "structure")
    return lowered.endswith((".png", ".jpg", ".jpeg", ".dds", ".tga", ".bmp", ".rgb", ".rgba", ".sgi", ".bw")) and not any(tag in lowered for tag in blocked)


def _texture_search_roots(fg_root: str) -> List[str]:
    if not fg_root:
        return []

    roots: List[str] = []
    root_path = os.path.abspath(fg_root)
    root_name = os.path.basename(root_path).lower()

    candidates = [
        os.path.join(root_path, "Textures"),
        os.path.join(root_path, "Textures", "Terrain"),
        os.path.join(root_path, "Textures", "Runway"),
    ]

    if root_name in {"textures", "terrain", "runway"}:
        candidates.append(root_path)

    for candidate in candidates:
        if os.path.isdir(candidate) and candidate not in roots:
            roots.append(candidate)
    return roots


def _texture_ext_rank(path: str) -> int:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".png":
        return 0
    if ext == ".jpg":
        return 1
    if ext == ".jpeg":
        return 2
    if ext == ".bmp":
        return 3
    if ext == ".tga":
        return 4
    if ext == ".rgb":
        return 5
    if ext == ".rgba":
        return 6
    if ext == ".sgi":
        return 7
    if ext == ".bw":
        return 8
    if ext == ".dds":
        return 9
    return 10


def _sort_texture_paths(paths: List[str]) -> List[str]:
    return sorted(paths, key=lambda p: (_texture_ext_rank(p), len(p), p.lower()))


def _candidate_fgdata_roots(path_hint: str) -> List[str]:
    if not path_hint:
        return []

    hint = os.path.abspath(path_hint)
    candidates: List[str] = [hint]

    current = hint
    for _ in range(3):
        current = os.path.dirname(current)
        if current and current not in candidates:
            candidates.append(current)

    parent = os.path.dirname(hint)
    try:
        for name in os.listdir(parent):
            full = os.path.join(parent, name)
            if not os.path.isdir(full):
                continue
            lowered = name.lower()
            if lowered.startswith("flightgear") or lowered in {"fgdata", "flightgear-data"}:
                if full not in candidates:
                    candidates.append(full)
    except Exception:
        pass

    deduped: List[str] = []
    seen: Set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def resolve_fgdata_root(path_hint: str) -> str:
    for candidate in _candidate_fgdata_roots(path_hint):
        if os.path.isdir(os.path.join(candidate, "Textures")) and os.path.isdir(os.path.join(candidate, "Materials")):
            return candidate
    return ""


def _material_xml_entry_files(fg_root: str) -> List[str]:
    data_root = resolve_fgdata_root(fg_root)
    if not data_root:
        return []

    candidates = [
        os.path.join(data_root, "Materials", "default", "materials.xml"),
        os.path.join(data_root, "Materials", "materials.xml"),
        os.path.join(data_root, "materials.xml"),
    ]
    return [path for path in candidates if os.path.isfile(path)]


def _material_path_candidates(relative_path: str, fg_root: str) -> List[str]:
    candidates: List[str] = []
    path_value = relative_path.replace("\\", "/").strip()
    if not path_value:
        return candidates

    if os.path.isabs(path_value) and os.path.isfile(path_value):
        candidates.append(path_value)

    for root in _texture_search_roots(fg_root):
        candidate = os.path.join(root, path_value)
        if os.path.isfile(candidate):
            candidates.append(candidate)
        candidate = os.path.join(os.path.dirname(root), path_value)
        if os.path.isfile(candidate):
            candidates.append(candidate)

    stem, ext = os.path.splitext(path_value)
    if ext.lower() == ".dds":
        for replacement in (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".rgb", ".rgba", ".sgi", ".bw"):
            repl = stem + replacement
            for root in _texture_search_roots(fg_root):
                candidate = os.path.join(root, repl)
                if os.path.isfile(candidate):
                    candidates.append(candidate)
                candidate = os.path.join(os.path.dirname(root), repl)
                if os.path.isfile(candidate):
                    candidates.append(candidate)

    return _sort_texture_paths(list(dict.fromkeys(candidates)))


def _load_fg_material_texture_map(fg_root: str) -> Dict[str, List[str]]:
    entry_files = _material_xml_entry_files(fg_root)
    if not entry_files:
        return {}

    cache_key = "|".join(sorted(entry_files))
    cached = _FG_MATERIAL_TEXTURE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    mapping: Dict[str, List[str]] = {}
    parsed_files: Set[str] = set()
    data_root = resolve_fgdata_root(fg_root)

    def add_mapping(name: str, texture_ref: str) -> None:
        if not name or not texture_ref:
            return
        resolved = _material_path_candidates(texture_ref, fg_root)
        if not resolved:
            return
        for key in (name, _normalize_key(name)):
            bucket = mapping.setdefault(key, [])
            for path in resolved:
                if path not in bucket:
                    bucket.append(path)
            mapping[key] = _sort_texture_paths(bucket)

    def parse_file(path: str) -> None:
        normalized = os.path.abspath(path)
        if normalized in parsed_files:
            return
        parsed_files.add(normalized)

        try:
            tree = ET.parse(normalized)
        except Exception:
            return

        root_node = tree.getroot()

        include_attr = root_node.attrib.get("include", "").strip()
        if include_attr:
            for include_path in (
                os.path.join(os.path.dirname(normalized), include_attr),
                os.path.join(data_root, include_attr),
            ):
                if os.path.isfile(include_path):
                    parse_file(include_path)
                    break

        for node in root_node.iter():
            include_value = node.attrib.get("include", "").strip()
            if include_value:
                for include_path in (
                    os.path.join(os.path.dirname(normalized), include_value),
                    os.path.join(data_root, include_value),
                ):
                    if os.path.isfile(include_path):
                        parse_file(include_path)
                        break

            if node.tag != "material":
                continue

            names = [
                child.text.strip()
                for child in node.findall("name")
                if child.text and child.text.strip()
            ]
            texture_node = node.find("texture")
            texture_ref = texture_node.text.strip() if texture_node is not None and texture_node.text else ""
            for material_name in names:
                add_mapping(material_name, texture_ref)

    for entry_file in entry_files:
        parse_file(entry_file)

    _FG_MATERIAL_TEXTURE_CACHE[cache_key] = mapping
    return mapping


def _texture_index(fg_root: str) -> Dict[str, List[str]]:
    search_roots = tuple(_texture_search_roots(fg_root))
    cached = _TEXTURE_INDEX_CACHE.get(search_roots)
    if cached is not None:
        return cached

    index: Dict[str, List[str]] = {}
    for root_path in search_roots:
        for walk_root, _dirs, files in os.walk(root_path):
            for filename in files:
                if not _is_primary_texture(filename):
                    continue
                full_path = os.path.join(walk_root, filename)
                stem = os.path.splitext(filename)[0]
                index.setdefault(_normalize_key(stem), []).append(full_path)

    for key, paths in list(index.items()):
        index[key] = _sort_texture_paths(paths)

    _TEXTURE_INDEX_CACHE[search_roots] = index
    return index


def default_material_map_path() -> str:
    return os.path.join(os.path.dirname(__file__), "material_map.json")


def _config_file_path() -> str:
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return os.path.join(os.path.abspath(xdg_config), CONFIG_FILENAME)
    return os.path.join(os.path.expanduser("~/.config"), CONFIG_FILENAME)


def load_viewer_config() -> Dict[str, object]:
    config_path = _config_file_path()
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            return {}
        root = loaded.get("flightgear_root", "")
        material_map = loaded.get("material_map_path", "")
        textured_mode = loaded.get("textured_mode")
        camera_frame_distance_factor = loaded.get("camera_frame_distance_factor")
        camera_frame_height_factor = loaded.get("camera_frame_height_factor")
        preview_panel_width_px = loaded.get("preview_panel_width_px")
        preview_panel_height_px = loaded.get("preview_panel_height_px")
        preview_panel_x_px = loaded.get("preview_panel_x_px")
        preview_panel_y_px = loaded.get("preview_panel_y_px")
        preview_panel_render_mode = loaded.get("preview_panel_render_mode")
        model_angle_step_index = loaded.get("model_angle_step_index")
        model_angle_step_adjust_deg = loaded.get("model_angle_step_adjust_deg")
        object_nudge_step_m = loaded.get("object_nudge_step_m")
        object_nudge_camera_relative = loaded.get("object_nudge_camera_relative")
        object_nudge_repeat_delay_s = loaded.get("object_nudge_repeat_delay_s")
        object_nudge_repeat_interval_s = loaded.get("object_nudge_repeat_interval_s")
        missing_material_color_rgb = loaded.get("missing_material_color_rgb")
        custom_scenery_paths = loaded.get("custom_scenery_paths")
        grid_size_units = loaded.get("grid_size_units")
        grid_spacing_units = loaded.get("grid_spacing_units")
        grid_z_height = loaded.get("grid_z_height")
        camera_pos_enu = loaded.get("camera_pos_enu")
        camera_yaw_deg = loaded.get("camera_yaw_deg")
        camera_pitch_deg = loaded.get("camera_pitch_deg")
        near_clip_m = loaded.get("near_clip_m")
        far_clip_m = loaded.get("far_clip_m")
        last_browse_dir = loaded.get("last_browse_dir", "")
        last_add_object_category = loaded.get("last_add_object_category", "")
        help_text_file_path = loaded.get("help_text_file_path", "")
        menu_text_file_path = loaded.get("menu_text_file_path", "")
        result: Dict[str, object] = {}
        if isinstance(root, str):
            result["flightgear_root"] = root
        if isinstance(material_map, str):
            result["material_map_path"] = material_map
        if isinstance(textured_mode, bool):
            result["textured_mode"] = textured_mode
        if isinstance(camera_frame_distance_factor, (int, float)):
            result["camera_frame_distance_factor"] = float(camera_frame_distance_factor)
        if isinstance(camera_frame_height_factor, (int, float)):
            result["camera_frame_height_factor"] = float(camera_frame_height_factor)
        if isinstance(preview_panel_width_px, (int, float)):
            result["preview_panel_width_px"] = float(preview_panel_width_px)
        if isinstance(preview_panel_height_px, (int, float)):
            result["preview_panel_height_px"] = float(preview_panel_height_px)
        if isinstance(preview_panel_x_px, (int, float)):
            result["preview_panel_x_px"] = float(preview_panel_x_px)
        if isinstance(preview_panel_y_px, (int, float)):
            result["preview_panel_y_px"] = float(preview_panel_y_px)
        if isinstance(preview_panel_render_mode, str):
            result["preview_panel_render_mode"] = preview_panel_render_mode
        if isinstance(model_angle_step_index, int):
            result["model_angle_step_index"] = int(model_angle_step_index)
        if isinstance(model_angle_step_adjust_deg, (int, float)):
            result["model_angle_step_adjust_deg"] = float(model_angle_step_adjust_deg)
        if isinstance(object_nudge_step_m, (int, float)):
            result["object_nudge_step_m"] = float(object_nudge_step_m)
        if isinstance(object_nudge_camera_relative, bool):
            result["object_nudge_camera_relative"] = object_nudge_camera_relative
        if isinstance(object_nudge_repeat_delay_s, (int, float)):
            result["object_nudge_repeat_delay_s"] = float(object_nudge_repeat_delay_s)
        if isinstance(object_nudge_repeat_interval_s, (int, float)):
            result["object_nudge_repeat_interval_s"] = float(object_nudge_repeat_interval_s)
        if (
            isinstance(missing_material_color_rgb, list)
            and len(missing_material_color_rgb) == 3
            and all(isinstance(v, (int, float)) for v in missing_material_color_rgb)
        ):
            result["missing_material_color_rgb"] = [int(missing_material_color_rgb[0]), int(missing_material_color_rgb[1]), int(missing_material_color_rgb[2])]
        if isinstance(custom_scenery_paths, list):
            cleaned = [str(path) for path in custom_scenery_paths if isinstance(path, str) and path.strip()]
            result["custom_scenery_paths"] = cleaned
        if isinstance(grid_size_units, (int, float)):
            result["grid_size_units"] = float(grid_size_units)
        if isinstance(grid_spacing_units, (int, float)):
            result["grid_spacing_units"] = float(grid_spacing_units)
        if isinstance(grid_z_height, (int, float)):
            result["grid_z_height"] = float(grid_z_height)
        if (
            isinstance(camera_pos_enu, list)
            and len(camera_pos_enu) == 3
            and all(isinstance(v, (int, float)) for v in camera_pos_enu)
        ):
            result["camera_pos_enu"] = [float(camera_pos_enu[0]), float(camera_pos_enu[1]), float(camera_pos_enu[2])]
        if isinstance(camera_yaw_deg, (int, float)):
            result["camera_yaw_deg"] = float(camera_yaw_deg)
        if isinstance(camera_pitch_deg, (int, float)):
            result["camera_pitch_deg"] = float(camera_pitch_deg)
        if isinstance(near_clip_m, (int, float)):
            result["near_clip_m"] = float(near_clip_m)
        if isinstance(far_clip_m, (int, float)):
            result["far_clip_m"] = float(far_clip_m)
        if isinstance(last_browse_dir, str):
            result["last_browse_dir"] = last_browse_dir
        if isinstance(last_add_object_category, str):
            result["last_add_object_category"] = last_add_object_category
        if isinstance(help_text_file_path, str):
            result["help_text_file_path"] = help_text_file_path
        if isinstance(menu_text_file_path, str):
            result["menu_text_file_path"] = menu_text_file_path
        return result
    except Exception:
        return {}


def save_viewer_config(
    flightgear_root: str,
    material_map_path: str,
    textured_mode: bool,
    camera_frame_distance_factor: float,
    camera_frame_height_factor: float,
    preview_panel_width_px: float = 384.0,
    preview_panel_height_px: float = 288.0,
    preview_panel_x_px: float = 876.0,
    preview_panel_y_px: float = 256.0,
    preview_panel_render_mode: str = "textured",
    model_angle_step_index: int = 0,
    object_nudge_step_m: float = 1.0,
    object_nudge_camera_relative: bool = True,
    object_nudge_repeat_delay_s: float = 0.25,
    object_nudge_repeat_interval_s: float = 0.06,
    missing_material_color_rgb: Tuple[int, int, int] = (255, 0, 255),
    custom_scenery_paths: Optional[Sequence[str]] = None,
    grid_size_units: float = 100.0,
    grid_spacing_units: float = 100.0,
    grid_z_height: float = 0.0,
    camera_pos_enu: Tuple[float, float, float] = (0.0, -120.0, 60.0),
    camera_yaw_deg: float = 90.0,
    camera_pitch_deg: float = -15.0,
    near_clip_m: float = 0.5,
    far_clip_m: float = 0.0,
    last_browse_dir: str = "",
    last_add_object_category: str = "",
    help_text_file_path: str = "",
    menu_text_file_path: str = "",
) -> None:
    config_path = _config_file_path()
    parent = os.path.dirname(config_path)
    os.makedirs(parent, exist_ok=True)
    payload = {
        "flightgear_root": flightgear_root,
        "material_map_path": material_map_path,
        "textured_mode": textured_mode,
        "camera_frame_distance_factor": camera_frame_distance_factor,
        "camera_frame_height_factor": camera_frame_height_factor,
        "preview_panel_width_px": preview_panel_width_px,
        "preview_panel_height_px": preview_panel_height_px,
        "preview_panel_x_px": preview_panel_x_px,
        "preview_panel_y_px": preview_panel_y_px,
        "preview_panel_render_mode": preview_panel_render_mode,
        "model_angle_step_index": int(model_angle_step_index),
        "object_nudge_step_m": object_nudge_step_m,
        "object_nudge_camera_relative": bool(object_nudge_camera_relative),
        "object_nudge_repeat_delay_s": object_nudge_repeat_delay_s,
        "object_nudge_repeat_interval_s": object_nudge_repeat_interval_s,
        "missing_material_color_rgb": [
            int(max(0, min(255, missing_material_color_rgb[0]))),
            int(max(0, min(255, missing_material_color_rgb[1]))),
            int(max(0, min(255, missing_material_color_rgb[2]))),
        ],
        "custom_scenery_paths": [path for path in (custom_scenery_paths or []) if path],
        "grid_size_units": float(max(1.0, grid_size_units)),
        "grid_spacing_units": float(max(0.01, grid_spacing_units)),
        "grid_z_height": float(grid_z_height),
        "camera_pos_enu": [
            float(camera_pos_enu[0]),
            float(camera_pos_enu[1]),
            float(camera_pos_enu[2]),
        ],
        "camera_yaw_deg": float(camera_yaw_deg),
        "camera_pitch_deg": float(camera_pitch_deg),
        "near_clip_m": float(max(0.01, near_clip_m)),
        "far_clip_m": float(max(0.0, far_clip_m)),
        "last_browse_dir": last_browse_dir,
        "last_add_object_category": last_add_object_category,
        "help_text_file_path": help_text_file_path,
        "menu_text_file_path": menu_text_file_path,
    }
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def _load_material_overrides(material_map_path: str) -> Dict[str, str]:
    resolved_path = material_map_path or default_material_map_path()
    cached = _MATERIAL_OVERRIDE_CACHE.get(resolved_path)
    if cached is not None:
        return cached

    data: Dict[str, str] = {}
    try:
        with open(resolved_path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                if isinstance(key, str) and isinstance(value, str):
                    data[key] = value
                    data[_normalize_key(key)] = value
    except Exception:
        data = {}

    _MATERIAL_OVERRIDE_CACHE[resolved_path] = data
    return data


def resolve_texture_path(material_name: str, fg_root: str, material_map_path: str) -> Optional[str]:
    if material_name.startswith("__file__:"):
        direct_path = material_name[len("__file__:") :]
        return direct_path if os.path.isfile(direct_path) else None

    # AC3D material-color surfaces are intentionally imageless.
    if material_name.startswith("__acmat__:"):
        return None

    if not fg_root:
        return None

    resolved_root = resolve_fgdata_root(fg_root) or fg_root

    overrides = _load_material_overrides(material_map_path)
    override_target = overrides.get(material_name) or overrides.get(_normalize_key(material_name))
    if override_target:
        override_candidates = _material_path_candidates(override_target, resolved_root)
        if override_candidates:
            return override_candidates[0]

    fg_material_map = _load_fg_material_texture_map(resolved_root)
    for candidate in _material_candidates(material_name):
        mapped_paths = fg_material_map.get(candidate)
        if mapped_paths:
            return mapped_paths[0]

    index = _texture_index(resolved_root)
    for candidate in _material_candidates(material_name):
        paths = index.get(candidate)
        if paths:
            return paths[0]

    for candidate in _material_candidates(material_name):
        partial_matches: List[str] = []
        for key, paths in index.items():
            if candidate in key or key in candidate:
                partial_matches.extend(paths)
        if partial_matches:
            return sorted(partial_matches)[0]

    return None


def discover_flightgear_root() -> str:
    env_candidates = [
        os.environ.get("FG_ROOT"),
        os.environ.get("FLIGHTGEAR_ROOT"),
        os.environ.get("FG_HOME"),
    ]

    fs_candidates = [
        "/games/flightgear-2024",
        "/usr/share/games/flightgear",
        "/usr/local/share/games/flightgear",
        os.path.expanduser("~/flightgear"),
    ]

    for candidate in env_candidates + fs_candidates:
        if not candidate:
            continue
        root = os.path.abspath(candidate)
        resolved = resolve_fgdata_root(root)
        if resolved:
            return resolved

    return ""


def _header_and_count_sizes(version: int) -> Tuple[int, int]:
    if version >= 10:
        return 12, 4
    return 10, 2


def _geometry_index_size(version: int) -> int:
    return 4 if version >= 10 else 2


def ecef_to_enu_matrix(cx: float, cy: float, cz: float) -> Tuple[Tuple[float, float, float], ...]:
    lon = math.atan2(cy, cx)
    lat = math.atan2(cz, math.sqrt(cx * cx + cy * cy))
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    return (
        (-sin_lon, cos_lon, 0.0),
        (-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat),
        (cos_lat * cos_lon, cos_lat * sin_lon, sin_lat),
    )


def ecef_from_geodetic(lon_deg: float, lat_deg: float, alt_m: float) -> Tuple[float, float, float]:
    a = 6378137.0
    e2 = 6.69437999014e-3

    lon = math.radians(lon_deg)
    lat = math.radians(lat_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    cos_lon = math.cos(lon)
    sin_lon = math.sin(lon)

    n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    x = (n + alt_m) * cos_lat * cos_lon
    y = (n + alt_m) * cos_lat * sin_lon
    z = (n * (1.0 - e2) + alt_m) * sin_lat
    return (x, y, z)


def ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
    a = 6378137.0
    e2 = 6.69437999014e-3

    lon = math.atan2(y, x)
    p = math.sqrt(x * x + y * y)

    if p < 1.0e-9:
        lat = math.pi / 2.0 if z >= 0.0 else -math.pi / 2.0
        b = a * math.sqrt(1.0 - e2)
        alt = abs(z) - b
        return (math.degrees(lon), math.degrees(lat), alt)

    lat = math.atan2(z, p * (1.0 - e2))
    alt = 0.0
    for _ in range(6):
        sin_lat = math.sin(lat)
        cos_lat = math.cos(lat)
        n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        if abs(cos_lat) < 1.0e-12:
            alt = abs(z) - n * (1.0 - e2)
            break
        alt = p / cos_lat - n
        lat = math.atan2(z, p * (1.0 - e2 * (n / (n + alt))))

    return (math.degrees(lon), math.degrees(lat), alt)


def rotate3(v: Tuple[float, float, float], r: Tuple[Tuple[float, float, float], ...]) -> Tuple[float, float, float]:
    x, y, z = v
    return (
        r[0][0] * x + r[0][1] * y + r[0][2] * z,
        r[1][0] * x + r[1][1] * y + r[1][2] * z,
        r[2][0] * x + r[2][1] * y + r[2][2] * z,
    )


def rotate3_inv(v: Tuple[float, float, float], r: Tuple[Tuple[float, float, float], ...]) -> Tuple[float, float, float]:
    x, y, z = v
    return (
        r[0][0] * x + r[1][0] * y + r[2][0] * z,
        r[0][1] * x + r[1][1] * y + r[2][1] * z,
        r[0][2] * x + r[1][2] * y + r[2][2] * z,
    )


def _read_file_bytes(path: str, stats: ValidationStats) -> bytes:
    stats.filepath = os.path.abspath(path)
    stats.compressed = path.lower().endswith(".gz")
    try:
        stats.file_size_bytes = os.path.getsize(path)
    except OSError:
        stats.file_size_bytes = 0

    if stats.compressed:
        try:
            with gzip.open(path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            raise BTGFormatError(f"Invalid gzip stream: {exc}") from exc
    else:
        with open(path, "rb") as handle:
            raw = handle.read()

    stats.decompressed_size_bytes = len(raw)
    return raw


def _parse_geometry_entries(raw: bytes, index_types: Optional[int], object_type: int, version: int) -> List[Dict[str, Optional[int]]]:
    if index_types is None:
        index_types = 0x01 if object_type == 9 else 0x09

    stride_indices = 0
    for bit in range(4):
        if index_types & (1 << bit):
            stride_indices += 1
    if stride_indices == 0:
        return []

    index_size = _geometry_index_size(version)
    stride = stride_indices * index_size
    tuple_count = len(raw) // stride
    entries: List[Dict[str, Optional[int]]] = []

    for i in range(tuple_count):
        cursor = i * stride
        entry: Dict[str, Optional[int]] = {"v": None, "t": None}
        for bit in range(4):
            if index_types & (1 << bit):
                if index_size == 4:
                    idx = struct.unpack_from("<I", raw, cursor)[0]
                else:
                    idx = struct.unpack_from("<H", raw, cursor)[0]
                cursor += index_size
                if bit == 0:
                    entry["v"] = idx
                elif bit == 3:
                    entry["t"] = idx
        if entry["v"] is not None:
            entries.append(entry)

    return entries


def _entries_to_faces(
    entries: Sequence[Dict[str, Optional[int]]],
    object_type: int,
) -> Tuple[List[Tuple[int, int, int]], List[Tuple[Optional[int], Optional[int], Optional[int]]]]:
    faces: List[Tuple[int, int, int]] = []
    uv_faces: List[Tuple[Optional[int], Optional[int], Optional[int]]] = []

    if object_type == 10:
        for i in range(0, len(entries) - 2, 3):
            tri = (entries[i], entries[i + 1], entries[i + 2])
            vi = (tri[0]["v"], tri[1]["v"], tri[2]["v"])
            ti = (tri[0]["t"], tri[1]["t"], tri[2]["t"])
            if None not in vi and len({vi[0], vi[1], vi[2]}) == 3:
                faces.append((int(vi[0]), int(vi[1]), int(vi[2])))
                uv_faces.append(ti)

    elif object_type == 11:
        for i in range(0, len(entries) - 2):
            if i % 2 == 0:
                tri = (entries[i], entries[i + 1], entries[i + 2])
            else:
                tri = (entries[i + 1], entries[i], entries[i + 2])
            vi = (tri[0]["v"], tri[1]["v"], tri[2]["v"])
            ti = (tri[0]["t"], tri[1]["t"], tri[2]["t"])
            if None not in vi and len({vi[0], vi[1], vi[2]}) == 3:
                faces.append((int(vi[0]), int(vi[1]), int(vi[2])))
                uv_faces.append(ti)

    elif object_type == 12 and len(entries) >= 3:
        anchor = entries[0]
        for i in range(1, len(entries) - 1):
            tri = (anchor, entries[i], entries[i + 1])
            vi = (tri[0]["v"], tri[1]["v"], tri[2]["v"])
            ti = (tri[0]["t"], tri[1]["t"], tri[2]["t"])
            if None not in vi and len({vi[0], vi[1], vi[2]}) == 3:
                faces.append((int(vi[0]), int(vi[1]), int(vi[2])))
                uv_faces.append(ti)

    return faces, uv_faces


def validate_and_load_btg(path: str) -> Tuple[BTGMesh, ValidationStats]:
    stats = ValidationStats()
    raw = _read_file_bytes(path, stats)
    mesh = BTGMesh()

    if len(raw) < 10:
        stats.add_error("File too short for BTG header")
        raise BTGFormatError("BTG file too short")

    version, magic = struct.unpack_from("<HH", raw, 0)
    stats.version = version
    if version not in (7, 10):
        stats.add_warning(f"Unexpected version {version}; parser will continue")
    if magic != BTG_MAGIC:
        stats.add_error(f"Invalid magic 0x{magic:04X}; expected 0x{BTG_MAGIC:04X}")
        raise BTGFormatError("Invalid BTG magic number")

    header_size, count_size = _header_and_count_sizes(version)
    if len(raw) < header_size:
        stats.add_error("Header truncated")
        raise BTGFormatError("Incomplete BTG header")

    if count_size == 4:
        num_objects = struct.unpack_from("<I", raw, 8)[0]
    else:
        num_objects = struct.unpack_from("<H", raw, 8)[0]
    stats.object_count_header = num_objects

    offset = header_size
    vertices: List[Tuple[float, float, float]] = []
    texcoords: List[Tuple[float, float]] = []
    faces: List[Tuple[int, int, int]] = []
    face_texcoords: List[Tuple[Optional[int], Optional[int], Optional[int]]] = []
    face_materials: List[str] = []

    for obj_index in range(num_objects):
        object_header_size = 9 if version >= 10 else 5
        if offset + object_header_size > len(raw):
            stats.add_error(f"Object {obj_index}: object header truncated")
            break

        object_type = struct.unpack_from("<B", raw, offset)[0]
        offset += 1
        if version >= 10:
            num_props, num_elems = struct.unpack_from("<II", raw, offset)
            offset += 8
        else:
            num_props, num_elems = struct.unpack_from("<HH", raw, offset)
            offset += 4

        stats.object_count_parsed += 1
        stats.object_type_counts[object_type] = stats.object_type_counts.get(object_type, 0) + 1
        if object_type not in SUPPORTED_OBJECT_TYPES:
            stats.add_warning(f"Object {obj_index}: unsupported type {object_type}")

        index_types: Optional[int] = None
        material = ""
        entries_per_element: List[List[Dict[str, Optional[int]]]] = []

        for prop_idx in range(num_props):
            if offset + 5 > len(raw):
                stats.add_error(f"Object {obj_index}: property header {prop_idx} truncated")
                break
            prop_type, prop_size = struct.unpack_from("<BI", raw, offset)
            offset += 5
            if offset + prop_size > len(raw):
                stats.add_error(f"Object {obj_index}: property {prop_idx} data truncated")
                break
            prop_raw = raw[offset: offset + prop_size]
            offset += prop_size

            if prop_type == 0:
                material = prop_raw.decode("utf-8", errors="replace")
            elif prop_type == 1 and prop_size >= 1:
                index_types = prop_raw[0]

        for elem_idx in range(num_elems):
            if offset + 4 > len(raw):
                stats.add_error(f"Object {obj_index}: element header {elem_idx} truncated")
                break

            elem_size = struct.unpack_from("<I", raw, offset)[0]
            offset += 4
            if offset + elem_size > len(raw):
                stats.add_error(f"Object {obj_index}: element {elem_idx} data truncated")
                break

            elem_raw = raw[offset: offset + elem_size]
            offset += elem_size

            if object_type == 0:
                if elem_size < 28:
                    stats.add_warning(f"Object {obj_index}: bounding sphere element too short ({elem_size})")
                else:
                    cx, cy, cz, radius = struct.unpack_from("<dddf", elem_raw, 0)
                    mesh.center_ecef = (cx, cy, cz)
                    mesh.radius = float(radius)
            elif object_type == 1:
                if elem_size % 12 != 0:
                    stats.add_warning(f"Object {obj_index}: vertex element size {elem_size} not divisible by 12")
                vert_count = elem_size // 12
                for i in range(vert_count):
                    vx, vy, vz = struct.unpack_from("<fff", elem_raw, i * 12)
                    vertices.append((float(vx), float(vy), float(vz)))
            elif object_type == 3:
                if elem_size % 8 != 0:
                    stats.add_warning(f"Object {obj_index}: texcoord element size {elem_size} not divisible by 8")
                uv_count = elem_size // 8
                for i in range(uv_count):
                    u, v = struct.unpack_from("<ff", elem_raw, i * 8)
                    texcoords.append((float(u), float(v)))
            elif object_type in (9, 10, 11, 12):
                effective_index_types = index_types
                if effective_index_types is None:
                    effective_index_types = 0x01 if object_type == 9 else 0x09
                index_size = _geometry_index_size(version)
                tuple_width = 0
                for bit in range(4):
                    if effective_index_types & (1 << bit):
                        tuple_width += index_size
                if tuple_width <= 0:
                    stats.add_warning(f"Object {obj_index}: index types produce empty tuples")
                elif elem_size % tuple_width != 0:
                    stats.add_warning(
                        f"Object {obj_index}: geometry element size {elem_size} not divisible by tuple width {tuple_width}"
                    )

                entries = _parse_geometry_entries(elem_raw, index_types, object_type, version)
                entries_per_element.append(entries)

        if object_type in (10, 11, 12):
            before_count = len(faces)
            if object_type == 10:
                flat: List[Dict[str, Optional[int]]] = []
                for entries in entries_per_element:
                    flat.extend(entries)
                new_faces, new_uv_faces = _entries_to_faces(flat, object_type)
                faces.extend(new_faces)
                face_texcoords.extend(new_uv_faces)
            else:
                for entries in entries_per_element:
                    new_faces, new_uv_faces = _entries_to_faces(entries, object_type)
                    faces.extend(new_faces)
                    face_texcoords.extend(new_uv_faces)
            new_count = len(faces) - before_count
            if new_count > 0:
                face_materials.extend([material] * new_count)

    if offset < len(raw):
        stats.add_warning(f"Trailing unread bytes: {len(raw) - offset}")

    max_v = len(vertices) - 1
    valid_faces: List[Tuple[int, int, int]] = []
    valid_uv_faces: List[Tuple[Optional[int], Optional[int], Optional[int]]] = []
    valid_materials: List[str] = []
    invalid_face_count = 0
    max_t = len(texcoords) - 1
    for a, b, c in faces:
        i = len(valid_faces) + invalid_face_count
        if a > max_v or b > max_v or c > max_v:
            invalid_face_count += 1
            continue
        valid_faces.append((a, b, c))
        if i < len(face_texcoords):
            ta, tb, tc = face_texcoords[i]
            if (
                ta is not None
                and tb is not None
                and tc is not None
                and 0 <= ta <= max_t
                and 0 <= tb <= max_t
                and 0 <= tc <= max_t
            ):
                valid_uv_faces.append((ta, tb, tc))
            else:
                valid_uv_faces.append((None, None, None))
        else:
            valid_uv_faces.append((None, None, None))

        if i < len(face_materials):
            valid_materials.append(face_materials[i])
        else:
            valid_materials.append("")

    if invalid_face_count:
        stats.add_warning(f"Dropped {invalid_face_count} faces with out-of-range vertex indices")

    stats.vertex_count = len(vertices)
    stats.texcoord_count = len(texcoords)
    stats.triangle_count = len(valid_faces)

    if stats.errors:
        raise BTGFormatError("BTG validation failed")

    if not vertices:
        stats.add_warning("No vertices found")
    if not valid_faces:
        stats.add_warning("No triangle geometry found")

    if mesh.center_ecef == (0.0, 0.0, 0.0):
        transformed_vertices = vertices
    else:
        rot = ecef_to_enu_matrix(*mesh.center_ecef)
        max_abs = 0.0
        for vx, vy, vz in vertices:
            max_abs = max(max_abs, abs(vx), abs(vy), abs(vz))

        if max_abs > 1.0e5:
            transformed_vertices = [
                rotate3((vx - mesh.center_ecef[0], vy - mesh.center_ecef[1], vz - mesh.center_ecef[2]), rot)
                for (vx, vy, vz) in vertices
            ]
        else:
            transformed_vertices = [rotate3(v, rot) for v in vertices]

    mesh.vertices = transformed_vertices
    mesh.faces = valid_faces
    mesh.texcoords = texcoords
    mesh.face_texcoords = valid_uv_faces
    mesh.face_materials = valid_materials
    return mesh, stats


def v_sub(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_dot(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def v_len(a: Tuple[float, float, float]) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def v_norm(a: Tuple[float, float, float]) -> Tuple[float, float, float]:
    length = v_len(a)
    if length <= 1e-12:
        return (0.0, 0.0, 1.0)
    return (a[0] / length, a[1] / length, a[2] / length)


def material_color_from_face(face_index: int) -> Tuple[int, int, int]:
    seed = (face_index * 1103515245 + 12345) & 0x7FFFFFFF
    r = 80 + int(((seed & 0xFF) / 255.0) * 120)
    g = 80 + int((((seed >> 8) & 0xFF) / 255.0) * 120)
    b = 80 + int((((seed >> 16) & 0xFF) / 255.0) * 120)
    return (r, g, b)



AC_MODEL_YAW_CORRECTION_DEG = -90
AC_MODEL_PITCH_CORRECTION_DEG = 0
AC_MODEL_ROLL_CORRECTION_DEG = 90


# Matches geo-tile directory names like e000n50, w073n45, etc.
_GEO_TILE_RE = re.compile(r'^[ew]\d+[ns]\d+$', re.IGNORECASE)


@dataclass(frozen=True)
class ObjectCatalogEntry:
    object_path: str
    absolute_path: str
    source_root: str
    category: str = ""


def _categorize_object(object_path: str) -> str:
    """Derive a display category from an object's virtual path.

    Categories mirror FlightGear's Models/ subdirectory names so the picker
    UI can group objects the same way the simulator does.
    """
    lower = object_path.lower()
    # BTG terrain tiles — kept behind a separate flag in the UI
    if lower.endswith(".btg") or lower.endswith(".btg.gz"):
        return "BTG Terrain"
    parts = object_path.replace("\\", "/").split("/")
    # Models/<Category>/... → use the category subdirectory name
    if parts[0].lower() == "models":
        if len(parts) >= 3:
            return parts[1]  # e.g. "Airport", "Buildings", "Transport" …
        return "Models"  # file sitting directly under Models/
    # Geo-indexed tile paths produced by TerraSync Objects trees
    if _GEO_TILE_RE.match(parts[0]):
        return "Scenery Tiles"
    return "Misc"


def _split_search_path(raw: str) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(os.pathsep) if part.strip()]


def discover_object_source_roots(
    flightgear_root: str,
    custom_scenery_paths: Optional[Sequence[str]] = None,
) -> List[str]:
    roots: List[str] = []

    def _add(path: str) -> None:
        if not path:
            return
        normalized = os.path.abspath(path)
        if not os.path.isdir(normalized):
            return
        if normalized not in roots:
            roots.append(normalized)

    data_root = resolve_fgdata_root(flightgear_root)
    if data_root:
        _add(data_root)
    if flightgear_root:
        _add(flightgear_root)

    for custom_path in custom_scenery_paths or []:
        _add(custom_path)

    for env_path in _split_search_path(os.environ.get("FG_SCENERY", "")):
        _add(env_path)

    terrasync_candidates = [
        os.environ.get("TERRASYNC_ROOT", ""),
        os.environ.get("TERRASYNC_PATH", ""),
        os.path.join(os.path.expanduser("~"), "TerraSync"),
        os.path.join(os.path.expanduser("~"), "terrasync"),
    ]
    if data_root:
        terrasync_candidates.append(os.path.join(data_root, "TerraSync"))
    if flightgear_root:
        terrasync_candidates.append(os.path.join(os.path.abspath(flightgear_root), "TerraSync"))
    for candidate in terrasync_candidates:
        _add(candidate)

    return roots


def _discover_object_dirs(root: str) -> List[Tuple[str, str]]:
    # Tuple format: (base_dir, virtual_prefix). virtual_prefix is included in
    # object_path entries for roots that use a Models directory directly.
    dirs: List[Tuple[str, str]] = []
    root_abs = os.path.abspath(root)
    base_name = os.path.basename(root_abs).lower()

    objects_dir = os.path.join(root_abs, "Objects")
    models_dir = os.path.join(root_abs, "Models")
    if os.path.isdir(objects_dir):
        dirs.append((objects_dir, ""))
    if os.path.isdir(models_dir):
        dirs.append((models_dir, "Models"))

    if base_name == "objects":
        dirs.append((root_abs, ""))
    elif base_name == "models":
        dirs.append((root_abs, "Models"))

    unique: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for item in dirs:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def build_object_catalog(
    flightgear_root: str,
    custom_scenery_paths: Optional[Sequence[str]] = None,
    include_btg: bool = False,
) -> List[ObjectCatalogEntry]:
    """Walk all model/object source roots and collect placeable model files.

    Args:
        flightgear_root: Path to the FlightGear data root.
        custom_scenery_paths: Additional scenery roots to scan.
        include_btg: When False (default) BTG terrain tiles are excluded from
            the catalog.  Pass True only when you explicitly want to browse
            raw terrain tiles.
    """
    entries: List[ObjectCatalogEntry] = []
    seen_paths: Set[Tuple[str, str]] = set()
    extensions = (".ac", ".xml", ".btg", ".btg.gz")

    for source_root in discover_object_source_roots(flightgear_root, custom_scenery_paths):
        for base_dir, virtual_prefix in _discover_object_dirs(source_root):
            for dirpath, _dirnames, filenames in os.walk(base_dir):
                for filename in filenames:
                    lower = filename.lower()
                    if not lower.endswith(extensions):
                        continue
                    full_path = os.path.join(dirpath, filename)
                    rel = os.path.relpath(full_path, base_dir).replace("\\", "/")
                    object_path = f"{virtual_prefix}/{rel}" if virtual_prefix else rel
                    category = _categorize_object(object_path)
                    if category == "BTG Terrain" and not include_btg:
                        continue
                    dedupe_key = (source_root, object_path)
                    if dedupe_key in seen_paths:
                        continue
                    seen_paths.add(dedupe_key)
                    entries.append(
                        ObjectCatalogEntry(
                            object_path=object_path,
                            absolute_path=full_path,
                            source_root=source_root,
                            category=category,
                        )
                    )

    entries.sort(key=lambda item: (item.category.lower(), item.object_path.lower()))
    return entries


def build_object_catalog_by_category(
    flightgear_root: str,
    custom_scenery_paths: Optional[Sequence[str]] = None,
    include_btg: bool = False,
) -> Dict[str, List[ObjectCatalogEntry]]:
    """Return the object catalog grouped by category.

    Keys are category names (e.g. ``'Airport'``, ``'Buildings'``) sorted
    alphabetically.  Values are the matching entries, already sorted by
    ``object_path``.
    """
    flat = build_object_catalog(flightgear_root, custom_scenery_paths, include_btg=include_btg)
    grouped: Dict[str, List[ObjectCatalogEntry]] = {}
    for entry in flat:
        grouped.setdefault(entry.category, []).append(entry)
    return dict(sorted(grouped.items()))

def _resolve_model_resource_path(resource_path: str, base_file: str, fg_root: str) -> str:
    if not resource_path:
        return ""

    normalized = resource_path.strip().replace("\\", "/")
    if os.path.isabs(normalized) and os.path.isfile(normalized):
        return normalized

    base_dir = os.path.dirname(os.path.abspath(base_file))
    candidates = [
        os.path.join(base_dir, normalized),
    ]

    data_root = resolve_fgdata_root(fg_root)
    if data_root:
        candidates.extend(
            [
                os.path.join(data_root, normalized),
                os.path.join(data_root, "Objects", normalized),
                os.path.join(data_root, "Models", normalized),
            ]
        )

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return ""


def _resolve_model_path_from_xml(xml_path: str, fg_root: str, max_depth: int = 4) -> str:
    visited: Set[str] = set()

    def _resolve(path: str, depth: int) -> str:
        normalized = os.path.abspath(path)
        if depth <= 0 or normalized in visited:
            return ""
        visited.add(normalized)

        try:
            root = ET.parse(normalized).getroot()
        except Exception:
            return ""

        for node in root.iter("path"):
            if node.text is None:
                continue
            target = node.text.strip()
            if not target:
                continue
            resolved = _resolve_model_resource_path(target, normalized, fg_root)
            if not resolved:
                continue
            ext = os.path.splitext(resolved)[1].lower()
            if ext == ".xml":
                nested = _resolve(resolved, depth - 1)
                if nested:
                    return nested
            elif ext in {".ac", ".btg", ".gz"}:
                return resolved

        return ""

    return _resolve(xml_path, max_depth)


def apply_hpr_enu(v: Tuple[float, float, float], heading_deg: float, pitch_deg: float, roll_deg: float) -> Tuple[float, float, float]:
    ex, ny, up = v
    x = -ny
    y = ex
    z = up

    h = math.radians(heading_deg)
    ch, sh = math.cos(h), math.sin(h)
    x1 = x * ch - y * sh
    y1 = x * sh + y * ch
    z1 = z

    p = math.radians(pitch_deg)
    cp, sp = math.cos(p), math.sin(p)
    x2 = x1 * cp + z1 * sp
    y2 = y1
    z2 = -x1 * sp + z1 * cp

    r = math.radians(roll_deg)
    cr, sr = math.cos(r), math.sin(r)
    x3 = x2
    y3 = y2 * cr - z2 * sr
    z3 = y2 * sr + z2 * cr

    return (y3, -x3, z3)


def stg_associated_path(btg_path: str) -> str:
    base, _ext = os.path.splitext(btg_path)
    return base + ".stg"


def parse_stg_file(stg_path: str) -> List[STGObjectEntry]:
    entries: List[STGObjectEntry] = []
    try:
        with open(stg_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except Exception:
        return entries

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        try:
            parts = shlex.split(line)
        except Exception:
            parts = line.split()
        if len(parts) < 2:
            continue

        directive = parts[0].upper()
        object_path = parts[1]
        if not directive.startswith("OBJECT"):
            continue

        lon = lat = elev = None
        heading = pitch = roll = 0.0
        if len(parts) >= 5:
            try:
                lon = float(parts[2])
                lat = float(parts[3])
                elev = float(parts[4])
            except Exception:
                lon = lat = elev = None
        if len(parts) >= 6:
            try:
                heading = float(parts[5])
            except Exception:
                heading = 0.0
        if len(parts) >= 7:
            try:
                pitch = float(parts[6])
            except Exception:
                pitch = 0.0
        if len(parts) >= 8:
            try:
                roll = float(parts[7])
            except Exception:
                roll = 0.0

        entries.append(
            STGObjectEntry(
                directive=directive,
                object_path=object_path,
                lon_deg=lon,
                lat_deg=lat,
                elev_m=elev,
                heading_deg=heading,
                pitch_deg=pitch,
                roll_deg=roll,
            )
        )

    return entries


def resolve_stg_object_path(object_path: str, stg_path: str, fg_root: str) -> str:
    def existing_path_with_btg_fallback(path: str) -> str:
        if os.path.isfile(path):
            return path

        lower = path.lower()
        alt = ""
        if lower.endswith(".btg"):
            alt = path + ".gz"
        elif lower.endswith(".btg.gz"):
            alt = path[:-3]

        if alt and os.path.isfile(alt):
            return alt
        return ""

    normalized_object_path = object_path.strip().strip('"').replace("\\", "/")

    if os.path.isabs(normalized_object_path) and os.path.isfile(normalized_object_path):
        return normalized_object_path

    if os.path.isabs(normalized_object_path):
        resolved = existing_path_with_btg_fallback(normalized_object_path)
        if resolved:
            return resolved

    def _candidate_scenery_roots_from_stg(path: str) -> List[str]:
        roots: List[str] = []

        def _add(candidate: str) -> None:
            if candidate and os.path.isdir(candidate) and candidate not in roots:
                roots.append(candidate)

        stg_abs = os.path.abspath(path)
        stg_dir_local = os.path.dirname(stg_abs)
        _add(stg_dir_local)

        current = stg_dir_local
        for _ in range(10):
            parent = os.path.dirname(current)
            if not parent or parent == current:
                break
            base = os.path.basename(current).lower()
            if base in {"terrain", "objects", "models"}:
                _add(parent)
            current = parent

        return roots

    stg_dir = os.path.dirname(os.path.abspath(stg_path))
    candidates: List[str] = [
        os.path.join(stg_dir, normalized_object_path),
    ]

    for scenery_root in _candidate_scenery_roots_from_stg(stg_path):
        candidates.extend(
            [
                os.path.join(scenery_root, normalized_object_path),
                os.path.join(scenery_root, "Objects", normalized_object_path),
                os.path.join(scenery_root, "Models", normalized_object_path),
            ]
        )

    data_root = resolve_fgdata_root(fg_root)
    if data_root:
        candidates.extend(
            [
                os.path.join(data_root, normalized_object_path),
                os.path.join(data_root, "Objects", normalized_object_path),
                os.path.join(data_root, "Models", normalized_object_path),
            ]
        )

    if fg_root:
        fg_root_abs = os.path.abspath(fg_root)
        candidates.extend(
            [
                os.path.join(fg_root_abs, normalized_object_path),
                os.path.join(fg_root_abs, "Objects", normalized_object_path),
                os.path.join(fg_root_abs, "Models", normalized_object_path),
            ]
        )

    deduped_candidates: List[str] = []
    seen: Set[str] = set()
    for candidate in candidates:
        normalized = os.path.normpath(candidate)
        if normalized not in seen:
            seen.add(normalized)
            deduped_candidates.append(normalized)

    for candidate in deduped_candidates:
        resolved = existing_path_with_btg_fallback(candidate)
        if resolved:
            return resolved
    return ""


def transform_mesh_to_base_enu(
    mesh: BTGMesh,
    base_center_ecef: Tuple[float, float, float],
    assume_local_enu: bool = False,
) -> BTGMesh:
    dst_rot = ecef_to_enu_matrix(*base_center_ecef)

    if not mesh.vertices:
        return mesh

    max_abs = 0.0
    for vx, vy, vz in mesh.vertices:
        max_abs = max(max_abs, abs(vx), abs(vy), abs(vz))

    if max_abs > 1.0e5 and not assume_local_enu:
        transformed_vertices: List[Tuple[float, float, float]] = []
        for vx, vy, vz in mesh.vertices:
            rel_to_base = (
                vx - base_center_ecef[0],
                vy - base_center_ecef[1],
                vz - base_center_ecef[2],
            )
            transformed_vertices.append(rotate3(rel_to_base, dst_rot))

        return BTGMesh(
            vertices=transformed_vertices,
            faces=list(mesh.faces),
            texcoords=list(mesh.texcoords),
            face_texcoords=list(mesh.face_texcoords),
            face_materials=list(mesh.face_materials),
            face_colors=list(mesh.face_colors),
            center_ecef=base_center_ecef,
            radius=mesh.radius,
        )

    if mesh.center_ecef == (0.0, 0.0, 0.0):
        return mesh

    working_vertices = list(mesh.vertices)
    if assume_local_enu and working_vertices:
        zs = [v[2] for v in working_vertices]
        z_span = max(zs) - min(zs)
        z_median = statistics.median(zs)
        # Some BTGs carry a very large constant local-Z bias after parse;
        # strip it before ENU frame conversion to avoid large lateral drift.
        if abs(z_median) > 1.0e6 and z_span < 1.0e4:
            working_vertices = [(vx, vy, vz - z_median) for vx, vy, vz in working_vertices]

    src_rot = ecef_to_enu_matrix(*mesh.center_ecef)

    transformed_vertices: List[Tuple[float, float, float]] = []
    for vx, vy, vz in working_vertices:
        ecef_offset = rotate3_inv((vx, vy, vz), src_rot)
        ecef_abs = (
            mesh.center_ecef[0] + ecef_offset[0],
            mesh.center_ecef[1] + ecef_offset[1],
            mesh.center_ecef[2] + ecef_offset[2],
        )
        rel_to_base = (
            ecef_abs[0] - base_center_ecef[0],
            ecef_abs[1] - base_center_ecef[1],
            ecef_abs[2] - base_center_ecef[2],
        )
        transformed_vertices.append(rotate3(rel_to_base, dst_rot))

    return BTGMesh(
        vertices=transformed_vertices,
        faces=list(mesh.faces),
        texcoords=list(mesh.texcoords),
        face_texcoords=list(mesh.face_texcoords),
        face_materials=list(mesh.face_materials),
        face_colors=list(mesh.face_colors),
        center_ecef=base_center_ecef,
        radius=mesh.radius,
    )


def merge_meshes(base_mesh: BTGMesh, extra_meshes: Sequence[BTGMesh]) -> BTGMesh:
    def _aligned_face_colors(mesh: BTGMesh) -> List[Tuple[int, int, int]]:
        colors = list(mesh.face_colors)
        face_count = len(mesh.faces)
        if len(colors) < face_count:
            for fi in range(len(colors), face_count):
                colors.append(material_color_from_face(fi))
        elif len(colors) > face_count:
            colors = colors[:face_count]
        return colors

    merged = BTGMesh(
        vertices=list(base_mesh.vertices),
        faces=list(base_mesh.faces),
        texcoords=list(base_mesh.texcoords),
        face_texcoords=list(base_mesh.face_texcoords),
        face_materials=list(base_mesh.face_materials),
        face_colors=_aligned_face_colors(base_mesh),
        center_ecef=base_mesh.center_ecef,
        radius=base_mesh.radius,
    )

    for mesh in extra_meshes:
        vertex_offset = len(merged.vertices)
        tex_offset = len(merged.texcoords)

        merged.vertices.extend(mesh.vertices)
        merged.texcoords.extend(mesh.texcoords)

        for a, b, c in mesh.faces:
            merged.faces.append((a + vertex_offset, b + vertex_offset, c + vertex_offset))

        for ta, tb, tc in mesh.face_texcoords:
            mapped = (
                (ta + tex_offset) if ta is not None else None,
                (tb + tex_offset) if tb is not None else None,
                (tc + tex_offset) if tc is not None else None,
            )
            merged.face_texcoords.append(mapped)

        merged.face_materials.extend(mesh.face_materials)
        merged.face_colors.extend(_aligned_face_colors(mesh))

    return merged


def _make_proxy_cube(
    center_enu: Tuple[float, float, float],
    size: float,
    material_name: str,
    base_center_ecef: Tuple[float, float, float],
) -> BTGMesh:
    cx, cy, cz = center_enu
    h = max(0.3, size * 0.5)
    vertices = [
        (cx - h, cy - h, cz),
        (cx + h, cy - h, cz),
        (cx + h, cy + h, cz),
        (cx - h, cy + h, cz),
        (cx - h, cy - h, cz + size),
        (cx + h, cy - h, cz + size),
        (cx + h, cy + h, cz + size),
        (cx - h, cy + h, cz + size),
    ]
    faces = [
        (0, 1, 2), (0, 2, 3),
        (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1),
        (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3),
        (3, 7, 4), (3, 4, 0),
    ]
    return BTGMesh(
        vertices=vertices,
        faces=faces,
        texcoords=[],
        face_texcoords=[(None, None, None)] * len(faces),
        face_materials=[material_name] * len(faces),
        face_colors=[],
        center_ecef=base_center_ecef,
        radius=size,
    )


def _load_ac3d_mesh(ac_path: str, fg_root: str) -> Optional[BTGMesh]:
    try:
        with open(ac_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = [line.rstrip("\n") for line in handle]
    except Exception:
        return None

    def _next_nonempty(start: int) -> int:
        i = start
        while i < len(lines) and not lines[i].strip():
            i += 1
        return i

    header_idx = _next_nonempty(0)
    if header_idx >= len(lines):
        return None

    header = lines[header_idx].strip()
    if not header.startswith("AC3D"):
        return None

    def _tokenize(raw: str) -> List[str]:
        stripped = raw.strip()
        if not stripped:
            return []
        try:
            return shlex.split(stripped)
        except Exception:
            return stripped.split()

    def _to_float(value: str, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _to_int(value: str, default: int = 0) -> int:
        try:
            return int(value, 0)
        except Exception:
            try:
                return int(float(value))
            except Exception:
                return default

    def _rgb_color(rgb: Sequence[float]) -> Tuple[int, int, int]:
        return (
            max(0, min(255, int(round(rgb[0] * 255.0)))),
            max(0, min(255, int(round(rgb[1] * 255.0)))),
            max(0, min(255, int(round(rgb[2] * 255.0)))),
        )

    def _parse_material_rgb(tokens: Sequence[str]) -> Optional[Tuple[int, int, int]]:
        for idx, token in enumerate(tokens):
            if token == "rgb" and idx + 3 < len(tokens):
                rgb = [_to_float(tokens[idx + 1]), _to_float(tokens[idx + 2]), _to_float(tokens[idx + 3])]
                return _rgb_color(rgb)
        return None

    def _mat_vec_mul(
        mat: Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]],
        vec: Tuple[float, float, float],
    ) -> Tuple[float, float, float]:
        return (
            mat[0][0] * vec[0] + mat[0][1] * vec[1] + mat[0][2] * vec[2],
            mat[1][0] * vec[0] + mat[1][1] * vec[1] + mat[1][2] * vec[2],
            mat[2][0] * vec[0] + mat[2][1] * vec[1] + mat[2][2] * vec[2],
        )

    def _mat_mul(
        a: Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]],
        b: Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]],
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
        return (
            (
                a[0][0] * b[0][0] + a[0][1] * b[1][0] + a[0][2] * b[2][0],
                a[0][0] * b[0][1] + a[0][1] * b[1][1] + a[0][2] * b[2][1],
                a[0][0] * b[0][2] + a[0][1] * b[1][2] + a[0][2] * b[2][2],
            ),
            (
                a[1][0] * b[0][0] + a[1][1] * b[1][0] + a[1][2] * b[2][0],
                a[1][0] * b[0][1] + a[1][1] * b[1][1] + a[1][2] * b[2][1],
                a[1][0] * b[0][2] + a[1][1] * b[1][2] + a[1][2] * b[2][2],
            ),
            (
                a[2][0] * b[0][0] + a[2][1] * b[1][0] + a[2][2] * b[2][0],
                a[2][0] * b[0][1] + a[2][1] * b[1][1] + a[2][2] * b[2][1],
                a[2][0] * b[0][2] + a[2][1] * b[1][2] + a[2][2] * b[2][2],
            ),
        )

    class _AcSurface:
        def __init__(self) -> None:
            self.surface_type = 0
            self.two_sided = False
            self.mat_idx: Optional[int] = None
            self.refs: List[Tuple[int, float, float]] = []

    class _AcObject:
        def __init__(self, obj_type: str) -> None:
            self.obj_type = obj_type
            self.loc = (0.0, 0.0, 0.0)
            self.rot: Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]] = (
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, 0.0, 1.0),
            )
            self.texture = ""
            self.texrep = (1.0, 1.0)
            self.texoff = (0.0, 0.0)
            self.vertices: List[Tuple[float, float, float]] = []
            self.surfaces: List[_AcSurface] = []
            self.children: List["_AcObject"] = []

    def _parse_data_block(start: int, token_count: int) -> int:
        i = start
        consumed = 0
        while i < len(lines) and consumed < token_count:
            consumed += len(lines[i]) + 1
            i += 1
        return i

    def _parse_surface(start: int) -> Tuple[Optional[_AcSurface], int]:
        i = _next_nonempty(start)
        if i >= len(lines):
            return None, i

        surf = _AcSurface()
        tokens = _tokenize(lines[i])
        if tokens and tokens[0] == "SURF":
            if len(tokens) >= 2:
                surf_flags = _to_int(tokens[1])
                surf.surface_type = surf_flags & 0xF
                surf.two_sided = bool(((surf_flags >> 4) & 0x2) != 0)
            i += 1

        while i < len(lines):
            i = _next_nonempty(i)
            if i >= len(lines):
                return surf, i

            tokens = _tokenize(lines[i])
            if not tokens:
                i += 1
                continue

            key = tokens[0]
            if key == "mat":
                if len(tokens) >= 2:
                    surf.mat_idx = _to_int(tokens[1], default=-1)
                    if surf.mat_idx < 0:
                        surf.mat_idx = None
                i += 1
                continue

            if key == "refs":
                ref_count = _to_int(tokens[1]) if len(tokens) >= 2 else 0
                i += 1
                for _ in range(max(0, ref_count)):
                    if i >= len(lines):
                        break
                    ref_tokens = _tokenize(lines[i])
                    i += 1
                    if len(ref_tokens) < 3:
                        continue
                    vert_idx = _to_int(ref_tokens[0], default=-1)
                    u = _to_float(ref_tokens[1])
                    v = _to_float(ref_tokens[2])
                    if vert_idx >= 0:
                        surf.refs.append((vert_idx, u, v))
                return surf, i

            if key in {"SURF", "kids", "OBJECT", "numsurf", "numvert", "MATERIAL", "MAT"}:
                return surf, i

            i += 1

        return surf, i

    def _parse_object(start: int) -> Tuple[Optional[_AcObject], int]:
        i = _next_nonempty(start)
        if i >= len(lines):
            return None, i

        header_tokens = _tokenize(lines[i])
        if len(header_tokens) < 2 or header_tokens[0] != "OBJECT":
            return None, i + 1

        obj = _AcObject(header_tokens[1])
        i += 1

        while i < len(lines):
            i = _next_nonempty(i)
            if i >= len(lines):
                return obj, i

            tokens = _tokenize(lines[i])
            if not tokens:
                i += 1
                continue

            key = tokens[0]

            if key in {"OBJECT", "MATERIAL", "MAT"}:
                return obj, i

            if key == "name" or key == "url" or key in {"hidden", "locked", "folded", "crease", "subdiv"}:
                i += 1
                continue

            if key == "data":
                char_count = _to_int(tokens[1]) if len(tokens) >= 2 else 0
                i = _parse_data_block(i + 1, max(0, char_count))
                continue

            if key == "texture":
                obj.texture = tokens[1] if len(tokens) >= 2 else ""
                i += 1
                continue

            if key == "texrep":
                if len(tokens) >= 3:
                    obj.texrep = (_to_float(tokens[1], 1.0), _to_float(tokens[2], 1.0))
                i += 1
                continue

            if key == "texoff":
                if len(tokens) >= 3:
                    obj.texoff = (_to_float(tokens[1], 0.0), _to_float(tokens[2], 0.0))
                i += 1
                continue

            if key == "loc":
                if len(tokens) >= 4:
                    obj.loc = (_to_float(tokens[1]), _to_float(tokens[2]), _to_float(tokens[3]))
                i += 1
                continue

            if key == "rot":
                if len(tokens) >= 10:
                    t = [_to_float(value) for value in tokens[1:10]]
                    # Blender importer transposes this tokenized matrix during import.
                    obj.rot = (
                        (t[0], t[3], t[6]),
                        (t[1], t[4], t[7]),
                        (t[2], t[5], t[8]),
                    )
                i += 1
                continue

            if key == "numvert":
                vert_count = _to_int(tokens[1]) if len(tokens) >= 2 else 0
                i += 1
                verts: List[Tuple[float, float, float]] = []
                for _ in range(max(0, vert_count)):
                    if i >= len(lines):
                        break
                    vert_tokens = _tokenize(lines[i])
                    i += 1
                    if len(vert_tokens) < 3:
                        continue
                    verts.append((_to_float(vert_tokens[0]), _to_float(vert_tokens[1]), _to_float(vert_tokens[2])))
                obj.vertices = verts
                continue

            if key == "numsurf":
                surf_count = _to_int(tokens[1]) if len(tokens) >= 2 else 0
                i += 1
                surfaces: List[_AcSurface] = []
                for _ in range(max(0, surf_count)):
                    prev_i = i
                    surf, i = _parse_surface(i)
                    if i <= prev_i:
                        i = prev_i + 1
                    if surf is not None and surf.refs:
                        surfaces.append(surf)
                obj.surfaces = surfaces
                continue

            if key == "kids":
                child_count = _to_int(tokens[1]) if len(tokens) >= 2 else 0
                i += 1
                children: List[_AcObject] = []
                for _ in range(max(0, child_count)):
                    i = _next_nonempty(i)
                    if i >= len(lines):
                        break
                    child_tokens = _tokenize(lines[i])
                    if not child_tokens or child_tokens[0] != "OBJECT":
                        break
                    child, i = _parse_object(i)
                    if child is not None:
                        children.append(child)
                obj.children = children
                return obj, i

            i += 1

        return obj, i

    # AC3D materials are global and referenced by zero-based "mat <idx>" in each surface.
    ac_material_colors: Dict[int, Tuple[int, int, int]] = {}
    ac_material_names: Dict[int, str] = {}
    roots: List[_AcObject] = []

    i = header_idx + 1
    material_idx = -1
    while i < len(lines):
        i = _next_nonempty(i)
        if i >= len(lines):
            break

        tokens = _tokenize(lines[i])
        if not tokens:
            i += 1
            continue

        key = tokens[0]
        if key == "MATERIAL":
            material_idx += 1
            if len(tokens) >= 2:
                ac_material_names[material_idx] = tokens[1]
            rgb = _parse_material_rgb(tokens)
            if rgb is not None:
                ac_material_colors[material_idx] = rgb
            i += 1
            continue

        if key == "MAT":
            material_idx += 1
            if len(tokens) >= 2:
                ac_material_names[material_idx] = tokens[1]
            i += 1
            rgb: Optional[Tuple[int, int, int]] = None
            while i < len(lines):
                i = _next_nonempty(i)
                if i >= len(lines):
                    break
                mat_tokens = _tokenize(lines[i])
                if not mat_tokens:
                    i += 1
                    continue

                mat_key = mat_tokens[0]
                if mat_key == "rgb" and len(mat_tokens) >= 4:
                    rgb = _rgb_color(
                        [_to_float(mat_tokens[1]), _to_float(mat_tokens[2]), _to_float(mat_tokens[3])]
                    )
                    i += 1
                    continue

                if mat_key == "data":
                    char_count = _to_int(mat_tokens[1]) if len(mat_tokens) >= 2 else 0
                    i = _parse_data_block(i + 1, max(0, char_count))
                    continue

                i += 1
                if mat_key == "ENDMAT":
                    break

            if rgb is not None:
                ac_material_colors[material_idx] = rgb
            continue

        if key == "OBJECT":
            obj, i = _parse_object(i)
            if obj is not None:
                roots.append(obj)
            continue

        i += 1

    if not roots:
        return None

    vertices: List[Tuple[float, float, float]] = []
    texcoords: List[Tuple[float, float]] = []
    faces: List[Tuple[int, int, int]] = []
    face_texcoords: List[Tuple[Optional[int], Optional[int], Optional[int]]] = []
    face_materials: List[str] = []
    face_colors: List[Tuple[int, int, int]] = []

    def resolve_texture_for_object(texture_name: str) -> str:
        if not texture_name:
            return ""
        resolved = _resolve_model_resource_path(texture_name, ac_path, fg_root)
        return f"__file__:{resolved}" if resolved else ""

    def walk(
        obj: _AcObject,
        parent_pos: Tuple[float, float, float],
        parent_rot: Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]],
    ) -> None:
        local_pos = _mat_vec_mul(parent_rot, obj.loc)
        world_pos = (
            parent_pos[0] + local_pos[0],
            parent_pos[1] + local_pos[1],
            parent_pos[2] + local_pos[2],
        )
        world_rot = _mat_mul(parent_rot, obj.rot)

        base_idx = len(vertices)
        for vx, vy, vz in obj.vertices:
            rotated = _mat_vec_mul(world_rot, (vx, vy, vz))
            vertices.append(
                (
                    world_pos[0] + rotated[0],
                    world_pos[1] + rotated[1],
                    world_pos[2] + rotated[2],
                )
            )

        obj_tex = resolve_texture_for_object(obj.texture)
        texrep_u, texrep_v = obj.texrep
        texoff_u, texoff_v = obj.texoff
        vertex_count = len(obj.vertices)

        for surf in obj.surfaces:
            if surf.surface_type != 0 or len(surf.refs) < 3:
                continue

            for j in range(1, len(surf.refs) - 1):
                tri_defs = [
                    (surf.refs[0], surf.refs[j], surf.refs[j + 1]),
                ]
                if surf.two_sided:
                    tri_defs.append((surf.refs[0], surf.refs[j + 1], surf.refs[j]))

                for tri in tri_defs:
                    local_v_idx = (tri[0][0], tri[1][0], tri[2][0])
                    if any(v < 0 or v >= vertex_count for v in local_v_idx):
                        continue

                    # Faces that rely on AC material colors (no object texture)
                    # need per-face vertices to preserve hard color boundaries
                    # in the GL vertex-color path.
                    is_ac_material_only_face = not obj_tex
                    if is_ac_material_only_face:
                        src_indices = (
                            base_idx + local_v_idx[0],
                            base_idx + local_v_idx[1],
                            base_idx + local_v_idx[2],
                        )
                        dup_indices: List[int] = []
                        for src_idx in src_indices:
                            vertices.append(vertices[src_idx])
                            dup_indices.append(len(vertices) - 1)
                        v_idx = (dup_indices[0], dup_indices[1], dup_indices[2])
                    else:
                        v_idx = (
                            base_idx + local_v_idx[0],
                            base_idx + local_v_idx[1],
                            base_idx + local_v_idx[2],
                        )
                    if len({v_idx[0], v_idx[1], v_idx[2]}) < 3:
                        continue
                    faces.append(v_idx)

                    t_idx: List[int] = []
                    for _vert_idx, u, v in tri:
                        texcoords.append((u * texrep_u + texoff_u, v * texrep_v + texoff_v))
                        t_idx.append(len(texcoords) - 1)
                    face_texcoords.append((t_idx[0], t_idx[1], t_idx[2]))
                    if obj_tex:
                        material_token = obj_tex
                    elif surf.mat_idx is not None:
                        material_name = ac_material_names.get(surf.mat_idx, "")
                        normalized_name = _normalize_key(material_name)
                        if normalized_name:
                            material_token = f"__acmat__:{normalized_name}"
                        else:
                            material_token = f"__acmat__:{surf.mat_idx}"
                    else:
                        material_token = "__acmat__:default"
                    face_materials.append(material_token)

                    if surf.mat_idx is not None and surf.mat_idx in ac_material_colors:
                        face_colors.append(ac_material_colors[surf.mat_idx])
                    else:
                        face_colors.append((176, 176, 176))

        for child in obj.children:
            walk(child, world_pos, world_rot)

    identity_matrix = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    for root in roots:
        walk(root, (0.0, 0.0, 0.0), identity_matrix)

    if not faces:
        return None

    return BTGMesh(
        vertices=vertices,
        faces=faces,
        texcoords=texcoords,
        face_texcoords=face_texcoords,
        face_materials=face_materials,
        face_colors=face_colors,
        center_ecef=(0.0, 0.0, 0.0),
        radius=0.0,
    )


def _instance_local_mesh(
    mesh: BTGMesh,
    origin_enu: Tuple[float, float, float],
    heading_deg: float,
    pitch_deg: float,
    roll_deg: float,
    base_center_ecef: Tuple[float, float, float],
    pre_heading_deg: float = 0.0,
    pre_pitch_deg: float = 0.0,
    pre_roll_deg: float = 0.0,
    offset_enu: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> BTGMesh:
    inst_vertices: List[Tuple[float, float, float]] = []
    ox, oy, oz = offset_enu
    for v in mesh.vertices:
        # Apply fixed model-frame correction first, then STG instance HPR.
        corrected = apply_hpr_enu(v, pre_heading_deg, pre_pitch_deg, pre_roll_deg)
        rv = apply_hpr_enu(corrected, heading_deg, pitch_deg, roll_deg)
        inst_vertices.append((rv[0] + origin_enu[0] + ox, rv[1] + origin_enu[1] + oy, rv[2] + origin_enu[2] + oz))

    return BTGMesh(
        vertices=inst_vertices,
        faces=list(mesh.faces),
        texcoords=list(mesh.texcoords),
        face_texcoords=list(mesh.face_texcoords),
        face_materials=list(mesh.face_materials),
        face_colors=list(mesh.face_colors),
        center_ecef=base_center_ecef,
        radius=mesh.radius,
    )


def _is_reasonable_local_mesh(mesh: BTGMesh, max_abs_coord: float = 2.0e5) -> bool:
    if not mesh.vertices:
        return False
    for vx, vy, vz in mesh.vertices:
        if abs(vx) > max_abs_coord or abs(vy) > max_abs_coord or abs(vz) > max_abs_coord:
            return False
    return True


def _mesh_center(mesh: BTGMesh) -> Optional[Tuple[float, float, float]]:
    if not mesh.vertices:
        return None
    xs = [v[0] for v in mesh.vertices]
    ys = [v[1] for v in mesh.vertices]
    zs = [v[2] for v in mesh.vertices]
    return (
        0.5 * (min(xs) + max(xs)),
        0.5 * (min(ys) + max(ys)),
        0.5 * (min(zs) + max(zs)),
    )


def _translate_mesh(mesh: BTGMesh, delta: Tuple[float, float, float]) -> BTGMesh:
    if delta == (0.0, 0.0, 0.0):
        return mesh
    dx, dy, dz = delta
    moved_vertices = [(vx + dx, vy + dy, vz + dz) for vx, vy, vz in mesh.vertices]
    return BTGMesh(
        vertices=moved_vertices,
        faces=list(mesh.faces),
        texcoords=list(mesh.texcoords),
        face_texcoords=list(mesh.face_texcoords),
        face_materials=list(mesh.face_materials),
        face_colors=list(mesh.face_colors),
        center_ecef=mesh.center_ecef,
        radius=mesh.radius,
    )


def _reanchor_mesh_center(mesh: BTGMesh, target_center: Tuple[float, float, float]) -> BTGMesh:
    current_center = _mesh_center(mesh)
    if current_center is None:
        return mesh
    delta = (
        target_center[0] - current_center[0],
        target_center[1] - current_center[1],
        target_center[2] - current_center[2],
    )
    return _translate_mesh(mesh, delta)


def compose_scene_mesh(
    base_mesh: BTGMesh,
    static_meshes: Sequence[BTGMesh],
    model_instances: Sequence[STGModelInstance],
    yaw_offset_deg: float,
    pitch_offset_deg: float,
    roll_offset_deg: float,
) -> BTGMesh:
    instance_meshes: List[BTGMesh] = []
    vertex_cursor = len(base_mesh.vertices)
    face_cursor = len(base_mesh.faces)
    for instance in model_instances:
        yaw_correction = AC_MODEL_YAW_CORRECTION_DEG if instance.is_ac_model else 0.0
        pitch_correction = AC_MODEL_PITCH_CORRECTION_DEG if instance.is_ac_model else 0.0
        roll_correction = AC_MODEL_ROLL_CORRECTION_DEG if instance.is_ac_model else 0.0
        instance_mesh = _instance_local_mesh(
            instance.template_mesh,
            instance.origin_enu,
            instance.heading_deg + yaw_offset_deg + instance.offset_yaw_deg,
            instance.pitch_deg + pitch_offset_deg + instance.offset_pitch_deg,
            instance.roll_deg + roll_offset_deg + instance.offset_roll_deg,
            base_mesh.center_ecef,
            pre_heading_deg=yaw_correction,
            pre_pitch_deg=pitch_correction,
            pre_roll_deg=roll_correction,
            offset_enu=(instance.offset_x_m, instance.offset_y_m, instance.offset_z_m),
        )
        instance.mesh_vertex_start = vertex_cursor
        instance.mesh_vertex_count = len(instance_mesh.vertices)
        instance.mesh_face_start = face_cursor
        instance.mesh_face_count = len(instance_mesh.faces)
        vertex_cursor += len(instance_mesh.vertices)
        face_cursor += len(instance_mesh.faces)
        # Keep labels pinned to the exact instantiated model origin.
        instance.render_anchor_enu = (
            instance.origin_enu[0] + instance.offset_x_m,
            instance.origin_enu[1] + instance.offset_y_m,
            instance.origin_enu[2] + instance.offset_z_m,
        )
        instance_meshes.append(instance_mesh)
    return merge_meshes(base_mesh, list(static_meshes) + instance_meshes)


def build_model_instance_mesh(
    instance: STGModelInstance,
    base_center_ecef: Tuple[float, float, float],
    yaw_offset_deg: float,
    pitch_offset_deg: float,
    roll_offset_deg: float,
) -> BTGMesh:
    yaw_correction = AC_MODEL_YAW_CORRECTION_DEG if instance.is_ac_model else 0.0
    pitch_correction = AC_MODEL_PITCH_CORRECTION_DEG if instance.is_ac_model else 0.0
    roll_correction = AC_MODEL_ROLL_CORRECTION_DEG if instance.is_ac_model else 0.0
    return _instance_local_mesh(
        instance.template_mesh,
        instance.origin_enu,
        instance.heading_deg + yaw_offset_deg + instance.offset_yaw_deg,
        instance.pitch_deg + pitch_offset_deg + instance.offset_pitch_deg,
        instance.roll_deg + roll_offset_deg + instance.offset_roll_deg,
        base_center_ecef,
        pre_heading_deg=yaw_correction,
        pre_pitch_deg=pitch_correction,
        pre_roll_deg=roll_correction,
        offset_enu=(instance.offset_x_m, instance.offset_y_m, instance.offset_z_m),
    )


def load_catalog_object_template(
    catalog_entry: ObjectCatalogEntry,
    base_center_ecef: Tuple[float, float, float],
    flightgear_root: str,
    shared_model_mesh_cache: Dict[str, Optional[BTGMesh]],
) -> Tuple[Optional[BTGMesh], bool, str]:
    """Resolve and load a catalog object into a template mesh.

    Returns ``(mesh_template, is_ac_model, resolved_model_path)``.
    ``mesh_template`` is ``None`` when the object cannot be loaded.
    """

    def _maybe_refresh_cached_ac_mesh(model_path_local: str, mesh_cached: Optional[BTGMesh]) -> Optional[BTGMesh]:
        if mesh_cached is None or not mesh_cached.faces or not mesh_cached.face_materials:
            return mesh_cached

        # Heuristic for stale cache generated before improved AC texture propagation:
        # mixed '__file__:' + many 'ac_object' faces usually indicates old parse data.
        has_file_material = any(m.startswith("__file__:") for m in mesh_cached.face_materials)
        if not has_file_material:
            return mesh_cached

        old_total = max(1, len(mesh_cached.face_materials))
        old_ac_object = sum(1 for m in mesh_cached.face_materials if m == "ac_object")
        if old_ac_object == 0:
            return mesh_cached

        reloaded = _load_ac3d_mesh(model_path_local, flightgear_root)
        if reloaded is None or not reloaded.faces or not reloaded.face_materials:
            return mesh_cached

        new_total = max(1, len(reloaded.face_materials))
        new_ac_object = sum(1 for m in reloaded.face_materials if m == "ac_object")
        old_ratio = old_ac_object / float(old_total)
        new_ratio = new_ac_object / float(new_total)
        if new_ratio + 1e-6 < old_ratio:
            return reloaded
        return mesh_cached

    model_path = catalog_entry.absolute_path
    if not model_path:
        return None, False, ""

    model_ext = os.path.splitext(model_path.lower())[1]
    if model_ext == ".xml":
        resolved = _resolve_model_path_from_xml(model_path, flightgear_root)
        if not resolved:
            return None, False, model_path
        model_path = resolved
        model_ext = os.path.splitext(model_path.lower())[1]

    cached = shared_model_mesh_cache.get(model_path)
    if cached is None and model_path not in shared_model_mesh_cache:
        if model_ext == ".ac":
            cached = _load_ac3d_mesh(model_path, flightgear_root)
        elif model_ext in {".btg", ".gz"}:
            try:
                cached, _model_stats = validate_and_load_btg(model_path)
                cached = transform_mesh_to_base_enu(cached, base_center_ecef, assume_local_enu=True)
            except Exception:
                cached = None
        else:
            cached = None
        shared_model_mesh_cache[model_path] = cached
    else:
        cached = shared_model_mesh_cache.get(model_path)

    if model_ext == ".ac":
        refreshed = _maybe_refresh_cached_ac_mesh(model_path, cached)
        if refreshed is not cached:
            cached = refreshed
            shared_model_mesh_cache[model_path] = cached

    is_ac_model = model_ext == ".ac"
    if not cached or not cached.vertices or not cached.faces:
        return None, is_ac_model, model_path
    return cached, is_ac_model, model_path
def load_associated_stg_objects(
    btg_path: str,
    base_mesh: BTGMesh,
    flightgear_root: str,
    shared_model_mesh_cache: Dict[str, Optional[BTGMesh]],
) -> Tuple[List[BTGMesh], List[STGModelInstance], Dict[str, object]]:
    stg_path = stg_associated_path(btg_path)
    if not os.path.isfile(stg_path):
        return [], [], {
            "stg_found": False,
            "entries": 0,
            "btg_objects_loaded": 0,
            "model_objects_loaded": 0,
            "proxy_objects_loaded": 0,
            "skipped": 0,
        }

    entries = parse_stg_file(stg_path)
    return load_stg_objects_from_entries(
        stg_path,
        base_mesh,
        entries,
        btg_path,
        flightgear_root,
        shared_model_mesh_cache,
    )


def load_stg_objects_from_entries(
    stg_path: str,
    base_mesh: BTGMesh,
    entries: List[STGObjectEntry],
    base_btg_path: str,
    flightgear_root: str,
    shared_model_mesh_cache: Dict[str, Optional[BTGMesh]],
) -> Tuple[List[BTGMesh], List[STGModelInstance], Dict[str, object]]:
    debug: Dict[str, object] = {
        "stg_found": os.path.isfile(stg_path),
        "entries": len(entries),
        "btg_objects_loaded": 0,
        "model_objects_loaded": 0,
        "proxy_objects_loaded": 0,
        "skipped": 0,
    }
    if not entries:
        return [], [], debug

    static_meshes: List[BTGMesh] = []
    model_instances: List[STGModelInstance] = []
    base_abs = os.path.abspath(base_btg_path)
    base_rot = ecef_to_enu_matrix(*base_mesh.center_ecef) if base_mesh.center_ecef != (0.0, 0.0, 0.0) else None

    for entry_index, entry in enumerate(entries):
        object_full = resolve_stg_object_path(entry.object_path, stg_path, flightgear_root)
        ext = os.path.splitext(entry.object_path.lower())[1]

        if ext in {".btg", ".gz"} and object_full:
            if os.path.abspath(object_full) == base_abs:
                continue
            try:
                child_mesh, _child_stats = validate_and_load_btg(object_full)
            except Exception:
                debug["skipped"] = int(debug["skipped"]) + 1
                continue

            child_center_enu: Optional[Tuple[float, float, float]] = None
            if (
                base_rot is not None
                and base_mesh.center_ecef != (0.0, 0.0, 0.0)
                and child_mesh.center_ecef != (0.0, 0.0, 0.0)
            ):
                rel_child_center = (
                    child_mesh.center_ecef[0] - base_mesh.center_ecef[0],
                    child_mesh.center_ecef[1] - base_mesh.center_ecef[1],
                    child_mesh.center_ecef[2] - base_mesh.center_ecef[2],
                )
                child_center_enu = rotate3(rel_child_center, base_rot)

            child_mesh = transform_mesh_to_base_enu(child_mesh, base_mesh.center_ecef, assume_local_enu=True)

            target_center_enu: Optional[Tuple[float, float, float]] = None

            if (
                entry.lon_deg is not None
                and entry.lat_deg is not None
                and entry.elev_m is not None
                and base_rot is not None
                and base_mesh.center_ecef != (0.0, 0.0, 0.0)
            ):
                target_ecef = ecef_from_geodetic(entry.lon_deg, entry.lat_deg, entry.elev_m)
                rel_target = (
                    target_ecef[0] - base_mesh.center_ecef[0],
                    target_ecef[1] - base_mesh.center_ecef[1],
                    target_ecef[2] - base_mesh.center_ecef[2],
                )
                target_center_enu = rotate3(rel_target, base_rot)
                source_center_enu = child_center_enu if child_center_enu is not None else _mesh_center(child_mesh)
                if source_center_enu is not None:
                    delta = (
                        target_center_enu[0] - source_center_enu[0],
                        target_center_enu[1] - source_center_enu[1],
                        target_center_enu[2] - source_center_enu[2],
                    )
                    child_mesh = _translate_mesh(child_mesh, delta)

            if target_center_enu is None:
                target_center_enu = child_center_enu

            if not _is_reasonable_local_mesh(child_mesh) and target_center_enu is not None:
                # Some BTGs contain a large constant ENU offset after parse/convert;
                # re-anchor around the intended tile center instead of skipping.
                recovered = _reanchor_mesh_center(child_mesh, target_center_enu)
                if _is_reasonable_local_mesh(recovered):
                    child_mesh = recovered

            if not _is_reasonable_local_mesh(child_mesh):
                debug["skipped"] = int(debug["skipped"]) + 1
                continue
            static_meshes.append(child_mesh)
            debug["btg_objects_loaded"] = int(debug["btg_objects_loaded"]) + 1
            continue

        if (
            entry.lon_deg is not None
            and entry.lat_deg is not None
            and entry.elev_m is not None
            and base_rot is not None
            and base_mesh.center_ecef != (0.0, 0.0, 0.0)
        ):
            obj_ecef = ecef_from_geodetic(entry.lon_deg, entry.lat_deg, entry.elev_m)
            rel = (
                obj_ecef[0] - base_mesh.center_ecef[0],
                obj_ecef[1] - base_mesh.center_ecef[1],
                obj_ecef[2] - base_mesh.center_ecef[2],
            )
            center_enu = rotate3(rel, base_rot)

            model_path = object_full
            if ext == ".xml" and object_full:
                model_path = _resolve_model_path_from_xml(object_full, flightgear_root)

            model_mesh_template: Optional[BTGMesh] = None
            model_is_ac = False
            if model_path:
                cached = shared_model_mesh_cache.get(model_path)
                if cached is None and model_path not in shared_model_mesh_cache:
                    model_ext = os.path.splitext(model_path.lower())[1]
                    if model_ext == ".ac":
                        cached = _load_ac3d_mesh(model_path, flightgear_root)
                    elif model_ext in {".btg", ".gz"}:
                        try:
                            cached, _model_stats = validate_and_load_btg(model_path)
                            cached = transform_mesh_to_base_enu(cached, base_mesh.center_ecef, assume_local_enu=True)
                        except Exception:
                            cached = None
                    else:
                        cached = None
                    shared_model_mesh_cache[model_path] = cached
                else:
                    cached = shared_model_mesh_cache.get(model_path)

                model_ext = os.path.splitext(model_path.lower())[1]
                if model_ext == ".ac" and cached is not None and cached.face_materials:
                    has_file_material = any(m.startswith("__file__:") for m in cached.face_materials)
                    old_total = max(1, len(cached.face_materials))
                    old_ac_object = sum(1 for m in cached.face_materials if m == "ac_object")
                    if has_file_material and old_ac_object > 0:
                        reloaded = _load_ac3d_mesh(model_path, flightgear_root)
                        if reloaded is not None and reloaded.faces and reloaded.face_materials:
                            new_total = max(1, len(reloaded.face_materials))
                            new_ac_object = sum(1 for m in reloaded.face_materials if m == "ac_object")
                            old_ratio = old_ac_object / float(old_total)
                            new_ratio = new_ac_object / float(new_total)
                            if new_ratio + 1e-6 < old_ratio:
                                cached = reloaded
                                shared_model_mesh_cache[model_path] = cached

                model_mesh_template = cached
                model_is_ac = model_ext == ".ac"

            if model_mesh_template and model_mesh_template.vertices and model_mesh_template.faces:
                model_instances.append(
                    STGModelInstance(
                        template_mesh=model_mesh_template,
                        origin_enu=center_enu,
                        heading_deg=entry.heading_deg,
                        pitch_deg=entry.pitch_deg,
                        roll_deg=entry.roll_deg,
                        is_ac_model=model_is_ac,
                        source_path=entry.object_path,
                        stg_directive=entry.directive,
                        stg_entry_index=entry_index,
                    )
                )
                debug["model_objects_loaded"] = int(debug["model_objects_loaded"]) + 1
                continue

            size = 3.0 if entry.directive == "OBJECT_SHARED" else 4.0
            proxy = _make_proxy_cube(center_enu, size, f"proxy_{entry.directive.lower()}", base_mesh.center_ecef)
            static_meshes.append(proxy)
            debug["proxy_objects_loaded"] = int(debug["proxy_objects_loaded"]) + 1
            continue

        debug["skipped"] = int(debug["skipped"]) + 1

    return static_meshes, model_instances, debug


