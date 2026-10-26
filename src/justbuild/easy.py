"""Chinese terminal entry for local intake/review; no automatic design approval or print."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import sys
import threading
import time
from urllib.parse import unquote, urlparse
import webbrowser

_DEFAULT_REQUIREMENTS = '上下盖、螺丝固定；保留原PCB安装孔；逐项审阅所有接口、按钮及插拔操作空间。'
_JOB = re.compile(r'[0-9a-f]{32}')


def _clean(value: str, limit: int = 160) -> str:
    return ''.join(c for c in str(value) if c.isprintable())[:limit]


def step_path(value: str) -> Path:
    """Accept a pasted path, POSIX drag/drop quoting or a local file URI; never execute it."""
    raw = value.strip()
    if not raw:
        raise ValueError('没有选择文件。请拖入一个 STEP/STP 文件。')
    if raw.startswith('file:'):
        uri = urlparse(raw)
        if uri.scheme != 'file' or uri.netloc not in ('', 'localhost') or uri.query or uri.fragment:
            raise ValueError('请选择本机文件，不支持远程文件链接。')
        candidate = Path(unquote(uri.path)).expanduser()
    else:
        candidate = Path(raw).expanduser()
        if not candidate.is_file():
            try:
                pieces = shlex.split(raw)
            except ValueError:
                raise ValueError('文件路径的引号不完整，请重新拖入文件。') from None
            if len(pieces) != 1:
                raise ValueError('一次请选择一个 STEP/STP 文件。')
            candidate = Path(pieces[0]).expanduser()
    if candidate.suffix.lower() not in ('.step', '.stp'):
        raise ValueError('当前支持 STEP/STP 三维板卡文件，请换一个文件。')
    if not candidate.is_file():
        raise ValueError('没有找到文件，请检查路径，或把文件重新拖进终端。')
    if not 1 <= candidate.stat().st_size <= 100 * 1024 * 1024:
        raise ValueError('文件为空或超过当前 100 MiB 限制。')
    with candidate.open('rb') as stream:
        if b'ISO-10303-21' not in stream.read(512):
            raise ValueError('这个文件不是可识别的 STEP Part 21，请重新导出板卡模型。')
    return candidate.resolve()


@contextmanager
def _working(message: str):
    print(message, flush=True)
    stop = threading.Event()
    def tick():
        start = time.monotonic()
        while not stop.wait(.5):
            print('\r  处理中，已用时 %d 秒…' % (time.monotonic() - start), end='', flush=True)
    thread = None
    if sys.stdout.isatty():
        thread = threading.Thread(target=tick, daemon=True)
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread:
            thread.join()
            print('\r' + ' ' * 45 + '\r', end='', flush=True)


def _open(url: str, enabled: bool) -> bool:
    if not enabled or not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        return False
    try:
        return bool(webbrowser.open(url, new=2))
    except (OSError, webbrowser.Error):
        return False


def _show_link(url: str, *, open_browser: bool, management: bool = False) -> None:
    print('\n打开下面的链接继续：\n' + url, flush=True)
    if management:
        print('这是你的一次性本机管理链接，请勿转发。')
    if _open(url, open_browser):
        print('已请求浏览器打开。若未出现页面，请点击上面的链接。')
    else:
        print('终端/云服务器可复制链接到可访问该服务的浏览器；127.0.0.1 指运行服务的机器。')


def _service(port: int, data_dir: Path | None) -> dict:
    from .easy_service import ensure_review_service
    with _working('正在准备审阅服务…'):
        result = ensure_review_service(port=port, data_dir=data_dir)
    print('审阅服务已就绪。' if result['started'] else '已连接正在运行的审阅服务。')
    return result


def submit_step(source: Path, requirements: str, *, label: str | None = None,
                port: int = 18765, data_dir: Path | None = None, open_browser: bool = True) -> dict:
    """Create only a new unconfirmed intake; preserve review, owner and print gates."""
    from .intake import intake_step
    service = _service(port, data_dir)
    with _working('正在读取板卡并整理提案，接口和孔位将在页面中确认…'):
        result = intake_step(source, requirements, data_dir=Path(service['data_dir']),
                             label=label or source.stem, base_url=service['base_url'])
    print('\n文件已接收：' + _clean(source.name))
    print('需求已保存：' + _clean(requirements, 240))
    if result['state'] == 'awaiting_confirmation':
        print('下一步：在页面确认 PCB、孔位、接口和操作空间，再生成外壳。')
    else:
        print('板卡资料需要补充，请打开页面查看具体原因；未自动生成或打印。')
    _show_link(result['local_owner_url'] or result['review_url'], open_browser=open_browser,
               management=bool(result['local_owner_url']))
    print('没有启动打印。')
    return result


def _recent_jobs(directory: Path) -> list[dict]:
    candidates = []
    for path in directory.glob('*/job.json'):
        try:
            if (path.is_symlink() or path.parent.is_symlink() or _JOB.fullmatch(path.parent.name) is None
                    or not path.is_file() or path.stat().st_size > 20 * 1024 * 1024):
                continue
            value = json.loads(path.read_text())
            if value.get('job_id') != path.parent.name:
                continue
            artifacts = value.get('artifacts', [])
            if not isinstance(artifacts, list):
                continue
            value['artifacts'] = [a for a in artifacts if isinstance(a, dict)]
            candidates.append(value)
        except (OSError, ValueError, AttributeError):
            continue
    # Show complete review models before newly imported/incomplete proposals.
    candidates.sort(key=lambda j: (any(a.get('kind') in ('review_glb', 'review_glb_light') for a in j.get('artifacts', [])),
                                    str(j.get('updated_at', ''))), reverse=True)
    return candidates[:10]


def _existing(port: int, data_dir: Path | None, open_browser: bool) -> None:
    service = _service(port, data_dir)
    jobs = _recent_jobs(Path(service['data_dir']))
    if not jobs:
        print('还没有设计，请先选择“新建外壳”。')
        return
    for index, job in enumerate(jobs, 1):
        has_model = any(a.get('kind') in ('review_glb', 'review_glb_light') for a in job.get('artifacts', []))
        print('%d. %s  [%s%s]' % (index, _clean(job.get('label', job['job_id'])),
                                  '已有三维模型' if has_model else '待确认提案', '；等待新版审阅' if job.get('print_hold') else ''))
    raw = input('选择编号 [1]：').strip() or '1'
    if not raw.isdigit() or not 1 <= int(raw) <= len(jobs):
        raise ValueError('编号无效，请重新选择。')
    job = jobs[int(raw) - 1]
    relative = job.get('share_url') if job.get('owner_sha256') else job.get('review_url')
    relative = relative or '/?job=' + job['job_id']
    if not isinstance(relative, str) or not relative.startswith('/?job=' + job['job_id']):
        relative = '/?job=' + job['job_id']
    print('打开已有结果不会重新生成、解除审阅限制或启动打印。')
    _show_link(service['base_url'] + relative, open_browser=open_browser)


def _printer(port: int, data_dir: Path | None, open_browser: bool) -> None:
    from .printing.lan import lan_profile_status, check_lan_connection
    profile = lan_profile_status()
    if not profile.get('offline_ready'):
        service = _service(port, data_dir)
        print('尚未完成这台机器的打印机配对，请在页面一次录入自己的设备资料。')
        _show_link(service['base_url'] + '/printer-setup', open_browser=open_browser)
        return
    with _working('正在只读连接 A1，不上传文件或启动打印…'):
        result = check_lan_connection(timeout=8)
    if result.get('connection_verified'):
        print('A1 已连接，设备身份和证书已核对。')
        print('当前状态：' + _clean(result.get('printer', {}).get('state', '未知')))
        print('设备报告空闲/完成；实际打印仍需审阅具体版本。' if result.get('ready_for_new_print') else '设备当前忙碌或状态未明确，将保留现有任务。')
    else:
        print('暂未连通。请确认电脑与 A1 网络可达、屏幕 IP 与配对记录一致。')
    print('没有启动打印。')


def run_easy(*, step: str | None = None, requirements: str | None = None,
             label: str | None = None, port: int = 18765, data_dir: Path | None = None,
             open_browser: bool = True) -> int:
    """One entry, four Chinese choices; no shell evaluation or hardware actuation."""
    print('\njustbuild · 板卡外壳助手\n提交板卡 → 确认接口 → 查看外壳 → 审阅后打印\n')
    try:
        if step is not None:
            source = step_path(step)
            text = requirements if requirements is not None else input('描述你的要求，直接回车使用默认方案：\n> ').strip() or _DEFAULT_REQUIREMENTS
            if not text.strip() or len(text) > 20000:
                raise ValueError('请填写 1–20000 字的需求。')
            submit_step(source, text, label=label, port=port, data_dir=data_dir, open_browser=open_browser)
            return 0
        while True:
            print('1  新建外壳：拖入 STEP 文件，填写需求')
            print('2  查看已有设计：直接打开三维结果')
            print('3  检查 A1 连接：不会启动打印')
            print('4  运行合成板自检：不使用打印机')
            print('0  退出')
            choice = input('请选择 [1]：').strip() or '1'
            try:
                if choice == '0':
                    return 0
                if choice == '1':
                    source = step_path(input('\n把 STEP/STP 文件拖进终端，或粘贴路径：\n> '))
                    print('默认：' + _DEFAULT_REQUIREMENTS)
                    text = input('描述你的要求，直接回车使用默认方案：\n> ').strip() or _DEFAULT_REQUIREMENTS
                    if len(text) > 20000:
                        raise ValueError('需求过长，请控制在 20000 字以内。')
                    submit_step(source, text, label=label, port=port, data_dir=data_dir, open_browser=open_browser)
                elif choice == '2':
                    _existing(port, data_dir, open_browser)
                elif choice == '3':
                    _printer(port, data_dir, open_browser)
                elif choice == '4':
                    from .selfcheck import run_self_check
                    import uuid
                    base = Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))/'justbuild/selftests'
                    target = base / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
                    with _working('正在生成并独立验证合成板外壳…'):
                        report = run_self_check(target)
                    print('自检通过。' if report['verdict'] == 'pass' else '自检未通过，请查看报告。')
                    print('报告：' + report['report_path'])
                else:
                    print('请输入 0、1、2、3 或 4。')
            except (ValueError, OSError) as exc:
                print('未完成：' + _clean(str(exc), 320))
            print()
    except (EOFError, KeyboardInterrupt):
        print('\n已退出；未启动打印。')
        return 0
    except (ValueError, OSError) as exc:
        print('未完成：' + _clean(str(exc), 320), file=sys.stderr)
        return 2
