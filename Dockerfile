FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl procps && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agente_ao_vivo_v4.py .
COPY kb_api.py .
COPY kb_admin.html .
COPY start.sh .
RUN chmod +x start.sh

RUN mkdir -p /app/media

EXPOSE 8000

CMD ["./start.sh"]
