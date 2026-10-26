# 独立文件接收 HTTP 服务

`src/justbuild/handoff_server.py` 提供 `create_handoff_app(store)`。它只读取由主控已授权流程签发的 `HandoffStore` 票据，不签发票据、不挂载主 CAD 服务、不接入打印机或启动桌面程序。

## HTTP 接口

| 方法与路径 | 行为 |
| --- | --- |
| `GET /handoffs/{token}` | 中文接收页：真实标签、合成/真实来源标记、字节数、完整 SHA-256、UTC 到期时间、打印机与切片配置；Windows/macOS 官方协议按钮和备用下载 |
| `GET /handoffs/{token}/manifest` | `HandoffStore.get()` 返回的公开元数据；不含本地路径、owner、实际设备身份或票据密封数据 |
| `GET /handoffs/{token}/file.gcode.3mf` | 已验证的原始文件字节；支持单个标准 `bytes=start-end`、`bytes=start-`、`bytes=-suffix` 区间 |
| 上述路径的 `HEAD` | 同一响应头及完整表示长度，空响应体；按 HTTP 规范忽略 `Range` |

支持强 ETag 和 `If-Range`：匹配时返回 `206`，不匹配时返回完整 `200`。越界、无效和多段 Range 返回 `416` 及 `Content-Range: bytes */size`。所有非 GET/HEAD 请求统一 `405`，不存在的路径/无效票据/到期票据/改动文件统一通用 `404`；错误响应不回显 token、内部路径或异常。关闭 OpenAPI、Swagger、ReDoc 与尾斜线自动重定向；无 `/api/jobs`、目录浏览或其他设计接口。

下载调用 `store.payload()` 获取已校验 bytes，不用 `FileResponse`，不会在验证后重新按路径打开文件。构造响应前再次核对元数据大小/哈希，避免 HTTP 层失去原始字节绑定。Range 对这份内存快照切片，完整文件的哈希保留在 manifest 和 `X-Artifact-SHA256`，区间响应不冒充完整文件。

## 页面与信息边界

- 所有动态文本与链接属性均 HTML 转义。无外部脚本、字体、图片或其他资源；无页面脚本。CSP 只允许带固定 SHA-256 的内联样式，禁止脚本、对象、嵌入与表单；禁止外部页面把它嵌入 frame。
- 全部响应含 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`。页面也有 referrer meta。
- Windows 按钮使用 `bambustudio://open?file=<URL编码后的下载URL>`；macOS 使用核心提供的 `bambustudioopen://<下载URL>`。URI 由核心的固定 origin 与 capability 生成，不使用请求 Host 拼接。
- 浏览器可能要求允许唤起本地程序；非官方受信域名也可能触发 Bambu Studio 原生确认。这些确认不能绕过。只有应用启动、没有打开结果回执时，不能声称文件已导入。
- 此票据只授权接收一份文件；不授权发印。网页明确写明接收/导入不启动打印，上传、设备接收、启动、物理完成需要各自证据。
- `synthetic` 严格按真实 manifest 显示：true 为合成示例，false 为真实板任务，缺失为来源类型未记录；服务不自行给产物重贴身份标签。

## 主控集成方式

本轮只交付工厂，没有修改 CLI 或主服务，没有启动常驻、公网隧道、上位机或打印。

由主控建立只读票据存储实例，然后在独立端口运行。例如主控的 Python 启动入口可以调用：

```python
from pathlib import Path
import uvicorn
from justbuild.handoff import HandoffStore
from justbuild.handoff_server import create_handoff_app

store = HandoffStore(Path("/absolute/private/handoff-tickets"))
uvicorn.run(create_handoff_app(store), host="127.0.0.1", port=18766,
            access_log=False)
```

**必须关闭或脱敏 uvicorn、反向代理、隧道与平台的 URL 路径访问日志。** token 是 bearer capability，不能写进访问日志。工厂自身不记录 token 或底层异常，但一个 ASGI 应用不能控制它外层的 HTTP 日志配置；因此 `access_log=False` 是启动入口的必要设置，不是工厂已经关闭了所有外部日志。

今后如需要公网，只给这个独立端口建立 HTTPS 入口，不给主 CAD 服务端口建公共转发。主控创建票据时的 origin 必须是该 HTTPS 入口；TLS/反代仅将原始路径转发给本服务。不要把主服务路由挂在同一公开应用内，也不要开放票据创建路由。票据的签发、实际任务所有权与 TTL 校验由已有核心流程负责。仅回环调试时才能通过核心的显式选项允许 HTTP；生产 origin 使用 HTTPS。

持有链接的人可以在到期前下载该文件，页面也明确说明分享范围；这不是公开账户系统。对于正式多人部署，创建票据的入口仍需实际用户认证、任务所有权与配额控制，这些不属于本只读服务。

## 已执行验证

命令：

```bash
/home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/integration/test_handoff_server.py -q
```

结果：**28 passed**，0.89 秒；一个现有 FastAPI/Starlette TestClient 的 httpx 弃用警告。测试以真实 `HandoffStore` 票据和内存 HTTP 客户端验证下载字节/哈希/文件名、HEAD、各类单 Range、If-Range、416、TTL 到期、无效 token、文件变更、通用异常、HTML 转义、响应安全头、全部写请求拒绝、无主 CAD API、无目录读取和无路径重定向。额外测试在 `payload()` 返回后改写磁盘文件，确认 HTTP 返回的仍是校验时的字节快照。

另将 `tests/integration/test_handoff.py` 与本 HTTP 测试一起执行，结果 **83 passed**，0.82 秒，同一个现有弃用警告。

测试产物是独立临时目录中的小型传输 fixture，未为旧合成板任务签发票据。上述 HTTP 测试不证明 Windows/macOS 协议接收、实际 Bambu Studio 导入、跨网络设备接收、启动或打印完成。
