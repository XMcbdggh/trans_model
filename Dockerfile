# agent3d API + web service (single container).
# Build:  docker build -t agent3d .
# Run:    docker run -p 8060:8060 -v agent3d_data:/data -e AGENT3D_SCENES=/data agent3d
FROM python:3.10-slim

# Runtime libs some scientific wheels expect (scipy/OpenMP, trimesh's optional GL import).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT3D_SCENES=/data

WORKDIR /app

# Install deps first (better layer caching) -- core pipeline + web/API extras.
COPY requirements.txt ./
COPY agent3d/requirements-web.txt ./agent3d/requirements-web.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install -r agent3d/requirements-web.txt

# App code.
COPY . .

# Persistent artifact store (mount a named volume / host dir here).
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8060

CMD ["python", "-m", "uvicorn", "agent3d.webapp.server:app", "--host", "0.0.0.0", "--port", "8060"]
