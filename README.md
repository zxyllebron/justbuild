# justbuild

开源、无桌面 CAD 依赖的 PCB 外壳生成框架。服务端读取 STEP/STP，提出机械约束候选；用户确认后，生成上下盖、独立检查实体与装配空间，并在浏览器审阅和下载。

当前版本：`0.1.0`。核心代码采用 MIT；内置 Three.js 保留其 MIT 声明。可选 Bambu Studio、其资源及网络组件分别遵循各自许可，没有打包进源码。

**交付形式是 Python 框架（CLI + HTTP API + 网页），当前未打包为 Skill。** 别人安装后可先用 `justbuild doctor` 检查依赖/资源，再用 `justbuild self-test --out ./selftest-001` 实际验证合成板 CAD 与独立检查链。迁移步骤、同效果的条件及未验收范围见 [便携使用与复现说明](docs/portable-use.md)。

## 第一版已跑通的内容

- STEP 实体清单、薄板与圆柱通孔候选，以及基于板边距离的接口外露候选。
- 明确的 PCB 实体选择、孔位、开口/封闭、插头包络与制造参数确认。
- 单块矩形 PCB、螺丝固定的底壳/平盖、PCB 支撑柱和独立盖板螺丝柱。
- 显式 `original_outline` 模式可保留真实等厚 PCB 的完整内外轮廓；对支撑环与原板做 BRep 核验，不能把未声明的小孔/槽抹平成矩形。真实 V821 输入与重新设计见 `docs/v821-redesign.md`。
- STEP/STL/GLB 导出及 SHA-256 绑定；STL 为单独放到打印床平面的零件。
- 独立实体有效性、空间受限的接触许可、碰撞、净距、真实孔基准、插拔与垂直装配路径检查；另检查侧壁与底板的实际 BRep 净距。
- 网页旋转、透明、剖切、爆炸、特征定位、报告与模型下载；新增 P（PCB 固定）/ L（上下盖锁合）孔位编号、俯视与坐标/孔轴偏差对照。无 WebGL 时使用实际网格的软件渲染兼容模式。
- CLI、HTTP API、磁盘任务记录、单进程顺序执行；浏览器会话隔离、只读分享能力及跨来源写操作限制。
- 功能未知的元件需分类或关联到已审阅命名组件；孔位、功能访问和避让范围分别确认，总确认不能直接抹掉未知项。
- 下载权限受限的桌面交接核心、独立只读 HTTP 服务及 Windows 自动下载/校验/打开接收器已有实现；这不等于打印机接收或自动启动。
- 已安装的官方 Bambu Studio CLI 可选择用于真实无显示切片，固定二进制哈希和完整 A1/喷嘴/耗材/工艺配置。切片与云下发分开。
- 可选的非交互 Codex/Claude 驱动函数，见 `src/justbuild/providers.py`；未在本次几何验收中调用真实模型服务。当前 STEP 候选由确定性几何规则生成，未宣称实现任意接口语义识别。

## 一条命令使用（中文向导）

这台电脑直接输入 `justbuild`，无需激活环境。菜单选择“1 新建外壳”后拖入 STEP、填写需求，即可得到待确认提案和审阅链接；选择“2 查看已有设计”可以直接查看真实 V821 结果。源码包用户在项目目录运行 `./justbuild`；缺少专用运行环境时会调用无界面依赖安装脚本。

向导自动复用/启动本机预览服务，支持无显示环境只输出链接。它不自动确认机械约束或启动打印。完整说明见 [傻瓜式终端入口](docs/easy-use.md)。

## 立即运行

项目声明 Python 3.11+；当前实际运行基线是 Linux x86_64 / Python 3.12.3，尚未完成干净新机/容器安装验收。在此工作机已创建运行环境 `/home/wujie/.cache/justbuild/venv`；源代码位于 T9 盘，运行环境位于支持符号链接的本地文件系统。

```bash
cd /media/wujie/T9/justbuild
./scripts/bootstrap.sh
./scripts/run.sh --port 18765
```

打开 <http://127.0.0.1:18765>，点击“运行合成板卡示例”。普通启动未配置切片器时，会生成并检查模型，切片阶段明确显示待配置。

合成参考 PCB 是 **80 × 55 × 1.6 mm**，含四个安装孔、一个外露的合成 USB 接口、芯片与不接线的预留排针。它不是用户图片中的 V821 板卡测量数据。该例仅用于软件流程和声明几何范围的验证。

纯命令行：

```bash
justbuild doctor
justbuild self-test --out ./selftest-001
justbuild demo --data-dir data/jobs
justbuild intake /absolute/path/board.step --requirements "保留按钮、卡槽和全部连接器操作空间"
justbuild inspect /absolute/path/board.step --out proposal.json
justbuild run /absolute/path/confirmed-request.json --data-dir data/jobs
justbuild capabilities
```

`inspect` 不会确认候选或启动打印。用户请求必须明确选 PCB 实体、孔位、接口和制造参数。可参考 CAD 模块的 `tests/cad/example_request.json` 以及网页导入后显示的提案。

## API

服务运行后，完整交互文档在 `/docs`。

| 操作 | 路由 |
| --- | --- |
| 能力与运行状态 | `GET /api/health` |
| 上传 STEP，得到待确认提案 | `POST /api/jobs`，multipart 字段 `board`，可选 `constraints` JSON 文本及用户独立 `requirements` 文本 |
| 运行合成示例 | `POST /api/demo` |
| 当前会话任务 | `GET /api/jobs` |
| 任务、提案、各阶段真实状态 | `GET /api/jobs/{job_id}` |
| 确认机械请求并运行 | `POST /api/jobs/{job_id}/confirm`，body 为完整已确认请求 |
| 下载已核对哈希的产物 | `GET /api/jobs/{job_id}/artifacts/{stage}/{index}` |
| 独立检查报告 | `GET /api/jobs/{job_id}/report` |
| 本机首次配对 | `GET /printer-setup`，默认关闭，仅允许回环地址；一次录入隐藏访问码与设备资料 |
| 准备打印审阅 | `POST /api/jobs/{job_id}/print/prepare`，仅 owner、本机且显式启用，绑定精确切片/设备/设置与风险 |
| 打印与查询 | `POST /api/jobs/{job_id}/print` 和 `/print/monitor`，LAN 兼容原型已接线；2026-10-24 已只读认证配对，实际上传/启动未验收；云仍不支持 |
| 桌面文件交接 | `POST /api/jobs/{job_id}/handoff`，需任务 owner、已核验切片与配置好的 HTTPS 交接地址；只创建文件读取能力，不打印 |

HTTP API 的创建与确认操作使用浏览器会话 cookie。HTTP 客户端需保留 cookie。只读分享不授予修改或发印权。CLI 创建的示例当前允许本机审阅，不能从陌生会话直接发印。

## 配置已安装的切片器

本机实际验证过的执行文件为 Bambu Studio `02.08.02.61`。此配置只控制本地无显示切片，不登录账户、不探测或操作打印机。

```bash
export JUSTBUILD_BAMBU_BINARY=/home/wujie/.local/opt/bambu-studio-2.8.2.61/squashfs-root/bin/bambu-studio
export JUSTBUILD_BAMBU_SHA256=d267ad0b4589c46cb18f49e2414f87a529b446dc992cb6ab8a728fe43f549eb8
export JUSTBUILD_BAMBU_VERSION=02.08.02.61
export JUSTBUILD_BBL_RESOURCES=/home/wujie/.local/opt/bambu-studio-2.8.2.61/squashfs-root/resources/profiles/BBL
export JUSTBUILD_BAMBU_LOCALE_DIR=/home/wujie/.local/opt/bambu-studio-2.8.2.61/locales
./scripts/run.sh --port 18765
```

配置后，合成示例自动选择 **A1 / 0.4 mm / PLA / 0.20 mm Standard / Textured PEI** 进行参考切片，并回读实际 3MF 中的 G-code、CRC 和有效配置。它不是对真实板卡材料或公差的推荐。真实板卡必须在确认请求里另行选择明确打印配置，且打印配置在几何验证之前固定，后改配置会使来源绑定失效。

## 第一版限制与下一阶段

- 默认模式仍严格要求矩形 PCB 减已声明安装孔；真实输入可显式选择 `original_outline`，保留等厚原板的全部孔槽，并证明安装点的承压环处于真实板材内。不支持的非等厚板、其他紧固方式和复杂插拔动作仍返回 `unsupported/needs_input`。至少需要三个非共线安装孔。
- STEP 丢失器件名称、缺元件、只含 PCB 基板或没有插头/线缆包络时，需要补充真实资料。几何规则不能证明接口电气用途。
- 壁厚完整分析、线缆弯曲、热、RF、强度与非线性装配仍为 `not_checked`。通过只针对报告中声明的几何范围；不代表实物、散热、螺丝强度或长期使用验收。
- 自动修复目前输出有界建议，尚未自动执行修复循环。不会自动降低净距或移动板孔以获得通过。
- Codex/Claude 目前是可调用的 CLI 驱动入口，模型 API 适配、跨格式语义理解和模型驱动的完整修复循环尚未完成集成。
- **A1 跨网络云端发送未实现或验收**。官方组件/合作接口或云 VM 是后续验证路线。没有用 LAN、模拟传输或登录成功替代云发印验收。
- **当前是本机 MVP 链接，不是已上线的公网多人自助打印服务**。多用户公网部署仍需 HTTPS、账户体系、配额、任务/存储隔离、各自打印机授权及云传输验收。本版单进程队列无跨机协调和自动重启恢复。
- Dockerfile 是部署起点，尚未构建验收；它包含 CAD 与网页服务，不包含官方切片器或云网络组件。

真实 V821 新版采用 543 个原始实体、21 个命名组件访问清单，新增半圆柱孔面严格合并、只从明确 PCB 实体建立孔基准、覆盖底部针脚的多个显式包络连续装配路径。每项检查记录实际执行范围；真实插头、按钮/锁扣行程、配套线缆与外设不在静态 STEP 中时仍为未验证。请以 `docs/v821-redesign.md` 和任务结果中的当前状态为准，旧合成示例不是 V821 适配件。

## 桌面自动接收的集成入口

```bash
justbuild handoff-serve --host 127.0.0.1 --port 18766
justbuild handoff-create JOB_ID --public-origin https://your-handoff-host.example
```

交接服务只暴露指定 3MF 的到期读取链接，不暴露主 CAD API 或目录。反向代理应使用 HTTPS 并关闭或脱敏 capability 路径访问日志。主 API 可通过 `JUSTBUILD_HANDOFF_PUBLIC_ORIGIN` 指向同一交接服务，不能从浏览器请求中自由指定文件或服务器地址。

Windows 文件接收器见 `scripts/windows-receiver.ps1` 和 `docs/windows-receiver.md`。下载/校验/进程启动分别留痕，未知启动结果不盲重启；本机 Linux 的包检查不能冒充 Windows 实机运行验收。原生 Bambu Studio URL 导入可能弹出第三方来源确认，这一限制保留，不绕过。真实无人值守发印仍需官方执行器与打印机状态/启动回执。

Linux 已有 `justbuild receive HANDOFF_URL --allowed-origin https://your-handoff-host.example`：默认仅下载/校验，显式 `--open-studio --launcher /absolute/launch.sh` 才把本地文件交给官方 `--single-instance` 入口。它不读取账号凭据或发送打印命令，真实 Studio 导入尚未实机验收；详见 `docs/linux-receiver.md`。Windows 一次安装器和 `justbuild://` 接收页按钮也已提供，见 `docs/windows-receiver-install.md`。

Linux一次用户级协议安装入口为 `python -m justbuild.receiver_install install --allowed-origin HTTPS_ORIGIN --launcher ABS_EXEC`，随后 `justbuild://` 链接可进入固定接收器；`uninstall`只移本模块拥有的注册和配置，保留下载缓存。实现与隔离测试已完成，本轮没有实际改动用户的MIME注册。详见 `docs/linux-receiver-install.md`。

交接模块的实现检查没有执行打印机运动、加热、发印、固件或账户修改。后续 LAN 只读认证证据单独记录，不能替代打印启动验收。

## 验证与模块证据

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider
```

真实 CLI 切片测试需显式设置上面的 binary/resources/locale 环境变量。旧合成版全套143项通过包含真实CLI测试。真实板本轮实际生成、独立核验与切片结果另见 `docs/v821-release-evidence.json`；软件回归和Windows运行跳过项也分别留痕，不与真实几何检查计数混用。

2026-10-24此前一轮软件回归：**359 passed、9 skipped**（48.69秒）；跳过8项Windows运行条件和1项未显式配置的真实CLI测试。本轮另已独立实际执行真实V821的官方CLI切片并验明非模拟，不使用跳过测试代替该结果。Linux/Windows协议注册、Studio实际导入和打印启动未执行。

- `docs/first-version-evidence.json`：本次合成外壳、实际检查与切片的交付证据。
- `docs/v821-release-evidence.json`：真实V821的声明几何674通过/0失败/11未检查、实际非模拟A1切片与新文件散列。
- `docs/cad.md`：生成模块输入、坐标、实体证据与支持边界。
- `docs/verification.md`：独立检查方法、范围、反例和修复建议约束。
- `docs/printing.md`：真实 CLI 切片、认证/幂等合同、云能力验证边界。
- `docs/architecture.md`：模块位置参数接口与阶段分离。



## 本轮新增：不登录 Studio 的 LAN 原型

无需 Studio 界面登录即可使用独立的 Developer-LAN 兼容适配器。安装可选依赖 `pip install -e '.[lan]'`。本机服务可显式启用：

```bash
JUSTBUILD_ENABLE_LOCAL_PRINTER_SETUP=1 JUSTBUILD_ENABLE_LAN_PRINTING=1 justbuild serve --host 127.0.0.1 --port 18765
justbuild printer status
justbuild printer probe PRINTER_PRIVATE_IP
justbuild printer check
```

打开本机 `/printer-setup` 录入 IP/SN/固件/访问码并固定 TLS 指纹；首次发现不证明厂商身份，今后指纹变化拒绝发送凭据。账号、Studio 会话和桌面 GUI 不是这个适配器的依赖。上述 CLI 只探测或只读检查；打印须精确版本授权，已有 hold 和未知提交不会自动重试。协议原型测试不代表当前打印机已接收/启动，更不代表云跨网打印已通。

2026-10-24后续曾通过当时的个人热点直接认证用户A1：MQTT与FTPS均通过、原设备SN和01.08.01.00固件/型号匹配、双TLS证书固定通过，配对资料保存在用户目录0600文件。只读检查读到现有任务RUNNING；连接已验证与是否可开始新打印分开显示。未上传文件或发送新打印指令。真实首个上传/启动、物理完成与云跨网仍未验收。最新证据见docs/lan-connection-evidence-20261024.json。`docs/printer-setup.md`、`docs/print-control.md`、`docs/printing.md` 记录协议与边界。

浏览模型采用不丢三角面的合并（真实 V821 43.4 MB → 10.7 MB），事件驱动重绘，交互预览在停下时恢复完整网格；原始 STEP 和切片不变。网页简化与测量叠加已由网页协同对话集成；尺寸来自实体几何与孔位约束，主控统一回归与打包。

本机 `intake` 输出的 `local_owner_url` 是一次性管理链接（不要当分享链接转发）；打开后绑定本机会话并可继续确认约束，fragment 随即清除。普通 `review_url` 是查看用途。远程 base URL 使用已认证上传 API，不签发本机领取链接。领取不自动生成或打印，旧版 hold 不变。

本轮整套最终软件回归：**699 passed、9 skipped**（61.60秒，1项Starlette test-client弃用提示）。跳过8项Windows运行条件和1项未显式配置的真实CLI测试；真实V821非模拟切片此前已单独执行，不能用跳过项代替设备接收/启动或物理打印验收。新增软件协议测试未连接实际打印机。

真实连接兼容修复后定向回归：294 passed、1 skipped（8.53秒），覆盖printing、控制层、HTTP发印边界和配对接口。查询显式使用QoS0并等待真实匹配响应；打印请求仍维持原有单次提交/设备ACK/未知不重试门禁。


## 2026-10-26 便携诊断与离线真实自检

- `justbuild doctor`：仅检查核心包元数据、网页资源、可选切片配置和驱动命令存在性。不联网，不读配对凭据，不执行切片器或模型；核心状态与可选能力分开。
- `justbuild self-test --out NEW_OR_EMPTY_DIR`：实际生成 80×55×1.6 mm 合成板和上下盖，运行独立检查，再从导出 STEP 重读实体、尺寸、体积与四个孔轴；不运行切片器、模型或打印，不覆盖旧目录。语义几何指纹用于同样例对比，不是完整形状同一性证明。
- 当前是本机框架验收；任意新板的接口语义、跨机安装、完整 AI 自动设计闭环、云跨网发印和物理装配均不能由自检通过直接推出。
- 使用方法与复现条件：[docs/portable-use.md](docs/portable-use.md)；诊断范围：[docs/doctor.md](docs/doctor.md)；合成样例基线：[docs/selfcheck.md](docs/selfcheck.md)。本轮复现结果另见 `docs/portability-evidence-20261026.json`。
