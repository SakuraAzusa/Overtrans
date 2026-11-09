"""窗口图标：exe 图标在打包时嵌入，运行时窗口图标从 assets 读"""
import os

from .paths import APP_DIR, bundle_dir


def icon_path():
    for base in (APP_DIR, bundle_dir()):
        p = os.path.join(base, "assets", "overtrans.ico")
        if os.path.exists(p):
            return p
    return None


def png_path():
    for base in (APP_DIR, bundle_dir()):
        p = os.path.join(base, "assets", "overtrans.png")
        if os.path.exists(p):
            return p
    return None


def apply_icon(win):
    p = icon_path()
    if p:
        try:
            win.iconbitmap(p)
        except Exception:
            pass


def logo_photo(master, size=(64, 64)):
    """引导页标题旁的圆形小图；失败返回 None"""
    try:
        from PIL import Image, ImageTk
        p = png_path()
        if not p:
            return None
        im = Image.open(p).convert("RGBA").resize((size[0], size[1]), Image.LANCZOS)
        return ImageTk.PhotoImage(im, master=master)
    except Exception:
        return None
