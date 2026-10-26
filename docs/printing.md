# 04 打印模块：实现、证据与集成交接

更新日期：2026-10-24（Asia/Shanghai）。本聊天仅写入 `src/justbuild/printing/`、`tests/printing/` 和本文件；未修改共享依赖、架构、其他模块或账户/打印机设置。

**当前结论：本机已验证 Bambu Studio 2.8.2.61 的无显示环境 CLI 切片，输出含真实 plate G-code 的单板 3MF。A1 无界面云端上传、打印机接收、启动确认及实体完成均未验证；默认实时传输返回 `unsupported`。** 测试切片输入是合成方块，不是已验收的 PCB 外壳。没有执行实时打印、运动、加热、固件操作或账户操作。

## 官方资料与源码核验

以下为 2026-10-24 实际读取的一手资料；没有把论坛回复或第三方逆向库当作官方能力保证。

| 资料 | 本次得到的证据 | 证据边界 |
| --- | --- | --- |
| [官方 CLI Wiki](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage)（页面标注 2026-03-31 更新） | `--slice`、`--load-settings`、`--load-filaments`、`--export-3mf`；外部配置应为 full config | 切片 CLI 不等于云打印接口 |
| [官方 Docker Guide](https://github.com/bambulab/BambuStudio/wiki/Docker-Run-Guide)（2023） | 官方仓库提供容器运行说明 | 文档年代较早，包含网络/桌面环境前提，不能作为云端无网关下发验收 |
| [官方最新 release API](https://api.github.com/repos/bambulab/BambuStudio/releases/latest) | 当次返回 `v02.08.02.61`，发布时间 `2026-08-21T06:31:50Z` | “最新”只针对本次查询时间 |
| [BambuStudio.cpp](https://github.com/bambulab/BambuStudio/blob/926a7192574bcb9b3a732e1ec59a46d79cb45466/src/BambuStudio.cpp) | CLI 装载配置、切片、导出和导出路径拼接实现 | 对应本次固定版本，不保证未来 CLI 相同 |
| [NetworkAgent.cpp](https://github.com/bambulab/BambuStudio/blob/926a7192574bcb9b3a732e1ec59a46d79cb45466/src/slic3r/Utils/NetworkAgent.cpp#L185) 与 [NetworkAgent.hpp](https://github.com/bambulab/BambuStudio/blob/926a7192574bcb9b3a732e1ec59a46d79cb45466/src/slic3r/Utils/NetworkAgent.hpp) | Linux 动态加载网络组件；解析 `bambu_network_start_print` 等入口；地区设置独立存在 | 开源包装声明没有提供一个经过许可、已验证的 Python 无界面上传/下发 API |
| [PrintJob.cpp](https://github.com/bambulab/BambuStudio/blob/926a7192574bcb9b3a732e1ec59a46d79cb45466/src/slic3r/GUI/Jobs/PrintJob.cpp#L722) | GUI 云路径调用 `start_print`；存在本地和云路由、回退逻辑 | 本项目不能把 LAN 回退算作跨网络云端成功；SDK 返回成功也需分别收集各阶段证据 |
| [官方 Bambu Connect 集成说明（2025-01-20）](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/) | 官方组件/合作集成路线；Developer Mode 属于本地网络方案 | 未由该说明证明 A1 当前固件、Linux 无界面或公网云 VM 全链路可用 |
| [官方 Cloud Access 说明（2026-05-07）](https://blog.bambulab.com/setting-the-record-straight-on-cloud-access-and-community/) | 明确区分开源代码许可与私有云服务接入；不允许伪装官方客户端 | 本实现不引入客户端身份伪装、提取签名密钥或复制账户凭据 |

源码固定 tag `v02.08.02.61`；tag object `5b343460213bc727baa66b34008751083d38da7b` 解引用到 commit `926a7192574bcb9b3a732e1ec59a46d79cb45466`。原始研究文件仅在 `/tmp/justbuild-printing-20261024/research/`。

以下官方页面在本次访问返回 HTTP 402/不可访问：[Connect Wiki](https://wiki.bambulab.com/en/software/bambu-connect)、[第三方集成 Wiki](https://wiki.bambulab.com/en/software/third-party-integration)、[A1 固件历史](https://wiki.bambulab.com/en/a1/manual/a1-firmware-release-history)、[安全白皮书](https://cdn1.bambulab.com/trust-center/file/bambulab-security-whitepaper-en.pdf)。因此不声明当前 Connect 平台支持表、URL Scheme 参数、A1 最低固件版本或最新固件号已经核实；也没有凭旧公告推断用户打印机当前的授权控制状态。

## 兼容性与方案判断

| 路径 | 切片 | 上传/下发 | 是否依赖现场同网段 | 当前结论 |
| --- | --- | --- | --- | --- |
| 本机 Linux Bambu Studio 2.8.2.61 CLI | 已测：两个合成 10×10×2 mm 方块，单板 3MF | CLI 未提供已验证的云发送流程 | 切片本身不依赖 | 可用作切片 worker |
| 原生 Python 云传输 | 可调用独立 slicer | 未实现、未验证 | 目标是不依赖 | `UnsupportedCloudTransport`，不访问云账户 |
| 官方 Studio 网络组件/正式合作接口 | 与切片分离 | 官方源码有调用链；本项目无集成验收 | 需强制云路由并禁止 LAN 回退 | 优先调查路线；API/许可/身份认证均需部署方确认 |
| 云 VM + 原版官方 Studio 或 Connect | 可复用已切片产物 | 待验证文件导入、云路由、机器确认和回执 | 候选设计不要求现场网关 | 允许的备选方案，未创建 VM、未安装组件、未登录、未做 GUI 自动化下发 |
| LAN MQTT/FTP / Developer Mode | 与切片无关 | 无论本地是否可行 | 是，或需要另建现场网络连接 | 不满足本任务的“无现场网关”云端约束，不作为降级成功 |
| FakeSlicer + FakeTransport | 明确模拟，不产生可打印 G-code | 内存模拟，无任何网络调用 | 否 | 仅用于合同/编排测试；不能证明任何云能力 |

云 VM 是待做技术实验的候选，不是已经可交付的云打印服务。可部署的最小逻辑为：headless core 生成最终文件和哈希 → 用户在网页审核并发出认证 start-print action → VM 中受支持的原版官方组件接收固定文件与目标打印机 → 返回上传、接收、启动回执和远端任务 ID → 独立轮询。若官方组件重新切片或改写产物，原审批绑定失效，应重新生成最终文件并重新审核。

未来验证应按以下顺序收集证据：固定官方组件版本/平台及允许的集成接口；只读核对账户地区、A1 绑定和实际固件/授权模式；确认安全文件导入及云路由；另获真实打印授权后，依次记录上传成功、打印机文件接收、启动 acknowledgement、相同远端任务的完成报告和人工实体完成检查。不能用在线状态、登录成功或 100% 进度替代这些步骤。未知提交结果必须通过既有幂等键/远端历史核对，不能再按一次开始。

## 已实现文件

| 文件 | 内容 |
| --- | --- |
| `src/justbuild/printing/__init__.py` | 公开 `prepare_print`、`submit_print`、`monitor_print` 及适配器类型 |
| `src/justbuild/printing/common.py` | JSON envelope、哈希、原子持久化、错误与输出路径保护 |
| `src/justbuild/printing/prepare.py` | 验证通过门槛、输入来源绑定、完整 A1 配置核对、隔离输入快照和 review-ready manifest |
| `src/justbuild/printing/profiles.py` | 递归解析现有官方资源树的继承/includes，导出完整 machine/process/filament JSON 及逐源文件哈希 |
| `src/justbuild/printing/slicer.py` | 固定二进制哈希/版本的 Bambu CLI、超时、私有 HOME/datadir、3MF ZIP/CRC/G-code 与有效配置回读；明确标注的 FakeSlicer |
| `src/justbuild/printing/transport.py` | Transport/Observation 协议、无网络 FakeTransport、默认不支持的实时云适配器和能力报告 |
| `src/justbuild/printing/jobs.py` | 审批绑定、认证审批回调接口、进程锁、提交前持久化、并发去重、未知结果保持、只读监控 |
| `src/justbuild/printing/__main__.py` | `prepare`、`submit`、`monitor`、`profiles`、`capabilities` 命令 |
| `tests/printing/conftest.py` | 打印模块独立的合成 fixture；假 STEP/假 pass 仅供模拟合同测试 |
| `tests/printing/test_printing_contract.py` | 审批、来源、篡改、并发、崩溃、超时、遥测和状态边界测试 |
| `tests/printing/test_profiles.py` | 配置继承/模板、源哈希、路径越界、循环与禁止覆盖测试 |
| `tests/printing/test_real_cli.py` | 显式启用后只运行本地真实切片，不探测账户/打印机 |

## 调用及 JSON 合同

兼容架构文件中的四个位置参数；额外适配器由可信服务端通过 keyword 注入，不从用户 JSON 动态加载类、执行文件或命令：

```python
prepare_print(request_path, cad_result_path, verification_path, output_dir,
              slicer=configured_slicer)
submit_print(print_job_path, approval_path, output_dir,
             transport=configured_transport,
             approval_validator=authenticated_review_validator,
             ledger_dir=persistent_shared_ledger)
monitor_print(submission_result_or_record_path, output_dir,
              transport=same_transport)
```

不注入 slicer 时，准备返回 `unsupported`；不注入 live transport 时，实时提交不会发生。没有集成认证审批回调时，实时提交返回 `REVIEW_AUTH_NOT_INTEGRATED`，即使输入 JSON 写了 `approved: true` 也不会发起任务。

请求使用现有 envelope，并增加 **printing 自有字段**。示例中的路径、哈希和 ID 必须来自实际选定文件；PLA/0.4/床板只是已执行合成测试的选择，绝非产品默认值：

```json
{
  "schema_version": "0.1",
  "job_id": "caller-job-id",
  "constraints": {"confirmed": true},
  "printing": {
    "printer": {"id": "caller-printer-id", "model": "Bambu Lab A1"},
    "nozzle_diameter_mm": 0.4,
    "material": "PLA",
    "bed_type": "Textured PEI Plate",
    "profiles": {
      "machine": {"id": "Bambu Lab A1 0.4 nozzle", "path": "profiles/machine.json", "sha256": "64 lowercase hex"},
      "process": {"id": "0.20mm Standard @BBL A1", "path": "profiles/process.json", "sha256": "64 lowercase hex"},
      "filament": {"id": "Bambu PLA Basic @BBL A1", "path": "profiles/filament.json", "sha256": "64 lowercase hex"}
    }
  }
}
```

准备条件：request/CAD/verification 同 job、`constraints.confirmed is True`、CAD `status=ok`、verification `status=ok` 且 `verdict=pass`。读取 verification worker 已采用的 `inputs.request.sha256` 与 `inputs.cad_result.sha256`，逐一绑定当前请求及 CAD result 的字节；也兼容早期调用方的同名 `*_sha256` 平铺字段，但冲突时拒绝。主模块必须在调用验证前写入最终 printing 配置，验证后改请求会使绑定失效。

CAD result 必须提供 `base_step`、`lid_step`、`base_stl`、`lid_stl`，均声明 `units=mm` 和正确 SHA-256。本模块不凭网格宣称 STEP 已验证，不执行新的几何检查；它信任独立 verifier 对其声明范围的 pass，再验证文件来源。缺 mesh 返回 `needs_input`。配置必须已经完整展开，没有未解析的继承/include；machine 必须是 A1，单喷嘴直径、材料及 process/filament 的显式 compatible_printers 必须对应。所有模型尺寸为 mm；本模块没有独立几何公差，未把切片成功解释为机械公差验证。

成功写出 `print_job.json`，包括 `state=awaiting_review`、`simulation`、最终 `sliced_artifact`、printer ID、完整配置 ID/哈希、slicer 二进制哈希/版本、输入文档哈希和已复制模型哈希。输入快照与实际切片输出在调用方 output 下独立的 `prepare-<uuid>/` 中；重复准备不会覆写之前的切片文件。主聊天应把 manifest 的哈希与审核版本一起固定，不在未重新审核时替换下载文件。

审批文档合同：

```json
{
  "schema_version": "0.1",
  "job_id": "caller-job-id",
  "approved": true,
  "action": "start_print",
  "approval_id": "server-review-action-reference",
  "simulation": false,
  "printer_id": "caller-printer-id",
  "print_job_sha256": "64 lowercase hex",
  "sliced_artifact_sha256": "64 lowercase hex",
  "approved_at": "2026-10-24T07:00:00+00:00",
  "expires_at": "2026-10-24T07:05:00+00:00"
}
```

时间示例已是历史，不能直接用于执行。时间必须带时区、未过期且有效窗口最多 15 分钟。认证 callback 接收 `(approval_dict, print_job_dict)`，必须仅在服务端已验证该 human review action 与具体 manifest、机器、最终字节绑定时返回 **布尔 True**。审批 ID 不是秘密；不要在这个文档存 token、密码、签名私钥或 signed URL。服务端保持身份/授权状态；本模块不定义最终的用户认证策略。

模拟要求 `FakeSlicer` 输出与 `FakeTransport` 模式匹配，审批明确 `simulation=true`。FakeSlicer 只写 `SIMULATION-not-printable.json`；不造一个看似可用的 G-code。模拟 CAD/验证不得放行一个标为实时的产物。

## 状态、去重与监控

```text
prepare: blocked -> awaiting_review
submit:  blocked -> dispatching -> uploaded / printer_received /
         start_acknowledged / printing / paused / completed / failed / cancelled
         dispatching -> unknown（超时、异常、无有效回执）
monitor: 只读 poll，不会 submit，不会自动重试
```

每次 Observation 必须携带相同 remote_job_id、来源 evidence_id 和新鲜的带时区时间。超过五分钟旧、超前超过五秒、远端 job 不同、状态倒退或终态重新开始均拒绝。部署应同步系统时间。`evidence_id` 必须由受信适配器生成无凭据的证据索引，不传原始网络响应或认证 URL；核心丢弃异常文本，防止供应商异常把秘密写入结果。

`upload`、`printer_receipt`、`start_acknowledgement`、`reported_completion`、`physical_completion` 分别记录 `not_observed/observed`。看见一个后期状态不会补造早期回执；远端 `completed` 只填 reported_completion。实体完成仍为 `not_observed`，需另有实体检查机制。

ledger 键绑定 job ID、printer ID 和模拟模式；同一 job/机器只能尝试一次，改产物不能绕过去重。所有并发服务实例必须共享同一个**持久化、支持 flock/原子 rename/fsync 的本地文件系统目录**，并将每个 HTTP 操作输出写到自己的目录。本实现没有分布式锁；多 VM 或不保证 flock 语义的存储须由主模块替换为数据库事务/分布式幂等协调。默认 ledger 在 output 下，只适合单目录调用；生产必须显式传共享 `ledger_dir`。

记录在外部副作用前写盘。进入 dispatching 后发生进程崩溃、网络超时、无效回执或其他异常，保留原尝试，不重发。后续调用返回已有记录并标记去重；`retry_allowed` 始终为 false。提交给 transport 的是 ledger 内重新核对哈希的只读文件快照。适配器也必须不在内部盲重试，不默认切换 LAN 或重新切片。

`monitor_print` 可以接收 submit 返回的 `submission.json`，它会跟随其中的 `submission_record_path` 锁定并更新权威 ledger；也可直接传权威记录。如果超时前没得到 remote_job_id，返回 `RECONCILIATION_REQUIRED`，需要以后通过官方组件历史或幂等键查询补齐。当前模块没有伪造一个远端 ID，也没有实现未授权的历史查询。

## 实际检查与产物

运行环境：Python `3.12.3`，pytest；已有 Bambu Studio 位于 `/home/wujie/.local/opt/bambu-studio-2.8.2.61/`，本次重新核验并使用，没有安装新软件。

固定执行文件：`squashfs-root/bin/bambu-studio`；SHA-256 `d267ad0b4589c46cb18f49e2414f87a529b446dc992cb6ab8a728fe43f549eb8`。私有 locale/lib 环境只传给子进程，HOME/config/cache/datadir 使用打印模块隔离目录，不读取桌面账户配置。

真实测试配置：`Bambu Lab A1 0.4 nozzle` / `0.20mm Standard @BBL A1` / `Bambu PLA Basic @BBL A1` / `Textured PEI Plate`。源资源递归展开，没有改写工艺或温度。运行没有 DISPLAY/Wayland 会话。初次测试暴露绝对 export 文件名被 outputdir 重复拼接的问题，已改传 basename。无显示环境的 vendor thumbnail 生成失败不阻止切片文件导出，产物可能无预览图；不能依赖切片器缩略图替代网页 review 模型。

| 检查 | 实际结果 |
| --- | --- |
| 全部打印模块测试，启用已有真实 CLI | **45 passed**；包含本地真实切片测试 |
| 真实 CLI 输出 | 单板 `Metadata/plate_1.gcode`，ZIP CRC 通过，含两个对象；有效配置回读为 A1 / 0.4 / PLA / 所选 process 和床板 |
| 手动真实 CLI 产物 | `/tmp/justbuild-printing-20261024/real-cli/slice-fixed/enclosure.gcode.3mf`，35,837 bytes；该次 SHA-256 `121d10d3e0fb50647a5f228b3dcf25d7d2fb044cb8fa158a1aba1d1a68a981b8`；重新切片时间戳可能改变哈希 |
| 自动化真实 CLI 证据 | `/tmp/justbuild-printing-20261024/pytest-delivery/test_installed_bambu_a1_cli_wi0/slice_evidence.json` 及同目录 slice；已回读有效配置与实际生成文件 |
| 未通过/不完整验证、旧验证绑定、模型被改 | 拒绝准备，不生成可审核任务 |
| 审批 job/机器/哈希不匹配、过期、无认证 live action | 不调用 transport |
| 两 worker 共用 ledger、提交异常/超时、提交后崩溃 | 一次 attempt，后续不重发 |
| 旧遥测、不同远端任务、状态倒退、终态变化 | 拒绝更新权威记录 |
| capabilities CLI | 输出 `unsupported`，进程返回码 3；所有实时能力 `not_checked`，未进行账户探测 |
| A1 账户登录/云遥测/上传/文件接收/启动/实体完成 | **全部未做** |

重复运行（不启用真实 CLI 时，真实测试会 skip）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider \
  --basetemp=/tmp/justbuild-printing-own-run tests/printing
```

启用本机已核验版本的真实切片：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
JUSTBUILD_BAMBU_BINARY=/home/wujie/.local/opt/bambu-studio-2.8.2.61/squashfs-root/bin/bambu-studio \
JUSTBUILD_BBL_RESOURCES=/home/wujie/.local/opt/bambu-studio-2.8.2.61/squashfs-root/resources/profiles/BBL \
JUSTBUILD_BAMBU_LOCALE_DIR=/home/wujie/.local/opt/bambu-studio-2.8.2.61/locales \
pytest -q -p no:cacheprovider --basetemp=/tmp/justbuild-printing-own-real-run tests/printing
```

CLI 使用 `python -m justbuild.printing --help`。各命令必须指定调用方 output 目录；profile export 要求 fresh output。`prepare` 可选择 `--simulate` 或 `--slicer PATH --slicer-sha256 HASH --slicer-version 02.08.02.61`；`submit` 需要 `--print-job --approval --ledger-dir`；实时 submit 仍返回不支持，`--simulate` 明确选择 FakeTransport。返回码：ok=0、failed=1、needs_input=2、unsupported=3。输出和输入文件名重合时拒绝写入，调用方应选择独立工作目录。

## 依赖与主聊天所需集成

核心运行时只用 Python 3.11+ 标准库，Linux `fcntl.flock`；测试需要 pytest。真实切片是可选外部依赖：固定版本 Bambu Studio、其资源/动态库/locale。没有修改 pyproject、系统包、桌面软件或共享环境。完整配置展开包含官方模板 G-code，其版本、许可和分发条件由部署方随官方资源处理，当前未把资源复制进源码库。

主聊天集成点：

1. 接入 printing 自有请求字段，并在调用 verifier 前完成 printer/profile 选择；沿用 verifier 的嵌套 `inputs` 来源结构。不要将缺少 profile 的准备结果显示为已切片。
2. 在服务端创建已配置的 `BambuCliSlicer`，固定二进制版本/哈希和资源来源；当前默认四参数调用有意返回 unsupported。UI 展示真实/模拟、最终哈希、配置、检查缺项和下载产物。
3. 实现认证审核 action，生成绑定当前 manifest 和打印机的审批记录，注入 `approval_validator`。浏览器 JSON 里的布尔值不能充当实时授权。
4. 为官方组件/正式合作接口或云 VM 完成实际技术验证，再实现 Transport。不得把目前的接口骨架、官方 GUI 调用源码或模拟成功说成完成云接入。
5. 为并发 HTTP 操作提供独立 output，配置持久共享 ledger；跨机部署使用事务型幂等存储。将 `unknown/dispatching` 显示为需核对，不能提供自动重试。
6. 新 Transport 的 Observation 必须对应本次远端任务；异常和证据索引不携带凭据。分别接入上传、接收、启动、报告完成和实体完成证据。实体外壳装配/散热验收不由本模块宣称通过。

没有修改任何共享接口文件；以上新增字段/认证/部署要求在本文件提出，供主聊天纳入正式架构与 schema。


## 2026-10-24 后续：无 Studio 登录的 Developer LAN 兼容适配器

用户最新要求取消 Studio 登录/拖文件前提。此次新增 `src/justbuild/printing/lan.py`，并通过 `printing.__init__` 导出配对/探测/只读预检和 `A1LanTransport`。这是一条单独的 LAN 原型路线，不替换默认的 `UnsupportedCloudTransport`，也不把 LAN 成功包装为跨网络云提交。

### 官方证据和实际边界

重新读取的 [拓竹官方更新说明](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/) 明确区分：LAN 通过 Connect 不需要互联网/用户账号；X1、P1、A1、A1 Mini 可由用户手动开启 Developer Mode，开放 MQTT、FTP 等；同时明确这些通信协议不受官方支持。前者是账号依赖边界，后者是兼容性边界，不能称为厂商保证稳定的公开 API。完整原文研究副本在 `data/printer-integration/research/official-connect-update.txt`，本项目没有复制任何账号会话或签名密钥。

固定官方 [DeviceManager.cpp](https://github.com/bambulab/BambuStudio/blob/926a7192574bcb9b3a732e1ec59a46d79cb45466/src/slic3r/GUI/DeviceManager.cpp#L1270) 给出 `info.get_version`，约第 1350 行给出 `pushing.pushall` 的只读查询；约第 3576 行确认 `project_file` 回执按 `result` 处理。官方安装资源 `resources/profiles/BBL/machine/Bambu Lab A1.json` 给出 A1 的 `model_id=N2S`。消息字段与官方 `PrintParams`/回执交叉核对，但完整 Developer MQTT 发印 wire 格式没有被该文章承诺为稳定规范；目前固定 `project_file` 消息是待该 A1 固件实测的兼容实现。

**真实设备状态：** 旧候选 IP `172.20.10.10` 于本轮重做无凭据 TLS 探测，MQTT 8883 与 FTPS 990 都不可达；先前热点可达证据已过时。证据为 `data/printer-integration/tls-discovery.json`。用户私有 `~/.config/justbuild/printers/a1.json` 目前不存在，因此没有使用真实访问码、没有完成设备身份/固件/模式/耗材核对，没有上传文件、没有发布 `project_file`，更没有开始、移动或加热。

### 主控配对页 API

```python
from justbuild.printing import (
    probe_lan_tls, save_lan_profile, check_lan_connection, A1LanTransport,
)

probe_lan_tls(host, *, mqtt_port=8883, ftp_port=990, timeout=5.0) -> dict
save_lan_profile(values, *, path: Path | None = None) -> dict
check_lan_connection(*, profile_path: Path | None = None, timeout=15.0) -> dict
```

探测只执行 TLS 握手，返回每个服务的证书 SHA-256、TLS 版本、端口和 `trusted=False`，不发账号、访问码或打印命令，不自动写配置。首次配对是用户接受当前本地网络中该 IP/SN 和所见证书的信任建立，不伪装成已由公网 CA 或打印机屏幕核对了证书指纹；之后每次凭据连接都做严格 pin 比较。网页应在本机来源和既有 CSRF/会话边界内提供此能力，不把访问码发到对话或任何公开请求日志。

保存字段：`host`（实际 IP）、`serial`、隐藏输入的八位 `access_code`、`firmware`、`mqtt_port`、`ftp_port`、`mqtt_certificate_sha256`、`ftp_certificate_sha256`、`authorization_mode='developer_lan'`、`developer_mode_confirmed=True`。后两项只能由用户实际在机器上确认，不由程序切换或推断。缺固件/模式/pin 可以保存草稿，但返回 `needs_input`，预检也不会连接。完整配置以原子文件、0600、当前用户目录保存；API 只回传脱敏序列号、序列号散列、IP、模式/固件和配置存在性，绝不回传 access code。默认路径为用户目录中的 `~/.config/justbuild/printers/a1.json`，不在仓库、产物、模型提示中。

`check_lan_connection` 只做：证书 pin → MQTT 登录/订阅 → `info.get_version` / `pushing.pushall` → FTPS 登录/PBSZ/PROT/NOOP。要求非 retained 的新鲜版本与状态、SN 匹配、N2S/A1 型号匹配、固件与用户所填完全一致、状态 IDLE/FINISH；否则 `needs_input`。不会取访问码、改模式、刷固件、清错、改账号或发启动/运动/加热命令。成功预检只表示身份和只读访问通过，`dispatch_verified` 始终为 false，不是打印成功，也不证明装载材料/打印板已符合本任务。

### TLS 与依赖

依赖可选的 `paho-mqtt>=2.1,<3`，本机已装；FTPS 使用 Python 标准库。Paho 为单次连接、关闭自动重连，精确订阅 `device/SERIAL/report`。自签证书不使用系统 CA/主机名判断，而由明确配对的 SHA-256 认证；MQTT 的 `on_socket_open` 在 TLS 后、发送含访问码的 CONNECT 前核对 pin，FTP 也在 USER/PASS 前核对。FTPS 数据连接使用受保护 PROT P、控制会话复用并再次钉扎证书；没有明文或“不匹配则继续”回退。单元测试额外检查当前 Paho 的回调顺序，升级依赖必须重新通过此保证。

### 真实 Transport 实现与提交边界

```python
transport = A1LanTransport(
    profile_path,
    state_dir=private_persistent_state_directory,
    print_options={
        'bed_leveling': True, 'flow_calibration': False,
        'vibration_calibration': False, 'timelapse': False,
        'use_ams': False, 'bed_type': 'textured_plate',
    },
)
```

此配置里的选项必须由主控写进最终 `print_job` 的审核哈希，不能在审批后从网页任意更换。初版只支持外置耗材和纹理 PEI，AMS/其他板不隐式降级。`supported=True` 仅表示本机明确配置了可调用原型代码，并非已验证本台固件发印。主控只可经现有 `submit_print(..., transport=transport, approval_validator=..., ledger_dir=...)` 进入，不可把 `transport.submit()` 直接暴露为绕过审批的 HTTP 按钮。本次真实板的 `print_hold` 未触碰。

实际实现包含真实 socket、TLS、FTPS 和 MQTT 路径：提交前新鲜只读核对 → 持久写入尝试 → 唯一远端文件上传 → `SIZE` 和完整 `RETR` 回读 SHA-256 → 再核对空闲/身份/固件 → 持久写请求意图 → 固定 `project_file`。该函数没有任意 G-code、任意远程 URL、固件或账号命令。MQTT broker PUBACK 单独记证据，不能建立设备接收或开始；只有相同 sequence 的设备明确 `success/ok` 回执才报告 `start_acknowledged`，明确 fail 报失败。超时、不明字段、异常均保留 `unknown`，同幂等键不再上传或重复启动。

适配器私有状态只保存哈希、字节数、远端唯一文件名、固定选项和阶段证据，不保存访问码或完整设备序列号。上传、broker PUBACK、设备 ACK 的细粒度证据保存在该状态记录；当前核心 Observation 仍只表达最后一项观察，不能自动补造其他里程碑。远端文件名作为 LAN 任务相关标识，不伪装成云任务 ID。只读 `poll` 必须让设备报告的任务名/文件名与此唯一标识吻合，才报告 printing/paused/completed；别的旧任务或 retained 状态不能用于本任务。完成仍是设备报告，不是实体完成。

LAN 适配器运行主机必须能安全路由到打印机的私网服务。浏览器可以通过项目 HTTPS 链接访问服务，但普通网页链接和私网 IP 不会穿透 NAT；最终跨网无现场网关目标仍需另行完成正式云通道。本轮没有暴露打印机公网端口或改路由。

### 本轮检查

`tests/printing/test_lan.py`：**29 passed**，0.73 秒。使用本机协议路径 harness，不用真实凭据/设备：0600 与脱敏、缺固件/模式/pin、身份/型号/固件、只读查询与 NOOP、retained/忙碌状态、上传精确字节与回读散列、设备 ACK/序号与 broker ACK 区分、未知不重试、相关任务后才认状态、pin 在 FTP 密码和 MQTT CONNECT 前、固定 options/无任意命令。

此测试证明代码路径和门槛，不证明真实 A1 兼容、FTP/MQTT 已授权、文件已接收或开始。唯一真实网络检查是上述无凭据 TLS 探测，结果不可达。下一步是用户在本机配对页提供当前 IP/SN/访问码并确认实际固件/Developer LAN，先只读预检；具体模型版本解除 hold 与启动仍属于单独的明确审阅动作。


只读配置状态入口：`lan_profile_status(*, path: Path | None = None) -> dict`，返回 `configured`（是否有合法私有草稿）、`offline_ready`（本地必需项齐全）、`ready_prerequisites`、`serial_masked` 等；不发网络请求，不回传访问码/完整 SN，不把本地配置齐全解释为实际在线。`validate_lan_print_options(value) -> dict` 已公开，便于主控把固定六项写入审核 manifest，未知字段/AMS/非纹理板直接拒绝。

最新全 printing 回归：**73 passed、1 skipped**（真实 CLI 用例未显式启用，不重复切片）。本轮联网发现的实际主机网段已变化：电脑 `wlp1s0=10.17.36.149/24`、网关 `10.17.36.1`，不同于旧 `172.20.10.x` 热点。只读邻居快照和两次短时固定 Bambu SSDP/mDNS 观察未建立 A1 身份；最终排除本机自己发出的 M-SEARCH 后，6 秒窗口没有发现其他 Bambu 候选。记录为 `data/printer-integration/current-network-discovery.json`，不扫网段、不读 Studio 账户数据库、不认证或发印。无广告并不能证明设备不存在，可能在不同网络、隔离 AP 或广播被过滤。
# A1 read-only query QoS compatibility (2026-10-24)

`LanMQTT.publish(value, *, qos=1)` retains QoS 1 as the default. The existing
print-request call and its broker-acknowledgement evidence are unchanged.
`_read_status` explicitly uses QoS 0 for `info.get_version` and `pushing.pushall`.
There is no automatic downgrade, reconnect or resend after a QoS 1 timeout.
Only integers 0 and 1 are accepted.

The returned publication evidence distinguishes `local_send_completed` at
QoS 0 from `mqtt_broker_puback` at QoS 1. Both have
`establishes_printer_receipt=false`; a locally written packet is not a broker or
device acknowledgement. `_read_status.query_publications` retains these query
evidence records. A successful version query still requires a non-retained,
fresh `get_version` response matching its unpredictable sequence, matching
serial and the existing strict A1 product/model rule. Missing, stale, retained
or mismatched responses cannot establish readiness. Fresh printer state and
firmware checks remain required; a running printer cannot accept a new job.

`check_lan_connection` always initializes `connection_verified=false` and
`ready_for_new_print=false`. Only authenticated MQTT plus FTPS and matching
device identity/firmware establish `connection_verified=true`. A fresh
`IDLE`/`FINISH` state is additionally required for `ready_for_new_print=true`.
A busy `RUNNING` A1 retains `status=needs_input` and `LAN_NOT_IDLE`, while
reporting that its connection was verified. This distinction changes no submit
or idle-state gate and grants no actuation permission.

Integration observations supplied by the main agent: this A1 on firmware
`01.08.01.00` returned a matching-sequence QoS 0 version response, its expected
serial and `ota.product_name="Bambu Lab A1"`; fresh state was `RUNNING` at 91%.
The independent pinned FTPS login/NOOP returned 200. No artifact was uploaded
or print request sent. These observations support read-only compatibility,
not physical print-dispatch acceptance. Model authentication was not relaxed.

Verification: the combined LAN/controller suite **86 passed** in 2.46 s,
including 45 LAN cases. New tests use
real Paho 2.1.0 `MQTTMessageInfo` objects behind an in-memory FakeClient: missing
QoS 1 PUBACK fails without fallback/retry; fast PUBACK is retained; QoS 0 local
write completion requires independent fresh matching device responses; invalid
QoS values are rejected before publishing. Existing wire fixtures now accept
the explicit query QoS argument. A busy-state test verifies connected=true,
ready=false, retained LAN_NOT_IDLE and query/NOOP-only behavior.
No real socket, private printer profile,
access code, upload, print start or hold change was used by this worker.

# Review-bound LAN configuration snapshot (2026-10-24)

`A1LanTransport(..., expected_profile_sha256: str | None = None)` optionally
binds the entire private pairing file to the approved review ticket. The
controller must pass its saved pairing-file SHA-256 for both submit and monitor.
`load_lan_profile(..., expected_sha256=...)` checks the private file before and
after parsing, then returns a dictionary frozen from those validated bytes.
The transport uses that snapshot for the whole operation. A same-serial change
to IP, access code, firmware or certificate pins therefore cannot redirect an
already reviewed submission. Subsequent filesystem changes cannot mutate the
snapshot; new operations must revalidate it.

Verification: `tests/printing/test_lan.py` **33 passed** in 0.71 s, including
same-serial connection replacement before submit, mutation while parsing,
immutable returned snapshot, invalid hash fail-closed, and the existing mocked
wire-path checks. No network connection, real credential, upload, print start,
GUI launch or review-hold change was performed for this correction.



# Explicit single-PLA AMS Lite slot 0 (2026-10-26)

The original external-filament six-field print-option contract is unchanged.
A separately reviewed AMS request now accepts exactly those six fields with
`use_ams=true` and the additional `ams_mapping: [0]`. Other slots, multiple
filaments, implicit mapping, booleans/floats/string slot IDs and unknown fields
are rejected. `validate_lan_print_options` returns an independent mapping list.
No arbitrary control command or URL is exposed.

Official-source checks: BambuStudio
[SelectMachine.cpp, get_ams_mapping_result](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/SelectMachine.cpp#L1284)
serializes legacy tray indexes and the explicit integer `ams_id`/`slot_id`
mapping; [DevMapping.cpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/DeviceCore/DevMapping.cpp#L71)
computes normal unit/slot indexes as unit times four plus slot. For the supported
fixed unit 0, slot 0 request, `project_file` therefore includes
`ams_mapping: [0]` and `ams_mapping2: [{"ams_id": 0, "slot_id": 0}]`.
These master-branch sources were read on 2026-10-26; they are source evidence,
not a vendor promise that the compatibility wire protocol remains stable.

`_read_status` adds only redacted `ams_slot0` presence and PLA/unknown material.
Presence requires both AMS/tray existence bit 0 and a unique unit 0/slot 0
record in the same fresh status used by preflight; no RFID, serial or colour
is returned. Before upload and again before the start request, an AMS job
requires that fresh slot to report PLA. The exact in-memory 3MF bytes must
also declare exactly one PLA in both `project_settings.config` and first-plate
`slice_info.config`, filament id 1, filament map 1 and any layer list using
only index 0. Missing/contradictory metadata fails closed; no CAD or model
inspection was added. XML declarations/entities and oversized metadata are
rejected. The external-filament path retains its prior behavior.

Certificate pins, reviewed pairing hash, identity/model/firmware/idle gates,
byte-count and SHA-256 upload readback, durable attempt-before-upload,
precise device response sequence, persistent idempotency and unknown-outcome
no-retry behavior remain unchanged. MQTT QoS and its evidence semantics were
not changed. An AMS change after upload leaves an unknown attempt requiring
reconciliation and sends no start request.

Offline verification: `tests/printing/test_lan.py` **63 passed in 1.12 s** in
`/home/wujie/.cache/justbuild/venv/bin/python`, including strict mapping types,
legacy external compatibility, redaction, non-PLA/multi-material rejection,
missing/wrong unit/slot/existence rejection, start-once behavior and fresh
post-upload material change. The actual user-selected top_front.gcode.3mf
single-PLA metadata was accepted by a local read only. This worker performed
no printer network access, upload, print request, account/firmware change or
hold change. The main chat exclusively owns the user's authorized live submit
and must separately record actual upload/start/completion evidence.

Full printing offline regression for this change: **107 passed, 1 skipped in 5.30 s**. The opt-in actual CLI slicing test was skipped; no slice was rerun.


# Removable PCB carrier as a required third print model (2026-10-26)

`prepare_print` now recognizes the explicit confirmed mode
`constraints.display.support_structure = "removable_pcb_carrier"`. Only that
mode extends the required CAD artifacts with `pcb_carrier_step` and
`pcb_carrier_stl`, both declaring millimeters and passing the same local-file
and SHA-256 checks as base/lid. Missing artifacts, inaccessible/non-file or
invalid paths, malformed/mismatched hashes and wrong units block preparation
before the slicer is called. Without this explicit mode the historical
base/lid requirement and two-model record are unchanged.

For the carrier mode, the exact model order is `base_stl`, `lid_stl`,
`pcb_carrier_stl`. Each is copied to a separate operation snapshot with hash
readback, all three are passed to the injected slicer and all three are recorded
as exact file references and SHA-256 hashes in `print_job.inputs.models`.
`BambuCliSlicer` already forwards every supplied model to its CLI command and
checks their hashes after the process; no change to its generic model loop
was necessary. For real carrier slices, preparation also reads bounded
`Metadata/slice_info.config` and requires exactly one plate with all three
snapshot model names, with no skipped object. Missing/renamed/skipped/extra
objects return `SLICED_MODELS_MISSING`; absent coverage cannot be called a
successful real three-part slice.

`FakeSlicer` remains `simulation=true` with `simulation_slice` output. A fake
slice cannot be relabelled real while CAD/verification is simulated. Existing
verification verdict/provenance checks, exact printer/profile binding and
separate print approval/idempotency are unchanged. The integration owner must
extend its independent print-review model-set/hash validation to include the
same third model; this worker did not modify `print_control.py`. Real geometry,
screen/carrier insertion and complete assembly checks remain CAD/verification
responsibilities; a matching sliced object list is only model coverage evidence.

Targeted offline regression: `tests/printing/test_carrier_prepare.py`
**17 passed in 1.93 s**, covering third input snapshots/hashes, unchanged two-part
history, missing carrier STEP/STL, eight invalid reference/units variants,
simulation relabelling refusal, all-three arguments through the actual
`BambuCliSlicer` Python implementation, and missing/renamed/skipped object
rejection. The subprocess was mocked with clearly synthetic test archives:
this is interface and fail-closed coverage, not a new actual vendor slice or
physical acceptance result. Existing real CLI opt-in test remains separate.

No network access, printer discovery, upload, movement/heating/start request,
account/firmware change or hold change was performed. This design remains
held; prior top_front print authorization does not authorize it. Earlier AMS
Lite slot-0 behavior was preserved.

Full printing offline regression after the carrier change: **124 passed, 1 skipped in 7.04 s**; the opt-in actual vendor CLI test was skipped.
