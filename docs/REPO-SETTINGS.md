# GitHub 仓库设置清单

上传时对照着填，省得来回找。

## 基本信息

- **Repository name**：`Overtrans`
- **Description**：
  ```
  用于overfield快速识别翻译与回复小工具，更好的与你的国际小朋友交流
  ```
- **Website**：留空（有 Releases 页面就不需要）
- **Topics**（逗号分隔，最多 20 个）：
  ```
  ocr, translate, rapidocr, overfield, game-tools, windows, python, tkinter, onnxruntime
  ```
- **License**：选 `MIT License`（仓库里已经有 LICENSE 文件，GitHub 会自动识别）
- **Visibility**：Public

## Release（必须做，README 里所有下载链接都指向它）

- **Tag**：`v1.0.0`（打 tag 会自动触发 Actions 构建，也可以手动上传现成的 zip）
- **Title**：`Overtrans 1.0.0`
- **说明模板**：

```markdown
首个可分发版本。

**下载**：`Overtrans-1.0.0-win64.zip`（114.8 MB），解压双击 `Overtrans.exe` 即可，无需安装运行库。

- 本地 OCR：RapidOCR（PP-OCRv6 检测 + 日英识别，韩语可选）
- 双采集区：聊天面板 + 底部预览，面板收起自动切换
- 时间线去重 + 翻译记忆，来回滚动不重复翻译
- 免费翻译通道：Microsoft / MyMemory，失效自动互备
- 支持任意 OpenAI 兼容接口（DeepSeek / 硅基流动 / 智谱 / Ollama …）

> 只读屏幕画面，不注入游戏、不修改游戏文件。
```

## 其他建议

- **About → Settings → Features**：勾上 `Issues`，`Discussions` 可勾可不勾
- **Actions**：默认开启；推送 `v*` tag 会跑 `.github/workflows/build.yml`
- 想加徽章统计：README 顶部的 visitor 计数用的是 <https://count.getloli.com/>，
  换主题只要改 `?theme=` 后面的值（`moebooru` / `miku` / `gelbooru` 等）
