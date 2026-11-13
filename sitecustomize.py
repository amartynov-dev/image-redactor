"""Image Redactor startup extension: add resize controls and polish the UI.

The build workflow loads this module as a PyInstaller runtime hook, while normal
Python launches discover it through sitecustomize when the project is on sys.path.
"""


def _install_image_resize(root):
    """Add Image > Resize... and a quick toolbar button to the editor."""
    if getattr(root, "_image_resize_installed", False):
        return
    required = ("base_image", "annotation_layer", "overlays", "push_undo",
                "_bake_overlays", "fit_to_window", "redraw")
    if not all(hasattr(root, name) for name in required):
        return

    menu = root.cget("menu")
    if menu:
        menu = root.nametowidget(menu)
        menu_end = menu.index("end")
        for index in range((menu_end + 1) if menu_end is not None else 0):
            if menu.type(index) == "cascade" and menu.entrycget(index, "label") == "Image":
                image_menu = root.nametowidget(menu.entrycget(index, "menu"))
                image_menu.add_command(label="Resize...", command=lambda: _resize_dialog(root))
                break

    # The editor's first top-level frame is its toolbar; add an explicit,
    # labeled action so resize is discoverable without opening the Image menu.
    try:
        import tkinter.ttk as ttk
        toolbar = next(child for child in root.winfo_children()
                       if child.winfo_class() == "Frame")
        inner = next(child for child in toolbar.winfo_children()
                     if child.winfo_class() == "TFrame")
        ttk.Separator(inner, orient="vertical").pack(side="left", fill="y", padx=6, pady=4)
        ttk.Button(inner, text="↔  Resize", style="Resize.TButton",
                   command=lambda: _resize_dialog(root)).pack(side="left", padx=3, pady=4)
    except (StopIteration, AttributeError, RuntimeError):
        pass

    _apply_modern_style(root)
    root._image_resize_installed = True


def _apply_modern_style(root):
    """Apply a restrained, consistent dark palette and cleaner control spacing."""
    import tkinter as tk
    from tkinter import ttk

    palette = {
        "bg": "#171a21", "panel": "#20242d", "border": "#343a46",
        "text": "#edf0f5", "text_dim": "#a5adba", "accent": "#708cff",
        "accent_dark": "#5874e8", "btn": "#2b303b", "btn_hover": "#383f4d",
        "canvas_bg": "#11141a",
    }
    # Keep the editor's existing drawing routines and widgets in sync with the
    # refreshed palette, not just the new resize button.
    module = __import__(root.__class__.__module__, fromlist=["COLORS"])
    if hasattr(module, "COLORS"):
        module.COLORS.update(palette)
    root.configure(bg=palette["bg"])
    style = ttk.Style(root)
    style.configure("TFrame", background=palette["panel"])
    style.configure("TLabel", background=palette["panel"], foreground=palette["text"],
                    font=("Segoe UI", 9))
    style.configure("TButton", background=palette["btn"], foreground=palette["text"],
                    borderwidth=0, padding=(10, 7), font=("Segoe UI", 9))
    style.map("TButton", background=[("active", palette["btn_hover"]),
                                      ("pressed", palette["accent"])])
    style.configure("Toolbar.TButton", background=palette["btn"], foreground=palette["text"],
                    borderwidth=0, padding=(9, 7), font=("Segoe UI", 12))
    style.map("Toolbar.TButton", background=[("active", palette["btn_hover"])])
    style.configure("ToolbarSelected.TButton", background=palette["accent"],
                    foreground="white", borderwidth=0, padding=(9, 7),
                    font=("Segoe UI", 12))
    style.configure("Resize.TButton", background=palette["accent"], foreground="white",
                    borderwidth=0, padding=(12, 8), font=("Segoe UI", 9, "bold"))
    style.map("Resize.TButton", background=[("active", palette["accent_dark"])])
    style.configure("TSeparator", background=palette["border"])
    style.configure("TScale", background=palette["panel"], troughcolor=palette["btn"])

    def recolor(widget):
        try:
            if isinstance(widget, tk.Canvas):
                widget.configure(bg=palette["canvas_bg"])
            elif isinstance(widget, tk.Frame):
                widget.configure(bg=palette["panel"])
            elif isinstance(widget, tk.Menu):
                widget.configure(bg=palette["btn"], fg=palette["text"],
                                 activebackground=palette["accent"], activeforeground="white")
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            recolor(child)

    recolor(root)


def _resize_dialog(app):
    import tkinter as tk
    from tkinter import messagebox, ttk

    if app.base_image is None:
        messagebox.showinfo("Resize", "Open or create an image first.", parent=app)
        return

    original_width, original_height = app.base_image.size
    dialog = tk.Toplevel(app)
    dialog.title("Resize Image")
    dialog.resizable(False, False)
    dialog.transient(app)
    dialog.grab_set()
    form = ttk.Frame(dialog, padding=16)
    form.pack(fill="both", expand=True)

    width_var = tk.StringVar(value=str(original_width))
    height_var = tk.StringVar(value=str(original_height))
    keep_ratio = tk.BooleanVar(value=True)
    ttk.Label(form, text=f"Current size  ·  {original_width} × {original_height} px").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
    ttk.Label(form, text="Width").grid(row=1, column=0, sticky="e", padx=8, pady=5)
    width_entry = ttk.Entry(form, textvariable=width_var, width=14)
    width_entry.grid(row=1, column=1, sticky="w", padx=8, pady=5)
    ttk.Label(form, text="Height").grid(row=2, column=0, sticky="e", padx=8, pady=5)
    ttk.Entry(form, textvariable=height_var, width=14).grid(
        row=2, column=1, sticky="w", padx=8, pady=5)
    ttk.Checkbutton(form, text="Keep aspect ratio (width controls height)",
                    variable=keep_ratio).grid(row=3, column=0, columnspan=2,
                                               sticky="w", padx=8, pady=8)

    def apply():
        try:
            width = int(width_var.get())
            height = int(height_var.get())
            if not (1 <= width <= 8000 and 1 <= height <= 8000):
                raise ValueError
            if keep_ratio.get():
                height = max(1, round(width * original_height / original_width))
                if height > 8000:
                    raise ValueError
        except (TypeError, ValueError):
            messagebox.showerror(
                "Resize", "Enter whole-number dimensions from 1 to 8000 pixels.",
                parent=dialog)
            return

        try:
            from PIL import Image
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            annotations = (app._bake_overlays(app.annotation_layer)
                           if app.overlays else app.annotation_layer)
            resized_base = app.base_image.resize((width, height), resampling)
            resized_annotations = annotations.resize((width, height), resampling)
        except Exception as error:
            messagebox.showerror("Resize failed", str(error), parent=dialog)
            return

        app.push_undo()
        app.base_image = resized_base
        app.annotation_layer = resized_annotations
        app.overlays = []
        app.selected_overlay = None
        app.fit_to_window()
        app.redraw()
        app.update_title()
        app.status.set(f"Resized image to {width} × {height} px.")
        dialog.destroy()

    buttons = ttk.Frame(form)
    buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
    ttk.Button(buttons, text="Resize", style="Resize.TButton", command=apply).pack(
        side="left", padx=4)
    width_entry.focus_set()
    dialog.bind("<Return>", lambda _event: apply())
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.wait_window(dialog)


def _install_before_mainloop():
    """Install a Tk hook; only Image Redactor roots are affected."""
    try:
        import tkinter as tk
        original_mainloop = tk.Tk.mainloop
        if getattr(original_mainloop, "_image_resize_hook", False):
            return

        def mainloop(root, *args, **kwargs):
            _install_image_resize(root)
            return original_mainloop(root, *args, **kwargs)

        mainloop._image_resize_hook = True
        tk.Tk.mainloop = mainloop
    except Exception:
        # Do not prevent Python or the editor from starting if Tk is unavailable.
        pass


_install_before_mainloop()
