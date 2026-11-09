"""运行日志：按天落盘，记录 OCR / 过滤 / 翻译全过程，方便事后排查"""
import os
import threading
import time

from .paths import LOG_DIR  # noqa: E402
_lock = threading.Lock()
_keep_days = 7


class Logger:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self._fh = None
        self._day = None
        if enabled:
            os.makedirs(LOG_DIR, exist_ok=True)

    def _file(self):
        day = time.strftime("%Y-%m-%d")
        if self._fh is None or self._day != day:
            if self._fh:
                try:
                    self._fh.close()
                except Exception:
                    pass
            path = os.path.join(LOG_DIR, f"{day}.log")
            self._fh = open(path, "a", encoding="utf-8")
            self._day = day
            self._prune()
        return self._fh

    def _prune(self):
        try:
            files = sorted(f for f in os.listdir(LOG_DIR) if f.endswith(".log"))
            for f in files[:-_keep_days]:
                os.remove(os.path.join(LOG_DIR, f))
        except Exception:
            pass

    def write(self, tag, msg):
        if not self.enabled:
            return
        line = f"{time.strftime('%H:%M:%S')}.{int(time.time()*1000)%1000:03d} [{tag}] {msg}"
        with _lock:
            try:
                fh = self._file()
                fh.write(line + "\n")
                fh.flush()
            except Exception:
                pass

    def ocr(self, lines, cost_ms):
        texts = " | ".join(l.text for l in lines) if lines else "(无)"
        self.write("OCR", f"{len(lines)}行 {cost_ms}ms | {texts}")

    def dropped(self, items):
        if not items:
            return
        parts = [f"{t}({why})" for t, why in items]
        self.write("丢弃", f"{len(parts)}行: " + " | ".join(parts))

    def new_msgs(self, msgs):
        if not msgs:
            return
        for m in msgs:
            who = f"{m.sender}: " if m.sender else ""
            seq = getattr(m, "seq", None)
            tag = f"#{seq:<4d} " if isinstance(seq, int) else ""
            self.write("新消息", f"{tag}{who}{m.body}" + ("[贴纸]" if m.sticker else ""))

    def translation(self, pairs, cost_ms, model):
        if not pairs:
            return
        body = " | ".join(f"{a} -> {b}" for a, b in pairs)
        self.write("翻译", f"{cost_ms}ms [{model}] {body}")

    def error(self, msg):
        self.write("错误", msg.replace("\n", " ")[:300])

    def info(self, msg):
        self.write("信息", msg)

    def close(self):
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None


LOG = Logger()


def set_enabled(flag):
    LOG.enabled = bool(flag)
