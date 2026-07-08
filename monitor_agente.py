"""Monitor local de uptime/backlog do agente.

Acompanha em tempo real se o agente esta de pe, se esta processando e se a fila
esta crescendo. Foco: detectar QUEDA (o maior risco identificado) e BACKLOG.

Uso:
  python monitor_agente.py           # atualiza a cada 30s (loop)
  python monitor_agente.py once      # uma leitura so e sai
  MONITOR_INTERVAL=15 python monitor_agente.py   # intervalo custom (segundos)

Sinais:
  [OK]     tudo normal
  [ALERTA] algo precisa de atencao (fila alta, distribuicao lenta)
  [CRITICO] agente parado / bloqueado (agir agora)
"""
import os, sys, io, time
import requests, psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

INTERVAL = int(os.environ.get('MONITOR_INTERVAL', '30'))
ONCE = len(sys.argv) > 1 and sys.argv[1].lower() == 'once'

DB = dict(host=os.environ['DB_HOST'], port=os.environ['DB_PORT'],
          user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'],
          dbname='log_conversa', connect_timeout=8)
DCZ = 'https://messaging.g1.datacrazy.io/api/messaging/conversations'
H = {'Authorization': f"Bearer {os.environ.get('DCZ_TOKEN','')}",
     'Content-Type': 'application/json',
     'User-Agent': 'Mozilla/5.0 (monitor)', 'Accept': 'application/json'}

# Limiares
HB_ALERTA = 120      # heartbeat parado > 2min = alerta
HB_CRITICO = 300     # > 5min = critico (agente caiu)
FILA_ALERTA = 300    # fila open >= 300 = alerta
FILA_CRITICO = 800   # fila open >= 800 = critico


def fmt_age(sec):
    if sec is None:
        return '?'
    sec = int(sec)
    if sec < 60:
        return f'{sec}s'
    if sec < 3600:
        return f'{sec//60}min{sec%60:02d}s'
    return f'{sec//3600}h{(sec%3600)//60:02d}min'


def queue_count(status):
    try:
        r = requests.get(DCZ, headers=H, params={'limit': 1, 'status': status}, timeout=15)
        if r.status_code == 200:
            return r.json().get('count'), None
        if r.status_code == 403:
            return None, '403-bloqueio'
        return None, f'http{r.status_code}'
    except Exception as e:
        return None, type(e).__name__


def snapshot():
    alerts = []
    now = datetime.now(timezone.utc)
    lines = []
    try:
        c = psycopg2.connect(**DB); cur = c.cursor()
    except Exception as e:
        return [f'[CRITICO] banco inacessivel: {e}'], ['banco off']

    # heartbeat
    cur.execute("SELECT status, pid, last_beat, extra FROM agent_heartbeat ORDER BY last_beat DESC LIMIT 1")
    row = cur.fetchone()
    if row:
        status, pid, lb, extra = row
        age = (now - lb.replace(tzinfo=timezone.utc)).total_seconds()
        flag = 'OK'
        if age > HB_CRITICO:
            flag = 'CRITICO'; alerts.append('AGENTE PARADO (heartbeat)')
        elif age > HB_ALERTA:
            flag = 'ALERTA'; alerts.append('heartbeat atrasado')
        lines.append(f'  [{flag}] heartbeat: {status} pid={pid} ha {fmt_age(age)}  ({extra})')
        if extra and 'fetched=0' in str(extra):
            alerts.append('fetched=0 (possivel bloqueio/fila vazia)')
    else:
        lines.append('  [CRITICO] sem heartbeat'); alerts.append('sem heartbeat')

    # interacoes / atividade
    cur.execute("SELECT count(*) FROM ia_interaction_log WHERE created_at > now()-interval '5 min'")
    i5 = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM ia_interaction_log WHERE created_at > now()-interval '30 min'")
    i30 = cur.fetchone()[0]
    cur.execute("SELECT now()-max(created_at) FROM ia_interaction_log")
    ia_age = cur.fetchone()[0]
    ia_age_s = ia_age.total_seconds() if ia_age else None
    lines.append(f'  interacoes: {i5} (5min) / {i30} (30min) | ultima ha {fmt_age(ia_age_s)}')

    # ultima msg enviada + distribuicoes recentes
    cur.execute("SELECT now()-max(sent_at) FROM agent_sent_signatures")
    snt = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM agent_sent_signatures
                   WHERE sent_at > now()-interval '15 min' AND signature LIKE 'dist_%'""")
    dist15 = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM agent_sent_signatures
                   WHERE sent_at > now()-interval '15 min' AND signature='ret_ia'""")
    ret15 = cur.fetchone()[0]
    lines.append(f'  envio: ultima ha {fmt_age(snt.total_seconds() if snt else None)} | '
                 f'distribuicoes(15min)={dist15} | retencao(15min)={ret15}')
    cur.close(); c.close()

    # fila DataCrazy
    op, e1 = queue_count('open')
    un, e2 = queue_count('unstarted')
    if e1 == '403-bloqueio' or e2 == '403-bloqueio':
        alerts.append('DataCrazy 403 (Cloudflare bloqueando)')
        lines.append('  [CRITICO] fila: DataCrazy respondendo 403 (bloqueio Cloudflare)')
    else:
        flag = 'OK'
        opv = op if op is not None else -1
        if opv >= FILA_CRITICO:
            flag = 'CRITICO'; alerts.append(f'fila alta ({op})')
        elif opv >= FILA_ALERTA:
            flag = 'ALERTA'; alerts.append(f'fila subindo ({op})')
        lines.append(f'  [{flag}] fila DataCrazy: open={op if op is not None else e1} '
                     f'unstarted={un if un is not None else e2}')

    return lines, alerts


def main():
    while True:
        ts = datetime.now().strftime('%H:%M:%S')
        try:
            lines, alerts = snapshot()
        except Exception as e:
            lines, alerts = [f'  [ERRO] {type(e).__name__}: {e}'], ['erro na leitura']
        header = '=' * 62
        veredito = '[OK] tudo normal' if not alerts else '[!] ' + ' | '.join(alerts)
        print(header)
        print(f'MONITOR AGENTE  {ts}   ->  {veredito}')
        print(header)
        for ln in lines:
            print(ln)
        print()
        if ONCE:
            break
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
