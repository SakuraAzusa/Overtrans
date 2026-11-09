"""首次启动引导：选 OCR 引擎包 + 选翻译服务 + 校准采集区"""
import tkinter as tk
from tkinter import messagebox

BG = "#eef1f5"
CARD = "#ffffff"
TXT = "#1f2937"
SUB = "#6b7280"
ACCENT = "#2563eb"
OK = "#16a34a"
WARN = "#b45309"
FONT = ("Microsoft YaHei UI", 10)
FONT_S = ("Microsoft YaHei UI", 9)
FONT_T = ("Microsoft YaHei UI", 13, "bold")

OCR_CHOICES = [
    ("ja", "仅日语 / 英语（推荐）",
     "模型约 11MB，每帧 OCR 约 1.0~1.3 秒，内存占用最低\n"
     "适合：只逛日文/英文频道的人，或电脑配置一般"),
    ("ja+ko", "日语 / 英语 / 韩语",
     "多加载 13MB 模型，每帧约多 40% 时间，内存多约 60MB\n"
     "适合：常逛韩文频道。可以随时在设置里切换"),
]

API_CHOICES = [
    ("microsoft", "Microsoft 翻译（免费 · 无需 Key）",
     "质量一般，胜在开箱即用、不用注册。接口失效时自动换 MyMemory"),
    ("mymemory", "MyMemory（免费 · 无需 Key）",
     "质量一般，额度也小（约 5000 字符/天），只适合当兜底"),
    ("openai", "用自己的 API（DeepSeek / 硅基流动 / 智谱 …）",
     "质量最好，需要自己填 Key。下一步到「设置」里选服务商填 Key"),
]


class Onboarding(tk.Toplevel):
    def __init__(self, master, cfg, on_done=None):
        super().__init__(master)
        self.cfg = cfg
        self.on_done = on_done
        self.title("首次使用 · Overtrans")
        try:
            from .icons import apply_icon
            apply_icon(self)
        except Exception:
            pass
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.ocr_var = tk.StringVar(value="ja" if "ko" not in cfg.get("ocr_langs", ["ja"]) else "ja+ko")
        self.api_var = tk.StringVar(value=cfg.get("provider", "microsoft"))
        if self.api_var.get() not in ("microsoft", "mymemory", "openai"):
            self.api_var.set("microsoft")

        self._build()
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")

    def _build(self):
        head = tk.Frame(self, bg=CARD)
        head.pack(fill="x")
        row = tk.Frame(head, bg=CARD)
        row.pack(fill="x", padx=18, pady=(14, 12))
        try:
            from .icons import logo_photo
            self._logo = logo_photo(self, (56, 56))
            if self._logo:
                tk.Label(row, image=self._logo, bg=CARD).pack(side="left", padx=(2, 12))
        except Exception:
            pass
        col = tk.Frame(row, bg=CARD)
        col.pack(side="left")
        tk.Label(col, text="欢迎使用 Overtrans", bg=CARD, fg=TXT, font=FONT_T).pack(anchor="w")
        tk.Label(col, text="游戏聊天实时翻译 · 本地 OCR，截图不上传",
                 bg=CARD, fg=SUB, font=FONT_S).pack(anchor="w", pady=(2, 0))

        # --- 第一步：OCR ---
        box = tk.Frame(self, bg=CARD)
        box.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(box, text="① 选识别语言",
                 bg=CARD, fg=TXT, font=(FONT[0], 10, "bold")).pack(anchor="w", padx=6, pady=(6, 4))
        for val, label, desc in OCR_CHOICES:
            row = tk.Frame(box, bg=CARD)
            row.pack(fill="x", padx=6, pady=2)
            tk.Radiobutton(row, text=label, value=val, variable=self.ocr_var, bg=CARD, fg=TXT,
                           font=FONT, activebackground=CARD, selectcolor=CARD,
                           highlightthickness=0, anchor="w").pack(anchor="w")
            tk.Label(row, text=desc, bg=CARD, fg=SUB, font=("Microsoft YaHei UI", 8),
                     justify="left").pack(anchor="w", padx=(22, 0))

        try:
            from .enginepack import ko_installed
            if not ko_installed():
                tk.Label(box, text="韩语模型尚未安装，选第二项后会提示你去设置里安装",
                         bg=CARD, fg=WARN, font=("Microsoft YaHei UI", 8)).pack(
                    anchor="w", padx=28, pady=(2, 0))
        except Exception:
            pass

        # --- 第二步：翻译服务 ---
        box2 = tk.Frame(self, bg=CARD)
        box2.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(box2, text="② 选翻译服务",
                 bg=CARD, fg=TXT, font=(FONT[0], 10, "bold")).pack(anchor="w", padx=6, pady=(6, 4))
        for val, label, desc in API_CHOICES:
            row = tk.Frame(box2, bg=CARD)
            row.pack(fill="x", padx=6, pady=2)
            tk.Radiobutton(row, text=label, value=val, variable=self.api_var, bg=CARD, fg=TXT,
                           font=FONT, activebackground=CARD, selectcolor=CARD,
                           highlightthickness=0, anchor="w").pack(anchor="w")
            tk.Label(row, text=desc, bg=CARD, fg=SUB, font=("Microsoft YaHei UI", 8),
                     justify="left").pack(anchor="w", padx=(22, 0))

        # --- 第三步：采集区 ---
        box3 = tk.Frame(self, bg=CARD)
        box3.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(box3, text="③ 对准聊天区域", bg=CARD, fg=TXT,
                 font=(FONT[0], 10, "bold")).pack(anchor="w", padx=6, pady=(6, 2))
        tk.Label(box3, text="默认比例已经按游戏 UI 调好，多数情况直接能用。\n"
                            "如果你的窗口不是常见分辨率，点下面按钮截图后拖动两个框对准即可。",
                 bg=CARD, fg=SUB, font=("Microsoft YaHei UI", 8), justify="left").pack(anchor="w", padx=6)
        self.btn_cal = tk.Button(box3, text="现在去校准（需要先开着游戏）", command=self._calibrate,
                                 bg="#eef2ff", fg=ACCENT, font=FONT_S, bd=0, padx=12, pady=6,
                                 activebackground="#e0e7ff", cursor="hand2")
        self.btn_cal.pack(anchor="w", padx=6, pady=(6, 10))

        foot = tk.Frame(self, bg=CARD)
        foot.pack(fill="x", pady=(4, 0))
        self.hint = tk.Label(foot, text="", bg=CARD, fg=SUB, font=FONT_S)
        self.hint.pack(side="left", padx=20)
        tk.Button(foot, text=" 开始使用 ", command=self._done, bg=ACCENT, fg="#ffffff",
                  font=FONT, bd=0, padx=18, pady=8, activebackground="#1d4ed8",
                  cursor="hand2").pack(side="right", padx=18, pady=14)

    def _calibrate(self):
        from .win_track import find_window
        if not find_window(self.cfg.get("window_title", ""))[0]:
            messagebox.showwarning(
                "没找到游戏窗口",
                f"没有找到标题包含「{self.cfg.get('window_title', 'OverField')}」的窗口。\n\n"
                "先把游戏开起来，进到有聊天的画面，再点这个按钮。", parent=self)
            return
        from .calibrate import CalibrateWindow
        CalibrateWindow(self, self.cfg)

    def _done(self):
        want_ko = self.ocr_var.get() == "ja+ko"
        if want_ko:
            try:
                from .enginepack import ko_installed
                if not ko_installed():
                    messagebox.showinfo(
                        "还需要装韩语引擎",
                        "先把基础功能跑起来，韩语模型到「设置 → 识别引擎 → 韩语引擎」里点安装。\n"
                        "（也可以直接把 onnx 文件放到程序目录的 models\\ 下）", parent=self)
            except Exception:
                pass
        self.cfg.set("ocr_langs", ["ja"] + (["ko"] if want_ko else []))
        self.cfg.set("provider", self.api_var.get())
        if self.ocr_var.get() == "ja":
            self.cfg.set("ko_mode", False)
        self.cfg.set("first_run", False)
        if self.on_done:
            try:
                self.on_done()
            except Exception:
                pass
        self.destroy()
