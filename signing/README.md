# 代码签名

不签名完全能用，但 Windows SmartScreen 会提示「未知发布者」。要消掉这个提示，
需要一个 **代码签名证书**（OV 证书首次仍需累积信誉，EV 证书可立即生效）。

## 1. 传统证书（pfx 文件）

```bat
set PFX_PATH=D:/certs/overtrans.pfx
set PFX_PASS=你的密码
signing\sign.bat dist\Overtrans\Overtrans.exe
```

`sign.bat` 用的是 Windows SDK 自带的 `signtool`，会带时间戳（证书过期后已签的包仍有效）。

## 2. Azure Trusted Signing（云端签名，按月付费，无需硬件令牌）

CI 里用 `azure/trusted-signing-action`，本地可以用 `trusted-signing-cli`。
在 `.github/workflows/build.yml` 的 `sign` 步骤里预留了位置。

## 3. 只做内部测试（自签名）

```bat
makecert -r -pe -n "CN=Overtrans Test" -b 01/01/2026 -e 12/31/2030 -sky signature overtrans.cer
```
自签名证书别人机器上依然会警告，只适合自己验证签名流程。

## 注意

- **私钥和 pfx 绝不入库**（`.gitignore` 已经排除）
- 签名顺序：先签 `Overtrans.exe`，再打包 zip；解压后签名依然有效
- 给多个文件签名时，`sign.bat` 会把参数依次传给 signtool
