"""屏幕/窗口截图

优先用 Win32 PrintWindow 抓取目标窗口自身内容（即使被其他窗口遮挡也能抓到），
失败时回退到 mss 屏幕区域截图。
"""
import ctypes
import threading
from ctypes import wintypes

import mss
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002  # Win8.1+：抓取含 DirectX/硬件加速内容


class ScreenGrabber:
    def __init__(self):
        self._local = threading.local()

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._local.sct = sct
        return sct

    def grab(self, region):
        """region = (left, top, right, bottom) 物理像素"""
        l, t, r, b = [int(v) for v in region]
        w, h = max(1, r - l), max(1, b - t)
        shot = self._sct().grab({"left": l, "top": t, "width": w, "height": h})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def grab_window(self, hwnd, client_rect):
        """用 PrintWindow 抓取窗口客户区，返回 (Image, 实际内容rect) 或 (None, None)

        返回的图像是完整客户区；若失败返回 None 由调用方回退到屏幕截图。
        """
        if not hwnd:
            return None
        cl, ct, cr, cb = client_rect
        w, h = cr - cl, cb - ct
        if w <= 0 or h <= 0:
            return None

        hdc = user32.GetWindowDC(hwnd)
        if not hdc:
            return None
        memdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(memdc, bmp)
        try:
            ok = user32.PrintWindow(hwnd, memdc, PW_RENDERFULLCONTENT)
            if not ok:
                return None
            # 读回位图
            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                            ("biClrImportant", wintypes.DWORD)]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h  # 自上而下
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0
            buf = ctypes.create_string_buffer(w * h * 4)
            got = gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
            if got == 0:
                return None
            img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
            return img, (cl, ct, cr, cb)
        finally:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(memdc)
            user32.ReleaseDC(hwnd, hdc)
