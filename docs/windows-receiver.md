# Windows 单次接收器（0.1 原型）

此脚本接收可信服务的交接链接，自动下载已切片 3MF、核对 SHA-256 和字节数，再以文件参数启动 Windows 上已经安装的 Bambu Studio。它使用 Windows PowerShell 5.1 自带的 .NET；不要求 Python、开发软件或本地 CAD。**启动应用进程不证明模型已经导入，更不证明打印机已接收或开始打印。**

## 一次配置和调用

把本项目的 `scripts/windows-receiver.ps1` 保存到 Windows 的固定位置，在 PowerShell 运行：

```powershell
powershell.exe -NoProfile -File "$env:USERPROFILE\Downloads\windows-receiver.ps1" -HandoffUrl "https://YOUR-TRUSTED-SERVICE/handoffs/RECEIVED-TOKEN"
```

`-HandoffUrl` 是 PowerShell 正式参数，也提供 `--handoff-url` 别名。脚本文件路径按实际保存位置替换。服务链接应由用户信任的 justbuild 实例提供；链接包含短期文件读取授权，不应公开转发。

脚本只读检查本机 App Paths、安装卸载注册信息和常见安装目录。如不能定位官方程序，可一次指定固定本机路径：

```powershell
powershell.exe -NoProfile -File ".\windows-receiver.ps1" -HandoffUrl "https://YOUR-TRUSTED-SERVICE/handoffs/RECEIVED-TOKEN" -BambuStudioPath "C:\Program Files\Bambu Studio\bambu-studio.exe"
```

成功发现或指定的路径记入 `%LOCALAPPDATA%\justbuild\receiver-config.json`，以后无需重复指定。该配置没有账号密码、设备访问码或交接链接。此原型不修改执行策略；若企业策略禁止运行未签名脚本，须使用所属组织允许的签名/部署流程，不应直接关闭系统策略。

只测试接收和校验、暂不打开应用，可加 `-DownloadOnly`。开发环境的 `http://127.0.0.1/...` 等显式回环地址需另加 `-AllowLoopbackHttp`；不允许普通局域网或公网明文 HTTP。

## 与服务的合同

若交接链接为 `https://host/handoffs/TOKEN`，脚本 GET `https://host/handoffs/TOKEN/manifest`。响应不超过 64 KiB，必需字段如下；额外的 `label`、`expires_at` 等展示字段不改变命令执行行为。

```json
{
  "id": "server-generated-handoff-id",
  "download_url": "/handoffs/TOKEN/file.gcode.3mf",
  "sha256": "64 hexadecimal characters",
  "bytes": 510514,
  "basename": "justbuild-a1-demo-enclosure.gcode.3mf"
}
```

下载地址可以是相对于 manifest 的 URL 或同 HTTPS origin 的绝对 URL；scheme、域名和端口必须与原交接链接一致。manifest、下载两步都拒绝重定向。TLS 使用 Windows 正常证书校验，不关闭校验。文件大小上限为 1 GiB；实际下载按声明长度流式限量，不能靠虚假 Content-Length 绕过。文件名只允许限定 ASCII 字符和 `.3mf` 扩展，拒绝路径、保留设备名和命令内容。

文件落在 `%LOCALAPPDATA%\justbuild\inbox\ID\BASENAME`。每次都核验实际字节数和 SHA-256，使用临时文件完成后再改名。实际应用路径只能来自本地自动发现、已保存本机配置或用户显式参数，不能由远端 manifest 指定。脚本使用 `ProcessStartInfo`、`UseShellExecute=false` 和单一文件参数；支持 `ArgumentList` 时直接传入，否则使用兼容 Windows 参数规则的转义，不通过 shell、文件关联或动态表达式执行内容。

## 状态、去重和未知结果

`%LOCALAPPDATA%\justbuild\receipts\ID.json` 分别记录：

- `download_status=verified`：本机收到相应字节并核对散列；
- `open_status=pending`：尚未请求打开；
- `open_status=launch_unknown`：已在启动前写入意图，启动结果或回执可能因异常丢失；
- `open_status=process_started`：Windows 接受启动应用进程，记录进程号；
- `printer_receipt=not_observed`、`print_start=not_observed`：没有打印机证据。

同一 ID 用独占锁防并发。ID 与 origin、散列、大小、文件名、路径绑定，重用 ID 更换文件会被拒绝。`launch_unknown` 和 `process_started` 均不自动再次启动，避免崩溃重试造成重复导入或未来重复打印。需要重新打开时，先由用户核对应用实际状态；原型没有自动清理回执、隐式强制重试或删除证据功能。启动前找不到 Bambu Studio 会保留 `pending`，可以补本机路径后重试。

脚本不会安装计划任务、常驻服务、URL 协议、浏览器扩展或防火墙规则，也不会点击打印、发加热/运动指令、修改打印机/账号。一次运行仍需由受信任的本机调用方传入交接链接。浏览器直接操纵另一台电脑上的本机应用需要事先部署的接收机制；仅一个网页链接不能让全新 Windows 自动执行脚本。

最终免人工工作流仍需独立完成：已授权并绑定的常驻队列接收、实际官方打印执行接口、设备身份与状态核对、文件接收回执、开始打印回执，以及未知结果对账。本脚本没有假造这些接口，也没有实现定时轮询假端点。

## 验证边界

当前开发主机是 Linux，没有 PowerShell，也未操作用户 Windows。源码包检查可在 Linux 运行；`tests/integration/test_receiver_package.py` 的 Windows 行为用例会在没有 Windows PowerShell 时明确跳过。Windows 用例使用本机 HTTP 测试服务和 `-DownloadOnly` 检查接收、去重、散列错误和拒绝重定向，绝不运行真实打印。Bambu Studio 的启动、现有实例接管文件和导入结果仍需 Windows 实机验证。
