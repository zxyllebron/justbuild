# Windows 一次部署后通过链接自动接收

本安装器把 `justbuild://open?handoff=<经过 URL 编码的 HTTPS 交接链接>` 注册为**当前用户**的协议。用户主动安装一次后，未来在网页点击该链接，就由本机固定接收器下载、核对哈希并打开已安装的 Bambu Studio，不再每次运行 PowerShell 或手动保存 3MF。

**此交付是可审阅的安装源码；Windows 实机编译、协议注册、浏览器唤起和 Studio 导入目前未验证。它不启动打印。** 文件接收、应用进程启动、Studio 实际导入、打印机接收和开始打印仍分别取证。

## 一次安装

把同一版本源码包中的以下两个文件放在 Windows 同一目录：

- `scripts/install-windows-receiver.ps1`：本次安装器，嵌入完整 C# 启动器源码。
- `scripts/windows-receiver.ps1`：已有下载、哈希验证和 Studio 打开接收器。

审阅这两个文件后，用 Windows PowerShell 5.1 主动运行一次，地址替换为你实际信任的稳定 HTTPS 服务 origin：

```powershell
powershell.exe -NoProfile -File ".\install-windows-receiver.ps1" -AllowedOrigin "https://YOUR-TRUSTED-SERVICE"
```

不需要管理员权限。`AllowedOrigin` 只接受 HTTPS、主机及可选端口；不接受路径、账号信息、查询参数、通配符或普通 HTTP。只信任这一处 origin；临时域名改变后必须明确重新安装配置，不能自动扩大允许范围。脚本不改变 PowerShell 执行策略；组织策略禁止未签名脚本时，使用组织允许的签名或部署流程，不直接关闭策略。

Windows 自带的 .NET 编译器通过 `Add-Type` 把嵌入 C# 编译为 `ProtocolLauncher.exe`，不下载其他程序。安装到 `%LOCALAPPDATA%\justbuild\protocol`，只保存允许 origin、接收器 SHA-256、本机程序及所有者标记，不存交接 token、拓竹账号或设备访问码。若只想先生成本机可审查文件、不注册协议，可加 `-PrepareOnly`；它写独立的 `protocol-preview` 目录。

接收器会从本机 App Paths、Bambu Studio 安装信息和标准路径定位现有 Studio。如果安装位置特殊，可按已有 `windows-receiver.md` 的一次配置方式指定本机路径；这不需要每次重新下载文件。

## 链接与自动接收

服务端网页应提供这种按钮，其中整个 HTTPS landing URL 编码为单一 `handoff` 值：

```text
justbuild://open?handoff=https%3A%2F%2FYOUR-TRUSTED-SERVICE%2Fhandoffs%2F64位小写十六进制票据
```

实际票据由主控签发，示例文字不能直接使用。接收页已提供 `justbuild://` 接收器按钮，并保留官方 `bambustudio://` 原生入口。前者要求目标电脑事先完成一次安装，并校验下载；两者都不会启动打印。安装器不会自行修改服务端网页。

浏览器首次唤起本机协议可能要求允许打开应用；Windows/组织策略也可能拦截未签名程序。此处不绕过原生确认。安装后无需常驻进程、轮询、计划任务、Windows 服务或防火墙配置。跨网络只要求 Windows 能正常访问这个 HTTPS 下载服务。

## 参数注入与权限边界

注册表只有 `HKCU\Software\Classes\justbuild`，打开命令直接指向固定 EXE 和 `"%1"`，没有 PowerShell、cmd、动态表达式或远端命令。启动器先要求原生 argv **恰好一个参数**；任何原始双引号造成的新参数在读配置/启动 PowerShell前直接拒绝。

随后只允许固定 `justbuild://open?handoff=` 格式和 URL 编码字符，百分号解码恰好一次，拒绝空格、控制字符、引号、反斜线、额外参数、非 HTTPS、userinfo、fragment、query、跨域/跨端口和非 `/handoffs/{64位小写十六进制}` 路径。配置 origin 与目标 HTTPS URL 比较 scheme/规范主机/端口。接收器下载仍要求 manifest 与文件同 origin，拒绝重定向并核验大小/哈希。

通过检查后，启动器核验本机接收器代码 SHA-256，以 Windows 系统目录中的固定 PowerShell、固定 `-File` 路径及独立参数调用。支持 `ArgumentList` 时直接分参，否则使用完整 Windows 引号转义；不使用 shell、`Invoke-Expression` 或 `-EncodedCommand`。任何远端字段都不能指定应用路径或增加命令。

安装检测已有 handler 的所有者与精确命令，不覆盖其他应用。目录或文件是 reparse point、已有目录缺本工具标记时拒绝。只配置当前用户，其他 Windows 用户需各自主动安装；管理员/相同账户恶意修改本机代码和配置不在此隔离边界内。

## 卸载

```powershell
powershell.exe -NoProfile -File ".\install-windows-receiver.ps1" -Uninstall
```

只在所有者标记、专用目录和注册命令仍对应本工具时删除自己的 HKCU 协议与四个已知安装文件。保留 `%LOCALAPPDATA%\justbuild\inbox`、`receipts`、接收器本机 Studio 配置及未知文件；不删除或覆盖其他 handler。

## 验证范围

Linux 上执行 `tests/integration/test_receiver_installer.py` 的源码/交付边界检查；Windows 行为测试明确 skip。Windows 测试使用 `-PrepareOnly`，只编译临时文件并用反射调用纯 URI/argv 校验，不注册实际协议、不访问打印机、不运行接收器。另用真实原生进程参数测试引号注入分出额外参数时立即拒绝。

Windows 编译、实际 HKCU 注册/卸载、浏览器唤起及真实 Studio 接收仍需在用户 Windows 上验证；不能把 Linux 源码测试当作这些结果。每次接收的去重、文件/启动回执沿用已有 receiver，`printer_receipt` 与 `print_start` 均保持 `not_observed`。
