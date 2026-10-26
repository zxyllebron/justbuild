# Linux 自动接收与现有 Studio 导入

`src/justbuild/receiver.py` 接收固定可信 origin 的 HTTPS handoff 链接，下载 manifest 与真实 3MF、验证大小和 SHA-256、原子缓存并保存独立收据。默认只下载；只有显式 `--open-studio` 才向已安装 Studio 请求导入。无 shell、无账号读取/复制、无打印机请求，不重新配置已登录账号。

## 已有本机入口与源码证据

本轮只读核验确认本 Linux 已安装并运行原生 Bambu Studio 2.8.2.61，桌面项为 `/home/wujie/.local/share/applications/BambuStudio.desktop`，`Exec=/home/wujie/.local/opt/bambu-studio-2.8.2.61/launch.sh %U`，已关联 `bambustudio`、`bambustudioopen`。两个现有进程使用同一官方二进制。没有读取 token、密码、cookie、签名密钥或会话文件内容，也未操作进程。

固定官方源码 [InstanceCheck.cpp](https://github.com/bambulab/BambuStudio/blob/926a7192574bcb9b3a732e1ec59a46d79cb45466/src/slic3r/GUI/InstanceCheck.cpp) 支持 `--single-instance`，Linux 经 D-Bus 把本地存在的文件路径交给已运行实例；接收器因此使用固定本机 launcher、`--single-instance`、单一绝对文件路径。现有实例 IPC 的 `get_path` 只识别本地文件，不能把远端 `bambustudio://` URL 当成本地路径转发。

[GUI_App.cpp](https://github.com/bambulab/BambuStudio/blob/926a7192574bcb9b3a732e1ec59a46d79cb45466/src/slic3r/GUI/GUI_App.cpp#L1125) 则有新实例的远端 URI 下载分支，非受信域名可能出现原生确认；接收器不修改其受信域名或伪装来源。用户明确选择的可信下载服务经本接收器校验后作为本地文件打开，Studio 仍可能显示自身项目确认。

当前发现了一个官方 InstanceCheck D-Bus 服务名；只读 Introspect 请求在 8 秒超时。没有发送 `AnotherInstance` 或实际 launcher 请求，因此尚未验证此时的实例能否响应导入，更不能把 D-Bus 注册或 launcher 子进程启动作为导入完成证据。

## 调用

主控已经接入 CLI。只下载并核验：

```bash
justbuild receive 'https://TRUSTED-SERVICE/handoffs/TOKEN' \
  --allowed-origin 'https://TRUSTED-SERVICE' \
  --cache-root "$HOME/.local/share/justbuild/receiver"
```

主动请求现有 Studio 导入时再加：

```bash
justbuild receive 'https://TRUSTED-SERVICE/handoffs/TOKEN' \
  --allowed-origin 'https://TRUSTED-SERVICE' \
  --cache-root "$HOME/.local/share/justbuild/receiver" \
  --open-studio \
  --launcher '/home/wujie/.local/opt/bambu-studio-2.8.2.61/launch.sh'
```

以上为接口示例，本轮未对真实文件执行 `--open-studio`。链接本身包含短期 bearer token，不公开转发，不开启 HTTP 客户端或代理的完整 URL 日志；交接链接也不应写入公开 shell 历史。后续网页/本机协议接收器应直接调用本 API，无需用户每次手动保存文件。

Python 入口：

```python
receive(handoff_url, *, allowed_origin, cache_root: Path,
        open_studio=False, launcher: Path | None = None,
        allow_loopback_http=False, max_bytes=128 * 1024 * 1024,
        client=None) -> dict
```

异常为 `ReceiverError`，文本不含 bearer URL、远端 body 或底层异常；CLI 将其返回为错误码 2。launcher 是可信本机配置，不接受远端 manifest 指定程序。打开时仅执行 `[launcher, '--single-instance', absolute_local_file]`，`shell=False`。若调用环境缺少会话总线变量，且当前用户 `/run/user/UID/bus` 存在，补充该用户会话地址；不修改 Studio 配置。

## 下载、缓存与去重

- 默认 HTTPS；origin 严格按 scheme、规范主机、有效端口比较。landing 必须为 `/handoffs/{64位小写hex}`，无用户信息、查询、fragment、空格、控制字符或编码路径。下载只允许同一 capability 的 `/file.gcode.3mf`。
- manifest 和文件拒绝一切重定向，使用系统 TLS 验证，无凭据/账户读取。manifest 上限 64 KiB，默认文件上限 128 MiB，流式大小和总时限限制；实际大小和哈希均必须一致。拒绝重复 JSON 字段、不安全名称与布尔/非整数大小。
- 缓存目录按当前用户建立；拒绝符号链接，子目录以目录描述符和 `O_NOFOLLOW` 打开。下载在同目录临时文件 fsync 后原子改名；收据也原子写入。文件的 hash 检查不等于 Studio 已经读过该文件。
- 收据位于 `receipts/ID.json`，缓存位于 `inbox/ID/BASENAME`；收据只存 origin、ID、哈希、大小、本地路径和状态，不存 handoff token。ID 与 origin/文件身份绑定；冲突、已验证文件失踪或本地内容改动均拒绝，不静默覆盖。
- 每个 ID 以 `flock` 独占保护。下载成功后 `download_status=verified`；导入请求前先持久记录 `open_status=launch_unknown`，Popen 返回后才记 `process_started`。重复 ID 不重新下载或再次启动；未知结果需核对，不自动重试。
- 仅显式 `--allow-loopback-http` 才接受 `http://127.0.0.1` 或 `http://[::1]` 开发地址；不接受 `localhost`、其他 127 地址或普通局域网明文 HTTP。

所有收据中的 `studio_import`、`printer_receipt`、`print_start` 都保持 `not_observed`。官方单实例 IPC 不返回实际导入完成回执，也没有打印命令；云打印仍由另一个经过授权、带设备身份/状态和真实回执的官方适配器处理。本接收器不提取会话、加载网络 SDK 或绕过原生授权。

## 本轮验证

`tests/integration/test_linux_receiver.py` 使用 HTTPX MockTransport 和模拟 Popen，覆盖精确字节/哈希、默认不打开、原子收据、下载/启动去重、未知启动、HTTPS/同 origin/路径、重定向、大小限制、错误哈希、符号链接、缓存变更、重复 JSON 和并发锁。测试不启动 GUI、不访问打印机、不使用账户。真实 Linux Studio 导入、跨网络下载服务、云提交和实体打印尚未验收。
