#!/usr/bin/env python3
"""
Image Redactor
A simple, lightweight image viewer / editor for Windows 10.

Features:
- Open & save: JPG/JPEG/JFIF, PNG, WEBP, BMP, GIF (first frame)
- Paste images or text from the clipboard
- Undo / redo
- Text tool (separate, movable/editable layer): font, size, color
- Pen tool: adjustable thickness & color
- Eraser tool: adjustable thickness (erases to transparency)
- Crop, flip (H/V), rotate (90 deg left/right)
- Adjustments: brightness, contrast, saturation, hue, sharpness, blur

Dependencies: Pillow (pip install pillow). Everything else is stdlib (tkinter).
Packaged to a Windows .exe with PyInstaller - see build.bat / README.
"""

import os
import sys
import copy
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser, simpledialog

from PIL import (
    Image, ImageDraw, ImageTk, ImageEnhance, ImageFilter,
    ImageGrab, ImageOps, ImageFont, ImageColor,
)

APP_TITLE = "Image Redactor"
MAX_HISTORY = 30

OPEN_FILETYPES = [
    ("Image files", "*.jpg *.jpeg *.jfif *.png *.webp *.bmp *.gif"),
    ("JPEG", "*.jpg *.jpeg *.jfif"),
    ("PNG", "*.png"),
    ("WebP", "*.webp"),
    ("Bitmap", "*.bmp"),
    ("GIF", "*.gif"),
    ("All files", "*.*"),
]
SAVE_FILETYPES = [
    ("PNG", "*.png"),
    ("JPEG", "*.jpg"),
    ("WebP", "*.webp"),
    ("Bitmap", "*.bmp"),
    ("GIF", "*.gif"),
]
ALPHA_LESS_EXT = {".jpg", ".jpeg", ".jfif", ".bmp"}

FONT_FILES = {
    "Arial": "arial.ttf",
    "Arial Black": "ariblk.ttf",
    "Times New Roman": "times.ttf",
    "Courier New": "cour.ttf",
    "Verdana": "verdana.ttf",
    "Comic Sans MS": "comic.ttf",
    "Impact": "impact.ttf",
    "Georgia": "georgia.ttf",
    "Tahoma": "tahoma.ttf",
    "Segoe UI": "segoeui.ttf",
    "Calibri": "calibri.ttf",
}
FONT_NAMES = list(FONT_FILES.keys())


def get_font(family, size):
    """Resolve a font family name to a PIL ImageFont, looking in the Windows
    Fonts directory first (works when frozen to .exe on Win10), then falling
    back gracefully so the app never crashes if a font is missing."""
    size = max(1, int(size))
    fname = FONT_FILES.get(family, "arial.ttf")
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        os.path.join(windir, "Fonts", fname),
        fname,
        os.path.join(windir, "Fonts", "arial.ttf"),
        "DejaVuSans.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def shift_hue(rgb_img, degrees):
    """Rotate the hue of an RGB image by `degrees` (-180..180) without numpy."""
    if degrees == 0:
        return rgb_img
    hsv = rgb_img.convert("HSV")
    h, s, v = hsv.split()
    shift = int(round(degrees / 360.0 * 255.0)) % 255
    h = h.point(lambda p, shift=shift: (p + shift) % 255)
    return Image.merge("HSV", (h, s, v)).convert("RGB")


def apply_adjustments(base_rgba, brightness, contrast, saturation, hue, sharpness, blur):
    """Apply all adjustment sliders to an RGBA base image, returning a new RGBA image."""
    img = base_rgba if base_rgba.mode == "RGBA" else base_rgba.convert("RGBA")
    r, g, b, a = img.split()
    rgb = Image.merge("RGB", (r, g, b))
    if hue:
        rgb = shift_hue(rgb, hue)
    if brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if sharpness != 1.0:
        rgb = ImageEnhance.Sharpness(rgb).enhance(sharpness)
    if blur > 0:
        rgb = rgb.filter(ImageFilter.GaussianBlur(radius=blur))
    r2, g2, b2 = rgb.split()
    return Image.merge("RGBA", (r2, g2, b2, a))


class Overlay:
    """A movable, editable object floating above the base raster image:
    either a text object or a pasted image. Overlays live independently
    until they are 'committed' (baked/flattened) into the base image."""

    _next_id = 1

    def __init__(self, kind, x, y, text="", font_family="Arial", font_size=32,
                 color="#ff0000", image=None):
        self.kind = kind  # "text" or "image"
        self.x = x
        self.y = y
        self.text = text
        self.font_family = font_family
        self.font_size = font_size
        self.color = color
        self.image = image  # PIL.Image (RGBA) for kind == "image"
        self.id = Overlay._next_id
        Overlay._next_id += 1

    def clone(self):
        o = Overlay(self.kind, self.x, self.y, text=self.text,
                    font_family=self.font_family, font_size=self.font_size,
                    color=self.color, image=self.image.copy() if self.image is not None else None)
        o.id = self.id
        return o

    def bbox(self):
        if self.kind == "text":
            tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            fnt = get_font(self.font_family, self.font_size)
            try:
                l, t, r, b = tmp.textbbox((self.x, self.y), self.text or " ", font=fnt)
            except Exception:
                w = 8 * len(self.text or " ")
                l, t, r, b = self.x, self.y, self.x + w, self.y + self.font_size
            return (l, t, r, b)
        else:
            w, h = self.image.size
            return (self.x, self.y, self.x + w, self.y + h)


class TextDialog(tk.Toplevel):
    """Modal dialog to add/edit a text overlay."""

    def __init__(self, master, overlay=None):
        super().__init__(master)
        self.title("Edit Text" if overlay else "Add Text")
        self.resizable(False, False)
        self.result = None
        self.transient(master)
        self.grab_set()

        is_edit = overlay is not None
        self.color = overlay.color if is_edit else "#ff0000"

        frm = ttk.Frame(self, padding=10)
        frm.grid(row=0, column=0)

        ttk.Label(frm, text="Text:").grid(row=0, column=0, sticky="nw", pady=4)
        self.text_widget = tk.Text(frm, width=32, height=4)
        self.text_widget.grid(row=0, column=1, columnspan=2, pady=4)
        if is_edit:
            self.text_widget.insert("1.0", overlay.text)

        ttk.Label(frm, text="Font:").grid(row=1, column=0, sticky="w", pady=4)
        self.font_var = tk.StringVar(value=overlay.font_family if is_edit else "Arial")
        font_box = ttk.Combobox(frm, textvariable=self.font_var, values=FONT_NAMES,
                                 state="readonly", width=18)
        font_box.grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="Size:").grid(row=2, column=0, sticky="w", pady=4)
        self.size_var = tk.IntVar(value=overlay.font_size if is_edit else 32)
        size_spin = ttk.Spinbox(frm, from_=6, to=300, textvariable=self.size_var, width=6)
        size_spin.grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="Color:").grid(row=3, column=0, sticky="w", pady=4)
        self.color_btn = tk.Button(frm, text="        ", bg=self.color,
                                    command=self.pick_color)
        self.color_btn.grid(row=3, column=1, sticky="w", pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=3, pady=(10, 0), sticky="e")
        if is_edit:
            ttk.Button(btns, text="Delete", command=self.on_delete).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(btns, text="OK", command=self.on_ok).pack(side="left", padx=4)

        self.text_widget.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.wait_window(self)

    def pick_color(self):
        rgb, hexval = colorchooser.askcolor(color=self.color, parent=self)
        if hexval:
            self.color = hexval
            self.color_btn.configure(bg=hexval)

    def on_ok(self):
        text = self.text_widget.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo("Add Text", "Text is empty.", parent=self)
            return
        self.result = {
            "action": "save",
            "text": text,
            "font_family": self.font_var.get(),
            "font_size": self.size_var.get(),
            "color": self.color,
        }
        self.destroy()

    def on_delete(self):
        self.result = {"action": "delete"}
        self.destroy()


class ImageRedactorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1150x760")
        self.minsize(700, 480)

        # --- document state ---
        self.image = None            # PIL.Image RGBA, the base raster
        self.overlays = []           # list[Overlay]
        self.selected_overlay = None
        self.current_path = None
        self.dirty = False

        # --- undo / redo ---
        self.undo_stack = []
        self.redo_stack = []

        # --- view state ---
        self.scale = 1.0
        self.off_x = 0
        self.off_y = 0
        self.tk_photo = None

        # --- tool state ---
        self.tool = tk.StringVar(value="select")
        self.pen_color = "#e02020"
        self.pen_size = tk.IntVar(value=6)
        self.eraser_size = tk.IntVar(value=24)
        self._drawing = False
        self._last_point = None
        self._drag_overlay = None
        self._drag_offset = None
        self._crop_start = None
        self._crop_rect = None  # (x0,y0,x1,y1) in image coords
        self._crop_canvas_id = None
        self._adjust_base = None
        self._resize_job = None

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._bind_shortcuts()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_title()
        self.set_tool("select")

    # ------------------------------------------------------------------ UI
    def _build_menu(self):
        m = tk.Menu(self)
        self.config(menu=m)

        filem = tk.Menu(m, tearoff=0)
        filem.add_command(label="New...", command=self.new_document, accelerator="Ctrl+N")
        filem.add_command(label="Open...", command=self.open_file, accelerator="Ctrl+O")
        filem.add_command(label="Paste as New/Overlay", command=self.paste_from_clipboard, accelerator="Ctrl+V")
        filem.add_separator()
        filem.add_command(label="Save", command=self.save, accelerator="Ctrl+S")
        filem.add_command(label="Save As...", command=lambda: self.save(save_as=True), accelerator="Ctrl+Shift+S")
        filem.add_separator()
        filem.add_command(label="Exit", command=self.on_close)
        m.add_cascade(label="File", menu=filem)

        editm = tk.Menu(m, tearoff=0)
        editm.add_command(label="Undo", command=self.undo, accelerator="Ctrl+Z")
        editm.add_command(label="Redo", command=self.redo, accelerator="Ctrl+Y")
        editm.add_separator()
        editm.add_command(label="Delete Selected Overlay", command=self.delete_selected_overlay, accelerator="Del")
        m.add_cascade(label="Edit", menu=editm)

        imgm = tk.Menu(m, tearoff=0)
        imgm.add_command(label="Crop", command=lambda: self.set_tool("crop"))
        imgm.add_command(label="Flip Horizontal", command=self.flip_horizontal)
        imgm.add_command(label="Flip Vertical", command=self.flip_vertical)
        imgm.add_command(label="Rotate Left 90", command=self.rotate_left)
        imgm.add_command(label="Rotate Right 90", command=self.rotate_right)
        imgm.add_separator()
        imgm.add_command(label="Adjustments...", command=self.open_adjust_panel)
        m.add_cascade(label="Image", menu=imgm)

        toolsm = tk.Menu(m, tearoff=0)
        toolsm.add_radiobutton(label="Select / Move", variable=self.tool, value="select",
                                command=lambda: self.set_tool("select"))
        toolsm.add_radiobutton(label="Pen", variable=self.tool, value="pen",
                                command=lambda: self.set_tool("pen"))
        toolsm.add_radiobutton(label="Eraser", variable=self.tool, value="eraser",
                                command=lambda: self.set_tool("eraser"))
        toolsm.add_radiobutton(label="Text", variable=self.tool, value="text",
                                command=lambda: self.set_tool("text"))
        m.add_cascade(label="Tools", menu=toolsm)

        helpm = tk.Menu(m, tearoff=0)
        helpm.add_command(label="About", command=self.show_about)
        m.add_cascade(label="Help", menu=helpm)

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(4, 4))
        bar.pack(side="top", fill="x")

        def btn(text, cmd, **kw):
            b = ttk.Button(bar, text=text, command=cmd)
            b.pack(side="left", padx=2)
            return b

        btn("Open", self.open_file)
        btn("Paste", self.paste_from_clipboard)
        btn("Save", self.save)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        btn("Undo", self.undo)
        btn("Redo", self.redo)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)

        self.tool_buttons = {}
        for label, val in (("Select", "select"), ("Pen", "pen"), ("Eraser", "eraser"), ("Text", "text")):
            b = ttk.Button(bar, text=label, command=lambda v=val: self.set_tool(v))
            b.pack(side="left", padx=2)
            self.tool_buttons[val] = b

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        btn("Crop", lambda: self.set_tool("crop"))
        btn("Flip H", self.flip_horizontal)
        btn("Flip V", self.flip_vertical)
        btn("Rotate L", self.rotate_left)
        btn("Rotate R", self.rotate_right)
        btn("Adjustments", self.open_adjust_panel)

    def _build_body(self):
        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)

        self.side_panel = ttk.Frame(body, padding=8, width=250)
        self.side_panel.pack(side="left", fill="y")
        self.side_panel.pack_propagate(False)

        canvas_frame = ttk.Frame(body)
        canvas_frame.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.canvas.bind("<Double-Button-1>", self.on_canvas_double_click)
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        self.canvas.bind("<Configure>", self.on_canvas_configure)

    def _build_statusbar(self):
        self.status = tk.StringVar(value="Open an image to get started (File > Open, or Ctrl+V to paste).")
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(side="left", padx=6, pady=2, fill="x", expand=True)

    def _bind_shortcuts(self):
        self.bind_all("<Control-o>", lambda e: self.open_file())
        self.bind_all("<Control-n>", lambda e: self.new_document())
        self.bind_all("<Control-s>", lambda e: self.save())
        self.bind_all("<Control-S>", lambda e: self.save(save_as=True))
        self.bind_all("<Control-v>", lambda e: self.paste_from_clipboard())
        self.bind_all("<Control-z>", lambda e: self.undo())
        self.bind_all("<Control-y>", lambda e: self.redo())
        self.bind_all("<Control-Shift-Z>", lambda e: self.redo())
        self.bind_all("<Delete>", lambda e: self.delete_selected_overlay())
        self.bind_all("<BackSpace>", lambda e: self.delete_selected_overlay())

    # ------------------------------------------------------------ doc / io
    def new_document(self):
        dlg = tk.Toplevel(self)
        dlg.title("New Image")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        ttk.Label(dlg, text="Width:").grid(row=0, column=0, padx=8, pady=8, sticky="e")
        w_var = tk.IntVar(value=800)
        ttk.Entry(dlg, textvariable=w_var, width=8).grid(row=0, column=1, padx=8, pady=8)
        ttk.Label(dlg, text="Height:").grid(row=1, column=0, padx=8, pady=8, sticky="e")
        h_var = tk.IntVar(value=600)
        ttk.Entry(dlg, textvariable=h_var, width=8).grid(row=1, column=1, padx=8, pady=8)

        def create():
            try:
                w, h = int(w_var.get()), int(h_var.get())
                w = max(1, min(w, 8000))
                h = max(1, min(h, 8000))
            except Exception:
                messagebox.showerror("New Image", "Invalid size.", parent=dlg)
                return
            img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
            self.new_document_from_image(img, None)
            dlg.destroy()

        ttk.Button(dlg, text="Create", command=create).grid(row=2, column=0, columnspan=2, pady=8)
        dlg.wait_window(dlg)

    def open_file(self):
        path = filedialog.askopenfilename(filetypes=OPEN_FILETYPES)
        if path:
            self.open_path(path)

    def open_path(self, path):
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA")
        except Exception as e:
            messagebox.showerror("Open failed", f"Could not open image:\n{e}")
            return
        self.new_document_from_image(img, path)

    def new_document_from_image(self, img, path):
        self.image = img
        self.overlays = []
        self.selected_overlay = None
        self.undo_stack = []
        self.redo_stack = []
        self.current_path = path
        self.dirty = False
        self.set_tool("select")
        self.fit_to_window()
        self.redraw()
        self.update_title()

    def save(self, save_as=False):
        if self.image is None:
            messagebox.showinfo("Save", "Nothing to save yet.")
            return
        self.commit_overlays(push_history=True)
        path = self.current_path
        if save_as or not path:
            initial = os.path.basename(path) if path else "untitled.png"
            path = filedialog.asksaveasfilename(defaultextension=".png",
                                                 filetypes=SAVE_FILETYPES,
                                                 initialfile=initial)
            if not path:
                return
        ext = os.path.splitext(path)[1].lower()
        out = self.image
        if ext in ALPHA_LESS_EXT and out.mode == "RGBA":
            bg = Image.new("RGB", out.size, (255, 255, 255))
            bg.paste(out, mask=out.split()[3])
            out = bg
        elif ext == ".gif":
            out = out.convert("RGB")
        try:
            out.save(path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        self.current_path = path
        self.dirty = False
        self.update_title()
        self.status.set(f"Saved to {path}")

    def paste_from_clipboard(self):
        img = None
        try:
            img = ImageGrab.grabclipboard()
        except Exception:
            img = None

        if isinstance(img, Image.Image):
            img = img.convert("RGBA")
            if self.image is None:
                self.new_document_from_image(img, None)
            else:
                self.push_undo()
                x = max(0, (self.image.width - img.width) // 2)
                y = max(0, (self.image.height - img.height) // 2)
                ov = Overlay("image", x, y, image=img)
                self.overlays.append(ov)
                self.selected_overlay = ov
                self.set_tool("select")
                self.redraw()
            self.status.set("Pasted image from clipboard.")
            return

        if isinstance(img, list) and img:
            for p in img:
                ext = os.path.splitext(str(p))[1].lower()
                if ext in (".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".gif"):
                    self.open_path(p)
                    return
            messagebox.showinfo("Paste", "No supported image file found on clipboard.")
            return

        text = None
        try:
            text = self.clipboard_get()
        except Exception:
            text = None
        if text:
            if self.image is None:
                messagebox.showinfo("Paste", "Open or create an image first, then paste text onto it.")
                return
            self.push_undo()
            ov = Overlay("text", self.image.width // 4, self.image.height // 4,
                         text=text, color=self.pen_color)
            self.overlays.append(ov)
            self.selected_overlay = ov
            self.set_tool("text")
            self.redraw()
            self.status.set("Pasted text from clipboard as a text layer.")
            return

        messagebox.showinfo("Paste", "Clipboard is empty or contains unsupported content.")

    # -------------------------------------------------------------- undo
    def _snapshot(self):
        return {
            "image": self.image.copy() if self.image is not None else None,
            "overlays": [o.clone() for o in self.overlays],
        }

    def push_undo(self):
        if self.image is None:
            return
        self.undo_stack.append(self._snapshot())
        if len(self.undo_stack) > MAX_HISTORY:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.dirty = True
        self.update_title()

    def _restore(self, snap):
        self.image = snap["image"]
        self.overlays = [o.clone() for o in snap["overlays"]]
        self.selected_overlay = None
        self.redraw()

    def undo(self):
        if not self.undo_stack:
            self.status.set("Nothing to undo.")
            return
        self.redo_stack.append(self._snapshot())
        snap = self.undo_stack.pop()
        self._restore(snap)
        self.status.set("Undo.")

    def redo(self):
        if not self.redo_stack:
            self.status.set("Nothing to redo.")
            return
        self.undo_stack.append(self._snapshot())
        snap = self.redo_stack.pop()
        self._restore(snap)
        self.status.set("Redo.")

    # ---------------------------------------------------------- overlays
    def render_composite(self, base=None):
        img = (base if base is not None else self.image).copy()
        draw = ImageDraw.Draw(img)
        for ov in self.overlays:
            if ov.kind == "text":
                fnt = get_font(ov.font_family, ov.font_size)
                draw.text((ov.x, ov.y), ov.text, fill=ov.color, font=fnt)
            elif ov.kind == "image" and ov.image is not None:
                img.paste(ov.image, (int(ov.x), int(ov.y)), ov.image)
        return img

    def commit_overlays(self, push_history=True):
        if not self.overlays or self.image is None:
            return
        if push_history:
            self.push_undo()
        self.image = self.render_composite()
        self.overlays = []
        self.selected_overlay = None

    def delete_selected_overlay(self):
        if self.selected_overlay is None:
            return
        self.push_undo()
        self.overlays = [o for o in self.overlays if o.id != self.selected_overlay.id]
        self.selected_overlay = None
        self.redraw()

    # -------------------------------------------------------------- tool
    def set_tool(self, name):
        if self.tool.get() == "crop" and name != "crop":
            self.cancel_crop()
        self.tool.set(name)
        for val, b in self.tool_buttons.items():
            b.state(["pressed"] if val == name else ["!pressed"])
        self.build_side_panel()
        cursors = {"select": "arrow", "pen": "pencil", "eraser": "dotbox",
                   "text": "xterm", "crop": "crosshair"}
        self.canvas.configure(cursor=cursors.get(name, "arrow"))

    def build_side_panel(self):
        for w in self.side_panel.winfo_children():
            w.destroy()
        tool = self.tool.get()
        p = self.side_panel

        if tool == "select":
            ttk.Label(p, text="Select / Move", font=("", 11, "bold")).pack(anchor="w")
            ttk.Label(p, wraplength=220, justify="left",
                      text="Click a text or pasted-image layer to select and drag it. "
                           "Double-click text to edit it. Press Delete to remove the "
                           "selected layer.").pack(anchor="w", pady=6)

        elif tool == "pen":
            ttk.Label(p, text="Pen", font=("", 11, "bold")).pack(anchor="w")
            ttk.Label(p, text="Color").pack(anchor="w", pady=(10, 2))
            self.pen_color_btn = tk.Button(p, text="        ", bg=self.pen_color,
                                            command=self.pick_pen_color)
            self.pen_color_btn.pack(anchor="w")
            ttk.Label(p, text="Thickness").pack(anchor="w", pady=(10, 2))
            ttk.Scale(p, from_=1, to=60, variable=self.pen_size, orient="horizontal").pack(fill="x")
            ttk.Label(p, textvariable=self.pen_size).pack(anchor="w")

        elif tool == "eraser":
            ttk.Label(p, text="Eraser", font=("", 11, "bold")).pack(anchor="w")
            ttk.Label(p, text="Thickness").pack(anchor="w", pady=(10, 2))
            ttk.Scale(p, from_=5, to=150, variable=self.eraser_size, orient="horizontal").pack(fill="x")
            ttk.Label(p, textvariable=self.eraser_size).pack(anchor="w")
            ttk.Label(p, wraplength=220, justify="left",
                      text="Erases to transparency. Save as PNG/WebP/GIF to keep "
                           "transparency; JPG/BMP will fill erased areas with white.").pack(anchor="w", pady=10)

        elif tool == "text":
            ttk.Label(p, text="Text", font=("", 11, "bold")).pack(anchor="w")
            ttk.Label(p, wraplength=220, justify="left",
                      text="Click anywhere on the image to add a text layer. "
                           "Click existing text to drag it, double-click to edit "
                           "its content, font, size or color.").pack(anchor="w", pady=6)
            ttk.Button(p, text="Add Text at Center", command=self.add_text_at_center).pack(anchor="w", pady=6)

        elif tool == "crop":
            ttk.Label(p, text="Crop", font=("", 11, "bold")).pack(anchor="w")
            ttk.Label(p, wraplength=220, justify="left",
                      text="Drag a rectangle on the image, then Apply.").pack(anchor="w", pady=6)
            row = ttk.Frame(p)
            row.pack(anchor="w", pady=6)
            ttk.Button(row, text="Apply", command=self.apply_crop).pack(side="left", padx=(0, 6))
            ttk.Button(row, text="Cancel", command=lambda: self.set_tool("select")).pack(side="left")

    def pick_pen_color(self):
        rgb, hexval = colorchooser.askcolor(color=self.pen_color, parent=self)
        if hexval:
            self.pen_color = hexval
            self.pen_color_btn.configure(bg=hexval)

    def add_text_at_center(self):
        if self.image is None:
            return
        x, y = self.image.width // 2 - 60, self.image.height // 2 - 20
        self._open_text_dialog(x, y)

    def _open_text_dialog(self, x, y, overlay=None):
        dlg = TextDialog(self, overlay=overlay)
        result = dlg.result
        if not result:
            return
        if result["action"] == "delete":
            self.push_undo()
            self.overlays = [o for o in self.overlays if o.id != overlay.id]
            self.selected_overlay = None
            self.redraw()
            return
        self.push_undo()
        if overlay is None:
            ov = Overlay("text", x, y, text=result["text"], font_family=result["font_family"],
                         font_size=result["font_size"], color=result["color"])
            self.overlays.append(ov)
            self.selected_overlay = ov
        else:
            overlay.text = result["text"]
            overlay.font_family = result["font_family"]
            overlay.font_size = result["font_size"]
            overlay.color = result["color"]
        self.redraw()

    # ------------------------------------------------------------ image ops
    def _require_image(self):
        if self.image is None:
            messagebox.showinfo(APP_TITLE, "Open or create an image first.")
            return False
        return True

    def flip_horizontal(self):
        if not self._require_image():
            return
        self.push_undo()
        if self.overlays:
            self.image = self.render_composite()
            self.overlays = []
        self.image = self.image.transpose(Image.FLIP_LEFT_RIGHT)
        self.fit_to_window()
        self.redraw()

    def flip_vertical(self):
        if not self._require_image():
            return
        self.push_undo()
        if self.overlays:
            self.image = self.render_composite()
            self.overlays = []
        self.image = self.image.transpose(Image.FLIP_TOP_BOTTOM)
        self.fit_to_window()
        self.redraw()

    def rotate_left(self):
        if not self._require_image():
            return
        self.push_undo()
        if self.overlays:
            self.image = self.render_composite()
            self.overlays = []
        self.image = self.image.transpose(Image.ROTATE_90)
        self.fit_to_window()
        self.redraw()

    def rotate_right(self):
        if not self._require_image():
            return
        self.push_undo()
        if self.overlays:
            self.image = self.render_composite()
            self.overlays = []
        self.image = self.image.transpose(Image.ROTATE_270)
        self.fit_to_window()
        self.redraw()

    def apply_crop(self):
        if not self._crop_rect:
            messagebox.showinfo("Crop", "Drag a rectangle on the image first.")
            return
        x0, y0, x1, y1 = self._crop_rect
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(self.image.width, int(x1)), min(self.image.height, int(y1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            messagebox.showinfo("Crop", "Selection is too small.")
            return
        self.push_undo()
        if self.overlays:
            self.image = self.render_composite()
            self.overlays = []
        self.image = self.image.crop((x0, y0, x1, y1))
        self.cancel_crop()
        self.set_tool("select")
        self.fit_to_window()
        self.redraw()

    def cancel_crop(self):
        self._crop_rect = None
        self._crop_start = None
        if self._crop_canvas_id:
            self.canvas.delete(self._crop_canvas_id)
            self._crop_canvas_id = None

    # --------------------------------------------------------- adjustments
    def open_adjust_panel(self):
        if not self._require_image():
            return
        if self.overlays:
            self.commit_overlays(push_history=True)
        self._adjust_base = self.image.copy()

        win = tk.Toplevel(self)
        win.title("Adjustments")
        win.resizable(False, False)
        win.transient(self)

        vars_ = {
            "brightness": tk.DoubleVar(value=1.0),
            "contrast": tk.DoubleVar(value=1.0),
            "saturation": tk.DoubleVar(value=1.0),
            "hue": tk.DoubleVar(value=0.0),
            "sharpness": tk.DoubleVar(value=1.0),
            "blur": tk.DoubleVar(value=0.0),
        }
        ranges = {
            "brightness": (0.0, 2.0),
            "contrast": (0.0, 2.0),
            "saturation": (0.0, 2.0),
            "hue": (-180.0, 180.0),
            "sharpness": (0.0, 3.0),
            "blur": (0.0, 20.0),
        }

        def preview(*_):
            kwargs = {k: v.get() for k, v in vars_.items()}
            self.image = apply_adjustments(self._adjust_base, **kwargs)
            self.redraw()

        row = 0
        for key, label in (("brightness", "Brightness"), ("contrast", "Contrast"),
                            ("saturation", "Saturation"), ("hue", "Hue"),
                            ("sharpness", "Sharpness"), ("blur", "Blur")):
            ttk.Label(win, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            lo, hi = ranges[key]
            s = ttk.Scale(win, from_=lo, to=hi, variable=vars_[key], orient="horizontal",
                          length=220, command=preview)
            s.grid(row=row, column=1, padx=8, pady=4)
            row += 1

        def do_reset():
            for k, v in vars_.items():
                v.set(0.0 if k == "hue" else 1.0 if k != "blur" else 0.0)
            preview()

        def do_apply():
            self.push_undo()
            # push_undo snapshots current (already-adjusted) image as if it
            # were the prior state, which is wrong -- fix by re-baselining:
            # replace the just-pushed snapshot's image with the pre-adjust one.
            self.undo_stack[-1]["image"] = self._adjust_base.copy()
            self.dirty = True
            self.update_title()
            win.destroy()

        def do_cancel():
            self.image = self._adjust_base
            self.redraw()
            win.destroy()

        btns = ttk.Frame(win)
        btns.grid(row=row, column=0, columnspan=2, pady=10)
        ttk.Button(btns, text="Reset", command=do_reset).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=do_cancel).pack(side="left", padx=4)
        ttk.Button(btns, text="Apply", command=do_apply).pack(side="left", padx=4)

        win.protocol("WM_DELETE_WINDOW", do_cancel)

    # ------------------------------------------------------------- canvas
    def fit_to_window(self):
        if self.image is None:
            return
        self.update_idletasks()
        cw = max(self.canvas.winfo_width(), 10)
        ch = max(self.canvas.winfo_height(), 10)
        iw, ih = self.image.size
        self.scale = max(0.01, min(cw / iw, ch / ih, 1.0)) if (iw > cw or ih > ch) else min(cw / iw, ch / ih)
        self.scale = min(self.scale, 4.0)
        disp_w, disp_h = iw * self.scale, ih * self.scale
        self.off_x = (cw - disp_w) / 2
        self.off_y = (ch - disp_h) / 2

    def on_canvas_configure(self, event):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._on_resize_settled)

    def _on_resize_settled(self):
        self._resize_job = None
        if self.image is not None:
            self.fit_to_window()
            self.redraw()

    def img_to_canvas(self, x, y):
        return self.off_x + x * self.scale, self.off_y + y * self.scale

    def canvas_to_img(self, cx, cy):
        return (cx - self.off_x) / self.scale, (cy - self.off_y) / self.scale

    def redraw(self):
        self.canvas.delete("all")
        if self.image is None:
            self.canvas.create_text(20, 20, anchor="nw", fill="#bbbbbb",
                                     text="No image open. File > Open, or Ctrl+V to paste.")
            return
        composite = self.render_composite() if self.overlays else self.image
        iw, ih = composite.size
        disp_w, disp_h = max(1, round(iw * self.scale)), max(1, round(ih * self.scale))
        resample = Image.NEAREST if self.scale >= 1 else Image.BOX
        disp = composite.resize((disp_w, disp_h), resample)
        self.tk_photo = ImageTk.PhotoImage(disp)
        self.canvas.create_image(self.off_x, self.off_y, anchor="nw", image=self.tk_photo)
        # subtle border around the canvas so users see the image edges
        self.canvas.create_rectangle(self.off_x, self.off_y, self.off_x + disp_w, self.off_y + disp_h,
                                      outline="#555555")
        if self.selected_overlay is not None and self.selected_overlay in self.overlays:
            l, t, r, b = self.selected_overlay.bbox()
            cl, ct = self.img_to_canvas(l, t)
            cr, cb = self.img_to_canvas(r, b)
            self.canvas.create_rectangle(cl, ct, cr, cb, outline="#00aaff", width=2, dash=(4, 2))
        self.status.set(self._status_text())

    def _status_text(self):
        if self.image is None:
            return "No image open."
        w, h = self.image.size
        name = os.path.basename(self.current_path) if self.current_path else "Untitled"
        return f"{name}  |  {w} x {h} px  |  tool: {self.tool.get()}  |  zoom: {round(self.scale * 100)}%"

    # ------------------------------------------------------- mouse events
    def on_canvas_motion(self, event):
        if self.image is None:
            return
        ix, iy = self.canvas_to_img(event.x, event.y)
        w, h = self.image.size
        if 0 <= ix < w and 0 <= iy < h:
            self.status.set(self._status_text() + f"  |  x:{int(ix)} y:{int(iy)}")

    def on_canvas_press(self, event):
        if self.image is None:
            return
        tool = self.tool.get()
        ix, iy = self.canvas_to_img(event.x, event.y)

        if tool in ("pen", "eraser"):
            self.push_undo()
            self._drawing = True
            self._last_point = (ix, iy)
            self._draw_dot(ix, iy)
            self.redraw()
            return

        if tool == "text":
            hit = self._hit_test(ix, iy)
            if hit is not None:
                self.selected_overlay = hit
                self._drag_overlay = hit
                self._drag_offset = (ix - hit.x, iy - hit.y)
                self.redraw()
            else:
                self._open_text_dialog(ix, iy)
            return

        if tool == "select":
            hit = self._hit_test(ix, iy)
            self.selected_overlay = hit
            if hit is not None:
                self._drag_overlay = hit
                self._drag_offset = (ix - hit.x, iy - hit.y)
            self.redraw()
            return

        if tool == "crop":
            self._crop_start = (ix, iy)
            self._crop_rect = (ix, iy, ix, iy)
            return

    def on_canvas_drag(self, event):
        if self.image is None:
            return
        tool = self.tool.get()
        ix, iy = self.canvas_to_img(event.x, event.y)
        w, h = self.image.size
        ix = max(0, min(w, ix))
        iy = max(0, min(h, iy))

        if tool in ("pen", "eraser") and self._drawing:
            self._draw_line(self._last_point, (ix, iy))
            self._last_point = (ix, iy)
            self.redraw()
            return

        if tool in ("select", "text") and self._drag_overlay is not None:
            self._drag_overlay.x = ix - self._drag_offset[0]
            self._drag_overlay.y = iy - self._drag_offset[1]
            self.redraw()
            return

        if tool == "crop" and self._crop_start is not None:
            x0, y0 = self._crop_start
            self._crop_rect = (x0, y0, ix, iy)
            self._draw_crop_overlay()
            return

    def on_canvas_release(self, event):
        tool = self.tool.get()
        if tool in ("pen", "eraser"):
            self._drawing = False
            self._last_point = None
        if tool in ("select", "text"):
            self._drag_overlay = None
            self._drag_offset = None
        if tool == "crop":
            self._crop_start = None

    def on_canvas_double_click(self, event):
        if self.image is None or self.tool.get() not in ("text", "select"):
            return
        ix, iy = self.canvas_to_img(event.x, event.y)
        hit = self._hit_test(ix, iy)
        if hit is not None and hit.kind == "text":
            self._open_text_dialog(hit.x, hit.y, overlay=hit)

    def _hit_test(self, ix, iy):
        for ov in reversed(self.overlays):
            l, t, r, b = ov.bbox()
            if l <= ix <= r and t <= iy <= b:
                return ov
        return None

    def _draw_crop_overlay(self):
        if self._crop_canvas_id:
            self.canvas.delete(self._crop_canvas_id)
        x0, y0, x1, y1 = self._crop_rect
        cx0, cy0 = self.img_to_canvas(x0, y0)
        cx1, cy1 = self.img_to_canvas(x1, y1)
        self.redraw()
        self._crop_canvas_id = self.canvas.create_rectangle(cx0, cy0, cx1, cy1,
                                                              outline="#ffdd00", width=2, dash=(5, 3))

    def _draw_dot(self, ix, iy):
        tool = self.tool.get()
        size = self.pen_size.get() if tool == "pen" else self.eraser_size.get()
        r = size / 2
        draw = ImageDraw.Draw(self.image)
        fill = self._rgba_fill(tool)
        draw.ellipse((ix - r, iy - r, ix + r, iy + r), fill=fill)

    def _draw_line(self, p0, p1):
        tool = self.tool.get()
        size = self.pen_size.get() if tool == "pen" else self.eraser_size.get()
        draw = ImageDraw.Draw(self.image)
        fill = self._rgba_fill(tool)
        draw.line([p0, p1], fill=fill, width=max(1, int(size)))
        self._draw_dot(*p1)

    def _rgba_fill(self, tool):
        if tool == "eraser":
            return (0, 0, 0, 0)
        r, g, b = ImageColor.getrgb(self.pen_color)
        return (r, g, b, 255)

    # ------------------------------------------------------------- misc
    def update_title(self):
        name = os.path.basename(self.current_path) if self.current_path else "Untitled"
        star = "*" if self.dirty else ""
        self.title(f"{name}{star} - {APP_TITLE}")

    def show_about(self):
        messagebox.showinfo("About", f"{APP_TITLE}\n\nA simple, light image viewer/editor.\n"
                                      "Built with Python, Tkinter and Pillow.")

    def on_close(self):
        if self.dirty:
            resp = messagebox.askyesnocancel(APP_TITLE, "You have unsaved changes. Save before exiting?")
            if resp is None:
                return
            if resp:
                self.save()
                if self.dirty:  # save was cancelled
                    return
        self.destroy()


def main():
    app = ImageRedactorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
