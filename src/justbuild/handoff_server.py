"""Small download-only HTTP surface; never mounts the CAD application."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import Response

from .handoff import HandoffStore


_CSS = """body{font:16px/1.7 system-ui,sans-serif;margin:0;background:#f2f5f8;color:#142332}
main{max-width:850px;margin:40px auto;padding:28px;background:white;border-radius:16px}
h1{font-size:26px}p,dd{overflow-wrap:anywhere}dl{display:grid;grid-template-columns:100px 1fr;gap:8px 16px}
dt{font-weight:600}dd{margin:0}code,pre{font:13px/1.6 monospace;overflow-wrap:anywhere;white-space:pre-wrap}
.actions{display:flex;flex-wrap:wrap;gap:12px;margin:24px 0}.button{padding:10px 16px;border-radius:8px;background:#164da0;color:white;text-decoration:none}
.note{padding:12px 16px;border-left:4px solid #bf7913;background:#fff7e6}.muted{color:#46556a}
@media(max-width:650px){main{margin:10px;padding:20px}dl{grid-template-columns:1fr;gap:2px}dd{margin-bottom:12px}}"""
_STYLE_HASH = base64.b64encode(hashlib.sha256(_CSS.encode()).digest()).decode()
_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'none'; "
        f"style-src 'sha256-{_STYLE_HASH}'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
    ),
}
_RANGE = re.compile(r"bytes=([0-9]*)-([0-9]*)\Z")


def _respond(request: Request, body: bytes, *, status: int = 200,
             media_type: str = "text/plain", headers: dict | None = None) -> Response:
    return Response(b"" if request.method == "HEAD" else body, status_code=status,
                    media_type=media_type,
                    headers={**_HEADERS, "Content-Length": str(len(body)), **(headers or {})})


def _unavailable(request: Request) -> Response:
    # Never echo a capability, a filesystem path, or an underlying exception.
    return _respond(request, b"Handoff unavailable", status=404)


def _page(metadata: dict) -> bytes:
    def esc(value) -> str:
        return html.escape(str(value), quote=True)

    synthetic = metadata.get("synthetic")
    source = "合成参考板示例" if synthetic is True else "真实板卡任务" if synthetic is False else "来源类型未记录"
    settings = json.dumps(metadata.get("settings", {}), ensure_ascii=False, indent=2)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>justbuild · 接收打印文件</title><style>{_CSS}</style></head>
<body><main><h1>接收外壳打印文件</h1>
<p>此链接将下列已切片文件导入本机 Bambu Studio。<strong>接收或导入不会启动打印，也不授予打印权限。</strong></p>
<dl><dt>任务</dt><dd>{esc(metadata.get('label', ''))}</dd>
<dt>来源</dt><dd>{source}</dd>
<dt>文件</dt><dd>{esc(metadata['basename'])}</dd>
<dt>文件大小</dt><dd>{esc(metadata['bytes'])} 字节</dd>
<dt>SHA-256</dt><dd><code>{esc(metadata['sha256'])}</code></dd>
<dt>到期时间</dt><dd>{esc(metadata['expires_at'])}（UTC）</dd>
<dt>打印机配置</dt><dd>{esc(metadata.get('printer_model') or '未记录')}</dd>
<dt>切片配置</dt><dd><pre>{esc(settings)}</pre></dd></dl>
<div class="actions"><a class="button" href="{esc(metadata['receiver_uri'])}">Windows：通过已安装的 justbuild 接收器导入</a>
<a class="button" href="{esc(metadata['windows_uri'])}">Windows：Bambu Studio 原生导入</a>
<a class="button" href="{esc(metadata['mac_uri'])}">macOS：自动导入 Bambu Studio</a></div>
<p class="note">justbuild 接收器需事先在目标电脑完成一次安装。请在装有 Bambu Studio 的电脑上点击对应按钮。浏览器可能先要求允许打开应用；
来自非官方受信域名的文件还可能触发 Bambu Studio 自身的确认，不能绕过这些原生确认。
当前尚未验证本机协议接收与导入结果；启动应用不等于打印机收到任务或开始打印。</p>
<p><a href="{esc(metadata['download_url'])}">直接下载切片文件（备用）</a> ·
<a href="{esc(metadata['manifest_url'])}">查看文件清单 JSON</a></p>
<p class="muted">链接仅能读取这一份有期限的文件，请仅分享给预期接收者。它不能访问设计服务或控制打印机。</p>
</main></body></html>""".encode("utf-8")


def _range(value: str, size: int) -> tuple[int, int]:
    """One inclusive byte range; reject malformed, multiple or empty ranges."""
    # Also bounds integer parsing and pathological header work.
    if len(value) > 80 or not (match := _RANGE.fullmatch(value)):
        raise ValueError("Invalid range")
    first, last = match.groups()
    if not first and not last:
        raise ValueError("Empty range")
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
        if start >= size or end < start:
            raise ValueError("Unsatisfiable range")
    else:
        suffix = int(last)
        if not suffix:
            raise ValueError("Empty suffix")
        start, end = max(0, size - suffix), size - 1
    return start, end


def create_handoff_app(store: HandoffStore) -> FastAPI:
    """Create a read-only app around existing capabilities, without issuing any.

    The caller must disable HTTP/proxy path access logs: capability URLs are
    bearer secrets. This app never logs them or underlying store exceptions.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,
                  debug=False, redirect_slashes=False)

    @app.middleware("http")
    async def readonly(request: Request, call_next):
        if request.method not in ("GET", "HEAD"):
            return _respond(request, b"Method not allowed", status=405,
                            headers={"Allow": "GET, HEAD"})
        response = await call_next(request)
        for name, value in _HEADERS.items():
            response.headers[name] = value
        return response

    @app.exception_handler(404)
    async def not_found(request: Request, _exc):
        return _unavailable(request)

    @app.api_route("/handoffs/{token}", methods=["GET", "HEAD"])
    def landing(request: Request, token: str):
        try:
            body = _page(store.landing(token))
        except Exception:
            return _unavailable(request)
        return _respond(request, body, media_type="text/html")

    @app.api_route("/handoffs/{token}/manifest", methods=["GET", "HEAD"])
    def manifest(request: Request, token: str):
        try:
            body = json.dumps(store.get(token), ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
        except Exception:
            return _unavailable(request)
        return _respond(request, body, media_type="application/json")

    @app.api_route("/handoffs/{token}/file.gcode.3mf", methods=["GET", "HEAD"])
    def download(request: Request, token: str):
        try:
            metadata = store.get(token)
            data = store.payload(token)
            # Metadata and payload are each validated by the store. Bind them
            # once more before constructing any HTTP file headers.
            if (len(data) != metadata["bytes"]
                    or hashlib.sha256(data).hexdigest() != metadata["sha256"]):
                return _unavailable(request)
            basename = metadata["basename"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.gcode\.3mf", basename):
                return _unavailable(request)
            etag = '"' + metadata["sha256"] + '"'
        except Exception:
            return _unavailable(request)
        headers = {"Accept-Ranges": "bytes", "ETag": etag,
                   "X-Artifact-SHA256": metadata["sha256"],
                   "Content-Disposition": f'attachment; filename="{basename}"'}
        # RFC 9110: Range is a GET modifier and must be ignored for HEAD.
        requested = request.headers.get("range") if request.method == "GET" else None
        if_range = request.headers.get("if-range")
        if requested and (not if_range or if_range == etag):
            try:
                start, end = _range(requested, len(data))
            except ValueError:
                return _respond(request, b"Range not satisfiable", status=416,
                                headers={**headers, "Content-Range": f"bytes */{len(data)}"})
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            return _respond(request, data[start:end + 1], status=206,
                            media_type="application/octet-stream", headers=headers)
        return _respond(request, data, media_type="application/octet-stream", headers=headers)

    return app
