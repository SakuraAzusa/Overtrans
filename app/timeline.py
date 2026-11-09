"""聊天时间线：把来回滚动看到的片段拼成一条有序对话

用户的真实操作是「在聊天框里上下滚动」：
  当前显示 3、4 → 向上滚看到 2、3 → 再向上看到 1、2 → 滚回底部又是 3、4

滚动只是视窗在移动，消息本身没变。所以这里维护一条有序时间线，
每帧把看到的那一小段并进来，只记录没见过的，顺序永远是 1、2、3、4。

跟原来「最近 N 条集合」相比：
  - 顺序对：日志、浮窗按真实发言先后排列，不再是你看到的顺序
  - 不受窗口大小限制：滚多远都记得（上限 limit 条）

去重仍容忍 OCR 抖动：`レイド行く人いる？` 和 `レイド行く人いる?`
会被认成同一条。
"""
from difflib import SequenceMatcher


def _ratio(a, b):
    return SequenceMatcher(None, a, b).ratio()


class ChatTimeline:
    def __init__(self, limit=2000, th=0.80, fuzzy_scan=300):
        self.limit = limit
        self.th = th                    # 模糊判同阈值（和翻译记忆一致）
        self.fuzzy_scan = fuzzy_scan    # 精确找不到时，往回扫多少条做模糊
        self.items = []                 # [{"norm","text","seq"}]
        self.seq = 0
        self._pos = {}
        self._last_y = {}               # norm -> 上一帧的 y0（判断滚动方向用）
        self._dirty = False

    def __len__(self):
        return len(self.items)

    # ---------- 查询 ----------
    def _rebuild(self):
        self._pos = {}
        for i, it in enumerate(self.items):
            self._pos.setdefault(it["norm"], i)

    def _find(self, norm):
        """精确 + 模糊都找，返回下标，找不到 -1"""
        i = self._pos.get(norm)
        return i if i is not None else self._fuzzy(norm)

    def _fuzzy(self, norm):
        """只在最近 fuzzy_scan 条里按相似度找（容忍 OCR 抖动）"""
        if len(norm) < 4:
            return -1                    # 太短不做模糊，差一个字就变意思
        start = max(0, len(self.items) - self.fuzzy_scan)
        for j in range(len(self.items) - 1, start - 1, -1):
            k = self.items[j]["norm"]
            if abs(len(k) - len(norm)) > max(3, int(len(norm) * 0.4)):
                continue
            if _ratio(k, norm) >= self.th:
                return j
        return -1

    # ---------- 写入 ----------
    def _insert(self, idx, msg):
        self.items.insert(idx, {"norm": msg.key, "text": msg.text,
                                "seq": 0, "msg": msg})
        self._dirty = True               # 索引在 merge 结束时统一重建，避免每插一条扫一遍

    def _renumber(self):
        """编号 = 在时间线里的位置。

        不能拿「第几个插入的」当序号 —— 向上翻历史时新消息是插在中间的，
        插入顺序和发言顺序对不上（会看到 #1 排在 #2 后面）。
        """
        for i, it in enumerate(self.items):
            it["seq"] = i
            m = it.get("msg")
            if m is not None:
                m.seq = i

    def _trim(self):
        if len(self.items) > self.limit:
            del self.items[: len(self.items) - self.limit]
            self._rebuild()

    def _scroll_dir(self, msgs):
        """靠同一批消息的 y 位移判断是向上翻历史还是向下翻回底部。

        y 变大 = 内容往下走 = 用户在向上滚（看更早的）。
        """
        dys = []
        for m in msgs:
            prev = self._last_y.get(m.key)
            if prev is not None:
                dys.append(m.y0 - prev)
        if not dys:
            return 0
        avg = sum(dys) / len(dys)
        if avg > 4:
            return 1        # 向上翻（更早）
        if avg < -4:
            return -1       # 向下翻（更新）
        return 0

    def merge(self, msgs):
        """把这一帧看到的一段连续消息并进时间线，返回真正新增的（按时间顺序）"""
        if not msgs:
            self._last_y = {}
            return []

        direction = self._scroll_dir(msgs)
        norms = [m.key for m in msgs]
        # 先整批走精确匹配（O(1)），一条都对不上再拿首尾两条试模糊。
        # 全量模糊的话，一帧 20 条就要比 6000 次，没必要。
        hits = [self._pos.get(n, -1) for n in norms]
        anchor_i = next((i for i, p in enumerate(hits) if p >= 0), None)
        if anchor_i is None and self.items:
            for cand in (0, len(norms) - 1):
                p = self._fuzzy(norms[cand])
                if p >= 0:
                    hits[cand] = p
                    anchor_i = cand
                    break
        self._last_y = {m.key: m.y0 for m in msgs}

        # 一条都不认识：整段都是新的
        if anchor_i is None:
            if direction > 0 and self.items:
                for k, m in enumerate(msgs):        # 向上翻到更早的地方 —— 接最前面
                    self._insert(k, m)
            else:
                for m in msgs:                      # 默认当作新消息接到末尾
                    self._insert(len(self.items), m)
            self._rebuild()
            self._trim()
            self._renumber()
            return list(msgs)

        new = []
        # 锚点之前的连续新消息：插到锚点前面（这些是更早说的话）
        head = []
        for i in range(anchor_i - 1, -1, -1):
            if hits[i] >= 0:
                break
            head.insert(0, msgs[i])
        base = self._find(norms[anchor_i])
        for k, m in enumerate(head):
            self._insert(base + k, m)
        new.extend(head)

        # 从锚点往后走：认识的就跳过（已经记过），不认识的顺次插在它后面。
        # 位置全部用 hits 推算，不在插入过程中再查索引——那时 _pos 是脏的。
        offset = len(head)               # 相对「原始 hits」的偏移量
        last_known = base + len(head)
        for i in range(anchor_i, len(msgs)):
            if hits[i] >= 0:
                last_known = (hits[i] + offset) if hits[i] >= base else hits[i]
                continue
            last_known += 1
            self._insert(last_known, msgs[i])
            offset += 1
            new.append(msgs[i])

        self._rebuild()
        self._trim()
        self._renumber()
        return new

    # ---------- 给界面/日志用 ----------
    def tail(self, n=30):
        return self.items[-n:]

    def reset(self):
        self.items.clear()
        self._pos.clear()
        self._last_y.clear()
        self.seq = 0
