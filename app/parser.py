"""把 OCR 行合并成聊天消息"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .ocr_engine import Line

NICK_SYMBOLS = set("♡♥★☆❤♪♬✩✦")
_CTRL = re.compile(r"[\u0000-\u001f]")
# 日语全角冒号 / 半角冒号
_SENDER_SPLIT = re.compile(r"^(?P<nick>.{1,16}?)[:：](?P<body>.+)$")

# 语种特征字符（注意：U+30FB「・」是标点不是假名，必须排除，否则 OD・TEMPO 会被当成日语）
_HAN = re.compile(r"[\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3041-\u309f\u30a1-\u30fa\u30fc-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")
_LATIN = re.compile(r"[A-Za-z]")


@dataclass
class Message:
    text: str
    y0: int
    y1: int
    sender: str = ""
    nickname: bool = False
    sticker: bool = False
    native: bool = False      # 中文玩家发言：显示原文，不送翻译（省 token）
    zone: str = ""
    seq: int = -1             # 在聊天时间线里的序号（由 timeline 赋值）
    lines: list = field(default_factory=list)

    @property
    def key(self):
        return normalize(self.text)

    @property
    def body(self):
        """去掉「昵称：」前缀后的正文（若有）"""
        m = _SENDER_SPLIT.match(self.text)
        if m and m.group("nick") and len(m.group("body").strip()) >= 1:
            return m.group("body").strip()
        return self.text


def normalize(s):
    s = _CTRL.sub("", s)
    s = re.sub(r"\s+", "", s)
    s = s.replace("，", ",").replace("。", ".")
    return s


def similar(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _lang_of(text):
    """粗分语种，用来判断两块文本是不是同一类东西。

    昵称的语言跟正文的语言往往不同（陈浩南 / 阿川ゆき / 람지 / anomachine），
    而同一个人的昵称和正文通常同语种。合并前用这个挡掉「两个昵称粘一起」。
    """
    has_kana = bool(_KANA.search(text))
    has_hangul = bool(_HANGUL.search(text))
    has_han = bool(_HAN.search(text))
    has_latin = bool(_LATIN.search(text))
    kinds = sum([has_kana, has_hangul, has_han, has_latin])
    if kinds > 1:
        return "mix"
    if has_kana:
        return "kana"
    if has_hangul:
        return "hangul"
    if has_han:
        return "han"
    if has_latin:
        return "latin"
    return "other"


def _is_chinese_ui(text):
    """判定为游戏中文 UI（界面文字/任务栏/钓鱼说明），不是玩家发言。

    允许混入少量拉丁字母——比如「我昨天問AI每日任務能拿ROTA積分」这种
    中文里夹英文缩写的聊天，汉字占比仍然很高，应当算中文而非外语。
    """
    s = re.sub(r"\s+", "", text)
    if not s:
        return False
    han = len(_HAN.findall(s))
    latin = len(_LATIN.findall(s))
    kana = len(_KANA.findall(s))
    hangul = len(_HANGUL.findall(s))
    if han < 2:
        return False

    # 汉字占绝对多数时，混进来的零星假名/韩文是 OCR 把隔壁场景文字粘上来了
    # （「不知道你說的那2隻什麼時候復刻 2に证」尾巴拖了个「に」）。
    # 不能因为一个字就把整条繁中聊天当外语送去翻译。
    if kana or hangul:
        if han >= 3 and (kana + hangul) <= max(2, len(s) * 0.2):
            return latin <= len(s) * 0.45
        return False

    # 汉字占一定分量、且不是被拉丁字母主导 —— 「1600失的」「稻采摸金子的狗risu」
    # 这类场景残片也算。0.45 能多拦一批中英混排的 UI，又不会误伤真实外语聊天。
    return latin <= len(s) * 0.45


# 底部预览条的每行都带频道标签：`附近 | 昵称：内容`。
# OCR 常把竖线认成「」丨I 之类，这里统一剥掉，否则整行会被 skip_words 当 UI 词扔掉。
_CHANNEL_PREFIX = re.compile(
    r"^\s*(附近|世界|系统|私聊|队伍|公会|招募|小队)\s*[|｜丨│┃」』'`Il1!！]?\s*")


_HANGUL_ALL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")


def is_korean(text):
    """这条是不是韩语（按字符占比判，韩文聊天里常混少量汉字）"""
    s = re.sub(r"\s+", "", text or "")
    if len(s) < 2:
        return False
    hangul = len(_HANGUL_ALL.findall(s))
    return hangul >= 2 and hangul >= len(s) * 0.4


def strip_channel_prefix(text):
    """剥掉行首的频道标签（附近/世界/…）及其后的竖线"""
    return _CHANNEL_PREFIX.sub("", text, count=1)


def _drop_reason(text, skip_words, filter_chinese=True, ko_relax=False):
    """返回丢弃原因，None 表示保留

    ko_relax: 打开「韩文」时对韩语行放宽 —— 韩语单字（펜 / ㅋ）和
              「字母太短凑不成句」这两条会误杀真发言。
    """
    if _is_sticker(text.strip()):
        return None           # 贴纸占位符交给后续 sticker 逻辑处理
    relax = ko_relax and bool(_HANGUL_ALL.search(text))
    t = normalize(strip_channel_prefix(text))
    if not t:
        return "空行"
    if len(t) <= 1 and not relax:
        return "过短"
    # 空的括号残片 / 纯符号
    if re.fullmatch(r"[\[\]【】（）()<>＜＞\s]+", t):
        return "符号"
    if not relax and not re.search(r"[ぁ-んァ-ヶ一-龥a-zA-Z\uac00-\ud7a3]{2,}", t):
        return "无文字"
    # 同一个词连着两遍（람지 람지）—— 是上下两个昵称粘一起了
    # 注意用原文 split：normalize 会把空格删掉，这里就分不出两段了
    parts = strip_channel_prefix(text).split()
    if len(parts) == 2 and parts[0] == parts[1]:
        return "重复昵称"
    for w in skip_words:
        wn = normalize(w)
        if not wn:
            continue
        if t == wn:
            return "UI词"
        if t.startswith(wn) and len(t) <= len(wn) + 3:
            return "UI词"
        # OCR 常把「【主线】暂时分别」切碎成「分别」「主【樱庭世界】」，
        # 严格前缀匹配会漏，这里对长一点的 UI 词做包含匹配
        if len(wn) >= 3 and wn in t and len(t) <= len(wn) + 6:
            return "UI词"
    if filter_chinese and _is_chinese_ui(t):
        return "中文UI"
    # 注意用原文判断：normalize 会把空格去掉，单词结构就没了
    if _is_noise_latin(text.strip()):
        return "装饰字"
    return None


def _is_ui_word(text, skip_words):
    return _drop_reason(text, skip_words) is not None


# 表情/贴纸占位符：形如 [xxx] 或 [表情名]
_STICKER = re.compile(r"^[\[【（(].{0,8}[\]】）)]$")


# 贴纸记号：[哦嗨哟] / 【表情】 / （笑） / []
_STICKER_TOKEN = re.compile(r"[\[【（(][^\[\]【】（）()]{0,8}[\]】）)]")


def _is_pure_sticker(text, sender=""):
    """正文里除了贴纸记号和昵称本身，一个实字都没有

    `Schylvr：[哦嗨哟]Schylvr:[]` / `Schylvr: ：[哦嗨哟][]` 这些其实都是同一条
    表情消息被 OCR 切成了几块，归一化后文本才稳定，跨帧去重才认得出。
    """
    t = _STICKER_TOKEN.sub("", text or "")
    if sender:
        t = t.replace(sender, "")
    t = re.sub(r"[\s:：,，.。!！?？~〜\-—…·・\[\]【】（）()]", "", t)
    return len(t) == 0


def _is_sticker(text):
    """贴纸/表情占位符（如 [疑惑] [大头]），无实际文字内容，跳过翻译。

    注意别把【樱庭世界·昼】这种带括号的任务栏文字误判成表情名。
    """
    s = text.strip()
    if not _STICKER.match(s):
        return False
    inner = s[1:-1].strip()
    if not inner:
        return False                              # 空的 [] ，不是表情
    if len(inner) > 6:
        return False                              # 太长，是句子不是表情名
    if re.search(r"[、。,.!?·・\-ー~〜]", inner):
        return False                              # 有标点/连接符，是句子
    return True


def _is_noise_latin(text):
    """拉丁字母但不像人话——贴纸/海报/装饰字上 OCR 出来的碎片。

    例（实测会误当消息的）：M.clover / y clover Clover_ / OD・TEMPO DD・T
    判据：单词数太少、单词太短、含全角中点、或单词重复。
    真实英文聊天（"Anyone up for a raid tonight?" / "GG well played"）能通过。
    """
    if not _LATIN.search(text):
        return False
    if _KANA.search(text) or _HANGUL.search(text) or _HAN.search(text):
        return False          # 混了别的语种，不算纯装饰拉丁
    if any(ch in text for ch in "・·•‧"):   # 中点分隔的代号：OD・TEMPO DD・T
        return True
    words = [w for w in re.split(r"\s+", text) if re.search(r"[A-Za-z]", w)]
    if len(words) < 2:
        # 单个词：纯字母（无数字/无点/无下划线）且 3~14 个字符，是英文昵称
        # （Orcal / Vicky / Meteor / Feltwins）或短句（lol / yes），
        # 放行让后面的「昵称+正文」合并逻辑处理。
        # 贴纸碎片通常带点或数字（M.clover / Clover_4 / 2QLO），仍算装饰字。
        if re.fullmatch(r"[A-Za-z]{3,14}[!?]*[-~〜ー]*", text.strip()):
            return False
        return True
    # 罗马音短语：「maji de」「sou na yo」「yabai yo」——全是小写、2~4 个词、
    # 总字母 ≥5，是一个人真在说话，不是贴纸碎片。原来按「总字母 <8 就算碎片」
    # 全丢了（实测 maji de 被判成装饰字），这类短句恰恰是外语频道最常见的回复。
    letters = sum(len(re.sub(r"[^A-Za-z]", "", w)) for w in words)
    if (2 <= len(words) <= 4 and letters >= 5
            and all(re.fullmatch(r"[a-z]+[,.!?~〜ー]*", w) for w in words)):
        return False
    if letters < 8:
        return True           # 太短，凑不成一句
    for w in words:
        core = re.sub(r"[^A-Za-z]", "", w)
        if len(core) == 1 and core.lower() not in ("a", "i"):
            return True       # 出现无意义单字母词（y clover Clover_）
    uniq = {re.sub(r"[^a-z]", "", w.lower()) for w in words}
    if len(uniq) < 2:
        return True           # 同一个词重复
    return False


def _join_parts(parts):
    """把一行被切碎的几段拼回去，拉丁字母之间补空格。"""
    out = parts[0]
    for p in parts[1:]:
        if re.search(r"[A-Za-z0-9]$", out) and re.search(r"^[A-Za-z0-9]", p):
            out += " " + p
        else:
            out += p
    return out


def merge_same_line(lines, skip_norm=None, frame_width=None,
                    scene_left=None, scene_drop=None):
    """把「同一水平行被 OCR 切成多块」的拼回一行。

    实测一条 `Alway short hair and boy costume` 会被拆成 6 个框，
    不拼回去的话每一块都会变成一条独立消息。

    做法：先按**行中心 y** 聚类（差 ≤2px 算同一行），再在行内按 x 排序拼接。
    不同消息哪怕挨得很近（正文和下一个昵称），中心也会差 3px 以上，不会被并进来。

    skip_norm/frame_width: 传了就顺手摘掉右对齐的频道贴片（「附近 |」），
    否则它会被拼到正文尾巴上变成 `Yuina_Mia附近`。
    """
    rows = []
    for l in sorted(lines, key=lambda x: (x.y0 + x.y1) / 2):
        cy = (l.y0 + l.y1) / 2
        for row in rows:
            if abs(cy - row[0]) <= 2:
                row[1].append(l)
                break
        else:
            rows.append([cy, [l]])

    out = []
    for cy, items in rows:
        if len(items) > 1 and skip_norm and frame_width:
            items = [x for x in items
                     if not (normalize(x.text) in skip_norm
                             and x.x0 > frame_width * 0.5)]
        if not items:
            continue
        if len(items) == 1:
            out.append(items[0])
            continue
        items.sort(key=lambda x: x.x0)
        if scene_left and frame_width:
            items = _drop_scene_runs(items, frame_width * scene_left, scene_drop)
            if not items:
                continue
            if len(items) == 1:
                out.append(items[0])
                continue
        items = _dedupe_overlap(items)
        out.append(Line(
            _join_parts([x.text for x in items]),
            items[0].x0, min(x.y0 for x in items),
            max(x.x1 for x in items), max(x.y1 for x in items),
            sum(x.score for x in items) / len(items),
        ))
    out.sort(key=lambda l: (l.y0, l.x0))
    return out


def _drop_scene_runs(items, left_limit, scene_drop=None):
    """把同一行里「从画面中间开始」的那一段当成场景透字扔掉。

    聊天面板是半透明的，背后的角色名/招牌会透进来，而且常常和左侧的真昵称
    落在同一行（`Feltwins` + `Yopi_Sayang Corahak`）。所以按 x 间隔切段，
    从左边沿（≤宽度 8%）起步的段才是气泡内容，从中间起步的整段丢掉。

    切段的间隔阈值取 2 倍行高：一句话被 OCR 切成几块时块间空隙很小，
    不会误切；而场景文字和左侧昵称之间隔着整片空白。
    """
    runs = []
    for it in items:
        if runs:
            prev = runs[-1][-1]
            h = max(prev.h, it.h, 8)
            if it.x0 - prev.x1 > h * 2.0:
                runs.append([it])
                continue
            runs[-1].append(it)
        else:
            runs.append([it])

    keep = []
    for r in runs:
        if r[0].x0 > left_limit:
            if scene_drop is not None:
                scene_drop.append((_join_parts([x.text for x in r]), "场景透字"))
            continue
        keep.extend(r)
    return keep


def _dedupe_overlap(items):
    """同一行里被 OCR 重复检出的同一段文字，只留一条。

    半透明底上同一行常被认出两次（`Hello Stricker` + `Hello Stricker`，
    或 `Feltwins: hahaha` + 漏了首字母的 `eltwins:hahaha`），
    拼起来会变成「Hello Stricker Hello Stricker」这种重复句。
    判据：水平区间重叠过半 + 文本相似 ≥0.7 —— 真正的多块切分不会重叠。
    重叠时留**更长**的那条：认漏首字母的碎片往往 score 更高，
    按 score 取会把整句换成碎片（Hello Stricker → Stricker）。
    """
    out = []
    for it in items:
        w = it.x1 - it.x0
        dup = False
        for j, o in enumerate(out):
            ow = o.x1 - o.x0
            ov = min(it.x1, o.x1) - max(it.x0, o.x0)
            if w <= 0 or ow <= 0 or ov < min(w, ow) * 0.5:
                continue
            ni, no = normalize(it.text), normalize(o.text)
            contained = len(ni) < len(no) and ni in no
            if contained or similar(ni, no) >= 0.7:
                if len(normalize(it.text)) > len(normalize(o.text)):
                    out[j] = it
                elif len(normalize(it.text)) == len(normalize(o.text)) and it.score > o.score:
                    out[j] = it
                dup = True
                break
        if not dup:
            out.append(it)
    return out


def build_messages(lines, skip_words=(), filter_chinese=True, dropped=None, frame_width=None,
                   scene_left=None, ko_mode=False):
    """行 -> 消息块

    折行的判据：上一行「写满了气泡宽度」才会续接到下一行（自动换行必然撑满）。
    仅靠行距不够可靠——昵称行和正文的行距常常一样。

    frame_width: 截图区域宽度。用来判断「短窄行=昵称」，不传则退回本帧最宽行，
                 但全帧都是窄文本时会失效，所以生产环境务必传入。
    scene_left:  聊天面板是半透明遮罩，背后的角色名/招牌会透进来。真实发言一定
                 贴着气泡左沿（实测 x0≈28~43，占宽度 5%~8%），透字则散落在中间。
                 传 0.30 表示「左沿超过宽度 30% 的行不算发言」。只对面板开，底部预览
                 每条都带「附近 |」标签、正文本来就偏右，不能开。
    ko_mode:  「韩文」开关。关（默认）时韩语发言全部丢弃 —— 韩语玩家少、基本聚在
              同一个频道，留着只占版面还烧 token；开时保留，并放宽按昵称猜的那些过滤
              （韩语短句很容易被误判成昵称行）。
    dropped: 传入 list 时，会填入 [(文本, 丢弃原因)]，供日志排查
    """
    if dropped is None:
        dropped = []
    skip_norm = {normalize(w) for w in skip_words if normalize(w)}
    # OCR 常把一句话切成好几块，先拼回整行，否则一块就是一条消息
    # （顺手摘掉右对齐的频道贴片，不然会粘成 `Yuina_Mia附近`）
    lines = merge_same_line(lines, skip_norm, frame_width,
                            scene_left=scene_left, scene_drop=dropped)
    # 再按位置排好：判断「中文昵称」要看它的下一行
    lines = sorted(lines, key=lambda l: (l.y0, l.x0))

    # 气泡右沿必须按**全部**行算，不能按过滤后的行算：场景透字被滤掉之后
    # 最右沿会缩到正文中间，折行判断立刻失真（`our's motion` 会被判成写满
    # 一整行，把下一条的昵称 `Vicky` 接上去）。
    all_right = max((l.x1 for l in lines if l.score >= 0.5), default=0)

    kept = []
    seen = []          # [(norm, y0)] —— 同一个位置被 OCR 重复检出时只留一条
    for l in lines:
        if l.score < 0.5:
            dropped.append((l.text, "低置信"))
            continue
        if scene_left and frame_width and l.x0 > frame_width * scene_left:
            dropped.append((l.text, "场景透字"))
            continue
        nl = normalize(l.text)
        if any(nl == sn and abs(l.y0 - sy) < 22 for sn, sy in seen):
            dropped.append((l.text, "重复检出"))
            continue
        seen.append((nl, l.y0))
        # 中文暂不判：游戏 UI 和玩家的中文发言长得一样，得等合并出昵称才分得清。
        # 中文判断放到最后，按「有没有昵称」来决定是 UI 还是玩家发言。
        why = _drop_reason(l.text, skip_words, False, ko_relax=ko_mode)

        if why:
            dropped.append((l.text, why))
        else:
            kept.append(l)
    lines = kept
    if not lines:
        return []

    max_right = all_right or max(l.x1 for l in lines)
    min_left = min(l.x0 for l in lines)
    content_w = frame_width if frame_width else max(1, max_right - min_left)

    groups = []
    cur = [lines[0]]
    for ln in lines[1:]:
        prev = cur[-1]
        h = max(prev.h, ln.h, 8)
        gap = ln.y0 - prev.y1
        full_line = prev.x1 >= max_right - h * 1.1
        aligned = abs(ln.x0 - prev.x0) <= h * 0.7
        # 底部预览条每行都以频道标签（附近/世界…）开头 —— 一定是新的一条，
        # 绝不能当成上一行的折行续接，否则三条消息会被粘成一句
        new_row = strip_channel_prefix(ln.text) != ln.text
        if full_line and aligned and gap <= h * 0.85 and not new_row:
            # 同一行被认成上下两簇（y 差 3~5px）时会在这里接回来 ——
            # 内容一样就是重复检出，别拼成「Hello Stricker Hello Stricker」
            if cur and similar(normalize(prev.text), normalize(ln.text)) >= 0.7:
                continue
            cur.append(ln)
        else:
            groups.append(cur)
            cur = [ln]
    groups.append(cur)

    # 先把每块整理成 dict，便于做「昵称+正文」合并
    blocks = []
    for g in groups:
        text = re.sub(r"\s+", " ", " ".join(l.text.strip() for l in g)).strip()
        # 底部预览每行带「附近 |」频道标签，先剥掉，昵称解析才准
        text = strip_channel_prefix(text).strip()
        if not text:
            continue
        blocks.append({
            "text": text,
            "width": max(l.x1 for l in g) - min(l.x0 for l in g),
            "h": max(l.h for l in g),
            "y0": g[0].y0,
            "y1": g[-1].y1,
            "lines": [l.text for l in g],
        })

    def strip_channel(name):
        """OCR 偶发把频道标签（附近/世界…）混进昵称"""
        for w in skip_words:
            if name.startswith(w) and len(name) > len(w):
                return name[len(w):].lstrip("・· ")
        return name

    msgs = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        sender, body, nick = "", b["text"], False

        m = _SENDER_SPLIT.match(b["text"])
        if m and m.group("body").strip() and len(m.group("nick")) <= 16:
            # 已经是「昵称：正文」结构
            sender = strip_channel(m.group("nick").strip())
            body = m.group("body").strip()
        else:
            key0 = normalize(b["text"])
            short_narrow = len(key0) <= 12 and b["width"] < content_w * 0.45
            # 开了「韩文」时，韩语块不走下面这些「按长度/语种猜昵称」的规则：
            # 韩语短句（뭐야 / 진짜?）本身就短，会被误判成昵称整条丢掉。
            ko_here = ko_mode and is_korean(b["text"])
            merged = False
            if short_narrow and i + 1 < len(blocks):
                nb = blocks[i + 1]
                gap = nb["y0"] - b["y1"]
                nk = normalize(nb["text"])
                nb_short = len(nk) <= 12 and nb["width"] < content_w * 0.45

                # 同一个名字重复（람지 / 람지）—— 是下一条消息的昵称，不是正文
                if nk == key0:
                    dropped.append((b["text"], "昵称(重名)"))
                    i += 1
                    continue

                # 两个都是短窄块、语种还不一样 —— 是两个不同玩家的昵称
                # 粘在一起了（东浩南 + 阿川ゆき）。绝不能合并，否则会翻出
                # 「东浩南阿川雪」这种把两个名字当一句话的结果。
                if nb_short and not ko_here and _lang_of(b["text"]) != _lang_of(nb["text"]):
                    dropped.append((b["text"], "昵称"))
                    i += 1
                    continue

                # 同组行距实测是 h 的 0.32~0.36，跨组是 0.54~0.68。
                # 行高取两者较大的：小写昵称（meron）只有 25px，而正文 39px，
                # 拿昵称的高度当尺子会把「昵称+正文」判成两条消息。
                if gap <= max(b["h"], nb["h"]) * 0.55:
                    sender = strip_channel(b["text"])
                    body = nb["text"]
                    msg = Message(
                        text=f"{b['text']}：{nb['text']}",
                        y0=b["y0"], y1=nb["y1"], sender=sender, nickname=False,
                        lines=b["lines"] + nb["lines"],
                    )
                    msg.sticker = bool(_is_sticker(body))
                    msgs.append(msg)
                    i += 2
                    merged = True
            if merged:
                continue
            # 场景里漂浮的玩家名（角色头顶的名字），形如「せいや#2」。
            # 聊天框半透明时它会透进识别区，反复被当成新消息。
            if re.search(r"[#＃]\d{1,3}$", b["text"]) and len(key0) <= 12:
                dropped.append((b["text"], "场景玩家名"))
                i += 1
                continue

            # 表情包消息：玩家发的是大图，游戏只画昵称，图本身没有文字。
            # 表现为短窄块后面隔着一大段空白才出现下一行文字。
            # 「ネラル またね!」这种被 OCR 粘成一行的要留住（含空格 = 有正文），
            # 纯昵称是连续的一串字，不会带空格。
            plain = b["text"].strip()
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            gap_next = (nxt["y0"] - b["y1"]) if nxt else 10 ** 6
            if (short_narrow and len(key0) <= 12 and " " not in plain
                    and gap_next > b["h"] * 2):
                dropped.append((b["text"], "昵称(表情消息)"))
                i += 1
                continue

            # 孤立昵称：游戏把「昵称」和「正文」分两行画，OCR 有时只抓到昵称
            # （新消息刚出现、正文还没渲染，或正文那行没认出来）。
            # 昵称一定是某条正文的前缀 ——「ネラル」是「ネラル またね!」的前缀。
            # 真实短回复（うん / 怖すぎ）不会是别的消息的前缀，不会被误伤。
            if short_narrow and not ko_here and len(key0) <= 10 and i + 1 >= len(blocks):
                prefix_of_other = any(
                    ob is not b and normalize(ob["text"]).startswith(key0)
                    for ob in blocks
                )
                if prefix_of_other:
                    dropped.append((b["text"], "昵称"))
                    i += 1
                    continue

            # 独立块：只有带符号的才认定是纯昵称行（其余按正常消息翻译）
            nick = any(ch in NICK_SYMBOLS for ch in b["text"]) and len(key0) <= 16

        sticker = bool(body and _is_sticker(body) and sender)
        msg = Message(
            text=(f"{sender}：{body}" if sender else body),
            y0=b["y0"], y1=b["y1"], sender=sender, nickname=nick, lines=b["lines"],
        )
        msg.sticker = sticker
        msgs.append(msg)
        i += 1

    # 正文里一个「字」都没有（只剩括号/竖线/数字）—— OCR 只认到了表情框；
    # 或者正文是多段短名字（생크림 우나나 생크림）—— 上下几个昵称粘成一行了
    kept2 = []
    for m in msgs:
        if not re.search(r"[ぁ-んァ-ヶ一-龥a-zA-Z\uac00-\ud7a3\u3131-\u318e]", m.body):
            dropped.append((m.text, "空正文"))
            continue
        # 三个短名字粘一行（생크림 우나나 생크림）。
        # 要求三段**等长**且都 ≤4 字符：真实短句很少这么整齐
        # （「life is suk」是 4/2/3，「펜쟝 중국어로 들어와서 했나보다」段数更多），
        # 只用「都很短」判定会把正常消息吃掉。
        parts = m.body.split()
        if (not (ko_mode and is_korean(m.body))
                and len(parts) == 3 and len({len(x) for x in parts}) == 1
                and len(parts[0]) <= 4):
            dropped.append((m.text, "昵称列表"))
            continue
        kept2.append(m)
    msgs = kept2

    # 表情包（贴纸）消息归一化。
    # 游戏里玩家发表情时画的是一个「昵称 + 贴纸框内的表情名」，OCR 每次切法都不一样：
    # `Schylvr：[哦嗨哟]` / `Schylvr:` `：[哦嗨哟]` `[]` / `[哦嗨哟]` `Schylvr:` `[]`，
    # 拼出来的文本每帧都变，跨帧去重认不出是同一条 —— 于是同一条表情被反复当新消息，
    # 还会被送去翻译（翻了也是原样返回，纯烧 token）。
    # 这里把「除了贴纸记号什么都没有」的消息统一成 `昵称：[表情]`，文本就稳定了。
    for m in msgs:
        if m.sticker:
            m.text = f"{m.sender}：[表情]" if m.sender else "[表情]"
            continue
        if _is_pure_sticker(m.body, m.sender):
            m.sticker = True
            m.text = f"{m.sender}：[表情]" if m.sender else "[表情]"

    # 同一帧里同一个人被识别成「截断版 + 完整版」两条（底部预览条滚动时会这样，
    # `Vicky: bye` / `Vicky: bye bye dayo-`）：短的只是长的前缀，丢掉别翻两遍
    dedup = []
    for m in msgs:
        body = normalize(m.body)
        cut = False
        for o in msgs:
            ob = normalize(o.body)
            if o is m or not body or not ob:
                continue
            if o.sender and m.sender and o.sender != m.sender:
                continue
            if len(body) < len(ob) and ob.startswith(body):
                cut = True
                break
            # 同一条被认成「带昵称」和「没带昵称」两份，留带昵称的
            if body == ob and not m.sender and o.sender:
                cut = True
                break
        if cut:
            dropped.append((m.text, "截断重复"))
            continue
        dedup.append(m)
    msgs = dedup

    # 中文判断放到这里：游戏 UI 和玩家发言都是中文，唯一的区别是
    # 玩家发言带昵称（「可爱小咕嘎：还有蓝也是的…」），UI 不带
    # （「鲫鱼主要是以植物为食的杂食性鱼」）。
    # 带昵称的中文不送翻译 —— 能直接看懂，翻了纯浪费 token。
    if filter_chinese:
        final = []
        for m in msgs:
            if not _is_chinese_ui(m.body):
                final.append(m)
                continue
            if m.sender:
                m.native = True
            elif m.nickname:
                dropped.append((m.text, "昵称"))
                continue
            else:
                dropped.append((m.text, "中文UI"))
                continue
            final.append(m)
        msgs = final

    # 韩语总闸：默认全丢。韩语玩家少、基本聚在同一个频道，不收就不会占版面、
    # 也不会白花 token；想临时看就点界面上的「韩文」，那一刻起实时生效。
    if not ko_mode:
        ko_kept = []
        for m in msgs:
            if is_korean(m.body):
                dropped.append((m.text, "韩文(未开)"))
                continue
            ko_kept.append(m)
        msgs = ko_kept

    return msgs


class MessageTracker:
    """跨帧去重：只把新出现的消息交给翻译

    三道判据：
    1. 文本相似度 >= 0.82 —— 消息滚动后位置变了但内容一样
    2. 同区域、位置几乎没动（y 差 <= 18px）且相似度 >= 0.45 —— OCR 抖动，
       同一行每次识别结果略有不同（K釣リン / 釣りプリン / 釣kypku）
    3. 精确匹配快速路径

    位置判据带区域标记：聊天面板和底部预览各自的 y 坐标是独立的，
    否则两个区域同一高度会被误判成「同一行在抖动」。
    """

    def __init__(self, history=800, threshold=0.82):
        self.history = history
        self.threshold = threshold
        self._items = []          # [(key, (zone, y0))]
        self._exact = {}          # 精确匹配快速路径

    def reset(self):
        self._items.clear()
        self._exact.clear()

    def is_new(self, key, pos=None):
        if key in self._exact:
            return False
        nk = len(key)
        for k, kp in self._items:
            # 长度差太大不可能相似，直接跳过 —— 历史上限 800 条，
            # 不做这步的话一屏 30 条消息要跑 20 多万次比对
            if abs(len(k) - nk) > max(3, int(nk * 0.4)):
                continue
            r = similar(k, key)
            if r >= self.threshold:
                return False
            # 同一区域、同一高度、文本大差不差 —— 当作 OCR 抖动
            if pos and kp and kp[0] == pos[0] and abs(kp[1] - pos[1]) <= 18 and r >= 0.45:
                return False
        return True

    def add(self, key, pos=None):
        self._items.append((key, pos))
        self._exact[key] = True
        if len(self._items) > self.history:
            drop = self._items[: len(self._items) - self.history]
            del self._items[: len(self._items) - self.history]
            for k, _ in drop:
                self._exact.pop(k, None)

    def filter_new(self, msgs):
        out = []
        for m in msgs:
            if self.is_new(m.key, (m.zone, m.y0)):
                out.append(m)
        for m in out:
            self.add(m.key, (m.zone, m.y0))
        return out
