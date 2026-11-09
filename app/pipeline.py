"""监听线程：截图 -> 变化检测 -> OCR -> 合并 -> 去重 -> 翻译

专版：一次抓取整个游戏窗口，裁出「聊天面板」和「底部预览」两个固定区域，
各自做变化检测，只有变了的才跑 OCR。
"""
import os
import threading
import time
import traceback

import numpy as np
from PIL import Image

from .capture import ScreenGrabber
from .gamepacks import MAX_OCR_PER_ROUND, panel_expanded
from .logger import LOG
from .ocr_engine import OcrEngine
from .parser import build_messages
from .timeline import ChatTimeline
from .translator import TranslateError, Translator
from .win_track import resolve_zones


ROLLING_RATIO = 0.05     # 变化率超过这个值 = 用户正在滚动，等停稳再 OCR


def _thumb(img, width=200):
    w, h = img.size
    nh = max(1, int(h * width / max(1, w)))
    return np.asarray(img.convert("L").resize((width, nh), Image.BILINEAR), dtype=np.int16)


def _changed_ratio(a, b, hard=45):
    """高对比变化像素占比。

    游戏画面（雨/水波/角色动作）透过半透明聊天框产生的变化幅度小，
    新文字出现是纯白硬边 -> 只有文字变化才会让这个值明显跳起。
    """
    d = np.abs(a - b)
    return float((d > hard).mean())


class Pipeline(threading.Thread):
    def __init__(self, cfg, on_messages, on_status=None, on_error=None):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.on_messages = on_messages
        self.on_status = on_status or (lambda *a, **k: None)
        self.on_error = on_error or (lambda *a, **k: None)

        self.translator = Translator(cfg)
        ocr_conf = cfg.get("ocr", {})
        # 引擎包（用户装的语种）和「显示韩语」是两件事：
        # 包决定能不能认，开关决定认到之后要不要。韩文开关关着就别加载 ko 引擎，
        # 那是实打实的 40% 速度 + 60MB 内存。
        self._pack_langs = list(cfg.get("ocr_langs", ["ja"]))
        self.ko_mode = bool(cfg.get("ko_mode", False))
        active = [x for x in self._pack_langs if x != "ko"]
        if self.ko_mode and "ko" in self._pack_langs:
            active.append("ko")
        if not active:
            active = ["ja"]
        self.ocr = OcrEngine(threads=ocr_conf.get("threads", 4),
                             autocontrast=ocr_conf.get("autocontrast", True),
                             langs=active,
                             det_tiny=bool(ocr_conf.get("det_tiny", True)))
        self.grabber = ScreenGrabber()
        # 聊天时间线：来回滚动时按真实发言顺序拼接，同一条只记一次
        self.timeline = ChatTimeline(
            th=float(ocr_conf.get("tm_threshold", 0.80)))

        # 「韩文」开关：默认关 = 韩语发言全部丢弃。界面上的勾选框改的就是它，
        # 监听线程每帧读一次，所以不用重启、不用重新开始监听。

        self._stop = threading.Event()
        self._running = False
        self._zone_thumb = {}     # zone key -> 上一帧缩略图
        self._zone_miss = {}      # zone key -> 连续「识别了但没有新消息」的轮数
        self._zone_empty = {}     # zone key -> 连续无消息轮数
        self._last_texts = []
        self.stats = {"ocr_ms": 0, "tr_ms": 0, "count": 0, "skipped": 0, "zone": ""}

    # ---------- 控制 ----------
    def stop(self):
        self._stop.set()
        self._running = False

    @property
    def is_running(self):
        return self._running

    def set_ko(self, on):
        self.ko_mode = bool(on)
        langs = [x for x in self._pack_langs if x != "ko"]
        if on and "ko" in self._pack_langs:
            langs.append("ko")
        if langs and langs != self.ocr.langs:
            self.ocr.set_langs(langs)
        LOG.info(f"韩文识别：{'开（放宽过滤，韩语会翻译）' if on else '关（韩语全部丢弃）'}")

    def set_langs(self, pack_langs):
        """用户在设置里换了引擎包（仅日英 / +韩语）"""
        self._pack_langs = list(pack_langs)
        langs = [x for x in self._pack_langs if x != "ko"]
        if self.ko_mode and "ko" in self._pack_langs:
            langs.append("ko")
        if langs:
            self.ocr.set_langs(langs)

    def warmup(self):
        self.ocr.warmup()

    # ---------- 主循环 ----------
    def run(self):
        self._running = True
        self._stop.clear()
        conf = self.cfg.get("ocr", {})
        interval = max(0.2, self.cfg.get("interval_ms", 600) / 1000.0)
        diff_th = cfg_float(conf.get("diff_ratio"), 0.002)
        max_idle = cfg_float(conf.get("idle_max_sec"), 4.0)
        skip_words = self.cfg.get("skip_words", [])
        filter_cn = self.cfg.get("filter_chinese", True)

        self.on_status("running")
        LOG.info(f"开始监听 langs={self.ocr.langs} 间隔={int(interval*1000)}ms "
                 f"变化阈值={diff_th}")
        if self.ocr.failed:
            LOG.error("OCR 引擎加载失败: " + "; ".join(self.ocr.failed))

        cooldown = interval
        while not self._stop.is_set():
            loop_start = time.time()

            zones = resolve_zones(self.cfg)
            if not zones:
                msg = ("未找到游戏窗口（标题含「%s」），请确认游戏已启动"
                       % self.cfg.get("window_title", ""))
                self.on_error(msg)
                LOG.error(msg)
                self._running = False
                self.on_status("stopped")
                return

            try:
                got = self._round(zones, skip_words, filter_cn, diff_th)
            except Exception:
                err = traceback.format_exc(limit=2)[-300:]
                self.on_error("识别异常:\n" + err)
                LOG.error(err)
                got = 0

            # 识别跑过、但一条新消息都没出 —— 画面在动（表情包在播动画）但内容没变。
            # 连续这样就把该区域的变化阈值往上抬，别一直对着同一张图反复 OCR。
            zk = self.stats.get("zone_key")
            if zk:
                if got:
                    self._zone_miss[zk] = 0
                elif self.stats.get("ocr_ran"):
                    self._zone_miss[zk] = self._zone_miss.get(zk, 0) + 1
                    if self._zone_miss[zk] > 40:      # 兜底，别把自己锁死
                        self._zone_miss[zk] = 0

            if got:
                cooldown = interval
            else:
                cooldown = min(cooldown * 1.6, max_idle)

            ocr_sec = self.stats["ocr_ms"] / 1000.0
            min_gap = max(cooldown, ocr_sec * 1.6)
            elapsed = time.time() - loop_start
            self._stop.wait(max(0.05, min_gap - elapsed))

        self._running = False
        self.on_status("stopped")
        LOG.info("停止监听")

    # ---------- 单轮 ----------
    def _round(self, zones, skip_words, filter_cn, diff_th):
        # 一次抓整个窗口，再按区域裁剪（PrintWindow 比截屏贵，只抓一次）
        client_rect = zones[0][4]
        hwnd = zones[0][5]
        full = self._grab_full(client_rect, hwnd) if (client_rect and hwnd) else None

        self.stats["ocr_ran"] = False
        pending = []
        fallback = []          # 面板展开时先跳过的区域，面板没新消息时补扫
        panel_open = False
        for key, name, region, prio, crect, zhwnd in zones:
            img = None
            if full is not None and client_rect:
                cl, ct = client_rect[0], client_rect[1]
                box = (region[0] - cl, region[1] - ct,
                       region[2] - cl, region[3] - ct)
                if box[0] >= 0 and box[1] >= 0 and box[2] <= full.width and box[3] <= full.height:
                    img = full.crop(box)
            if img is None:
                try:
                    img = self.grabber.grab(region)
                except Exception as e:
                    LOG.error(f"截图失败[{name}]: {e}")
                    continue

            # 聊天面板是半透明白遮罩盖出来的，收起时整块都是游戏场景，
            # 扫它只会把主线任务、角色名当成聊天。用饱和度判掉。
            if key == "chat":
                panel_open = panel_expanded(img)
                self.stats["panel"] = "展开" if panel_open else "收起"
                if not panel_open:
                    self._zone_thumb[key] = _thumb(img)
                    continue
            elif panel_open:
                # 面板展开时底部预览通常只是面板里那几条的重复，扫了浪费。
                # 但面板自己停住不动时（玩家往上翻历史、或面板没刷新），
                # 新消息只出现在底部这条窄带上 —— 所以先记下变化率，
                # 等主扫描一条都没扫出来再补扫（见下面 fallback）。
                thumb = _thumb(img)
                last = self._zone_thumb.get(key)
                self._zone_thumb[key] = thumb
                ratio = (None if (last is None or last.shape != thumb.shape)
                         else _changed_ratio(thumb, last))
                fallback.append((prio, key, name, img, ratio))
                continue

            thumb = _thumb(img)
            last = self._zone_thumb.get(key)
            self._zone_thumb[key] = thumb
            # None = 第一帧（还没有参照），必须识别一次；
            # 不能拿 1.0 当「首帧」，那会被下面当成「正在滚动」直接跳过，
            # 结果程序启动后画面不动就永远不识别。
            ratio = (None if (last is None or last.shape != thumb.shape)
                     else _changed_ratio(thumb, last))
            pending.append((prio, key, name, img, ratio))

        if not pending:
            return 0

        # 优先级 + 空转惩罚：一直识别不出内容的区域往后排
        pending.sort(key=lambda x: x[0] + self._zone_empty.get(x[1], 0) * 5)

        collected = []
        done = 0
        rolling = False
        for prio, key, name, img, ratio in pending:
            # 滚动时画面在大面积位移，这一帧 OCR 出来全是碎片
            # （实测静止变化率 0.0000，滚动 0.25），等停稳再识别
            if ratio is not None and ratio > ROLLING_RATIO:
                rolling = True
                continue
            if ratio is not None and ratio < self._th_for(key, diff_th):
                continue
            if done >= MAX_OCR_PER_ROUND:
                break
            msgs = self._ocr_zone(img, skip_words, filter_cn, name,
                                  guard=_scene_guard(key), key=key)
            done += 1
            if msgs:
                collected.extend(msgs)
                self._zone_empty[key] = 0
                # 聊天面板有内容时，底部预览必然包含同样的消息，无需再看
                if key == "chat":
                    break
            else:
                self._zone_empty[key] = self._zone_empty.get(key, 0) + 1

        # 面板这一帧没扫出新东西 —— 补扫底部预览条（窄，~0.3s）。
        # 这正是「面板开着但停在旧位置、新消息只在底部出现」的情况。
        if not collected and not rolling and done < MAX_OCR_PER_ROUND:
            for prio, key, name, img, ratio in fallback:
                if ratio is not None and (ratio < self._th_for(key, diff_th)
                                          or ratio > ROLLING_RATIO):
                    continue
                msgs = self._ocr_zone(img, skip_words, filter_cn, name, key=key)
                if msgs:
                    collected.extend(msgs)
                    self._zone_empty[key] = 0
                    self.stats["zone"] = name + "(补扫)"
                    break
                self._zone_empty[key] = self._zone_empty.get(key, 0) + 1

        self.stats["rolling"] = rolling
        self.stats["skipped"] = self.stats.get("skipped", 0) + (len(pending) - done)
        if not collected:
            return 0
        return self._deliver(collected)

    def _th_for(self, key, diff_th):
        """该区域本轮的变化阈值。

        同一块画面反复识别却没有新消息（表情包动画、loading 光效），说明
        「在动但不是新内容」，把阈值逐级抬到 0.02（新消息实测 ≥0.03）。
        """
        miss = self._zone_miss.get(key, 0)
        return min(0.02, diff_th * (2 ** min(miss, 4)))

    def _ocr_zone(self, img, skip_words, filter_cn, name, guard=None, key=""):
        t0 = time.time()
        lines = self.ocr.recognize(img)
        self.stats["ocr_ms"] = int((time.time() - t0) * 1000)
        self.stats["zone"] = name
        LOG.ocr(lines, self.stats["ocr_ms"])

        dropped = []
        msgs = build_messages(lines, skip_words, filter_cn, dropped,
                              frame_width=img.width, scene_left=guard,
                              ko_mode=self.ko_mode)
        for m in msgs:
            m.zone = name
        if dropped:
            LOG.dropped(dropped)
        self._last_texts = [m.text for m in msgs]
        self._last_lines = lines
        self.stats["zone_key"] = key
        self.stats["ocr_ran"] = True
        return msgs

    def _deliver(self, msgs):
        new = self.timeline.merge(msgs)
        if not new:
            return 0
        LOG.new_msgs(new)

        # 只翻译真正的玩家正文：贴纸、昵称行都不送翻译
        # 中文发言（native）不送翻译 —— 能直接看懂，翻了纯浪费 token
        targets = [m for m in new if not m.sticker and not m.nickname and not m.native]
        max_batch = int(self.cfg.get("ocr", {}).get("max_batch", 30))
        if len(targets) > max_batch:
            targets = targets[-max_batch:]

        texts = [m.body for m in targets]
        t1 = time.time()
        try:
            trans = self.translator.translate(texts) if texts else []
        except TranslateError as e:
            self.on_error(f"翻译失败: {e}")
            LOG.error(f"翻译失败: {e}")
            trans = ["〔未翻译〕"] * len(texts)
        except Exception as e:
            self.on_error(f"翻译异常: {e}")
            LOG.error(f"翻译异常: {e}")
            trans = ["〔未翻译〕"] * len(texts)
        self.stats["tr_ms"] = int((time.time() - t1) * 1000)
        self.stats["count"] += len(new)
        if texts:
            LOG.translation(list(zip(texts, trans)), self.stats["tr_ms"],
                            getattr(self.translator, "last_model", ""))

        tr_map = {id(m): t for m, t in zip(targets, trans)}
        full_trans = []
        for m in new:
            if id(m) in tr_map:
                full_trans.append(tr_map[id(m)])
            elif m.sticker:
                full_trans.append("[表情]")
            elif m.native:
                full_trans.append(m.body)    # 中文原文直接显示，不走翻译
            else:
                full_trans.append(m.body)

        self.on_messages(new, full_trans)
        return len(new)

    # ---------- 截图 ----------
    def _grab_full(self, client_rect, hwnd):
        """抓整个窗口客户区（PrintWindow，可穿透遮挡）"""
        try:
            img, _ = self.grabber.grab_window(hwnd, client_rect)
            return img
        except Exception:
            return None

    def _capture(self, region, client_rect, hwnd):
        """兼容旧接口：优先 PrintWindow，失败回退屏幕截图"""
        if hwnd and client_rect:
            full = self._grab_full(client_rect, hwnd)
            if full is not None:
                cl, ct = client_rect[0], client_rect[1]
                box = (region[0] - cl, region[1] - ct, region[2] - cl, region[3] - ct)
                if box[0] >= 0 and box[1] >= 0 and box[2] <= full.width and box[3] <= full.height:
                    return full.crop(box)
        return self.grabber.grab(region)

    # ---------- 立即识别 ----------
    def snapshot(self):
        """对两个区域各识别一次，返回 (合并消息, 行, 各区域截图)"""
        zones = resolve_zones(self.cfg)
        if not zones:
            raise RuntimeError("未找到游戏窗口")

        client_rect = zones[0][4]
        hwnd = zones[0][5]
        full = self._grab_full(client_rect, hwnd) if (client_rect and hwnd) else None

        skip_words = self.cfg.get("skip_words", [])
        filter_cn = self.cfg.get("filter_chinese", True)
        all_msgs, all_lines, shots, marked = [], [], [], []

        for key, name, region, prio, crect, zhwnd in zones:
            img = None
            if full is not None and client_rect:
                cl, ct = client_rect[0], client_rect[1]
                box = (region[0] - cl, region[1] - ct, region[2] - cl, region[3] - ct)
                if box[0] >= 0 and box[1] >= 0 and box[2] <= full.width and box[3] <= full.height:
                    img = full.crop(box)
            if img is None:
                img = self.grabber.grab(region)
            shots.append((name, img))
            # 聊天面板收起时整块是游戏场景，扫它只会读到任务名/角色名
            if key == "chat" and not panel_expanded(img):
                continue
            msgs = self._ocr_zone(img, skip_words, filter_cn, name,
                                  guard=_scene_guard(key))
            lines = self._last_lines
            all_msgs.extend(msgs)
            all_lines.extend(lines)
            marked.append((name, _mark(img, lines, msgs)))

        try:
            from .logger import LOG_DIR
            for name, img in shots:
                img.save(os.path.join(LOG_DIR, f"zone_{name}.png"))
            for name, img in marked:
                img.save(os.path.join(LOG_DIR, f"marked_{name}.png"))
        except Exception:
            pass
        return all_msgs, all_lines, shots


def _mark(img, lines, msgs):
    """在截图上把识别框画出来，一眼看出漏了什么。

    绿框 = 识别到了并且进了消息，红框 = 识别到但被过滤掉。
    画面上明明有字、却没框的，就是 OCR 没认出来的。
    """
    from PIL import ImageDraw

    kept = set()
    for m in msgs:
        for t in m.lines:
            kept.add(t)

    vis = img.convert("RGB").copy()
    d = ImageDraw.Draw(vis)
    for l in lines:
        ok = l.text in kept
        color = (34, 197, 94) if ok else (239, 68, 68)
        d.rectangle([l.x0, l.y0, l.x1, l.y1], outline=color, width=2)
    return vis


# 聊天面板是半透明遮罩：左沿超过宽度 30% 的文字是背后场景透进来的
# （角色名/招牌/远景点缀字），真实气泡正文都贴着 x0≈5~8% 的左沿。
SCENE_LEFT_RATIO = 0.30


def _scene_guard(key):
    """只有聊天面板要防场景透字；底部预览条每行带「附近 |」标签，正文本来就偏右"""
    return SCENE_LEFT_RATIO if key == "chat" else None


def cfg_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
