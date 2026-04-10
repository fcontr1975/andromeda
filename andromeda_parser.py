#!/usr/bin/env python3
"""BTG data loading and scene composition helpers."""

from __future__ import annotations

import gzip
import json
import math
import os
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
    return lowered.endswith((".png", ".jpg", ".jpeg", ".dds", ".tga", ".bmp")) and not any(tag in lowered for tag in blocked)


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
        for replacement in (".png", ".jpg", ".jpeg", ".bmp", ".tga"):
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
        object_nudge_step_m = loaded.get("object_nudge_step_m")
        object_nudge_repeat_delay_s = loaded.get("object_nudge_repeat_delay_s")
        object_nudge_repeat_interval_s = loaded.get("object_nudge_repeat_interval_s")
        missing_material_color_rgb = loaded.get("missing_material_color_rgb")
        custom_scenery_paths = loaded.get("custom_scenery_paths")
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
        if isinstance(object_nudge_step_m, (int, float)):
            result["object_nudge_step_m"] = float(object_nudge_step_m)
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
        return result
    except Exception:
        return {}


def save_viewer_config(
    flightgear_root: str,
    material_map_path: str,
    textured_mode: bool,
    camera_frame_distance_factor: float,
    camera_frame_height_factor: float,
    object_nudge_step_m: float = 1.0,
    object_nudge_repeat_delay_s: float = 0.25,
    object_nudge_repeat_interval_s: float = 0.06,
    missing_material_color_rgb: Tuple[int, int, int] = (255, 0, 255),
    custom_scenery_paths: Optional[Sequence[str]] = None,
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
        "object_nudge_step_m": object_nudge_step_m,
        "object_nudge_repeat_delay_s": object_nudge_repeat_delay_s,
        "object_nudge_repeat_interval_s": object_nudge_repeat_interval_s,
        "missing_material_color_rgb": [
            int(max(0, min(255, missing_material_color_rgb[0]))),
            int(max(0, min(255, missing_material_color_rgb[1]))),
            int(max(0, min(255, missing_material_color_rgb[2]))),
        ],
        "custom_scenery_paths": [path for path in (custom_scenery_paths or []) if path],
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
    if not fg_root:
        return None

    if material_name.startswith("__file__:"):
        direct_path = material_name[len("__file__:") :]
        return direct_path if os.path.isfile(direct_path) else None

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
