"""翻译服务：统一封装多种 provider，带缓存与批量能力"""
import base64
import json
import os
import re
import threading
import time
from difflib import SequenceMatcher

import requests

LANG_NAMES = {
    "ja": "日语", "zh-CN": "简体中文", "zh-TW": "繁体中文", "en": "英语", "ko": "韩语",
}
DEEPL_LANG = {"ja": "JA", "zh-CN": "ZH", "zh-TW": "ZH", "en": "EN", "ko": "KO"}
GOOGLE_LANG = {"ja": "ja", "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en": "en", "ko": "ko"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# 免 Key 服务的互备顺序：主服务挂了自动往下试，不打断翻译
FREE_FALLBACK = {
    "microsoft": ["mymemory", "google"],
    "mymemory": ["microsoft", "google"],
    "google": ["microsoft", "mymemory"],
}

MS_LANG = {"ja": "ja", "ko": "ko", "zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "en": "en"}
MM_LANG = {"ja": "ja-JP", "ko": "ko-KR", "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en": "en-US"}

# Edge 翻译的免 Key 令牌（约 10 分钟过期，过期自己再取一次）
_EDGE = {"token": "", "exp": 0.0}
_EDGE_LOCK = threading.Lock()
LOG_FALLBACK = []      # 最近几次回落记录，UI 可选展示

DEFAULT_MODEL = "cn:deepseek-v4.1-flash"
MODEL_TIMEOUT = 18      # 单个模型单次请求超时（秒）
MAX_CANDIDATES = 4      # 最多尝试几个模型，避免拖太久


class TranslateError(Exception):
    pass


def _jwt_exp(tok):
    """从 JWT 里读过期时间（拿不到就按 8 分钟算）"""
    try:
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part))
        return float(data.get("exp") or 0)
    except Exception:
        return 0.0


EDGE_AUTH_URL = "https://edge.microsoft.com/translate/auth"


def edge_token(force=False):
    """取 Microsoft 翻译的临时令牌（免 Key，走 Edge 的公开接口）

    令牌失效有两种表现：拿不到（接口变了）和过期（401）。
    前者抛错让上层换 MyMemory，后者重新取一次。
    """
    with _EDGE_LOCK:
        now = time.time()
        if not force and _EDGE["token"] and _EDGE["exp"] - 60 > now:
            return _EDGE["token"]
        try:
            r = requests.get(EDGE_AUTH_URL, headers={"User-Agent": UA}, timeout=10)
        except requests.RequestException as e:
            raise TranslateError(f"取微软令牌失败: {e}")
        if r.status_code != 200:
            raise TranslateError(f"取微软令牌失败 HTTP {r.status_code}")
        tok = (r.text or "").strip()
        if tok.count(".") != 2:
            raise TranslateError("微软令牌返回异常（接口可能变了）")
        exp = _jwt_exp(tok) or now + 480
        _EDGE["token"] = tok
        _EDGE["exp"] = exp
        return tok


# ---------------------------------------------------------------- 翻译记忆
from .paths import CACHE_DIR  # noqa: E402

TM_PATH = os.path.join(CACHE_DIR, "tm.json")
TM_MAX = 1500          # 最多记多少条
TM_SCAN = 400          # 模糊匹配最多往回扫多少条（性能上限）
TM_SAVE_GAP = 10.0     # 两次落盘的最小间隔（秒）


def tm_norm(s):
    """归一化：去掉空白/控制符，全角转半角标点，用于模糊比较"""
    s = re.sub(r"[\u0000-\u001f]", "", s or "")
    s = re.sub(r"\s+", "", s)
    return (s.replace("，", ",").replace("。", ".").replace("！", "!")
             .replace("？", "?").replace("：", ":").replace("　", ""))


def _ratio(a, b):
    return SequenceMatcher(None, a, b).ratio()


class Translator:
    def __init__(self, cfg):
        self.cfg = cfg
        self._cache = {}
        self._lock = threading.Lock()
        self._active_model = ""     # 上次成功的模型（熔断记忆）
        self._last_model = ""       # 最近一次实际使用的模型
        self.last_provider = ""     # 最近一次实际生效的服务商（含自动回落）

        # 翻译记忆：(src, tgt, norm原文, 译文)。命中就直接复用，不调 API。
        # 用户向上翻历史再滚回底部时，同一条消息会被 OCR 反复读出来，
        # 即使识别有细微偏差也要认得出来。
        self._tm = []
        self._tm_lock = threading.Lock()
        self._tm_dirty = False
        self._tm_saved = 0.0
        self.hits = 0               # 命中次数（给界面显示用）
        self._load_tm()

    @property
    def last_model(self):
        return self._last_model or self._active_model

    # ---------- 翻译记忆 ----------
    def _load_tm(self):
        try:
            with open(TM_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("items", [])
            self._tm = [(r[0], r[1], r[2], r[3]) for r in rows
                        if isinstance(r, list) and len(r) == 4][-TM_MAX:]
        except Exception:
            self._tm = []

    def _save_tm(self, force=False):
        now = time.time()
        if not self._tm_dirty:
            return
        if not force and now - self._tm_saved < TM_SAVE_GAP:
            return
        self._tm_saved = now
        self._tm_dirty = False
        try:
            os.makedirs(os.path.dirname(TM_PATH), exist_ok=True)
            with self._tm_lock:
                items = [list(r) for r in self._tm]
            tmp = TM_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "items": items}, f, ensure_ascii=False)
            os.replace(tmp, TM_PATH)
        except Exception:
            pass

    def remember(self, src, tgt, text, trans):
        """记一条原文->译文"""
        n = tm_norm(text)
        if len(n) < 2 or not trans:
            return
        with self._tm_lock:
            for i, (a, b, c, _) in enumerate(self._tm):
                if a == src and b == tgt and c == n:
                    if self._tm[i][3] != trans:
                        self._tm[i] = (a, b, c, trans)
                        self._tm_dirty = True
                    return
            self._tm.append((src, tgt, n, trans))
            if len(self._tm) > TM_MAX:
                del self._tm[: len(self._tm) - TM_MAX]
            self._tm_dirty = True
        self._save_tm()

    def lookup_tm(self, text, src, tgt):
        """查记忆：先精确（O(1)），再模糊（容忍 OCR 抖动）。

        模糊阈值默认 0.80：日文短句错一个字也就 0.85~0.9，
        隔着一句不同的问候语大约 0.4~0.6，分得开。
        """
        hit = self.cache_get(text, src, tgt)
        if hit is not None:
            self.hits += 1
            return hit

        n = tm_norm(text)
        if len(n) < 4:
            # 太短的不做模糊（三个字差一个字就变意思了），但完全相同仍要认出来
            with self._tm_lock:
                for a, b, key, trans in reversed(self._tm):
                    if a == src and b == tgt and key == n:
                        self.hits += 1
                        return trans
            return None
        th = float(self.cfg.get("ocr", {}).get("tm_threshold", 0.80))
        with self._tm_lock:
            items = self._tm[-TM_SCAN:]

        best, best_r = None, 0.0
        for a, b, key, trans in items:
            if a != src or b != tgt:
                continue
            # 长度差太大的直接跳过，省掉大部分比对
            if abs(len(key) - len(n)) > max(2, int(len(n) * 0.3)):
                continue
            r = _ratio(key, n)
            if r > best_r:
                best_r, best = r, trans
                if r >= 0.999:
                    break
        if best is not None and best_r >= th:
            self.hits += 1
            return best
        return None

    # ---------- public ----------
    def cache_get(self, text, src, tgt):
        with self._lock:
            return self._cache.get((src, tgt, text))

    def translate(self, texts, src=None, tgt=None):
        """返回与 texts 等长的译文列表；失败抛 TranslateError"""
        src = src or self.cfg.get("source_lang", "ja")
        tgt = tgt or self.cfg.get("target_lang", "zh-CN")
        if not texts:
            return []

        out = [None] * len(texts)
        todo, idx = [], []
        for i, t in enumerate(texts):
            # 先查翻译记忆：精确命中走 O(1)，没命中的再模糊比一次。
            # 向上翻历史再滚回来时，同一条消息会被反复读出来，这一步能直接复用旧译文。
            hit = self.lookup_tm(t, src, tgt)
            if hit is not None:
                out[i] = hit
            else:
                todo.append(t)
                idx.append(i)
        if not todo:
            return out

        provider = self.cfg.get("provider", "openai")
        table = {
            "openai": self._openai,
            "deepl": self._deepl,
            "google": self._google,
            "microsoft": self._microsoft,
            "mymemory": self._mymemory,
            "none": lambda ts, s, t: list(ts),
        }
        chain = [provider] + [p for p in FREE_FALLBACK.get(provider, []) if p != provider]
        results, last_err, used = None, None, provider
        for p in chain:
            fn = table.get(p)
            if fn is None:
                continue
            try:
                results = fn(todo, src, tgt)
                used = p
                break
            except Exception as e:      # 免 Key 服务随时可能失效，逐个往下试
                last_err = e
                LOG_FALLBACK.append(f"{p}: {e}")
                continue
        if results is None:
            raise TranslateError(str(last_err) if last_err else "翻译失败")
        self.last_provider = used
        if results is None:
            results = ["?"] * len(todo)
        if len(results) != len(todo):
            results = (results + ["?"] * len(todo))[: len(todo)]
        for i, r in zip(idx, results):
            out[i] = r
            with self._lock:
                self._cache[(src, tgt, texts[i])] = r
                if len(self._cache) > 2000:
                    for k in list(self._cache)[:500]:
                        self._cache.pop(k, None)
            self.remember(src, tgt, texts[i], r)
        return out

    def test(self):
        r = self.translate(["テスト、聞こえますか"], "ja", "zh-CN")
        return r[0]

    def list_models(self):
        """从 /v1/models 拉取可用模型 id 列表"""
        conf = self.cfg.get("openai", {})
        key = (conf.get("api_key") or "").strip()
        base = (conf.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
        try:
            r = requests.get(f"{base}/models",
                             headers={"Authorization": f"Bearer {key}"}, timeout=15)
        except requests.RequestException as e:
            raise TranslateError(f"获取模型列表失败: {e}")
        if r.status_code != 200:
            raise TranslateError(f"HTTP {r.status_code}")
        try:
            data = r.json()
            items = data.get("data", data)
            return [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
        except Exception:
            raise TranslateError("模型列表解析失败")

    # ---------- providers ----------
    def _candidates(self):
        """按优先级返回要尝试的模型列表：当前生效模型优先，其次主模型，再次备用链"""
        conf = self.cfg.get("openai", {})
        primary = (conf.get("model") or DEFAULT_MODEL).strip()
        fallbacks = conf.get("fallbacks") or []
        out = []
        # 上次成功的模型优先（熔断记忆，避免每轮都去撞坏模型）
        if self._active_model:
            out.append(self._active_model)
        for m in [primary] + list(fallbacks):
            m = (m or "").strip()
            if m and m not in out:
                out.append(m)
        return out[:MAX_CANDIDATES]

    def _post_chat(self, base, key, model, sys_prompt, user_content, timeout):
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload, timeout=timeout,
        )
        if r.status_code != 200:
            raise TranslateError(f"HTTP {r.status_code}: {r.text[:120]}")
        try:
            content = r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            raise TranslateError(f"返回格式异常: {r.text[:120]}")
        if not content:
            raise TranslateError("模型返回空内容")
        return content

    def _chat_once(self, base, key, model, sys_prompt, texts, timeout):
        content = self._post_chat(base, key, model, sys_prompt,
                                  json.dumps(texts, ensure_ascii=False), timeout)
        return _parse_json_array(content, len(texts))

    def _chat_dialogue(self, base, key, model, src_name, tgt_name, texts, timeout):
        """把多句聊天当成一段连续对话整体提交，让模型利用上下文。

        比逐条翻译更准（"またね" 在前面聊打副本时和孤立出现时语气不同），
        而且只有一次请求。
        """
        n = len(texts)
        sys_prompt = (
            f"你是游戏内聊天翻译器。下面是同一段游戏聊天里连续的多条玩家发言，"
            f"请逐条翻译成{tgt_name}。\n"
            f"原文语言：{src_name}。\n"
            "要求：把整段当成一个连续对话来理解，前后句可互相参考，翻译要贴合对话氛围；"
            "口语化、贴近玩家聊天语气；保留昵称/颜文字/拟声词的意思；不要解释、不要加引号。\n"
            f"输入共 {n} 行，每行格式「序号. 原文」。输出必须正好 {n} 行，"
            "每行格式「序号. 译文」，序号与输入一一对应。不要输出任何其他内容。"
        )
        user_content = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
        content = self._post_chat(base, key, model, sys_prompt, user_content, timeout)
        return _parse_numbered_lines(content, n)

    def _openai(self, texts, src, tgt):
        conf = self.cfg.get("openai", {})
        key = (conf.get("api_key") or "").strip()
        base = (conf.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
        if not key:
            raise TranslateError("未配置 API Key（设置 → 翻译服务）")

        src_name = LANG_NAMES.get(src, src) if src != "auto" else "（自动识别）"
        tgt_name = LANG_NAMES.get(tgt, tgt)
        sys_prompt = (
            f"你是游戏内聊天翻译器。把用户给出的每一条玩家发言翻译成{tgt_name}。\n"
            f"原文语言：{src_name}。\n"
            "要求：口语化、贴近日常聊天语气；保留昵称/颜文字/拟声词的意思；不要解释、不要加引号、不要合并句子；"
            "输入是 JSON 字符串数组，输出必须是等长同序的 JSON 字符串数组，只输出 JSON，不要任何多余文字。"
        )
        # 多条时走「整段对话」模式，能利用上下文；单条没必要
        use_dialogue = len(texts) > 1

        cands = self._candidates()
        errors = []
        for model in cands:
            # 每个模型：超时时间由短到长（首个请求可能冷启动，给足）
            for attempt, timeout in enumerate((MODEL_TIMEOUT, MODEL_TIMEOUT)):
                try:
                    if use_dialogue:
                        result = self._chat_dialogue(base, key, model, src_name,
                                                     tgt_name, texts, timeout)
                        if result is None:
                            # 行格式没对齐，退回逐条 JSON 模式
                            result = self._chat_once(base, key, model, sys_prompt,
                                                     texts, timeout)
                    else:
                        result = self._chat_once(base, key, model, sys_prompt, texts, timeout)
                    self._active_model = model          # 记住能用的模型
                    self._last_model = model
                    return result
                except requests.Timeout:
                    errors.append(f"{model}: 超时({timeout}s)")
                    break                               # 超时直接换模型，不重试
                except requests.RequestException as e:
                    errors.append(f"{model}: 网络错误")
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    break
                except TranslateError as e:
                    msg = str(e)
                    errors.append(f"{model}: {msg}")
                    if msg.startswith("HTTP 401"):
                        raise                           # 真鉴权失败，换模型无意义
                    # 403 在自建 NAS 上常常是瞬时限流（账号池/并发），退避重试一次
                    if msg.startswith("HTTP 403"):
                        if attempt == 0:
                            time.sleep(1.5)
                            continue
                        break
                    if msg.startswith("HTTP 503") or msg.startswith("HTTP 429"):
                        if attempt == 0:
                            time.sleep(1.2)
                            continue                    # 瞬时繁忙，同模型再试一次
                    break                               # 其他错误：换下一个模型

        self._last_model = ""
        detail = "；".join(errors[:4])
        raise TranslateError(f"所有模型均无响应（已试 {len(cands)} 个）：{detail}")

    def _deepl(self, texts, src, tgt):
        conf = self.cfg.get("deepl", {})
        key = (conf.get("api_key") or "").strip()
        if not key:
            raise TranslateError("未配置 DeepL API Key")
        url = conf.get("endpoint") or "https://api-free.deepl.com/v2/translate"
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"DeepL-Auth-Key {key}"},
                data={"text": texts, "source_lang": DEEPL_LANG.get(src, "JA"),
                      "target_lang": DEEPL_LANG.get(tgt, "ZH")},
                timeout=30,
            )
        except requests.RequestException as e:
            raise TranslateError(f"网络错误: {e}")
        if r.status_code != 200:
            raise TranslateError(f"HTTP {r.status_code}: {r.text[:160]}")
        return [t["text"] for t in r.json().get("translations", [])]

    def _google(self, texts, src, tgt):
        out = []
        for t in texts:
            try:
                r = requests.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={"client": "gtx", "sl": GOOGLE_LANG.get(src, "auto"),
                            "tl": GOOGLE_LANG.get(tgt, "zh-CN"), "dt": "t", "q": t},
                    headers={"User-Agent": UA}, timeout=15,
                )
            except requests.RequestException as e:
                raise TranslateError(f"网络错误(Google 可能被墙): {e}")
            if r.status_code != 200:
                raise TranslateError(f"HTTP {r.status_code}")
            try:
                seg = r.json()[0]
                out.append("".join(s[0] for s in seg if s and s[0]))
            except Exception:
                raise TranslateError("返回解析失败")
        return out


    # ---------- 免 Key 服务 ----------
    def _microsoft(self, texts, src, tgt):
        """Microsoft 翻译（Edge 接口，免 Key）

        令牌约 10 分钟过期：过期会 401，这里重新取一次；还失败就抛错，
        由上层自动换 MyMemory —— 用户不会看到翻译中断。
        """
        params = {"api-version": "3.0", "to": MS_LANG.get(tgt, "zh-Hans")}
        frm = MS_LANG.get(src, "")
        if frm and src not in ("auto", ""):
            params["from"] = frm
        body = [{"Text": t} for t in texts]

        custom = (self.cfg.get("microsoft", {}) or {}).get("auth_url")
        if custom:
            global EDGE_AUTH_URL
            EDGE_AUTH_URL = custom

        for attempt in (0, 1):
            tok = edge_token(force=attempt == 1)
            try:
                r = requests.post(
                    "https://api-edge.cognitive.microsofttranslator.com/translate",
                    params=params,
                    headers={"Authorization": f"Bearer {tok}",
                             "Content-Type": "application/json"},
                    json=body, timeout=20)
            except requests.RequestException as e:
                raise TranslateError(f"微软翻译网络错误: {e}")
            if r.status_code in (401, 403) and attempt == 0:
                continue
            if r.status_code != 200:
                raise TranslateError(f"微软翻译 HTTP {r.status_code}")
            try:
                data = r.json()
                out = [(item.get("translations") or [{}])[0].get("text", "") for item in data]
            except Exception:
                raise TranslateError("微软翻译返回解析失败")
            if len(out) != len(texts) or not all(out):
                raise TranslateError("微软翻译返回条数不符")
            return out
        raise TranslateError("微软令牌反复失效")

    def _mymemory(self, texts, src, tgt):
        """MyMemory（免 Key，按 IP 限流）。一次一条，适合当兜底"""
        pair = f"{MM_LANG.get(src, 'ja-JP')}|{MM_LANG.get(tgt, 'zh-CN')}"
        out = []
        for t in texts:
            try:
                r = requests.get("https://api.mymemory.translated.net/get",
                                 params={"q": t, "langpair": pair},
                                 headers={"User-Agent": UA}, timeout=20)
            except requests.RequestException as e:
                raise TranslateError(f"MyMemory 网络错误: {e}")
            if r.status_code != 200:
                raise TranslateError(f"MyMemory HTTP {r.status_code}")
            try:
                d = r.json()
            except Exception:
                raise TranslateError("MyMemory 返回解析失败")
            txt = (d.get("responseData") or {}).get("translatedText") or ""
            if not txt or str(txt).upper().startswith("MYMEMORY WARNING"):
                raise TranslateError("MyMemory 配额用尽")
            out.append(txt)
        return out


def _parse_numbered_lines(content, expect):
    """解析「1. 译文」这种带序号的逐行输出。

    按序号回填，即使模型多说了几句废话也能对齐。
    对不上的行太少（<60%）时返回 None，让上层退回 JSON 模式。
    """
    out = [None] * expect
    for line in content.splitlines():
        m = re.match(r"^\s*(\d{1,3})\s*[.、．)）:：]\s*(.+?)\s*$", line)
        if not m:
            continue
        i = int(m.group(1)) - 1
        if 0 <= i < expect and out[i] is None:
            txt = m.group(2).strip().strip('"').strip("「」")
            if txt:
                out[i] = txt
    filled = sum(1 for x in out if x)
    if filled < max(1, int(expect * 0.6)):
        return None
    # 个别漏掉的用原文占位，避免整批失败
    return [x if x else "?" for x in out]


def _parse_json_array(content, expect):
    txt = content.strip()
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", txt, re.S)
    if m:
        txt = m.group(1).strip()
    try:
        val = json.loads(txt)
        if isinstance(val, list):
            return [str(x) for x in val]
    except Exception:
        pass
    # 退化：按换行拆分
    lines = [ln.strip(" \t-•\"'") for ln in txt.splitlines() if ln.strip()]
    if len(lines) == expect:
        return lines
    if len(lines) == 1:
        return [lines[0]] * expect
    raise TranslateError(f"翻译返回无法解析（期望 {expect} 条，实得 {len(lines)} 条）")
