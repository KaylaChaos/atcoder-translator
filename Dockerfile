FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ARG BUILD_ID=local

ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV IMAGE_BUILD_ID=${BUILD_ID}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir playwright==1.48.0

COPY index.py container_server.py watermark.png ./

EXPOSE 8000

CMD ["python", "/app/container_server.py"]
