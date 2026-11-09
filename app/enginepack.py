"""识别引擎包：韩语模型的"有没有装"与安装（下载 / 手动选文件）

日英引擎随安装包内置；韩语模型体积大（13MB）且只有少数人用，
所以支持后装：下载到程序目录 models/，或让用户自己选一个 onnx 文件。
"""
import os
import shutil

from .paths import APP_DIR, bundle_dir

KO_MODEL = "korean_PP-OCRv5_rec_mobile.onnx"
DEFAULT_KO_URL = ("https://huggingface.co/SWHL/RapidOCR/resolve/main/"
                  "PP-OCRv5/korean_PP-OCRv5_rec_mobile.onnx")


def user_model_dir():
    d = os.path.join(APP_DIR, "models")
    os.makedirs(d, exist_ok=True)
    return d


def find_model(name):
    """按优先级找模型：程序目录/models → 安装包内置 → rapidocr 自带"""
    cands = [os.path.join(APP_DIR, "models", name),          # 用户在 exe 旁自建的（可选覆盖）
             os.path.join(bundle_dir(), "models", name),      # runtime/models（打包后的正式位置）
             os.path.join(bundle_dir(), "rapidocr", "models", name)]
    try:
        import rapidocr
        cands.append(os.path.join(os.path.dirname(rapidocr.__file__), "models", name))
    except Exception:
        pass
    for p in cands:
        try:
            if os.path.exists(p) and os.path.getsize(p) > 1024:
                return p
        except OSError:
            continue
    return None


def ko_installed():
    return find_model(KO_MODEL) is not None


def ko_model_path():
    return find_model(KO_MODEL)


def install_from_file(src, progress=None):
    """把用户选的 onnx 拷进程序目录"""
    dst = os.path.join(user_model_dir(), KO_MODEL)
    shutil.copyfile(src, dst)
    if progress:
        progress(f"已安装到 {dst}")
    return dst


def download_ko(url=None, progress=None, timeout=60):
    """下载韩语模型；失败抛异常（上层提示改用「手动选择文件」）"""
    import requests

    url = (url or DEFAULT_KO_URL).strip()
    if not url:
        raise RuntimeError("没有配置下载地址")
    dst = os.path.join(user_model_dir(), KO_MODEL)
    tmp = dst + ".part"
    if progress:
        progress("正在下载韩语模型（约 13MB）…")
    with requests.get(url, stream=True, timeout=timeout) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(f"下载中 {done * 100 // total}%（{done // 1024 // 1024}MB）")
    if os.path.getsize(tmp) < 1024 * 1024:
        os.remove(tmp)
        raise RuntimeError("下载的文件不完整")
    os.replace(tmp, dst)
    if progress:
        progress("韩语引擎安装完成")
    return dst


REQUIRED = ["PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"]


def missing_models():
    """日英引擎必需的模型有没有缺（缺了基本就是安装包不完整）"""
    return [m for m in REQUIRED if find_model(m) is None]
