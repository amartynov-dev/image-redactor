"""Image Redactor startup extension: add a document resize command.

Python imports sitecustomize automatically when this project directory is on
sys.path. Keeping the extension here avoids changing the editor's large, single
file while allowing its existing document and undo APIs to handle resizing.
"""


def _install_image_resize(root):
    """Add Image > Resize... to an initialized Image Redactor window."""
    if getattr(root, "_image_resize_installed", False):
        return
    required = ("base_image", "annotation_layer", "overlays", "push_undo",
                "_bake_overlays", "fit_to_window", "redraw")
    if not all(hasattr(root, name) for name in required):
        return

    menu = root.cget("menu")
    if not menu:
        return
    menu = root.nametowidget(menu)
    image_menu = None
    for index in range(menu.index("end") + 1 if menu.index("end") is not None else 0):
        if menu.type(index) == "cascade" and menu.entrycget(index, "label") == "Image":
            image_menu = root.nametowidget(menu.entrycget(index, "menu"))
            break
    if image_menu is None:
        return

    image_menu.add_command(label="Resize...", command=lambda: _resize_dialog(root))
    root._image_resize_installed = True


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
    form = ttk.Frame(dialog, padding=12)
    form.pack(fill="both", expand=True)

    width_var = tk.StringVar(value=str(original_width))
    height_var = tk.StringVar(value=str(original_height))
    keep_ratio = tk.BooleanVar(value=True)
    ttk.Label(form, text=f"Current size: {original_width} x {original_height} px").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
    ttk.Label(form, text="Width:").grid(row=1, column=0, sticky="e", padx=6, pady=4)
    width_entry = ttk.Entry(form, textvariable=width_var, width=12)
    width_entry.grid(row=1, column=1, sticky="w", padx=6, pady=4)
    ttk.Label(form, text="Height:").grid(row=2, column=0, sticky="e", padx=6, pady=4)
    ttk.Entry(form, textvariable=height_var, width=12).grid(
        row=2, column=1, sticky="w", padx=6, pady=4)
    ttk.Checkbutton(form, text="Keep aspect ratio (width controls height)",
                    variable=keep_ratio).grid(row=3, column=0, columnspan=2,
                                               sticky="w", padx=6, pady=6)

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
            # Bake live overlays into a temporary annotation image so resizing
            # affects the complete document. Keep the current editable state
            # intact until both resized images have been created successfully.
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
        app.status.set(f"Resized image to {width} x {height} px.")
        dialog.destroy()

    buttons = ttk.Frame(form)
    buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
    ttk.Button(buttons, text="Resize", command=apply).pack(side="left", padx=4)
    width_entry.focus_set()
    dialog.bind("<Return>", lambda _event: apply())
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.wait_window(dialog)


def _install_before_mainloop():
    """Install a small Tk hook; only Image Redactor roots are affected."""
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
