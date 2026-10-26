# V821 真实 STEP 外壳重新设计

输入：`3D_V821L2WBX_1_2026-10-24.step`，SHA-256 `2b096e5a608d7089dbc6062cfb4fd410af0d810e17436928e0bbb47b45dd51cc`。源文件保留全部 543 实体。PCB 约 99.974801 × 59.974719 × 1.586157 mm，四个安装孔实际 Ø2.00000508001 mm，原始孔轴不移动；旧 80 × 55 mm 合成参考停止作为本板适配件发印。

## 访问方案

本轮为上下盖、螺丝固定的全接口维护窗设计稿。左侧 USB-C、TF 和 SIM 窗，SIM 另有顶部维护窗；KEY 后侧操作窗；FEL、ADB/4G 开关、CAM/FPC、天线扣合、调试排针、MIC/SPK 各有顶部服务窗；蜂鸣器留独立声孔。AIR780E 模块和晶振保留原实体净空，不冒充外部接口开洞。

| 器件 | 类型 | 访问面 | 原始实体数 |
| --- | --- | --- | --- |
| USB1 · USB | USB Type-C | 左侧 | 42 |
| CARD1 · SD | TF / microSD card holder | 左侧 | 24 |
| CARD2 · CARD2 | SIM card holder | 左侧 / 顶部 | 15 |
| U11 · KEY | KEY switch | 后侧 | 2 |
| SW2 · FEL | FEL tactile switch | 顶部 | 16 |
| SW3 · ADB | two-position DIP switch group | 顶部 | 14 |
| SW4 · 4G switch | two-position DIP switch group | 顶部 | 14 |
| P1 · CAM | 24-contact FPC connector | 顶部 | 4 |
| FPC1 · FPC1 | 40-contact FPC connector | 顶部 | 44 |
| RF1 · WIFI/BT | IPEX-series coax connector | 顶部 | 1 |
| RF2 · GPS | IPEX-series coax connector | 顶部 | 1 |
| RF3 · 4G antenna | IPEX-series coax connector | 顶部 | 1 |
| U8 · UART3 / TX RX GND | 3-contact 1.27 mm header | 顶部 | 1 |
| U10 · adjacent TX RX GND header | 3-contact 1.27 mm header | 顶部 | 1 |
| H3 · top-edge header | 4-contact 2.54 mm header | 顶部 | 4 |
| H2 · 3V3-associated header | 4-contact 1.27 mm header | 顶部 | 1 |
| CN5 · MIC | 2-contact 1.25 mm connector | 顶部 | 3 |
| CN1 · SPK | 2-contact 1.25 mm connector | 顶部 | 3 |
| BUZZER1 · buzzer | SMD buzzer | 顶部 | 1 |
| U6 · AIR780E | AIR780E communication module | 内部保留 | 1 |
| X2 · X2 | 3.2 x 2.5 crystal | 内部保留 | 1 |

## 原型制造与装配参数

- 外壳壁厚 2 mm，底板 2 mm，上盖 3 mm；板下柱高 5 mm，实际底部针脚全部保留并检查，不用整板大包围盒删掉针脚来获得装配通过。
- PCB 柱外径 3.8 mm，导孔 1.2 mm，螺钉通径要求 1.8 mm，拟 M1.6 自攻原型紧固；导孔尺寸、实际螺钉头/长度、扭矩与强度尚未实物验收。PCB 柱径向最小肉厚参数独立为 1 mm，整壳壁厚没有因此降低。
- 上下盖外圈独立锁合孔继续与板孔区分。打印文件按零件分别放到 Z=0；底壳/上盖 STEP 与原始板卡保留相同坐标，由装配清单关联。
- 离线参考切片为 A1 / 0.4 mm / PLA / 0.20 mm Standard / Textured PEI，尚未绑定或读取实际打印机/耗材，也不代表热/RF/强度合格。

## 检查与修订证据

真实 PCB 全内外轮廓及 31 个内部轮廓通过等截面 BRep 证明；四个支撑环完全落在真实板材内，孔内残留与支撑环缺料均 0 mm³。四孔的两个半圆柱面由独立检查器合并解析，不能拿部件的圆孔替代 PCB 孔。

初始 SW4 工具窗口与邻接 CAM 的 3 个实体发生服务空间冲突，已保持窗口总尺寸、仅向 X 负方向平移 1.9 mm；完整 SW4 本体仍包含在窗口投影内，任何 PCB/器件/板孔都未移动。20 个有效静态服务包络随后完成正式独立窗口/接近路径核验；相邻维护窗可能连通，不宣称是20个互不相连的孔。

真实装配的下方针脚会使单个整板矩形包络误挡安装柱。本轮使用 543 个显式包络，每个实际源实体由至少一个独立包络完整包含，逐个检查单轴直线连续扫掠；不是离散采样或忽略底部器件。

首次 CAD 生成遇空 TopoDS 结果，失败证据保留于旧 run；程序没有把异常判为“无碰撞”。后续按保守包围体筛选和逐原实体精确布尔核对，非零交集体积和明确为保守上界，0 判据不放松。

源板 STEP 重编码曾出现 0.5261451013302576 mm³ 体积差，超过原容差，故该重编码产物未发布。最终确认 identity frame、原生毫米单位且无转换单位，直接保留用户原始 STEP 字节；可选整装配 STEP 未发布，提供同坐标部件与装配清单，完整 GLB 保留全部543源实体和两壳体。后续 `generate()` 已自动执行这条严格、限条件的保真续接，不把其他几何失败视为可恢复。

## 最终几何与切片结果

2026-10-24 正式原始独立报告为 **671 pass / 0 fail / 6 not_checked / 3 not_applicable**。追加独立侧壁/底板净距与命名功能清单检查、缺失动作分析后，交付报告为 **674 pass / 0 fail / 11 not_checked / 3 not_applicable**，必需未检查列表为空。四个底座导孔相对真实 PCB 孔轴的偏差均为 0.000 mm（几何容差0.001 mm）。board/base/lid三组实体对的正体积交集均为0，允许的接触严格限制在已声明支撑或盖板区域。

侧壁实际最小净距约9.0 mm，底板约3.58616 mm，板与上盖约3.68917 mm；分别满足本稿所声明下限。STL两件分别6108和4084三角形，精确float32坐标合并后边界边、非流形边、方向不一致边和退化三角均为0；这不是最大离散误差或实物公差证明。

官方 Bambu Studio CLI `02.08.02.61` 实际切片完成，非模拟。3MF含同一盘的底壳与上盖，实际回读A1/0.4 mm/PLA/0.20 mm Standard/Textured PEI配置，CRC通过且G-code非空；对象没有跳过，打印区域未越界。切片器估算6119秒（约1小时42分）、62.31 g PLA。延时摄影有切片器不支持提示，没有启用支撑；尚未独立验证工具路径安全或打印机实况。

交付切片：`dist/justbuild-v821-real-a1.gcode.3mf`，633265字节，SHA-256 `91d91dfbd8e6f2e95687e883589bb793c552e33c1cf0b2d36320a38b2ed6083b`。证据见 `docs/v821-release-evidence.json`，任务内保留原始独立报告与附加范围报告两份。

## 尚未证明的内容

静态 STEP 与几何服务窗不能证明实际插头完整啮合、按压/拨动行程、卡和 FPC 锁扣动作、真实线缆/外设装配、声学、热、RF 和结构强度。这些在报告保留 `not_checked`。本稿为已完成声明几何检查和参考切片的试装原型；新设计仍需用户审阅，当前不启动打印。

运行任务：`019d8f0d6d92426aa96618210deb469a`；当前进展与最终结果另见 `data/v821-redesign/` 和任务内 JSON。
