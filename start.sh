#!/bin/bash
set -e

echo "=== Iniciando Agente IA + Cockpit API ==="
echo "DB_HOST=${DB_HOST:-NÃO DEFINIDO}"
echo "DB_PORT=${DB_PORT:-NÃO DEFINIDO}"
echo "DB_USER=${DB_USER:-NÃO DEFINIDO}"
echo "DB_NAME=${DB_NAME:-NÃO DEFINIDO}"
echo "OPENAI_API_KEY=${OPENAI_API_KEY:+DEFINIDO (${#OPENAI_API_KEY} chars)}"
echo "DCZ_TOKEN=${DCZ_TOKEN:+DEFINIDO (${#DCZ_TOKEN} chars)}"
echo "META_TOKEN=${META_TOKEN:+DEFINIDO (${#META_TOKEN} chars)}"

# Aguardar DB ficar acessível (até 30s)
echo "Testando conexão com DB ($DB_HOST:$DB_PORT)..."
for i in $(seq 1 15); do
    if python -c "import psycopg2; psycopg2.connect(host='$DB_HOST', port=$DB_PORT, user='$DB_USER', password='$DB_PASSWORD', dbname='$DB_NAME'); print('DB OK')" 2>/dev/null; then
        break
    fi
    echo "  Aguardando DB... ($i/15)"
    sleep 2
done

python agente_ao_vivo_v4.py &
AGENT_PID=$!
echo "Agente iniciado (PID $AGENT_PID)"

sleep 3

uvicorn kb_api:app --host 0.0.0.0 --port 8000 &
API_PID=$!
echo "API Cockpit iniciada (PID $API_PID)"

trap "kill $AGENT_PID $API_PID 2>/dev/null; exit 0" SIGTERM SIGINT

while true; do
    if ! kill -0 $AGENT_PID 2>/dev/null; then
        echo "$(date '+%H:%M:%S') Agente caiu, reiniciando..."
        python agente_ao_vivo_v4.py &
        AGENT_PID=$!
    fi
    if ! kill -0 $API_PID 2>/dev/null; then
        echo "$(date '+%H:%M:%S') API caiu, reiniciando..."
        uvicorn kb_api:app --host 0.0.0.0 --port 8000 &
        API_PID=$!
    fi
    sleep 10
done
