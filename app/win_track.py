"""窗口追踪：按标题找游戏窗口，返回窗口客户区矩形（物理像素）

用于「例1行动模式」窗口经常移动的场景——不用固定坐标，
而是每次截图前定位窗口，再用窗口内相对比例换算聊天区域。
开销极低（一次 EnumWindows 遍历，<1ms）。
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32


def _enum_windows():
    """返回 [(hwnd, title, left, top, right, bottom)] 可见窗口"""
    result = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            result.append((hwnd, buf.value, rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return result


def find_window(title_keyword):
    """按标题关键字找窗口，返回 (hwnd, (left, top, right, bottom)) 或 (None, None)"""
    for hwnd, title, l, t, r, b in _enum_windows():
        if title_keyword and title_keyword.lower() in title.lower():
            return hwnd, (l, t, r, b)
    return None, None


def get_client_rect(hwnd):
    """窗口客户区（不含标题栏/边框）的屏幕坐标"""
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return (pt.x, pt.y, pt.x + rect.right, pt.y + rect.bottom)


def resolve_region(cfg):
    """根据配置返回本次截图用的物理像素区域。

    优先级：
    1. track_window=True 且能按标题找到窗口 -> 返回 (region, client_rect, hwnd)
    2. 固定 region 坐标（用户手动框选）-> (region, None, None)
    """
    zones = resolve_zones(cfg)
    if zones:
        _, _, region, _, _, _ = zones[0]
        info = _client_info(cfg)
        if info:
            _, client_rect, hwnd = info
            return region, client_rect, hwnd
        return region, None, None
    region = cfg.get("region")
    if region:
        return tuple(int(v) for v in region), None, None
    return None, None, None


def _client_info(cfg):
    """(hwnd, client_rect, size) 或 None"""
    if not cfg.get("track_window", True):
        return None
    hwnd, _ = find_window(cfg.get("window_title", ""))
    if not hwnd:
        return None
    cl, ct, cr, cb = get_client_rect(hwnd)
    w, h = cr - cl, cb - ct
    if w < 100 or h < 100:
        return None
    return hwnd, (cl, ct, cr, cb), (w, h)


def resolve_zones(cfg):
    """返回本次要识别的所有采集区（专版：聊天面板 + 底部预览）。

    每项：(key, name, region_rect, priority, client_rect, hwnd)
    region_rect 是屏幕物理像素坐标，可直接截图。
    """
    from .gamepacks import zones_for

    info = _client_info(cfg)
    if info:
        hwnd, (cl, ct, cr, cb), (w, h) = info
        saved = cfg.get("zones") or {}
        out = []
        for key, name, ratio, prio in zones_for(w, h):
            # 校准过就用用户框的比例，否则用游戏专版内置的默认比例
            r = saved.get(key)
            if isinstance(r, (list, tuple)) and len(r) == 4:
                try:
                    r = [float(v) for v in r]
                    if all(0.0 <= v <= 1.0 for v in r) and r[2] > r[0] and r[3] > r[1]:
                        ratio = r
                except (TypeError, ValueError):
                    pass
            rx0, ry0, rx1, ry1 = ratio
            out.append((key, name,
                        (int(cl + w * rx0), int(ct + h * ry0),
                         int(cl + w * rx1), int(ct + h * ry1)),
                        prio, (cl, ct, cr, cb), hwnd))
        return out

    # 找不到窗口时，退回旧的单区域配置（兼容手动框选）
    region = cfg.get("region")
    if region:
        r = tuple(int(v) for v in region)
        return [("chat", "框选区域", r, 0, None, None)]
    return []
