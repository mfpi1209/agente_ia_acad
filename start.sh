#!/bin/bash
set -e

echo "=== Iniciando Agente IA + Cockpit API ==="

python agente_ao_vivo_v4.py &
AGENT_PID=$!
echo "Agente iniciado (PID $AGENT_PID)"

uvicorn kb_api:app --host 0.0.0.0 --port 8000 &
API_PID=$!
echo "API Cockpit iniciada (PID $API_PID)"

trap "kill $AGENT_PID $API_PID 2>/dev/null; exit 0" SIGTERM SIGINT

while true; do
    if ! kill -0 $AGENT_PID 2>/dev/null; then
        echo "Agente caiu, reiniciando..."
        python agente_ao_vivo_v4.py &
        AGENT_PID=$!
    fi
    if ! kill -0 $API_PID 2>/dev/null; then
        echo "API caiu, reiniciando..."
        uvicorn kb_api:app --host 0.0.0.0 --port 8000 &
        API_PID=$!
    fi
    sleep 10
done
