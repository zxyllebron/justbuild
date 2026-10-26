# Main integration status

Updated 2026-10-24. Main is integrating the completed CAD, verification and printing-worker deliverables; other user-owned chats are idle after delivery.

- Main is implementing project packaging, orchestration, CLI/HTTP API and browser review.
- T9 filesystem does not support virtualenv symlinks. Main's dependency environment is `/home/wujie/.cache/justbuild/venv`; installing CadQuery, FastAPI, uvicorn and integration-test dependencies there.
- CAD, verification and printing workers retain their assigned ownership. Record integration changes in each module's documentation.
- The initial end-to-end example uses a clearly identified synthetic STEP board, not the user's pictured V821 board.
- Live printer submission remains outside implementation checks. A1 cloud transport feasibility must be reported separately.
- User update: the A1 and current computer are presently on the user's personal hotspot, available for initial connectivity verification. Final product is a multi-user link with each user's authorized printer across networks; hotspot/shared-LAN access is not the final architecture.

## First integrated release

- All three module implementations integrated, without editing their owned source paths.
- Real synthetic board produces STEP/STL/GLB; independent report has 36 pass, 0 fail, 6 explicit not_checked and 2 not_applicable checks.
- Main maps the reviewed CAD dialect into the verifier plan, preserves spatial contact regions and assembly stage inventories, and independently checks side/floor clearance regions.
- Existing Bambu Studio 02.08.02.61 used as a pinned, isolated, no-display slicer for the actual generated base/lid; no printer access performed.
- Full tests: 143 passed in 26.94 seconds, with one test-client deprecation warning.
- Local service: http://127.0.0.1:18765 . Reference job and artifact evidence: `docs/first-version-evidence.json`.
- Browser QA exercised complete example creation, GLB loading, transparent shell, exploded lid and section-plane interaction. Actual WebGL initialization failed in the in-app browser; the tested software viewer renders the real tessellated model and keeps all task actions usable.
- Runtime venv is `/home/wujie/.cache/justbuild/venv`, CAD pinned to 2.6.1; core installed editable. No desktop CAD was installed.
- Pending: genuine user STEP/plug requirements, model API and full model-driven planning integration, bounded automatic repair execution, production public multi-user deployment and A1 cloud dispatch/receipt/start/physical acceptance.

## Follow-up: hole review and explicitly authorized print

- User questioned the hole pattern and explicitly requested starting the current version using defaults. That authorization permits this specific live print once device authentication, actual device state and transmission are available; it does not establish any of those facts by itself.
- Independently read the reference STEP and base STEP check evidence: four PCB axes at (5,5), (75,5), (5,50), (75,50) mm have zero measured axis offset (0.001 mm tolerance). Four exterior lid-fastener holes are separate structural features.
- Browser now distinguishes P (PCB) and L (lid) rings, labels, hole view, source/coordinate/dimension table and measured axis offsets. Browser QA verified all eight rows. CAD and the authorized sliced artifact were not changed.
- Same-hotspot read-only TCP observation found one candidate printer. No authenticated identity, device idle/material readback, upload, start command or receipt occurred. Standalone official-component session probe did not establish a usable session and crashed during teardown; it is not a proven live adapter.
- User prefers discussing the LAN access-code route and says there is no desktop Studio installation in use. Existing Studio binary remains a background slicer. `scripts/configure_a1.py` provides hidden local credential entry outside the repo, with 0600 file permissions and no printer actions. Await connection profile and actual firmware/access-mode evidence.
- `docs/current-print-and-hole-review.json` records the current evidence. `docs/architecture.md` now separates the implemented local workflow from the intended public multi-user/cloud printing deployment and unimplemented automatic repair loop.


## 2026-10-24 当前整合完成

- 原始 V821、CAD、独立几何报告和真实 A1 切片不变；新增显示用无损GLB、事件驱动/像素缓冲渲染、交互预览与静止完整恢复。
- 05网页协同已完成简洁布局、图上外壳/板卡/孔距/开口尺寸；测量不修改实体。最终截图见dist/justbuild-v821-web-measurements.png。
- 无Studio登录依赖的Developer LAN兼容适配器、首次本机配对页、精确版本打印清单/提交/监控已接线。仅离线协议测试；当前私有配置不存在、当前网络未发现A1，不表示真机已经可用或跨网云端已通。
- 新intake接收STEP与独立用户要求并返回链接；仅新本机任务可一次领取owner。只读分享、旧版hold、未知提交不重试的边界保留。
- 整套最终699 passed、9 skipped、1 test-client弃用提示，61.60秒。无真实网络/打印操作；源/CAD/显示/切片与配对配置实际bytes均受审阅hash绑定。
- 主控统一构建源码/wheel并核对静态资源内容；本机service绑定127.0.0.1:18765，配对及LAN打印开关启用，但未配对不会启动设备。
- 详见docs/update-20261024-evidence.json。真实A1的授权、接收/启动/物理完成、云多用户部署仍待外部条件与验收。

## 2026-10-24 后续真实设备连接已通

A1已通过独立LAN MQTT/FTPS认证并核对型号、SN、01.08.01.00固件及双固定TLS证书，私有配置0600。只读查询QoS兼容修复后294 passed、1 skipped；设备现有任务RUNNING，连接已验证不等于可发新任务。新增connection_verified/ready_for_new_print，网页正确显示忙碌。只读取得状态，没有上传/启动新打印，没有中断原任务，真实V821 hold不变。最新脱敏证据docs/lan-connection-evidence-20261024.json。


## 2026-10-26 便携入口、复现边界与 LAN 复查

- 当前交付为 Python 包/CLI/HTTP API/网页，未打包 Skill；完整模型驱动规划与修复循环仍未集成。
- 新增 `doctor` 离线清单与 `self-test --out DIR` 真实合成板生成/独立验证命令。两个自检真实运行同语义指纹、四底座孔轴偏差0；独立必需检查38项（36 pass、2 not_applicable），另6项not_checked保留。不运行切片器、模型或网络/打印。
- 62项定向回归通过（6.46秒，1个TestClient弃用提示）。wheel已从独立临时目录载入并检查index/health及19项JS/CSS路由，配对资源在未启用时按预期隐藏。该检查复用现有依赖，不能冒充干净新机安装。
- 迁移指南 `docs/portable-use.md` 区分安装、同几何、同切片和实物使用验收。记录Linux x86_64/Python3.12.3/CQ2.6.1/OCP7.8.1.1.post1的现场清单，不声称完整锁定环境、Docker或跨机复现已验收。
- 本轮原始V821、CAD、切片、旧版hold和用户配对秘密保持原样。当前网络与设备状态以docs/lan-connection-evidence-20261026.json最新复查为准；历史10/24认证不是当前连通性证据。

用户恢复热点后只读复查已通过：电脑172.20.10.4，原配对A1 172.20.10.10，两项TLS固定证书、MQTT/FTPS认证、原型号/SN与01.08.01.00固件均匹配。当前FINISH，connection_verified和ready_for_new_print均为true；无上传/启动/参数改动，旧版hold未变。


## 2026-10-26 中文傻瓜式终端入口

- 新入口`justbuild`无参直接中文菜单；文件可拖进菜单，也可`justbuild board.step --requirements ...`。本机用户级快捷安装无同名覆盖，无需切目录/激活环境；源码根目录`./justbuild`可定位/首次安装headless运行环境。
- 新easy/easy_service负责友好输入、等待提示、审阅链接、已有设计、只读设备检查与合成自检；workspace endpoint严格loopback，自动复用实际任务目录或单次启动服务，原有约束确认、claim、hold和发印边界不变。
- 现有服务确认无active job后重载；真实PTY从home目录运行，菜单2能将完整V821列首项并输出正确链接。隔离真实后台进程只启动一次，再次调用复用，日志0600，测试结束仅清理自己捕获的子进程。小合成STEP经过新全局入口提交后可在正式审阅服务读取，仍未确认/生成/切片/打印。
- 定向回归240 passed（4.99秒），一项既有TestClient弃用提示。原V821 job.json逐字节不变；未连接设备或发起实际打印。
- docs/easy-use.md为用户步骤，docs/easy-evidence-20261026.json为新证据。通用AI接口规划/修复、干净新机安装、公网多用户、跨网云发印和首件物理验收仍待完成。


## 2026-10-27 源码仓库同步

- GitHub 已新建个人私有仓库 `https://github.com/zxyllebron/justbuild`，`main` 初始源码提交为 `0f29f38e315c3c2e5450377ebdd8adb62c2981a7`。通过远端分支回读确认提交；后续本节记录使用独立文档提交。
- 从共享目录建立独立源码快照，上传 203 个文件；包含源代码、文档、测试及明确合成的 CAD 证据。排除 `data/`、`dist/`、`build/`、本地环境、缓存、egg-info、日志和根目录切片临时 `result.json`。未修改 CAD、verification 或 printing 所有者的实现。
- 对快照运行现有 Python 全套：964 passed、9 skipped、1 项现有依赖弃用提示，106.36 秒；四个前端测试脚本共 43 项全部通过。未连接打印机，未运行真实切片/上传/打印。
- 扫描上传文件中的凭据签名，并只在内存中与本机真实打印机访问码、序列号和双证书指纹比对：均无匹配；私有配置未纳入快照。提交前逐文件 SHA-256 确认工作区与快照一致。
- 本次本地文档变更仅为 `tasks/integration-status.md` 本节；源码提交与检测日志保存在操作者本机独立发布目录，未在共享目录初始化 Git 或切换分支。
- GitLab：本机 `glab` 已验证两个站点的当前用户；两个站点均未检索到 justbuild 项目。已向用户询问目标仓库 URL，或新项目的站点及命名空间。目标未确定前未创建 GitLab 项目/分支、未向其他项目推送；后续需在指定代码库的新分支提交，并独立回读验证。
- 仓库同步和软件测试不代替跨网云发印、Windows 接收器、干净新机安装或实物装配验收。
