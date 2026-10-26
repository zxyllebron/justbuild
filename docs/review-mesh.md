# 完整审阅模型的无损轻量派生

`review_mesh.py` 只派生浏览器显示文件，不改 STEP、CAD 结果清单、请求、独立核验、打印 STL 或 3MF。默认不简化几何，不删器件，不用包围盒替代器件，不删除 normals。完整原 GLB 继续保留，可作为下载与回退。

## 当前真实 V821 结果

输入：`data/jobs/019d8f0d6d92426aa96618210deb469a/generating/cad-preserved-787nmihw/review.glb`。

派生：`data/v821-redesign/review-optimization/lossless-01/review-light.glb`，SHA-256 `029f95ad83f20dbb2d1a1b54ecff7a63c3a09524e071f9911fa160b1b73dcf83`。

证据：同目录 `review-mesh-result.json`。

| 项目 | 原始 | 无损派生 |
| --- | ---: | ---: |
| GLB 字节数 | 43,367,816 | 10,713,216 |
| primitives（单材质情况下对应 draw call） | 64,840 | 545 |
| accessors | 194,520 | 1,086 |
| mesh 数 | 545 | 545 |
| node 数 | 546 | 546 |
| 三角形 | 384,357 | 384,357 |
| 属性顶点行 | 483,136 | 463,739 |
| 原始板卡器件实体对应 mesh | 543 | 543 |
| 命名功能组件映射 | 21 | 21 |

文件减少 **75.30%**，没有减少任何三角形。原始 GLB 的 29.45 MB 是 JSON：OCC 把每个 B-rep 面各自导成 primitive 和三组 accessor。按原 mesh、材质、属性格式与其他 primitive 语义字段合并后，JSON 结构大幅缩小；同位置/同法线等全部属性逐字节一致的顶点可安全复用。重复 accessor 的实际字节也可共享，索引按最大顶点编号使用合法的 uint8/uint16/uint32。

完整 543 器件、底座、上盖均保留。所有 545 个 mesh 的展开三角形顶点及每个属性（含 NORMAL）字节散列在重新序列化、重新读取 GLB 后逐一相等；节点、层级、场景、材质与变换完整相等，局部/世界 bounds 一致。相对于原 GLB 的新增位置误差为 **0 mm**。原 GLB 本身是 STEP 的显示网格，因此这个 0 不能解释为相对于精确 STEP 曲面的网格误差为 0。

实际世界坐标 bounds（glTF 米）：

```json
{
  "min": [-0.012426776885986328, -0.007000000000000001, -0.07427651977539061],
  "max": [0.11098750305175782, 0.015086160659790052, 0.01098729991912842]
}
```

21 项映射来自 `data/v821-redesign/interface-map.json`，每项保留命名引用、源实体索引、`input_solid_N` mesh 名称、原 mesh/node 索引；缺任一成员实体会拒绝发布。此映射不是新的插头/动作/热学验收。

## API 与主控集成

```python
from pathlib import Path
from justbuild.review_mesh import optimize_review_glb

result_path = optimize_review_glb(
    source_glb=Path(".../review.glb"),
    output_dir=Path("caller-provided-derived-stage-directory"),
    component_map_path=Path(".../interface-map.json"),  # 可选，真实 V821 应提供
    job_id="019d8f0d6d92426aa96618210deb469a",
)
```

返回 `review-mesh-result.json`，共同 envelope 为 `schema_version/job_id/status/artifacts/issues`，派生产物 `kind=review_glb_light`，相对路径 `review-light.glb`、SHA-256、字节数和 glTF `units=m`。metadata 包含前后统计、逐 mesh 保真证据、全部命名组件对应、来源散列、世界 bounds、零新增误差与 `display_only=true`。真实 V821 首次运行约 **3.01 秒**，不是新 CAD 或碰撞核验。

主控可在 generating 成功后增加派生 stage；保持原 CAD 结果和散列不变。浏览器优先取 `review_glb_light`，不存在或派生不支持时回退原 `review_glb`。两者几何相同，无需另设精度模式。GLB primitive 分组改变绘制批次，透明混合的排序可能受渲染器影响；本模块证明的是几何/材质/属性保真，不声称像素逐点相同。浏览器性能仍需真实渲染基准，不把 draw call 数变化当作实测帧率。

异常抛出 `ReviewMeshError`（无完整轻量输出成功结论）。不原地覆盖输入；源 GLB 与可选映射文件在处理前后散列绑定。派生 GLB 写入临时文件并重新解析校验，成功后才发布到指定路径。若不能减小文件，保留原始显示文件作为回退。

## 范围与依赖

仅用 Python 标准库和已安装的 NumPy，没有安装 trimesh、open3d、fast_simplification 或新的系统软件。支持 uncompressed glTF 2.0 triangle primitives、普通/交错 vertex buffer、合法整数索引和原 primitive 的材质/附加字段。原 normals、UV、normalized color 等属性保留。稀疏 accessor、压缩扩展、嵌入图像、蒙皮、动画、morph targets 等未实现完整保真路径时拒绝处理，不能悄悄删掉它们。

不调用 CAD 内核、不重跑 9 分钟独立核验、不执行切片、不联网发印。

## 检查

`PYTHONPATH=src /home/wujie/.cache/justbuild/venv/bin/python -m pytest tests/integration/test_lossless_review_mesh.py -q`

首次测试：**7 passed in 0.12s**。独立小 GLB fixture 覆盖全部 mesh/node/材质/变换和三角数保留，反向/尖锐法线，normalized color，交错属性，多材质分组，一项功能关联多个原实体、缺失命名实体不得发布、越界索引/未知压缩不得成功、不能覆盖源文件。真实 545 mesh 的逐角点属性散列验证另由实际派生产物的证据 JSON 保存。
