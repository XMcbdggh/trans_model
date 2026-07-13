# 智能体建图系统：图片 → 建筑 JSON → 3D 模型（可复现 / 可扩展文档）

> 目标读者：另一个 AI 或工程师。读完本文档 + 随附的 `agent3d/` 代码，应能**完整复现**
> 这套系统，并在其上**扩展**（新构件、新风格、爆炸模块等）。
>
> 本系统在现有 `stand_trans` 管道（参数化建筑 JSON → 3D）之上，增加了两层：
> **① 图片+文字 → 建筑语义 JSON（大模型）**、**② 语义 JSON → 合法 param.json（可靠代码生成）**，
> 并封装成**一个 Web 应用**（普通用户可用）＋**两个 Skill**（给智能体/开发者），
> 用 three.js 查看最终 GLB 与 litematic 体素。爆炸模块本期不实现，但 `.litematic` 全程保留以便后续扩展。

---

## 0. 交付物清单（全部已落地并验证）

```
agent3d/
  core/
    builder.py          SceneBuilder —— Layer2 可靠生成器(闭合墙环/自动host_id/布窗/堆叠楼层/屋顶字段)
    spec_to_param.py    Building Spec(Layer1) → param.json(Layer2) 声明式翻译器
    pipeline_runner.py  param.json → model.glb + model.litematic + voxels.json (+manifest)
    vision.py           服务端视觉:图片+文字 → Building Spec(调 Anthropic,强制工具输出+一次自修复)
    __init__.py
  schema/
    building-spec.schema.json   ★ 智能体 JSON 输出标准(Layer1),JSON Schema draft-07
  examples/
    two_storey_house.spec.json  / .param.json   最简示例(已验证跑通全管道)
    walled_compound.spec.json   / .param.json   带围墙/穹顶/景观/车辆的复杂示例
    build_scene_example.py       手写 scene 函数示例(不规则布局的逃生舱)
  webapp/
    server.py           FastAPI:POST /api/generate + 产物静态服务 + UI
    static/index.html   普通用户上传页(图片+描述→生成→内嵌查看器)
    static/viewer.html  单文件 three.js 查看器(GLB/体素切换 + 自动旋转)
  skills/
    building-image-to-json/SKILL.md   Skill A
    json-to-3d/SKILL.md               Skill B
  requirements-web.txt  Web 额外依赖(fastapi/uvicorn/python-multipart/anthropic)
serve-webapp.ps1        启动 Web 应用(默认 :8060)
docs/AGENT_3D_PIPELINE.md  本文档
```

现有仓库（复用，未改动）：`stand_trans/`（5 步管道）、`backend/`、`frontend/`、`examples/`。

---

## 1. 系统总览

```
┌─────────────────────────────── 入口一：Web 应用（普通用户） ───────────────────────────────┐
│  浏览器 index.html  ── 上传图片 + 文字描述 ──►  FastAPI /api/generate                        │
│                                                   │                                          │
│                                    ① vision.py  图片+文字 ─(Anthropic 视觉)─► Building Spec  │
│                                    ② spec_to_param  Spec ─(SceneBuilder)─► param.json        │
│                                    ③ pipeline_runner  param.json ─(stand_trans)─► 产物        │
│                                                   │                                          │
│                          返回 {scene_id, urls}  ──►  内嵌 viewer.html (GLB/体素切换+自动旋转) │
└──────────────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────── 入口二：两个 Skill（智能体 / 开发者） ──────────────────────────┐
│  Skill A building-image-to-json : 智能体自身视觉读图 → Building Spec JSON                    │
│  Skill B json-to-3d             : Building Spec / param.json → GLB + litematic + 查看器      │
└──────────────────────────────────────────────────────────────────────────────────────────┘

两个入口共享同一套 core/ 引擎与同一个 Building Spec 标准。
```

**数据流水（两层 + 现有管道）**

```
[图片 + 文字]
   │  大模型(视觉)
   ▼
[Layer1] Building Spec JSON   ← 智能体输出标准(schema/building-spec.schema.json)
   │  spec_to_param.py + SceneBuilder  (可靠代码生成,几何永远合法)
   ▼
[Layer2] param.json          ← stand_trans 管道输入契约
   │  stand_trans: step1 归一 → step2 BIM → step3 GLB → step4 litematic
   ▼
model.glb + model.litematic + voxels.json
   │
   ▼
three.js 查看器      ·  .litematic 保留 → 后续接 step5 爆炸模块
```

**为什么分两层**：一张建筑照片，大模型很难可靠地反推出**以米为单位的墙体起止坐标**、以及门窗
挂载到哪面墙（`host_id`）。所以让大模型只产出**高层语义**（几层、外轮廓 bbox、屋顶类型、每面
几扇窗、风格），再用**确定性代码**（`SceneBuilder`）把语义翻译成精确、合法的几何。大模型表达
意图，代码保证正确性 —— 这就是"用生成的 py 脚本可靠性生成 JSON"。

---

## 2. 坐标系与单位约定（务必统一，扩展时最易踩坑）

| 空间 | 轴向 | 单位 | 出现处 |
|---|---|---|---|
| **建筑空间**（Spec / param / BIM） | X=东, Y=北, Z=上（右手系） | 米（归一化后） | Building Spec、param.json、BIM |
| **GLB 导出** | 场景绕 X 轴旋转 −90° → Y=上 | 米 | model.glb（three.js 约定） |
| **Litematic 区域** | X=东, Y=上(=建筑 Z), Z=镜像(建筑 Y，深度翻转) | 1 方块 = `pitch_m` 米 | model.litematic、voxels.json |

- 建筑空间 `(x_e, y_n, z_up)` 经 GLB 的 −90°×X 旋转 → `(x_e, z_up, -y_n)`，即 GLB 里 **Y 朝上**。
- Litematic 写入时深度轴翻转 `(wy-1-j)`，使体素朝向与 GLB 一致。
- `voxels.json` 的 `blocks` 是 **区域坐标 `[x, y(上), z]`**，与 GLB 同为 Y-up；查看器把体素组
  整体缩放 `pitch_m` 即与 GLB 同尺度（米）。
- `pitch_m = 1 / blocks_per_meter`（默认 `blocks_per_meter=4` → 0.25 m/块）。

---

## 3. ★ 智能体 JSON 输出标准：Building Spec（Layer 1）

这是大模型**唯一**要产出的东西，也是本系统的核心契约。完整机器可校验定义见
`agent3d/schema/building-spec.schema.json`（JSON Schema draft-07）。下面是字段总览。

### 3.1 顶层结构

```jsonc
{
  "meta":   { "name": str, "style": "persian|modern|classical|islamic", "description": str },
  "site":   { ...整体地块... },              // 可选
  "buildings": [ { ...每栋楼... }, ... ],     // 必填,>=1
  "domes":      [ ... ],   "pools":    [ ... ],   "gardens": [ ... ],   // 可选场景要素
  "vegetation": [ ... ],   "vehicles": [ ... ]
}
```

### 3.2 `site`（地块，可选）

```jsonc
"site": {
  "width_m": 120, "depth_m": 90,                       // 地块尺寸;建筑须落在 [0..width, 0..depth]
  "ground": { "surface": "sand|grass|concrete|none", "material"?: str, "thickness_m"?: 0.5 },
  "perimeter_wall": {                                   // 可选围墙
    "height_m": 5, "thickness_m": 1.0, "material": "stone_masonry",
    "corner_towers": true, "tower_size_m": 6.0
  }
}
```

### 3.3 `buildings[]`（每栋楼，必填）

```jsonc
{
  "id": "central",
  "footprint": [x0, y0, x1, y1],        // ★必填:米制矩形,x1>x0 且 y1>y0,不得越界/重叠
  "floors": 2,                          // 地上层数 >=1
  "floor_height_m": 4.5,
  "wall_thickness_m": 0.4,
  "material": "reinforced_concrete|stone_masonry|brick_masonry|steel|timber",
  "roof": { "type": "flat|gable|hip|pyramidal" },
  "columns_spacing_m": 7,               // 0/省略 = 无内柱网
  "windows": {
    "shape": "rect|pointed_arch|horseshoe_arch|round_arch",
    "width_m": 1.4, "height_m": 1.6, "sill_m": 0.9,
    "per_facade": { "south": 4, "north": 4, "east": 2, "west": 2 }   // ★只给"数量",坐标由代码算
  },
  "entrance": { "facade": "south|north|east|west", "type": "door|iwan",
                "shape": "...", "width_m"?: 5, "height_m"?: 3 },
  "dome":     { "radius_m": 5, "height_m": 6, "shape": "onion|hemisphere|tent" },  // 楼顶穹顶,可选
  "basement": { "floors": 3, "floor_height_m": 4, "footprint"?: [x0,y0,x1,y1] }    // 地下,可选
}
```

**立面命名 → 墙面映射**（矩形 `(x0,y0)-(x1,y1)`，与 `SceneBuilder._box_walls` 一致）：
`south=0 (y=y0)`、`east=1 (x=x1)`、`north=2 (y=y1)`、`west=3 (x=x0)`。

### 3.4 场景要素（均可选）

```jsonc
"domes":      [ { "id", "level"?: "G", "center": [x,y], "radius_m", "height_m", "shape", "base_height_m" } ],
"pools":      [ { "id", "footprint": [x0,y0,x1,y1] 或 [[x,y],...], "depth_m" } ],
"gardens":    [ { "id", "footprint": ... } ],
"vegetation": [ { "kind": "palm|cypress|tree", "height_m"?, "canopy_radius_m"?, "positions": [[x,y],...] } ],
"vehicles":   [ { "id", "kind": "car|truck|tank|equipment", "center": [x,y], "heading_deg", "length_m"?, ... } ]
```

### 3.5 约束（大模型必须自检）

1. 每个 `footprint` 满足 `x1>x0`、`y1>y0`，且在地块内、彼此不重叠；
2. `floors>=1`；`per_facade` 各数为非负整数；
3. 枚举字段只用允许值；
4. 只放"看得见/文字提到"的要素，宁少勿滥。

> 完整可运行示例见 `agent3d/examples/two_storey_house.spec.json`、`walled_compound.spec.json`。

---

## 4. Layer 2：`SceneBuilder` 可靠生成器

`agent3d/core/builder.py`。它把高层语义翻译成**保证通过 stand_trans 校验**的 param.json，
承担全部"易错的几何细节"，因此大模型永不需要手写坐标：

- `box_building(prefix, bbox, level_names, ...)`：每层生成 4 面闭合外墙（环精确闭合）+ 楼板
  （+ 可选柱网）+ 顶层屋顶；返回 handle（含各层各立面的墙 id）供挂窗挂门。
- `add_windows(building, facade, count, ...)`：沿某立面**按数量均布**窗，自动算中心坐标并
  确保落在宿主墙上、自动接 `host_id`（否则 normalize 会因越界报错）。
- `add_door / add_iwan / perimeter_wall / add_dome / add_pool / add_garden / add_terrain /
  add_tree / add_vehicle / add_room / add_stair`：其余要素。
- `stack_levels(names, base, height)`：楼层标高连续堆叠。
- `_add_roof(...)`：按屋顶类型补齐字段 —— `gable` 自动给 `ridge_start/ridge_end/eave_height_m/
  ridge_height_m`；`hip/pyramidal` 给屋脊高度；`flat` 无需额外字段。**这是最常见的校验坑**。
- `to_param()` / `write(path)`：产出 param.json。

**两种用法**：
- **声明式（常见）**：`spec_to_param(spec)` 自动驱动 builder，大模型只出 Spec JSON。
- **命令式（不规则布局的逃生舱）**：大模型仿 `examples/build_scene_example.py` 写一小段
  `build()` 函数直接调 `SceneBuilder`（可用循环/数学），产出 param.json。二者输出同构。

---

## 5. param.json 标准（stand_trans 管道输入契约）

Layer2 产物，也是现有管道的输入。校验/归一化逻辑在
`stand_trans/step1_normalize/{schema.py,normalize.py}`。要点：

- `project`: `{ name, unit: "m|cm|mm", style?, north_angle_deg? }`（归一化后统一转米）。
- `levels[]`: `{ name(唯一), elevation_m, height_m>0 }`。
- 构件集合（本系统由 builder 产出的）：`walls`(起止/厚/category/load_bearing)、`columns`
  (center + rect size / circle radius)、`slabs`(polygon 或 bbox + 厚)、`doors/windows`
  (**host_id 指向墙 + center 落在墙上** + 宽高 sill + shape)、`roofs`(type + profile；gable
  需 ridge_*)、`stairs`(from/to level + bbox)、`rooms`、`domes/iwans/pishtaqs`、`pools/gardens/
  canals`、`trees/vehicles/terrain`。
- 完整集合清单见 `stand_trans/step1_normalize/schema.py::COLLECTIONS`。
- 风格：`style.preset ∈ {persian,modern,classical,islamic}`（islamic 归一到 persian 家族），
  影响材质配色、窗/门/柱造型、立面、屋顶细节。材质→颜色/Minecraft 方块/抗爆强度映射在
  `stand_trans/shared/materials.py::MATERIALS`。

> 参考产物：`examples/*.param.json` 与本系统 `agent3d/examples/*.param.json`。

---

## 6. stand_trans 管道（step1–4，本系统只用到前四步）

`stand_trans/pipeline.py::convert(input, out_dir, name, make_glb, make_litematic, blocks_per_meter)`：

1. **step1 normalize**：校验 + 单位归一（→米）+ 补默认值。
2. **step2 to_bim**：参数 → BIM 中间模型（`{project, levels, elements[], stats}`；每个 element
   带 `id/type/level/geometry/source`）。还会派生梁/基础/MEP（本系统关掉 `auto_structure/auto_mep`）。
3. **step3 build_visual_glb**：trimesh 造型，CSG 给门窗/iwan 开洞（`manifold3d` 后端），PBR 顶点
   色来自风格调色板；场景绕 X −90° 导出 **Y-up GLB**。
4. **step4 build_litematic**：复用 step3 的网格体素化（`litemapy`），按材质映射 Minecraft 方块，
   写 `.litematic`；同时产出 `.voxelclass.json` 语义边车（本系统查看器不用，但保留）。
   `litematic_to_voxels()` 把 litematic 解码为浏览器友好的 `{dims,palette,palette_ids,blocks,count}`。

`agent3d/core/pipeline_runner.build_scene()` 封装了上述调用，并额外把 GLB/litematic 重命名为
`model.glb/model.litematic`、导出 `voxels.json`（附 `pitch_m`）、写 `manifest.json`。

**CLI（现有）**：
```powershell
.\run.ps1 convert .\examples\...param.json --out-dir .\out\x --name x --litematic
```

---

## 7. Web 应用（普通用户入口）

### 7.1 安装与启动

```powershell
pip install -r requirements.txt                 # 现有管道依赖(trimesh/litemapy/manifold3d/...)
pip install -r agent3d/requirements-web.txt      # fastapi/uvicorn/python-multipart/anthropic
$env:ANTHROPIC_API_KEY = "sk-ant-..."            # 视觉步骤需要
.\serve-webapp.ps1                               # http://127.0.0.1:8060/
```

浏览器打开 `http://127.0.0.1:8060/`：上传图片 + 填描述 → 「生成 3D 模型」→ 页面内嵌查看器，
可切换 **实景(GLB) / 体素(Litematic)** 并 **自动旋转**；可展开查看生成的 Building Spec。

### 7.2 API 契约

- `POST /api/generate`（multipart）
  - 表单字段：`images`（一或多张图片文件）、`description`（可选文字）；**或** `spec`
    （直接给 Building Spec JSON 字符串，跳过视觉 —— Skill A / 智能体走这条）。
  - 流程：`vision.image_to_spec` → `spec_to_param` → `build_scene`。
  - 返回：`{ scene_id, name, spec, stats, urls:{glb,voxels,spec,param,litematic,viewer} }`。
- `GET /api/scenes/{id}/{file}`：静态取产物（model.glb / voxels.json / spec.json / param.json /
  model.litematic / manifest.json）。
- `GET /`、`GET /viewer.html`：上传页与查看器。

环境变量：`ANTHROPIC_API_KEY`（必需）、`WIDE_SIM_VISION_MODEL`（默认 `claude-sonnet-5`，难图可
换 `claude-opus-4-8`）、`AGENT3D_BPM`（体素分辨率，默认 4.0；大场景用 ~2.0）、`AGENT3D_SCENES`
（产物根目录，默认 `artifacts_web/`）。

### 7.3 视觉步骤（`core/vision.py`）

用 Anthropic Messages API + **强制工具调用**（`tool_choice` 指定 `emit_building_spec`，
`input_schema` = Building Spec schema），因此模型必须返回合法结构；`validate_spec` 快速校验，
失败则携带错误信息**自动重试一次**。系统提示与 Skill A 的 SKILL.md 内容一致。

> 说明：本文档随附代码中，除"实时视觉调用"（需 API key）外，spec→param→GLB→litematic→voxels
> →Web API→静态服务的**全链路已用 TestClient 端到端验证通过**（简单房子：235 网格 GLB /
> 41957 体素）。

---

## 8. three.js 查看器（单文件，自包含）

`agent3d/webapp/static/viewer.html`。纯 ES Module + importmap 从 CDN 载入 three 0.184.0：

- **GLB**：`GLTFLoader` 载入 `model.glb`，加入 `glbGroup`，`Box3` 自动取景。
- **体素**：`fetch voxels.json` → 一个 `InstancedMesh`（`BoxGeometry(1,1,1)` + `MeshLambertMaterial`），
  逐实例 `setMatrixAt`（按 `dims` 居中 XZ、贴地）+ `setColorAt`（调色板 hex）；整组缩放
  `pitch_m` 与 GLB 同尺度。
- **UI**：三个按钮 —— 实景(GLB) / 体素(Litematic) 切换、自动旋转开关（`OrbitControls.autoRotate`）。
  切换时对当前对象重新取景。**不含**结构 HUD、构件查询、爆炸等（按需求裁掉）。

独立使用（脱离 Web 应用）：
```powershell
python -m http.server 8080 --directory .\scene_out
# 打开 viewer.html?glb=/model.glb&vox=/voxels.json
```
带参 `?scene=<id>` 时自动指向 `/api/scenes/<id>/...`。**离线部署**：把 importmap 里的三个
three.js URL 换成本地 vendored 文件即可（无其它外部依赖）。

---

## 9. litematic 与爆炸扩展点（本期不实现，已预留）

- 每次生成都产出并保留 `model.litematic`（Minecraft Litematica 原生格式）。
- 现有仓库已有完整爆炸实现可对接：`stand_trans/step5_blast/blast.py`（Kinney-Graham 超压 +
  侵彻 + 级联倒塌）、`backend/blast_runner.py`（弹种当量/引信/坐标）、`stand_trans/shared/
  structure.py`（支承图/级联）、`materials.py::block_resistance()`（方块→抗爆强度）。
- 体素→毁伤所需的语义（结构类别/材质/临界度/建议打击点）在 step4 附带产出的
  `*.voxelclass.json` 里（本系统未纳入 Web 流程，但 `stand_trans` 会生成，可直接取用）。
- **接法建议**：新增 `POST /api/blast`，读 `model.litematic` + `voxelclass.json`，调
  `compute_blast()`，把 `removed_xyz` 回传查看器做逐体素消隐/坍塌动画（可参考 `frontend/` 里
  `BuildingViewer.tsx` 的 `debrisMesh`/`LitematicMesh` 实现，注意剔除其 blast 专有部分）。

---

## 10. 复现步骤（从零到能用）

```powershell
# 1. 依赖
pip install -r requirements.txt
pip install -r agent3d/requirements-web.txt

# 2. 冒烟测试:声明式 spec → 全套产物(不需 API key)
python -c "import json,sys; sys.path.insert(0,'.'); \
from agent3d.core import spec_to_param, build_scene; \
spec=json.load(open('agent3d/examples/two_storey_house.spec.json',encoding='utf-8')); \
print(build_scene(spec_to_param(spec), './scene_out', name='house', blocks_per_meter=4.0)['stats'])"

# 3. 看模型(不需后端)
python -m http.server 8080 --directory .\scene_out
#   打开 http://localhost:8080/... 用 viewer.html?glb=/model.glb&vox=/voxels.json

# 4. 完整 Web 应用(需 API key 才能走图片→JSON)
$env:ANTHROPIC_API_KEY="sk-ant-..."; .\serve-webapp.ps1   # http://127.0.0.1:8060/
```

**分层自测**（排障顺序）：`spec_to_param` → `stand_trans.step1.load_parametric`（校验）→
`step2.to_bim` → `build_scene`（GLB/体素）→ Web API。哪层报错就查哪层；builder 已保证几何合法，
报错通常是 Spec 里 footprint 越界/重叠或某立面窗数过密。

---

## 11. 扩展指引

| 想做的事 | 改哪里 |
|---|---|
| Building Spec 加字段（如阳台、女儿墙） | `schema/building-spec.schema.json` + `spec_to_param.py` + 必要时 `builder.py` 加 helper |
| 新的可靠几何 helper（如圆形塔楼） | `builder.py` 加方法，产出对应 param 集合项 |
| 新建筑构件（管道层面） | `stand_trans/step1_normalize/schema.py`(集合) + `step2_bim/to_bim.py`(→Element) + `step3_glb/primitives.py`(网格) |
| 材质/方块/配色 | `stand_trans/shared/materials.py`；风格 `stand_trans/shared/styles/registry.py` |
| 视觉模型/提示词 | `core/vision.py::SYSTEM_PROMPT` + `WIDE_SIM_VISION_MODEL`；Skill A 同步改 |
| 查看器增强（如底面网格尺寸、光照） | `webapp/static/viewer.html` |
| 爆炸模块 | 见 §9 |

---

## 12. 关键决策记录（供后续 AI 理解取舍）

- **两层输出**：大模型出高层语义(Layer1)，代码可靠展开(Layer2)。原因：视觉模型难以稳定给出
  度量精确的墙坐标与 host_id 挂载，交给确定性代码保证几何合法与一致。
- **查看器用 voxels.json 而非在浏览器解析 litematic**：`litematic` 是 gzip+NBT+位打包，浏览器
  端解析复杂；改为服务端 `litematic_to_voxels()` 解码成简单 JSON，查看器只 `fetch`+`InstancedMesh`。
  `.litematic` 本体仍保留给爆炸模块。
- **查看器功能**：按需求仅保留 **GLB/体素切换 + 自动旋转**，去掉结构 HUD/构件查询/爆炸。
- **后端 FastAPI、独立端口 8060**：不侵入现有 `backend/server_mr.py`(:8050) 与 `frontend/`(:5173)。
- **两个 Skill 作为第二入口**：Web 面向普通用户（服务端调视觉 API）；Skill 面向带视觉的智能体
  （自身读图），二者共享 core/ 与同一 Building Spec 标准。
```
