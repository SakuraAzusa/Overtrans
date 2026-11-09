"""Overtrans · 游戏聊天实时翻译（入口）

单实例 + 崩溃兜底：pythonw 没有控制台，异常必须自己写盘，否则用户只看到窗口消失。
"""
import ctypes
import os
import sys
import threading
import time
import tkinter as tk
import traceback

# 必须在创建 Tk 之前声明 DPI 感知，否则截图坐标和窗口坐标会对不上
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import Config  # noqa: E402
from app.logger import LOG, LOG_DIR  # noqa: E402
from app.paths import APP_DIR  # noqa: E402
from app.ui import App  # noqa: E402

MUTEX_NAME = "Global\\Overtrans_SingleInstance"


def _single_instance():
    """已经有实例在跑就返回 False"""
    try:
        ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _hook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "crash.log"), "a", encoding="utf-8") as f:
            f.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n{text}")
    except Exception:
        pass
    try:
        from tkinter import messagebox
        last = [x for x in text.strip().splitlines() if x.strip()][-1][:300]
        messagebox.showerror("Overtrans 出错了",
                             "程序遇到一个错误，详情已写入 logs\\crash.log。\n\n" + last)
    except Exception:
        pass


def main():
    if not _single_instance():
        r = tk.Tk()
        r.withdraw()
        from tkinter import messagebox
        messagebox.showinfo("Overtrans 已在运行", "已经开着一个 Overtrans 了，看任务栏。")
        return

    sys.excepthook = _hook
    threading.excepthook = lambda a: _hook(a.exc_type, a.exc_value, a.exc_traceback)

    cfg = Config()
    LOG.enabled = bool(cfg.get("log_enabled", True))
    LOG.info(f"启动 Overtrans | 程序目录 {APP_DIR} | 日志 {LOG_DIR}")
    root = tk.Tk()
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass
    App(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
