"""
See3D E57 Converter — GUI front-end for the Realsee Galois M2 → COLMAP pipeline.
"""
from __future__ import annotations
import multiprocessing, os, queue, sys, threading, tkinter as tk
from pathlib import Path
from tkinter import filedialog
import customtkinter as ctk
from PIL import Image

multiprocessing.freeze_support()

try:
    import tkinterdnd2 as _tkdnd
    _DND_FILES = _tkdnd.DND_FILES
    HAS_DND = True
except ImportError:
    _tkdnd = None; _DND_FILES = None; HAS_DND = False

# ── Palette ───────────────────────────────────────────────────────────────────
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

# Tab strip palette
_TAB_BG       = "#001B87"   # navy strip background
_TAB_ACTIVE   = "#00A6D7"   # selected tab fill (cyan)
_TAB_INACTIVE = "#163FA8"   # unselected tab fill (medium navy)
_TAB_HOV      = "#1D4EBE"   # hover on inactive

# Secondary (outlined) button style
_BTN_SEC = dict(fg_color=C_CARD, border_color=C_ACCENT2, border_width=2,
                text_color=C_ACCENT2, hover_color="#EEF2FA")

_FD, _FT, _FM = "Segoe UI Variable Display", "Segoe UI Variable Text", "Consolas"

def _f(size: int, weight: str = "normal", mono: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=_FM if mono else (_FD if size >= 14 else _FT),
                       size=size, weight=weight)

PRESETS = [
    ("Small",    "500K",  "Studio",        500_000),
    ("Standard", "1M",    "4–6 rooms",   1_000_000),
    ("Large",    "4M",    "Multi-storey", 4_000_000),
    ("Huge",     "6M",    "Estate",       6_000_000),
]
_TABS = ["Convert", "Validate", "Logs"]

def resource_path(rel: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", None) or Path(__file__).parent) / rel

def _fmt_pts(n: int) -> str: return f"{n:,}"
def _parse_pts(s: str) -> int: return int(s.replace(",", "").strip())


# ── Tooltip ───────────────────────────────────────────────────────────────────
class _Tip:
    def __init__(self, widget: tk.Widget, text: str):
        self._w = widget; self._text = text; self._win = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _=None):
        if self._win: return
        x = self._w.winfo_rootx() + 16
        y = self._w.winfo_rooty() + self._w.winfo_height() + 4
        self._win = tw = tk.Toplevel(self._w)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        tk.Label(tw, text=self._text, background=C_NAVY, foreground="white",
                 font=("Segoe UI Variable Text", 10), relief="flat",
                 padx=10, pady=7, wraplength=300, justify="left").pack()

    def _hide(self, _=None):
        if self._win: self._win.destroy(); self._win = None


def _qmark(parent, tip_text: str) -> ctk.CTkLabel:
    lbl = ctk.CTkLabel(parent, text=" ? ", font=_f(9, "bold"),
                        text_color="white", fg_color=C_ACCENT2,
                        corner_radius=8, width=20, height=18, cursor="question_arrow")
    _Tip(lbl, tip_text)
    return lbl


# ── CombinedDropZone ──────────────────────────────────────────────────────────
class CombinedDropZone(ctk.CTkFrame):
    """Single drop target. Empty state = unified prompt; after load = two status rows."""

    def __init__(self, parent, on_e57_change=None, on_images_change=None, **kw):
        super().__init__(parent, fg_color=C_CARD, corner_radius=10,
                         border_color=C_BORDER, border_width=2, **kw)
        self._on_e57    = on_e57_change
        self._on_images = on_images_change
        self.var_e57    = tk.StringVar()
        self.var_images = tk.StringVar()
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)

        # ── Empty / prompt state ─────────────────────────────────────────────
        self._empty = ctk.CTkFrame(self, fg_color="transparent")
        self._empty.grid(row=0, column=0, sticky="ew")
        self._empty.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self._empty, text="📦", font=_f(26), text_color=C_HINT,
                     fg_color="transparent").grid(row=0, column=0, pady=(14, 2))
        ctk.CTkLabel(self._empty,
                     text="Drop your E57 file and panoramas folder here",
                     font=_f(13), text_color=C_DIM,
                     fg_color="transparent").grid(row=1, column=0, pady=(0, 2))
        ctk.CTkLabel(self._empty, text="or browse to select each",
                     font=_f(10), text_color=C_HINT,
                     fg_color="transparent").grid(row=2, column=0, pady=(0, 10))

        btns = ctk.CTkFrame(self._empty, fg_color="transparent")
        btns.grid(row=3, column=0, pady=(0, 14))
        ctk.CTkButton(btns, text="Browse E57…", width=110, height=28,
                      **_BTN_SEC, font=_f(11), corner_radius=6,
                      command=self._browse_e57).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Browse Folder…", width=120, height=28,
                      **_BTN_SEC, font=_f(11), corner_radius=6,
                      command=self._browse_images).pack(side="left", padx=6)

        # ── Loaded state — two compact rows ──────────────────────────────────
        self._loaded = ctk.CTkFrame(self, fg_color="transparent")
        self._loaded.grid_columnconfigure(1, weight=1)

        # E57 row
        self._e57_icon = ctk.CTkLabel(self._loaded, text="📄", font=_f(15),
                                       text_color=C_HINT, fg_color="transparent", width=34)
        self._e57_icon.grid(row=0, column=0, padx=(12, 0), pady=(10, 1), sticky="w")
        self._e57_name = ctk.CTkLabel(self._loaded,
                                       text="Drop E57 file here  ·  or Browse",
                                       font=_f(12), text_color=C_DIM,
                                       fg_color="transparent", anchor="w")
        self._e57_name.grid(row=0, column=1, sticky="w", padx=(6, 4), pady=(10, 0))
        self._e57_meta = ctk.CTkLabel(self._loaded, text="", font=_f(10),
                                       text_color=C_HINT, fg_color="transparent", anchor="w")
        self._e57_meta.grid(row=1, column=1, sticky="w", padx=(6, 4), pady=(0, 4))
        self._e57_btn = ctk.CTkButton(self._loaded, text="Browse", width=72, height=26,
                                       **_BTN_SEC, font=_f(10), corner_radius=6,
                                       command=self._browse_e57)
        self._e57_btn.grid(row=0, column=2, rowspan=2, padx=(4, 12), pady=8)

        ctk.CTkFrame(self._loaded, fg_color=C_BORDER, height=1).grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=12)

        # Images row
        self._img_icon = ctk.CTkLabel(self._loaded, text="📁", font=_f(15),
                                       text_color=C_HINT, fg_color="transparent", width=34)
        self._img_icon.grid(row=3, column=0, padx=(12, 0), pady=(4, 10), sticky="w")
        self._img_name = ctk.CTkLabel(self._loaded,
                                       text="Drop panoramas folder here  ·  or Browse",
                                       font=_f(12), text_color=C_DIM,
                                       fg_color="transparent", anchor="w")
        self._img_name.grid(row=3, column=1, sticky="w", padx=(6, 4), pady=(4, 0))
        self._img_meta = ctk.CTkLabel(self._loaded, text="", font=_f(10),
                                       text_color=C_HINT, fg_color="transparent", anchor="w")
        self._img_meta.grid(row=4, column=1, sticky="w", padx=(6, 4), pady=(0, 10))
        self._img_btn = ctk.CTkButton(self._loaded, text="Browse", width=72, height=26,
                                       **_BTN_SEC, font=_f(10), corner_radius=6,
                                       command=self._browse_images)
        self._img_btn.grid(row=3, column=2, rowspan=2, padx=(4, 12), pady=8)

        self._update_view()

    def _update_view(self):
        has_any = self.var_e57.get() or self.var_images.get()
        if has_any:
            self._empty.grid_remove()
            self._loaded.grid(row=0, column=0, sticky="ew")
        else:
            self._loaded.grid_remove()
            self._empty.grid(row=0, column=0, sticky="ew")

    # ── Browse ────────────────────────────────────────────────────────────────
    def _browse_e57(self):
        p = filedialog.askopenfilename(title="Select E57 file",
                                        filetypes=[("E57", "*.e57"), ("All", "*.*")])
        if p: self._set_e57(p)

    def _browse_images(self):
        p = filedialog.askdirectory(title="Select panoramas folder")
        if p: self._set_images(p)

    # ── Setters ───────────────────────────────────────────────────────────────
    def _set_e57(self, path: str):
        path = path.strip().strip("{}")
        self.var_e57.set(path)
        self._e57_name.configure(text=Path(path).name, text_color=C_TEXT, font=_f(12, "bold"))
        self._e57_meta.configure(text="Reading…", text_color=C_HINT)
        self._e57_icon.configure(text_color=C_ACCENT2)
        self._e57_btn.configure(text="Change")
        self.configure(border_color=C_ACCENT2)
        self._update_view()
        if self._on_e57: self._on_e57(path)
        threading.Thread(target=self._detect_e57, args=(path,), daemon=True).start()

    def _set_images(self, path: str):
        path = path.strip().strip("{}")
        self.var_images.set(path)
        self._img_name.configure(text=Path(path).name, text_color=C_TEXT, font=_f(12, "bold"))
        self._img_meta.configure(text="Counting…", text_color=C_HINT)
        self._img_icon.configure(text_color=C_ACCENT2)
        self._img_btn.configure(text="Change")
        self._update_view()
        if self._on_images: self._on_images(path)
        threading.Thread(target=self._detect_images, args=(path,), daemon=True).start()

    def _detect_e57(self, path):
        try:
            mb = os.path.getsize(path) / 1e6
            sz = f"{mb/1000:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB"
            try:
                import pye57; n = pye57.E57(path).scan_count
                info = f"{sz}  ·  {n} scans  ✓"
            except Exception:
                info = sz
            self.after(0, lambda i=info: self._e57_meta.configure(text=i, text_color=C_SUCCESS))
        except Exception:
            self.after(0, lambda: self._e57_meta.configure(text="Loaded ✓", text_color=C_SUCCESS))

    def _detect_images(self, path):
        try:
            n = sum(1 for f in Path(path).iterdir()
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png"))
            self.after(0, lambda i=n: self._img_meta.configure(
                text=f"{i} panorama images  ✓", text_color=C_SUCCESS))
        except Exception:
            self.after(0, lambda: self._img_meta.configure(text="Loaded ✓", text_color=C_SUCCESS))

    # ── Drag-and-drop ─────────────────────────────────────────────────────────
    def register_dnd(self, has_dnd: bool):
        if not has_dnd: return
        try:
            self.drop_target_register(_DND_FILES)
            self.dnd_bind("<<Drop>>",      self._dnd_drop)
            self.dnd_bind("<<DragEnter>>", lambda e: self.configure(border_color=C_ACCENT))
            self.dnd_bind("<<DragLeave>>", lambda e: self.configure(
                border_color=C_ACCENT2 if (self.var_e57.get() or self.var_images.get())
                else C_BORDER))
        except Exception: pass

    def _dnd_drop(self, event):
        raw = event.data.strip()
        raw = raw[1:raw.rfind("}")] if raw.startswith("{") else raw.split()[0]
        if Path(raw).suffix.lower() == ".e57":
            self._set_e57(raw)
        else:
            self._set_images(raw)

    def get_e57(self) -> str: return self.var_e57.get()
    def get_images(self) -> str: return self.var_images.get()


# ── PresetTile ────────────────────────────────────────────────────────────────
class PresetTile(ctk.CTkFrame):
    def __init__(self, parent, name: str, pts_label: str, desc: str,
                 var: tk.StringVar, **kw):
        super().__init__(parent, fg_color=C_CARD, corner_radius=10,
                         border_color=C_BORDER, border_width=2, cursor="hand2", **kw)
        self._var = var; self._value = name
        self.grid_columnconfigure(0, weight=1)
        self._pts_lbl = ctk.CTkLabel(self, text=pts_label, font=_f(16, "bold"),
                                      text_color=C_NAVY, fg_color="transparent")
        self._pts_lbl.grid(row=0, column=0, pady=(10, 1), padx=8)
        ctk.CTkLabel(self, text=name, font=_f(11, "bold"),
                     text_color=C_TEXT, fg_color="transparent").grid(row=1, column=0, pady=(0, 1))
        ctk.CTkLabel(self, text=desc, font=_f(9),
                     text_color=C_DIM, fg_color="transparent").grid(row=2, column=0, pady=(0, 10))
        self._chk = ctk.CTkLabel(self, text=" ✓ ", font=_f(9, "bold"),
                                  text_color="white", fg_color=C_ACCENT2,
                                  corner_radius=8, width=22, height=16)
        var.trace_add("write", self._refresh)
        self._refresh()
        for w in [self] + list(self.winfo_children()):
            w.bind("<Button-1>", self._select)

    def _select(self, *_): self._var.set(self._value)

    def _refresh(self, *_):
        sel = self._var.get() == self._value
        self.configure(border_color=C_ACCENT2 if sel else C_BORDER)
        self._pts_lbl.configure(text_color=C_ACCENT2 if sel else C_NAVY)
        if sel: self._chk.place(relx=1.0, rely=0.0, anchor="ne", x=-5, y=5)
        else:   self._chk.place_forget()


# ── QueueStream ───────────────────────────────────────────────────────────────
class _QS:
    def __init__(self, q): self._q = q
    def write(self, t):
        if t: self._q.put(("log", t))
    def flush(self): pass


# ── App ───────────────────────────────────────────────────────────────────────
class See3DConverterApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("light")
        self.title("See3D — E57 Converter")
        self.geometry("860x710")
        self.minsize(780, 615)
        self.configure(fg_color=C_BG)
        try: self.iconbitmap(str(resource_path("assets/app_icon.ico")))
        except Exception: pass

        self._has_dnd = False
        if HAS_DND and _tkdnd:
            try: _tkdnd.TkinterDnD._require(self); self._has_dnd = True
            except Exception: pass

        self._q          = queue.Queue()
        self._converting = False
        self._validating = False
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

        self._build_ui()
        self._poll()

    # ── Shell ─────────────────────────────────────────────────────────────────
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
        hdr.pack(fill="x"); hdr.pack_propagate(False)
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
        ctk.CTkFrame(self, fg_color=C_BORDER, height=1, corner_radius=0).pack(fill="x")

    def _build_tab_strip(self):
        """Navy bar with cyan active tab and medium-blue inactive tabs — all visible."""
        strip = ctk.CTkFrame(self, fg_color=_TAB_BG, corner_radius=0, height=40)
        strip.pack(fill="x"); strip.pack_propagate(False)

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
        for f in self._frames.values(): f.pack_forget()
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

    # ── Convert tab ───────────────────────────────────────────────────────────
    def _build_convert(self, tab: ctk.CTkScrollableFrame):
        r = 0

        r = self._sec(tab, "INPUT FILES", r)
        self._dropzone = CombinedDropZone(tab,
                                           on_e57_change=self._on_e57_change,
                                           on_images_change=None)
        self._dropzone.grid(row=r, column=0, sticky="ew", padx=2, pady=(0, 4)); r += 1
        self._dropzone.register_dnd(self._has_dnd)

        # Output folder
        out_row = ctk.CTkFrame(tab, fg_color=C_CARD, corner_radius=8,
                                border_color=C_BORDER, border_width=1)
        out_row.grid(row=r, column=0, sticky="ew", padx=2, pady=(0, 4)); r += 1
        out_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(out_row, text="📂", font=_f(15), text_color=C_HINT,
                     fg_color="transparent", width=34).grid(row=0, column=0, padx=(12, 0), pady=8)
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
        r1.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 3))
        ctk.CTkCheckBox(r1,
                        text="Exclude floor & ceiling  (saved to nadir_and_zenith/ folder)",
                        variable=self.var_4face, checkmark_color="white",
                        fg_color=C_ACCENT2, hover_color="#0058B3",
                        text_color=C_TEXT, font=_f(12)).pack(side="left")
        _qmark(r1, "The M2's camera doesn't fully reach the poles — Realsee fills those areas "
                   "with generative AI that differs between scan positions, causing ghosting in "
                   "the splat. When ticked, floor & ceiling images are still rendered but saved "
                   "to a nadir_and_zenith/ folder alongside Colmap/ — not included in training. "
                   "Recommended for all indoor work.").pack(side="left", padx=(8, 0))

        r2 = ctk.CTkFrame(opt, fg_color="transparent")
        r2.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 3))
        ctk.CTkCheckBox(r2, text="Run alignment validation after conversion",
                        variable=self.var_validate, checkmark_color="white",
                        fg_color=C_ACCENT2, hover_color="#0058B3",
                        text_color=C_TEXT, font=_f(12)).pack(side="left")
        _qmark(r2, "Re-projects LiDAR points through the camera poses and measures colour "
                   "alignment. A healthy M2 capture scores 5–9. Above ~12 usually indicates "
                   "a panorama count mismatch or capture issue.").pack(side="left", padx=(8, 0))

        self._adv_open = False
        self._adv_toggle_btn = ctk.CTkButton(
            opt, text="▶  Advanced settings", anchor="w",
            fg_color="transparent", hover_color=C_BG, text_color=C_DIM, font=_f(10),
            command=self._toggle_adv, height=24, corner_radius=4)
        self._adv_toggle_btn.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))

        self._adv_inner = ctk.CTkFrame(opt, fg_color=C_BG, corner_radius=8)
        self._adv_inner.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))
        self._adv_inner.grid_remove()
        self._adv_inner.grid_columnconfigure((0, 1, 2), weight=1)

        for i, (lbl, var, tip) in enumerate([
            ("Face Size (px)", self.var_facesize,
             "Cubemap face resolution. 4,000 matches the M2's native resolution. "
             "Lower values reduce VRAM and training time."),
            ("Workers", self.var_workers,
             "CPU cores for cubemap rendering. Blank = automatic. "
             "Set to 1 if you see multiprocessing errors."),
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

        # Convert button + progress
        self._conv_btn = ctk.CTkButton(
            tab, text="CONVERT", height=46,
            fg_color=C_NAVY, hover_color=C_ACCENT2, text_color="white",
            font=_f(14, "bold"), corner_radius=8, command=self._start_conversion)
        self._conv_btn.grid(row=r, column=0, sticky="ew", padx=2, pady=(10, 0)); r += 1

        self._prog = ctk.CTkProgressBar(
            tab, fg_color=C_BORDER, progress_color=C_ACCENT, height=6, corner_radius=3)
        self._prog.grid(row=r, column=0, sticky="ew", padx=2, pady=(5, 0)); r += 1
        self._prog.set(0); self._prog.grid_remove()

        self._stat_lbl = ctk.CTkLabel(tab, text="", fg_color="transparent",
                                       text_color=C_DIM, font=_f(10))
        self._stat_lbl.grid(row=r, column=0, pady=(2, 8)); r += 1

    def _make_custom_tile(self, parent) -> ctk.CTkFrame:
        tile = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=10,
                            border_color=C_BORDER, border_width=2, cursor="hand2")
        tile.grid_columnconfigure(0, weight=1)
        self._cust_icon = ctk.CTkLabel(tile, text="✏", font=_f(15),
                                        text_color=C_HINT, fg_color="transparent")
        self._cust_icon.grid(row=0, column=0, pady=(10, 1))
        ctk.CTkLabel(tile, text="Custom", font=_f(11, "bold"),
                     text_color=C_TEXT, fg_color="transparent").grid(row=1, column=0, pady=(0, 1))
        self._cust_hint = ctk.CTkLabel(tile, text="Set value", font=_f(9),
                                        text_color=C_DIM, fg_color="transparent")
        self._cust_hint.grid(row=2, column=0, pady=(0, 10))
        self._cust_entry = ctk.CTkEntry(tile, textvariable=self.var_points,
                                         fg_color=C_CARD, border_color=C_BORDER, border_width=1,
                                         text_color=C_TEXT, height=24, width=86,
                                         font=_f(10), justify="center")
        self._cust_chk = ctk.CTkLabel(tile, text=" ✓ ", font=_f(9, "bold"),
                                       text_color="white", fg_color=C_ACCENT2,
                                       corner_radius=8, width=22, height=16)
        tile.bind("<Button-1>", lambda e: self.var_preset.set("Custom"))
        self._cust_icon.bind("<Button-1>", lambda e: self.var_preset.set("Custom"))
        self._cust_hint.bind("<Button-1>", lambda e: self.var_preset.set("Custom"))
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
            text=("▼" if self._adv_open else "▶") + "  Advanced settings")
        if self._adv_open:
            self._adv_inner.grid()
        else:
            self._adv_inner.grid_remove()

    # ── Validate tab ──────────────────────────────────────────────────────────
    def _build_validate(self, tab: ctk.CTkScrollableFrame):
        tab.grid_columnconfigure(0, weight=1)
        r = 0
        ctk.CTkLabel(tab,
                     text="Re-project LiDAR points through the generated camera poses to measure "
                          "alignment quality. A healthy Realsee M2 capture scores 5–9.",
                     text_color=C_DIM, font=_f(11), justify="left", anchor="w",
                     wraplength=720, fg_color="transparent").grid(
            row=r, column=0, sticky="ew", padx=4, pady=(8, 6)); r += 1

        self._val_drop = CombinedDropZone(tab)
        self._val_drop.grid(row=r, column=0, sticky="ew", padx=2, pady=(0, 4)); r += 1
        self._val_drop.register_dnd(self._has_dnd)
        self._val_drop._e57_name.configure(text="Drop Colmap folder here  ·  or Browse")
        self._val_drop._img_name.configure(text="Drop E57 file here  ·  or Browse")
        self._val_drop._e57_btn.configure(command=self._browse_val_colmap)
        self._val_drop._img_btn.configure(command=self._browse_val_e57)
        self._val_drop._e57_icon.configure(text="📁")
        self._val_drop._img_icon.configure(text="📄")

        br = ctk.CTkFrame(tab, fg_color="transparent")
        br.grid(row=r, column=0, sticky="ew", padx=4, pady=(10, 0)); r += 1
        br.grid_columnconfigure(1, weight=1)
        sb = ctk.CTkFrame(br, fg_color=C_BG, corner_radius=10, border_color=C_BORDER,
                           border_width=1, width=100, height=70)
        sb.grid(row=0, column=0); sb.pack_propagate(False)
        self._score_num = ctk.CTkLabel(sb, text="—", font=_f(28, "bold"),
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
        self._prev_btn = ctk.CTkButton(nav, text="← Prev", width=70, height=26,
                                        **_BTN_SEC, font=_f(11), corner_radius=6,
                                        command=self._prev_ovr)
        self._prev_btn.pack(side="left")
        self._ovr_lbl = ctk.CTkLabel(nav, text="", fg_color="transparent",
                                      font=_f(11), text_color=C_DIM)
        self._ovr_lbl.pack(side="left", expand=True)
        self._next_btn = ctk.CTkButton(nav, text="Next →", width=70, height=26,
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

    # ── Log tab ───────────────────────────────────────────────────────────────
    def _build_log(self, tab: ctk.CTkScrollableFrame):
        tab.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(tab, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=4, pady=(8, 4))
        ctk.CTkLabel(hdr, text="Output log", font=_f(13, "bold"),
                     text_color=C_TEXT, fg_color="transparent").pack(side="left")
        ctk.CTkButton(hdr, text="Clear", width=56, height=26, **_BTN_SEC, font=_f(11),
                      corner_radius=6, command=self._clear_log).pack(side="right")
        self._log = ctk.CTkTextbox(tab, fg_color=C_LOG_BG, text_color=C_TEXT,
                                    font=_f(11, mono=True), height=500, corner_radius=8,
                                    wrap="word", border_color=C_BORDER, border_width=1)
        self._log.grid(row=1, column=0, sticky="nsew", padx=2, pady=(0, 8))
        self._log.configure(state="disabled")

    # ── File callbacks ────────────────────────────────────────────────────────
    def _on_e57_change(self, path: str):
        parent = Path(path).parent
        root = parent.parent if parent.name.lower() == "points" else parent
        if (root / "images").is_dir() and not self._dropzone.get_images():
            self._dropzone._set_images(str(root / "images"))
        if not self._out_var.get():
            self._set_output(str(root / "Colmap"))
        if not self._val_drop.var_images.get():
            self._val_drop.var_images.set(path)
            self._val_drop._img_name.configure(text=Path(path).name,
                                               text_color=C_TEXT, font=_f(12, "bold"))
            self._val_drop._update_view()
            threading.Thread(target=self._val_drop._detect_e57, args=(path,), daemon=True).start()
        if not self._val_drop.var_e57.get():
            colmap_path = str(root / "Colmap")
            self._val_drop.var_e57.set(colmap_path)
            self._val_drop._e57_name.configure(text="Colmap/",
                                               text_color=C_TEXT, font=_f(12, "bold"))
            self._val_drop._update_view()

    def _browse_output(self):
        p = filedialog.askdirectory(title="Select output folder")
        if p: self._set_output(p)

    def _set_output(self, path: str):
        self._out_var.set(path)
        self._out_lbl.configure(text=Path(path).name, text_color=C_TEXT, font=_f(12, "bold"))

    def _browse_val_colmap(self):
        p = filedialog.askdirectory(title="Select Colmap folder")
        if p:
            self._val_drop.var_e57.set(p)
            self._val_drop._e57_name.configure(text=Path(p).name,
                                               text_color=C_TEXT, font=_f(12, "bold"))
            self._val_drop._e57_meta.configure(text="Folder selected ✓", text_color=C_SUCCESS)
            self._val_drop._e57_icon.configure(text_color=C_ACCENT2)
            self._val_drop._e57_btn.configure(text="Change")
            self._val_drop.configure(border_color=C_ACCENT2)
            self._val_drop._update_view()

    def _browse_val_e57(self):
        p = filedialog.askopenfilename(title="Select E57 file",
                                        filetypes=[("E57", "*.e57"), ("All", "*.*")])
        if p:
            self._val_drop.var_images.set(p)
            self._val_drop._img_name.configure(text=Path(p).name,
                                               text_color=C_TEXT, font=_f(12, "bold"))
            self._val_drop._update_view()
            threading.Thread(target=self._val_drop._detect_e57, args=(p,), daemon=True).start()
            self._val_drop._img_icon.configure(text_color=C_ACCENT2)
            self._val_drop._img_btn.configure(text="Change")

    def _on_preset_change(self, *_):
        pts = next((p for n, _, __, p in PRESETS if n == self.var_preset.get()), None)
        if pts is not None:
            self.var_points.set(_fmt_pts(pts))

    # ── Log ───────────────────────────────────────────────────────────────────
    def _clear_log(self):
        self._log.configure(state="normal"); self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _append_log(self, text: str):
        self._log.configure(state="normal"); self._log.insert("end", text)
        self._log.see("end"); self._log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, msg = self._q.get_nowait()
                if kind == "log":        self._append_log(msg)
                elif kind == "prog":     self._set_progress(msg)
                elif kind == "done":     self._on_done(True, msg)
                elif kind == "error":    self._on_done(False, msg)
                elif kind == "val_done": self._on_val_done(msg)
        except queue.Empty: pass
        self.after(80, self._poll)

    def _set_progress(self, frac: float):
        self._prog.set(frac)
        self._stat_lbl.configure(text=f"Converting…  {int(frac * 100)}%", text_color=C_ACCENT2)

    # ── Conversion ────────────────────────────────────────────────────────────
    def _start_conversion(self):
        if self._converting or self._validating: return
        e57    = self._dropzone.get_e57()
        images = self._dropzone.get_images()
        output = self._out_var.get().strip()
        for val, msg in [(e57, "Select an E57 file."),
                         (images, "Select the panoramas folder."),
                         (output, "Select an output folder.")]:
            if not val:
                self._switch("Logs")
                self._append_log(f"ERROR: {msg}\n"); return
        try:
            face_size = _parse_pts(self.var_facesize.get()) if self.var_facesize.get() else 4000
            workers   = int(self.var_workers.get()) if self.var_workers.get().strip() else None
            seed      = int(self.var_seed.get()) if self.var_seed.get() else 0
            max_pts   = _parse_pts(self.var_points.get()) if self.var_points.get().strip() else 1_000_000
        except ValueError as exc:
            self._append_log(f"ERROR: {exc}\n"); return

        self._converting = True
        self._conv_btn.configure(state="disabled", text="Converting…")
        self._prog.grid()
        self._prog.configure(mode="determinate")
        self._prog.set(0)
        self._stat_lbl.configure(text="Starting…", text_color=C_ACCENT2)
        self._clear_log()
        self._switch("Logs")

        def _cb(frac): self._q.put(("prog", frac))

        threading.Thread(target=self._conv_worker,
                         args=(e57, images, output, face_size, workers, max_pts, seed, _cb),
                         daemon=True).start()

    def _conv_worker(self, e57, images, output, face_size, workers, max_pts, seed, cb):
        import numpy as np
        from converter_core import run_conversion, validate_conversion
        qs = _QS(self._q)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = qs; sys.stderr = qs
        try:
            run_conversion(
                images_dir=Path(images), points_path=Path(e57),
                colmap_dir=Path(output), face_size=face_size, yaw_offset_deg=0.0,
                workers=workers, max_points=max_pts if max_pts > 0 else None,
                camera_offset=np.zeros(3), seed=seed,
                include_nadir_zenith=not self.var_4face.get(),
                progress_callback=cb)
            if self.var_validate.get():
                print("\n── Alignment Validation ──────────────────────────")
                validate_conversion(colmap_dir=Path(output), e57_path=Path(e57))
            self._q.put(("done", "Conversion complete."))
        except Exception as exc:
            import traceback
            self._q.put(("log", f"\n─── ERROR ───\n{exc}\n{traceback.format_exc()}\n"))
            self._q.put(("error", str(exc)))
        finally:
            sys.stdout = old_out; sys.stderr = old_err

    def _on_done(self, success: bool, message: str):
        self._converting = False
        if success:
            self._prog.set(1.0)
            self._conv_btn.configure(state="normal", text="CONVERT AGAIN", fg_color=C_NAVY)
            self._stat_lbl.configure(text=f"Done — {message}", text_color=C_SUCCESS)
        else:
            self._prog.set(0)
            self._conv_btn.configure(state="normal", text="RETRY", fg_color="#880000")
            self._stat_lbl.configure(text="Failed — see log", text_color=C_ERROR)

    # ── Validation ────────────────────────────────────────────────────────────
    def _start_validation(self):
        if self._validating or self._converting: return
        colmap = self._val_drop.var_e57.get()
        e57    = self._val_drop.var_images.get()
        if not colmap: self._append_log("ERROR: Select a Colmap folder.\n"); return
        if not e57:    self._append_log("ERROR: Select the E57 file.\n"); return
        self._validating = True
        self._val_btn.configure(state="disabled", text="Validating…")
        self._score_num.configure(text="…", text_color=C_DIM)
        self._score_status.configure(text="Running…", text_color=C_ACCENT2)
        self._score_sub.configure(text="Scoring and generating visual overlays…", text_color=C_HINT)
        self._ovr_frame.grid_remove()
        self._clear_log()
        self._switch("Logs")
        self._append_log("── Alignment Validation ──────────────────────────\n")
        threading.Thread(target=self._val_worker, args=(colmap, e57), daemon=True).start()

    def _val_worker(self, colmap: str, e57: str):
        from converter_core import validate_conversion, get_available_scans, generate_overlay
        qs = _QS(self._q)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = qs; sys.stderr = qs
        try:
            score = validate_conversion(Path(colmap), Path(e57))
            overlays, labels = [], []
            try:
                scans = get_available_scans(Path(colmap))
                if scans:
                    for sid in sorted({scans[0], scans[len(scans) // 2], scans[-1]}):
                        print(f"Generating overlay for scan {sid}…")
                        img = generate_overlay(Path(colmap), Path(e57), scan_id=sid, face="pz")
                        overlays.append(img); labels.append(f"Scan {sid}  ·  pz face")
            except Exception as oe:
                print(f"Note: overlay generation skipped — {oe}")
            self._q.put(("val_done", (score, overlays, labels)))
        except Exception as exc:
            import traceback
            self._q.put(("log", f"\nValidation ERROR: {exc}\n{traceback.format_exc()}\n"))
            self._q.put(("val_done", (None, [], [])))
        finally:
            sys.stdout = old_out; sys.stderr = old_err

    def _on_val_done(self, payload):
        score, overlays, labels = payload
        self._validating = False
        self._val_btn.configure(state="normal", text="RUN VALIDATION")
        if score is None:
            self._score_num.configure(text="ERR", text_color=C_ERROR)
            self._score_status.configure(text="Validation failed", text_color=C_ERROR)
            self._score_sub.configure(text="See log for details", text_color=C_ERROR)
        elif score <= 9:
            self._score_num.configure(text=f"{score:.1f}", text_color=C_SUCCESS)
            self._score_status.configure(text="GOOD  ✓", text_color=C_SUCCESS)
            self._score_sub.configure(text="Healthy alignment  (target 5–9)", text_color=C_SUCCESS)
        elif score <= 14:
            self._score_num.configure(text=f"{score:.1f}", text_color=C_WARN)
            self._score_status.configure(text="MARGINAL  ⚠", text_color=C_WARN)
            self._score_sub.configure(
                text="Slightly high — check panorama count matches scan count", text_color=C_WARN)
        else:
            self._score_num.configure(text=f"{score:.1f}", text_color=C_ERROR)
            self._score_status.configure(text="HIGH  ✗", text_color=C_ERROR)
            self._score_sub.configure(
                text="Alignment issue — verify E57 matches images folder", text_color=C_ERROR)
        if overlays:
            self._ovr_imgs = overlays; self._ovr_lbls = labels
            self._ovr_idx = min(1, len(overlays) - 1)
            self._show_ovr(self._ovr_idx)
            self._ovr_frame.grid()
        self._switch("Validate")

    def _show_ovr(self, idx: int):
        self._ovr_idx = idx
        img = self._ovr_imgs[idx]; w = 740
        ci = ctk.CTkImage(light_image=img, dark_image=img,
                           size=(w, int(img.height * w / img.width)))
        self._ovr_img.configure(image=ci, text=""); self._ovr_img._image = ci
        total = len(self._ovr_imgs)
        self._ovr_lbl.configure(text=f"{self._ovr_lbls[idx]}  ({idx + 1} / {total})")
        self._prev_btn.configure(state="normal" if idx > 0 else "disabled")
        self._next_btn.configure(state="normal" if idx < total - 1 else "disabled")

    def _prev_ovr(self):
        if self._ovr_idx > 0: self._show_ovr(self._ovr_idx - 1)

    def _next_ovr(self):
        if self._ovr_idx < len(self._ovr_imgs) - 1: self._show_ovr(self._ovr_idx + 1)


if __name__ == "__main__":
    ctk.set_appearance_mode("light")
    app = See3DConverterApp()
    app.mainloop()
