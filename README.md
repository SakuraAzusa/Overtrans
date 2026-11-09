# Overtrans · 游戏聊天实时翻译

本地 OCR 读游戏聊天框 → 调翻译接口 → 悬浮窗显示中文。**截图不出本机**。

本程序针对 **OverField** 的 UI 布局做了预设（聊天面板 + 底部预览条），也支持自己框选位置，
换成别的游戏只要重新框一次区域即可。

## 特点

- **纯本地识别**：RapidOCR（PP-OCRv6 检测 + 日英识别，可选韩语），截图不上传
- **两个区域同时盯**：聊天面板 + 底部滚动预览，面板收起时自动切到底部条
- **滚动去重**：上下翻历史再回来不会重复翻译，带翻译记忆
- **省资源**：画面静止不识别；同一块反复识别没有新内容会自动降频
- **免 Key 可用**：内置 Microsoft / MyMemory 免费通道，失效自动互备
- **可换引擎**：DeepSeek、硅基流动、智谱、Kimi、通义、OpenRouter、Ollama 等 OpenAI 兼容接口

## 快速开始

1. 到 Releases 下载 `Overtrans-win64.zip`，解压到任意目录
2. 双击 `Overtrans.exe`
3. 首次启动会引导：选识别语言 → 选翻译服务 → 框选聊天区域
4. 先开游戏，再点「开始监听」
5. 点「悬浮窗」把译文挂到游戏旁边

> 免安装、免运行库，但需要 Windows 10/11 64 位。

## 目录结构

```
Overtrans.exe            主程序
logs/                    按天日志 + 崩溃日志（根目录只留它）
config.json              配置（首次保存时生成）
runtime/                 运行库与资源，别动
  ├── models/            OCR 模型（可替换、可补装）
  ├── assets/            图标
  └── rapidocr/models/   检测/识别模型
README.md / LICENSE / THIRD_PARTY_NOTICES.md
```

## 常见问题

**识别不到字**
设置 → 采集区 → 重新框选位置。截图后拖动两个框对准聊天文字，点「保存并测试识别」，
会直接告诉你认到几行、组成几条。

**译文质量差**
免费的 Microsoft / MyMemory 通道质量一般、也可能随时失效。建议换成自己的 API
（DeepSeek、智谱 glm-4-flash 免费额度等），设置里「快速填入」有预设地址。

**韩文不显示**
默认关闭（韩语玩家少，省资源）。点顶部「韩文」开启；如果没装韩语模型，
会提示你到设置里安装（或手动放一个 onnx 到 `runtime/models/`）。

**占 CPU**
设置 → 性能档位选「省电」，或把轮询间隔调大。画面静止时几乎不占。

**杀软报毒 / 未知发布者**
PyInstaller 打包的程序常被误报，加白名单即可。要彻底消掉提示需要代码签名，
见 `signing/README.md`。

## 从源码运行 / 打包

```bash
pip install -r requirements.txt
python main.py          # 直接跑

build.bat               # 生成 dist\Overtrans\ 和 release\Overtrans-win64.zip
```

打 tag（`v1.0.0`）推上去，GitHub Actions 会自动构建并发布到 Releases。

## 许可

MIT，见 [LICENSE](LICENSE)。分发包里的第三方组件许可见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
