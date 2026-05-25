"""Resgate manual das 9 conversas mostradas na imagem (2026-05-25 17:30).
NAO toca no agente principal — apenas distribui cada conversa manualmente,
criando lead se necessario.

Logica por conversa:
- Detecta intent na ultima msg do aluno:
   - payment_confirmed  -> responde "Tudo bem!" + finaliza
   - retention/cancel  -> transfere p/ Wesley
   - resolved          -> responde curto + finaliza
   - generico          -> distribui round-robin (atendente menor fila)
- Garante lead criado no CRM antes de transferir.
- Sempre posta nota interna de auditoria.
"""

import os
import re
import time
import unicodedata
import requests

DCZ_TOKEN = os.environ.get('DCZ_TOKEN', '')
if not DCZ_TOKEN:
    raise SystemExit('ERRO: DCZ_TOKEN nao definido no ambiente')

DCZ_API = 'https://api.g1.datacrazy.io'
DCZ_CRM = 'https://crm.g1.datacrazy.io/api/crm'
DCZ_MSG = 'https://messaging.g1.datacrazy.io/api'
H = {'Authorization': f'Bearer {DCZ_TOKEN}', 'Content-Type': 'application/json'}

INSTANCE_ACADEMICO_ID = '692a13008721fc1c4000859f'
STAGE_BASE_ALUNOS_ID = '742714eb-ac5a-435f-8680-97e6ab8f2f6e'

SUPABASE_URL = 'https://gtmeiltmhytufwdjhzxh.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd0bWVpbHRtaHl0dWZ3ZGpoenhoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NTYzMzQ1MywiZXhwIjoyMDcxMjA5NDUzfQ.Sy5JRcYqmKh-Rd9PDScGftQ_rOqQHLOIPLyvDoHDJeM'
SUPABASE_HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
}
DISTRIBUICAO_TABLE = 'distribuicao_academico_duplicate'

ATTENDANT_MAP = {
    'julia':   '69161295adb204a6c1033c27',
    'marilia': '6903721f1be7fd548fbd5cd3',
    'gustavo': '69026c3a4c877a72ba961aa6',
    'mariana': '69025e95b4c8740e16bb5fbf',
    'debora':  '69025ddf04698c58701e2792',
    'joyce':   '69024f58706ac6e207bf961e',
    'emanuel': '690248cb1f4a6684ed64de58',
    'jessica': '690247b616be0c8343ba8b3a',
    'camila':  '69024741a25c3347e8bdcb4d',
    'danubia': '6902473c20efbbc9adb9d08f',
    'wesley':  '69024605706ac6e207a35209',
    'felipe':  '696fcd21767a0bfa800d1034',
    'beatriz': '6989ef9a6ae58a6435bd2438',
}
CRM_ATTENDANT_MAP = {
    'julia':   'e85ac56f-0dbb-4233-9825-b6b1616d07f7',
    'marilia': '79dc861b-152e-4e8d-8bcf-a99ff64090ba',
    'gustavo': 'a19ff106-ca9b-42aa-b7cd-3d3ac5231b9f',
    'mariana': 'eeb44e51-1193-4d77-812b-7b6873d011c8',
    'debora':  '26882d34-6787-4a06-aa8a-691b42406570',
    'joyce':   'b4a6025c-b5dc-4261-980f-fac1309637fd',
    'emanuel': '0b7f49cb-6fba-4f7b-8de8-f838fa03ea08',
    'jessica': 'b0335732-776e-4bf5-9d5b-44830cbca10d',
    'camila':  'e8e5ddd9-796c-4f89-8670-c5cbf3a2f02f',
    'danubia': 'ab86c173-3353-4c43-9a2e-0a90507bb7bf',
    'wesley':  'dd6cbed7-7666-45d1-bd90-368c8b97e217',
    'felipe':  '59039319-9f52-4ec8-8e12-e554bcd7a9ef',
    'beatriz': 'ab65b480-1761-42d8-815f-c7e3c8a7b6b4',
}

_PAYMENT_OK = (
    'ja paguei', 'já paguei', 'paguei', 'paguei ja', 'paguei já',
    'boleto pago', 'pagamento feito', 'pagamento realizado', 'ja foi pago',
    'já foi pago', 'pix realizado', 'pix feito', 'pix enviado',
    'pagamento ok', 'efetuei o pagamento', 'realizei o pagamento',
    'pago', 'pagamento ja realizado', 'pagamento já realizado',
)
_RETENTION = (
    'cancelar', 'cancelei', 'trancar', 'tranquei', 'cancelamento',
    'trancamento', 'desistir', 'desisti', 'matricula cancelada',
    'matrícula cancelada', 'fui cancelado', 'fui cancelada',
)
_FAREWELL = ('obrigad', 'valeu', 'vlw', 'agradeco', 'agradeço', 'tchau',
             'ate mais', 'até mais', 'beleza', 'blz', 'ok obrigad',
             'okay obrigad', 'show')
_RESOLVED = ('resolvi', 'resolvido', 'consegui', 'deu certo', 'funcionou',
             'ja resolvi', 'já resolvi', 'tudo certo')


def _norm(s):
    s = (s or '').lower().strip()
    s = ''.join(c for c in unicodedata.normalize('NFD', s)
                if unicodedata.category(c) != 'Mn')
    return s


def detect(text):
    t = _norm(text)
    if not t:
        return 'generico'
    for k in _PAYMENT_OK:
        if k in t:
            return 'payment'
    for k in _RETENTION:
        if re.search(r'\b' + re.escape(k) + r'\b', t):
            return 'retention'
    for k in _RESOLVED:
        if re.search(r'\b' + re.escape(k) + r'\b', t):
            return 'resolved'
    if any(k in t for k in _FAREWELL) and len(t) < 50:
        return 'farewell'
    return 'generico'


def list_orphans():
    """Busca conversas ABERTAS, sem atendente, da instancia academica, onde a
    ultima msg foi do aluno (lastReceived > lastSended)."""
    found = {}
    for status in ('open', 'opened', 'unstarted'):
        try:
            r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                             params={'limit': 500, 'status': status,
                                     'instanceId': INSTANCE_ACADEMICO_ID},
                             timeout=30)
            if r.status_code != 200:
                continue
            data = r.json()
            convs = data.get('data', data) if isinstance(data, dict) else data
        except Exception as e:
            print(f'  [LIST] erro status={status}: {e}')
            continue
        if not isinstance(convs, list):
            continue
        for c in convs:
            cid = c.get('id', '')
            if not cid or cid in found:
                continue
            inst = c.get('instance', {}) or {}
            iid = inst.get('id', '') if isinstance(inst, dict) else str(inst)
            if iid != INSTANCE_ACADEMICO_ID:
                continue
            sts = c.get('statuses', []) or []
            if 'finished' in sts:
                continue
            if c.get('attendants'):
                continue
            recv = c.get('lastReceivedMessageDate', '') or ''
            sent = c.get('lastSendedMessageDate', '') or ''
            if not recv:
                continue
            if sent and recv <= sent:
                continue
            found[cid] = c
    return list(found.values())


def last_aluno_msg(conv_id):
    try:
        r = requests.get(f'{DCZ_MSG}/messaging/conversations/{conv_id}/messages',
                         headers=H, params={'limit': 30}, timeout=20)
        if r.status_code != 200:
            return ''
        msgs = r.json().get('messages', []) or []
        for m in msgs:
            if m.get('received', False):
                body = (m.get('body') or m.get('text') or '').strip()
                if body and not body.startswith('['):
                    return body
    except Exception:
        return ''
    return ''


def get_attendants_filaa():
    """Retorna lista de consultores ATIVOS com fila atual."""
    try:
        url = (f'{SUPABASE_URL}/rest/v1/{DISTRIBUICAO_TABLE}'
               f'?ativo_inativo=eq.Ativo&tipo_atendimento=eq.Atendimento&select=*')
        r = requests.get(url, headers=SUPABASE_HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        out = []
        for row in r.json() or []:
            if row.get('status_almoco') != 'Ativo':
                continue
            if row.get('status_final_expediente') != 'Ativo':
                continue
            resp = (row.get('responsavel') or '').strip()
            first = _norm(resp).split()[0] if resp else ''
            fila = int(row.get('fila') or 0)
            limite = int(row.get('volume_distribuicao') or 10)
            if fila >= limite:
                continue
            out.append({'id': row.get('id'), 'nome': resp,
                        'first': first, 'fila': fila})
        out.sort(key=lambda x: x['fila'])
        return out
    except Exception as e:
        print(f'  [SUPABASE] erro: {e}')
        return []


def increment_fila(consultant_id, current):
    try:
        from datetime import datetime, timezone, timedelta
        now_str = datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y - %H:%M')
        iso_str = datetime.now(timezone(timedelta(hours=-3))).isoformat()
        url = f'{SUPABASE_URL}/rest/v1/{DISTRIBUICAO_TABLE}?id=eq.{consultant_id}'
        requests.patch(url, headers=SUPABASE_HEADERS,
                       json={'fila': current + 1, 'ultima_execucao': now_str,
                             'timestamp': iso_str}, timeout=10)
    except Exception:
        pass


def ensure_lead(phone, name):
    clean = phone.replace('+', '').replace(' ', '').replace('-', '')
    candidates = [clean]
    if not clean.startswith('55'):
        candidates.append('55' + clean)
    elif len(clean) > 11:
        candidates.append(clean[2:])
    for p_try in candidates:
        try:
            r = requests.get(f'{DCZ_CRM}/leads', headers=H,
                             params={'search': p_try, 'limit': 5}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                leads = data.get('data', data) if isinstance(data, dict) else data
                if isinstance(leads, list) and leads:
                    lid = leads[0].get('id', '')
                    if lid:
                        return lid, False
        except Exception:
            pass
    body = {'phone': clean, 'name': name or clean}
    for attempt in (1, 2, 3):
        try:
            r = requests.post(f'{DCZ_CRM}/leads', headers=H, json=body, timeout=12)
            if r.status_code in (200, 201):
                lid = (r.json() or {}).get('id', '')
                if lid:
                    try:
                        requests.post(f'{DCZ_CRM}/businesses', headers=H,
                                      json={'leadId': lid,
                                            'stageId': STAGE_BASE_ALUNOS_ID},
                                      timeout=12)
                    except Exception:
                        pass
                    return lid, True
        except Exception:
            pass
        time.sleep(1.5 * attempt)
    return '', False


def transfer_chat(conv_id, target_first):
    att_id = ATTENDANT_MAP.get(target_first)
    if not att_id:
        return False
    try:
        r = requests.post(
            f'{DCZ_MSG}/messaging/conversations/{conv_id}/change-attendant',
            headers=H, json={'attendantId': att_id}, timeout=15)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def transfer_lead_crm(lead_id, target_first):
    att_id = CRM_ATTENDANT_MAP.get(target_first)
    if not att_id or not lead_id:
        return False
    try:
        r = requests.patch(f'{DCZ_CRM}/leads/{lead_id}', headers=H,
                           json={'attendantId': att_id}, timeout=12)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def send_msg(conv_id, body):
    try:
        r = requests.post(f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                          headers=H, json={'body': body}, timeout=15)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def send_internal_note(conv_id, body):
    try:
        requests.post(f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                      headers=H, json={'body': body, 'isInternal': True},
                      timeout=10)
    except Exception:
        pass


def finish_conv(conv_id):
    try:
        r = requests.post(f'{DCZ_API}/api/v1/conversations/{conv_id}/finish',
                          headers=H, json={}, timeout=15)
        if r.status_code in (200, 201, 204):
            return True
    except Exception:
        pass
    try:
        r = requests.post(f'{DCZ_MSG}/messaging/conversations/{conv_id}/finish',
                          headers=H, json={}, timeout=15)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def process(c, attendants_pool):
    cid = c.get('id', '')
    ct = c.get('contact', {}) or {}
    phone = (ct.get('phoneNumber', '') or ct.get('contactId', '')
             or ct.get('phone', '') or '')
    phone = phone.replace('+', '').replace(' ', '').replace('-', '')
    if phone.startswith('55') and len(phone) > 11:
        phone = phone[2:]
    name = (ct.get('name', '') or '').strip()
    first_name = name.split()[0] if name else ''

    body = last_aluno_msg(cid)
    intent = detect(body)
    label = f'{name or "??"} ...{phone[-4:] if phone else "????"} [{intent}] "{body[:50]}"'
    print(f'\n>>> {label}')

    if intent == 'payment':
        ack = (f"Tudo bem{', *' + first_name + '*' if first_name else ''}! 😊 "
               f"Obrigado pela confirmação. Qualquer coisa, é só me chamar. Até mais!")
        if send_msg(cid, ack):
            time.sleep(1.0)
            ok = finish_conv(cid)
            send_internal_note(cid, '✅ *Resgate manual* — payment_confirmed: '
                                    'agente respondeu e fechou.')
            print(f'  -> OK payment + close (finish={ok})')
            return True
        print('  -> ERR send payment')
        return False

    if intent == 'farewell':
        ack = (f"Obrigado pelo contato{', *' + first_name + '*' if first_name else ''}! 🙏 "
               f"Qualquer coisa, é só me chamar de novo 😊")
        if send_msg(cid, ack):
            time.sleep(1.0)
            ok = finish_conv(cid)
            send_internal_note(cid, '👋 *Resgate manual* — farewell: '
                                    'agente agradeceu e fechou.')
            print(f'  -> OK farewell + close (finish={ok})')
            return True
        return False

    target_first = None
    if intent == 'retention':
        target_first = 'wesley'
    else:
        if not attendants_pool:
            print('  -> ERR sem consultor ativo no Supabase')
            return False
        target_first = attendants_pool[0]['first']

    if not target_first or target_first not in ATTENDANT_MAP:
        print(f'  -> ERR target invalido: {target_first}')
        return False

    lead_id, created = ensure_lead(phone, name)
    if not lead_id:
        send_internal_note(cid, '⚠️ *Resgate manual* — falha ao criar lead apos 3x. '
                                'Cria lead manualmente, por favor.')
        print(f'  -> ATENCAO sem lead — mas transferindo chat mesmo assim')

    if lead_id:
        transfer_lead_crm(lead_id, target_first)

    ok_chat = transfer_chat(cid, target_first)
    if not ok_chat:
        print(f'  -> ERR transferencia chat falhou')
        send_internal_note(cid, f'❌ *Resgate manual* — falha ao transferir p/ '
                                f'{target_first.title()}. Atendente nao assumiu.')
        return False

    greet = (f"Oii{', *' + first_name + '*' if first_name else ''}! "
             f"Desculpa a demora pra te responder 🙏\n\n"
             f"Vou te conectar agora com o(a) *{target_first.title()}*, "
             f"que vai dar continuidade ao seu atendimento. "
             f"Em pouquinho ele(a) assume aqui 😊")
    send_msg(cid, greet)
    send_internal_note(cid, f'🚑 *Resgate manual* — transferido para '
                            f'*{target_first.title()}* via _rescue_now.py. '
                            f'Lead={"novo" if created else "existente"} ({lead_id[:12] if lead_id else "SEM-LEAD"}).')

    # incrementa fila no supabase
    for a in attendants_pool:
        if a['first'] == target_first:
            increment_fila(a['id'], a['fila'])
            a['fila'] += 1
            break
    attendants_pool.sort(key=lambda x: x['fila'])
    print(f'  -> OK transferido p/ {target_first.title()} (lead={lead_id[:12] if lead_id else "-"})')
    return True


def main():
    print('=== RESCUE NOW (manual, sem desligar agente) ===\n')
    print('Buscando orfas...')
    orphans = list_orphans()
    print(f'Total orfas encontradas: {len(orphans)}\n')
    if not orphans:
        print('Nenhuma orfa para processar. Saindo.')
        return

    pool = get_attendants_filaa()
    print(f'Consultores ativos: {len(pool)}')
    for a in pool[:5]:
        print(f'  - {a["nome"]} (fila={a["fila"]})')
    if not pool:
        print('AVISO: nenhum consultor ativo — apenas payment/farewell sera tratado.')

    ok = err = 0
    for c in orphans:
        try:
            if process(c, pool):
                ok += 1
            else:
                err += 1
        except Exception as e:
            err += 1
            print(f'  -> EXCECAO: {e}')
        time.sleep(1.5)

    print(f'\n=== FINAL === OK={ok} ERR={err} TOTAL={ok+err}/{len(orphans)}')


if __name__ == '__main__':
    main()
