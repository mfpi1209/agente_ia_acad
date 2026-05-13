"""
Dashboard Visual - Agente IA Acadêmico
Servidor FastAPI que serve o painel de métricas e interações do agente.
"""

import os, json
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import psycopg2
import requests
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 5432)),
    'user': os.environ.get('DB_USER', 'postgres'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'dbname': os.environ.get('DB_NAME', 'log_conversa'),
}

DCZ_MSG_BASE = 'https://messaging.g1.datacrazy.io'
INSTANCE_ID = '692a13008721fc1c4000859f'

app = FastAPI(title="Dashboard Agente IA")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


@app.get("/api/stats")
def api_stats(days: int = Query(7, ge=1, le=90)):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    cur.execute("SELECT COUNT(*) as total FROM interaction_summary WHERE created_at >= %s", (since,))
    total = cur.fetchone()['total']

    cur.execute("SELECT tema, COUNT(*) as cnt FROM interaction_summary WHERE created_at >= %s GROUP BY tema ORDER BY cnt DESC", (since,))
    temas = cur.fetchall()

    cur.execute("SELECT sentimento, COUNT(*) as cnt FROM interaction_summary WHERE created_at >= %s GROUP BY sentimento ORDER BY cnt DESC", (since,))
    sentimentos = cur.fetchall()

    cur.execute("SELECT nps_implicito, COUNT(*) as cnt FROM interaction_summary WHERE created_at >= %s AND nps_implicito IS NOT NULL GROUP BY nps_implicito ORDER BY nps_implicito", (since,))
    nps = cur.fetchall()

    cur.execute("SELECT resolvido, COUNT(*) as cnt FROM interaction_summary WHERE created_at >= %s GROUP BY resolvido ORDER BY cnt DESC", (since,))
    resolvido = cur.fetchall()

    cur.execute("SELECT DATE(created_at) as d, COUNT(*) as cnt FROM interaction_summary WHERE created_at >= %s GROUP BY d ORDER BY d", (since,))
    por_dia = [{'d': r['d'].isoformat(), 'cnt': r['cnt']} for r in cur.fetchall()]

    cur.execute("SELECT subtema, COUNT(*) as cnt FROM interaction_summary WHERE created_at >= %s AND subtema IS NOT NULL GROUP BY subtema ORDER BY cnt DESC LIMIT 15", (since,))
    subtemas = cur.fetchall()

    cur.execute("SELECT ROUND(AVG(nps_implicito)::numeric, 1) as avg_nps FROM interaction_summary WHERE created_at >= %s AND nps_implicito IS NOT NULL", (since,))
    avg_nps = cur.fetchone()['avg_nps']

    cur.execute("SELECT COUNT(*) as cnt FROM interaction_summary WHERE created_at >= %s AND resolvido IN ('sim', 'parcial')", (since,))
    resolvidos = cur.fetchone()['cnt']
    taxa_res = round(resolvidos / total * 100, 1) if total > 0 else 0

    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE nps_implicito >= 9) as promotores,
               COUNT(*) FILTER (WHERE nps_implicito >= 7 AND nps_implicito <= 8) as neutros,
               COUNT(*) FILTER (WHERE nps_implicito <= 6) as detratores,
               COUNT(*) FILTER (WHERE nps_implicito IS NOT NULL) as total_nps
        FROM interaction_summary WHERE created_at >= %s
    """, (since,))
    nps_row = cur.fetchone()
    nps_score = 0
    if nps_row['total_nps'] > 0:
        nps_score = round(((nps_row['promotores'] - nps_row['detratores']) / nps_row['total_nps']) * 100, 1)

    # Hora do dia
    cur.execute("""
        SELECT EXTRACT(HOUR FROM created_at)::int as h, COUNT(*) as cnt
        FROM interaction_summary WHERE created_at >= %s
        GROUP BY h ORDER BY h
    """, (since,))
    por_hora = cur.fetchall()

    conn.close()
    return {
        'total': total, 'temas': temas, 'sentimentos': sentimentos, 'nps': nps,
        'resolvido': resolvido, 'por_dia': por_dia, 'subtemas': subtemas,
        'avg_nps': float(avg_nps) if avg_nps else 0, 'taxa_resolucao': taxa_res,
        'nps_score': nps_score, 'por_hora': por_hora,
        'promotores': nps_row['promotores'], 'neutros': nps_row['neutros'], 'detratores': nps_row['detratores'],
    }


@app.get("/api/recent")
def api_recent(limit: int = Query(30, ge=1, le=200), page: int = Query(1, ge=1),
               tema: str = Query(None), sentimento: str = Query(None), search: str = Query(None)):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    where = ["1=1"]
    params = []
    if tema:
        where.append("tema = %s")
        params.append(tema)
    if sentimento:
        where.append("sentimento = %s")
        params.append(sentimento)
    if search:
        where.append("(student_name ILIKE %s OR pergunta_aluno ILIKE %s OR phone ILIKE %s)")
        params.extend([f'%{search}%'] * 3)

    w = " AND ".join(where)
    offset = (page - 1) * limit
    cur.execute(f"SELECT COUNT(*) as cnt FROM interaction_summary WHERE {w}", params)
    total = cur.fetchone()['cnt']

    cur.execute(f"""
        SELECT id, phone, student_name, tema, subtema, sentimento, resolvido,
               nps_implicito, pergunta_aluno, resposta_agente, avaliacao, conv_id,
               created_at
        FROM interaction_summary WHERE {w}
        ORDER BY id DESC LIMIT %s OFFSET %s
    """, params + [limit, offset])
    rows = cur.fetchall()
    for r in rows:
        if r.get('created_at'):
            r['created_at'] = r['created_at'].isoformat()
    conn.close()
    return {'total': total, 'page': page, 'limit': limit, 'rows': rows}


@app.post("/api/avaliar/{record_id}")
def api_avaliar(record_id: int, avaliacao: str = Query(...)):
    if avaliacao not in ('correta', 'incorreta', None, ''):
        return JSONResponse({"error": "Valor inválido"}, 400)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE interaction_summary SET avaliacao = %s WHERE id = %s", (avaliacao or None, record_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/agent-status")
def api_agent_status():
    """Retorna status do agente verificando o processo via Cockpit + heartbeat DB."""
    process_online = False
    cockpit_pid = None
    try:
        r = requests.get(f'{COCKPIT_BASE}/api/agent/live/status', timeout=3)
        if r.status_code == 200:
            data = r.json()
            process_online = data.get('running', False)
            cockpit_pid = data.get('pid')
    except Exception:
        pass

    hb_status = 'offline'
    hb_extra = ''
    hb_seconds = 9999
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT status, pid, last_beat, extra,
                   EXTRACT(EPOCH FROM (NOW() - last_beat)) as seconds_ago
            FROM agent_heartbeat WHERE id = 1
        """)
        row = cur.fetchone()
        conn.close()
        if row:
            hb_seconds = float(row['seconds_ago'] or 9999)
            hb_status = row['status']
            hb_extra = row['extra'] or ''
    except Exception:
        pass

    is_online = process_online or (hb_status == 'online' and hb_seconds < 120)
    return {
        'status': 'online' if is_online else 'offline',
        'pid': cockpit_pid,
        'seconds_ago': round(hb_seconds),
        'extra': hb_extra,
    }


COCKPIT_BASE = os.environ.get('COCKPIT_BASE_URL', 'http://localhost:8000')


@app.post("/api/agent/start")
def api_agent_start():
    """Proxy para iniciar o agente via Cockpit API."""
    try:
        r = requests.post(f'{COCKPIT_BASE}/api/agent/live/start', timeout=30)
        return r.json()
    except Exception as e:
        return {'ok': False, 'msg': str(e)}


@app.post("/api/agent/stop")
def api_agent_stop():
    """Proxy para parar o agente via Cockpit API."""
    try:
        r = requests.post(f'{COCKPIT_BASE}/api/agent/live/stop', timeout=15)
        return r.json()
    except Exception as e:
        return {'ok': False, 'msg': str(e)}


@app.get("/api/alerts")
def api_alerts():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, phone, student_name, tema, sentimento, nps_implicito,
               pergunta_aluno, resposta_agente, conv_id, created_at
        FROM interaction_summary
        WHERE (nps_implicito <= 5 OR sentimento = 'frustrado')
          AND created_at >= NOW() - INTERVAL '24 hours'
        ORDER BY id DESC LIMIT 20
    """)
    rows = cur.fetchall()
    for r in rows:
        if r.get('created_at'):
            r['created_at'] = r['created_at'].isoformat()
    conn.close()
    return rows


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    with open(os.path.join(os.path.dirname(__file__), 'dashboard.html'), 'r', encoding='utf-8') as f:
        return f.read()


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8050, log_level='info')
