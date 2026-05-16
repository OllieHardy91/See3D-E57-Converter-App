# See3D — E57 Converter

A desktop GUI for converting **Realsee Galois M2** LiDAR captures into a
[COLMAP](https://colmap.github.io/)-compatible dataset ready for Gaussian Splatting training.

![See3D E57 Converter](assets/favicon-dark-512.png)

---

## What it does

The Realsee Galois M2 exports a single `.e57` file containing LiDAR point clouds and
scan poses, alongside a folder of equirectangular panorama images. This tool:

1. Reads each panorama and renders **6 cubemap face images** per scan position (or 4,
   excluding floor/ceiling)
2. Reads scan poses from the `.e57` and writes a **COLMAP dataset**
   (`cameras.txt`, `images.txt`, `points3D.txt`)
3. Subsamples the LiDAR point cloud into `points3D.txt` as a Gaussian Splatting
   initialisation cloud
4. Optionally runs an **alignment validation** — re-projects LiDAR points through the
   generated camera poses and reports a mean colour-diff score (5–9 is healthy on a
   well-captured M2 dataset)

The output `Colmap/` folder can be opened directly in any Gaussian Splatting trainer
(Brush, gsplat, INRIA 3DGS, etc.).

---

## Dataset structure expected

```
your_dataset/
├── images/          ← equirectangular panoramas (1.jpg … N.jpg)
└── points/
    └── data.e57     ← Realsee .e57 export
```

---

## Installation

**Python 3.10 or 3.11 recommended.**

```bash
pip install -r requirements.txt
python app.py
```

Or double-click **`build.bat`** to produce a standalone `.exe` (requires PyInstaller):

```
build.bat
```

The exe will appear as `See3D_E57_Converter.exe` in the same folder.

---

## Usage

1. Launch the app (`python app.py` or the `.exe`)
2. Drop your `.e57` file and panoramas folder onto the **Input Files** card, or use Browse
3. Select an output folder
4. Pick a **Scene Preset** based on scene size:

| Preset | Points | Best for |
|--------|--------|----------|
| Small | 500K | Studio / single room |
| Standard | 1M | 4–6 rooms |
| Large | 4M | Multi-storey |
| Huge | 6M | Estate / large commercial |
| Custom | — | Manual point budget |

5. Click **CONVERT** — progress updates live as each scan is processed
6. Switch to the **Validate** tab to check alignment quality after conversion

---

## Calibration notes

These values are empirically validated on real M2 captures and should not be changed
without re-running alignment validation:

| Parameter | Value | Reason |
|-----------|-------|--------|
| `yaw_offset` | 0.0° | Script's azimuth convention matches Realsee's panorama frame exactly |
| `camera_offset` | (0, 0, 0) | Realsee internally aligns panorama to LiDAR origin in the `.e57` |
| `face_size` | 4000 px | Matches M2 native resolution (16000 × 8000 ÷ 4) |

Alignment validation score guide:
- **5–9** — healthy, expected range for a well-captured M2 dataset
- **10–14** — marginal, check panorama count matches scan count
- **15+** — issue — verify the `.e57` and images folder belong to the same capture

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `customtkinter` | GUI framework |
| `pye57` | Read `.e57` LiDAR files |
| `Pillow` | Image processing |
| `numpy` / `scipy` | Point cloud maths |
| `opencv-python` | Cubemap rendering |
| `tqdm` | Progress reporting |
| `tkinterdnd2` | Drag-and-drop support |

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

Built by [See3D](https://see3d.co.uk) · Contributions welcome
