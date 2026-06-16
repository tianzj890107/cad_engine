# 图纸解析与生成平台 —— 应用镜像
# 含 CadQuery(OpenCASCADE),故需若干 OpenGL/X 运行时库。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data

WORKDIR /app

# OCCT/OpenGL 运行时依赖(几何内核需要)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libxrender1 libxext6 libsm6 \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖(利用层缓存)。私有化生产额外装 Postgres 驱动 + S3 客户端。
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && pip install "psycopg[binary]>=3.2" "boto3>=1.34"

COPY backend ./backend
COPY frontend ./frontend

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
