# converter_core.py
# Realsee Galois M2 -> COLMAP converter (6-face and 4-face modes).
# Calibration constants must not change. See CLAUDE.md for the empirical
# validation history behind these defaults.

from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import numpy as np

__version__ = "1.1.0"

# Converts Realsee scan-world frame (Y-down after pose) to COLMAP Z-up world.
# Applied identically to points AND poses - do not modify.
FLIP_MAT = np.array([
    [1, 0,  0, 0],
    [0, 0, -1, 0],
    [0, 1,  0, 0],
    [0, 0,  0, 1],
], dtype=np.float64)
FLIP_ROT     = FLIP_MAT[:3, :3]
FLIP_ROT_F32 = FLIP_ROT.astype(np.float32)

DEFAULT_CAMERA_OFFSET = np.zeros(3, dtype=np.float64)

FACE_ORDER         = ("px", "nx", "py", "ny", "pz", "nz")
HORIZONTAL_FACES   = ("px", "nx", "pz", "nz")
NADIR_ZENITH_FACES = ("py", "ny")

# Camera-to-scan-local rotations per cubemap face.
# Validated against both residential (223-scan) and commercial (45-scan) M2
# datasets. Changing any entry breaks alignment - re-validate before touching.
FACE_ROTATIONS = {
    "pz": np.eye(3, dtype=np.float64),
    "nz": np.array([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=np.float64),
    "px": np.array([[0, 0, 1],  [0, 1, 0], [-1, 0, 0]], dtype=np.float64),
    "nx": np.array([[0, 0, -1], [0, 1, 0], [1,  0, 0]], dtype=np.float64),
    "py": np.array([[1, 0, 0],  [0, 0, 1], [0, -1, 0]], dtype=np.float64),
    "ny": np.array([[1, 0, 0],  [0, 0, -1],[0,  1, 0]], dtype=np.float64),
}


# ---- lazy imports ----------------------------------------------------------

@lru_cache(maxsize=1)
def load_cv2():
    try:
        import cv2
        return cv2
    except ImportError as e:
        raise SystemExit("Missing 'opencv-python'. Run: pip install opencv-python") from e

@lru_cache(maxsize=1)
def load_pye57():
    try:
        import pye57
        return pye57
    except ImportError as e:
        raise SystemExit("Missing 'pye57'. Run: pip install pye57") from e

@lru_cache(maxsize=1)
def load_rotation():
    try:
        from scipy.spatial.transform import Rotation
        return Rotation
    except ImportError as e:
        raise SystemExit("Missing 'scipy'. Run: pip install scipy") from e

@lru_cache(maxsize=1)
def load_tqdm():
    try:
        from tqdm.auto import tqdm
        return tqdm
    except ImportError as e:
        raise SystemExit("Missing 'tqdm'. Run: pip install tqdm") from e


# ---- safe E57 handle -------------------------------------------------------

@contextmanager
def open_e57(path):
    """Context manager wrapper around pye57.E57 to ensure the C++ handle
    is released promptly. pye57 may or may not expose .close() depending on
    version, so we fall back to del + the GC."""
    pye57 = load_pye57()
    handle = pye57.E57(str(path))
    try:
        yield handle
    finally:
        try:
            close = getattr(handle, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
        del handle


def get_e57_scan_count(path) -> int:
    """Open the e57 just long enough to read scan_count, then release."""
    with open_e57(path) as h:
        return int(h.scan_count)


# ---- file helpers ----------------------------------------------------------

def _natural_key(p: Path) -> list:
    return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", p.name.lower())]


def list_panoramas(images_dir: Path) -> list[Path]:
    if not images_dir.is_dir():
        raise FileNotFoundError(f"Images directory not found: {images_dir}")
    imgs = sorted(
        [p for p in images_dir.iterdir()
         if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}],
        key=_natural_key,
    )
    if not imgs:
        raise FileNotFoundError(f"No images found in {images_dir}")
    return imgs


def count_panoramas(images_dir: Path) -> int:
    if not images_dir.is_dir():
        return 0
    return sum(1 for p in images_dir.iterdir()
               if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"})


# ---- cubemap generation ----------------------------------------------------

def precompute_face_maps(face_size: int) -> dict:
    u = np.linspace(-1, 1, face_size, dtype=np.float32)
    v = np.linspace(-1, 1, face_size, dtype=np.float32)
    uu, vv = np.meshgrid(u, v)
    face_defs = {
        "px": lambda u_, v_: np.stack([np.ones_like(u_),  -v_, -u_], axis=-1),
        "nx": lambda u_, v_: np.stack([-np.ones_like(u_), -v_,  u_], axis=-1),
        "py": lambda u_, v_: np.stack([u_, -np.ones_like(u_), -v_], axis=-1),
        "ny": lambda u_, v_: np.stack([u_,  np.ones_like(u_),  v_], axis=-1),
        "pz": lambda u_, v_: np.stack([u_, -v_,  np.ones_like(u_)], axis=-1),
        "nz": lambda u_, v_: np.stack([-u_, -v_, -np.ones_like(u_)], axis=-1),
    }
    maps = {}
    for face in FACE_ORDER:
        vec = face_defs[face](uu, vv)
        vec = vec / np.linalg.norm(vec, axis=-1, keepdims=True)
        maps[face] = (np.arctan2(vec[..., 0], vec[..., 2]), np.arcsin(vec[..., 1]))
    return maps


def pano_to_cubemap(pano: np.ndarray, yaw_offset_px: int, face_maps: dict) -> dict:
    cv2 = load_cv2()
    h, w = pano.shape[:2]
    faces = {}
    for face in FACE_ORDER:
        theta, phi = face_maps[face]
        map_x = (theta / (2 * np.pi) + 0.5) * w
        map_y = (0.5 - phi / np.pi) * h
        if yaw_offset_px:
            map_x = (map_x + yaw_offset_px) % w
        faces[face] = cv2.remap(
            pano,
            map_x.astype(np.float32),
            map_y.astype(np.float32),
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_WRAP,
        )
    return faces


def process_panorama(
    image_id: int,
    pano_path: Path,
    output_dir: Path,
    yaw_offset_px: int,
    face_maps: dict,
    nadir_zenith_dir: Path | None,
) -> None:
    cv2 = load_cv2()
    pano = cv2.imread(str(pano_path), cv2.IMREAD_COLOR)
    if pano is None:
        raise RuntimeError(f"Failed to load: {pano_path}")
    faces = pano_to_cubemap(pano, yaw_offset_px, face_maps)
    for face_name, face_img in faces.items():
        if face_name in NADIR_ZENITH_FACES and nadir_zenith_dir is not None:
            out = nadir_zenith_dir / f"{image_id}_{face_name}.jpg"
        else:
            out = output_dir / f"{image_id}_{face_name}.jpg"
        cv2.imwrite(str(out), face_img)


# Worker-pool globals - populated by _worker_init in each subprocess so the
# face_maps dict is built once per worker instead of pickled with every task.
_W_FACE_MAPS: dict | None = None
_W_YAW_PX: int = 0
_W_OUT_DIR: Path | None = None
_W_NZ_DIR: Path | None = None


def _worker_init(face_size: int, yaw_offset_px: int,
                 out_dir: str, nz_dir: str | None) -> None:
    global _W_FACE_MAPS, _W_YAW_PX, _W_OUT_DIR, _W_NZ_DIR
    _W_FACE_MAPS = precompute_face_maps(face_size)
    _W_YAW_PX    = yaw_offset_px
    _W_OUT_DIR   = Path(out_dir)
    _W_NZ_DIR    = Path(nz_dir) if nz_dir else None


def _worker_run(image_id: int, pano_path: str) -> None:
    process_panorama(image_id, Path(pano_path),
                     _W_OUT_DIR, _W_YAW_PX, _W_FACE_MAPS, _W_NZ_DIR)


def _detect_pano_width(images: list[Path]) -> int:
    cv2 = load_cv2()
    for p in images:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is not None:
            return img.shape[1]
    raise RuntimeError("Failed to load any panorama image")


def _emit_progress(cb, frac: float, phase: str = "") -> None:
    if cb is None:
        return
    try:
        cb(frac, phase)
    except TypeError:
        # Backwards compat: older single-arg callbacks.
        cb(frac)


def convert_panoramas_to_cubemaps(
    images: list[Path],
    output_dir: Path,
    face_size: int | None,
    yaw_offset_deg: float,
    workers: int | None,
    nadir_zenith_dir: Path | None = None,
    progress_callback=None,
    cancel_event=None,
) -> int:
    tqdm = load_tqdm()
    pano_w = _detect_pano_width(images)
    fs = face_size or (pano_w // 4)
    yaw_px = int(round((yaw_offset_deg / 360.0) * pano_w))
    total = len(images)

    # Default to a conservative worker count - more than 8 risks RAM pressure
    # because each worker holds a precomputed face_maps dict (~750 MB at fs=4000).
    cpu = os.cpu_count() or 1
    if workers is None or workers <= 0:
        max_w = max(1, min(8, cpu, total))
    else:
        max_w = max(1, min(workers, total))

    print(f"Cubemap generation: {total} panoramas | face_size={fs} | workers={max_w}")

    if max_w == 1:
        face_maps = precompute_face_maps(fs)
        for i, (image_id, path) in enumerate(
            zip(range(1, total + 1), tqdm(images, desc="Cubemap faces", unit="pano"))
        ):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Cancelled by user")
            process_panorama(image_id, path, output_dir, yaw_px, face_maps, nadir_zenith_dir)
            _emit_progress(progress_callback, (i + 1) / total * 0.85,
                           f"Rendering cubemap {i + 1}/{total}")
        return fs

    nz_arg = str(nadir_zenith_dir) if nadir_zenith_dir is not None else None
    with ProcessPoolExecutor(
        max_workers=max_w,
        initializer=_worker_init,
        initargs=(fs, yaw_px, str(output_dir), nz_arg),
    ) as ex:
        futures = {
            ex.submit(_worker_run, i + 1, str(p)): i
            for i, p in enumerate(images)
        }
        bar = tqdm(total=total, desc="Cubemap faces", unit="pano")
        completed = 0
        try:
            for fut in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    for f in futures:
                        f.cancel()
                    raise InterruptedError("Cancelled by user")
                fut.result()
                completed += 1
                bar.update(1)
                _emit_progress(progress_callback, completed / total * 0.85,
                               f"Rendering cubemap {completed}/{total}")
        finally:
            bar.close()
    return fs


# ---- COLMAP text writers ---------------------------------------------------

def write_cameras_txt(path: Path, face_size: int) -> None:
    f = face_size / 2.0
    with path.open("w", encoding="utf-8") as h:
        h.write("# Camera list\n# CAMERA_ID MODEL WIDTH HEIGHT PARAMS[]\n")
        h.write(f"1 PINHOLE {face_size} {face_size} {f} {f} {f} {f}\n")


def _yaw_rotation_matrix(deg: float) -> np.ndarray:
    r = np.radians(deg)
    return np.array([
        [np.cos(r), 0, np.sin(r)],
        [0,         1, 0        ],
        [-np.sin(r),0, np.cos(r)],
    ], dtype=np.float64)


def _scan_pose_matrix(e57_handle, scan_id: int, camera_offset: np.ndarray) -> np.ndarray:
    Rotation = load_rotation()
    hdr = e57_handle.get_header(scan_id)
    rot = Rotation.from_quat([
        hdr.rotation[1], hdr.rotation[2], hdr.rotation[3], hdr.rotation[0]
    ]).as_matrix()
    t = np.array(hdr.translation, dtype=np.float64)
    c2w = np.eye(4, dtype=np.float64)
    c2w[:3, :3] = rot
    c2w[:3, 3] = t
    if np.any(camera_offset != 0.0):
        c2w[:3, 3] += rot @ camera_offset
    return c2w


def write_images_txt(
    e57_handle,
    path: Path,
    num_scans: int,
    yaw_offset_deg: float,
    camera_offset: np.ndarray,
    include_nadir_zenith: bool = True,
) -> None:
    Rotation = load_rotation()
    tqdm = load_tqdm()
    yaw_rot = _yaw_rotation_matrix(yaw_offset_deg)
    faces = FACE_ORDER if include_nadir_zenith else HORIZONTAL_FACES
    image_id = 1

    with path.open("w", encoding="utf-8") as h:
        h.write("# Image list\n# IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME\n")
        for scan_idx in tqdm(range(num_scans), desc="Image poses", unit="scan"):
            base_c2w = _scan_pose_matrix(e57_handle, scan_idx, camera_offset)
            for face in faces:
                c2w = base_c2w.copy()
                c2w[:3, :3] = c2w[:3, :3] @ FACE_ROTATIONS[face]
                c2w = FLIP_MAT @ c2w
                if yaw_offset_deg:
                    c2w[:3, :3] = yaw_rot @ c2w[:3, :3]
                w2c = np.linalg.inv(c2w)
                qx, qy, qz, qw = Rotation.from_matrix(w2c[:3, :3]).as_quat()
                tx, ty, tz = w2c[:3, 3]
                h.write(
                    f"{image_id} {qw} {qx} {qy} {qz} {tx} {ty} {tz}"
                    f" 1 {scan_idx + 1}_{face}.jpg\n\n"
                )
                image_id += 1


# ---- point cloud export ----------------------------------------------------

def _extract_rgb(points: dict, count: int) -> np.ndarray:
    if {"colorRed", "colorGreen", "colorBlue"}.issubset(points):
        return np.vstack([points["colorRed"], points["colorGreen"], points["colorBlue"]]).T.astype(np.uint8)
    return np.full((count, 3), 255, dtype=np.uint8)


def _read_scan_chunk(e57_handle, scan_id: int) -> tuple[np.ndarray, np.ndarray]:
    pts = e57_handle.read_scan(scan_id, colors=True, ignore_missing_fields=True)
    xyz = np.vstack([pts["cartesianX"], pts["cartesianY"], pts["cartesianZ"]]).T.astype(np.float32)
    xyz = xyz @ FLIP_ROT_F32.T
    rgb = _extract_rgb(pts, xyz.shape[0])
    return xyz, rgb


def _get_scan_counts(e57_handle) -> list[int] | None:
    counts = []
    for sid in range(e57_handle.scan_count):
        hdr = e57_handle.get_header(sid)
        sc = None
        for attr in ("point_count", "pointCount", "points_count", "pointsCount"):
            v = getattr(hdr, attr, None)
            if isinstance(v, (int, np.integer)):
                sc = int(v); break
        if sc is None:
            return None
        counts.append(sc)
    return counts


def _select_indices(total: int, max_pts: int, seed: int) -> np.ndarray | None:
    if max_pts >= total:
        return None
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(total, size=max_pts, replace=False)).astype(np.int64)


def _write_point_rows(h, start_id: int, xyz: np.ndarray, rgb: np.ndarray) -> int:
    n = xyz.shape[0]
    if n == 0:
        return start_id
    ids = np.arange(start_id, start_id + n, dtype=np.int64)
    errs = np.zeros((n, 1), dtype=np.float32)
    np.savetxt(h, np.column_stack([ids, xyz, rgb, errs]),
               fmt="%d %.6f %.6f %.6f %d %d %d %.6f")
    return start_id + n


def write_points3d_txt(
    e57_handle,
    path: Path,
    max_points: int | None,
    seed: int,
    progress_callback=None,
    progress_lo: float = 0.92,
    progress_hi: float = 1.0,
) -> None:
    tqdm = load_tqdm()
    total_scans = e57_handle.scan_count

    def _phase_progress(idx_done: int) -> None:
        if total_scans > 0:
            frac = progress_lo + (progress_hi - progress_lo) * (idx_done / total_scans)
            _emit_progress(progress_callback, frac,
                           f"Writing points cloud ({idx_done}/{total_scans} scans)")

    with path.open("w", encoding="utf-8") as h:
        h.write("# 3D point list\n# POINT3D_ID X Y Z R G B ERROR TRACK[]\n")
        pid = 1

        if max_points is None:
            for sid in tqdm(range(total_scans), desc="Exporting points", unit="scan"):
                xyz, rgb = _read_scan_chunk(e57_handle, sid)
                pid = _write_point_rows(h, pid, xyz, rgb)
                _phase_progress(sid + 1)
            return

        scan_counts = _get_scan_counts(e57_handle)
        if scan_counts is not None:
            total = sum(scan_counts)
            sel = _select_indices(total, max_points, seed)
            if sel is None:
                for sid in tqdm(range(total_scans), desc="Exporting points", unit="scan"):
                    xyz, rgb = _read_scan_chunk(e57_handle, sid)
                    pid = _write_point_rows(h, pid, xyz, rgb)
                    _phase_progress(sid + 1)
                return
            chunk_start = 0
            for sid, sc in enumerate(tqdm(scan_counts, desc="Exporting sampled points", unit="scan")):
                chunk_end = chunk_start + sc
                s0 = int(np.searchsorted(sel, chunk_start, side="left"))
                s1 = int(np.searchsorted(sel, chunk_end,   side="left"))
                local = sel[s0:s1] - chunk_start
                if local.size > 0:
                    xyz, rgb = _read_scan_chunk(e57_handle, sid)
                    pid = _write_point_rows(h, pid, xyz[local], rgb[local])
                chunk_start = chunk_end
                _phase_progress(sid + 1)
            return

        print("Note: loading all points first (scan counts unavailable)")
        chunks, total = [], 0
        for sid in tqdm(range(total_scans), desc="Loading points", unit="scan"):
            xyz, rgb = _read_scan_chunk(e57_handle, sid)
            if xyz.shape[0]:
                chunks.append((xyz, rgb)); total += xyz.shape[0]
        sel = _select_indices(total, max_points, seed)
        chunk_start = 0
        for sid, (xyz, rgb) in enumerate(chunks):
            chunk_end = chunk_start + xyz.shape[0]
            s0 = int(np.searchsorted(sel, chunk_start))
            s1 = int(np.searchsorted(sel, chunk_end))
            local = sel[s0:s1] - chunk_start
            if local.size > 0:
                pid = _write_point_rows(h, pid, xyz[local], rgb[local])
            chunk_start = chunk_end
            _phase_progress(sid + 1)


# ---- top-level entry point -------------------------------------------------

def run_conversion(
    images_dir: Path,
    points_path: Path,
    colmap_dir: Path,
    face_size: int | None = 4000,
    yaw_offset_deg: float = 0.0,
    workers: int | None = None,
    max_points: int | None = 1_000_000,
    camera_offset: np.ndarray | None = None,
    seed: int = 0,
    include_nadir_zenith: bool = False,
    progress_callback=None,
    cancel_event=None,
    clean_orphan_faces: bool = True,
) -> None:
    """Convert a Realsee Galois M2 capture into a COLMAP dataset.

    include_nadir_zenith=True  -> 6-face mode (all faces in images.txt)
    include_nadir_zenith=False -> 4-face mode (py/ny go to sibling nadir_and_zenith/)
    """
    if not points_path.is_file():
        raise FileNotFoundError(f"E57 file not found: {points_path}")

    images = list_panoramas(images_dir)

    with open_e57(points_path) as e57:
        scan_count = int(e57.scan_count)

        if len(images) != scan_count:
            raise ValueError(
                f"Panorama count ({len(images)}) does not match E57 scan count "
                f"({scan_count}). Check that images/ contains exactly one JPEG per scan."
            )

        print(f"Loaded {len(images)} panoramas  |  {scan_count} scans in E57")

        colmap_dir.mkdir(parents=True, exist_ok=True)
        img_out = colmap_dir / "images"
        img_out.mkdir(parents=True, exist_ok=True)

        nadir_zenith_dir: Path | None = None
        if not include_nadir_zenith:
            nadir_zenith_dir = colmap_dir.parent / "nadir_and_zenith"
            nadir_zenith_dir.mkdir(parents=True, exist_ok=True)
            print(f"4-face mode  ->  py/ny faces saved to: {nadir_zenith_dir.name}/")
            if clean_orphan_faces:
                _purge_orphan_pole_faces(img_out)
        else:
            print("6-face mode  ->  all faces included in COLMAP dataset")
            if clean_orphan_faces:
                _purge_orphan_pole_faces(colmap_dir.parent / "nadir_and_zenith", remove_dir=True)

        _emit_progress(progress_callback, 0.0, "Starting cubemap render")

        fs = convert_panoramas_to_cubemaps(
            images, img_out, face_size, yaw_offset_deg, workers, nadir_zenith_dir,
            progress_callback=progress_callback, cancel_event=cancel_event,
        )

        print("Writing cameras.txt ...")
        write_cameras_txt(colmap_dir / "cameras.txt", fs)
        _emit_progress(progress_callback, 0.88, "Writing cameras.txt")

        print("Writing images.txt ...")
        write_images_txt(
            e57,
            colmap_dir / "images.txt",
            len(images),
            yaw_offset_deg,
            camera_offset if camera_offset is not None else DEFAULT_CAMERA_OFFSET,
            include_nadir_zenith=include_nadir_zenith,
        )
        _emit_progress(progress_callback, 0.92, "Writing points3D.txt")

        write_points3d_txt(
            e57, colmap_dir / "points3D.txt", max_points, seed,
            progress_callback=progress_callback,
            progress_lo=0.92, progress_hi=1.0,
        )
        _emit_progress(progress_callback, 1.0, "Done")

        n_faces = len(FACE_ORDER if include_nadir_zenith else HORIZONTAL_FACES)
        print(f"\nConversion complete - {len(images) * n_faces} cubemap images -> {colmap_dir}")


def _purge_orphan_pole_faces(target: Path, remove_dir: bool = False) -> None:
    """Remove leftover _py/_ny face JPEGs from a prior conversion in the
    opposite mode so Brush doesn't see images without matching images.txt
    entries. Best-effort - errors are swallowed."""
    try:
        if not target.exists():
            return
        if remove_dir and target.is_dir():
            for f in target.iterdir():
                try: f.unlink()
                except Exception: pass
            try: target.rmdir()
            except Exception: pass
            return
        if target.is_dir():
            for f in target.iterdir():
                if f.is_file() and re.match(r"^\d+_(py|ny)\.jpg$", f.name, re.IGNORECASE):
                    try: f.unlink()
                    except Exception: pass
    except Exception:
        pass


# ---- overlay helpers -------------------------------------------------------

def get_available_scans(colmap_dir: Path) -> list[int]:
    """Return sorted list of scan indices present in images.txt."""
    scan_ids: set[int] = set()
    for line in (colmap_dir / "images.txt").read_text().splitlines():
        m = re.search(r"(\d+)_[a-z]{2}\.jpg$", line.strip())
        if m:
            scan_ids.add(int(m.group(1)) - 1)
    return sorted(scan_ids)


def generate_overlay(
    colmap_dir: Path,
    e57_path: Path,
    scan_id: int = 0,
    face: str = "pz",
    max_points: int = 6000,
):
    """Generate a 3-panel image: cubemap face | LiDAR overlay | LiDAR dots only.
    Returns a PIL Image for display in the GUI."""
    from PIL import Image as PILImage, ImageDraw

    Rotation = load_rotation()
    PILImage.MAX_IMAGE_PIXELS = None

    cams: dict = {}
    for line in (colmap_dir / "cameras.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        cams[int(p[0])] = {"W": int(p[2]), "H": int(p[3]), "params": [float(x) for x in p[4:]]}
    cam = cams[1]
    fx, fy, cx, cy = cam["params"]
    W, H = cam["W"], cam["H"]

    target_name = f"{scan_id + 1}_{face}.jpg"
    entry: dict | None = None
    lines = (colmap_dir / "images.txt").read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip(); i += 1
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 10 and parts[9] == target_name:
            entry = {
                "qw": float(parts[1]), "qx": float(parts[2]),
                "qy": float(parts[3]), "qz": float(parts[4]),
                "tx": float(parts[5]), "ty": float(parts[6]), "tz": float(parts[7]),
            }
            break
        if i < len(lines):
            i += 1

    if entry is None:
        raise ValueError(f"No COLMAP entry for {target_name}")

    R_w2c = Rotation.from_quat([entry["qx"], entry["qy"], entry["qz"], entry["qw"]]).as_matrix()
    t_w2c = np.array([entry["tx"], entry["ty"], entry["tz"]])

    with open_e57(e57_path) as e57:
        pts = e57.read_scan(scan_id, colors=True, transform=True, ignore_missing_fields=True)
    x = pts["cartesianX"]; y = pts["cartesianY"]; z = pts["cartesianZ"]
    r = pts.get("colorRed",   np.full_like(x, 180, dtype=np.uint8))
    g = pts.get("colorGreen", np.full_like(x, 180, dtype=np.uint8))
    b = pts.get("colorBlue",  np.full_like(x, 180, dtype=np.uint8))

    P = np.stack([x, y, z], axis=1).astype(np.float64) @ FLIP_ROT.T
    P_cam = P @ R_w2c.T + t_w2c
    z_c = P_cam[:, 2]
    ok = z_c > 0.01
    u_f = fx * P_cam[:, 0] / np.where(ok, z_c, 1.0) + cx
    v_f = fy * P_cam[:, 1] / np.where(ok, z_c, 1.0) + cy
    valid = ok & (u_f >= 0) & (u_f < W) & (v_f >= 0) & (v_f < H)

    idx = np.where(valid)[0]
    if idx.size > max_points:
        rng = np.random.default_rng(42)
        idx = rng.choice(idx, size=max_points, replace=False)

    face_path = colmap_dir / "images" / target_name
    base = PILImage.open(face_path).convert("RGB")

    panel_w = 380
    factor = panel_w / W
    panel_h = int(H * factor)

    base_small = base.resize((panel_w, panel_h), PILImage.LANCZOS)
    dots_only = PILImage.new("RGB", (panel_w, panel_h), (240, 245, 255))
    overlay = base_small.copy()

    draw_ov = ImageDraw.Draw(overlay)
    draw_do = ImageDraw.Draw(dots_only)
    ds = 3

    for ii in idx:
        u_px = int(u_f[ii] * factor)
        v_px = int(v_f[ii] * factor)
        col  = (int(r[ii]), int(g[ii]), int(b[ii]))
        bbox = [u_px - ds, v_px - ds, u_px + ds, v_px + ds]
        draw_ov.ellipse(bbox, fill=col, outline=(255, 220, 0))
        draw_do.ellipse(bbox, fill=col)

    gap = 10
    label_h = 26
    total_w = panel_w * 3 + gap * 2
    total_h = panel_h + label_h
    out = PILImage.new("RGB", (total_w, total_h), (248, 251, 255))
    draw = ImageDraw.Draw(out)

    out.paste(base_small, (0, label_h))
    out.paste(overlay,    (panel_w + gap, label_h))
    out.paste(dots_only,  (panel_w * 2 + gap * 2, label_h))

    for text, x_pos in [
        ("Cubemap face",   panel_w // 2),
        ("LiDAR overlay",  panel_w + gap + panel_w // 2),
        ("LiDAR dots only",panel_w * 2 + gap * 2 + panel_w // 2),
    ]:
        tw = len(text) * 6
        draw.text((x_pos - tw // 2, 5), text, fill=(0, 58, 140))

    n_proj = int(valid.sum())
    caption = f"Scan {scan_id}  -  face {face}  -  {n_proj:,} projected points"
    draw.text((8, total_h - 18), caption, fill=(80, 100, 140))

    return out


# ---- alignment validation --------------------------------------------------

def validate_conversion(
    colmap_dir: Path,
    e57_path: Path,
    num_scans: int = 11,
    max_points_per_scan: int = 5000,
    seed: int = 42,
    progress_callback=None,
) -> float | None:
    """Re-project LiDAR points through COLMAP poses and report mean colour diff.
    Expected range for a healthy M2 capture: 5-9.
    Returns the overall weighted mean diff, or None if validation could not run."""
    try:
        from PIL import Image as PILImage
    except ImportError as e:
        raise SystemExit("Missing 'pillow'. Run: pip install pillow") from e

    Rotation = load_rotation()
    PILImage.MAX_IMAGE_PIXELS = None

    cams: dict = {}
    for line in (colmap_dir / "cameras.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        cams[int(p[0])] = {"W": int(p[2]), "H": int(p[3]), "params": [float(x) for x in p[4:]]}
    cam = cams[1]
    fx, fy, cx, cy = cam["params"]
    W, H = cam["W"], cam["H"]

    by_scan: dict = {}
    lines = (colmap_dir / "images.txt").read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip(); i += 1
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        name = parts[9]
        m = re.match(r"(\d+)_([a-z]+)\.jpg", name)
        if not m:
            continue
        sid, face = int(m.group(1)) - 1, m.group(2)
        by_scan.setdefault(sid, {})[face] = {
            "qw": float(parts[1]), "qx": float(parts[2]),
            "qy": float(parts[3]), "qz": float(parts[4]),
            "tx": float(parts[5]), "ty": float(parts[6]), "tz": float(parts[7]),
            "name": name,
        }
        if i < len(lines):
            i += 1

    available = sorted(by_scan)
    if not available:
        print("No scans found in images.txt")
        return None

    if len(available) <= num_scans:
        scan_ids = available
    else:
        scan_ids = [
            available[int(round(k * (len(available) - 1) / (num_scans - 1)))]
            for k in range(num_scans)
        ]

    print(f"Validating {len(scan_ids)} of {len(available)} scans ...")
    rng = np.random.default_rng(seed)
    images_dir = colmap_dir / "images"

    all_diffs: list[float] = []
    total_pts = 0
    weighted_sum = 0.0

    with open_e57(e57_path) as e57:
        for idx_scan, sid in enumerate(scan_ids):
            if sid not in by_scan:
                continue
            _emit_progress(progress_callback,
                           (idx_scan + 1) / max(1, len(scan_ids)),
                           f"Validating scan {idx_scan + 1}/{len(scan_ids)}")
            pts = e57.read_scan(sid, colors=True, transform=True, ignore_missing_fields=True)
            x = pts["cartesianX"]; y = pts["cartesianY"]; z = pts["cartesianZ"]
            r = pts.get("colorRed",   np.full_like(x, 200, dtype=np.uint8))
            g = pts.get("colorGreen", np.full_like(x, 200, dtype=np.uint8))
            b = pts.get("colorBlue",  np.full_like(x, 200, dtype=np.uint8))
            P = np.stack([x, y, z], axis=1).astype(np.float64) @ FLIP_ROT.T

            if P.shape[0] > max_points_per_scan:
                idx = rng.choice(P.shape[0], size=max_points_per_scan, replace=False)
                P = P[idx]; r = np.asarray(r)[idx]; g = np.asarray(g)[idx]; b = np.asarray(b)[idx]
            P_rgb = np.stack([r, g, b], axis=1).astype(np.uint8)

            scan_diffs = []
            for face, entry in by_scan[sid].items():
                R = Rotation.from_quat([entry["qx"], entry["qy"], entry["qz"], entry["qw"]]).as_matrix()
                t = np.array([entry["tx"], entry["ty"], entry["tz"]])
                P_cam = P @ R.T + t
                z_c = P_cam[:, 2]
                ok = z_c > 0.01
                u = np.where(ok, fx * P_cam[:, 0] / np.where(ok, z_c, 1) + cx, -1.0)
                v = np.where(ok, fy * P_cam[:, 1] / np.where(ok, z_c, 1) + cy, -1.0)
                in_bounds = ok & (u >= 0) & (u < W) & (v >= 0) & (v < H)
                if not in_bounds.any():
                    continue
                img_path = images_dir / entry["name"]
                if not img_path.exists():
                    continue
                img = np.array(PILImage.open(img_path).convert("RGB"))
                us = np.clip(u[in_bounds].astype(np.int32), 0, W - 1)
                vs = np.clip(v[in_bounds].astype(np.int32), 0, H - 1)
                diff = float(np.abs(img[vs, us].astype(np.int16) - P_rgb[in_bounds].astype(np.int16)).mean())
                n = int(in_bounds.sum())
                scan_diffs.append(diff)
                all_diffs.append(diff)
                total_pts += n
                weighted_sum += diff * n

            if scan_diffs:
                print(f"  Scan {sid:4d}:  mean diff = {np.mean(scan_diffs):.2f}  ({len(scan_diffs)} faces)")

    if all_diffs:
        overall = weighted_sum / total_pts if total_pts else float("nan")
        status = "GOOD" if overall <= 10 else "HIGH - check inputs"
        print(f"\n  Overall: mean={np.mean(all_diffs):.2f}  weighted={overall:.2f}  [{status}]")
        print(f"  Expected range for a healthy M2 capture: 5-9")
        return overall

    print("No valid projections found - check that Colmap/images/ contains face images.")
    return None
