"""框选校准：把游戏窗口截图铺在画布上，拖动两个采集框，存成窗口内比例

为什么用比例不用绝对坐标：游戏窗口可以移动、最大化、改大小，
按客户区百分比存下来，换窗口尺寸时采集区跟着走。
"""
import threading
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageTk

from .capture import ScreenGrabber
from .gamepacks import zones_for
from .parser import build_messages
from .win_track import find_window, get_client_rect

BG = "#eef1f5"
CARD = "#ffffff"
TXT = "#1f2937"
SUB = "#6b7280"
OK = "#16a34a"
WARN = "#b45309"
FONT = ("Microsoft YaHei UI", 10)
FONT_S = ("Microsoft YaHei UI", 9)

ZONE_STYLE = {
    "chat": ("聊天面板", "#2563eb"),
    "bottom": ("底部预览", "#0d9488"),
}


class CalibrateWindow(tk.Toplevel):
    """两个可拖动 / 可缩放的采集框"""

    HANDLE = 9
    MAX_W, MAX_H = 1040, 660

    def __init__(self, master, cfg, on_save=None):
        super().__init__(master)
        self.cfg = cfg
        self.on_save = on_save
        self.title("框选采集区 · Overtrans")
        try:
            from .icons import apply_icon
            apply_icon(self)
        except Exception:
            pass
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)

        self.client = None
        self.img = None            # 客户区原图
        self.scale = 1.0
        self.rects = {}            # key -> [x0, y0, x1, y1]（画布坐标）
        self.disp_w = self.disp_h = 1   # 画布上图片的实际像素尺寸
        self.items = {}
        self._drag = None
        self._busy = False

        self._build()
        self.after(120, self._grab)

    # ---------------- 界面 ----------------
    def _build(self):
        tk.Label(self, text="拖动蓝框（聊天面板）和青框（底部预览）对准游戏里的聊天区域，"
                            "拖角可以改大小", bg=BG, fg=TXT, font=FONT).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(self, text="窗口移动 / 最大化后不用重新框：程序按窗口比例记位置。"
                            "框错了可以点「恢复默认」。",
                 bg=BG, fg=SUB, font=FONT_S).pack(anchor="w", padx=14, pady=(0, 8))

        wrap = tk.Frame(self, bg=CARD, bd=1, relief="solid")
        wrap.pack(padx=14)
        self.canvas = tk.Canvas(wrap, bg="#111827", highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=14, pady=12)
        self.btn_save = self._btn(bar, "保存并测试识别", self._save, "primary")
        self.btn_save.pack(side="left")
        self._btn(bar, "重新截图", self._grab).pack(side="left", padx=6)
        self._btn(bar, "恢复默认", self._reset).pack(side="left")
        self._btn(bar, "关闭", self.destroy).pack(side="right")

        self.status = tk.Label(self, text="正在截图…", bg=BG, fg=SUB, font=FONT_S, anchor="w")
        self.status.pack(fill="x", padx=14, pady=(0, 12))
        self.foot = tk.Label(self, text="", bg=BG, fg=SUB, font=FONT_S, anchor="w", justify="left")
        self.foot.pack(fill="x", padx=14, pady=(0, 10))

    def _btn(self, parent, text, cmd, kind="normal"):
        bg = {"primary": "#2563eb", "danger": "#dc2626"}.get(kind, "#ffffff")
        fg = "#ffffff" if kind in ("primary", "danger") else TXT
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, font=FONT_S,
                      bd=0, padx=14, pady=6, activebackground="#1d4ed8" if kind == "primary" else "#e5e7eb",
                      activeforeground=fg, cursor="hand2")
        return b

    # ---------------- 截图 ----------------
    def _grab(self):
        title = self.cfg.get("window_title", "OverField")
        hwnd, _ = find_window(title)
        if not hwnd:
            self.status.config(text=f"没找到游戏窗口（标题含「{title}」）", fg=WARN)
            messagebox.showwarning(
                "没找到游戏窗口",
                f"没有找到标题包含「{title}」的窗口。\n\n"
                "1) 先把游戏开起来并进入游戏画面\n"
                "2) 再点「重新截图」\n\n"
                "（窗口化 / 最大化都可以）", parent=self)
            return

        cl, ct, cr, cb = get_client_rect(hwnd)
        w, h = cr - cl, cb - ct
        if w < 200 or h < 200:
            self.status.config(text="游戏窗口太小，先最大化再试", fg=WARN)
            return

        try:
            shot, _ = ScreenGrabber().grab_window(hwnd, (cl, ct, cr, cb))
        except Exception as e:
            self.status.config(text=f"截图失败: {e}", fg=WARN)
            return
        if shot is None:
            self.status.config(text="截图失败（PrintWindow 返回空），试试把游戏切到前台", fg=WARN)
            return

        self.client = (w, h)
        self.img = shot.convert("RGB")
        self.scale = min(self.MAX_W / w, self.MAX_H / h, 1.0)
        disp = self.img.resize((max(1, int(w * self.scale)), max(1, int(h * self.scale))),
                               Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(disp)
        self.disp_w, self.disp_h = disp.width, disp.height
        self.canvas.config(width=disp.width, height=disp.height)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

        self._init_rects()
        self.status.config(text=f"已截图 {w}×{h}（显示比例 {self.scale:.0%}）", fg=SUB)
        self.foot.config(text="提示：只框「有字的那一列」，别把角色立绘、任务栏框进来，"
                              "框大了只会更慢、更容易误识别。")

    def _default_ratios(self):
        w, h = self.client
        return {k: list(r) for k, _, r, _ in zones_for(w, h)}

    def _init_rects(self):
        saved = self.cfg.get("zones") or {}
        ratios = self._default_ratios()
        self.canvas.delete("zone")
        self.rects.clear()
        cw, ch = self.disp_w, self.disp_h
        for key in ("chat", "bottom"):
            r = saved.get(key) or ratios.get(key)
            if not r or len(r) != 4:
                continue
            x0, y0 = r[0] * cw, r[1] * ch
            x1, y1 = r[2] * cw, r[3] * ch
            self.rects[key] = [x0, y0, x1, y1]
        self._redraw()

    def _reset(self):
        self.cfg.set("zones", {}, save=True)
        self._init_rects()
        self.status.config(text="已恢复默认比例", fg=SUB)

    # ---------------- 画框 ----------------
    def _redraw(self):
        self.canvas.delete("zone")
        for key, (x0, y0, x1, y1) in self.rects.items():
            label, color = ZONE_STYLE.get(key, (key, "#f59e0b"))
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=2,
                                         tags="zone", dash=() if key == "chat" else (6, 4))
            self.canvas.create_rectangle(x0, y0, x0 + 96, y0 - 20, fill=color, outline=color, tags="zone")
            self.canvas.create_text(x0 + 48, y0 - 10, text=label, fill="#ffffff",
                                    font=("Microsoft YaHei UI", 9, "bold"), tags="zone")
            # 四角把手
            for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                self.canvas.create_rectangle(hx - 4, hy - 4, hx + 4, hy + 4,
                                             fill="#ffffff", outline=color, tags="zone")

    # ---------------- 拖动 ----------------
    def _hit(self, x, y):
        for key, (x0, y0, x1, y1) in self.rects.items():
            h = self.HANDLE
            near = {
                "tl": abs(x - x0) <= h and abs(y - y0) <= h,
                "tr": abs(x - x1) <= h and abs(y - y0) <= h,
                "bl": abs(x - x0) <= h and abs(y - y1) <= h,
                "br": abs(x - x1) <= h and abs(y - y1) <= h,
            }
            for mode, ok in near.items():
                if ok:
                    return key, mode
            if x0 <= x <= x1 and y0 <= y <= y1:
                inner_x = min(x - x0, x1 - x)
                inner_y = min(y - y0, y1 - y)
                if inner_x < h:
                    return key, "l"
                if inner_x > (x1 - x0) - h:
                    return key, "r"
                if inner_y < h:
                    return key, "t"
                if inner_y > (y1 - y0) - h:
                    return key, "b"
                return key, "move"
        return None, None

    def _on_press(self, e):
        key, mode = self._hit(e.x, e.y)
        if not key:
            return
        self._drag = {"key": key, "mode": mode, "x": e.x, "y": e.y,
                      "rect": list(self.rects[key])}

    def _on_drag(self, e):
        if not self._drag:
            return
        d = self._drag
        x0, y0, x1, y1 = d["rect"]
        dx, dy = e.x - d["x"], e.y - d["y"]
        mode = d["mode"]
        if mode == "move":
            x0, x1, y0, y1 = x0 + dx, x1 + dx, y0 + dy, y1 + dy
        else:
            if "l" in mode:
                x0 += dx
            if "r" in mode:
                x1 += dx
            if "t" in mode:
                y0 += dy
            if "b" in mode:
                y1 += dy
        cw, ch = self.disp_w, self.disp_h
        x0, x1 = sorted((max(0, min(x0, cw - 1)), max(0, min(x1, cw - 1))))
        y0, y1 = sorted((max(0, min(y0, ch - 1)), max(0, min(y1, ch - 1))))
        if x1 - x0 < 20 or y1 - y0 < 20:
            return
        self.rects[d["key"]] = [x0, y0, x1, y1]
        self._drag["rect"] = [x0, y0, x1, y1]
        self._drag["x"], self._drag["y"] = e.x, e.y
        self._redraw()

    def _on_release(self, _e):
        self._drag = None

    # ---------------- 保存 ----------------
    def _ratios(self):
        cw, ch = self.disp_w, self.disp_h
        out = {}
        for key, (x0, y0, x1, y1) in self.rects.items():
            out[key] = [round(x0 / cw, 4), round(y0 / ch, 4),
                        round(x1 / cw, 4), round(y1 / ch, 4)]
        return out

    def _save(self):
        if not self.rects or self._busy:
            return
        self.cfg.set("zones", self._ratios(), save=True)
        self.status.config(text="已保存，正在测试识别…", fg=SUB)
        self._busy = True
        threading.Thread(target=self._test, daemon=True).start()

    def _test(self):
        try:
            w, h = self.client
            skip = self.cfg.get("skip_words", [])
            filter_cn = self.cfg.get("filter_chinese", True)
            ratios = self._ratios()
            from .ocr_engine import OcrEngine
            langs = self.cfg.get("ocr_langs", ["ja"])
            ko = bool(self.cfg.get("ko_mode", False))
            if not ko and "ko" in langs:
                langs = [x for x in langs if x != "ko"]
            eng = OcrEngine(threads=self.cfg.get("ocr", {}).get("threads", 3), langs=langs)

            parts, total_lines, total_msgs = [], 0, 0
            for key, label in (("chat", "聊天面板"), ("bottom", "底部预览")):
                r = ratios.get(key)
                if not r:
                    continue
                box = (int(r[0] * w), int(r[1] * h), int(r[2] * w), int(r[3] * h))
                crop = self.img.crop(box)
                lines = eng.recognize(crop)
                msgs = build_messages(lines, skip, filter_cn, [], frame_width=crop.width,
                                      scene_left=0.30 if key == "chat" else None, ko_mode=ko)
                total_lines += len(lines)
                total_msgs += len(msgs)
                parts.append(f"{label}：{len(lines)} 行 / {len(msgs)} 条"
                             + ("" if lines else "（没认到字）"))
            if total_lines == 0:
                tip = "一个字都没认到：可能框错位置、或游戏画面被遮挡。把游戏切到前台再点「保存并测试识别」。"
                color = WARN
            elif total_msgs == 0:
                tip = "认到字但没组成消息：框里可能只有昵称行或 UI 文字，试着把框往右/往下挪一点。"
                color = WARN
            else:
                tip = "识别正常。"
                color = OK
            self.after(0, lambda: self._done("　".join(parts) + "　·　" + tip, color, total_msgs))
        except Exception as e:
            self.after(0, lambda: self._done(f"测试失败：{e}", WARN, 0))

    def _done(self, text, color, ok_count):
        self._busy = False
        self.status.config(text=text, fg=color)
        if ok_count and self.on_save:
            try:
                self.on_save()
            except Exception:
                pass
