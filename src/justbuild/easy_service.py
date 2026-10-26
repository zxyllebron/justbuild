"""Reuse or start a loopback review service; never stop another process.

Cold start currently requires POSIX flock. Runtime files contain no environment
values or credentials. An uncertain earlier launch prevents an automatic retry.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
import math
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

_MAX_REPLY = 16384


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("审阅服务未在时限内就绪；已有进程会保留，请稍后再试或查看本机启动日志。")
    return remaining


def _workspace(port: int, deadline: float) -> str | None:
    """Direct HTTPConnection never uses proxy environment or follows redirects."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=min(1.0, _remaining(deadline)))
    timer = None
    try:
        connection.connect()
        client_socket = connection.sock
        def stop_read():
            try:
                client_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        # Bound slow headers/body as well as connect; only our client socket is
        # shut down. No signal or command is sent to the serving process.
        timer = threading.Timer(_remaining(deadline), stop_read)
        timer.daemon = True
        timer.start()
        connection.request("GET", "/api/local/workspace", headers={"Accept": "application/json", "Connection": "close"})
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ValueError("这个端口已有其他或旧版服务，请更换端口或手动更新；原进程没有停止。")
        raw = response.read(_MAX_REPLY + 1)
        if len(raw) > _MAX_REPLY:
            raise ValueError("端口上的服务返回了不兼容的信息；原进程没有停止，请更换端口。")
        value = json.loads(raw)
        path = value.get("data_dir") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or value.get("product") != "justbuild" or value.get("interface") != "easy-v1"
                or not isinstance(path, str) or not path or len(path) > 4096 or "\0" in path or not Path(path).is_absolute()):
            raise ValueError("这个端口没有兼容的 justbuild 审阅接口；原进程没有停止，请更换端口。")
        return str(Path(path))
    except ConnectionRefusedError:
        return None
    except (OSError, http.client.HTTPException):
        raise ValueError("无法确认本机端口的服务状态；本次不会重复启动，请稍后重试或更换端口。") from None
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("端口上的服务返回了无效数据；原进程没有停止，请更换端口。") from None
    finally:
        if timer is not None:
            timer.cancel()
        connection.close()


def _port_unused(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _state_directory() -> Path:
    raw = os.environ.get("XDG_STATE_HOME")
    root = (Path(raw).expanduser() if raw else Path.home() / ".local/state") / "justbuild/easy"
    root = root.absolute()
    try:
        if any(path.is_symlink() for path in (root, *root.parents)):
            raise OSError()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise OSError()
        root.chmod(0o700)
        return root
    except (OSError, ValueError):
        raise ValueError("无法创建本机私有启动目录；请检查用户状态目录的权限。") from None


def _private_open(path: Path, flags: int) -> int:
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(descriptor)
        raise OSError()
    os.fchmod(descriptor, 0o600)
    return descriptor


@contextmanager
def _startup_lock(state: Path, port: int, deadline: float):
    try:
        import fcntl
    except ImportError:
        raise ValueError("当前系统暂不支持自动后台启动；请先手动运行本机审阅服务。") from None
    try:
        descriptor = _private_open(state / f"service-{port}.lock", os.O_RDWR | os.O_CREAT)
    except OSError:
        raise ValueError("无法读取本机私有启动锁；没有启动新进程。") from None
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.05, _remaining(deadline)))
            except OSError:
                raise ValueError("本机启动锁暂不可用；没有启动新进程，请稍后重试。") from None
        _remaining(deadline)
        yield
    finally:
        os.close(descriptor)


def _prior_launch_pending(record_path: Path) -> bool:
    try:
        descriptor = _private_open(record_path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        raise ValueError("已有启动记录无法安全读取；不会自动启动第二个服务。") from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(_MAX_REPLY + 1)
        value = json.loads(data) if len(data) <= _MAX_REPLY else None
        pid = value.get("pid") if isinstance(value, dict) else None
        if type(pid) is not int or not 1 <= pid <= 2**31 - 1:
            return True  # Previous launch outcome unknown: never blindly retry.
        if sys.platform.startswith("linux"):
            return Path("/proc", str(pid)).exists()  # Read-only, no process signal.
        try:
            os.kill(pid, 0)  # POSIX existence probe only; never a termination signal.
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    except (OSError, ValueError, TypeError):
        raise ValueError("已有启动记录状态不明；不会自动启动第二个服务。") from None


def _record(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".launch-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def ensure_review_service(*, port: int = 18765, data_dir: Path | None = None, timeout: float = 12) -> dict:
    """Reuse a compatible service or make one bounded attempt to start it.

    The existing service's data_dir always wins. A failed/uncertain launch is not
    killed or automatically repeated. Only a local review server is launched;
    model generation, slicing and printing still require their normal workflow.
    """
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("端口必须是 1 到 65535 的整数。")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("等待时间必须是大于零的有限秒数。")
    deadline = time.monotonic() + float(timeout)
    base_url = f"http://127.0.0.1:{port}"
    existing = _workspace(port, deadline)
    if existing is not None:
        return {"base_url": base_url, "data_dir": existing, "started": False}
    state = _state_directory()
    with _startup_lock(state, port, deadline):
        existing = _workspace(port, deadline)
        if existing is not None:
            return {"base_url": base_url, "data_dir": existing, "started": False}
        record_path = state / f"service-{port}.json"
        if _prior_launch_pending(record_path):
            raise ValueError("上次启动仍在运行或结果不明；不会重复启动，请稍后再试或查看本机启动日志。")
        if not _port_unused(port):
            raise ValueError("这个端口已被占用或正在启动；没有停止原进程，请稍后重试或更换端口。")
        try:
            raw = data_dir if data_dir is not None else (os.environ.get("JUSTBUILD_EASY_DATA_DIR") or Path.home() / ".local/share/justbuild/jobs")
            try:
                target = Path(raw).expanduser().resolve()
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            except (OSError, ValueError, TypeError, RuntimeError):
                raise ValueError("无法使用这个任务目录，请检查路径和目录权限。") from None
            log_path = state / f"service-{port}-{secrets.token_hex(8)}.log"
            log_descriptor = _private_open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(log_descriptor, "ab", buffering=0) as log:
                _record(record_path, {"pid": None, "phase": "launching", "created_at": time.time()})
                environment = os.environ.copy()
                environment.update(JUSTBUILD_ENABLE_LOCAL_PRINTER_SETUP="1", JUSTBUILD_ENABLE_LAN_PRINTING="1")
                _remaining(deadline)
                try:
                    process = subprocess.Popen([sys.executable, "-m", "justbuild", "serve", "--host", "127.0.0.1",
                                                "--port", str(port), "--data-dir", str(target)],
                                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=True, close_fds=True, env=environment)
                except (OSError, ValueError):
                    record_path.unlink(missing_ok=True)  # Known failure to create the child.
                    raise ValueError("本机审阅服务未能启动，请检查 Python 安装和私有启动日志。") from None
                _record(record_path, {"pid": process.pid, "phase": "spawned", "created_at": time.time(), "log_path": str(log_path)})
        except ValueError:
            raise
        except OSError:
            raise ValueError("无法准备本机审阅目录或启动日志；没有重试启动，请检查用户目录权限。") from None
        while True:
            _remaining(deadline)
            if process.poll() is not None:
                raise ValueError("本机审阅服务启动后已退出，请查看私有启动日志；没有再次启动。")
            workspace = _workspace(port, deadline)
            if workspace is not None:
                return {"base_url": base_url, "data_dir": workspace, "started": True, "log_path": str(log_path)}
            time.sleep(min(0.1, _remaining(deadline)))
