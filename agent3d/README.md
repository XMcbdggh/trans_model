# agent3d

图片 → 建筑 JSON → 3D 模型（GLB + litematic）→ three.js 查看器。
建在现有 `stand_trans` 管道之上，两个交付入口：**Web 应用**（普通用户）与**两个 Skill**（智能体）。

完整设计 / 复现 / 扩展文档见 **[`../docs/AGENT_3D_PIPELINE.md`](../docs/AGENT_3D_PIPELINE.md)**。

## 快速开始

```powershell
pip install -r ../requirements.txt
pip install -r requirements-web.txt

# 冒烟测试(无需 API key):spec → GLB + litematic + voxels.json
python -c "import json,sys; sys.path.insert(0,'..'); \
from agent3d.core import spec_to_param, build_scene; \
spec=json.load(open('examples/two_storey_house.spec.json',encoding='utf-8')); \
print(build_scene(spec_to_param(spec), './scene_out', name='house')['stats'])"

# Web 应用(图片→JSON 需 ANTHROPIC_API_KEY)
$env:ANTHROPIC_API_KEY="sk-ant-..."
python -m uvicorn agent3d.webapp.server:app --host 127.0.0.1 --port 8060   # 从仓库根运行
```

- `core/` 引擎：`builder.py`（SceneBuilder）· `spec_to_param.py` · `pipeline_runner.py` · `vision.py`
- `schema/building-spec.schema.json`：智能体 JSON 输出标准（Layer 1）
- `webapp/`：FastAPI + 上传页 + 单文件 three.js 查看器
- `skills/`：Skill A（图片→JSON）· Skill B（JSON→3D）
- `examples/`：可运行示例（spec + 展开后的 param）
