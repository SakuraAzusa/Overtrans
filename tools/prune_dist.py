"""打包后瘦身：只删不用到的东西，不再往外搬

程序目录的可见结构（根目录尽量干净）：

    Overtrans/
    ├── Overtrans.exe
    ├── logs/                    ← 根目录只留日志
    ├── config.json              （首次保存时生成）
    ├── README.md / LICENSE
    └── runtime/                 ← 其余全部藏在这层
        ├── models/ ...          （图标和模型都在 runtime 里，不占根目录）
        └── rapidocr/models/*.onnx

rapidocr 自带一整套多语言模型，其中大部分用不到：
  · PP-OCRv6_det_small / det_tiny   检测
  · PP-OCRv6_rec_small              日 / 英 / 拉丁识别
  · korean_PP-OCRv5_rec_mobile      韩语识别
其余（中文检测、韩语 v4 等）白占 60MB+，连同 cv2 的视频解码 DLL 一起删掉。
"""
import os
import shutil
import sys

KEEP_MODELS = {
    "PP-OCRv6_det_small.onnx",
    "PP-OCRv6_det_tiny.onnx",
    "PP-OCRv6_rec_small.onnx",
    "korean_PP-OCRv5_rec_mobile.onnx",
}
CONTENTS = ("runtime", "_internal")


def _contents(root):
    for name in CONTENTS:
        p = os.path.join(root, name)
        if os.path.isdir(p):
            return p
    return None


def main(root):
    rt = _contents(root)
    if not rt:
        print("找不到 runtime/ 或 _internal/，跳过整理")
        return 1

    removed, saved = 0, 0

    # 1) 模型：要留的挪到 runtime/models（二级目录），其余删掉
    src = os.path.join(rt, "rapidocr", "models")
    dst = os.path.join(rt, "models")
    if os.path.isdir(src):
        for f in os.listdir(src):
            p = os.path.join(src, f)
            if f in KEEP_MODELS:
                os.makedirs(dst, exist_ok=True)
                target = os.path.join(dst, f)
                if not os.path.exists(target) or os.path.getsize(target) != os.path.getsize(p):
                    shutil.move(p, target)
                else:
                    os.remove(p)
                continue
            try:
                saved += os.path.getsize(p)
                os.remove(p)
                removed += 1
            except OSError as e:
                print(f"  跳过 {f}: {e}")

    # 2) cv2 的视频解码 DLL（只喂 numpy 图片，用不到）
    cv2 = os.path.join(rt, "cv2")
    if os.path.isdir(cv2):
        for name in os.listdir(cv2):
            if name.startswith("opencv_videoio_ffmpeg"):
                p = os.path.join(cv2, name)
                try:
                    saved += os.path.getsize(p)
                    os.remove(p)
                    removed += 1
                except OSError as e:
                    print(f"  跳过 {name}: {e}")

    print(f"pruned {removed} files, saved {saved / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join("dist", "Overtrans")
    if not os.path.isdir(target):
        print("dist 不存在:", target)
        sys.exit(1)
    sys.exit(main(target))
