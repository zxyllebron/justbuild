# 便携只读自检

`justbuild.diagnostics.doctor_report() -> dict` 返回 JSON 可序列化的安装清单，供 CLI `doctor` 调用。它使用 Python 标准库和已安装分发包的 metadata，不导入 CadQuery/OCP/FastAPI 等被检查依赖，不执行切片器或模型 CLI，不访问打印机、账户或网络，不读取 LAN 私有配对文件。没有新增依赖或改变共享环境。

本功能用于回答“这个环境还缺什么”，不能替代真实小 STEP 的生成／独立核验、实际切片、打印机配对、授权或装配验收。`status:'ok'` 仅表示声明的核心 metadata 与打包资产存在，不能作为开始打印的条件。

## 接口与状态

```python
import json
from justbuild.diagnostics import doctor_report

report = doctor_report()
print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
```

主控已接入 `justbuild doctor`：打印 JSON，核心 `ok` 返回 0，核心缺项返回 2；可选项缺失单独留在报告中。模块函数没有调用参数，也不会写报告文件。使用者如需分享结果，可自行把 JSON 保存到指定位置。

| 字段 | 范围 |
| --- | --- |
| `status` / `core.status` | Python 至少 3.11、全部核心分发 metadata、18 份核心网页资产；有缺项为 `needs_input`，否则 `ok` |
| `runtime` | Python 版本／实现、系统、内核发行号与架构；没有主机名、用户目录、解释器绝对路径 |
| `packages` | 分发包版本、`core`／`optional_lan` 角色、metadata 存在性；全部 `import_tested:false` |
| `optional.slicer` | 操作者配置是否齐全，二进制是否为正规可执行文件、SHA-256 是否匹配、资源文件存在性；缺项不改变核心状态 |
| `optional.lan` | 仅 paho-mqtt metadata；不读取配对文件、不检查当前网络或设备、不判断是否可发印 |
| `optional.pairing_assets` | 三份本机配对页面资产存在性；缺项单独报告 |
| `optional.model_drivers` | `codex`／`claude` 在 PATH 上的命令存在性；不调用命令、不检查登录或 API、无自动闭环验证 |
| `tested_environment` | 与已测 CAD 版本逐项比较；保留“不保证相同几何或打印结果”标记 |
| `check_scope` | 明确本次没有网络／设备访问、私有配对文件读取、程序执行或被检查依赖的实际导入 |

核心 metadata 包含 `cadquery`、`cadquery-ocp`、`fastapi`、`uvicorn`、`httpx`、`python-multipart`；`paho-mqtt` 为可选 LAN 依赖。OCP 使用实际分发名 `cadquery-ocp`，不会把另一个同名相似包当成已验证后端。找不到 metadata、metadata 无法读取或版本字段异常时保留缺项，异常文本不进入报告。

核心网页清单来自当前页面的直接与间接模块依赖，包括 Three.js、GLTFLoader、OrbitControls、SkeletonUtils、BufferGeometryUtils、软件渲染，以及孔位／尺寸／功能访问／打印审阅／新任务领取模块。只检查每个文件是非空正规文件，不执行 JS、不做浏览器渲染；输出缺项仅为打包的相对文件名。

## 切片器配置检查

显式读取以下四个已有公共部署变量：

- `JUSTBUILD_BAMBU_BINARY`
- `JUSTBUILD_BAMBU_SHA256`
- `JUSTBUILD_BAMBU_VERSION`
- `JUSTBUILD_BBL_RESOURCES`

JSON 只报告变量名及是否设置，不输出原始值、二进制路径、资源目录或其他用户路径。CLI 命令发现由 `shutil.which` 使用 PATH（Windows 还可能使用 PATHEXT），结果也只保留布尔值。不遍历环境变量，不读取 `.env`、账户密钥、模型 API key 或打印机访问码。

配置二进制必须是正规文件，并通过当前用户的可执行权限检查；有效的 64 位十六进制 SHA-256 才触发只读散列。文件读取前后核对设备、inode、大小、mtime、ctime；读取中发生变化则 `stable_during_read:false`、`sha256_matches:null`，不会标记匹配。非执行文件、目录、无效 SHA 或 I/O 失败均保留为待补充，不启动程序。报告不输出文件内容或异常中的路径。

资源树仅通过 stat 检查三份当前默认 A1 参考配置存在且非空：

- `machine/Bambu Lab A1 0.4 nozzle.json`
- `process/0.20mm Standard @BBL A1.json`
- `filament/Bambu PLA Basic @BBL A1.json`

不会读取或展开这些配置的继承链，因此 `profile_inheritance_verified:false`。即使四变量齐全、二进制散列匹配且三份资源文件都存在，切片器的版本输出、运行库／locale、CLI 能力、真实切片结果与厂商来源仍是未检查。`optional.slicer.status:'ok'` 只代表这些离线配置条件一致，不能推导程序可以运行或来自官方。正式切片阶段继续使用既有版本、输入和产物散列检查。

当前 doctor 进程的环境与正在运行的服务器环境可能不同；当前 shell 未设置四变量，不表示电脑未安装切片器或已有服务无法切片。需要在实际部署的进程环境中运行自检来比较。

## 版本对照与复现边界

已测 CAD 参考为 CadQuery `2.6.1` 和 cadquery-ocp `7.8.1.1.post1`。每项返回 observed／tested／matches；其他版本存在时清单仍可为 `ok`，但 `tested_cad_versions_match:false`，实际兼容性未检查。doctor 不解析全部依赖约束，不代替安装器或锁文件检查。

即使这两个版本一致，也没有锁定所有传递依赖、平台、STEP 导入差异、模型生成建议、完整制造参数和设备状态。因此报告固定保留 `complete_dependency_lock_verified:false` 与 `same_geometry_or_print_result_guaranteed:false`。别人要比较结果，应继续对齐同一输入 STEP 散列、已确认需求与几何参数、完整软件版本、实际切片器及 profile 散列，并运行同一参考测试。真实板适配、功能可用性和实体装配需要各自证据。

## 本次验证

实际执行：

```sh
PYTHONPATH=src /home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/integration/test_diagnostics.py -q
```

结果：**28 passed in 0.10s**。测试使用 mock metadata、临时假二进制与临时静态／资源文件，禁止 socket 和子进程调用。覆盖每个核心依赖缺失、可选项缺失不阻断核心、Python 版本、已测 CAD 对照、异常版本／异常文本脱敏、核心／可选资产、合法散列、错误／非法散列、空资源目录、非执行文件／目录拒绝读取、I/O 失败、读中变化、CLI 存在性与未调用标记，以及仅访问白名单环境和已配置二进制。未接触真实打印机或私有配对文件。

本次在当前命令环境只读调用 `doctor_report()`：Python 3.12.3，核心 metadata 与 18 份核心、3 份配对资产均存在；CadQuery/OCP 与上述参考版本一致。该进程未设置四个切片器变量，因此 slicer 为可选 `needs_input`。Codex/Claude 命令存在，但未调用或验证授权。此结果只属于本次命令环境，不作为在线服务、CAD 运行或打印机验收。

模块文件：`src/justbuild/diagnostics.py`、`tests/integration/test_diagnostics.py`、本文件；主控另已接入 CLI 并完成公开入口测试。
