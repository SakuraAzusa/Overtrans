"""本地 OCR 引擎（RapidOCR），纯离线，支持日/韩/英

关键事实（实测）：
- PP-OCRv6 的 rec 只支持 japan / en / 拉丁语系，**不支持 korean**（会抛异常）
- 韩语必须用 PP-OCRv5 mobile
- japan 模型对拉丁字母（英文）识别良好，所以 en 不需要单独跑
"""
import os
import re
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

# 每种语言对应的模型配置
LANG_PROFILES = {
    "ja": {"lang": "japan"},
    "ko": {"lang": "korean", "version": "PP-OCRv5", "model": "mobile"},
    "en": {"lang": "en"},
    "ch": {"lang": "ch"},
}


def _has_model(name):
    """rapidocr 自带的 models 目录里有没有这个模型文件"""
    try:
        import rapidocr
        return os.path.exists(os.path.join(os.path.dirname(rapidocr.__file__), "models", name))
    except Exception:
        return False


@dataclass
class Line:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    score: float
    lang: str = ""

    @property
    def h(self):
        return self.y1 - self.y0


class OcrEngine:
    """多语言 OCR：每种语言一个引擎，按需加载"""

    def __init__(self, threads=4, autocontrast=True, langs=("ja",), det_tiny=True):
        self.threads = threads
        self.autocontrast = autocontrast
        self.langs = list(langs)
        self.det_tiny = det_tiny
        self._engines = {}
        self._init_lock = threading.Lock()
        self.failed = []          # 加载失败的语种，便于 UI 提示

    def warmup(self):
        for lang in self.langs:
            self._ensure(lang)

    def _make_params(self, lang):
        prof = LANG_PROFILES.get(lang, {"lang": lang})
        p = {
            "Rec.lang_type": prof["lang"],
            "Global.log_level": "error",
            "EngineConfig.onnxruntime.intra_op_num_threads": int(self.threads),
        }
        try:
            from rapidocr import OCRVersion, ModelType
        except Exception:
            return p

        # 模型文件优先用程序目录 models\ 下的实体文件（看得见、能替换、能补装），
        # 找不到才回落到 rapidocr 自带的。显式指定路径还能避免不同版本
        # rapidocr 默认模型不一样导致的行为漂移。
        from .enginepack import find_model

        det_name = "PP-OCRv6_det_tiny.onnx" if (self.det_tiny and find_model("PP-OCRv6_det_tiny.onnx"))             else "PP-OCRv6_det_small.onnx"
        det = find_model(det_name)
        if det:
            p["Det.model_path"] = det
            p["Det.ocr_version"] = OCRVersion.PPOCRV6
            p["Det.model_type"] = ModelType.TINY if "tiny" in det_name else ModelType.SMALL

        if prof["lang"] == "korean":
            rec_name, rec_ver, rec_type = ("korean_PP-OCRv5_rec_mobile.onnx",
                                           OCRVersion.PPOCRV5, ModelType.MOBILE)
        else:
            rec_name, rec_ver, rec_type = ("PP-OCRv6_rec_small.onnx",
                                           OCRVersion.PPOCRV6, ModelType.SMALL)
        rec = find_model(rec_name)
        if rec:
            p["Rec.model_path"] = rec
            p["Rec.ocr_version"] = rec_ver
            p["Rec.model_type"] = rec_type
        return p

    def set_langs(self, langs):
        """运行中切换语种包：新语种后台加载，去掉的在下一帧自然不再使用"""
        langs = [x for x in langs if x]
        if not langs:
            return
        self.langs = langs
        threading.Thread(target=self.warmup, daemon=True).start()

    def _ensure(self, lang):
        if lang in self._engines:
            return self._engines[lang]
        with self._init_lock:
            if lang in self._engines:
                return self._engines[lang]
            from rapidocr import RapidOCR
            try:
                self._engines[lang] = RapidOCR(params=self._make_params(lang))
            except Exception as e:
                # 不再静默失败：记录并跳过
                self.failed.append(f"{lang}: {e}")
                self._engines[lang] = None
        return self._engines[lang]

    def _prep(self, img: Image.Image):
        g = ImageOps.grayscale(img)
        if self.autocontrast:
            g = ImageOps.autocontrast(g, cutoff=1)
        arr = np.array(g)
        return np.stack([arr] * 3, axis=-1)

    def _recognize_one(self, lang, arr):
        eng = self._ensure(lang)
        if eng is None:
            return []
        res = eng(arr)
        out = []
        txts = getattr(res, "txts", None)
        if txts is None:
            return out
        for txt, box, sc in zip(res.txts, res.boxes, res.scores):
            txt = (txt or "").strip()
            if not txt:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append(Line(txt, int(min(xs)), int(min(ys)),
                            int(max(xs)), int(max(ys)), float(sc), lang))
        return out

    def recognize(self, img: Image.Image):
        """多语言识别后按位置去重。

        同一位置多个引擎都有结果时：含韩文的结果优先（只有 ko 引擎认得韩文），
        否则按置信度取高（日/英由 ja 引擎负责，通常更准）。
        """
        arr = self._prep(img)
        all_lines = []
        for lang in self.langs:
            all_lines.extend(self._recognize_one(lang, arr))

        def rank(ln):
            has_hangul = bool(re.search(r"[\uac00-\ud7af]", ln.text))
            return (1 if has_hangul else 0, ln.score)

        deduped = []
        for ln in sorted(all_lines, key=rank, reverse=True):
            cx = (ln.x0 + ln.x1) / 2
            cy = (ln.y0 + ln.y1) / 2
            if any(abs(cx - (k.x0 + k.x1) / 2) < 12 and abs(cy - (k.y0 + k.y1) / 2) < 8
                   for k in deduped):
                continue
            deduped.append(ln)
        deduped.sort(key=lambda l: (l.y0, l.x0))
        return deduped
