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
DB_CONNECTED=0
for i in $(seq 1 15); do
    DB_RESULT=$(python -c "
import psycopg2
try:
    conn = psycopg2.connect(host='$DB_HOST', port=${DB_PORT:-5432}, user='$DB_USER', password='$DB_PASSWORD', dbname='$DB_NAME', connect_timeout=5)
    cur = conn.cursor()
    cur.execute('SELECT version()')
    ver = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f'OK: {ver}')
except Exception as e:
    print(f'ERRO: {e}')
" 2>&1)
    echo "  Tentativa $i/15: $DB_RESULT"
    if echo "$DB_RESULT" | grep -q "^OK:"; then
        DB_CONNECTED=1
        break
    fi
    sleep 2
done

if [ "$DB_CONNECTED" = "0" ]; then
    echo "AVISO: DB não conectou após 30s! A API vai iniciar mas endpoints podem falhar."
fi

python agente_ao_vivo_v4.py &
AGENT_PID=$!
echo "Agente iniciado (PID $AGENT_PID)"

sleep 3

uvicorn kb_api:app --host 0.0.0.0 --port 8000 --log-level info &
API_PID=$!
echo "API Cockpit iniciada (PID $API_PID)"

sleep 5
echo "Executando health check da API..."
HEALTH_RESULT=$(curl -s --max-time 10 http://localhost:8000/api/health 2>&1)
HEALTH_CODE=$?
if [ "$HEALTH_CODE" = "0" ]; then
    echo "API health check: $HEALTH_RESULT"
else
    echo "API health check FALHOU (curl exit=$HEALTH_CODE): $HEALTH_RESULT"
    if kill -0 $API_PID 2>/dev/null; then
        echo "  Processo $API_PID está vivo, pode estar iniciando ainda"
    else
        echo "  Processo $API_PID morreu!"
    fi
fi

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
