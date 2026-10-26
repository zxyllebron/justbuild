# 终端向导的本机审阅服务

`ensure_review_service(*, port=18765, data_dir=None, timeout=12) -> dict` 为中文终端向导发现或启动审阅服务。它只管理 `justbuild serve` 的本机入口，不调用 CAD、模型 CLI、切片、配对、打印提交或设备操作。

## 返回合同

```python
from justbuild.easy_service import ensure_review_service

service = ensure_review_service()
# {"base_url": "http://127.0.0.1:18765", "data_dir": "/absolute/jobs", "started": False}
```

在线服务通过兼容检查时，始终复用其返回的绝对 `data_dir`，忽略本次传入的其他目录。这保证向导导入的任务与浏览器看到的任务属于同一服务。发现在线服务不写启动状态、不创建新的任务目录、不更改在线服务的配置。

冷启动成功返回 `started:true`，并附带绝对 `log_path`。默认任务目录优先级为显式 `data_dir`、`JUSTBUILD_EASY_DATA_DIR`、`~/.local/share/justbuild/jobs`，启动前展开并转换为绝对路径。

`port` 只接受 1–65535 的整数；`timeout` 是正的有限秒数。超时和不兼容服务均抛出中文 `ValueError`，错误中不拼接原始响应、底层异常、环境变量值或凭据。

## 发现与单次启动

1. 使用直接 TCP HTTP GET `http://127.0.0.1:PORT/api/local/workspace`。HTTPConnection 不使用代理变量、不自动跟随重定向；仅接受 200 JSON、`product:'justbuild'`、`interface:'easy-v1'` 与绝对 `data_dir`。
2. 其他 HTTP 状态、旧接口、错误身份、相对目录、超大／无效 JSON 或无法确认的连接状态均停止本次操作。原服务保留，不接管、不停止、不重启。
3. 只有明确连接被拒绝，才进入该端口的私有启动锁。得到锁后重新发现服务；另一个向导已启动时直接复用。
4. 检查已有启动记录。若先前 PID 仍存在、记录只有启动 intent，或记录无法安全判断，则保留未知状态，不再启动第二个进程。已退出进程的记录可以由本次新启动记录替换，旧日志保留。
5. 再通过临时绑定 `127.0.0.1:PORT` 确认没有监听／端口占用。该确认到新服务绑定之间仍可能出现其他进程抢占；遇到冲突后保留相关进程，不进行自动重启或换端口。
6. 准备目录、私有日志与启动 intent，只执行一次以下固定 argv：

```text
sys.executable -m justbuild serve --host 127.0.0.1 --port PORT --data-dir ABSOLUTE_DIRECTORY
```

实际调用是参数列表，未使用 shell。子进程继承当前配置并设置 `JUSTBUILD_ENABLE_LOCAL_PRINTER_SETUP=1`、`JUSTBUILD_ENABLE_LAN_PRINTING=1`。这两个开关只开放本机流程，设备配对、具体文件版本审阅与打印审批仍由既有网页和控制器执行；启动服务不会开始打印。父进程的环境保持原样。

使用 `stdin=DEVNULL`、`stdout=私有日志`、`stderr=STDOUT`、`start_new_session=True`、`close_fds=True`。健康等待只检查该子进程状态和固定 workspace 接口。单次调用从首次发现开始共享同一个 monotonic deadline，HTTP 读取也有截止计时器；计时器只关闭本模块自己的客户端 socket。

子进程退出、启动结果不明或健康超时后，函数不会 terminate、kill 或自动再次 Popen。等待超时仍保留启动记录，后续向导会先复用可用服务，或拒绝对尚在运行／未知的启动重复操作。

## 私有状态

状态目录为 `${XDG_STATE_HOME:-~/.local/state}/justbuild/easy`，目录 0700、锁／记录／日志 0600，要求当前用户所有，拒绝通过符号链接访问启动状态。路径以用户状态目录为基础，不写进源码、任务下载产物或模型提示。

文件包括按端口区分的启动锁、最小启动记录和每次独立的日志。启动记录只有 PID、阶段、时间及日志路径；不包含环境副本、访问码、会话 Cookie 或模型 key。日志接收服务标准输出／错误，因此按私有文件保存；函数不会把日志内容直接打印到终端错误中。

当前自动冷启动依赖 POSIX `flock`，目标是现有 Linux 桌面／云端部署。Linux 通过 `/proc/PID` 只读判断既有进程；其他 POSIX 平台仅用 signal 0 存在性检查，不发送终止信号。无 flock 的平台可以复用兼容的在线服务；需要先手动启动服务，当前没有承诺自动冷启动验收。

本模块不提供停止服务或自动清理未知启动记录的命令。已有服务需更新时，由操作者另行确认其正在运行的任务后管理。主控负责中文向导、CLI／启动器、workspace 只读 endpoint 和真实终端验收。

## 验证

新增文件仅为 `src/justbuild/easy_service.py`、`tests/integration/test_easy_service.py`、本文件。未修改原服务或其他模块。

```sh
PYTHONPATH=src /home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/integration/test_easy_service.py -q
```

当前结果 **34 passed in 0.37s**。覆盖在线目录复用、目录优先级、固定 argv／环境继承／父环境不变／私有权限、锁后重复发现、端口仍占用拒绝、先前 PID／未知启动记录阻止重复、健康超时与子进程退出不重启、异常脱敏、参数校验、无代理与无重定向、旧服务及错误 JSON 合同、路径错误、符号链接拒绝、启动锁总超时，以及两个并发向导只生成一个后台启动。

HTTP 测试只在随机端口运行隔离的临时测试服务器；全部 Popen 都是 mock，没有启动真实 justbuild app、模型或打印机操作。没有访问或重启正在使用的 18765 服务。实际中文向导与已安装启动器的终端验收由主控进行。
