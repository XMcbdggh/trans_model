# Start the agent3d web app (image -> building JSON -> 3D model, viewable in the browser).
# Default http://127.0.0.1:8060/ . Requires ANTHROPIC_API_KEY for the vision step.
param([int]$Port = 8060, [string]$BindHost = "127.0.0.1")
python -m uvicorn agent3d.webapp.server:app --host $BindHost --port $Port
