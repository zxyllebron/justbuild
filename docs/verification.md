# 03 几何检查：实现与集成交接

本模块只写入 verification 所有者目录。实现独立读取导入 PCB 与生成 base/lid 的 STEP，不依赖 CAD 工作者的碰撞结论；不修改 CAD，不执行打印。

## 入口与文件

```python
from pathlib import Path
from justbuild.verification import verify

report_path = verify(Path("request.json"), Path("cad_result.json"), Path("caller-output"))
```

```bash
PYTHONPATH=src /tmp/justbuild-03-venv/bin/python -m justbuild.verification request.json cad_result.json --output-dir caller-output
```

CLI 输出报告绝对路径，退出码：`0` 声明范围通过、`1` 有确定失败、`2` 不完整或不支持。Python 入口返回报告路径，检查失败写入报告。输出目录不可写等文件系统故障可能仍抛出异常。

精确文件清单：

- `src/justbuild/verification/__init__.py`：延迟加载依赖的公共 `verify`。
- `src/justbuild/verification/__main__.py`：无界面 CLI。
- `src/justbuild/verification/engine.py`：输入验证、检查调度、问题、修复建议、JSON 报告。
- `src/justbuild/verification/geometry.py`：CadQuery/OpenCascade STEP 实体操作。
- `tests/verification/conftest.py`：临时目录内合成实体和完整请求。
- `tests/verification/test_geometry.py`：几何、依赖缺失、清单与证据用例。
- `tests/verification/test_input.py`：无 CAD 依赖也可运行的输入/输出边界用例。
- `docs/verification.md`：本说明及集成契约建议。

未编辑共享架构、任务、schema、依赖文件、CAD、打印或 web 目录；未初始化 Git 或提交其他所有者文件。

## 实际检查方法

| 检查 | 已实现的方法 | 通过结论的范围 |
| --- | --- | --- |
| 来源与身份 | job/schema/status、STEP 路径及 SHA-256、前后读回 | 检查结果绑定实际输入字节，base/lid 哈希必须与 CAD 清单一致 |
| 实体有效性 | OCC B-rep 有效性、所有 solid 正体积、拒绝混入孤立面/边 | 输入由有效实体构成；不是网格视觉检查 |
| 碰撞/接触 | 逐实体精确 Common 体积和上界 + B-rep 最小距离 | board/base/lid 三对材料重叠或未声明接触；多组件交集和不冒充融合并集体积 |
| 净距 | OCC 最短距离及最近点坐标，与明确阈值比较 | 整个零件对的全局最小距离；不从材质推断安全间隙 |
| 孔位 | PCB 真实圆柱内孔面建立基准；生成件内孔面测量轴线、直径、轴向区间；完整圆柱空腔探针 | 已声明圆柱孔的对齐、尺寸、通孔/盲孔障碍；不能只凭空区域冒充孔 |
| 必需开口 | 明确的开口实体包络与 base/lid 做交集及距离 | 已确认开口包络为空且满足附加间隙 |
| 插拔 | 插头矩形包络的整段连续平移扫掠，与全部在场零件检查 | 给定方向、给定包络与路径范围；不是离散端点采样 |
| 装配/拆卸 | PCB、lid 均需路径；先检查实际移动实体被包络包含，再检查整段扫掠 | 声明装配阶段下的刚性单轴直线路径 |
| 维护禁入区 | 已确认矩形 keepout 的实体交集与最小距离 | base/lid 不侵入指定服务空间；可额外指定 board 为障碍 |

`board` 可以是含元件的 STEP compound；其全部实体与壳体一起检查。当前不提取组件命名树，不单独检查 PCB 装配内部元件之间的相互干涉。

有意接触仅支持 `touch_only`。必须声明零件对、原因、来源和有限的接触区域。任何超过体积容差的材料重叠始终失败。区域从双方实体分别扣除，剩余实体与对方原实体仍需分离，防止一个支撑点的例外遮蔽别处意外接触。区域要留有限缓冲；边界处的接触保守判为失败。接触例外不覆盖任何明确的最小净距要求。

## 数值与坐标约定

全部坐标和距离为 mm，体积为 mm³。默认数值容差 `tolerance_mm=1e-5`、`volume_tolerance_mm3=1e-7`，属于计算政策，不是打印/配合间隙。调用者可明确提供更小或适度更大的容差；上限分别为 `0.001 mm`、`0.001 mm³`，更大返回不完整。净距满足 `measured + tolerance >= required`；材料交集超过体积容差失败。报告逐项记录容差及原始测量值，不声称数学上的精确算术。

STEP 读取器明确设置目标长度单位为 mm；请求和 CAD artifact 的 `units` 必须为 `mm`。最终只比较已置于共同确认板坐标系中的几何。`constraints.board_frame` 必须是右手正交坐标架；调用者声明 `verification.geometry_frame="confirmed_board_frame"`。它表示所有生成 STEP、孔位和包络都已采用这一坐标系。可提供 `verification.board_transform`，将原始板 STEP 放到该系中；缺省单位矩阵是这一明确声明的含义。该矩阵只允许刚性变换，禁止缩放、镜像和剪切。

模块不根据 `board_frame` 猜测源 STEP 是否已变换。源板字节哈希、实际应用矩阵和 frame 会进入证据；CAD 工作者与主集成必须确认相同变换，避免把板坐标重复转换。

## 需要主集成的字段建议

以下是 03 模块私有的附加输入，不擅自改变共享 schema。缺少字段会得到 `needs_input/incomplete`，请主聊天在提案确认与 CAD 元数据对接后集成。

```json
{
  "verification": {
    "geometry_frame": "confirmed_board_frame",
    "tolerance_mm": 0.00001,
    "volume_tolerance_mm3": 0.0000001,
    "coverage": {
      "board_geometry": {"complete": true, "provenance": "example: confirmed STEP assembly inventory"},
      "intentional_contacts": {"complete": true, "provenance": "example: confirmed contact inventory"},
      "clearances": {"complete": true, "provenance": "example: confirmed clearance requirements"},
      "mounting_holes": {"complete": true, "provenance": "example: confirmed mounting-hole inventory"},
      "ports": {"complete": true, "provenance": "example: confirmed connector usage inventory"},
      "keepouts": {"complete": true, "provenance": "example: confirmed service-space inventory"},
      "assembly_paths": {"complete": true, "provenance": "example: confirmed assembly sequence"}
    },
    "clearances": [
      {"parts": ["board", "base"], "minimum_mm": 0.5, "provenance": "example only: confirmed mechanical requirement"}
    ],
    "intentional_contacts": [],
    "mounting_checks": {},
    "port_checks": {},
    "assembly_checks": [],
    "required_checks": []
  }
}
```

示例数值仅演示字段，不能作为实际设计默认值。`coverage` 是带来源的输入完整性声明，模块无法从 STEP 推断是否漏导了某个元件。主集成不能自动填充 `complete=true`。空 mounting/ports/keepouts/clearance/contact 清单只有对应完整性声明后才代表有意为空；没有声明依然阻断通过。`single_pcb_screw_lid` 结构始终要求 PCB 与 lid 两条路径，空装配路径清单不能通过。

CAD 结果遵循架构约定，必须含唯一 `base_step`、`lid_step`，每项含 `kind/path/sha256/units`。路径相对于 CAD 结果 JSON；`board.step_path` 相对于请求 JSON。允许绝对路径。参考报告中的 `checked_geometry` 可核对实际几何哈希；不能使用 STL/GLB 代替 STEP。

### 孔位数据

`constraints.mounting_holes[]` 每项：`id`、`center_mm`、单位向量 `axis`、`diameter_mm`、`provenance`。可提供对导入板基准测量的 `position_tolerance_mm`、`diameter_tolerance_mm`；缺省只容许上述数值容差，不推断制造公差。板的实际圆柱内孔面必须能与此基准匹配。

真实多实体 STEP 必须通过 `board.metadata.pcb_solid_index` 明确选择 PCB，并用 `pcb_selection_source` 记录选择证据。源文件只有一个 solid 时兼容旧输入自动选 0；多 solid 缺少有效选择则安装孔范围 `not_checked/incomplete`。板孔 datum 仅从该实际 PCB solid 测量，不能用另一个连接器自身的同轴圆孔冒充 PCB 孔；报告逐孔记录选择索引、来源和完整源 STEP SHA-256。源装配的全部其他 solid 仍进入有效性、碰撞、净距与装配检查，不因孔位选择而删减。

`verification.mounting_checks` 以 hole id 为键，每个孔至少一个壳体目标：

```json
{
  "h1": [{
    "part_id": "base",
    "span_mm": [-3, 0],
    "minimum_diameter_mm": 2.8,
    "maximum_diameter_mm": 3.1,
    "position_tolerance_mm": 0.1,
    "angle_tolerance_deg": 0.1,
    "provenance": "example only: confirmed fastener requirement"
  }]
}
```

`span_mm` 是从板孔 center 沿 axis 的有符号偏移，不能猜壳体厚度。`maximum_diameter_mm` 可省略；最小直径、位置与角度阈值必须明确。完整探针位于板的固定基准轴上，因此“孔轴偏差在容许范围内”仍不能豁免螺杆通道被挡。

要求解析圆柱面覆盖完整周向与单个检查区间。2026-10-24 真实 V821 导出使用两个半圆柱面表达每个完整孔，现已增加严格合并：只组合数值容差内同轴、同径、同轴向跨度的圆柱面，将各面参数角度转到共同物理轴向后计算角区间并集；并集必须覆盖完整 2π。重复同半圆不能靠角度求和冒充整圆，不同高度的互补半圆也不能合并。报告保留 `merged_face_count/face_indices/angular_coverage_rad`，区间取各面共同交集而不扩展。缺角/不能证明完整周向的拓扑仍返回 `UNSUPPORTED_BORE_TOPOLOGY/not_checked`。生成孔仍额外执行完整圆柱空腔探针。沉孔/台阶孔可由多个明确区间分别声明，但不实现螺纹啮合、螺钉头支撑、孔边强度或自动孔分类。

### 开口、插拔与 keepout

统一矩形包络：

```json
{"kind":"box","min_mm":[-1,4,3.5],"max_mm":[2,6,4.5],"provenance":"example: confirmed plug or opening drawing"}
```

`constraints.ports[]` 使用 `id/access_mode/insertion_direction`。`insertion_direction` 是插入方向的单位向量。`external/maintenance` 需要开口与插拔检查；`internal` 不要求外壳开口，仍需插拔路径；`covered` 是明确不使用的口，不要求开口或插拔。

`verification.port_checks[port_id]`：

- `opening_envelope`：所需开口空间；`against` 至少含 base/lid；`minimum_clearance_mm` 必填，可明确为 0。
- `insertion`：`envelope` 为连接状态的插头/手部空间，`translation_mm` 为连接状态向拔出状态的移动，必须与插入方向相反；还需 `against/minimum_clearance_mm/provenance`。
- 插拔路径必须覆盖 board/base/lid 所有在场障碍物。组件已包含在 board STEP 时也被检查。当前无“随意忽略插座实体”例外；如配对区与包络相交，应提供能区分真实配对区的上游几何/新的接口设计，再扩展本模块。

本模块检查确认后的包络，不从组件树自动推导插头尺寸，也不自动证明包络绑定了正确的连接器型号/位置；这些对应关系需要提案证据与用户确认。未提供包络时不会从 STEP 包围盒补出配合要求。

`constraints.keepouts[]` 使用 `id/purpose/provenance/geometry/against/minimum_clearance_mm`，其中 `geometry` 为上述 box。必须检查 base/lid，可额外包含 board。包络来源应包括实际维护工具/空间要求，不能把元件包围盒冒充已确认插拔尺寸。

### 装配阶段与连续路径

```json
{
  "id": "pcb-remove-after-lid",
  "moving_part": "board",
  "envelope": {"kind":"box","min_mm":[2,2,2],"max_mm":[8,8,3],"provenance":"example: confirmed PCB assembly envelope"},
  "translation_mm": [0,0,20],
  "against": ["base"],
  "absent_parts": ["lid"],
  "stage_provenance": "example: remove lid and fasteners before PCB extraction",
  "minimum_clearance_mm": 0.5,
  "provenance": "example only: confirmed rigid removal sequence"
}
```

此类条目放入 `verification.assembly_checks[]`，至少有 `moving_part=board` 和 `moving_part=lid`。`against` 与显式 `absent_parts` 必须恰好覆盖除移动件外的全部零件；缺少或悄悄省略障碍物返回不完整。插拔也支持 `absent_parts/stage_provenance` 描述取下 lid 后的维护阶段。

包络必须是显式矩形实体，移动向量只允许一个非零分量。用移动实体减去起始包络来验证包含，再构造整段平移的解析矩形并做 B-rep 检查。整个连续区间被覆盖，不会漏掉中途障碍。对复杂/中空零件，这种包络可能保守地报警；它是“声明的必需包络受阻”，不等于已证明精确零件不存在其他可行装配路径。旋转、斜向平移、多段规划或柔性变形返回 `not_checked`，不采样后冒充连续证明。

### 真实多元件板卡的多包络装配路径

2026-10-24 扩展只针对 `assembly_checks`：同一刚性单轴平移可以使用 1–5000 个显式 box：

```json
{
  "envelope": {
    "kind": "boxes",
    "provenance": "source STEP complete solid inventory",
    "boxes": [
      {"kind":"box", "min_mm":[0,0,0], "max_mm":[100,60,1.6], "provenance":"actual PCB bounds"},
      {"kind":"box", "min_mm":[15,57,-1.4], "max_mm":[18,60,10.1], "provenance":"actual lower and upper header bounds"}
    ]
  }
}
```

其余 `moving_part/translation_mm/against/absent_parts/stage_provenance/minimum_clearance_mm/provenance` 规则与单 box 相同。以上坐标只说明格式，不能用于真实 V821。调用者必须使用实际导入实体的 bounds，不能删掉下方焊针。

独立检查器重新读取全部真实移动 solid 的 OCC bounds，并要求每个 solid 的完整保守 bounds 被至少一个声明 box 包含；不接受盒子并集的总 bbox 冒充覆盖，也不以重叠盒 compound 的体积近似真实移动实体。此 containment 是界限包含证明，报告明确写明方法；它不是碰撞证明。声明 box 不会在检查器中自动扩大。与安装柱贴合的 PCB 底面应保持真实接触平面；不能为消除序列化误差而把 box 向安装柱内扩张，再把人工穿透忽略。

随后对每个 box 分别构造整段单轴平移的解析扫掠体，执行原有真实 B-rep 交集和距离检查；即使起终点均无碰撞，中途挡墙也会失败。逐 box 独立检查，不把箱体间重叠体积拿来作为通过证据。零距离接触还必须对应唯一的 `touch_only` 声明并完全位于已有有限 contact regions 中，正体积重叠从不豁免。报告保留 `box_index`、完整扫掠坐标及实际 source solid → containing box 映射。

分离的小 box 可以保留真实组件之间的间隙，避免把整板下方空域一律填满而误撞安装柱。若某一个真实元件自身的保守 box 仍与壳碰撞，结果保持失败；它不能证明不存在其他精确可行路径，也不会擅自缩小包络。旋转、斜向、柔性线缆和非直线规划仍未实现。原单 box 路径保持原有行为。

## 报告与修复约束

报告保留共同 envelope，并增加 `verdict/checks/repair_proposals/checked_geometry/inputs/backend/numerical_policy/check_summary/required_not_checked`。

每项检查具有 `id/category/status/required/method/tolerance_mm/volume_tolerance_mm3/measurements`，有特征 id 时附带 `feature_id`。问题含 code、severity、check_id、feature_id 和原始测量 evidence。最近点、孔轴位置与偏差、扫掠区间可供 web 定位风险。未输出虚构置信度。

- 确定失败优先得到 `failed/fail`；即便其他项目未完成，也保留 `required_not_checked`。
- 必需项目缺字段、未知范围、缺几何、未支持的几何/路径，得到 `needs_input/incomplete`。
- 缺可选几何依赖得到 `unsupported/incomplete`，不会用包围盒替代实体检查。
- 只有必需项目全部 `pass/not_applicable` 才得到 `ok/pass`。

修复建议不会修改几何或自动执行。建议冻结板位置、板孔基准、所需开口和净距；只允许在确认约束内调整 base/lid。净距建议记录最小增量；孔建议记录固定轴、区间和直径界限。建议标记需重检，最多三次由上游编排重试；壳体尺寸、最小壁厚和紧固支撑可行性尚须校验。不能证明界限可满足时要求新的需求确认，不可缩小间隙、删开口或移动板孔。

## 明确未完成的分析

壁厚、线缆弯曲、热、RF、强度、非直线装配均输出独立 `not_checked`。它们默认是当前范围外的提示，并非通过项；将名字放入 `verification.required_checks` 后，会直接阻断总 verdict。未知必需检查名字也阻断通过。

因此 `pass` 只表示这份来源明确的输入在声明几何范围内通过，不是热安全、强度、打印可靠性或实物装配验收。主集成应根据产品/材料/环境确认所需附加分析；切片必须拒绝 `incomplete/fail`，打印仍需独立的显式审核动作。

## 依赖及实际验证

使用独立环境 `/tmp/justbuild-03-venv`，未修改共享依赖文件或系统 Python。此次实际版本：Python 3.12.3、CadQuery 2.6.1、cadquery-ocp 7.8.1.1.post1、pytest 8.4.2。生产建议由主集成加入 optional verification extra：`cadquery==2.6.1`；测试加入 `pytest>=8,<9`。实际环境的传递依赖仍需主集成锁定。

实际执行：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/justbuild-03-venv/bin/python -m pytest tests/verification -q --basetemp=/tmp/justbuild-03-tests-release -p no:cacheprovider
```

测试生成的 STEP、请求、manifest、报告只位于所有者专用临时目录。覆盖实体分离但包围盒相交、真实交叠、净距阈值与容差、有意接触的空间边界、板真实孔基准、孔偏移/过小/过大/盲孔、空位置伪孔、开口堵塞、插拔整段障碍、PCB/lid 包络包含与装配阶段、keepout、缺少参数、哈希/身份/单位/文件错误、变更中输入、内核故障及依赖不可用。

2026-10-24 最终结果：**53 passed in 5.99s**。另用无 CadQuery 的系统 Python 运行 `test_input.py`：**6 passed in 0.01s**。测试加强期间曾发现合成板孔与板边相切造成圆柱面拆分；已明确区分该不支持拓扑，并用独立用例要求 `incomplete`，而不是宣称已验证完整孔。

实际 CLI 另执行六组拷贝后的 STEP/请求：正常孔位与正常开口返回 `0/pass`，实体碰撞、净距不足、中途路径受阻返回 `1/fail`，缺完整性声明返回 `2/incomplete`。再用真正缺 CAD 后端的 `/usr/bin/python3` 调用 CLI，得到 `2/unsupported/incomplete`。

证据在 `/tmp/justbuild-03-evidence-20261024/evidence_summary.json`；每个场景保留原始 STEP、request、CAD manifest 和重新执行的报告。例：`pass_mounting/out/verification.json`、`pass_connector/out/verification.json`、`collision_failure/out/verification.json`、`clearance_failure/out/verification.json`、`midpath_obstacle/out/verification.json`、`incomplete/out/verification.json`，以及 `backend_unavailable/verification.json`。临时证据目录不作为源码依赖。

所有合成用例只验证代码行为；真实输入情况见下方 2026-10-24 V821 独立审计。已确认插头尺寸与物理装配测量仍缺失。

## 2026-10-24 真实 V821 STEP 独立输入审计

实际源文件 `/home/wujie/下载/3D_V821L2WBX_1_2026-10-24.step`，SHA-256 为 `2b096e5a608d7089dbc6062cfb4fd410af0d810e17436928e0bbb47b45dd51cc`。通过本模块的 `STEPControl_Reader` 重新独立读取，目标单位明确为 mm；没有复制合成板尺寸，也没有删除真实实体、过孔或槽。审计脚本和报告位于 `data/v821-redesign/verification-analysis/`，它们是输入审计证据，不是新外壳的放行报告。

- 真实输入含 543 个 solid。递归叶均为 Solid，没有游离面或游离壳。总计 544 个 shell 的额外壳属于第 541 号 solid 的内部壳，不能把它当多余几何删除。
- 最大薄 XY 实体候选为 solid 0，独立 `isValid` 与正体积检查通过。PCB bounds：X `[0.01270002540005, 99.987500635001]`、Y `[0.01270002540004639, 59.987419354839]`、Z `[0, 1.586156972314]` mm。它不是精确 100×60 矩形减四孔；原始小孔、长槽与板边必须保留。
- 全装配 bounds：`[-1.4267771437920067, 0.01270002540004639, -1.4138398365462443]` 至 `[99.987500635001, 63.27652026456601, 10.08616036345312]` mm。最高元件超出 PCB 顶面约 `8.5000033911 mm`，底部焊针低于 PCB 底面约 `1.4138398365 mm`。必须以整个装配而非裸 PCB 确定上下避让。
- 四角安装孔候选直径均为 `2.00000508001 mm`，真实 XY 为 `(1.999654559309,57.999822199644)`、`(1.99967995936,2.000454660909)`、`(97.99982981966,2.000185420371)`、`(98.000411480823,57.999723139446)`。每个孔在源 STEP 中分成两个 π 半圆柱面，Z 覆盖真实整板厚。
- 孔轴到最近板边约 `1.987 mm`；完整接触环若按原板材料承压，外径必须小于约 `3.973 mm` 并由 CAD 对原始 PCB 做实体证明。旧合成板的 Ø7.5 支柱与 M2.5 导孔不能复用。新的 M1.6/Ø3.8 支柱配置属于明确的原型制造选择，孔边强度、螺纹啮合和实物可靠性未由几何检查证明。

真实输入促成的检查器扩展均不放松约束：完整环的解析角度并集合并；安装孔 datum 绑定明确选择的 PCB solid，而非全装配的其他连接器孔；孔面材料分类先用 OCC 含公差的保守包围盒树筛掉不可能覆盖采样点的实体，再对每个候选执行原有精确 `isInside`；多 box 的连续装配路径与每源 solid 独立覆盖证明。broad phase 仅减少无关调用，不用包围盒或渲染代替实体碰撞核验。圆柱测量按同一 shape/数值容差弱引用缓存，四个安装孔只需遍历真实 PCB 面，其他组件仍完整保留在碰撞/净距/装配检查中。

扩展后实际执行 `PYTHONPATH=src /home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/verification -q`：**58 passed in 6.38s**。新增用例验证真实 OCC 拆面后的完整孔通过，重复同半圆、不同高度互补半圆、真正缺角孔不能通过，反向基准轴正确，其他实体占据孔心不能被 broad phase 遗漏。真实源完整有效性/四孔 datum 的运行记录另见 `checkability.json`；新壳尚未生成时，壳体碰撞、净距与装配路径仍保持未检查，不能用输入审计替代它们。

加入显式 PCB 选择和多 box 路径后最终本轮测试：**66 passed in 8.52s**。新增反例覆盖非 PCB 部件孔冒充 datum、多实体缺 PCB 选择、遗漏下方焊针、总 bbox 假覆盖、中途扫掠障碍和接触区域；实际分离组件的两个 box 可绕开中央柱，而保守总 box 会被明确挡住。真实 STEP 整装配有效性已重新独立运行通过，约 62.35 秒；新外壳的真实完整核验需等 CAD 产物写完后再执行，不能由这些代码测试替代。

### 大装配 Boolean 的保守分解

CAD 首次真实生成在复杂 compound 的 Common 阶段报告 `Null TopoDS_Shape`。独立实验 `common-experiment.json` 使用绑定原始输入的只读 BREP 缓存和当前修订的 USB1-left 原型服务包络：本次旧 compound Common 成功得到 0，耗时约 16.78 秒，**此样本没有复现原失败**，不据此把原失败改成通过。新方法首次构建独立实体 bounds 并筛选约 20.14 秒，543 个 source/box 对均由分离的保守 bounds 排除，实际另测 B-rep 最小距离约 `8.9e-15 mm`（按数值容差视为接触）。缓存实验不替代新外壳正式核验时重读真实 STEP。

检查器现对 Common 使用逐原 solid pair 策略：只有保守 bounds 严格分离的对可以排除；其余逐对运行真实 OCC Common，检查 `IsDone`、非 Null、有效 B-rep 和有限体积。操作采用 non-destructive 模式，不修改被测输入。每个实际候选对的原 solid 索引、Common 体积、排除数和执行数进入 `intersection_evidence`。任何 Null 或未完成操作仍返回未知/不完整，绝不解释为 0。

多组件可能彼此重叠，逐对 Common 的体积和因此是融合并集交叠体积的上界；报告明示 `sum_of_per_solid_intersections_upper_bound_on_union_volume`。和为 0 才能证明没有体积穿透；非 0 不声称是准确的并集交叠体积。按数值体积阈值判定仍保持保守，可能拒绝而不会把真实超过阈值的穿透改成通过。Cut、接触区域扣除也按原 solid 分解并逐次核验，保留全部源几何，避免先把 543 组件混成一次复杂 Boolean。

增加上述 Boolean 反例后：**71 passed in 7.24s**。测试包括重叠组件交集和为 16 而实际并集为 12（只能标上界）、AABB 分离才能跳过 Common、AABB 重叠但中空材料不相交仍必须运行 Common，以及 Common/Cut 返回 Null 时不得得到 0。

### 首次真实新壳正式独立核验

通过公开 CLI（内部调用公开 `verify`）执行一次完整核验，没有重复运行或删减原源。冻结请求 SHA-256：`636451e11bedbd0cfdc3e6813a924db66b676775db34804ee93ecd09ceeb49f2`；成功 CAD 清单为 `data/jobs/019d8f0d6d92426aa96618210deb469a/generating/cad-preserved-787nmihw/cad_result.json`。输入板 STEP 保持原始 62,527,894 字节，全部 543 solid；生成 base SHA-256 `43b6a734e4aaa97f9293a0beb8edd4b2ad0e27a772390a1dbb4cb0638a98a54c`，lid SHA-256 `9654475fe31219284a462d39cd9b3663e3feaea2841e1e1ad4c3d3e2ce772077`。

原始独立报告：`data/jobs/019d8f0d6d92426aa96618210deb469a/verifying/independent-original-step-01/verification.json`，SHA-256 `6c6e2efe383a33d50bff58433d201aab041f7e7062e00c320c99f2ca03d333ab`。结果 `ok/pass`：**671 pass、0 fail、6 not_checked、3 not_applicable，required_not_checked=[]**。四个真实 PCB datum 和 543 个独立 box 连续扫掠均通过；包络覆盖映射独立包含全部 543 source solid。数值长度容差采用冻结请求明确的 `0.001 mm`，体积容差 `1e-7 mm³`，未放宽。

六项未执行分析仍为 cable_bend、nonlinear_assembly、rf、strength、thermal、wall_thickness。该结果只证明声明的静态实体/净距/孔位/原型访问包络与刚性单轴装配范围，不表示真实插头、按钮行程、锁扣、线缆、热/RF/声学、强度或打印/实物装配验收通过。主控的功能访问与底板/侧壁补充保存在另一份 `with-functional-scope.json`，原始独立报告未被覆盖。没有启动打印。

原报告写入时间为 2026-10-24 17:26:35（Asia/Shanghai）。此次入口没有记录总运行计时，不能提供精确秒数；进程采样曾确认已运行 9 分 20 秒仍在计算，此后完成。目录创建时间在当前文件系统上不能作为可靠的开始时间，因此不据此推算耗时。

### 正式核验之后的等价性能修正

保持已完成原报告不变，不再执行第二次真实全源核验，只做三项无判据变动的修正：

1. `distance` 改为空构造、LoadS1/LoadS2、一次 Perform，并继续检查完成状态、解数量、有限距离/最近点。原二参数构造在小实验中已 `IsDone=true`，之后显式 Perform 会再次计算；修正消除这次重复，不跳过距离检查。
2. 同一不可变、有序 B-rep pair 复用已完成的精确距离结果；弱引用避免保留几何，位置/方向快照阻止改变放置后使用旧结果，返回独立数值副本避免报告字段污染缓存。Boolean 使用 non-destructive 模式，支持的验证流程不原地修改输入几何；任意外部直接篡改底层 TShape 不属于缓存的不可变合同。
3. 已经是 Solid 的输入在 bounds/差集 helper 中保留同一 Python wrapper；明确不相交的 region 不创建等价新 wrapper，因此 weak bounds 缓存可跨多个区域重复利用。真正相交仍执行原精确 Cut 和有效性检查。

新增调用次数、失败不缓存、不同 B-rep/改变位置不得复用、结果副本隔离、弱引用释放、未变实体 bounds 仅计算一次与真实相交仍切除等用例。最终 verification suite：**78 passed in 5.27s**。该测试时间不能当作真实 V821 全核验的新耗时；没有用优化代码重跑整源，也没有降低 Common、距离或接触区域判据。

## 主集成下一步

1. 把完整性声明、共同坐标系、真实孔基准、插头/维护包络和装配阶段纳入提案确认；不要为通过检查而自动补来源。
2. 将 CAD artifact 身份、路径、单位及哈希接入上述接口，并保留源板变换；输出文件写完后再调用 verifier。
3. 审核 warning、`required_not_checked` 和产品需要的额外检查，只有所需范围通过才允许切片；保留报告与被检几何哈希的绑定。
4. 将 check evidence 接入 web 风险标记；修复编排只采用确认界限内的提案，重生成后重新验证全部必需检查。


## 2026-10-26 屏幕背置叠放与真实孔洞的连续装配路径

### 用户尺寸屏幕的独立几何检查

新增 `justbuild.verification.display.display_checks(request, cad, cad_path) -> list[dict]`，由主集成在原有 verifier 之后加入 supplemental checks。未含 `constraints.display` 的历史请求返回空列表，不追改旧产物。当前仅支持确认板坐标系 X 长轴下的 `pcb_back_parallel`：屏幕总包络长边平行 PCB 长边，XY 中心由原 PCB 计算，屏背面 Z = PCB 底面 - `board_to_display_gap_mm`。原始 STEP 通过 `board.step_path` 绝对路径读取，并用 `board.sha256` / CAD 输入 provenance 绑定；四份几何 STEP（base、lid、screen envelope 及原源）均检查哈希、有效实体和结束后输入稳定性。未读取 CAD metadata 自报 bounds 作为通过证据。

`constraints.display` 当前字段为 `id/size_mm/orientation/clearance_xy_mm/clearance_z_mm/board_to_display_gap_mm/pcb_post_height_mm/pcb_bridge_thickness_mm/face_opening/ribbon_channel`。这次用户明确的屏幕总外形为 **110 × 70 × 3.2 mm**；侧向 0.5、背侧 0.5、板屏间 4、短柱 2、悬臂桥厚 1.5 mm 属可见的原型设计选择，不能当成实测公差或真实 FFC 要求。PCB 背面的下方器件保留全部原实体。

实际检查包括：

- 用原始 PCB 坐标独立计算屏幕应在的位置，和 hash 绑定的 `screen_envelope_step` 做双向实体差集；同 bounds 的空心假包络不能通过。
- 用原始整板、base、lid 逐真实 solid Common 检查屏幕穿透，并独立测所有原始组件到屏幕的最小距离；任何正体积穿透都不豁免。
- 对屏幕侧向/背侧安装容纳区执行整壳实体空域检查。所有柱与桥属于实际壳体，不因 metadata 标为 support 而忽略。
- 原型 1 mm 边缘托台对应前方 108 × 68 mm 贯穿开口；要求该开口空，且所需边缘托台实体确实存在、恰位于屏幕前侧接触面。不能以整个底部缺失冒充合格托台。
- 四个原孔中心处要求短柱承压环实体存在；原始 PCB 孔 datum、导孔直径/轴线继续由 core verifier 检查，没有移动孔位或缩板。
- 排线矩形通道以原板边、整壳外界、所选侧面、用户可见宽/高/中心推导，并和原始整板/base/lid 做真实 Common。当前 x_max 原型例为 Y 10–50、Z -3.5–-1.5 mm，只证明静态通道，不证明一整根 FFC 已可折返安装。

缺少有效显示区、玻璃边缘可承压范围和真实屏固定要求，`display_active_area` 保持 **required not_checked**。缺少 FFC 实际长度/厚度/端点/弯曲半径及折返路径，`display_ribbon_bend` 保持 **required not_checked**。这些信息不能用外框尺寸、照片比例、空盒或 CAD 自报字段补成通过，当前修订可审阅，不应被发布成已完成物理装配验收或自动放行打印。

### `moving_part_brep`：保留孔洞的连续单轴路径

旧 box/boxes 路径保持原有行为。新增 assembly-only 的 `envelope={"kind":"moving_part_brep","provenance":"..."}`：输入只能使用 verifier 重新读入且 hash 绑定的实际 moving part，不能用调用方另给的代理 BRep。`against` 加明确 `absent_parts` 仍须完整覆盖其他部件，最小净距、正体积碰撞和有界 `touch_only` 规则保持不变。

对有界实体 S 和单轴平移 v，连续占据域可表示为 S 与所有边界面沿 `[0,v]` 棱柱的并集。实现保留全部原实体，并对每个非平行**平面**边界面执行真实 `BRepPrimAPI_MakePrism`，原面内环直接保留通孔。解析上精确平行的平面、轴线精确平行平移的圆柱面只生成零体积边界，可由相邻面棱柱闭包覆盖。其他曲面（例如非轴向圆柱、球、圆锥或未知面）返回 required `not_checked`，没有采样姿态代替证明。每个新棱柱须为有效正体积实体；负朝向只翻转新结果的朝向，不修改原输入。

扫掠结果用实际实体 compound 表达占据域并集。各片可能重叠，沿用既有逐 solid Common 的体积上界语义，绝不把体积和当成精确融合体积。零交叠及精确最小距离可以证明当前声明的刚性单轴路径空，仍不证明旋转、柔性线缆、非线性整机装配或动作可靠性。

这解决了带贯穿孔的盖板围绕外露针脚装配时，旧整矩形 box 把孔填满导致的保守误报；也保留了起终点均无碰撞但中途挡梁时的真实失败。

### 本轮文件、测试与集成

新增：`src/justbuild/verification/display.py`、`tests/verification/test_display.py`、`tests/verification/test_brep_sweep.py`。修改：`src/justbuild/verification/geometry.py` 的 `linear_brep_sweep`、`engine.py` 的 assembly 路径分支，以及本文档。未更改共享依赖或 CAD/打印/主集成代码，未访问打印机。

实际执行 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/verification -q --basetemp=/tmp/justbuild-03-display-and-sweep-suite -p no:cacheprovider`：**97 passed in 10.53s**。其中新增 11 个屏幕用例（4.94 s）和 8 个真实 BRep 扫掠用例（1.66 s）：穿柱、间隙不足、底部元件碰撞、空心包络、排线堵塞、开口挡梁、缺托台、哈希失配，以及穿孔提盖、途中挡梁、负轴、多 solid、不支持曲面、完整障碍物与接触区域。以上均为合成 BRep 回归，不能替代真实新修订 STEP 的正式核验或物理验收。

主控需将 `display_checks` 加入 supplemental 汇总并重新计算 verdict/required_not_checked；为 compact 盖板选择真实 BRep 路径即可，保留原 source/constraint/artifact hash、四孔 datum 和旧原型证据。真实屏幕/排线资料补齐前维持新修订发印阻断。


## 2026-10-26 后续：可拆载架与屏幕装配顺序

独立检查实际两件式静态稿 `3ccab1f1f0ad4b81b0d8c8d474475fd7` 后，发现“最终位置无穿透”不足以满足屏幕装入。报告 `/tmp/justbuild-03-static-display-audit-20261026/display-checks.json` 保存 source/base/lid/screen 四份 STEP 的实际 hash 和检查：17 pass、0 fail、3 required not_checked，耗时 113.92 s。板卡与盖子均移除后，screen 连续 +Z 扫掠仍与 base 一体桥交叠约 **573.7147 mm³**；-Z 扫掠与前托边交叠约 **1780 mm³**。这证明两条声明轴向路线都被阻塞，不证明没有任何可能的旋转/柔性/其他路线。新增 `display_screen_assembly` 必需未检查项保留该事实，旧产物不覆盖、不发印。

为支持后续可拆载架修订，core verifier 仅在显式 `constraints.display.support_structure="removable_pcb_carrier"` 下增加已知角色：

- `pcb_carrier_step`（mm/hash）→ `part_id="pcb_carrier"`；
- `screen_envelope_step`（mm/hash）→ `part_id="screen"`，明确是用户总尺寸机械包络，非制造商有效显示区模型。

两者和 board/base/lid 共五个真实读回 BRep，执行全部 **10 对**碰撞/接触检查。缺载架文件、缺屏幕文件、hash 失配或无显式结构却出现载架 artifact 都阻断通过，不能只检原三件。PCB 原孔 datum 不变，生成导孔可将 `mounting_checks[].part_id` 指向 `pcb_carrier`。opening 和 keepout 必须涵盖所有已声明壳体/屏幕角色，插拔路径仍要求所有在场障碍或带来源的缺席阶段。

此结构目前只支持明确的装配顺序 **screen → pcb_carrier → board → lid**。逆向拆卸路径依次满足：screen 对 base，其余三件 absent；carrier 对 base/screen，board/lid absent；board 对 base/screen/carrier，lid absent；lid 对其余四件。core 强制每个 moving part 都有路径，并检查每阶段完整 present/absent 集合。不能把所有配件都写成 absent，分别得到空路径后宣称最终组合可装配。实际各路径沿用真实 BRep 连续扫掠、最小净距和有界零体积接触规则；不支持曲面或资料缺失继续 required not_checked。

`display_checks` 在该显式结构下将承压环检查对象从 base 改为真实 carrier，并把 carrier 加到屏幕穿透、111×71 容纳区、前窗和排线通道的整壳障碍物中。其装配证明由 core 的上述四阶段检查承担，supplement 不另补一个 metadata 或“声明有载架即通过”的装配结论。有效显示区/边压与实际 FFC 资料仍分别阻断发印。

新增 `tests/verification/test_carrier.py`，覆盖五件读取/10对碰撞、载架碰板、载架漏声明/漏文件、缺屏路径、途中挡梁、阶段静默省略屏、原孔导孔指向载架、访问开口漏障碍物、以及不一致的配件装配顺序。增加屏幕用例检查支撑实体确实从 base 转移到 carrier。此前全 verification suite 实际为 **107 passed in 13.10s**；增加阶段顺序约束及独立反例后，载架针对性测试 **10 passed in 2.61s**，最终完整 suite **108 passed in 12.83s**。没有设备访问或真实打印。


## 2026-10-26 保守距离下界的保真提速

新增 `geometry.conservative_bounds` 与 `separation_lower_bound`，仅采用 OCC `BRepBndLib.Add(useTriangulation=False)` 的实体/曲面公差包容边界，不使用 CAD metadata 或显示网格 bounds。缓存为弱引用，记录位置/朝向快照；移动原 wrapper 后旧边界不可复用，返回数值副本防止报告污染缓存。

`engine.empty_envelope` 仍先执行原来的逐实体 Common。只有 Common 体积严格为 0，且保守 AABB 欧氏距离下界 **严格大于** `max(required_clearance, contact_tolerance)`，才跳过精确 `DistShapeShape`。该结果只写 `minimum_distance_lower_bound_mm/axis_separation_lower_bounds_mm/conservative_bounds_mm/bound_method/distance_semantics/exact_distance_solver_skipped`，**不写精确 `minimum_distance_mm` 或最近点**。下界不足/相等、bbox 重叠、可能接触及任何正体积交叠仍进入原精确距离和接触区域判据。没有放宽净距、体积容差、接触区域或旧错误处理。

新增 `tests/verification/test_separation_bound.py`：方盒/斜分离/球/圆柱的下界不超过实际 BRep 距离、严格分离才跳过精确 solver、0.0009 mm 近接触必须精确并受 contact policy 限制、重叠 bbox 中的真实孔仍跑精确检查、等于所需下界不短路、净距不足、正体积穿透即使下界异常也不能短路、位移后缓存失效、报告副本隔离。针对性 **12 passed in 0.87s**；最终完整 verification suite **120 passed in 12.23s**。

当前已经开始的 `d0ab2583a907400f9fa0d82ea79e7d17` 老进程没有被打断或替换。只读其冻结的 543 个 PCB 扫掠盒与真实 screen/carrier 小 STEP，1086 个新增距离对子中有 **543 + 533 = 1076** 个满足严格下界条件，计数内核部分耗时 0.036 s；这是可避免调用数，不是全源重新核验或总耗时基准。全部原始源、孔位、base 障碍与可能接触的余下对子仍保留真实检查。

### d0ab 的实际小件预检与 core 反馈

`/tmp/justbuild-03-carrier-preflight-20261026.json` 保留新四件 STEP 的 hash 与实际解析面类型。screen/carrier/lid 的真实连续扫掠均可由当前后端构建（2/18/19 个实际面棱柱），没有未知曲面被忽略。`/tmp/justbuild-03-carrier-path-pairs-20261026.json` 用相同内核只检查新小件之间的部分路径，不读取 543 源、不当作整体放行证据：screen/base、carrier/screen、lid/screen、lid/carrier 通过；carrier/base 和 lid/base 发生有限接触区域外的滑动接触。

- carrier/base 的非耳滑接点例：`(105.5001003302,63.20005969012,-2)`，扫掠阶段另在 base 顶面相接；不能扩大耳面豁免区来隐藏。
- lid/base 的 U 槽填块侧隙仅约 `0.00099999999999 mm`，不大于当前 0.001 mm 数值接触界限；需真实增加间隙，不能放宽比较符或容差。

随后只读实际 core 报告得到 **1851 pass / 4 fail / 6 not_checked / 3 not_applicable**，四个失败恰为上述两类几何的静态接触与连续路径检查。主控/CAD 将在新修订修实体间隙和侧面接口 U 湾，不修改本稿证据。用户最新接受从照片估计和暂用现有排线通道，当前屏幕边托/面窗/40×2 通道继续作为审阅原型；有效显示区和真实 FFC 两项保持 required not_checked。它们当前是明确未实现的分析分支，补一段说明文字不会自动变成 pass。


## 2026-10-26 M2 与薄壁参数兼容核查

只读检索确认 verification/display 生产源码没有 M3 名称、旧 M3 盲孔深度/径或锁盖头径硬编码。`carrier_clamp` 目前不是本模块判定分支，旧 `shared_lid_m3` 与中性 `shared_lid_fastener` 均不触发名称误报。PCB 承压环从 `manufacturing.pcb_mount` 读取实际 post/pilot 尺寸，屏幕排线静态通道从 request 的实际 `wall_thickness_mm` 推导。原 PCB 四孔 datum/导孔由 core 的明确 mounting targets 独立核验。

按主控本轮快速交付范围，**没有新增锁盖 M2 螺钉专题检查**：M2 柱 Ø6.2、导孔 Ø1.6/深4.5、间隙孔 Ø2.2、头 Ø4/深1.2、M2×14 是本次请求参数，不是本独立模块已验证的锁盖盲孔/头部/螺纹/强度结论。当前四个独立孔目标仍是原 PCB 安装孔，不可将它们的通过扩大成锁盖五金验收。主控负责为新轴位和 Ø6.2 外耳声明与实际导出相符的有限接触区；真实碰撞、承压环和四阶段路径保持原有判据。

新增两个参数化兼容用例，真实将合成壳壁/底由 2.0 减至 1.6 mm、盖保持 2.0 mm，并将锁盖请求设为上述 M2 值，分别选择新旧 clamp 标签。检查屏幕/短柱/托台/通道确实通过，通道起点按实际 1.6 mm 壁厚变为 X=98.4，而非旧 98.0；有效显示区和 FFC 两项仍 required not_checked。执行 `tests/verification/test_display.py`：**14 passed in 6.32s**。这些是参数兼容与屏幕几何回归，明确不检验或宣称 M2 盲孔/头部已被独立验收。没有修改旧任务或启动设备。
