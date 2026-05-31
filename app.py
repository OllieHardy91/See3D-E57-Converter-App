"""See3D E57 Converter - GUI front-end for the Realsee Galois M2 -> COLMAP pipeline."""
from __future__ import annotations

import contextlib
import math
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

try:
    import tkinterdnd2 as _tkdnd
    _DND_FILES = _tkdnd.DND_FILES
    HAS_DND = True
except ImportError:
    _tkdnd = None
    _DND_FILES = None
    HAS_DND = False

try:
    from converter_core import __version__ as CORE_VERSION
    from converter_core import (
        run_conversion, validate_conversion, count_panoramas,
        get_e57_scan_count, get_available_scans, generate_overlay,
    )
except Exception:
    CORE_VERSION = "0.0.0"
    run_conversion = validate_conversion = count_panoramas = None          # type: ignore[assignment]
    get_e57_scan_count = get_available_scans = generate_overlay = None     # type: ignore[assignment]


def _safe_schedule(widget: tk.Misc, ms: int, fn) -> None:
    """Schedule fn on widget's event loop if the widget still exists."""
    try:
        if widget.winfo_exists():
            widget.after(ms, fn)
    except tk.TclError:
        pass

APP_VERSION = CORE_VERSION

# ---- Palette ---------------------------------------------------------------
C_BG      = "#EEF2FA"
C_CARD    = "#FFFFFF"
C_BORDER  = "#C4D4EC"
C_ACCENT  = "#00A6D7"   # active tab / progress bar
C_ACCENT2 = "#0058B3"   # browse borders / section bars
C_NAVY    = "#001B87"   # Convert button / active tab text
C_TEXT    = "#0D1E3A"
C_DIM     = "#5C789C"
C_HINT    = "#94B0CC"
C_LOG_BG  = "#EAF9FF"
C_SUCCESS = "#007A50"
C_WARN    = "#B06000"
C_ERROR   = "#B00020"
C_ERROR_SOFT = "#7A2230"   # muted red used for retry button

_TAB_BG       = "#001B87"
_TAB_ACTIVE   = "#00A6D7"
_TAB_INACTIVE = "#163FA8"
_TAB_HOV      = "#1D4EBE"

_BTN_SEC = dict(fg_color=C_CARD, border_color=C_ACCENT2, border_width=2,
                text_color=C_ACCENT2, hover_color="#EEF2FA")

# Font fallback chain: Windows 11 -> Windows 10 -> macOS -> generic
_DISPLAY_FAMILIES = ("Segoe UI Variable Display", "Segoe UI", "SF Pro Display", "Helvetica Neue", "Helvetica")
_TEXT_FAMILIES    = ("Segoe UI Variable Text",    "Segoe UI", "SF Pro Text",    "Helvetica Neue", "Helvetica")
_MONO_FAMILIES    = ("Consolas", "Menlo", "Monaco", "Courier New", "Courier")

_FAMILY_CACHE: dict[tuple, str] = {}


def _pick_family(candidates: tuple[str, ...]) -> str:
    """First family from `candidates` that Tk reports as installed; falls back to the last entry."""
    cached = _FAMILY_CACHE.get(candidates)
    if cached is not None:
        return cached
    try:
        from tkinter import font as tkfont
        available = set(tkfont.families())
    except Exception:
        available = set()
    chosen = next((c for c in candidates if c in available), candidates[-1])
    _FAMILY_CACHE[candidates] = chosen
    return chosen


def _f(size: int, weight: str = "normal", mono: bool = False) -> ctk.CTkFont:
    if mono:
        family = _pick_family(_MONO_FAMILIES)
    elif size >= 14:
        family = _pick_family(_DISPLAY_FAMILIES)
    else:
        family = _pick_family(_TEXT_FAMILIES)
    return ctk.CTkFont(family=family, size=size, weight=weight)


PRESETS = [
    ("Small",    "500K", "Studio",        500_000),
    ("Standard", "1M",   "4-6 rooms",   1_000_000),
    ("Large",    "4M",   "Multi-storey", 4_000_000),
    ("Huge",     "6M",   "Estate",       6_000_000),
]
_TABS = ["Convert", "Validate", "Logs"]

# Max custom points - matches Brush's 10M splat ceiling per CLAUDE.md
MAX_CUSTOM_POINTS = 10_000_000


def resource_path(rel: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", None) or Path(__file__).parent) / rel


def _fmt_pts(n: int) -> str:
    return f"{n:,}"


def _parse_pts(s: str) -> int:
    return int(s.replace(",", "").strip())


def _open_path(p: Path) -> None:
    """Open a folder or file in the host OS's file manager."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # noqa: SIM115
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", str(p)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(p)])
    except Exception as exc:
        print(f"Could not open path {p}: {exc}")


def _set_app_icon(window: tk.Misc) -> None:
    """Cross-platform window icon setter.

    Uses the platform-native bundled icon when available (.ico on Windows,
    .icns on macOS — both derived from assets/Final_Icon.png at build time)
    and falls back to assets/Final_Icon.png loaded as a Tk PhotoImage.
    """
    try:
        if sys.platform.startswith("win"):
            ico = resource_path("assets/app_icon.ico")
            if ico.exists():
                window.iconbitmap(str(ico))
                return
        # Non-Windows runtime (and Win fallback): load Final_Icon.png directly.
        # Earlier versions fell back to favicon-light/dark-512.png; those are
        # the auto-generated wordmark and don't match the polished app icon.
        png = resource_path("assets/Final_Icon.png")
        if not png.exists():
            png = resource_path("assets/favicon-light-512.png")
        if png.exists():
            img = tk.PhotoImage(file=str(png))
            window.iconphoto(True, img)
            window._icon_ref = img
    except Exception:
        pass


# ---- Tooltip ---------------------------------------------------------------
class _Tip:
    def __init__(self, widget: tk.Widget, text: str):
        self._w = widget
        self._text = text
        self._win = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _=None):
        if self._win:
            return
        try:
            x = self._w.winfo_rootx() + 16
            y = self._w.winfo_rooty() + self._w.winfo_height() + 4
        except tk.TclError:
            return
        self._win = tw = tk.Toplevel(self._w)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        tk.Label(tw, text=self._text, background=C_NAVY, foreground="white",
                 font=(_pick_family(_TEXT_FAMILIES), 10), relief="flat",
                 padx=10, pady=7, wraplength=320, justify="left").pack()

    def _hide(self, _=None):
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


def _qmark(parent, tip_text: str) -> ctk.CTkLabel:
    lbl = ctk.CTkLabel(parent, text=" ? ", font=_f(9, "bold"),
                       text_color="white", fg_color=C_ACCENT2,
                       corner_radius=8, width=20, height=18, cursor="question_arrow")
    _Tip(lbl, tip_text)
    return lbl


# ---- CombinedDropZone -------------------------------------------------------
class CombinedDropZone(ctk.CTkFrame):
    """Two-slot drop target. `role` controls which row accepts files vs folders.

    role="convert" (default):
        Row 1: E57 file (.e57)        -> var_e57
        Row 2: Panoramas folder (dir) -> var_images
    role="validate":
        Row 1: Colmap folder (dir)    -> var_images
        Row 2: E57 file (.e57)        -> var_e57

    In both roles, var_e57 always stores the .e57 file path and
    var_images always stores the directory path. Only the visual order
    and labels change.
    """

    def __init__(self, parent, on_e57_change=None, on_images_change=None,
                 has_dnd: bool = False, role: str = "convert", **kw):
        super().__init__(parent, fg_color=C_CARD, corner_radius=10,
                         border_color=C_BORDER, border_width=2, **kw)
        self._on_e57    = on_e57_change
        self._on_images = on_images_change
        self._has_dnd   = has_dnd
        self._role      = role
        self.var_e57    = tk.StringVar()    # always the .e57 file path
        self.var_images = tk.StringVar()    # always the directory path
        self._build()
        if has_dnd:
            self._register_dnd()

    # ---- build ----
    def _build(self):
        self.grid_columnconfigure(0, weight=1)

        if self._role == "validate":
            empty_msg = "Drop your Colmap folder and E57 file here"
            row_a_role = "dir"   # top
            row_b_role = "file"  # bottom
        else:
            empty_msg = "Drop your E57 file and panoramas folder here"
            row_a_role = "file"
            row_b_role = "dir"

        # Empty state
        self._empty = ctk.CTkFrame(self, fg_color="transparent")
        self._empty.grid(row=0, column=0, sticky="ew")
        self._empty.grid_columnconfigure(0, weight=1)

        icon = "*" if not self._has_dnd else "."
        empty_top = "Click Browse to select your files" if not self._has_dnd else empty_msg
        empty_sub = "or use the buttons below" if not self._has_dnd else "or browse to select each"

        ctk.CTkLabel(self._empty, text=icon, font=_f(26), text_color=C_HINT,
                     fg_color="transparent").grid(row=0, column=0, pady=(14, 2))
        ctk.CTkLabel(self._empty, text=empty_top, font=_f(13), text_color=C_DIM,
                     fg_color="transparent").grid(row=1, column=0, pady=(0, 2))
        ctk.CTkLabel(self._empty, text=empty_sub, font=_f(10), text_color=C_HINT,
                     fg_color="transparent").grid(row=2, column=0, pady=(0, 10))

        btns = ctk.CTkFrame(self._empty, fg_color="transparent")
        btns.grid(row=3, column=0, pady=(0, 14))
        if self._role == "validate":
            ctk.CTkButton(btns, text="Browse Colmap folder...", width=160, height=28,
                          **_BTN_SEC, font=_f(11), corner_radius=6,
                          command=self._browse_images).pack(side="left", padx=6)
            ctk.CTkButton(btns, text="Browse E57...", width=120, height=28,
                          **_BTN_SEC, font=_f(11), corner_radius=6,
                          command=self._browse_e57).pack(side="left", padx=6)
        else:
            ctk.CTkButton(btns, text="Browse E57...", width=110, height=28,
                          **_BTN_SEC, font=_f(11), corner_radius=6,
                          command=self._browse_e57).pack(side="left", padx=6)
            ctk.CTkButton(btns, text="Browse Folder...", width=120, height=28,
                          **_BTN_SEC, font=_f(11), corner_radius=6,
                          command=self._browse_images).pack(side="left", padx=6)

        # Loaded state - rows
        self._loaded = ctk.CTkFrame(self, fg_color="transparent")
        self._loaded.grid_columnconfigure(1, weight=1)

        # Row A (top)
        a_label = "Drop Colmap folder here  -  or Browse" if row_a_role == "dir" \
                  else "Drop E57 file here  -  or Browse"
        a_icon = "[Dir]" if row_a_role == "dir" else "[E57]"
        self._row_a_icon = ctk.CTkLabel(self._loaded, text=a_icon, font=_f(11, "bold"),
                                        text_color=C_HINT, fg_color="transparent", width=44)
        self._row_a_icon.grid(row=0, column=0, padx=(12, 0), pady=(10, 1), sticky="w")
        self._row_a_name = ctk.CTkLabel(self._loaded, text=a_label, font=_f(12),
                                        text_color=C_DIM, fg_color="transparent", anchor="w")
        self._row_a_name.grid(row=0, column=1, sticky="w", padx=(6, 4), pady=(10, 0))
        self._row_a_meta = ctk.CTkLabel(self._loaded, text="", font=_f(10),
                                        text_color=C_HINT, fg_color="transparent", anchor="w")
        self._row_a_meta.grid(row=1, column=1, sticky="w", padx=(6, 4), pady=(0, 4))
        cmd_a = self._browse_images if row_a_role == "dir" else self._browse_e57
        self._row_a_btn = ctk.CTkButton(self._loaded, text="Browse", width=72, height=26,
                                        **_BTN_SEC, font=_f(10), corner_radius=6, command=cmd_a)
        self._row_a_btn.grid(row=0, column=2, rowspan=2, padx=(4, 12), pady=8)

        ctk.CTkFrame(self._loaded, fg_color=C_BORDER, height=1).grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=12)

        # Row B (bottom)
        b_label = "Drop Colmap folder here  -  or Browse" if row_b_role == "dir" \
                  else "Drop E57 file here  -  or Browse"
        b_icon = "[Dir]" if row_b_role == "dir" else "[E57]"
        self._row_b_icon = ctk.CTkLabel(self._loaded, text=b_icon, font=_f(11, "bold"),
                                        text_color=C_HINT, fg_color="transparent", width=44)
        self._row_b_icon.grid(row=3, column=0, padx=(12, 0), pady=(4, 10), sticky="w")
        self._row_b_name = ctk.CTkLabel(self._loaded, text=b_label, font=_f(12),
                                        text_color=C_DIM, fg_color="transparent", anchor="w")
        self._row_b_name.grid(row=3, column=1, sticky="w", padx=(6, 4), pady=(4, 0))
        self._row_b_meta = ctk.CTkLabel(self._loaded, text="", font=_f(10),
                                        text_color=C_HINT, fg_color="transparent", anchor="w")
        self._row_b_meta.grid(row=4, column=1, sticky="w", padx=(6, 4), pady=(0, 10))
        cmd_b = self._browse_images if row_b_role == "dir" else self._browse_e57
        self._row_b_btn = ctk.CTkButton(self._loaded, text="Browse", width=72, height=26,
                                        **_BTN_SEC, font=_f(10), corner_radius=6, command=cmd_b)
        self._row_b_btn.grid(row=3, column=2, rowspan=2, padx=(4, 12), pady=8)

        # Bind row widgets to their roles so we can update them generically
        if row_a_role == "file":
            self._file_row = (self._row_a_icon, self._row_a_name, self._row_a_meta, self._row_a_btn)
            self._dir_row  = (self._row_b_icon, self._row_b_name, self._row_b_meta, self._row_b_btn)
        else:
            self._dir_row  = (self._row_a_icon, self._row_a_name, self._row_a_meta, self._row_a_btn)
            self._file_row = (self._row_b_icon, self._row_b_name, self._row_b_meta, self._row_b_btn)

        self._update_view()

    def _update_view(self):
        has_any = self.var_e57.get() or self.var_images.get()
        if has_any:
            self._empty.grid_remove()
            self._loaded.grid(row=0, column=0, sticky="ew")
        else:
            self._loaded.grid_remove()
            self._empty.grid(row=0, column=0, sticky="ew")

    # ---- browse ----
    def _browse_e57(self):
        p = filedialog.askopenfilename(title="Select E57 file",
                                       filetypes=[("E57", "*.e57"), ("All", "*.*")])
        if p:
            self._set_e57(p)

    def _browse_images(self):
        title = "Select Colmap folder" if self._role == "validate" else "Select panoramas folder"
        p = filedialog.askdirectory(title=title)
        if p:
            self._set_images(p)

    # ---- setters ----
    def _set_e57(self, path: str):
        path = path.strip().strip("{}")
        self.var_e57.set(path)
        icon, name, meta, btn = self._file_row
        name.configure(text=Path(path).name, text_color=C_TEXT, font=_f(12, "bold"))
        meta.configure(text="Reading...", text_color=C_HINT)
        icon.configure(text_color=C_ACCENT2)
        btn.configure(text="Change")
        self.configure(border_color=C_ACCENT2)
        self._update_view()
        if self._on_e57:
            self._on_e57(path)
        threading.Thread(target=self._detect_e57, args=(path,), daemon=True).start()

    def _set_images(self, path: str):
        path = path.strip().strip("{}")
        self.var_images.set(path)
        icon, name, meta, btn = self._dir_row
        name.configure(text=Path(path).name, text_color=C_TEXT, font=_f(12, "bold"))
        meta.configure(text="Counting..." if self._role == "convert" else "Reading folder...",
                       text_color=C_HINT)
        icon.configure(text_color=C_ACCENT2)
        btn.configure(text="Change")
        self.configure(border_color=C_ACCENT2)
        self._update_view()
        if self._on_images:
            self._on_images(path)
        threading.Thread(target=self._detect_images, args=(path,), daemon=True).start()

    def _safe_update(self, fn):
        _safe_schedule(self, 0, fn)

    def _detect_e57(self, path):
        try:
            mb = os.path.getsize(path) / 1e6
            sz = f"{mb/1000:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB"
            try:
                n = get_e57_scan_count(path)
                info = f"{sz}  -  {n} scans  OK"
            except Exception as exc:
                info = f"{sz}  -  could not read scan count ({exc.__class__.__name__})"
            _, _, meta, _ = self._file_row
            self._safe_update(lambda i=info: meta.configure(text=i, text_color=C_SUCCESS))
        except Exception:
            _, _, meta, _ = self._file_row
            self._safe_update(lambda: meta.configure(text="Loaded OK", text_color=C_SUCCESS))

    def _detect_images(self, path):
        _, _, meta, _ = self._dir_row
        try:
            if self._role == "validate":
                # For validate, just verify the folder looks like a Colmap dir
                p = Path(path)
                has_cameras = (p / "cameras.txt").exists()
                has_images  = (p / "images.txt").exists()
                if has_cameras and has_images:
                    self._safe_update(lambda: meta.configure(
                        text="cameras.txt + images.txt found  OK", text_color=C_SUCCESS))
                else:
                    missing = [n for n in ("cameras.txt", "images.txt") if not (p / n).exists()]
                    self._safe_update(lambda m=missing: meta.configure(
                        text=f"Missing: {', '.join(m)}", text_color=C_WARN))
                return
            n = sum(1 for f in Path(path).iterdir()
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png"))
            if n == 0:
                self._safe_update(lambda: meta.configure(
                    text="0 panorama images — check folder", text_color=C_WARN))
            else:
                self._safe_update(lambda i=n: meta.configure(
                    text=f"{i} panorama images  OK", text_color=C_SUCCESS))
        except Exception:
            self._safe_update(lambda: meta.configure(text="Loaded OK", text_color=C_SUCCESS))

    # ---- drag-and-drop ----
    def _register_dnd(self):
        try:
            self.drop_target_register(_DND_FILES)
            self.dnd_bind("<<Drop>>",      self._dnd_drop)
            self.dnd_bind("<<DragEnter>>", lambda e: self.configure(border_color=C_ACCENT))
            self.dnd_bind("<<DragLeave>>", lambda e: self.configure(
                border_color=C_ACCENT2 if (self.var_e57.get() or self.var_images.get())
                else C_BORDER))
        except Exception:
            pass

    def _parse_dnd_paths(self, data: str) -> list[str]:
        """Use Tk's list-splitter to handle paths-with-spaces and multi-file drops."""
        try:
            parts = self.tk.splitlist(data)
        except Exception:
            parts = data.split()
        return [p.strip().strip("{}") for p in parts if p.strip()]

    def _dnd_drop(self, event):
        paths = self._parse_dnd_paths(event.data)
        for raw in paths:
            p = Path(raw)
            if p.suffix.lower() == ".e57":
                self._set_e57(raw)
            elif p.is_dir():
                self._set_images(raw)
            else:
                print(f"Ignored dropped item (not .e57 or folder): {raw}")

    # ---- accessors ----
    def get_e57(self) -> str:    return self.var_e57.get()
    def get_images(self) -> str: return self.var_images.get()

    def set_e57(self, path: str) -> None:    self._set_e57(path)
    def set_images(self, path: str) -> None: self._set_images(path)


# ---- PresetTile ------------------------------------------------------------
class PresetTile(ctk.CTkFrame):
    def __init__(self, parent, name: str, pts_label: str, desc: str,
                 var: tk.StringVar, **kw):
        super().__init__(parent, fg_color=C_CARD, corner_radius=10,
                         border_color=C_BORDER, border_width=2, cursor="hand2", **kw)
        self._var = var
        self._value = name
        self.grid_columnconfigure(0, weight=1)
        self._pts_lbl = ctk.CTkLabel(self, text=pts_label, font=_f(16, "bold"),
                                     text_color=C_NAVY, fg_color="transparent")
        self._pts_lbl.grid(row=0, column=0, pady=(10, 1), padx=8)
        ctk.CTkLabel(self, text=name, font=_f(11, "bold"),
                     text_color=C_TEXT, fg_color="transparent").grid(row=1, column=0, pady=(0, 1))
        ctk.CTkLabel(self, text=desc, font=_f(9),
                     text_color=C_DIM, fg_color="transparent").grid(row=2, column=0, pady=(0, 10))
        self._chk = ctk.CTkLabel(self, text=" v ", font=_f(9, "bold"),
                                 text_color="white", fg_color=C_ACCENT2,
                                 corner_radius=8, width=22, height=16)
        var.trace_add("write", self._refresh)
        self._refresh()
        for w in [self] + list(self.winfo_children()):
            w.bind("<Button-1>", self._select)

    def _select(self, *_):
        self._var.set(self._value)

    def _refresh(self, *_):
        sel = self._var.get() == self._value
        self.configure(border_color=C_ACCENT2 if sel else C_BORDER)
        self._pts_lbl.configure(text_color=C_ACCENT2 if sel else C_NAVY)
        if sel:
            self._chk.place(relx=1.0, rely=0.0, anchor="ne", x=-5, y=5)
        else:
            self._chk.place_forget()


# ---- ConvertingVideo -------------------------------------------------------
class ConvertingVideo(ctk.CTkFrame):
    """Looping point-cloud video viewport, shown while a conversion runs.

    Locked to the source asset's native pixel size (default 800x300) so
    widening the parent column adds horizontal margin rather than
    upscaling the frames -- upscaling makes the wave look pixelated and
    dominate the layout. Progress isn't indicated here; the CONVERT
    button text + phase label below the viewport carry that.

    Frames are pre-rasterised to PhotoImage once at construction; the
    animation loop is just a Canvas itemconfig swap at ~15 fps.
    """

    _FRAME_MS = 33  # ~30 fps to match the source decimation

    def __init__(self, master, *, anim_rel: str,
                 nominal_w: int = 800, nominal_h: int = 300, **kw):
        # Let the Canvas's explicit pixel size drive the frame's natural
        # size -- passing width/height to CTkFrame applies DPI scaling
        # which then mismatches tk.Canvas's raw pixel sizing.
        # Border is C_ACCENT2 / 2 px so the viewport reads as an intentional
        # "section card" rather than dissolving into the page.
        super().__init__(master, fg_color=C_CARD,
                         border_color=C_BORDER, border_width=1,
                         corner_radius=8, **kw)

        self._idx = 0
        self._anim_id: str | None = None
        # PIL Images loaded once; PhotoImages rasterised lazily in start()
        # so the ~322 MB allocation only happens when a conversion actually runs.
        self._src_frames = self._load_frames(anim_rel)
        self._photos: list[ImageTk.PhotoImage] = []

        # Use _vc (not _canvas) to avoid shadowing CTkBaseClass's own
        # self._canvas, which handles border/corner-radius drawing.
        self._vc = tk.Canvas(self, highlightthickness=0, bd=0,
                             bg=C_CARD, width=nominal_w, height=nominal_h)
        self._vc.grid(row=0, column=0, padx=4, pady=4)
        self._img_item = self._vc.create_image(0, 0, anchor="nw")

    @staticmethod
    def _load_frames(anim_rel: str) -> list[Image.Image]:
        path = resource_path(anim_rel)
        if not path.exists():
            return []
        try:
            src = Image.open(path)
        except Exception:
            return []
        frames: list[Image.Image] = []
        i = 0
        try:
            while True:
                src.seek(i)
                frames.append(src.copy().convert("RGB"))
                i += 1
        except EOFError:
            pass
        return frames

    # ---- public API ---------------------------------------------------------
    def start(self) -> None:
        if self._anim_id is not None:
            return
        if not self._photos:
            if not self._src_frames:
                return
            # Rasterise frames on first start() — deferred from __init__ so
            # the ~322 MB footprint only appears while a conversion is running.
            self._photos = [ImageTk.PhotoImage(f) for f in self._src_frames]
        self._tick()

    def stop(self) -> None:
        if self._anim_id is not None:
            try:
                self.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None

    def _tick(self) -> None:
        if self._photos:
            self._idx = (self._idx + 1) % len(self._photos)
            self._vc.itemconfig(self._img_item, image=self._photos[self._idx])
        self._anim_id = self.after(self._FRAME_MS, self._tick)


# ---- ShimmerProgressBanner -------------------------------------------------
class ShimmerProgressBanner(tk.Canvas):
    """Drop-in replacement for the CONVERT button shown during conversion.

    Looks like the navy CONVERT button at rest, but the background fills
    cyan from left to right as progress advances. A brighter band
    continuously sweeps across the *filled* portion of the bar, reading
    as a "shimmer" effect -- closest tkinter analogue to the
    CSS background-clip:text shimmer the user referenced (text-shimmer
    on 21st.dev). Text "Converting... NN%" is overlaid centred.

    Public API:
      .set_progress(frac)  -- 0..1, advances the cyan fill width
      .set_text(s)         -- updates the centred label
      .start() / .stop()   -- shimmer animation loop
    """

    _TICK_MS = 40          # 25 fps shimmer sweep
    _CYCLE_PER_SEC = 0.8   # full sweeps per second (slower = calmer)
    _BAND_FRAC = 0.18      # shimmer band width as fraction of widget width
    _RADIUS = 8

    def __init__(self, parent, *, height: int = 46, **kw):
        # bg=C_BG matches the app background so the four corners of the
        # rounded-rect geometry (which are canvas-background coloured) blend
        # into the surrounding panel rather than showing a dark square.
        super().__init__(parent, height=height, bg=C_BG,
                         highlightthickness=0, bd=0, **kw)
        self._frac = 0.0
        self._text = "Converting... 0%"
        self._shimmer_t = 0.0
        self._shimmer_disabled = False
        self._anim_id: str | None = None
        self._font = _f(14, "bold")
        self._cw = self._ch = 0   # cached canvas dimensions from last Configure event
        # Deferred redraw: the layout hasn't fully settled when <Configure>
        # fires (especially on multi-monitor DPI changes), so winfo_width()
        # can return a stale value if called immediately.
        self.bind("<Configure>", lambda e: self.after(5, self._redraw))

    # ---- public API ---------------------------------------------------------
    def set_progress(self, frac: float) -> None:
        self._frac = max(0.0, min(1.0, float(frac)))
        self._draw_fg(self._cw, self._ch)

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw_fg(self._cw, self._ch)

    def nudge_to_converting(self) -> None:
        """Switch 'Starting...' placeholder to 'Converting... 0%' only if no
        real progress callback has arrived yet (frac still at 0)."""
        if self._frac == 0.0 and not self._shimmer_disabled:
            self.set_text("Converting... 0%")

    def set_success(self, text: str = "Done") -> None:
        """Show 100 %-filled cyan + still text (no shimmer) for the linger
        moment between conversion-finished and the swap back to CTkButton."""
        self.stop()
        self._frac = 1.0
        self._text = text
        self._shimmer_disabled = True
        self._draw_fg(self._cw, self._ch)

    def start(self) -> None:
        self._shimmer_disabled = False
        if self._anim_id is None:
            self._tick()

    def stop(self) -> None:
        if self._anim_id is not None:
            try:
                self.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None

    # ---- internals ----------------------------------------------------------
    def _tick(self) -> None:
        # Advance shimmer; cycle to slightly past 1.0 so the band exits the
        # right edge fully before wrapping (no abrupt jump).
        step = self._CYCLE_PER_SEC * (self._TICK_MS / 1000.0)
        self._shimmer_t = (self._shimmer_t + step) % (1.0 + self._BAND_FRAC)
        self._draw_fg(self._cw, self._ch)
        self._anim_id = self.after(self._TICK_MS, self._tick)

    def _rrect(self, x1, y1, x2, y2, r, fill):
        """Rounded rectangle approximated with 4 corner ovals + 2 rects."""
        if x2 - x1 < 2 * r or y2 - y1 < 2 * r:
            self.create_rectangle(x1, y1, x2, y2, fill=fill, outline="")
            return
        self.create_oval(x1, y1, x1 + 2 * r, y1 + 2 * r, fill=fill, outline="")
        self.create_oval(x2 - 2 * r, y1, x2, y1 + 2 * r, fill=fill, outline="")
        self.create_oval(x1, y2 - 2 * r, x1 + 2 * r, y2, fill=fill, outline="")
        self.create_oval(x2 - 2 * r, y2 - 2 * r, x2, y2, fill=fill, outline="")
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
        self.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="")

    def _rrect_left(self, x1, y1, x2, y2, r, fill):
        """Rounded left corners + sharp right edge -- used by the cyan fill."""
        if x2 - x1 < r:
            self.create_rectangle(x1, y1, x2, y2, fill=fill, outline="")
            return
        if y2 - y1 < 2 * r:
            self.create_rectangle(x1, y1, x2, y2, fill=fill, outline="")
            return
        self.create_oval(x1, y1, x1 + 2 * r, y1 + 2 * r, fill=fill, outline="")
        self.create_oval(x1, y2 - 2 * r, x1 + 2 * r, y2, fill=fill, outline="")
        self.create_rectangle(x1 + r, y1, x2, y2, fill=fill, outline="")
        self.create_rectangle(x1, y1 + r, x1 + r, y2 - r, fill=fill, outline="")

    @staticmethod
    def _mix(base_hex: str, target_hex: str, t: float) -> str:
        """Linear blend two #RRGGBB colours, t in 0..1."""
        b = (int(base_hex[1:3], 16), int(base_hex[3:5], 16), int(base_hex[5:7], 16))
        tgt = (int(target_hex[1:3], 16), int(target_hex[3:5], 16),
               int(target_hex[5:7], 16))
        out = tuple(int(b[i] + (tgt[i] - b[i]) * t) for i in range(3))
        return f"#{out[0]:02x}{out[1]:02x}{out[2]:02x}"

    def _draw_shimmer(self, x_start: int, band_w: int, h: int, max_x: int):
        """Vertical lines forming a soft-edged bright band, clipped to max_x.

        Cosine-squared falloff (vs the old triangular falloff) hides the
        band edges; the peak only whitens the cyan by ~25 % so brand
        colour stays recognisable and the white text contrast holds.
        """
        if band_w <= 1 or self._shimmer_disabled:
            return
        center = band_w / 2.0
        for i in range(band_w):
            xi = x_start + i
            if xi < 0 or xi >= max_x:
                continue
            d = min(1.0, abs(i - center) / center) if center > 0 else 1.0
            # cos^2 profile: 1 at centre, 0 at edges, smooth derivative.
            b = math.cos(d * math.pi / 2.0) ** 2
            colour = self._mix(C_ACCENT, "#FFFFFF", 0.25 * b)
            self.create_line(xi, 0, xi, h, fill=colour)

    def _redraw(self):
        """Full redraw — called on Configure (resize / DPI change).

        Draws the static navy track tagged '_bg', then calls _draw_fg for
        the dynamic fill/shimmer/text.  Caches canvas dimensions so _tick
        can call _draw_fg without an extra winfo query per frame.
        """
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            self._cw = self._ch = 0
            return
        self._cw, self._ch = w, h
        # Navy track is static — draw it once and tag so _draw_fg can
        # leave it in place and only replace the dynamic layer.
        self._rrect(0, 0, w, h, self._RADIUS, C_NAVY)
        for item in self.find_all():
            self.addtag_withtag("_bg", item)
        self._draw_fg(w, h)

    def _draw_fg(self, w: int, h: int) -> None:
        """Redraw only the fill, shimmer and text over the static navy bg.

        Called every tick (~25 fps) so avoids touching the '_bg' items.
        Also called directly from set_progress / set_text / set_success
        for instant state-change redraws without touching the track.
        """
        if w <= 1 or h <= 1:
            return
        self.delete("_fg")
        # Fill (cyan) -- 0..frac*w, rounded-left only
        fill_w = int(self._frac * w)
        if fill_w > 0:
            self._rrect_left(0, 0, fill_w, h, self._RADIUS, C_ACCENT)
            if not self._shimmer_disabled:
                # Shimmer band at current phase AND one cycle behind so the
                # band wraps smoothly with no visible gap.
                band_w = max(40, int(self._BAND_FRAC * w))
                cycle_w = w + band_w
                x = int(self._shimmer_t * cycle_w) - band_w
                self._draw_shimmer(x, band_w, h, fill_w)
                self._draw_shimmer(x - cycle_w, band_w, h, fill_w)
        # Text: 1 px navy shadow + white overlay for legibility over shimmer.
        cx, cy = w // 2, h // 2
        self.create_text(cx + 1, cy + 1, text=self._text, fill=C_NAVY,
                         font=self._font)
        self.create_text(cx, cy, text=self._text, fill="white",
                         font=self._font)
        # Tag all newly drawn items as '_fg' so the next tick can delete
        # only them, leaving the '_bg' navy track untouched.
        self.addtag_withtag("_fg", "all")
        self.dtag("_bg", "_fg")   # remove '_fg' from the bg items


# ---- QueueStream -----------------------------------------------------------
class _QS:
    """Stdout/stderr redirect that filters tqdm in-place updates (lines with \\r
    but no \\n) so the log view doesn't flood with progress duplicates."""

    def __init__(self, q):
        self._q = q

    def write(self, t: str):
        if not t:
            return
        if "\r" in t and "\n" not in t:
            return
        cleaned = t.replace("\r", "")
        if cleaned.strip() or cleaned == "\n":
            self._q.put(("log", cleaned))

    def flush(self):
        pass


# ---- App -------------------------------------------------------------------
class See3DConverterApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("light")
        self.title(f"See3D - E57 Converter  v{APP_VERSION}")
        # Open tall enough that every idle control is visible without
        # scrolling AND the 800x300 native-resolution converting-video
        # viewport drops in cleanly below the CONVERT button when a job
        # starts. On smaller screens (13" MacBook, low-res Windows
        # laptops) clamp to the actual usable display height so the
        # window doesn't open partly off-screen -- viewport may then
        # scroll during conversion, but idle state still fits.
        desired_w, desired_h = 900, 1100
        try:
            screen_h = self.winfo_screenheight()
            chrome = 95 if sys.platform == "darwin" else 80  # menu/dock vs taskbar
            actual_h = min(desired_h, max(720, screen_h - chrome))
        except Exception:
            actual_h = desired_h
        self.geometry(f"{desired_w}x{actual_h}")
        self.minsize(820, 720)
        self.configure(fg_color=C_BG)
        _set_app_icon(self)

        self._has_dnd = False
        if HAS_DND and _tkdnd:
            try:
                if hasattr(sys, "_MEIPASS"):
                    # PyInstaller bundle: --add-data places tkdnd here.
                    # Pass the path explicitly so _require doesn't rely on
                    # __file__ which can resolve incorrectly in onefile mode.
                    _tkdnd.TkinterDnD._require(
                        self,
                        str(Path(sys._MEIPASS) / "tkinterdnd2" / "tkdnd"))
                else:
                    _tkdnd.TkinterDnD._require(self)
                self._has_dnd = True
            except Exception:
                pass

        self._q             = queue.Queue()
        self._converting    = False
        self._validating    = False
        self._cancel_event  = threading.Event()
        self._last_output   = ""           # set when conversion finishes
        self._ovr_imgs: list = []
        self._ovr_lbls: list[str] = []
        self._ovr_idx  = 0

        self.var_preset   = tk.StringVar(value="Standard")
        self.var_points   = tk.StringVar(value="1,000,000")
        self.var_4face    = tk.BooleanVar(value=True)
        self.var_validate = tk.BooleanVar(value=True)
        self.var_facesize = tk.StringVar(value="4,000")
        self.var_workers  = tk.StringVar(value="")
        self.var_seed     = tk.StringVar(value="0")
        self.var_preset.trace_add("write", self._on_preset_change)
        for v in (self.var_preset, self.var_points, self.var_4face):
            v.trace_add("write", self._refresh_summary)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._poll()

    # ---- close ----
    def _on_close(self):
        if self._converting or self._validating:
            if not messagebox.askyesno(
                "Quit?",
                "A job is running. Quit and abandon it?\n"
                "Half-written output files will be left on disk."):
                return
            self._cancel_event.set()
        self.destroy()

    def _safe_after(self, ms: int, fn):
        _safe_schedule(self, ms, fn)

    # ---- shell ----
    def _build_ui(self):
        self._build_header()
        self._build_tab_strip()

        self._frames: dict[str, ctk.CTkScrollableFrame] = {}
        for name in _TABS:
            f = ctk.CTkScrollableFrame(self, fg_color=C_BG,
                                       scrollbar_button_color=C_BORDER,
                                       scrollbar_button_hover_color=C_ACCENT2)
            f.grid_columnconfigure(0, weight=1)
            self._frames[name] = f

        self._build_convert(self._frames["Convert"])
        self._build_validate(self._frames["Validate"])
        self._build_log(self._frames["Logs"])
        self._switch("Convert")

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=0, height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20)
        try:
            raw = Image.open(resource_path("assets/See3D Vector.png"))
            logo = ctk.CTkImage(light_image=raw, dark_image=raw, size=(180, 50))
            ctk.CTkLabel(inner, image=logo, text="", fg_color="transparent").pack(
                side="left", pady=5)
        except Exception:
            ctk.CTkLabel(inner, text="See3D", font=_f(24, "bold"),
                         text_color=C_ACCENT2, fg_color="transparent").pack(side="left")

        ctk.CTkLabel(inner, text=f"v{APP_VERSION}", font=_f(10),
                     text_color=C_HINT, fg_color="transparent").pack(side="right", padx=4)

        ctk.CTkFrame(self, fg_color=C_BORDER, height=1, corner_radius=0).pack(fill="x")

    def _build_tab_strip(self):
        strip = ctk.CTkFrame(self, fg_color=_TAB_BG, corner_radius=0, height=40)
        strip.pack(fill="x")
        strip.pack_propagate(False)

        self._tab_btns: dict[str, ctk.CTkButton] = {}
        for name in _TABS:
            btn = ctk.CTkButton(
                strip, text=f"  {name}  ",
                font=_f(12), corner_radius=0, height=40,
                fg_color=_TAB_INACTIVE, hover_color=_TAB_HOV,
                text_color="white", border_width=0,
                command=lambda n=name: self._switch(n))
            btn.pack(side="left", padx=(0, 1))
            self._tab_btns[name] = btn

    def _switch(self, name: str):
        for n, btn in self._tab_btns.items():
            if n == name:
                btn.configure(fg_color=_TAB_ACTIVE, font=_f(12, "bold"))
            else:
                btn.configure(fg_color=_TAB_INACTIVE, font=_f(12))
        for f in self._frames.values():
            f.pack_forget()
        self._frames[name].pack(fill="both", expand=True, padx=12, pady=(6, 12))

    def _sec(self, parent, text: str, row: int, tip: str = "") -> int:
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=row, column=0, sticky="ew", padx=2, pady=(8, 2))
        ctk.CTkFrame(hdr, fg_color=C_ACCENT2, width=4, height=14,
                     corner_radius=2).pack(side="left")
        ctk.CTkLabel(hdr, text=text, font=_f(10, "bold"), text_color=C_ACCENT2,
                     fg_color="transparent").pack(side="left", padx=(7, 0))
        if tip:
            _qmark(hdr, tip).pack(side="left", padx=(7, 0))
        return row + 1

    # ---- Convert tab ----
    def _build_convert(self, tab: ctk.CTkScrollableFrame):
        r = 0

        r = self._sec(tab, "INPUT FILES", r)
        self._dropzone = CombinedDropZone(tab,
                                          on_e57_change=self._on_e57_change,
                                          on_images_change=self._on_images_change,
                                          has_dnd=self._has_dnd,
                                          role="convert")
        self._dropzone.grid(row=r, column=0, sticky="ew", padx=2, pady=(0, 4)); r += 1

        # Output folder
        out_row = ctk.CTkFrame(tab, fg_color=C_CARD, corner_radius=8,
                               border_color=C_BORDER, border_width=1)
        out_row.grid(row=r, column=0, sticky="ew", padx=2, pady=(0, 4)); r += 1
        out_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(out_row, text="[Out]", font=_f(11, "bold"), text_color=C_HINT,
                     fg_color="transparent", width=44).grid(row=0, column=0, padx=(12, 0), pady=8)
        self._out_lbl = ctk.CTkLabel(out_row, text="Output folder  (Colmap/ created inside)",
                                     font=_f(11), text_color=C_DIM,
                                     fg_color="transparent", anchor="w")
        self._out_lbl.grid(row=0, column=1, sticky="w", padx=(6, 4))
        self._out_var = tk.StringVar()
        ctk.CTkButton(out_row, text="Browse", width=72, height=28, **_BTN_SEC,
                      font=_f(11), corner_radius=6,
                      command=self._browse_output).grid(row=0, column=2, padx=(4, 12), pady=8)

        # Scene preset
        r = self._sec(tab, "SCENE PRESET", r,
                      tip="Controls how many LiDAR points are exported as the starting cloud. "
                          "More points = denser initial result but slower training.")
        tile_frame = ctk.CTkFrame(tab, fg_color="transparent")
        tile_frame.grid(row=r, column=0, sticky="ew", padx=2, pady=(0, 4)); r += 1
        tile_frame.grid_columnconfigure(tuple(range(5)), weight=1, uniform="tile")

        for i, (name, pts_lbl, desc, _) in enumerate(PRESETS):
            PresetTile(tile_frame, name, pts_lbl, desc, self.var_preset).grid(
                row=0, column=i, padx=(0, 4), pady=2, sticky="nsew")

        self._custom_tile = self._make_custom_tile(tile_frame)
        self._custom_tile.grid(row=0, column=4, padx=0, pady=2, sticky="nsew")
        self.var_preset.trace_add("write", self._refresh_custom)
        self._refresh_custom()

        # Options
        r = self._sec(tab, "OPTIONS", r)
        opt = ctk.CTkFrame(tab, fg_color=C_CARD, corner_radius=10,
                           border_color=C_BORDER, border_width=1)
        opt.grid(row=r, column=0, sticky="ew", padx=2, pady=(0, 4)); r += 1
        opt.grid_columnconfigure(0, weight=1)

        r1 = ctk.CTkFrame(opt, fg_color="transparent")
        r1.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 2))
        ctk.CTkCheckBox(r1, text="Exclude floor & ceiling",
                        variable=self.var_4face, checkmark_color="white",
                        fg_color=C_ACCENT2, hover_color="#0058B3",
                        text_color=C_TEXT, font=_f(12)).pack(side="left")
        _qmark(r1, "The M2's camera doesn't fully reach the poles - Realsee fills those areas "
                   "with generative AI that differs between scan positions, causing ghosting in "
                   "the splat. When ticked, floor & ceiling images are still rendered but saved "
                   "to a nadir_and_zenith/ folder alongside Colmap/ - not included in training. "
                   "Recommended for indoor scenes.").pack(side="left", padx=(8, 0))
        ctk.CTkLabel(opt, text="Recommended for indoor scenes (avoids generative-fill ghosting)",
                     text_color=C_DIM, font=_f(10), fg_color="transparent", anchor="w").grid(
            row=1, column=0, sticky="w", padx=(36, 14), pady=(0, 4))

        r2 = ctk.CTkFrame(opt, fg_color="transparent")
        r2.grid(row=2, column=0, sticky="ew", padx=14, pady=(2, 6))
        ctk.CTkCheckBox(r2, text="Run alignment validation after conversion",
                        variable=self.var_validate, checkmark_color="white",
                        fg_color=C_ACCENT2, hover_color="#0058B3",
                        text_color=C_TEXT, font=_f(12)).pack(side="left")
        _qmark(r2, "Re-projects LiDAR points through the camera poses and measures colour "
                   "alignment. A healthy M2 capture scores 5-9.").pack(side="left", padx=(8, 0))

        self._adv_open = False
        self._adv_toggle_btn = ctk.CTkButton(
            opt, text=">  Advanced settings", anchor="w",
            fg_color="transparent", hover_color=C_BG, text_color=C_DIM, font=_f(10),
            command=self._toggle_adv, height=24, corner_radius=4)
        self._adv_toggle_btn.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 6))

        self._adv_inner = ctk.CTkFrame(opt, fg_color=C_BG, corner_radius=8)
        self._adv_inner.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 8))
        self._adv_inner.grid_remove()
        self._adv_inner.grid_columnconfigure((0, 1, 2), weight=1)

        for i, (lbl, var, tip) in enumerate([
            ("Face Size (px)", self.var_facesize,
             "Cubemap face resolution. 4,000 matches the M2's native resolution. "
             "Lower values reduce VRAM and training time but degrade splat detail."),
            ("Workers", self.var_workers,
             "CPU cores for cubemap rendering. Blank or 0 = automatic "
             "(capped at 8 to limit RAM). Set to 1 if you see multiprocessing errors."),
            ("Random Seed", self.var_seed,
             "Controls which LiDAR points are subsampled. Same seed = same points "
             "every run. 0 is fine."),
        ]):
            cell = ctk.CTkFrame(self._adv_inner, fg_color="transparent")
            cell.grid(row=0, column=i, sticky="nsew", padx=10, pady=(8, 10))
            cell.grid_columnconfigure(0, weight=1)
            lbl_row = ctk.CTkFrame(cell, fg_color="transparent")
            lbl_row.grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(lbl_row, text=lbl, text_color=C_TEXT,
                         font=_f(10, "bold"), anchor="w").pack(side="left")
            _qmark(lbl_row, tip).pack(side="left", padx=(6, 0))
            ctk.CTkEntry(cell, textvariable=var, fg_color=C_CARD,
                         border_color=C_BORDER, border_width=1, text_color=C_TEXT,
                         height=26, font=_f(11)).grid(row=1, column=0, sticky="ew", pady=(3, 0))

        # Summary card
        self._summary_card = ctk.CTkFrame(tab, fg_color=C_CARD, corner_radius=8,
                                          border_color=C_BORDER, border_width=1)
        self._summary_card.grid(row=r, column=0, sticky="ew", padx=2, pady=(8, 0)); r += 1
        self._summary_card.grid_columnconfigure(0, weight=1)
        self._summary_lbl = ctk.CTkLabel(self._summary_card,
                                         text="Load an E57 file to see a summary.",
                                         font=_f(11), text_color=C_DIM,
                                         fg_color="transparent", anchor="w",
                                         justify="left", wraplength=820)
        self._summary_lbl.grid(row=0, column=0, sticky="ew", padx=14, pady=8)

        # Convert button + progress. Two widgets occupy the same grid slot
        # and we toggle which is visible:
        #   - _conv_btn:    standard CTkButton (idle / done / retry / error)
        #   - _conv_shim:   canvas-based banner with fill + shimmer (running)
        self._conv_btn = ctk.CTkButton(
            tab, text="CONVERT", height=46,
            fg_color=C_NAVY, hover_color=C_ACCENT2, text_color="white",
            font=_f(14, "bold"), corner_radius=8, command=self._start_conversion)
        self._conv_btn.grid(row=r, column=0, sticky="ew", padx=2, pady=(10, 0))

        self._conv_shim = ShimmerProgressBanner(tab, height=46)
        self._conv_shim.grid(row=r, column=0, sticky="ew", padx=2, pady=(10, 0))
        self._conv_shim.grid_remove()
        r += 1

        self._cancel_btn = ctk.CTkButton(
            tab, text="CANCEL", height=32,
            fg_color=C_CARD, hover_color="#FBE9EC", text_color=C_ERROR_SOFT,
            border_color=C_ERROR_SOFT, border_width=2,
            font=_f(11, "bold"), corner_radius=8, command=self._request_cancel)
        self._cancel_btn.grid(row=r, column=0, sticky="ew", padx=2, pady=(6, 0)); r += 1
        self._cancel_btn.grid_remove()

        self._convideo = ConvertingVideo(tab, anim_rel="assets/converting_loop.webp",
                                          nominal_w=800, nominal_h=300)
        self._convideo.grid(row=r, column=0, padx=2, pady=(16, 0)); r += 1
        self._convideo.grid_remove()

        # Phase caption beneath the viewport. Bumped from C_DIM/10 to
        # C_ACCENT2/11-bold so it competes with the 308 px viewport rather
        # than disappearing under it.
        self._stat_lbl = ctk.CTkLabel(tab, text="", fg_color="transparent",
                                      text_color=C_ACCENT2, font=_f(11, "bold"))
        self._stat_lbl.grid(row=r, column=0, pady=(12, 8)); r += 1

        # Post-conversion actions
        self._post_row = ctk.CTkFrame(tab, fg_color="transparent")
        self._post_row.grid(row=r, column=0, sticky="ew", padx=2, pady=(0, 8)); r += 1
        self._open_colmap_btn = ctk.CTkButton(
            self._post_row, text="Open Colmap folder", width=180, height=30, **_BTN_SEC,
            font=_f(11), corner_radius=6, command=self._open_output)
        self._open_colmap_btn.pack(side="left", padx=(0, 6))
        self._view_log_btn = ctk.CTkButton(
            self._post_row, text="View log", width=120, height=30, **_BTN_SEC,
            font=_f(11), corner_radius=6, command=lambda: self._switch("Logs"))
        self._view_log_btn.pack(side="left", padx=6)
        self._post_row.grid_remove()

    def _make_custom_tile(self, parent) -> ctk.CTkFrame:
        tile = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=10,
                            border_color=C_BORDER, border_width=2, cursor="hand2")
        tile.grid_columnconfigure(0, weight=1)
        self._cust_icon = ctk.CTkLabel(tile, text="...", font=_f(15, "bold"),
                                       text_color=C_HINT, fg_color="transparent")
        self._cust_icon.grid(row=0, column=0, pady=(10, 1))
        self._cust_title = ctk.CTkLabel(tile, text="Custom", font=_f(11, "bold"),
                                        text_color=C_TEXT, fg_color="transparent")
        self._cust_title.grid(row=1, column=0, pady=(0, 1))
        self._cust_hint = ctk.CTkLabel(tile, text="Up to 10M", font=_f(9),
                                       text_color=C_DIM, fg_color="transparent")
        self._cust_hint.grid(row=2, column=0, pady=(0, 10))
        self._cust_entry = ctk.CTkEntry(tile, textvariable=self.var_points,
                                        fg_color=C_CARD, border_color=C_BORDER, border_width=1,
                                        text_color=C_TEXT, height=24, width=86,
                                        font=_f(10), justify="center")
        self._cust_chk = ctk.CTkLabel(tile, text=" v ", font=_f(9, "bold"),
                                      text_color="white", fg_color=C_ACCENT2,
                                      corner_radius=8, width=22, height=16)

        # Bind every child (except the entry, which has its own click handling)
        # so a click anywhere on the tile body selects the Custom preset.
        def _select(_=None):
            self.var_preset.set("Custom")
        tile.bind("<Button-1>", _select)
        for child in (self._cust_icon, self._cust_title, self._cust_hint):
            child.bind("<Button-1>", _select)
        return tile

    def _refresh_custom(self, *_):
        sel = self.var_preset.get() == "Custom"
        self._custom_tile.configure(border_color=C_ACCENT2 if sel else C_BORDER)
        self._cust_icon.configure(text_color=C_ACCENT2 if sel else C_HINT)
        if sel:
            self._cust_hint.grid_remove()
            self._cust_entry.grid(row=2, column=0, pady=(0, 10), padx=8)
            self._cust_chk.place(relx=1.0, rely=0.0, anchor="ne", x=-5, y=5)
        else:
            self._cust_entry.grid_remove()
            self._cust_hint.grid()
            self._cust_chk.place_forget()

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        self._adv_toggle_btn.configure(
            text=("v" if self._adv_open else ">") + "  Advanced settings")
        if self._adv_open:
            self._adv_inner.grid()
        else:
            self._adv_inner.grid_remove()

    # ---- Validate tab ----
    def _build_validate(self, tab: ctk.CTkScrollableFrame):
        tab.grid_columnconfigure(0, weight=1)
        r = 0
        ctk.CTkLabel(tab,
                     text="Re-project LiDAR points through the generated camera poses to measure "
                          "alignment quality. A healthy Realsee M2 capture scores 5-9.",
                     text_color=C_DIM, font=_f(11), justify="left", anchor="w",
                     wraplength=820, fg_color="transparent").grid(
            row=r, column=0, sticky="ew", padx=4, pady=(8, 6)); r += 1

        self._val_drop = CombinedDropZone(tab, has_dnd=self._has_dnd, role="validate")
        self._val_drop.grid(row=r, column=0, sticky="ew", padx=2, pady=(0, 4)); r += 1

        br = ctk.CTkFrame(tab, fg_color="transparent")
        br.grid(row=r, column=0, sticky="ew", padx=4, pady=(10, 0)); r += 1
        br.grid_columnconfigure(1, weight=1)
        sb = ctk.CTkFrame(br, fg_color=C_BG, corner_radius=10, border_color=C_BORDER,
                          border_width=1, width=100, height=70)
        sb.grid(row=0, column=0)
        sb.pack_propagate(False)
        self._score_num = ctk.CTkLabel(sb, text="-", font=_f(28, "bold"),
                                       text_color=C_DIM, fg_color="transparent")
        self._score_num.pack(expand=True, pady=(6, 0))
        ctk.CTkLabel(sb, text="mean diff", font=_f(9),
                     text_color=C_HINT, fg_color="transparent").pack(pady=(0, 6))
        sf = ctk.CTkFrame(br, fg_color="transparent")
        sf.grid(row=0, column=1, padx=(12, 0), sticky="w")
        self._score_status = ctk.CTkLabel(sf, text="Not run yet", font=_f(14, "bold"),
                                          text_color=C_DIM, fg_color="transparent", anchor="w")
        self._score_status.pack(anchor="w")
        self._score_sub = ctk.CTkLabel(sf, text="Click Run Validation to check",
                                       font=_f(11), text_color=C_HINT,
                                       fg_color="transparent", anchor="w")
        self._score_sub.pack(anchor="w", pady=(2, 0))

        self._ovr_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self._ovr_frame.grid(row=r, column=0, sticky="ew", padx=2); r += 1
        nav = ctk.CTkFrame(self._ovr_frame, fg_color="transparent")
        nav.pack(fill="x")
        self._prev_btn = ctk.CTkButton(nav, text="<- Prev", width=80, height=26,
                                       **_BTN_SEC, font=_f(11), corner_radius=6,
                                       command=self._prev_ovr)
        self._prev_btn.pack(side="left")
        self._ovr_lbl = ctk.CTkLabel(nav, text="", fg_color="transparent",
                                     font=_f(11), text_color=C_DIM)
        self._ovr_lbl.pack(side="left", expand=True)
        self._next_btn = ctk.CTkButton(nav, text="Next ->", width=80, height=26,
                                       **_BTN_SEC, font=_f(11), corner_radius=6,
                                       command=self._next_ovr)
        self._next_btn.pack(side="right")
        self._ovr_img = ctk.CTkLabel(self._ovr_frame, text="", fg_color=C_BG, corner_radius=6)
        self._ovr_img.pack(fill="x", pady=(4, 0))
        self._ovr_frame.grid_remove()

        self._val_btn = ctk.CTkButton(
            tab, text="RUN VALIDATION", height=44,
            fg_color=C_NAVY, hover_color=C_ACCENT2, text_color="white",
            font=_f(13, "bold"), corner_radius=8, command=self._start_validation)
        self._val_btn.grid(row=r, column=0, sticky="ew", padx=2, pady=(12, 10)); r += 1

    # ---- Log tab ----
    def _build_log(self, tab: ctk.CTkScrollableFrame):
        tab.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(tab, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=4, pady=(8, 4))
        ctk.CTkLabel(hdr, text="Output log", font=_f(13, "bold"),
                     text_color=C_TEXT, fg_color="transparent").pack(side="left")
        ctk.CTkButton(hdr, text="Clear", width=56, height=26, **_BTN_SEC, font=_f(11),
                      corner_radius=6, command=self._clear_log).pack(side="right")
        self._log = ctk.CTkTextbox(tab, fg_color=C_LOG_BG, text_color=C_TEXT,
                                   font=_f(11, mono=True), height=520, corner_radius=8,
                                   wrap="word", border_color=C_BORDER, border_width=1)
        self._log.grid(row=1, column=0, sticky="nsew", padx=2, pady=(0, 8))
        self._log.configure(state="disabled")

    # ---- file callbacks ----
    def _on_e57_change(self, path: str):
        parent = Path(path).parent
        root = parent.parent if parent.name.lower() == "points" else parent
        _IMG_EXTS = {".jpg", ".jpeg", ".png"}
        _img_dir = root / "images"
        if (_img_dir.is_dir()
                and not self._dropzone.get_images()
                and any(f.suffix.lower() in _IMG_EXTS for f in _img_dir.iterdir())):
            self._dropzone.set_images(str(_img_dir))
        if not self._out_var.get():
            self._set_output(str(root / "Colmap"))
        # Cross-fill the Validate tab so the user doesn't re-enter paths.
        # Only point at Colmap/ when it actually exists, otherwise the slot
        # immediately reads "Missing: cameras.txt, images.txt" before the
        # conversion has even been run.
        if not self._val_drop.var_e57.get():
            self._val_drop.set_e57(path)
        colmap_dir = root / "Colmap"
        if colmap_dir.is_dir() and not self._val_drop.var_images.get():
            self._val_drop.set_images(str(colmap_dir))
        self._refresh_summary()

    def _on_images_change(self, path: str):
        self._refresh_summary()

    def _browse_output(self):
        p = filedialog.askdirectory(title="Select output folder")
        if p:
            self._set_output(p)

    def _set_output(self, path: str):
        self._out_var.set(path)
        self._out_lbl.configure(text=Path(path).name, text_color=C_TEXT, font=_f(12, "bold"))
        self._refresh_summary()

    def _open_output(self):
        if self._last_output and Path(self._last_output).exists():
            _open_path(Path(self._last_output))
        elif self._out_var.get():
            p = Path(self._out_var.get())
            if p.exists():
                _open_path(p)
            elif p.parent.exists():
                _open_path(p.parent)

    def _on_preset_change(self, *_):
        pts = next((p for n, _, __, p in PRESETS if n == self.var_preset.get()), None)
        if pts is not None:
            self.var_points.set(_fmt_pts(pts))

    def _refresh_summary(self, *_):
        e57 = self._dropzone.get_e57() if hasattr(self, "_dropzone") else ""
        imgs = self._dropzone.get_images() if hasattr(self, "_dropzone") else ""
        out = self._out_var.get() if hasattr(self, "_out_var") else ""
        if not e57 and not imgs:
            self._summary_lbl.configure(
                text="Load an E57 file to see a summary.", text_color=C_DIM)
            return
        bits = []
        if e57:
            bits.append(f"E57: {Path(e57).name}")
        if imgs:
            bits.append(f"Panoramas: {Path(imgs).name}")
        try:
            pts = _parse_pts(self.var_points.get())
            bits.append(f"Points: {_fmt_pts(pts)}")
        except Exception:
            pass
        bits.append("4-face" if self.var_4face.get() else "6-face")
        if out:
            bits.append(f"-> {out}")
        self._summary_lbl.configure(text="  -  ".join(bits), text_color=C_TEXT)

    # ---- log ----
    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _append_log(self, text: str):
        self._log.configure(state="normal")
        self._log.insert("end", text)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _poll(self):
        try:
            for _ in range(50):   # cap per cycle so a log burst can't block repaints
                kind, msg = self._q.get_nowait()
                if kind == "log":
                    self._append_log(msg)
                elif kind == "prog":
                    self._set_progress(msg)
                elif kind == "done":
                    self._on_done(True, msg)
                elif kind == "error":
                    self._on_done(False, msg)
                elif kind == "val_done":
                    self._on_val_done(msg)
        except queue.Empty:
            pass
        self._safe_after(80, self._poll)

    def _set_progress(self, payload):
        if isinstance(payload, tuple):
            frac, phase = payload
        else:
            frac, phase = float(payload), ""
        pct = int(max(0.0, min(1.0, float(frac))) * 100)
        self._conv_shim.set_progress(float(frac))
        self._conv_shim.set_text(f"Converting... {pct}%")
        self._stat_lbl.configure(text=phase, text_color=C_ACCENT2)

    # ---- conversion ----
    def _preflight(self, e57: str, images: str, output: str) -> str | None:
        """Return an error string, or None if inputs look OK."""
        e57p = Path(e57)
        imgp = Path(images)
        outp = Path(output)
        if not e57p.is_file():
            return f"E57 file not found:\n{e57p}"
        if not imgp.is_dir():
            return f"Panoramas folder not found:\n{imgp}"
        try:
            n_img = count_panoramas(imgp)
            if n_img == 0:
                return f"No JPG/PNG panoramas in:\n{imgp}"
            try:
                n_scans = get_e57_scan_count(e57p)
            except Exception as exc:
                return f"Could not read E57 scan count:\n{exc}"
            if n_img != n_scans:
                return (f"Panorama / scan count mismatch:\n"
                        f"  {n_img} panoramas in folder\n"
                        f"  {n_scans} scans in E57\n\n"
                        f"There must be exactly one panorama per scan.")
        except Exception as exc:
            return f"Pre-flight check failed:\n{exc}"
        try:
            import shutil
            # Walk up to the nearest existing ancestor for the disk-space probe.
            # Don't create the output dir here — pre-flight may still bail
            # out below and we'd leave a stray empty folder behind.
            check_at = outp
            while not check_at.exists() and check_at != check_at.parent:
                check_at = check_at.parent
            free_gb = shutil.disk_usage(check_at).free / 1e9
            est_gb = n_img * 0.05 + 1.0
            if free_gb < est_gb:
                return (f"Low disk space at {check_at}:\n"
                        f"  {free_gb:.1f} GB free, need ~{est_gb:.1f} GB.")
        except Exception:
            pass
        try:
            if outp.exists() and any(outp.iterdir()):
                if not messagebox.askyesno(
                    "Overwrite existing output?",
                    f"{outp} already contains files.\n"
                    "Continue and overwrite the conversion?"):
                    return "Cancelled by user."
        except PermissionError as exc:
            return f"Cannot read output folder (permission denied):\n{exc}"
        except OSError:
            pass  # non-existent or unreadable — conversion will create it
        return None

    def _start_conversion(self):
        if self._converting or self._validating:
            return
        e57    = self._dropzone.get_e57()
        images = self._dropzone.get_images()
        output = self._out_var.get().strip()
        for val, msg in [(e57, "Select an E57 file."),
                         (images, "Select the panoramas folder."),
                         (output, "Select an output folder.")]:
            if not val:
                messagebox.showwarning("Missing input", msg)
                return

        err = self._preflight(e57, images, output)
        if err:
            messagebox.showerror("Cannot start conversion", err)
            return

        try:
            face_size = _parse_pts(self.var_facesize.get()) if self.var_facesize.get() else 4000
            if not (256 <= face_size <= 16384):
                raise ValueError("Face size must be between 256 and 16,384.")
            workers_raw = self.var_workers.get().strip()
            workers = int(workers_raw) if workers_raw else None
            if workers is not None and workers < 0:
                raise ValueError("Workers must be 0 or a positive integer.")
            seed = int(self.var_seed.get()) if self.var_seed.get().strip() else 0
            max_pts_raw = self.var_points.get().strip()
            max_pts = _parse_pts(max_pts_raw) if max_pts_raw else 1_000_000
            if max_pts <= 0:
                max_pts = 1_000_000
            if max_pts > MAX_CUSTOM_POINTS:
                if not messagebox.askyesno(
                    "Points cloud above Brush ceiling",
                    f"You've set {max_pts:,} init points. Brush's known-stable "
                    f"ceiling is {MAX_CUSTOM_POINTS:,} (12M splat bug). "
                    "Continue anyway?"):
                    return
        except ValueError as exc:
            messagebox.showerror("Invalid advanced setting", str(exc))
            return

        # Snapshot BooleanVars on the main thread — tkinter vars are not
        # thread-safe; reading them inside the worker can corrupt Tcl state.
        include_nadir = not self.var_4face.get()
        run_validate  = self.var_validate.get()

        self._converting = True
        self._cancel_event.clear()
        # Swap CTkButton out for the shimmer banner while running.
        self._conv_btn.grid_remove()
        self._conv_shim.set_progress(0.0)
        self._conv_shim.set_text("Starting...")
        self._conv_shim.grid()
        self._conv_shim.start()
        self._cancel_btn.grid()
        self._post_row.grid_remove()
        # Stage the viewport reveal ~220 ms after the banner so the
        # idle->converting transition feels intentional rather than an
        # instant pop.  nudge_to_converting() switches the placeholder text
        # only if no real progress callback has arrived yet.
        def _stage2():
            if not self._converting:
                return
            self._convideo.grid()
            self._convideo.start()
            self._conv_shim.nudge_to_converting()
        self._safe_after(220, _stage2)
        self._stat_lbl.configure(text="Starting...", text_color=C_ACCENT2)
        self._clear_log()
        # Stay on the Convert tab so users watch the progress bar fill instead
        # of reading a log scroll — a determinate progress bar is a much better
        # waiting-time signal. Users can switch to Logs manually via the
        # "View log" button that appears once the run completes.
        self._last_output = output

        def _cb(frac, phase=""):
            self._q.put(("prog", (frac, phase)))

        threading.Thread(
            target=self._conv_worker,
            args=(e57, images, output, face_size, workers, max_pts, seed,
                  include_nadir, run_validate, _cb),
            daemon=True,
        ).start()

    def _request_cancel(self):
        if not self._converting:
            return
        self._cancel_event.set()
        self._cancel_btn.configure(state="disabled", text="Cancelling...")
        self._stat_lbl.configure(text="Cancelling...  (current step will finish first)",
                                 text_color=C_WARN)

    def _conv_worker(self, e57, images, output, face_size, workers, max_pts, seed,
                     include_nadir: bool, run_validate: bool, cb):
        # include_nadir and run_validate are snapshotted from BooleanVars on the
        # main thread before launch — tkinter vars are not thread-safe.
        import numpy as np
        qs = _QS(self._q)
        validation_failed_msg = None
        with contextlib.redirect_stdout(qs), contextlib.redirect_stderr(qs):
            try:
                run_conversion(
                    images_dir=Path(images), points_path=Path(e57),
                    colmap_dir=Path(output), face_size=face_size, yaw_offset_deg=0.0,
                    workers=workers, max_points=max_pts if max_pts > 0 else None,
                    camera_offset=np.zeros(3), seed=seed,
                    include_nadir_zenith=include_nadir,
                    progress_callback=cb,
                    cancel_event=self._cancel_event,
                )
                if run_validate and not self._cancel_event.is_set():
                    print("\n-- Alignment validation -----------------------------")
                    try:
                        val_score = validate_conversion(
                            colmap_dir=Path(output), e57_path=Path(e57))
                        if val_score is None:
                            validation_failed_msg = (
                                "validation produced no score (no valid projections)")
                    except Exception as v_exc:
                        import traceback
                        validation_failed_msg = str(v_exc)
                        print(f"\nValidation skipped: {v_exc}\n{traceback.format_exc()}")
                if self._cancel_event.is_set():
                    self._q.put(("error", "Cancelled by user"))
                else:
                    done_msg = "Conversion complete."
                    if validation_failed_msg:
                        done_msg += f" (validation skipped — {validation_failed_msg})"
                    self._q.put(("done", done_msg))
            except InterruptedError:
                self._q.put(("error", "Cancelled by user"))
            except Exception as exc:
                import traceback
                self._q.put(("log", f"\n--- ERROR ---\n{exc}\n{traceback.format_exc()}\n"))
                self._q.put(("error", str(exc)))

    def _on_done(self, success: bool, message: str):
        self._converting = False
        self._cancel_btn.grid_remove()
        self._cancel_btn.configure(state="normal", text="CANCEL")
        self._convideo.stop()
        self._convideo.grid_remove()
        if success:
            # Linger on a fully-filled cyan banner with "Done ✓" before
            # swapping back to the CTkButton -- a small celebratory beat
            # so the transition doesn't feel abrupt.
            self._conv_shim.set_success("Done ✓")
            self._stat_lbl.configure(text=f"Done - {message}", text_color=C_SUCCESS)
            self._post_row.grid()
            def _swap_back():
                self._conv_shim.grid_remove()
                self._conv_btn.grid()
                self._conv_btn.configure(state="normal", text="CONVERT AGAIN",
                                         fg_color=C_NAVY)
            self._safe_after(1500, _swap_back)
        else:
            # Failures swap back immediately -- no celebratory linger when
            # something broke.
            self._conv_shim.stop()
            self._conv_shim.grid_remove()
            self._conv_btn.grid()
            self._conv_btn.configure(state="normal", text="RETRY", fg_color=C_ERROR_SOFT)
            self._stat_lbl.configure(text=f"Failed - {message}", text_color=C_ERROR)
            self._post_row.grid_remove()

    # ---- validation ----
    def _start_validation(self):
        if self._validating or self._converting:
            return
        colmap = self._val_drop.var_images.get()   # validate role: dir slot = Colmap
        e57    = self._val_drop.var_e57.get()       # validate role: file slot = E57
        if not colmap:
            messagebox.showwarning("Missing input", "Select a Colmap folder.")
            return
        if not e57:
            messagebox.showwarning("Missing input", "Select the E57 file.")
            return
        cp = Path(colmap)
        if not (cp / "cameras.txt").is_file() or not (cp / "images.txt").is_file():
            messagebox.showerror("Invalid Colmap folder",
                                 f"{colmap} does not look like a Colmap folder "
                                 "(missing cameras.txt or images.txt).")
            return
        if not Path(e57).is_file():
            messagebox.showerror("E57 not found", f"{e57} not found.")
            return
        self._validating = True
        self._val_btn.configure(state="disabled", text="Validating...")
        self._score_num.configure(text="...", text_color=C_DIM)
        self._score_status.configure(text="Running...", text_color=C_ACCENT2)
        self._score_sub.configure(text="Scoring and generating visual overlays...",
                                  text_color=C_HINT)
        self._ovr_frame.grid_remove()
        self._clear_log()
        # Stay on the Validate tab so the user sees the live score/status
        # update rather than the log scroll. We switch to Logs only if the
        # validation actually fails (see _on_val_done).
        self._append_log("-- Alignment validation -----------------------------\n")
        threading.Thread(target=self._val_worker, args=(colmap, e57), daemon=True).start()

    def _val_worker(self, colmap: str, e57: str):
        qs = _QS(self._q)
        with contextlib.redirect_stdout(qs), contextlib.redirect_stderr(qs):
            try:
                score = validate_conversion(Path(colmap), Path(e57))
                overlays, labels = [], []
                try:
                    scans = get_available_scans(Path(colmap))
                    if scans:
                        picks = sorted({scans[0], scans[len(scans) // 2], scans[-1]})
                        for sid in picks:
                            print(f"Generating overlay for scan {sid}...")
                            img = generate_overlay(Path(colmap), Path(e57),
                                                   scan_id=sid, face="pz")
                            overlays.append(img)
                            labels.append(f"Scan {sid}  -  pz face")
                except Exception as oe:
                    print(f"Note: overlay generation skipped - {oe}")
                self._q.put(("val_done", (score, overlays, labels)))
            except Exception as exc:
                import traceback
                self._q.put(("log", f"\nValidation ERROR: {exc}\n{traceback.format_exc()}\n"))
                self._q.put(("val_done", (None, [], [])))

    def _on_val_done(self, payload):
        score, overlays, labels = payload
        self._validating = False
        self._val_btn.configure(state="normal", text="RUN VALIDATION")
        if score is None:
            self._score_num.configure(text="ERR", text_color=C_ERROR)
            self._score_status.configure(text="Validation failed", text_color=C_ERROR)
            self._score_sub.configure(text="See log for details", text_color=C_ERROR)
            self._switch("Logs")
            return
        # Narrower bands per CLAUDE.md: 5-9 GOOD, 10-11 MARGINAL, >=12 HIGH
        if score <= 9:
            self._score_num.configure(text=f"{score:.1f}", text_color=C_SUCCESS)
            self._score_status.configure(text="GOOD", text_color=C_SUCCESS)
            self._score_sub.configure(text="Healthy alignment  (target 5-9)",
                                      text_color=C_SUCCESS)
        elif score < 12:
            self._score_num.configure(text=f"{score:.1f}", text_color=C_WARN)
            self._score_status.configure(text="MARGINAL", text_color=C_WARN)
            self._score_sub.configure(
                text="Slightly high - check panorama count matches scan count",
                text_color=C_WARN)
        else:
            self._score_num.configure(text=f"{score:.1f}", text_color=C_ERROR)
            self._score_status.configure(text="HIGH", text_color=C_ERROR)
            self._score_sub.configure(
                text="Alignment issue - verify E57 matches images folder",
                text_color=C_ERROR)
        if overlays:
            self._ovr_imgs = overlays
            self._ovr_lbls = labels
            self._ovr_idx = 0
            self._show_ovr(self._ovr_idx)
            self._ovr_frame.grid()
        self._switch("Validate")

    def _show_ovr(self, idx: int):
        self._ovr_idx = idx
        img = self._ovr_imgs[idx]
        w = 780
        ci = ctk.CTkImage(light_image=img, dark_image=img,
                          size=(w, int(img.height * w / img.width)))
        self._ovr_img.configure(image=ci, text="")
        self._ovr_img._image = ci
        total = len(self._ovr_imgs)
        self._ovr_lbl.configure(text=f"{self._ovr_lbls[idx]}  ({idx + 1} / {total})")
        self._prev_btn.configure(state="normal" if idx > 0 else "disabled")
        self._next_btn.configure(state="normal" if idx < total - 1 else "disabled")

    def _prev_ovr(self):
        if self._ovr_idx > 0:
            self._show_ovr(self._ovr_idx - 1)

    def _next_ovr(self):
        if self._ovr_idx < len(self._ovr_imgs) - 1:
            self._show_ovr(self._ovr_idx + 1)


def _main():
    import multiprocessing
    multiprocessing.freeze_support()
    ctk.set_appearance_mode("light")
    app = See3DConverterApp()
    app.mainloop()


if __name__ == "__main__":
    _main()
