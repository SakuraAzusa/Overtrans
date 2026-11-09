"""界面：控制台主窗 + 置顶悬浮译文窗 + 区域框选器"""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .logger import LOG
from .pipeline import Pipeline
from .translator import TranslateError, Translator

BG = "#f4f5f7"
CARD = "#ffffff"
LINE = "#e3e6ea"
TXT = "#1f2328"
SUB = "#6b7280"
ACCENT = "#2f6fed"
OK = "#16a34a"
WARN = "#dc2626"

F_BG = "#12161b"
F_HEAD = "#1b2028"
F_NICK = "#7d8896"
F_ORIG = "#9aa4b2"
F_TRANS = "#f2f5f8"
F_OK = "#3dbb6a"

UI_FONT = ("Microsoft YaHei UI", 10)
UI_FONT_S = ("Microsoft YaHei UI", 9)
MONO_JP = ("Yu Gothic UI", 10)

# 模型下拉预设（点「刷新列表」会从 /v1/models 拉全量覆盖）
# 服务商（显示名里带「免费」标识，用户一眼能看出哪些不用花钱/不用 Key）
PROVIDERS = [
    ("microsoft", "Microsoft 翻译（免费 · 无需 Key）"),
    ("mymemory", "MyMemory（免费 · 无需 Key · 有日限额）"),
    ("google", "Google 翻译（免费 · 国内需代理）"),
    ("openai", "OpenAI 兼容（DeepSeek / 硅基流动 / 智谱 / Kimi …）"),
    ("deepl", "DeepL（需 Key）"),
    ("none", "不翻译（只显示原文）"),
]

# OpenAI 兼容接口的常用服务商预设：名称 / base_url / 默认模型
API_PRESETS = [
    ("自定义", "", ""),
    ("DeepSeek 官方（需 Key）", "https://api.deepseek.com/v1", "deepseek-chat"),
    ("硅基流动 SiliconFlow（有免费模型）", "https://api.siliconflow.cn/v1",
     "Qwen/Qwen2.5-7B-Instruct"),
    ("智谱 GLM（glm-4-flash 免费）", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    ("月之暗面 Kimi（需 Key）", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ("阿里百炼（有免费额度）", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    ("OpenRouter（有 :free 模型）", "https://openrouter.ai/api/v1",
     "meta-llama/llama-3.1-8b-instruct:free"),
    ("本地 Ollama（零成本）", "http://localhost:11434/v1", "qwen2.5:7b"),
    ("本地 LM Studio", "http://localhost:1234/v1", "local-model"),
]

# OCR 引擎包：都打包进安装包，靠选择决定加载哪些模型
OCR_PACKS = [
    ("ja", "仅日语 / 英语", "约 11MB 模型 · 每帧 OCR ≈ 1.0~1.3 秒（推荐）"),
    ("ja+ko", "日语 / 英语 + 韩语", "多 13MB 模型 · 每帧约 +40% 时间 · 内存 +60MB"),
]

# 性能档位 -> ocr 配置
PERF_LEVELS = [
    ("saver", "省电"),
    ("balanced", "均衡"),
    ("accurate", "精准"),
]
PERF_DESC = {
    "saver": "最省 CPU，识别略慢（老机器、笔记本用这档）",
    "balanced": "默认，速度与占用平衡",
    "accurate": "识别最稳，吃 CPU（新机器用）",
}
PERF_MAP = {
    "saver": {"det_tiny": False, "threads": 1, "interval_ms": 900},
    "balanced": {"det_tiny": False, "threads": 2, "interval_ms": 600},
    "accurate": {"det_tiny": False, "threads": 3, "interval_ms": 500},
}

PRESET_MODELS = [
    "cn:deepseek-v4.1-flash",
    "cn:deepseek-v4-flash",
    "cn:deepseek-v4-pro",
    "cn:glm-5.3-flash",
    "cn:auto",
]


class ToolTip:
    """极简悬停提示：鼠标停 400ms 弹一个小窗"""

    def __init__(self, widget, text, delay=400):
        self.w = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.w.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.w.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _show(self):
        text = self.text() if callable(self.text) else self.text
        if self.tip or not text:
            return
        x = self.w.winfo_rootx() + 12
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        tip = tk.Toplevel(self.w)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip.attributes("-topmost", True)
        tk.Label(tip, text=text, bg="#1f2937", fg="#ffffff", justify="left",
                 font=("Microsoft YaHei UI", 9), padx=10, pady=7).pack()
        self.tip = tip

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ============================ 悬浮窗 ============================
class FloatWindow(tk.Toplevel):
    def __init__(self, master, cfg, on_close=None, on_ko=None, ko_state=None):
        super().__init__(master)
        self.cfg = cfg
        self.on_close = on_close
        self.on_ko = on_ko                 # 点「韩」时回调主窗口，返回最新状态
        self.ko_state = ko_state or (lambda: bool(cfg.get("ko_mode", False)))
        self.font_size = int(cfg.get("float", {}).get("font_size", 11))
        self.show_orig = True
        self._count = 0

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", float(cfg.get("float", {}).get("opacity", 0.9)))
        geo = cfg.get("float", {}).get("geometry", "420x560+1180+60")
        self.geometry(geo)
        self.configure(bg=F_BG)

        head = tk.Frame(self, bg=F_HEAD, height=30)
        head.pack(fill="x", side="top")
        head.pack_propagate(False)
        self.title_lbl = tk.Label(head, text="  聊天翻译", bg=F_HEAD, fg="#e6ecf3",
                                  font=("Microsoft YaHei UI", 9, "bold"))
        self.title_lbl.pack(side="left")
        self.stat_lbl = tk.Label(head, text="", bg=F_HEAD, fg=F_OK, font=("Microsoft YaHei UI", 8))
        self.stat_lbl.pack(side="left", padx=6)

        # 韩文开关：和主窗口那个勾选框是同一件事，点一下立即生效
        self.ko_lbl = tk.Label(head, text="韩", bg=F_HEAD, fg="#93a0ae",
                               font=("Microsoft YaHei UI", 9, "bold"), padx=7, cursor="hand2")
        self.ko_lbl.pack(side="right")
        self.ko_lbl.bind("<Button-1>", lambda e: self._toggle_ko())
        self.ko_lbl.bind("<Enter>", lambda e: self.ko_lbl.config(fg="#ffffff"))
        self.ko_lbl.bind("<Leave>", lambda e: self._sync_ko())
        ToolTip(self.ko_lbl, self._ko_tip_text)
        self._sync_ko()

        for txt, cmd in (("✕", self._close), ("—", self._min), ("A+", lambda: self._zoom(1)),
                         ("A-", lambda: self._zoom(-1)), ("清", self.clear)):
            b = tk.Label(head, text=txt, bg=F_HEAD, fg="#93a0ae", font=("Microsoft YaHei UI", 9),
                         padx=6, cursor="hand2")
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, c=cmd: c())
            b.bind("<Enter>", lambda e, w=b: w.config(fg="#ffffff"))
            b.bind("<Leave>", lambda e, w=b: w.config(fg="#93a0ae"))

        for w in (self, head, self.title_lbl):
            w.bind("<Button-1>", self._press)
            w.bind("<B1-Motion>", self._drag)

        self.text = tk.Text(self, bg=F_BG, fg=F_TRANS, bd=0, wrap="word",
                            font=(MONO_JP[0], self.font_size), padx=12, pady=10,
                            insertbackground=F_TRANS, selectbackground="#2f4f7a",
                            spacing1=1, spacing3=2, cursor="arrow")
        self.text.pack(fill="both", expand=True)
        self.text.tag_config("nick", foreground=F_NICK, font=(UI_FONT[0], max(8, self.font_size - 2)))
        self.text.tag_config("orig", foreground=F_ORIG, font=(MONO_JP[0], max(8, self.font_size - 1)))
        self.text.tag_config("trans", foreground=F_TRANS, font=(UI_FONT[0], self.font_size, "bold"))
        self.text.tag_config("time", foreground="#4b5563", font=("Consolas", 8))
        self.text.config(state="disabled")

        self.text.bind("<Button-3>", self._menu)
        self._menu_obj = tk.Menu(self, tearoff=0)
        self._menu_obj.add_command(label="复制全部译文", command=self.copy_all)
        self._menu_obj.add_command(label="清空", command=self.clear)
        self._menu_obj.add_separator()
        self._menu_obj.add_command(label="显示/隐藏原文", command=self.toggle_orig)
        self._menu_obj.add_command(label="隐藏悬浮窗", command=self._min)

        self._dx = self._dy = 0
        self._placeholder()

    # --- 韩文开关 ---
    def _ko_tip_text(self):
        rows = ["韩语发言的识别开关",
                "· 关闭：韩语全部丢弃，不占版面",
                "· 打开：识别并翻译韩语"]
        try:
            from .enginepack import ko_installed
            if not ko_installed():
                rows.append("你当前没有安装韩文识别引擎，到「设置」里安装后可用")
        except Exception:
            pass
        return "\n".join(rows)

    def _toggle_ko(self):
        if self.on_ko:
            on = self.on_ko()
        else:
            on = not self.ko_state()
        self._sync_ko(on)

    def _sync_ko(self, on=None):
        on = self.ko_state() if on is None else on
        self.ko_lbl.config(fg=F_OK if on else "#93a0ae")

    # --- 拖动 ---
    def _press(self, e):
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _drag(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _menu(self, e):
        self._menu_obj.tk_popup(e.x_root, e.y_root)

    def _close(self):
        self.cfg.set("float", {**self.cfg.get("float", {}), "geometry": self.geometry()})
        if self.on_close:
            self.on_close()
        self.destroy()

    def _min(self):
        self.cfg.set("float", {**self.cfg.get("float", {}), "geometry": self.geometry()})
        self.withdraw()

    def _zoom(self, d):
        self.font_size = max(8, min(22, self.font_size + d))
        conf = {**self.cfg.get("float", {}), "font_size": self.font_size, "geometry": self.geometry()}
        self.cfg.set("float", conf)
        self.text.config(font=(MONO_JP[0], self.font_size))
        self.text.tag_config("orig", font=(MONO_JP[0], max(8, self.font_size - 1)))
        self.text.tag_config("trans", font=(UI_FONT[0], self.font_size, "bold"))
        self.text.tag_config("nick", font=(UI_FONT[0], max(8, self.font_size - 2)))

    def _placeholder(self):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "\n  等待新消息…\n", ("time",))
        self.text.insert("end", "  框选游戏聊天栏后点击「开始监听」\n", ("orig",))
        self.text.config(state="disabled")
        self.text.see("end")

    def toggle_orig(self):
        self.show_orig = not self.show_orig
        self.set_status("原文" + ("开" if self.show_orig else "关"))

    def clear(self):
        self._count = 0
        self._placeholder()

    def set_status(self, s, color=F_OK):
        self.stat_lbl.config(text=s, fg=color)

    def add(self, items):
        """items: [(nick, orig, trans)]"""
        self.text.config(state="normal")
        if self._count == 0:
            self.text.delete("1.0", "end")
        ts = time.strftime("%H:%M")
        for nick, orig, trans in items:
            self.text.insert("end", f"{ts} ", ("time",))
            if nick:
                self.text.insert("end", f"{nick}\n", ("nick",))
            else:
                self.text.insert("end", "\n")
            self.text.insert("end", f"{trans}\n", ("trans",))
            # 中文消息不翻译，译文就是原文，别重复显示
            if self.show_orig and orig and orig.strip() != trans.strip():
                self.text.insert("end", f"{orig}\n", ("orig",))
            self.text.insert("end", "\n")
            self._count += 1
        # 控制总长度
        if int(self.text.index("end-1c").split(".")[0]) > 400:
            self.text.delete("1.0", "80.0")
        self.text.config(state="disabled")
        self.text.see("end")

    def copy_all(self):
        txt = self.text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(txt)
        self.set_status("已复制")


# ============================ 主程序 ============================
class App:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.q = queue.Queue()
        self.pipeline = None
        self.float_win = None
        self.translator = Translator(cfg)
        self._busy = False

        root.title("Overtrans · 游戏聊天翻译")
        try:
            from .icons import apply_icon
            apply_icon(root)
        except Exception:
            pass
        root.geometry("900x750")
        root.configure(bg=BG)
        root.minsize(800, 620)
        self._style()
        self._build()
        self._refresh_zone_label()

        root.protocol("WM_DELETE_WINDOW", self._on_quit)
        root.after(120, self._poll)
        threading.Thread(target=self._warmup, daemon=True).start()
        root.after(700, self._maybe_onboarding)

    def _maybe_onboarding(self):
        """首次启动：让你选 OCR 引擎包和翻译服务，顺便校准采集区"""
        if not self.cfg.get("first_run", False):
            return
        from .onboarding import Onboarding
        try:
            Onboarding(self.root, self.cfg, on_done=self._after_onboarding)
        except Exception as e:
            self._set_status(f"引导页打开失败：{e}", WARN)

    def _after_onboarding(self):
        try:
            self._load_settings()
            self.ko_chk_var.set(bool(self.cfg.get("ko_mode", False)))
            self.ko_chk.config(fg=OK if self.ko_chk_var.get() else SUB)
            self._refresh_zone_label()
            self._set_status("配置完成，点「开始监听」", OK)
        except Exception:
            pass

    # ---------- 样式 ----------
    def _style(self):
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", padding=(16, 7), font=UI_FONT, background="#e8eaee",
                    foreground="#40474f")
        s.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", ACCENT)])
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG, foreground=TXT, font=UI_FONT)
        s.configure("Sub.TLabel", background=BG, foreground=SUB, font=UI_FONT_S)

    def _btn(self, parent, text, cmd, kind="normal", width=None):
        colors = {"normal": ("#eef0f3", "#1f2328", "#dfe3e8"),
                  "primary": (ACCENT, "#ffffff", "#2559c9"),
                  "danger": ("#fdecec", "#b91c1c", "#f8d7d7")}
        bg, fg, hv = colors[kind]
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=hv,
                      activeforeground=fg, relief="flat", bd=0, font=UI_FONT,
                      padx=14, pady=6, cursor="hand2", highlightthickness=0)
        if width:
            b.config(width=width)
        b.bind("<Enter>", lambda e: b.config(bg=hv))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    # ---------- 布局 ----------
    def _build(self):
        root = self.root
        # 顶部条
        top = tk.Frame(root, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        top.pack(fill="x", padx=12, pady=(12, 8))
        inner = tk.Frame(top, bg=CARD)
        inner.pack(fill="x", padx=14, pady=10)

        self.dot = tk.Canvas(inner, width=10, height=10, bg=CARD, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 6))
        self._dot_id = self.dot.create_oval(1, 1, 9, 9, fill="#cbd5e1", outline="")

        self.status = tk.Label(inner, text="待机", bg=CARD, fg=SUB, font=UI_FONT_S)
        self.status.pack(side="left")
        self.region_lbl = tk.Label(inner, text="", bg=CARD, fg=SUB, font=UI_FONT_S)
        self.region_lbl.pack(side="left", padx=(6, 10))

        # 专版：区域固定，只显示当前在监听的采集区
        self.zone_lbl = tk.Label(inner, text="", bg=CARD, fg="#0e7490", font=UI_FONT_S)
        self.zone_lbl.pack(side="left", padx=(0, 8))

        # 韩文总闸：默认不收韩语发言（玩家少、基本聚在一个频道）。
        # 想看时点一下即可，监听线程下一帧就生效，不用重启也不用停止监听。
        self.ko_chk_var = tk.BooleanVar(value=bool(self.cfg.get("ko_mode", False)))
        self.ko_chk = tk.Checkbutton(
            inner, text="韩文", variable=self.ko_chk_var, command=self._on_ko_toggle,
            bg=CARD, fg=SUB, activebackground=CARD, activeforeground=TXT,
            selectcolor=CARD, highlightthickness=0, bd=0, font=UI_FONT_S)
        self.ko_chk.pack(side="left")
        ToolTip(self.ko_chk, self._ko_tip_text)

        self.btn_float = self._btn(inner, "悬浮窗", self._toggle_float)
        self.btn_float.pack(side="right", padx=(4, 0))
        self.btn_stop = self._btn(inner, "停止", self._stop, "danger")
        self.btn_stop.pack(side="right", padx=4)
        self.btn_start = self._btn(inner, "开始监听", self._start, "primary")
        self.btn_start.pack(side="right", padx=4)

        nb = ttk.Notebook(root)
        self.nb = nb
        nb.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self._tab_chat(nb)
        self._tab_input(nb)
        self._tab_settings(nb)

        foot = tk.Label(root, text="OCR：本地离线（RapidOCR · 日/英 + 可选韩语）　|　翻译：API　|　区域：预设比例或自行框选", bg=BG,
                        fg="#9aa3ad", font=UI_FONT_S, anchor="w")
        foot.pack(fill="x", padx=16, pady=(0, 8))
        self.foot = foot

    def _card(self, parent):
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="both", expand=True)
        return f

    # --- 页签1：聊天翻译日志 ---
    def _tab_chat(self, nb):
        f = tk.Frame(nb, bg=CARD)
        nb.add(f, text="  聊天翻译  ")
        bar = tk.Frame(f, bg=CARD)
        bar.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(bar, text="识别日志（实时预览，正式译文在悬浮窗）", bg=CARD, fg=SUB,
                 font=UI_FONT_S).pack(side="left")
        self._btn(bar, "清空", self._clear_log).pack(side="right")

        wrap = tk.Frame(f, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.log = tk.Text(wrap, bg="#fafbfc", fg=TXT, bd=1, relief="solid",
                           highlightthickness=0, wrap="word", font=UI_FONT_S, padx=10, pady=8)
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=sb.set, state="disabled")
        self.log.tag_config("jp", foreground="#7c8794")
        self.log.tag_config("zh", foreground="#111827", font=(UI_FONT[0], 10, "bold"))
        self.log.tag_config("sys", foreground="#94a3b8", font=("Consolas", 8))

    # --- 页签2：我的输入 ---
    def _tab_input(self, nb):
        f = tk.Frame(nb, bg=CARD)
        nb.add(f, text="  我的输入  ")
        tk.Label(f, text="粘贴中文，翻成游戏里用的外语（Ctrl+Enter 翻译）", bg=CARD, fg=SUB,
                 font=UI_FONT_S).pack(anchor="w", padx=14, pady=(10, 4))

        pad = 14
        tk.Label(f, text="中文原文", bg=CARD, fg=TXT, font=UI_FONT_S).pack(anchor="w", padx=pad)
        self.inp = tk.Text(f, height=6, bg="#fafbfc", bd=1, relief="solid", highlightthickness=0,
                           wrap="word", font=UI_FONT, padx=10, pady=8)
        self.inp.pack(fill="x", padx=pad, pady=(2, 8))
        self.inp.bind("<Control-Return>", lambda e: (self._do_input_translate(), "break")[1])

        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x", padx=pad)
        self._btn(row, "翻译 →", self._do_input_translate, "primary").pack(side="left")
        self._btn(row, "翻译并复制", self._do_input_translate_copy).pack(side="left", padx=8)
        self._btn(row, "清空", lambda: (self.inp.delete("1.0", "end"),
                                        self.out.config(state="normal"),
                                        self.out.delete("1.0", "end"),
                                        self.out.config(state="disabled"))).pack(side="left")

        tk.Label(row, text="翻成", bg=CARD, fg=SUB, font=UI_FONT_S).pack(side="left", padx=(14, 4))
        self.input_tgt = ttk.Combobox(row, state="readonly", width=6, font=UI_FONT_S,
                                      values=["日语", "韩语", "英语"])
        self.input_tgt.set(self.cfg.get("input_target_label", "日语"))
        self.input_tgt.pack(side="left")
        self.input_tgt.bind("<<ComboboxSelected>>", lambda e: self._on_input_tgt())

        self.in_hint = tk.Label(row, text="", bg=CARD, fg=SUB, font=UI_FONT_S)
        self.in_hint.pack(side="left", padx=10)

        self.out_label = tk.Label(f, text="日文译文", bg=CARD, fg=TXT, font=UI_FONT_S)
        self.out_label.pack(anchor="w", padx=pad, pady=(12, 0))
        self.out = tk.Text(f, height=6, bg="#f2f7ff", bd=1, relief="solid", highlightthickness=0,
                           wrap="word", font=(MONO_JP[0], 12), padx=10, pady=8)
        self.out.pack(fill="x", padx=pad, pady=(2, 8))
        self.out.config(state="disabled")

        tk.Label(f, text="提示：游戏内可能无法粘贴，可用输入法手动输入。\n"
                         "翻成外语时会尽量贴近玩家口语，不带敬语腔。", bg=CARD, fg="#9aa3ad",
                 font=UI_FONT_S, justify="left").pack(anchor="w", padx=pad, pady=(0, 12))

    INPUT_TGT_CODES = {"日语": "ja", "韩语": "ko", "英语": "en"}

    def _on_input_tgt(self):
        lbl = self.input_tgt.get()
        self.cfg.set("input_target_label", lbl)
        self.out_label.config(text=f"{lbl}译文")

    def _input_target_code(self):
        return self.INPUT_TGT_CODES.get(self.input_tgt.get(), "ja")

    # --- 页签3：设置 ---
    def _tab_settings(self, nb):
        # 设置项比较多，套一层滚动：别人机器 125%/150% 缩放时也不会被切掉
        outer = tk.Frame(nb, bg=CARD)
        nb.add(outer, text="  设置  ")
        canvas = tk.Canvas(outer, bg=CARD, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        f = tk.Frame(canvas, bg=CARD)
        _win = canvas.create_window((0, 0), window=f, anchor="nw")
        f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(_win, width=e.width))

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        def _bind(_e=None):
            canvas.bind_all("<MouseWheel>", _wheel)

        def _unbind(_e=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind)
        canvas.bind("<Leave>", _unbind)
        pad = 16

        # ---------------- 翻译服务 ----------------
        box = tk.Frame(f, bg=CARD)
        box.pack(fill="x", padx=pad, pady=(14, 0))
        tk.Label(box, text="翻译服务", bg=CARD, fg=TXT, font=(UI_FONT[0], 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        tk.Label(box, text="服务商", bg=CARD, fg=SUB, font=UI_FONT_S).grid(row=1, column=0, sticky="w")
        self.provider = tk.StringVar(value=self.cfg.get("provider", "microsoft"))
        self.provider_labels = {k: v for k, v in PROVIDERS}
        self.provider_cb = ttk.Combobox(
            box, state="readonly", width=38, font=UI_FONT_S,
            values=[v for _, v in PROVIDERS])
        self.provider_cb.grid(row=1, column=1, columnspan=2, sticky="w", padx=8)
        self.provider_cb.bind("<<ComboboxSelected>>", lambda e: self._on_provider())

        tk.Label(box, text="快速填入", bg=CARD, fg=SUB, font=UI_FONT_S).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.api_preset = ttk.Combobox(box, state="readonly", width=30, font=UI_FONT_S,
                                       values=[x[0] for x in API_PRESETS])
        self.api_preset.grid(row=2, column=1, sticky="w", padx=8, pady=(6, 0))
        self.api_preset.bind("<<ComboboxSelected>>", lambda e: self._on_api_preset())
        self._btn(box, "清空 Key", self._clear_key).grid(row=2, column=2, sticky="w")

        tk.Label(box, text="接口地址", bg=CARD, fg=SUB, font=UI_FONT_S).grid(row=3, column=0, sticky="w", pady=6)
        self.base_url = tk.Entry(box, width=46, font=UI_FONT_S, bd=1, relief="solid")
        self.base_url.grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=6)

        tk.Label(box, text="API Key", bg=CARD, fg=SUB, font=UI_FONT_S).grid(row=4, column=0, sticky="w")
        self.api_key = tk.Entry(box, width=46, show="•", font=UI_FONT_S, bd=1, relief="solid")
        self.api_key.grid(row=4, column=1, columnspan=2, sticky="w", padx=8)
        self.key_hint = tk.Label(box, text="", bg=CARD, fg="#9aa3ad", font=("Microsoft YaHei UI", 8))
        self.key_hint.grid(row=5, column=1, columnspan=2, sticky="w", padx=8)

        tk.Label(box, text="模型", bg=CARD, fg=SUB, font=UI_FONT_S).grid(row=6, column=0, sticky="w", pady=6)
        self.model_var = tk.StringVar(value=self.cfg.get("openai", {}).get("model", ""))
        self.model = ttk.Combobox(box, textvariable=self.model_var, width=32, font=UI_FONT_S,
                                  values=PRESET_MODELS)
        self.model.grid(row=6, column=1, sticky="w", padx=8, pady=6)
        self.btn_refresh_model = self._btn(box, "刷新列表", self._refresh_models)
        self.btn_refresh_model.grid(row=6, column=2, sticky="w")

        tk.Label(box, text="备用模型", bg=CARD, fg=SUB, font=UI_FONT_S).grid(row=7, column=0, sticky="w")
        self.fallbacks = tk.Entry(box, width=46, font=UI_FONT_S, bd=1, relief="solid")
        self.fallbacks.grid(row=7, column=1, columnspan=2, sticky="w", padx=8)
        tk.Label(box, text="主模型超时/报错时按顺序自动切换（逗号分隔）；无 Key 的免费服务会自动互备",
                 bg=CARD, fg="#9aa3ad", font=("Microsoft YaHei UI", 8)).grid(
            row=8, column=1, sticky="w", padx=8, pady=(0, 4))

        r2 = tk.Frame(box, bg=CARD)
        r2.grid(row=9, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self._btn(r2, "保存", self._save_settings, "primary").pack(side="left")
        self._btn(r2, "测试连接", self._test_api).pack(side="left", padx=8)
        self.api_hint = tk.Label(r2, text="", bg=CARD, fg=SUB, font=UI_FONT_S)
        self.api_hint.pack(side="left", padx=6)

        # ---------------- 识别引擎 ----------------
        box2 = tk.Frame(f, bg=CARD)
        box2.pack(fill="x", padx=pad, pady=(18, 0))
        tk.Label(box2, text="识别引擎", bg=CARD, fg=TXT, font=(UI_FONT[0], 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        self.ocr_pack_var = tk.StringVar(
            value="ja+ko" if "ko" in self.cfg.get("ocr_langs", ["ja"]) else "ja")
        for i, (val, label, desc) in enumerate(OCR_PACKS):
            rb = tk.Radiobutton(box2, text=label, value=val, variable=self.ocr_pack_var,
                                bg=CARD, fg=TXT, font=UI_FONT_S, activebackground=CARD,
                                selectcolor=CARD, highlightthickness=0, anchor="w",
                                command=self._on_ocr_pack)
            rb.grid(row=1 + i, column=0, columnspan=2, sticky="w")
            tk.Label(box2, text=desc, bg=CARD, fg="#9aa3ad",
                     font=("Microsoft YaHei UI", 8)).grid(row=1 + i, column=2, sticky="w", padx=(8, 0))

        tk.Label(box2, text="性能档位", bg=CARD, fg=SUB, font=UI_FONT_S).grid(row=4, column=0, sticky="w", pady=(8, 0))
        _perf_key = self.cfg.get("ocr", {}).get("perf", "balanced")
        _perf_lbl = next((v for k, v in PERF_LEVELS if k == _perf_key), "均衡")
        self.perf_var = tk.StringVar(value=_perf_lbl)
        self.perf_cb = ttk.Combobox(box2, textvariable=self.perf_var, state="readonly", width=10,
                                    font=UI_FONT_S, values=[p[1] for p in PERF_LEVELS])
        self.perf_cb.grid(row=4, column=1, sticky="w", padx=8, pady=(8, 0))
        self.perf_cb.bind("<<ComboboxSelected>>", lambda e: self._on_perf())
        # 说明单独一行，别和下拉框挤在一起（原来在第三列被窗口裁掉了）
        self.perf_hint = tk.Label(box2, text="", bg=CARD, fg="#9aa3ad",
                                  font=("Microsoft YaHei UI", 8), anchor="w", justify="left")
        self.perf_hint.grid(row=5, column=1, columnspan=2, sticky="w", padx=8)

        # 韩语识别引擎的安装状态 + 安装按钮
        tk.Label(box2, text="韩语引擎", bg=CARD, fg=SUB, font=UI_FONT_S).grid(
            row=6, column=0, sticky="w", pady=(8, 0))
        self.ko_engine_lbl = tk.Label(box2, text="", bg=CARD, fg=SUB, font=UI_FONT_S, anchor="w")
        self.ko_engine_lbl.grid(row=6, column=1, sticky="w", padx=8, pady=(8, 0))
        self.btn_ko_engine = self._btn(box2, "安装", self._install_ko_engine)
        self.btn_ko_engine.grid(row=6, column=2, sticky="w", pady=(8, 0))
        tk.Label(box2, text="日英引擎随安装包自带；韩语模型 13MB，可下载或手动选择本地 onnx 文件",
                 bg=CARD, fg="#9aa3ad", font=("Microsoft YaHei UI", 8), anchor="w").grid(
            row=7, column=1, columnspan=2, sticky="w", padx=8)

        # ---------------- 采集区 ----------------
        box3 = tk.Frame(f, bg=CARD)
        box3.pack(fill="x", padx=pad, pady=(18, 0))
        tk.Label(box3, text="采集区", bg=CARD, fg=TXT, font=(UI_FONT[0], 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        r3 = tk.Frame(box3, bg=CARD)
        r3.grid(row=1, column=0, columnspan=3, sticky="w")
        self._btn(r3, "重新框选位置", self._open_calibrate, "primary").pack(side="left")
        tk.Label(r3, text="  截图后用鼠标拖动两个框对准聊天区域，拖角改大小", bg=CARD, fg=SUB,
                 font=UI_FONT_S).pack(side="left")

        self.track_var = tk.BooleanVar(value=bool(self.cfg.get("track_window", True)))
        tk.Checkbutton(box3, text="跟随游戏窗口（移动、最大化后自动跟随）",
                       variable=self.track_var, bg=CARD, fg=TXT, font=UI_FONT_S,
                       activebackground=CARD, highlightthickness=0).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        tk.Label(box3, text="窗口标题", bg=CARD, fg=SUB, font=UI_FONT_S).grid(row=3, column=0, sticky="w", pady=6)
        self.window_title = tk.Entry(box3, width=20, font=UI_FONT_S, bd=1, relief="solid")
        self.window_title.insert(0, self.cfg.get("window_title", "OverField"))
        self.window_title.grid(row=3, column=1, sticky="w", padx=8, pady=6)
        tk.Label(box3, text="（标题含此关键字即匹配）", bg=CARD, fg=SUB, font=UI_FONT_S).grid(
            row=3, column=2, sticky="w")

        tk.Label(box3, text="轮询间隔(ms)", bg=CARD, fg=SUB, font=UI_FONT_S).grid(row=4, column=0, sticky="w")
        self.interval = tk.Spinbox(box3, from_=300, to=3000, increment=50, width=8, font=UI_FONT_S)
        self.interval.delete(0, "end")
        self.interval.insert(0, str(self.cfg.get("interval_ms", 600)))
        self.interval.grid(row=4, column=1, sticky="w", padx=8)

        self.filter_cn_var = tk.BooleanVar(value=bool(self.cfg.get("filter_chinese", True)))
        tk.Checkbutton(box3, text="忽略纯中文（游戏中文 UI 不翻译，如钓鱼产物说明）",
                       variable=self.filter_cn_var, bg=CARD, fg=TXT, font=UI_FONT_S,
                       activebackground=CARD, highlightthickness=0).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        r4 = tk.Frame(f, bg=CARD)
        r4.pack(fill="x", padx=pad, pady=(16, 0))
        self._btn(r4, "保存全部设置", self._save_settings, "primary").pack(side="left")
        self._btn(r4, "打开配置文件", self._open_config).pack(side="left", padx=8)
        self._btn(r4, "打开日志目录", self._open_logs).pack(side="left")
        self._btn(r4, "看识别标注", self._open_region_png).pack(side="left", padx=8)

        tk.Label(f, text="日志按天保存在程序目录 logs\\ 下，记录每次识别、被丢弃的行和翻译结果。",
                 bg=CARD, fg="#9aa3ad", font=UI_FONT_S, justify="left").pack(anchor="w", padx=pad, pady=(12, 0))

        self._load_settings()

    # ---------- 采集区（专版：固定布局，无需框选） ----------
    def _refresh_zone_label(self):
        try:
            from .win_track import resolve_zones, find_window, get_client_rect
            from .gamepacks import describe
            zones = resolve_zones(self.cfg)
            if zones:
                names = " + ".join(z[1] for z in zones)
                self.zone_lbl.config(text=names, fg="#0e7490")
                hwnd, _ = find_window(self.cfg.get("window_title", ""))
                if hwnd:
                    cl, ct, cr, cb = get_client_rect(hwnd)
                    self.region_lbl.config(text=f"{cr-cl}×{cb-ct}", fg=SUB)
            else:
                self.zone_lbl.config(text="未找到游戏窗口", fg=WARN)
                self.region_lbl.config(text="", fg=SUB)
                if self.pipeline is None:
                    self._set_status("没找到游戏窗口 · 先开游戏", WARN)
        except Exception:
            self.zone_lbl.config(text=describe(), fg="#0e7490")

    # ---------- 设置读写 ----------
    # ---- 服务商 / 引擎 相关的取值与联动 ----
    def _provider_key(self):
        label = self.provider_cb.get()
        for k, v in PROVIDERS:
            if v == label:
                return k
        return self.cfg.get("provider", "microsoft")

    def _provider_label(self, key):
        for k, v in PROVIDERS:
            if k == key:
                return v
        return PROVIDERS[0][1]

    def _load_settings(self):
        conf = self.cfg.get("openai", {})
        self.provider_cb.set(self._provider_label(self.cfg.get("provider", "microsoft")))
        self.base_url.delete(0, "end")
        self.base_url.insert(0, conf.get("base_url", ""))
        self.api_key.delete(0, "end")
        self.api_key.insert(0, conf.get("api_key", ""))
        self.model_var.set(conf.get("model", ""))
        self.fallbacks.delete(0, "end")
        self.fallbacks.insert(0, ", ".join(conf.get("fallbacks", [])))
        self._on_provider()
        self._on_perf()
        self._refresh_ko_engine()

    # ---- 韩语引擎安装 ----
    def _refresh_ko_engine(self):
        try:
            from .enginepack import ko_installed
            ok = ko_installed()
        except Exception:
            ok = False
        try:
            if ok:
                self.ko_engine_lbl.config(text="已安装", fg=OK)
                self.btn_ko_engine.config(text="重新安装")
            else:
                self.ko_engine_lbl.config(text="未安装（打开「韩文」前需要装）", fg=WARN)
                self.btn_ko_engine.config(text="安装")
        except Exception:
            pass
        return ok

    def _install_ko_engine(self):
        from .enginepack import ko_installed, download_ko, DEFAULT_KO_URL
        if ko_installed():
            if not messagebox.askyesno("韩语引擎已安装",
                                       "已经装好了，重新下载会覆盖现有文件。要重装吗？"):
                return
        url = (self.cfg.get("ocr", {}) or {}).get("ko_model_url") or DEFAULT_KO_URL
        if not messagebox.askyesno(
                "安装韩语识别引擎",
                "需要韩语模型文件（约 13MB）。\n\n"
                "「是」＝ 自动下载（默认从 HuggingFace 取，国内可能要代理）\n"
                "「否」＝ 我手动选一个本地的 .onnx 文件"):
            self._pick_ko_model()
            return

        def prog(msg):
            self.root.after(0, lambda: self._safe_lbl(self.ko_engine_lbl, msg, SUB))

        def run():
            try:
                download_ko(url, progress=prog)
                self.root.after(0, self._after_ko_install)
            except Exception as e:
                self.root.after(0, lambda: self._ko_install_failed(e))
        threading.Thread(target=run, daemon=True).start()

    def _safe_lbl(self, lbl, text, color):
        try:
            lbl.config(text=text, fg=color)
        except Exception:
            pass

    def _pick_ko_model(self):
        from tkinter import filedialog
        from .enginepack import install_from_file
        path = filedialog.askopenfilename(
            title="选择韩语识别模型 korean_PP-OCRv5_rec_mobile.onnx",
            filetypes=[("ONNX 模型", "*.onnx"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            install_from_file(path)
            self._after_ko_install()
        except Exception as e:
            messagebox.showerror("安装失败", f"{e}\n\n直接把文件复制到程序目录的 models\\ 下也可以。")

    def _after_ko_install(self):
        self._refresh_ko_engine()
        self._hint(self.api_hint, "韩语引擎已就绪，打开「韩文」即可识别韩语", OK)
        try:
            if self.float_win and self.float_win.winfo_exists():
                self.float_win._sync_ko()
        except Exception:
            pass

    def _ko_install_failed(self, err):
        self._safe_lbl(self.ko_engine_lbl, "未安装", WARN)
        if messagebox.askyesno(
                "下载失败",
                f"{err}\n\n可能是网络不通（HuggingFace 国内常被墙）。\n"
                "要不要手动选择本地的 onnx 文件？"):
            self._pick_ko_model()

    def _on_api_preset(self):
        name = self.api_preset.get()
        for n, url, model in API_PRESETS:
            if n == name:
                if n == "自定义":
                    return
                self.base_url.delete(0, "end")
                self.base_url.insert(0, url)
                if model:
                    self.model_var.set(model)
                self._hint(self.api_hint, f"已填入 {n.split('（')[0]} 的地址和模型，别忘了填 Key", SUB)
                return

    def _clear_key(self):
        self.api_key.delete(0, "end")
        self._hint(self.api_hint, "已清空 Key（分享前记得清一下）", SUB)

    def _on_perf(self):
        label = self.perf_cb.get()
        key = next((k for k, v in PERF_LEVELS if v == label), "balanced")
        info = PERF_MAP[key]
        self.perf_hint.config(
            text=f"{PERF_DESC.get(key, '')}　（线程 {info['threads']} · 轮询 {info['interval_ms']}ms）")
        try:
            self.interval.delete(0, "end")
            self.interval.insert(0, str(info["interval_ms"]))
        except Exception:
            pass

    def _on_ocr_pack(self):
        pack = self.ocr_pack_var.get()
        langs = ["ja"] + (["ko"] if pack == "ja+ko" else [])
        self.cfg.set("ocr_langs", langs)
        if pack == "ja":
            # 关掉韩文引擎时顺手把「显示韩语」也关掉，否则会出现「开关开着但认不出」
            if self.ko_chk_var.get():
                self.ko_chk_var.set(False)
                self._on_ko_toggle()
        self._apply_langs(langs)
        self._hint(self.api_hint, "已切换识别引擎（下次开始监听生效）", OK)

    def _apply_langs(self, langs):
        if self.pipeline:
            try:
                self.pipeline.set_langs(langs)
            except Exception:
                pass

    def _open_calibrate(self):
        from .win_track import find_window
        if not find_window(self.cfg.get("window_title", ""))[0]:
            messagebox.showwarning(
                "没找到游戏窗口",
                "校准需要先把游戏打开。\n\n"
                f"没有找到标题包含「{self.cfg.get('window_title', 'OverField')}」的窗口。\n"
                "开好游戏、进到聊天画面后再点一次。")
            return
        from .calibrate import CalibrateWindow
        try:
            CalibrateWindow(self.root, self.cfg, on_save=self._refresh_zone_label)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _refresh_models(self):
        """从 NAS 拉取模型列表，填充下拉框"""
        self._save_settings()
        self._hint(self.api_hint, "获取模型列表…", SUB)

        def run():
            try:
                models = self.translator.list_models()
                self.q.put(("models", models, None))
            except Exception as e:
                self.q.put(("api", f"获取失败：{e}", WARN))
        threading.Thread(target=run, daemon=True).start()

    def _apply_models(self, models):
        cur = self.model_var.get()
        self.model.config(values=models)
        if cur not in models and cur:
            # 手动输入的模型不在列表里也保留，不清空
            pass
        self._hint(self.api_hint, f"已加载 {len(models)} 个模型", OK)

    def _on_provider(self):
        """切换服务商：免费的把地址填好、Key 置灰，收费的留空让用户填"""
        p = self._provider_key()
        prev = getattr(self, "_cur_provider", None)
        # 离开 openai 时先把表单存起来，回来时还原 —— 否则会被免费服务的
        # 占位地址覆盖掉，一保存就把用户的接口地址写坏了
        if prev == "openai" and p != "openai":
            self._draft_openai = {
                "base_url": self.base_url.get().strip(),
                "api_key": self.api_key.get().strip(),
                "model": self.model_var.get().strip(),
                "fallbacks": self.fallbacks.get().strip(),
            }
        no_key = p in ("microsoft", "mymemory", "google", "none")
        try:
            self.api_key.config(state="disabled" if no_key else "normal")
            self.model.config(state="disabled" if no_key else "normal")
            self.btn_refresh_model.config(state="disabled" if p != "openai" else "normal")
        except Exception:
            pass

        if p == "microsoft":
            self.base_url.delete(0, "end")
            self.base_url.insert(0, "https://api-edge.cognitive.microsofttranslator.com")
            self.key_hint.config(text="免 Key：自动取临时令牌并续期；接口失效时自动换 MyMemory")
        elif p == "mymemory":
            self.base_url.delete(0, "end")
            self.base_url.insert(0, "https://api.mymemory.translated.net")
            self.key_hint.config(text="免 Key：按 IP 限流（约 5000 字符/天），译文质量一般")
        elif p == "google":
            self.base_url.delete(0, "end")
            self.base_url.insert(0, "https://translate.googleapis.com")
            self.key_hint.config(text="免 Key：国内直连多数不通，需要系统代理")
        elif p == "deepl":
            self.base_url.delete(0, "end")
            self.base_url.insert(0, self.cfg.get("deepl", {}).get("endpoint", "")
                                 or "https://api-free.deepl.com/v2/translate")
            self.api_key.delete(0, "end")
            self.api_key.insert(0, self.cfg.get("deepl", {}).get("api_key", ""))
            self.key_hint.config(text="DeepL Free 的 Key（以 :fx 结尾）")
        elif p == "none":
            self.base_url.delete(0, "end")
            self.key_hint.config(text="只显示原文，不调用任何接口")
        else:
            d = getattr(self, "_draft_openai", None) or {
                "base_url": self.cfg.get("openai", {}).get("base_url", ""),
                "api_key": self.cfg.get("openai", {}).get("api_key", ""),
                "model": self.cfg.get("openai", {}).get("model", ""),
                "fallbacks": ", ".join(self.cfg.get("openai", {}).get("fallbacks", [])),
            }
            self.base_url.delete(0, "end")
            self.base_url.insert(0, d["base_url"])
            self.api_key.delete(0, "end")
            self.api_key.insert(0, d["api_key"])
            self.model_var.set(d["model"])
            self.fallbacks.delete(0, "end")
            self.fallbacks.insert(0, d["fallbacks"])
            self.key_hint.config(text="填服务商给的 Key；地址要带 /v1（或 /api/paas/v4）")
        self._cur_provider = p

    def _save_settings(self):
        p = self._provider_key()
        self.cfg.set("provider", p)
        if p == "deepl":
            self.cfg.set("deepl", {"api_key": self.api_key.get().strip(),
                                   "endpoint": self.base_url.get().strip()})
        elif p == "openai":
            raw_fb = self.fallbacks.get().strip()
            fb = [x.strip() for x in raw_fb.replace("，", ",").split(",") if x.strip()]
            self.cfg.set("openai", {
                "base_url": self.base_url.get().strip() or "https://api.deepseek.com/v1",
                "api_key": self.api_key.get().strip(),
                "model": self.model_var.get().strip(),
                "fallbacks": fb,
            })

        # 识别引擎包 + 性能档位
        pack = self.ocr_pack_var.get()
        langs = ["ja"] + (["ko"] if pack == "ja+ko" else [])
        if langs != self.cfg.get("ocr_langs"):
            self.cfg.set("ocr_langs", langs)
            self._apply_langs(langs)

        perf = next((k for k, v in PERF_LEVELS if v == self.perf_cb.get()), "balanced")
        info = PERF_MAP[perf]
        ocr_conf = dict(self.cfg.get("ocr", {}))
        ocr_conf["perf"] = perf
        ocr_conf["threads"] = info["threads"]
        ocr_conf["det_tiny"] = info["det_tiny"]
        self.cfg.set("ocr", ocr_conf)

        try:
            self.cfg.set("interval_ms", max(250, int(float(self.interval.get()))))
        except Exception:
            pass
        self.cfg.set("track_window", bool(self.track_var.get()))
        self.cfg.set("window_title", self.window_title.get().strip() or "OverField")
        self.cfg.set("filter_chinese", bool(self.filter_cn_var.get()))
        self.translator = Translator(self.cfg)
        self._hint(self.api_hint, "已保存", OK)

    def _open_logs(self):
        from .logger import LOG_DIR
        import os
        os.makedirs(LOG_DIR, exist_ok=True)
        try:
            os.startfile(LOG_DIR)
        except Exception:
            messagebox.showinfo("日志目录", LOG_DIR)

    def _open_region_png(self):
        """打开最近一次「立即识别」截下的各采集区原图，用来确认区域是否准确"""
        from .logger import LOG_DIR
        import glob
        import os
        # 优先看带识别框的标注图：绿框=识别并保留，红框=识别到但被过滤，
        # 画面上有字却没框的就是 OCR 没认出来
        shots = sorted(glob.glob(os.path.join(LOG_DIR, "marked_*.png")))
        if not shots:
            shots = sorted(glob.glob(os.path.join(LOG_DIR, "zone_*.png")))
        if not shots:
            messagebox.showinfo("还没有截图", "先点一次「重新框选位置」并保存，就会生成带识别框的截图。")
            return
        try:
            os.startfile(shots[0])
        except Exception:
            messagebox.showinfo("采集区截图", "\n".join(shots))

    def _test_api(self):
        self._save_settings()
        self._hint(self.api_hint, "测试中…", SUB)

        def run():
            try:
                r = self.translator.test()
                used = self.translator.last_model
                self.q.put(("api", f"成功〔{used}〕：{r}", OK))
            except TranslateError as e:
                self.q.put(("api", f"失败：{e}", WARN))
            except Exception as e:
                self.q.put(("api", f"失败：{e}", WARN))
        threading.Thread(target=run, daemon=True).start()

    def _open_config(self):
        import os
        os.startfile(self.cfg.path) if hasattr(os, "startfile") else None

    def _hint(self, lbl, text, color=SUB):
        lbl.config(text=text, fg=color)

    # ---------- 生命周期 ----------
    def _warmup(self):
        try:
            from .ocr_engine import OcrEngine
            conf = self.cfg.get("ocr", {})
            langs = list(self.cfg.get("ocr_langs", ["ja"]))
            if not self.cfg.get("ko_mode", False):
                langs = [x for x in langs if x != "ko"]     # 韩文关着就别加载韩语模型
            if not langs:
                langs = ["ja"]
            from .enginepack import missing_models
            lack = missing_models()
            if lack:
                self.q.put(("status", "模型文件缺失：" + ", ".join(lack), WARN))
                LOG.error("模型文件缺失: " + ", ".join(lack) +
                          "（models\\ 目录被删了？重新解压一次）")
                return
            eng = OcrEngine(threads=conf.get("threads", 2), langs=langs,
                            det_tiny=bool(conf.get("det_tiny", True)))
            eng.warmup()
            if eng.failed:
                self.q.put(("status", "OCR 部分语种失败: " + "; ".join(eng.failed)[:60], WARN))
                LOG.error("OCR 引擎失败: " + "; ".join(eng.failed))
            else:
                self.q.put(("status", f"OCR 引擎就绪（{'+'.join(langs)}）", SUB))
                LOG.info(f"OCR 引擎就绪 langs={langs}")
        except Exception as e:
            self.q.put(("status", f"OCR 初始化失败: {e}", WARN))
            LOG.error(f"OCR 初始化失败: {e}")

    def _start(self):
        if self.pipeline and self.pipeline.is_running:
            return
        from .win_track import resolve_zones
        if not resolve_zones(self.cfg):
            messagebox.showwarning(
                "没找到游戏窗口",
                f"没找到标题包含「{self.cfg.get('window_title', 'OverField')}」的窗口。\n\n"
                "1) 先把游戏打开，进到有聊天的画面\n"
                "2) 再回来点「开始监听」\n\n"
                "如果游戏已经开着，可能是标题不同 —— 到设置里把「窗口标题」改成实际标题里的词。")
            self._set_status("未找到游戏窗口", WARN)
            return
        self._save_settings()
        self.pipeline = Pipeline(
            self.cfg,
            on_messages=lambda msgs, trans: self.q.put(("msg", msgs, trans)),
            on_status=lambda s: self.q.put(("run", s)),
            on_error=lambda e: self.q.put(("err", e)),
        )
        self.pipeline.set_ko(bool(self.ko_chk_var.get()))
        self.pipeline.start()
        self._set_status("监听中…", OK)
        self.dot.itemconfig(self._dot_id, fill=OK)
        self.btn_start.config(state="disabled")
        if not self.float_win:
            self._toggle_float()

    def _stop(self):
        if self.pipeline:
            self.pipeline.stop()
        self._set_status("已停止", SUB)
        self.dot.itemconfig(self._dot_id, fill="#cbd5e1")
        self.btn_start.config(state="normal")

    def _ko_tip_text(self):
        """韩文开关的悬停说明；没装引擎才补最后一句"""
        rows = ["韩语发言的识别开关（默认关闭）",
                "· 关闭：韩语全部丢弃，不占版面、不花 token",
                "· 打开：韩语会识别、放宽过滤并翻译"]
        try:
            from .enginepack import ko_installed
            if not ko_installed():
                rows.append("你当前没有安装韩文识别引擎")
        except Exception:
            pass
        return "\n".join(rows)

    def _toggle_ko_from_float(self):
        """悬浮窗点「韩」：走和主窗口勾选框完全相同的逻辑"""
        self.ko_chk_var.set(not self.ko_chk_var.get())
        self._on_ko_toggle()
        return bool(self.ko_chk_var.get())

    def _on_ko_toggle(self):
        """「韩文」开关：立即写配置并通知正在跑的监听线程（下一帧生效）"""
        on = bool(self.ko_chk_var.get())
        if on:
            try:
                from .enginepack import ko_installed
                if not ko_installed():
                    self.ko_chk_var.set(False)
                    self.ko_chk.config(fg=SUB)
                    if messagebox.askyesno(
                            "还没装韩语识别引擎",
                            "要识别韩语需要先安装韩语模型（约 13MB）。\n\n现在去安装吗？"):
                        try:
                            self.nb.select(2)
                        except Exception:
                            pass
                        self._install_ko_engine()
                    return
            except Exception:
                pass
        if on and "ko" not in self.cfg.get("ocr_langs", []):
            ok = messagebox.askyesno(
                "需要加载韩语模型",
                "当前识别引擎只有「日语 / 英语」。\n\n"
                "开启韩文需要额外加载韩语模型：\n"
                "  · 内存多约 60MB\n"
                "  · 每帧识别时间约 +40%\n\n"
                "现在就切换成「日英 + 韩语」吗？（之后可在设置里改回）")
            if not ok:
                self.ko_chk_var.set(False)
                self.ko_chk.config(fg=SUB)
                return
            self.cfg.set("ocr_langs", ["ja", "ko"])
            try:
                self.ocr_pack_var.set("ja+ko")
            except Exception:
                pass
            if self.pipeline:
                self.pipeline.set_langs(["ja", "ko"])
        self.cfg.set("ko_mode", on)
        if self.pipeline:
            self.pipeline.set_ko(on)
        self.ko_chk.config(fg=OK if on else SUB)
        try:
            if self.float_win and self.float_win.winfo_exists():
                self.float_win._sync_ko(on)
        except Exception:
            pass
        self._set_status("韩文：开（韩语会识别并翻译）" if on else "韩文：关（韩语全部丢弃）",
                         OK if on else SUB)

    def _set_status(self, text, color=SUB):
        self.status.config(text=text, fg=color)

    def _toggle_float(self):
        if self.float_win and self.float_win.winfo_exists():
            self.float_win.deiconify()
            self.float_win.lift()
            return
        self.float_win = FloatWindow(
            self.root, self.cfg,
            on_close=lambda: setattr(self, "float_win", None),
            on_ko=self._toggle_ko_from_float,
            ko_state=lambda: bool(self.ko_chk_var.get()))

    def _snapshot(self):
        from .win_track import resolve_zones
        if not resolve_zones(self.cfg):
            messagebox.showinfo("提示", "没找到游戏窗口，请先启动游戏")
            return
        self._set_status("识别中…", SUB)

        def run():
            try:
                p = Pipeline(self.cfg, lambda *a: None)
                msgs, lines, shots = p.snapshot()
                self.q.put(("snap", msgs, lines, shots))
            except Exception as e:
                self.q.put(("err", str(e)))
        threading.Thread(target=run, daemon=True).start()

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    # ---------- 输入翻译 ----------
    def _do_input_translate(self):
        text = self.inp.get("1.0", "end").strip()
        if not text:
            return
        if self._busy:
            return
        self._busy = True
        tgt = self._input_target_code()
        self.in_hint.config(text="翻译中…", fg=SUB)

        def run():
            try:
                # 输入方向固定是中文 -> 选中的目标语言
                r = self.translator.translate([text], "zh-CN", tgt)
                self.q.put(("input", r[0], True))
            except Exception as e:
                self.q.put(("input", f"翻译失败：{e}", False))
            finally:
                self._busy = False
        threading.Thread(target=run, daemon=True).start()

    def _do_input_translate_copy(self):
        self._do_input_translate()
        self.root.after(1200, self._copy_out)

    def _copy_out(self):
        txt = self.out.get("1.0", "end").strip()
        if txt:
            self.clipboard_clear()
            self.clipboard_append(txt)
            self.in_hint.config(text="已复制到剪贴板", fg=OK)

    # ---------- 队列轮询 ----------
    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "msg":
                    self._on_msgs(item[1], item[2])
                elif kind == "addr":
                    pass
                elif kind == "run":
                    if item[1] == "stopped":
                        self._stop()
                elif kind == "status":
                    self._set_status(item[1], item[2] if len(item) > 2 else SUB)
                elif kind == "err":
                    self._log_line(f"[错误] {item[1]}", "sys")
                elif kind == "api":
                    self._hint(self.api_hint, item[1], item[2])
                elif kind == "models":
                    self._apply_models(item[1])
                elif kind == "snap":
                    self._on_snapshot(item[1], item[2], item[3] if len(item) > 3 else [])
                elif kind == "input":
                    self.out.config(state="normal")
                    self.out.delete("1.0", "end")
                    self.out.insert("1.0", item[1])
                    self.out.config(state="disabled")
                    self.in_hint.config(text="" if item[2] else "", fg=WARN if not item[2] else OK)
                    if item[2]:
                        self.in_hint.config(text="完成", fg=OK)
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _on_msgs(self, msgs, trans):
        items = []
        for m, t in zip(msgs, trans):
            if m.native:
                self._log_line(f"• {m.text}　（中文，不翻译）", "jp")
            else:
                self._log_line(f"• {m.text}", "jp")
                self._log_line(f"  → {t}", "zh")
            # 纯昵称行不当消息显示（没有正文可翻）
            if m.nickname and not m.sender:
                continue
            items.append((m.sender, m.body, t))
        if self.float_win and self.float_win.winfo_exists():
            self.float_win.add(items)
            self.float_win.set_status(f"{self.pipeline.stats['ocr_ms']}ms")
        self._set_status(f"监听中 · 累计 {self.pipeline.stats['count']} 条", OK)
        st = self.pipeline.stats
        idle = f"　跳过静帧 {st['skipped']}" if st.get("skipped") else ""
        tr = getattr(self.pipeline, "translator", None)
        mem = ""
        if tr is not None and getattr(tr, "hits", 0):
            mem = f"　记忆复用 {tr.hits} 次"
        self.foot.config(text=f"OCR {st['ocr_ms']}ms　翻译 {st['tr_ms']}ms　"
                              f"累计 {st['count']} 条{idle}{mem}　|　面板{st.get('panel','-')}")

    def _on_snapshot(self, msgs, lines, shots=None):
        self._clear_log()
        zones = [n for n, _ in (shots or [])]
        self._log_line(f"识别 {len(lines)} 行 / 保留 {len(msgs)} 条消息"
                       + (f"　（{' + '.join(zones)}）" if zones else ""), "sys")
        for m in msgs:
            tag = "sys" if m.nickname else "jp"
            who = f"[{m.sender}] " if m.sender else ""
            self._log_line(f"  {who}{m.body}", tag)
        if not msgs:
            self._log_line("（两个采集区都没读到聊天内容：聊天框可能是收起的，"
                           "或当前画面没有新消息）", "sys")
        # 自动切到识别日志页，让人直接看到结果
        try:
            self.nb.select(0)
        except Exception:
            pass
        self._set_status(f"识别完成：{len(lines)} 行 → {len(msgs)} 条", OK)

    def _log_line(self, text, tag=None):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def _on_quit(self):
        if self.pipeline:
            self.pipeline.stop()
        self.cfg.save()
        self.root.destroy()
