# 本机 A1 首次配对页

本模块让用户在浏览器中完成一次本机 A1 Developer LAN 配对，随后可以执行只读状态检查。它不上传打印文件，也不调用打印提交、移动、加热或设备模式设置。当前仅适用于打印机与运行服务的电脑可通过私网互通的现场验证；不会由 LAN 配对建立跨网络的云打印能力。

## 集成入口

```python
from justbuild.printer_setup import install_local_printer_routes

install_local_printer_routes(
    app,
    enabled=os.environ.get("JUSTBUILD_ENABLE_LOCAL_PRINTER_SETUP") == "1",
    profile_path=None,
)
```

默认 `enabled=False`；只接受字面布尔值 `True` 开启，`"1"`、`1` 等其他对象不启用。模块不自行读取环境变量或更改服务器绑定。主控在 `create_app` 中调用，保留现有 `justbuild_session` 与 `request.state.owner` 会话中间件。没有已存在的有效 Cookie、或 owner 与该 Cookie 的 SHA-256 不相符时，所有 POST 都拒绝。打开主页面或配对页面可由主应用中间件建立 Cookie，再提交表单。

默认私有文件由 `printing.lan.profile_path()` 指定，为 `~/.config/justbuild/printers/a1.json`。显式 `profile_path` 只能是服务器受信任配置；不得从请求体、审阅分享链接或查询参数读取，也不能指向静态目录、任务产物、下载包或源码。模块把配置路径直接传给 LAN 模块，后者负责当前用户所有权、父目录 0700、原子写入 0600、拒绝符号链接与安全读取。

依赖只有项目已经使用的 FastAPI / Starlette，以及同仓库 `printing.lan` 的公开函数。`lan_profile_status` 仅脱敏读取；`probe_lan_tls` 只读取证书；`save_lan_profile` 私有保存；`check_lan_connection` 执行受固定证书保护的 MQTT 状态／版本及 FTPS 登录／NOOP。没有增加第三方依赖，没有修改共享依赖文件。

新增资产位于实际打包目录 `src/justbuild/web/printer-setup.{html,js,css}`。页面使用 `/printer-setup.js` 和 `/printer-setup.css` 两个同样受保护的明确路由。`/static/printer-setup.html`、`.js`、`.css` 一律返回 404，避免主应用 StaticFiles 挂载绕过本机页检查；无需修改已有 index/app/style。

## API 合同

| 路由 | 请求 | 返回／行为 |
| --- | --- | --- |
| GET `/printer-setup` | 本机直接导航 | 首次配对表单，无内嵌凭据 |
| GET `/api/printers/local` | 本机访问 | `{schema_version, profile, dispatch_verified:false, print_command_sent:false}`；profile 是 `lan_profile_status` 结果的字段白名单 |
| POST `/api/printers/local/probe` | `{host, developer_mode_confirmed:true}` | `{schema_version,status:'needs_confirmation',probe_id,host,expires_in_seconds:300,mqtt_certificate_sha256,ftp_certificate_sha256,identity_verified:false,credentials_sent:false,print_command_sent:false,dispatch_verified:false}` |
| POST `/api/printers/local/pair` | 见下文 | `{schema_version,status:'paired',profile,print_command_sent:false,dispatch_verified:false}` |
| POST `/api/printers/local/check` | `{}` | 脱敏只读检查结果，强制 `read_only:true`、`print_command_sent:false`、`dispatch_verified:false` |

所有 POST 要求 `Content-Type: application/json`、精确同源 `Origin`、`X-Justbuild-Intent: printer-setup`、有效既有会话 Cookie、正确 `request.state.owner`。拒绝请求查询参数、未知或缺失字段、超过 4096 字节的请求体。不会接受客户端提供的配置文件路径、端口、任意命令或打印选项。

`pair` 请求的精确字段为：

| 字段 | 校验 |
| --- | --- |
| `probe_id` | 本会话本次尚未消费的随机挑战，有效 5 分钟 |
| `host` | 与探测时完全相同的 RFC1918 IPv4 |
| `mqtt_certificate_sha256` | 与服务器本次记录完全相同的 64 位小写十六进制指纹 |
| `ftp_certificate_sha256` | 同上 |
| `serial` | 用户按设备屏幕输入，6–64 位字母、数字、下划线、连字符 |
| `access_code` | 8 位字母或数字，仅 JSON 请求体中传输 |
| `firmware` | 屏幕固件标识，1–64 位可打印 ASCII 非空白字符 |
| `developer_mode_confirmed` | 必须是布尔值 `true`，表示用户已在设备上允许模式 |
| `fingerprint_confirmed` | 必须是布尔值 `true`，表示用户核对设备后同意首次固定证书 |

服务器探测仅接受显式输入的 `10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16` IPv4，固定连接 MQTT 8883 与 FTPS 990。拒绝主机名、IPv6、公网、loopback、链路本地、CGNAT、地址加端口等输入。仅两服务都返回有效指纹才创建挑战，不发送访问码。探测记录仅包含 owner hash、host、两个指纹、过期时间与配对锁，不包含凭据。每会话新探测使旧挑战失效；全进程最多 4 个同时探测、128 个未清理有效记录，超过上限返回 429。进程重启会丢失挑战，需重新探测。

`pair` 要求会话、IP、两个指纹、nonce 与两项明确确认全部匹配。成功保存后立即消费挑战；保存失败可在有效期内重试。序列号与访问码既不写到普通任务产物，也不出现在返回或异常字符串中。对底层异常只回传受限错误码与固定提示，不回传 `str(exc)`；只读结果也采用字段白名单。没有访问码明文回显、localStorage、sessionStorage、console 日志或第三方脚本。保存成功后清空密码输入框。

状态的 `profile` 白名单保留 `configured/status`、host、型号、`serial_masked`、`printer_id_sha256`、固件、授权模式、Developer LAN 确认、证书存在性、访问码存在性、依赖／离线先决条件、`network_check_performed`。不能序列化私有 `load_lan_profile` 的返回。配置存在和先决条件满足均不等于网络连接或发印成功。

## 本机与首次信任边界

启用后仍要求 TCP peer 是回环地址，Host 必须严格为 `127.0.0.1`、`localhost` 或 `[::1]`（可带 1–65535 的端口），拒绝重复 Host、伪后缀域名与非 HTTP(S) scheme。拒绝 Forwarded、Via、X-Real-IP、X-Forwarded-*、X-Original-Host/URL、X-Rewrite-URL，并拒绝跨站 Fetch Metadata 与不同 Origin。

部署必须把此功能用于直接绑定本机 loopback 的服务，不通过公网反代或反向隧道转发。应用无法区分本机浏览器与一个刻意删去全部代理标头、并伪装 Host 的本地代理；因此这不是公网账户授权机制，不能代替服务器的绑定和部署隔离。不要在多用户云实例开启，也不要把打印机端口映射到公网。

首次 TLS 探测的自签名证书没有厂商身份链；界面明确显示 `identity_verified:false`。用户应核对打印机屏幕 IP／SN，并在可信本地网络确认目标。如没有独立可信指纹来源，该操作是首次信任，不能证明厂商身份；后续连接则在发送凭据前强制精确 pin 匹配，证书变化须重新配对。固件版本和 Developer LAN 由用户在设备侧确认，网页不会修改模式或重置访问码。

页面与 API 响应设置 `Cache-Control:no-store`、`Referrer-Policy:no-referrer`、禁止 iframe、CSP 仅允许同源静态脚本／样式／连接。主应用部署若需要 HTTPS，浏览器到本机服务的 TLS 仍由部署负责；模块不自行申请证书或配置监听端口。

## 实际验证与限制

执行：

```sh
PYTHONPATH=src /home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/integration/test_printer_setup.py -q
node --check src/justbuild/web/printer-setup.js
```

当前结果：**60 passed in 0.67s**；JS 语法检查退出 0。覆盖默认关闭与布尔型开关、所有规范静态路由、静态别名封锁、IPv4/IPv6 loopback peer 与严格 Host、不同来源与代理头拒绝、已有 session/owner、RFC1918 目标、固定端口、模式确认、双服务证书完整性、nonce 的 owner/host/双 pins/有效期/一次消费／新探测失效、未知字段／大小／媒体类型、保存路径、只读检查与错误脱敏。安装的 Starlette TestClient 传输器不能解析 IPv6 URL authority，IPv6 用例保留真实 ASGI `::1` peer 与 `[::1]:port` Host，通过 IPv4 测试传输 URL 驱动，不发生网络连接。

所有底层函数在本组测试中由 mock 代替，私有路径只指向 pytest 临时目录。没有创建或读取真实 A1 配置、没有探测实际服务、没有登录打印机／账户、没有上传或发印。页面未做真实浏览器操作验收，设备屏幕位置取决于具体固件；实际证书固定、设备身份／版本匹配和发印回执仍需按分阶段实机流程观察。主控负责把此模块接入 `create_app`、添加审阅页入口并运行全套集成测试；本模块没有修改 server.py、已有网页、printing/ 或原始任务／几何／3MF。
