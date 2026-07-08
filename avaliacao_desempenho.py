"""Relatorio-baseline de desempenho: era ANTIGA (bot de menu) vs ERA DO AGENTE (IA).

Fase 0 do plano de avaliacao. Fontes: banco log_conversa (mensagens + acoes da IA)
e disparos (funil de retencao).

DESCOBERTA-CHAVE que define a metodologia:
- A era "antiga" NAO era manual pura: tinha um bot de menu/saudacao (DataCrazy) que
  respondia instantaneamente ("Bem vindo ao Suporte", "Veja as opcoes"). A conta
  compartilhada 'Suporte'/'Administrador' = AUTOMACAO nas duas eras.
- Por isso, comparar "tempo ate 1a resposta" cru e injusto (auto-saudacao x resposta
  real). A metrica JUSTA entre eras e o TEMPO ATE UM HUMANO DE VERDADE (nome proprio).

Metodologia:
- Humano = atendente com nome proprio (nao 'Suporte'/'Administrador'/vazio).
- Automacao = 'Suporte'/'Administrador'/vazio (bot antigo ou IA nova).
- FRT-humano: do 1o CLIENTE ao 1o SUPORTE de humano na conversa.
- Autonomia da IA: conversas que a IA tocou e NAO precisaram de humano.
- Corte de producao configuravel (CUTOFF). Default 2026-05-20.

Uso:  python avaliacao_desempenho.py
"""
import os, io, sys
import psycopg2
from dotenv import load_dotenv
load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

CUTOFF = os.environ.get('AGENT_PROD_CUTOFF', '2026-05-20')  # inicio da producao plena
HIST_START = '2025-11-01'

BASE = dict(host=os.environ['DB_HOST'], port=os.environ['DB_PORT'],
            user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], connect_timeout=10)


def fmt(s):
    if s is None:
        return '-'
    s = float(s)
    if s < 60:
        return f'{s:.0f}s'
    if s < 3600:
        return f'{s/60:.1f}min'
    if s < 86400:
        return f'{s/3600:.1f}h'
    return f'{s/86400:.1f}d'


def hr(t):
    print('\n' + '=' * 74)
    print(t)
    print('=' * 74)


def main():
    c = psycopg2.connect(dbname='log_conversa', **BASE)
    cur = c.cursor()

    print(f'CORTE DE PRODUCAO (manual -> agente): {CUTOFF}')
    print(f'Janela historica analisada: {HIST_START} .. hoje')

    # SQL: humano = atendente com nome proprio (nao a conta de automacao)
    IS_HUMAN = "COALESCE(NULLIF(TRIM(atendente_nome),''),'Suporte') NOT IN ('Suporte','Administrador')"

    # ---------- 1) TEMPO ATE UM HUMANO REAL por mes (metrica justa) ----------
    cur.execute(f"""
    WITH firsts AS (
      SELECT conversation_id, MIN(message_at) AS first_client
      FROM log_conversa WHERE message_at >= %s AND sender_type='CLIENTE'
      GROUP BY conversation_id
    ),
    fh AS (
      SELECT DISTINCT ON (l.conversation_id)
             f.first_client, l.message_at AS human_at
      FROM firsts f
      JOIN log_conversa l ON l.conversation_id=f.conversation_id
       AND l.sender_type='SUPORTE' AND l.message_at > f.first_client AND {IS_HUMAN}
      ORDER BY l.conversation_id, l.message_at
    ),
    tot AS (  -- total de conversas iniciadas por cliente no mes
      SELECT to_char(first_client,'YYYY-MM') ym, count(*) n FROM firsts
      WHERE first_client IS NOT NULL GROUP BY 1
    ),
    hum AS (
      SELECT to_char(first_client,'YYYY-MM') ym, count(*) n,
             round(percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM(human_at-first_client)))::numeric,0) med,
             round(percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM(human_at-first_client)))::numeric,0) p90,
             round((100.0*sum((EXTRACT(EPOCH FROM(human_at-first_client))<=300)::int)/count(*))::numeric,1) p5
      FROM fh GROUP BY 1
    )
    SELECT t.ym, t.n AS total, COALESCE(h.n,0) AS chegou_humano,
           round((100.0*COALESCE(h.n,0)/t.n)::numeric,1) AS pct_humano,
           h.med, h.p90, h.p5
    FROM tot t LEFT JOIN hum h USING (ym) ORDER BY t.ym;
    """, (HIST_START,))
    hr('1) TEMPO ATE UM HUMANO REAL responder, por mes (metrica justa entre eras)')
    print(f"{'mes':8}| {'convs':>7} | {'p/humano':>9} | {'%humano':>7} | {'mediana':>8} | {'p90':>8} | {'<=5min':>7}")
    print('-' * 74)
    for ym, tot_, ch, pct, med, p90, p5 in cur.fetchall():
        print(f'{ym:8}| {tot_:>7} | {ch:>9} | {pct:>6}% | {fmt(med):>8} | {fmt(p90):>8} | {str(p5) + "%" if p5 is not None else "-":>7}')

    # ---------- 2) Resumo: ANTIGA vs AGENTE ----------
    cur.execute(f"""
    WITH firsts AS (
      SELECT conversation_id, MIN(message_at) AS first_client
      FROM log_conversa WHERE message_at >= %s AND sender_type='CLIENTE'
      GROUP BY conversation_id
    ),
    fh AS (
      SELECT DISTINCT ON (l.conversation_id)
             f.first_client, l.message_at AS human_at
      FROM firsts f
      JOIN log_conversa l ON l.conversation_id=f.conversation_id
       AND l.sender_type='SUPORTE' AND l.message_at > f.first_client AND {IS_HUMAN}
      ORDER BY l.conversation_id, l.message_at
    )
    SELECT CASE WHEN first_client>=%s THEN 'AGENTE' ELSE 'ANTIGA' END era,
           count(*) n,
           round(percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM(human_at-first_client)))::numeric,0) med,
           round(percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM(human_at-first_client)))::numeric,0) p90,
           round((100.0*sum((EXTRACT(EPOCH FROM(human_at-first_client))<=300)::int)/count(*))::numeric,1) p5
    FROM fh GROUP BY 1 ORDER BY 1;
    """, (HIST_START, CUTOFF))
    hr('2) TEMPO ATE HUMANO: era ANTIGA vs AGENTE (conversas que chegaram a humano)')
    print(f"{'era':8}| {'convs':>7} | {'mediana':>8} | {'p90':>8} | {'<=5min':>7}")
    print('-' * 55)
    for era, n, med, p90, p5 in cur.fetchall():
        print(f'{era:8}| {n:>7} | {fmt(med):>8} | {fmt(p90):>8} | {p5:>6}%')

    # ---------- 3) Deflection / autonomia da IA (so era agente) ----------
    cur.execute(f"""
    WITH conv AS (
      SELECT conversation_id,
             bool_or(sender_type='SUPORTE' AND COALESCE(NULLIF(TRIM(atendente_nome),''),'Suporte')
                     IN ('Suporte','Administrador')) AS teve_ia,
             bool_or(sender_type='SUPORTE' AND COALESCE(NULLIF(TRIM(atendente_nome),''),'Suporte')
                     NOT IN ('Suporte','Administrador')) AS teve_hum,
             MIN(message_at) AS ini
      FROM log_conversa WHERE message_at >= %s GROUP BY conversation_id
    )
    SELECT count(*) FILTER (WHERE teve_ia) tot_ia,
           count(*) FILTER (WHERE teve_ia AND NOT teve_hum) so_ia,
           count(*) FILTER (WHERE teve_ia AND teve_hum) ia_e_hum
    FROM conv WHERE ini >= %s;
    """, (HIST_START, CUTOFF))
    tot_ia, so_ia, ia_e_hum = cur.fetchone()
    hr('3) AUTONOMIA DA IA (era agente): conversas resolvidas sem humano')
    if tot_ia:
        print(f'  Conversas que a IA respondeu:        {tot_ia}')
        print(f'  Resolvidas SO pela IA (sem humano):  {so_ia}  ({100*so_ia/tot_ia:.1f}%)')
        print(f'  Passaram para humano:                {ia_e_hum}  ({100*ia_e_hum/tot_ia:.1f}%)')

    # ---------- 4) Cobertura fora do horario comercial (08-18 BRT) ----------
    cur.execute(f"""
    WITH firsts AS (
      SELECT conversation_id, MIN(message_at) AS first_client
      FROM log_conversa WHERE message_at >= %s AND sender_type='CLIENTE'
      GROUP BY conversation_id
    ),
    resp AS (  -- teve QUALQUER resposta (inclui automacao/IA) em <=5min?
      SELECT f.conversation_id, f.first_client,
             bool_or(l.sender_type='SUPORTE' AND l.message_at>f.first_client
                     AND EXTRACT(EPOCH FROM(l.message_at-f.first_client))<=300) AS resp_5min
      FROM firsts f
      JOIN log_conversa l ON l.conversation_id=f.conversation_id
      GROUP BY 1,2
    )
    SELECT CASE WHEN first_client>=%s THEN 'AGENTE' ELSE 'ANTIGA' END era,
           count(*) n,
           round((100.0*sum(resp_5min::int)/count(*))::numeric,1) pct_resp5
    FROM resp
    WHERE EXTRACT(HOUR FROM first_client AT TIME ZONE 'America/Sao_Paulo') NOT BETWEEN 8 AND 17
    GROUP BY 1 ORDER BY 1;
    """, (HIST_START, CUTOFF))
    hr('4) FORA DO HORARIO: % de conversas com alguma resposta em <=5min')
    print(f"{'era':8}| {'convs':>7} | {'resp<=5min':>10}")
    print('-' * 34)
    for era, n, p5 in cur.fetchall():
        print(f'{era:8}| {n:>7} | {p5:>9}%')

    # ---------- 5) Volume de mensagens: IA vs humano por mes (era agente) ----------
    cur.execute(f"""
    SELECT to_char(message_at,'YYYY-MM') ym,
           sum((COALESCE(NULLIF(TRIM(atendente_nome),''),'Suporte') IN ('Suporte','Administrador'))::int) ia,
           sum((COALESCE(NULLIF(TRIM(atendente_nome),''),'Suporte') NOT IN ('Suporte','Administrador'))::int) hum
    FROM log_conversa
    WHERE sender_type='SUPORTE' AND message_at >= %s
    GROUP BY 1 ORDER BY 1;
    """, (CUTOFF,))
    hr('5) VOLUME DE MENSAGENS ENVIADAS: IA vs HUMANO por mes (era agente)')
    print(f"{'mes':8}| {'IA':>8} | {'humano':>8} | {'% IA':>6}")
    print('-' * 40)
    for ym, ia, hum in cur.fetchall():
        tot = (ia or 0) + (hum or 0)
        pia = 100 * (ia or 0) / tot if tot else 0
        print(f'{ym:8}| {ia or 0:>8} | {hum or 0:>8} | {pia:>5.1f}%')

    # ---------- 6) O que a IA faz (mix de acoes, ult 30d) ----------
    cur.execute("""
    SELECT
      sum((acao LIKE 'escalate%' OR acao LIKE '%transfer')::int) escala,
      sum((acao IN ('resolved','auto_reply','payment_confirmed','confirmacao_resolucao',
                    'closing','despedida','semestre_resolved','inicio_aulas_resolved',
                    'esqueci_senha_canonical'))::int) resolveu,
      sum((acao LIKE 'menu%' OR acao='greeting' OR acao='after_hours_first')::int) navegacao,
      sum((acao LIKE 'followup%' OR acao='greeting_repeat' OR acao='auto_close')::int) followup,
      sum((acao LIKE 'retention%' OR acao='retention')::int) retencao,
      count(*) tot
    FROM ia_interaction_log WHERE created_at > now()-interval '30 days';
    """)
    esc, res, nav, fup, ret, tot = cur.fetchone()
    hr('6) O QUE A IA FEZ (ult 30 dias, por categoria de acao)')
    if tot:
        for lbl, v in [('Resolveu/encerrou', res), ('Escalou p/ humano', esc),
                       ('Navegacao/menu/saudacao', nav), ('Follow-up/auto-close', fup),
                       ('Retencao', ret)]:
            print(f'  {lbl:26} {v or 0:>7}  ({100*(v or 0)/tot:.1f}%)')
        print(f'  {"TOTAL de acoes":26} {tot:>7}')

    # ---------- 8/9) TEMPO ATE DISTRIBUIR e ATE A TAG DE RETENCAO ----------
    # Latencia = evento (dispatch/tag) - ULTIMA msg do cliente antes do evento.
    # As conversas do DataCrazy sao threads longas por contato; por isso NAO se usa a
    # 1a msg da conversa. Separa-se "fresco/normal" (<=1h) de "resgate de backlog" (>1h),
    # porque distribuir uma conversa parada ha dias e resgate, nao distribuicao normal.
    FRESH = 3600

    def latencia_semanal(tbl, tcol, filtro):
        cur.execute(f"""
        WITH h AS (SELECT conv_id, {tcol} ev FROM {tbl} WHERE {filtro}),
        lat AS (
          SELECT date_trunc('week', h.ev) wk,
                 EXTRACT(EPOCH FROM (h.ev - lc.last_cli)) g
          FROM h JOIN LATERAL (
             SELECT max(message_at) AT TIME ZONE 'UTC' last_cli FROM log_conversa l
             WHERE l.conversation_id=h.conv_id AND l.sender_type='CLIENTE'
               AND l.message_at <= h.ev AT TIME ZONE 'UTC'
          ) lc ON true WHERE lc.last_cli IS NOT NULL
        )
        SELECT to_char(wk,'YYYY-MM-DD'), count(*),
               round((100.0*sum((g BETWEEN 0 AND {FRESH})::int)/count(*))::numeric,1),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY g) FILTER (WHERE g BETWEEN 0 AND {FRESH}),
               percentile_cont(0.9) WITHIN GROUP (ORDER BY g) FILTER (WHERE g BETWEEN 0 AND {FRESH})
        FROM lat GROUP BY wk ORDER BY wk;
        """)
        return cur.fetchall()

    hr('8) DISTRIBUICAO: tempo ate distribuir, por semana (normal x resgate)')
    print(f"{'semana':12}| {'total':>6} | {'%normal':>7} | {'med(normal)':>11} | {'p90(normal)':>11}")
    print('-' * 62)
    for wk, n, pf, med, p90 in latencia_semanal('handoff_active', 'created_at', "motivo='dispatch'"):
        print(f'{wk:12}| {n:>6} | {pf:>6}% | {fmt(med):>11} | {fmt(p90):>11}')
    print('  (%normal = distribuido em <=1h da msg do aluno; o resto e resgate de backlog)')

    hr('9) RETENCAO (tag ret_ia): tempo ate a tag, por semana')
    print(f"{'semana':12}| {'total':>6} | {'%normal':>7} | {'med(normal)':>11} | {'p90(normal)':>11}")
    print('-' * 62)
    for wk, n, pf, med, p90 in latencia_semanal('agent_sent_signatures', 'sent_at', "signature='ret_ia'"):
        print(f'{wk:12}| {n:>6} | {pf:>6}% | {fmt(med):>11} | {fmt(p90):>11}')

    cur.close(); c.close()

    # ---------- 7) Funil de retencao / disparos ----------
    d = psycopg2.connect(dbname='disparos', **BASE); dc = d.cursor()
    hr('7) FUNIL DE DISPARO / RETENCAO')
    dc.execute("SELECT count(*) FROM activation_dispatch_events WHERE status='sent'")
    env = dc.fetchone()[0]
    dc.execute("SELECT count(*) FROM activation_responses")
    resp = dc.fetchone()[0]
    dc.execute("SELECT outcome, count(*) FROM activation_manual_outcomes GROUP BY 1 ORDER BY 2 DESC")
    outc = dc.fetchall()
    print(f'  Disparos enviados:   {env}')
    print(f'  Respostas recebidas: {resp}  ({100*resp/env:.1f}% de resposta)' if env else '')
    print('  Desfechos manuais registrados:')
    for o, n in outc:
        print(f'    {str(o):20} {n}')
    dc.close(); d.close()

    print('\n' + '=' * 74)
    print('NOTA METODOLOGICA:')
    print('- "Humano" = atendente com nome proprio; automacao (bot antigo OU IA nova) posta')
    print('  como "Suporte"/"Administrador". Por isso a metrica JUSTA e "tempo ate humano".')
    print('- A era ANTIGA ja tinha bot de menu/saudacao instantaneo -> "tempo ate 1a resposta"')
    print('  cru enganava (auto-saudacao x resposta real). Nao use aquele numero.')
    print('- Ha lacuna de sincronia em Mar/2026 (menos msgs); leia tendencia, nao valor isolado.')
    print('- Fontes: log_conversa (msgs), ia_interaction_log (acoes IA), disparos (retencao).')
    print('=' * 74)


if __name__ == '__main__':
    main()
