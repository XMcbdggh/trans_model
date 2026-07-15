Agent3D 用户指南（Windows）
===========================

一、安装方式（二选一）

  A) Setup.exe 安装包
     - 双击 Setup.exe，按向导安装
     - 从开始菜单 / 桌面启动「Agent3D」

  B) 便携 Zip（无需安装）
     - 解压 Agent3D-Windows-x64.zip 到任意文件夹
     - 双击「开始 Agent3D.bat」（或 Start-Agent3D.bat）

二、首次使用

  1) 浏览器会自动打开 http://127.0.0.1:8060/
  2) 点击右上角「模型设置」，填写：
     - API Key（图片→3D 必填）
     - Base URL（OpenRouter 示例：https://openrouter.ai/api ）
     - 模型名称
  3) 也可将 .env.example 复制为 .env 后编辑

三、停止服务

  - 关闭黑色控制台窗口，或双击「停止 Agent3D.bat」

四、运行环境

  - Windows 10 / 11（64 位）
  - 不需要 WSL、Docker，也不用预先安装 Python
  - 视觉读图需要能访问外网 API
  - 若提示缺少 DLL，请安装 VC++ 运行库：
    https://aka.ms/vs/17/release/vc_redist.x64.exe

五、数据位置

  - 生成模型：安装目录\artifacts_web\
  - 网页设置：安装目录\agent3d_settings.json
  - 请勿分享 .env / agent3d_settings.json（可能含 API Key）

  安装包内已带若干示例模型（artifacts_web\<场景ID>\），启动后可直接预览：
    http://127.0.0.1:8060/viewer.html?scene=<场景ID>
  场景ID 即 artifacts_web 下的文件夹名。

六、常见问题

  Q: 端口被占用？
  A: 先运行「停止 Agent3D.bat」，或设置环境变量 AGENT3D_PORT=8070 后重开。

  Q: 能出几何、但读图失败？
  A: 检查 API Key / Base URL 是否正确。

===========================
