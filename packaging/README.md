# Windows 一键部署打包说明

面向「没有 WSL、没有 Docker、也不用自己装 Python」的 Windows 客户。  
本目录脚本会把 **嵌入式 Python + 依赖 + 应用代码** 打成便携 Zip，并可选生成 `Setup.exe`。

---

## 1. 交付物一览

| 文件 | 给谁 | 说明 |
|------|------|------|
| `dist/Agent3D-Windows-x64.zip` | **客户（推荐）** | 解压后双击「开始 Agent3D.bat」即可 |
| `dist/Agent3D-Setup.exe` | 客户（可选） | 安装向导；装完从开始菜单启动 |
| `packaging/` 下脚本与启动器 | 开发者 / 仓库 | 可提交到 Git；**不要**提交 zip / exe |

客户机器要求：

- Windows 10 / 11（64 位）
- 不需要 WSL、Docker、预装 Python
- 图片→3D 需要能访问外网 API（OpenRouter / Anthropic 等）
- 若缺 DLL，安装 [VC++ 运行库 x64](https://aka.ms/vs/17/release/vc_redist.x64.exe)

---

## 2. 目录与脚本

```
packaging/
├── README.md              ← 本文档
├── VERSION                ← 版本号（写入安装包 VERSION.txt）
├── build.ps1              ← 主构建：embed Python → 装依赖 → zip → 可选 Setup.exe
├── publish-release.ps1    ← 把 dist 产物挂到 GitHub Release
├── Agent3D.iss            ← Inno Setup 脚本（生成 Setup.exe）
├── launcher/              ← 打进安装包的启动器与用户说明
│   ├── launch.py / stop.py
│   ├── Start-Agent3D.bat / Stop-Agent3D.bat
│   ├── 开始 Agent3D.bat / 停止 Agent3D.bat
│   └── README.txt
├── _cache/                ← 下载缓存（gitignore）
└── _stage/                ← 组装中的安装树（gitignore）

仓库根目录 dist/           ← 最终 zip / Setup.exe（gitignore）
```

---

## 3. 构建前准备（开发机）

1. Windows 64 位，已安装 PowerShell 5.1+
2. 能访问外网（下载 Python embeddable、pip 包；可选 Inno Setup）
3. 在仓库根目录操作（路径含 `trans_model`）
4. （可选）安装 [GitHub CLI](https://cli.github.com/) 并 `gh auth login`，用于一键发 Release
5. （可选）安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)；没有也可只出 Zip

改版本号：编辑 `packaging/VERSION`（例如 `0.1.0` / `1.0.0`）。

---

## 4. 完整打包流程

### 步骤 A：构建

在仓库**根目录**执行：

```powershell
# 完整构建（首次较慢：下载 embed Python + pip 装依赖）
.\packaging\build.ps1

# 同时自动静默安装 Inno Setup，并生成 Setup.exe
.\packaging\build.ps1 -InstallInno

# 已有 Inno，但这次只想要 Zip
.\packaging\build.ps1 -SkipInno

# 清理旧 stage/dist 再打
.\packaging\build.ps1 -Clean

# 跳过 pip 重装（仅当你确认 runtime 依赖未变时）
.\packaging\build.ps1 -SkipDeps
```

`build.ps1` 实际做了什么：

1. 下载并解压 **Python embeddable** 到 `packaging/_stage/Agent3D/runtime/`
2. 启用 `site-packages`，安装 **pip**
3. `pip install -r requirements.txt` + `agent3d/requirements-web.txt` 进 runtime（禁止写用户目录）
4. 拷贝 `agent3d/`、`stand_trans/`、启动器、`.env.example`、用户 `README.txt`
5. （可选）打入示例模型：默认把若干 `artifacts_web\<id>` 拷进安装包，便于客户直接预览
6. 冒烟导入：`ifcopenshell` / `fastapi` / `trimesh` 等必须来自 **runtime**，不能来自开发机 site-packages
7. 打出 `dist/Agent3D-Windows-x64.zip`
8. 若检测到 `ISCC.exe`（或 `-InstallInno`），编译 `Agent3D.iss` → `dist/Agent3D-Setup.exe`

自定义示例模型：

```powershell
.\packaging\build.ps1 -SampleSceneIds @("d461c16a8539","a37b10fc5003")
# 不打包任何示例：
.\packaging\build.ps1 -SampleSceneIds @()
```

成功后应看到类似：

```
Zip ready: ...\dist\Agent3D-Windows-x64.zip (约 130 MB)
Setup ready: ...\dist\Agent3D-Setup.exe (约 77 MB)   # 若启用了 Inno
```

### 步骤 B：本地冒烟（建议）

1. 解压 `dist\Agent3D-Windows-x64.zip` 到临时目录（不要在 `_stage` 里测）
2. 双击 **开始 Agent3D.bat**
3. 浏览器应打开 `http://127.0.0.1:8060/`
4. 右上角「模型设置」填 API Key，试一张图或命令行几何冒烟
5. 双击 **停止 Agent3D.bat** 或关控制台窗口

### 步骤 C：发给客户 / 挂到 GitHub

**不要把 zip、exe `git commit` 进仓库**（体积大，且已被 ignore）。

#### 方式 1：网页手动挂 Release（你现在用的方式）

1. 打开仓库 → **Releases** → **Draft a new release**
2. **Choose a tag**：输入 `v1.0`（或 `v0.1.0`）→ Create new tag on publish  
   Target 一般选 `main`
3. 填写 Title / 说明（例如「第一次发布」）
4. 在 **Attach binaries by dropping them here or selecting them** 区域：
   - 拖入 `dist\Agent3D-Windows-x64.zip`
   - 拖入 `dist\Agent3D-Setup.exe`（若有）
5. 等上传完成 → **Publish release**
6. 把 Release 页链接发给客户即可

#### 方式 2：命令行一键上传

```powershell
.\packaging\publish-release.ps1 -Tag v1.0.0
# 或自定义说明：
.\packaging\publish-release.ps1 -Tag v1.0.0 -Notes "首个 Windows 客户交付包"
```

---

## 5. 客户怎么用

### Zip 便携版

1. 下载并解压 `Agent3D-Windows-x64.zip`
2. 双击 **开始 Agent3D.bat**（或 `Start-Agent3D.bat`）
3. 浏览器打开后，在「模型设置」填写 API Key / Base URL / 模型名  
   （也可复制 `.env.example` → `.env` 后编辑）
4. 停止：关黑色窗口，或双击 **停止 Agent3D.bat**

### Setup.exe

1. 双击安装，按向导完成（默认装到用户本地 AppData，无需管理员）
2. 从开始菜单 / 桌面启动 Agent3D
3. 其余同 Zip

数据位置（安装目录内）：

- 生成模型：`artifacts_web\`
- 网页设置：`agent3d_settings.json`（可能含 Key，勿外传）

---

## 6. 哪些进 Git、哪些必须 ignore

| 路径 | 是否提交 |
|------|----------|
| `packaging/build.ps1`、`Agent3D.iss`、`publish-release.ps1`、`VERSION`、`launcher/`、`README.md` | ✅ 提交 |
| `packaging/_cache/`、`_stage/`、`_trash_*/`、`_stage_trash_*/` | ❌ ignore |
| `dist/`、`*.zip`、`Agent3D-Setup.exe` | ❌ ignore |
| `.env`、`agent3d_settings.json`、`agent3d.pid` | ❌ ignore |

仓库根 `.gitignore` 中对应段落：

```gitignore
# Windows packaging (scripts under packaging/ are tracked; build outputs are not)
packaging/_cache/
packaging/_stage/
packaging/_trash_*/
packaging/_stage_trash_*/
dist/
*.zip
Agent3D-Setup.exe
agent3d.pid
```

大文件请走 **GitHub Releases 附件**，不要塞进 Git 历史。

---

## 7. 常见问题

**Q: 只有 Zip、没有 Setup.exe？**  
A: 本机未装 Inno Setup。安装后重跑 `.\packaging\build.ps1`，或加 `-InstallInno`。

**Q: 冒烟 import 失败 / 提示从用户目录加载包？**  
A: 构建脚本已设 `PYTHONNOUSERSITE=1`。用 `-Clean` 重打；确认不要把系统 Python 的 site-packages 拷进 zip。

**Q: 客户提示缺 DLL？**  
A: 让客户安装 [VC++ Redistributable x64](https://aka.ms/vs/17/release/vc_redist.x64.exe)。

**Q: 端口 8060 被占用？**  
A: 先运行「停止 Agent3D.bat」，或设置环境变量 `AGENT3D_PORT=8070` 后再启动。

**Q: 能出几何、读图失败？**  
A: 检查 API Key / Base URL；纯 Spec→3D 不需要 Key。

**Q: 想改默认端口 / 绑定地址？**  
A: 启动前设置 `AGENT3D_PORT`、`AGENT3D_HOST`（见 `launcher/launch.py`）。

---

## 8. 建议发布检查清单

- [ ] 已更新 `packaging/VERSION`
- [ ] `.\packaging\build.ps1`（或带 `-InstallInno`）成功
- [ ] `dist\Agent3D-Windows-x64.zip` 存在且体积合理（约百 MB 级）
- [ ] （可选）`dist\Agent3D-Setup.exe` 存在
- [ ] 解压 Zip 后本地能启动并打开网页
- [ ] 未把 `dist/`、zip、exe 加入 git commit
- [ ] 已通过 Release 网页或 `publish-release.ps1` 挂上附件
- [ ] 把 Release 链接发给客户，并说明填 API Key
