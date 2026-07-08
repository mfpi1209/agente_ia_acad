"""
Agente IA v4 - Cruzeiro do Sul
Fases 1-4: Identificacao + Memoria + Empatia + Tabulacao
Pipeline: WhatsApp -> Identificar Aluno -> Carregar Memoria -> RAG -> GPT (com contexto) -> Resposta -> Tabular
"""
import requests
import psycopg2
import psycopg2.extras
import json
import subprocess
import sys
import io
import os
import re
import time
import random
import hashlib
import base64
from datetime import datetime, timedelta
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ===================== CONFIG =====================

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
DCZ_API = 'https://api.g1.datacrazy.io'
DCZ_CRM = 'https://crm.g1.datacrazy.io/api/crm'
DCZ_MSG = 'https://messaging.g1.datacrazy.io/api'
DCZ_TOKEN = os.environ.get('DCZ_TOKEN', '')
# (2026-07-06) User-Agent OBRIGATORIO: o Cloudflare do DataCrazy passou a bloquear
# (HTTP 403 "Attention Required") requests sem User-Agent / com o UA padrao do
# python-requests. Sem este header o agente recebe 403 em TODAS as chamadas e fica
# 'cego' (fetched=0, sem distribuir). Testado: sem UA=403, com UA=200.
H = {
    'Authorization': f'Bearer {DCZ_TOKEN}',
    'Content-Type': 'application/json',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
}

PIPELINE_ALUNOS_ID = '7d1b30e3-b554-4225-8523-d2d21ffc7c35'
INSTANCE_ACADEMICO_ID = '692a13008721fc1c4000859f'
N8N_WEBHOOK_LEADS_CPF = 'https://n8n-new-n8n.ca31ey.easypanel.host/webhook/leads_cpf_csv'
STAGE_ATENDIMENTO_ID = 'ce42afe6-757f-405c-aa34-6668f4a75d07'
STAGE_BASE_ALUNOS_ID = '742714eb-ac5a-435f-8680-97e6ab8f2f6e'
STAGE_ENCERRAMENTO_ID = '3016b9c8-3914-4bf5-8f7c-1fee44baea9c'
STAGE_PERDIDO_ID = '7e89e4a3-09ca-4e5a-976b-35f7f041ccf6'
PIPELINE_ENCERRAMENTO_ID = '16638d55-b556-4792-8e11-f67c04ecf94c'

POLOS_LIST = ("Ibirapuera\nTaboão da Serra Centro\nVila Mariana\nItapira\n"
              "Capivari\nCampinas\nPrudente 2\nBarra Funda\nMorumbi\n"
              "Sapopemba\nFreguesia do Ó\nSantana 2\nTaboão Mituzzi")

POLOS_NOSSOS_NORMALIZED = [
    'ibirapuera', 'taboao da serra centro', 'taboao centro', 'vila mariana',
    'itapira', 'capivari', 'campinas', 'prudente', 'prudente 2', 'vila prudente',
    'barra funda', 'morumbi', 'sapopemba', 'vila ema', 'freguesia',
    'freguesia do o', 'moinho velho', 'santana', 'santana 2',
    'taboao mituzzi', 'taboao mituzi', 'jardim mituzi', 'jardim mituzzi',
    'taboao da serra', 'ouro verde', 'indianopolis',
]

# ===================== ENDERECOS OFICIAIS DOS POLOS =====================
# FONTE UNICA DA VERDADE. O LLM NUNCA deve inventar endereco; ele consulta
# essa estrutura via prompt injection quando o aluno pergunta endereco/polo.
# Atualizar AQUI (e somente aqui) quando algo mudar.
POLOS_OFICIAIS = [
    {
        'key': 'barra_funda',
        'nome': 'Polo Barra Funda',
        'endereco': 'Rua do Bosque, 1621, Loja 12 - Térreo',
        'referencia': '10 minutos do Metrô - Estação Palmeiras Barra Funda - Linha 3 - Vermelha',
    },
    {
        'key': 'vila_prudente_2',
        'nome': 'Polo Vila Prudente 2',
        'endereco': 'Rua Ibitirama, 404',
        'referencia': '5 minutos do terminal de ônibus - Estação Vila Prudente - Linha 2 - Verde',
    },
    {
        'key': 'morumbi',
        'nome': 'Polo Morumbi',
        'endereco': 'Rua Amélia Corrêa Fontes Guimarães, 34',
        'referencia': '10 minutos do Metrô São Paulo - Morumbi - Linha Amarela. Seguir na Av. Francisco Morato e virar na Rua Três Irmãos do Hospital Lefort',
    },
    {
        'key': 'taboao_centro',
        'nome': 'Polo Taboão da Serra Centro',
        'endereco': 'Av. Jovina de Carvalho Dau, 216 - Parque Santos Dumont',
        'referencia': 'Centro de Taboão da Serra - Em frente à Delegacia',
    },
    {
        'key': 'taboao_mituizi',
        'nome': 'Polo Taboão da Serra Jardim Mituizi',
        'endereco': 'Rua Osmar Antônio Silva, 128',
        'referencia': 'Altura do número 2800 da Av. Kizaemon Takeuti, em frente ao colégio Dom Pedro',
    },
    {
        'key': 'sapopemba',
        'nome': 'Polo Sapopemba',
        'endereco': 'Av. Vila Ema, 6121 - Sapopemba',
        'referencia': 'Travessa da Av. Sapopemba - Altura do número 7737',
    },
    {
        'key': 'freguesia_o',
        'nome': 'Polo Freguesia do Ó',
        'endereco': 'Rua Manuel Madruga, 82 - Freguesia do Ó',
        'referencia': 'Travessa da Av. Itaberaba - Altura do número 591',
    },
    {
        'key': 'ibirapuera',
        'nome': 'Polo Ibirapuera',
        'endereco': 'Av. Iraí, 79, 21B - Moema',
        'referencia': 'Próximo à estação Eucaliptos',
    },
    {
        'key': 'campinas',
        'nome': 'Polo Campinas',
        'endereco': 'R. Armando Frederico Renganeschi, 276 - Ouro Verde (Jardim Cristina), Campinas - SP, 13054-000',
        'referencia': '',
    },
    {
        'key': 'capivari',
        'nome': 'Polo Capivari',
        'endereco': 'Rua Padre Haroldo, 746 - Centro, Capivari - SP, 13360-000',
        'referencia': '',
    },
    {
        'key': 'itapira',
        'nome': 'Polo Itapira',
        'endereco': 'R. 15 de Novembro, 366 - Centro, Itapira - SP, 13970-270',
        'referencia': '',
    },
]


def _format_polo(p):
    """Formata 1 polo para WhatsApp."""
    txt = f"*{p['nome']}*\n📍 {p['endereco']}"
    if p.get('referencia'):
        txt += f"\n_{p['referencia']}_"
    return txt


def _format_polos_oficiais_para_prompt():
    """Bloco fixo a injetar no SYSTEM_PROMPT sempre que aluno menciona polo/endereco.
    Usa formatacao WhatsApp.
    """
    linhas = []
    for p in POLOS_OFICIAIS:
        ref = f" — {p['referencia']}" if p.get('referencia') else ''
        linhas.append(f"- {p['nome']}: {p['endereco']}{ref}")
    return "\n".join(linhas)


def _normalize_polo_match(text):
    """Devolve a entrada de POLOS_OFICIAIS que melhor casa com o texto, ou None."""
    if not text:
        return None
    import unicodedata
    norm = ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn')
    # tabela palavras-chave -> key
    aliases = {
        'barra funda': 'barra_funda',
        'barra-funda': 'barra_funda',
        'vila prudente 2': 'vila_prudente_2',
        'vila prudente2': 'vila_prudente_2',
        'prudente 2': 'vila_prudente_2',
        'vila prudente': 'vila_prudente_2',
        'morumbi': 'morumbi',
        'taboao centro': 'taboao_centro',
        'taboao da serra centro': 'taboao_centro',
        'taboao da serra': 'taboao_centro',
        'mituizi': 'taboao_mituizi',
        'mituzi': 'taboao_mituizi',
        'mituzzi': 'taboao_mituizi',
        'jardim mituizi': 'taboao_mituizi',
        'jardim mituzi': 'taboao_mituizi',
        'sapopemba': 'sapopemba',
        'vila ema': 'sapopemba',
        'freguesia do o': 'freguesia_o',
        'freguesia': 'freguesia_o',
        'ibirapuera': 'ibirapuera',
        'moema': 'ibirapuera',
        'campinas': 'campinas',
        'ouro verde': 'campinas',
        'capivari': 'capivari',
        'itapira': 'itapira',
    }
    for k, key in sorted(aliases.items(), key=lambda x: -len(x[0])):
        if k in norm:
            return next((p for p in POLOS_OFICIAIS if p['key'] == key), None)
    return None


# Frases que disparam intencao de "visita / preciso ir presencialmente / dificuldade comunicacao"
_PRESENCIAL_TRIGGERS = [
    'pessoalmente', 'pessoa mesmo', 'ir ao polo', 'ir no polo', 'ir aí no polo',
    'ir aí', 'visitar o polo', 'visitar a unidade', 'comparecer',
    'ir presencial', 'ir presencialmente', 'atendimento presencial',
    'conversar pessoalmente', 'falar pessoalmente', 'ir na unidade',
    'aparecer no polo', 'passar no polo', 'passar aí',
    'onde posso conversar pessoalmente', 'onde fica o polo', 'onde e o polo',
    'onde é o polo', 'qual endereço do polo', 'qual o endereço do polo',
    'qual endereco do polo', 'endereço do polo',
    'dificil pelo whatsapp', 'difícil pelo whatsapp',
    'dificil a distancia', 'difícil a distância', 'dificil a distância',
    'difícil a comunicacao', 'difícil a comunicação', 'dificil comunicacao',
    'dificil comunicar', 'difícil comunicar',
    'nao to entendendo', 'não tô entendendo', 'nao to conseguindo',
    'não consigo por aqui', 'nao consigo por aqui',
    'prefiro ir', 'prefiro pessoalmente',
]

# Apenas palavras tipo "endereço/polo/local" puras (sem intencao de visita) -
# nesses casos so respondemos com o endereco oficial, sem transferir
_ENDERECO_ONLY = [
    'qual endereço', 'qual endereco', 'qual o endereço', 'qual o endereco',
    'endereço completo', 'endereco completo', 'me passa o endereço',
    'me manda o endereço', 'me manda o endereco', 'me passa o endereco',
    'cep do polo', 'me informa o endereço', 'me informa o endereco',
    'qual cep',
]


def detect_polo_intent(text):
    """Retorna dict com:
      - intent: 'visit' (quer ir / dificuldade), 'address_only' (so endereco), 'none'
      - polo_mencionado: entry de POLOS_OFICIAIS ou None
    """
    if not text:
        return {'intent': 'none', 'polo_mencionado': None}
    import unicodedata
    norm = ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn')
    norm = ' '.join(norm.split())
    polo = _normalize_polo_match(text)

    # 1) trigger explicito de visita / dificuldade
    for kw in _PRESENCIAL_TRIGGERS:
        kw_n = ''.join(c for c in unicodedata.normalize('NFD', kw.lower())
                       if unicodedata.category(c) != 'Mn')
        if kw_n in norm:
            return {'intent': 'visit', 'polo_mencionado': polo}

    # 2) so endereco / cep / local sem intencao de ir
    for kw in _ENDERECO_ONLY:
        kw_n = ''.join(c for c in unicodedata.normalize('NFD', kw.lower())
                       if unicodedata.category(c) != 'Mn')
        if kw_n in norm:
            return {'intent': 'address_only', 'polo_mencionado': polo}

    # 3) menciona polo + verbo de ir/visitar generico
    if polo and any(w in norm for w in (' ir ', ' vou ', ' chegar ', ' fica ', ' onde ')):
        return {'intent': 'visit', 'polo_mencionado': polo}

    return {'intent': 'none', 'polo_mencionado': polo}


def handle_polo_visit_intent(conv_id, polo_entry, question=''):
    """Aluno disse que quer ir pessoalmente / esta com dificuldade.
    1) Envia mensagem humanizada com:
       - acolhida
       - endereco oficial se polo conhecido (do contexto ou questao)
       - aviso que vai transferir
    2) Transfere para consultor humano (distribuicao normal).

    GUARD: signature 'polo_visit_handled' 4h. Evita duas chamadas concorrentes
    enviarem 2 mensagens E executarem 2 distribute_to_attendant.
    """
    try:
        if _signature_recently_sent(conv_id, 'polo_visit_handled', window_s=4 * 3600):
            p(f"  [POLO-VISIT] {conv_id[:12]} dedup: ja tratado nas ultimas 4h - SKIP")
            return False
    except Exception:
        pass
    try:
        _register_signature(conv_id, 'polo_visit_handled', f'polo:{(polo_entry or {}).get("nome", "?")}')
    except Exception:
        pass
    name_prefix = _student_first_name_prefix(conv_id)
    profile_polo = None
    try:
        st = _conv_states.get(conv_id, {}) or {}
        sp = st.get('student_profile') or {}
        if sp.get('polo'):
            profile_polo = _normalize_polo_match(sp.get('polo') or '')
    except Exception:
        pass
    polo = polo_entry or profile_polo
    if polo:
        msg = (
            f"Imagino que esteja sendo difícil resolver tudo por aqui mesmo{name_prefix}, "
            f"e isso é totalmente compreensível 💙\n\n"
            f"O endereço do *{polo['nome']}* é:\n\n"
            f"📍 *{polo['endereco']}*"
        )
        if polo.get('referencia'):
            msg += f"\n_{polo['referencia']}_"
        msg += (
            "\n\nMas antes de você se deslocar até lá, vou *te transferir agora* para um(a) consultor(a) "
            "que pode te orientar direitinho sobre o melhor caminho — pessoal ou aqui mesmo. "
            "Em pouquinho alguém te chama por aqui, tá? 😊"
        )
    else:
        msg = (
            f"Imagino que esteja sendo difícil resolver tudo por aqui mesmo{name_prefix}, "
            f"e isso é totalmente compreensível 💙\n\n"
            f"Vou *te transferir agora* para um(a) consultor(a) que vai conseguir te orientar melhor — "
            f"incluindo o endereço do polo certo e a melhor forma de te ajudar. "
            f"Em pouquinho alguém te chama por aqui! 😊"
        )

    meta_typing_on()
    send_and_track(conv_id, msg)
    log_to_db(conv_id, question or '[polo_visit_intent]', msg, 1.0, 'polo_visit_intent')
    try:
        _register_signature(conv_id, 'polo_visit_intent', msg)
    except Exception:
        pass

    # Transferir agora se estiver no horario; senao, registrar fila
    if is_within_business_hours():
        try:
            distribute_to_attendant(
                conv_id,
                reason=f'Aluno pediu atendimento presencial / dificuldade comunicação online: "{(question or "")[:140]}"',
            )
        except Exception as e:
            p(f"  [POLO-VISIT] erro distribuir: {e}")
    else:
        try:
            record_pending_escalation(
                conv_id, reason='polo_visit_after_hours', tier='insist',
                retorno_label=next_human_available_label(),
                question=(question or '')[:500],
            )
            _mark_handoff_active(conv_id, 'polo_visit',
                                 target='', ttl_s=12 * 3600, body=msg)
        except Exception:
            pass
    return True


def handle_masterclass_intent(conv_id, question=''):
    """Responde a perguntas sobre certificado do Masterclass.
    Dedup por signature (6h) para evitar repeticao se o aluno reenviar.
    Retorna True se enviou, False se foi suprimido por dedup.
    """
    try:
        if _signature_recently_sent(conv_id, 'masterclass_info', window_s=6 * 3600):
            p(f"  [MASTERCLASS] dedup: ja enviado nas ultimas 6h - suprimindo")
            return False
    except Exception:
        pass
    meta_typing_on()
    sent_ok = send_and_track(conv_id, MASTERCLASS_MSG)
    try:
        log_to_db(conv_id, question or '[masterclass_intent]', MASTERCLASS_MSG,
                  1.0, 'masterclass_info')
    except Exception:
        pass
    if sent_ok:
        try:
            _register_signature(conv_id, 'masterclass_info', MASTERCLASS_MSG)
        except Exception:
            pass
    return True


def handle_polo_address_only(conv_id, polo_entry, question=''):
    """Aluno pediu so o endereco de um polo — responde com endereco oficial.
    Se polo nao foi identificado, pergunta qual polo.

    GUARD: signature 'polo_address_handled' 30min. Aluno pode reperguntar
    apos 30min sem problema, mas evita duplicacao por ciclos concorrentes.
    """
    try:
        sig_addr = f'polo_address_handled:{(polo_entry or {}).get("nome", "_unk")}'
        if _signature_recently_sent(conv_id, sig_addr, window_s=30 * 60):
            p(f"  [POLO-ADDR] {conv_id[:12]} dedup: ja enviado nos ultimos 30min - SKIP")
            return False
    except Exception:
        pass
    try:
        _register_signature(conv_id, sig_addr, str((polo_entry or {}).get('nome', '')))
    except Exception:
        pass
    name_prefix = _student_first_name_prefix(conv_id)
    if polo_entry:
        msg = (
            f"Claro{name_prefix}! O endereço do *{polo_entry['nome']}* é:\n\n"
            f"📍 *{polo_entry['endereco']}*"
        )
        if polo_entry.get('referencia'):
            msg += f"\n_{polo_entry['referencia']}_"
        msg += "\n\nSe precisar de mais alguma coisa, é só me chamar! 😊"
    else:
        polos_curtos = "\n".join(f"- {p['nome']}" for p in POLOS_OFICIAIS)
        msg = (
            f"Claro{name_prefix}! Me conta qual polo você quer saber o endereço, "
            f"que eu te passo certinho 😊\n\n*Polos atendidos:*\n{polos_curtos}"
        )
    meta_typing_on()
    send_and_track(conv_id, msg)
    log_to_db(conv_id, question or '[polo_address_only]', msg, 1.0, 'polo_address_only')
    return True

OUTRO_POLO_MSG_1 = (
    "Identifiquei que você não está vinculado(a) a um dos polos que atendemos "
    "e, por motivos de segurança e ética, não tenho acesso às suas informações "
    "por aqui 🥹.\n\n"
    "Mas não se preocupe! Basta clicar no link abaixo 👇 para localizar o seu "
    "polo de apoio e falar diretamente com a sua unidade:\n\n"
    "https://www.cruzeirodosulvirtual.com.br/nossos-polos/"
)

OUTRO_POLO_MSG_2 = (
    "Este atendimento está sendo encerrado. 😉\n\n"
    "Espero de verdade que você consiga todo o apoio necessário no seu polo. "
    "Se em algum momento sentir que precisa de um atendimento mais próximo, "
    "ágil ou quiser conhecer nosso polo, é só me chamar. Estou por aqui."
)

NOT_IN_BASE_BUTTONS = ['Já sou aluno', 'Quero me matricular']
COMMERCIAL_REDIRECT_MSG = (
    "Certo!\n\n"
    "Este canal é dedicado ao *atendimento acadêmico* dos nossos alunos. "
    "Para *matrículas e informações comerciais*, quem vai te orientar "
    "direitinho é o nosso time comercial 😊\n\n"
    "Vou *te transferir agora* para um(a) consultor(a) que vai te passar "
    "o contato certo e dar todo o suporte que você precisa. "
    "Em pouquinho alguém te chama por aqui! 💙"
)

# === INICIO DAS AULAS (matricula nova) ===
# Regra: quem se matricula AGORA em graduacao ingressa na turma do 2o
# semestre (agosto). NaO eh fevereiro. Resposta canonica e ANTI-alucinacao.
_INICIO_AULAS_TRIGGERS = [
    # perguntas explicitas sobre data de inicio
    'quando comecam as aulas', 'quando começam as aulas',
    'quando comeca as aulas', 'quando começa as aulas',
    'quando comecam aula', 'quando começam aula',
    'quando inicia as aulas', 'quando iniciam as aulas',
    'quando inicia aula', 'quando iniciam aula',
    'quando vou comecar', 'quando vou começar',
    'inicio das aulas', 'início das aulas',
    'inicio do curso', 'início do curso',
    'inicia o curso', 'começa o curso', 'comeca o curso',
    'comeco as aulas', 'começo as aulas',
    'data de inicio', 'data de início',
    'mes que comeca', 'mês que começa',
    'em que mes', 'em que mês',
    'que mes', 'que mês',
    # contestacoes diretas a info errada anterior
    'comecam em fevereiro', 'começam em fevereiro',
    'comeca em fevereiro', 'começa em fevereiro',
    'inicia em fevereiro',
    'em fevereiro mesmo', 'em fevereiro',
    'comecam em janeiro', 'começam em janeiro',
    'inicia em janeiro', 'em janeiro',
    'turma de fevereiro', 'turma de janeiro',
]

# (2026-06-03) Inicio das aulas agora eh RESOLVIDO por aluno: a turma de
# ingresso vem da data_matricula (mm_matriculados) cruzada com as janelas de
# matricula do Calendario Academico Graduacao EAD 2026, e a data de inicio
# das aulas vem do proprio calendario. NUNCA mais resposta fixa "agosto".
# Quando NAO for possivel determinar (Pos, aluno fora da base, data fora das
# janelas conhecidas), o agente TRANSFERE para consultor — nao inventa.
#
# Janelas SEQUENCIAIS e SEM sobreposicao: cada data_matricula cai em exatamente
# uma. Tupla = (janela_ini_iso, janela_fim_iso, nome_turma, inicio_aulas_iso).
# Fonte: Calendario Academico Graduacao EAD 2026 (oficial).
_TURMAS_INGRESSO_2026 = [
    ("2025-11-18", "2026-02-15", "Fevereiro", "2026-02-02"),
    ("2026-02-16", "2026-03-08", "Março",     "2026-03-02"),
    ("2026-03-09", "2026-04-12", "Abril",     "2026-04-01"),
    ("2026-04-13", "2026-05-12", "Maio",      "2026-05-04"),
    ("2026-05-13", "2026-08-16", "Agosto",    "2026-08-03"),
    ("2026-08-17", "2026-09-13", "Setembro",  "2026-09-01"),
    ("2026-09-14", "2026-10-11", "Outubro",   "2026-10-01"),
    ("2026-10-12", "2026-11-17", "Novembro",  "2026-11-03"),
]


def _parse_iso_date(s):
    """Converte 'YYYY-MM-DD' (ou prefixo) em datetime.date, ou None."""
    if not s:
        return None
    try:
        from datetime import date as _date
        y, m, d = str(s)[:10].split('-')
        return _date(int(y), int(m), int(d))
    except Exception:
        return None


def resolve_turma_ingresso(data_matricula):
    """Recebe a data_matricula (ISO 'YYYY-MM-DD') e devolve a turma de ingresso
    + data de inicio das aulas. Retorna dict {turma, inicio_dt, inicio_br} ou
    None se a data nao mapear em nenhuma janela conhecida de 2026 (-> o caller
    deve transferir para consultor, NUNCA chutar)."""
    dt = _parse_iso_date(data_matricula)
    if not dt:
        return None
    for ini, fim, turma, inicio in _TURMAS_INGRESSO_2026:
        di = _parse_iso_date(ini)
        df = _parse_iso_date(fim)
        if di and df and di <= dt <= df:
            ini_dt = _parse_iso_date(inicio)
            return {
                'turma': turma,
                'inicio_dt': ini_dt,
                'inicio_br': ini_dt.strftime('%d/%m') if ini_dt else '',
            }
    return None


def detect_inicio_aulas_intent(text):
    """True se o aluno perguntou sobre quando as aulas comecam/iniciam ou
    mencionou 'fevereiro/janeiro' no contexto de inicio (matricula nova)."""
    if not text:
        return False
    import unicodedata
    norm = ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn')
    norm = ' '.join(norm.split())
    for kw in _INICIO_AULAS_TRIGGERS:
        kw_n = ''.join(c for c in unicodedata.normalize('NFD', kw.lower())
                       if unicodedata.category(c) != 'Mn')
        if kw_n in norm:
            return True
    return False


def _transfer_acad_question_to_consultant(conv_id, question='', assunto='essa informação',
                                          motivo='', handoff_tag='acad_info'):
    """Fallback generico para perguntas academicas sem dado confiavel (Pos,
    aluno fora da base, dado ausente) -> avisa o aluno e transfere para
    consultor. NUNCA inventa. Mesmo padrao do handle_polo_visit_intent.

    assunto: o que o consultor vai confirmar (ex: 'a data de início das suas
             aulas', 'o seu semestre atual').
    handoff_tag: usado em reason/handoff (ex: 'inicio_aulas', 'semestre').
    """
    name_prefix = ''
    try:
        name_prefix = _student_first_name_prefix(conv_id)
    except Exception:
        pass
    msg = (
        f"Pra te passar {assunto} certinho{name_prefix}, vou *te conectar com "
        f"um(a) consultor(a)* que confirma isso no seu cadastro. Em pouquinho "
        f"alguém te chama por aqui, tá? 😊"
    )
    try:
        meta_typing_on()
        send_and_track(conv_id, msg)
        log_to_db(conv_id, question or f'[{handoff_tag}_transfer]', msg, 1.0, f'{handoff_tag}_transfer')
    except Exception as e:
        p(f"  [{handoff_tag.upper()}] erro ao enviar msg de transferencia: {e}")
    if is_within_business_hours():
        try:
            distribute_to_attendant(
                conv_id,
                reason=f'{assunto} — sem dado confiável ({motivo}). Pergunta: "{(question or "")[:120]}"',
            )
        except Exception as e:
            p(f"  [{handoff_tag.upper()}] erro distribuir: {e}")
    else:
        try:
            record_pending_escalation(
                conv_id, reason=f'{handoff_tag}_after_hours', tier='insist',
                retorno_label=next_human_available_label(),
                question=(question or '')[:500],
            )
            _mark_handoff_active(conv_id, handoff_tag, target='', ttl_s=12 * 3600, body=msg)
        except Exception:
            pass
    return msg


def handle_inicio_aulas_intent(conv_id, question='', academic=None):
    """Responde quando comecam as aulas usando a TURMA REAL do aluno.

    Estrategia (2026-06-03):
    - Resolve a turma de ingresso pela data_matricula (mm_matriculados) cruzada
      com as janelas do calendario, e devolve a data oficial de inicio.
    - Se nao for possivel determinar com certeza (Pos / fora da base /
      data fora das janelas), TRANSFERE para consultor (nunca inventa).

    Dedup de 6h. Retorna a mensagem enviada (str) ou '' se nada foi enviado.
    """
    sig = 'inicio_aulas_resolved'
    if _signature_recently_sent(conv_id, sig, window_s=6 * 3600):
        p(f"  [INICIO-AULAS] dedup: ja respondido nas ultimas 6h - suprimindo")
        return ''

    acad = academic or {}
    nivel = (acad.get('nivel') or '').strip().lower()
    data_matricula = acad.get('data_matricula')

    # 1) Aluno nao encontrado na base academica OU nao eh graduacao (ex: Pos):
    #    sem dado confiavel -> transfere, nunca chuta.
    if not acad or (nivel and 'grad' not in nivel):
        p(f"  [INICIO-AULAS] sem dado de graduacao (nivel='{nivel or 'vazio'}') -> transferir consultor")
        msg = _transfer_acad_question_to_consultant(
            conv_id, question=question, assunto='a data de início das suas aulas',
            motivo='pos/sem dado acadêmico' if nivel else 'aluno fora da base',
            handoff_tag='inicio_aulas',
        )
        _register_signature(conv_id, sig, msg)
        return msg

    # 2) Resolve a turma de ingresso pela data_matricula.
    turma = resolve_turma_ingresso(data_matricula)
    if not turma:
        p(f"  [INICIO-AULAS] data_matricula='{data_matricula}' fora das janelas -> transferir consultor")
        msg = _transfer_acad_question_to_consultant(
            conv_id, question=question, assunto='a data de início das suas aulas',
            motivo='data_matricula fora das janelas', handoff_tag='inicio_aulas',
        )
        _register_signature(conv_id, sig, msg)
        return msg

    # 3) Temos a turma + data de inicio. Personaliza por tempo (futuro/passado).
    try:
        from datetime import date as _date
        hoje = _date.today()
    except Exception:
        hoje = None
    name_prefix = ''
    try:
        name_prefix = _student_first_name_prefix(conv_id)
    except Exception:
        pass

    inicio_dt = turma['inicio_dt']
    inicio_br = turma['inicio_br']
    if hoje and inicio_dt and inicio_dt > hoje:
        # Aulas ainda vao comecar
        msg = (
            f"Que bom que você perguntou{name_prefix}! 😊\n\n"
            f"Pela sua matrícula, você entra na *turma de {turma['turma']}*, então "
            f"suas aulas começam em *{inicio_br}*.\n\n"
            f"Assim que liberar, o conteúdo aparece pra você na plataforma. "
            f"Qualquer dúvida, é só me chamar! 💙"
        )
    else:
        # Aulas ja comecaram (semestre em andamento)
        msg = (
            f"Deixa eu te explicar{name_prefix} 😊\n\n"
            f"Pela sua matrícula, suas aulas *já começaram em {inicio_br}* e "
            f"seguem ao longo do semestre, com as disciplinas mensais.\n\n"
            f"Se você não está conseguindo acessar o conteúdo, me avisa que eu te "
            f"ajudo a resolver, tá? 💙"
        )

    try:
        meta_typing_on()
        sent_ok = send_and_track(conv_id, msg)
        if sent_ok:
            log_to_db(conv_id, question or '', msg, 1.0, sig)
            _register_signature(conv_id, sig, msg)
            p(f"  [INICIO-AULAS] turma={turma['turma']} inicio={inicio_br} (resolvido por data_matricula)")
        return msg if sent_ok else ''
    except Exception as e:
        p(f"  [INICIO-AULAS] erro: {e}")
        return ''


# === SEMESTRE / TURMA ATUAL DO ALUNO ===
# (2026-06-03) Mesmo principio do inicio das aulas: responde o semestre (serie
# da mm_matriculados) e, para calouro, a turma de ingresso (data_matricula).
# Sem dado confiavel (Pos / fora da base / sem serie) -> transfere, nao inventa.
_SEMESTRE_TRIGGERS = [
    'qual meu semestre', 'qual o meu semestre', 'qual e meu semestre',
    'meu semestre atual', 'semestre atual', 'que semestre eu estou',
    'que semestre estou', 'em que semestre eu', 'em que semestre estou',
    'em qual semestre', 'qual semestre eu estou', 'qual semestre estou',
    'qual semestre eu to', 'qual semestre to', 'qual semestre eu curso',
    'qual meu periodo', 'qual o meu periodo', 'meu periodo atual',
    'em que periodo eu', 'em que periodo estou', 'em qual periodo',
    'qual meu modulo', 'qual o meu modulo', 'meu modulo atual',
    'qual minha turma', 'qual a minha turma', 'minha turma atual',
    'que turma eu sou', 'qual turma eu sou', 'qual turma eu estou',
    'turma atual',
]


def detect_semestre_intent(text):
    """True se o aluno perguntou em qual semestre/periodo/turma ele esta."""
    if not text:
        return False
    import unicodedata
    norm = ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn')
    norm = ' '.join(norm.split())
    for kw in _SEMESTRE_TRIGGERS:
        kw_n = ''.join(c for c in unicodedata.normalize('NFD', kw.lower())
                       if unicodedata.category(c) != 'Mn')
        if kw_n in norm:
            return True
    return False


def handle_semestre_intent(conv_id, question='', academic=None):
    """Responde o semestre/turma atual do aluno usando a mm_matriculados.

    - Graduacao encontrada: informa o semestre (serie) e, para calouro (nova
      matricula), a turma de ingresso (resolvida pela data_matricula).
    - Multiplos cursos: lista o semestre de cada um.
    - Pos / fora da base / sem serie: transfere para consultor (nao inventa).

    Dedup 6h. Retorna a mensagem enviada (str) ou '' se nada foi enviado.
    """
    sig = 'semestre_resolved'
    if _signature_recently_sent(conv_id, sig, window_s=6 * 3600):
        p(f"  [SEMESTRE] dedup: ja respondido nas ultimas 6h - suprimindo")
        return ''

    acad = academic or {}
    nivel = (acad.get('nivel') or '').strip().lower()

    if not acad or (nivel and 'grad' not in nivel):
        p(f"  [SEMESTRE] sem dado de graduacao (nivel='{nivel or 'vazio'}') -> transferir consultor")
        msg = _transfer_acad_question_to_consultant(
            conv_id, question=question, assunto='o seu semestre atual',
            motivo='pos/sem dado acadêmico' if nivel else 'aluno fora da base',
            handoff_tag='semestre',
        )
        _register_signature(conv_id, sig, msg)
        return msg

    name_prefix = ''
    try:
        name_prefix = _student_first_name_prefix(conv_id)
    except Exception:
        pass

    def _ord_sem(s):
        s = (s or '').strip()
        return f"{s}º semestre" if s.isdigit() else (s or '')

    # Multiplos cursos -> lista cada um
    multi = acad.get('_all_courses') or []
    if len(multi) > 1:
        linhas = []
        for c in multi[:3]:
            s = _ord_sem(c.get('serie'))
            cu = (c.get('curso') or 'seu curso').strip()
            if s:
                linhas.append(f"• *{cu}*: {s}")
        if linhas:
            msg = (
                f"Você tem mais de um curso com a gente{name_prefix}! 😊\n\n"
                + "\n".join(linhas)
                + "\n\nQualquer dúvida, é só me chamar! 💙"
            )
            try:
                meta_typing_on()
                sent_ok = send_and_track(conv_id, msg)
                if sent_ok:
                    log_to_db(conv_id, question or '', msg, 1.0, sig)
                    _register_signature(conv_id, sig, msg)
                    p(f"  [SEMESTRE] multi-curso respondido ({len(linhas)} cursos)")
                return msg if sent_ok else ''
            except Exception as e:
                p(f"  [SEMESTRE] erro: {e}")
                return ''

    serie = (acad.get('serie') or '').strip()
    if not serie:
        p(f"  [SEMESTRE] sem serie -> transferir consultor")
        msg = _transfer_acad_question_to_consultant(
            conv_id, question=question, assunto='o seu semestre atual',
            motivo='serie ausente', handoff_tag='semestre',
        )
        _register_signature(conv_id, sig, msg)
        return msg

    curso = (acad.get('curso') or '').strip()
    tipo = (acad.get('tipo_matricula') or '').lower()
    base = f"Deixa eu conferir aqui{name_prefix}! 😊\n\nVocê está no *{_ord_sem(serie)}*"
    if curso:
        base += f" do curso de *{curso}*"
    base += "."
    # Para calouro (nova matricula) a turma de ingresso eh significativa.
    if 'nova matricula' in tipo:
        turma = resolve_turma_ingresso(acad.get('data_matricula'))
        if turma:
            base += f"\n\nVocê entrou na *turma de {turma['turma']}*."
    base += "\n\nQualquer dúvida, é só me chamar! 💙"

    try:
        meta_typing_on()
        sent_ok = send_and_track(conv_id, base)
        if sent_ok:
            log_to_db(conv_id, question or '', base, 1.0, sig)
            _register_signature(conv_id, sig, base)
            p(f"  [SEMESTRE] serie={serie} tipo='{tipo}' respondido")
        return base if sent_ok else ''
    except Exception as e:
        p(f"  [SEMESTRE] erro: {e}")
        return ''


# === MasterClass FAQ ===
# Resposta canonica definida pelo time. NUNCA deve ser parafraseada pelo LLM.
_MASTERCLASS_TRIGGERS = [
    'masterclass', 'master class', 'master-class',
]

MASTERCLASS_MSG = (
    "Sobre o certificado do *Masterclass*, vou te orientar 😊\n\n"
    "Se você já conferiu seu e-mail e não encontrou, o próximo passo é "
    "*acessar novamente o link do Masterclass* e preencher o formulário com seus dados.\n\n"
    "Depois de enviar, *aguarde de 48 a 72 horas* pra receber o certificado "
    "no e-mail que você cadastrou.\n\n"
    "Se mesmo assim não receber, você pode entrar em contato direto pelo e-mail:\n"
    "📧 *masterclass@cruzeirodosul.edu.br*\n\n"
    "Qualquer dúvida, é só me avisar! 💙"
)


def detect_masterclass_intent(text):
    """True se o aluno mencionou 'masterclass' (variantes acentuacao/espaco)."""
    if not text:
        return False
    import unicodedata
    norm = ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn')
    norm = ' '.join(norm.split())
    for kw in _MASTERCLASS_TRIGGERS:
        kw_n = ''.join(c for c in unicodedata.normalize('NFD', kw.lower())
                       if unicodedata.category(c) != 'Mn')
        if kw_n in norm:
            return True
    return False


# === ESQUECI MINHA SENHA / REDEFINIR SENHA ===
# Resposta canonica definida pelo time (atualizada em 2026-05-21):
# Aluno clica em "Esqueci minha senha" -> digita TELEFONE atualizado ->
# recebe codigo por SMS -> cria nova senha.
# NAO eh por e-mail/link/CPF. O LLM ja errou nesse caminho — entao
# resposta canonica e ANTI-paraphrase, plugada ANTES do KB/LLM.
_ESQUECI_SENHA_TRIGGERS = [
    'esqueci minha senha', 'esqueci a senha', 'esqueci senha',
    'esqueci a minha senha', 'esqueci a senha do portal',
    'esqueci a senha do app', 'esqueci a senha da duda',
    'esqueci o meu acesso',
    # variantes de "como fazer"
    'recuperar minha senha', 'recuperar a senha', 'recuperar senha',
    'redefinir minha senha', 'redefinir a senha', 'redefinir senha',
    'trocar minha senha', 'trocar a senha', 'trocar senha',
    'mudar minha senha', 'mudar a senha', 'mudar senha',
    'alterar minha senha', 'alterar a senha', 'alterar senha',
    'criar nova senha', 'nova senha',
    # variantes de problema
    'minha senha nao ta funcionando', 'minha senha não tá funcionando',
    'minha senha nao funciona', 'minha senha não funciona',
    'senha nao funciona', 'senha não funciona',
    'senha invalida', 'senha inválida',
    'esqueci a senha do portal do aluno',
    # variantes simples
    'como recupero a senha', 'como recupero minha senha',
    'como redefino a senha', 'como redefino minha senha',
    'como troco a senha', 'como troco minha senha',
    'como mudo a senha', 'como mudo minha senha',
]

ESQUECI_SENHA_MSG = (
    "Pra redefinir sua senha, é só clicar em *Esqueci minha senha* na "
    "tela de login. Você vai digitar o seu *telefone atualizado* e "
    "receber um *código por SMS*. Informa o código no campo indicado e "
    "na sequência você cria sua *nova senha* 💙\n\n"
    "⚠️ O telefone precisa estar atualizado no seu cadastro pra o SMS "
    "chegar. Se você trocou de número e não recebeu o SMS, me avisa que "
    "a gente vê o melhor caminho juntos."
)


def detect_esqueci_senha_intent(text):
    """True se o aluno perguntou sobre redefinir/recuperar/trocar senha
    ou disse explicitamente 'esqueci minha senha'."""
    if not text:
        return False
    import unicodedata
    norm = ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn')
    norm = ' '.join(norm.split())
    for kw in _ESQUECI_SENHA_TRIGGERS:
        kw_n = ''.join(c for c in unicodedata.normalize('NFD', kw.lower())
                       if unicodedata.category(c) != 'Mn')
        if kw_n in norm:
            return True
    return False


def handle_esqueci_senha_intent(conv_id, question=''):
    """Envia a resposta canonica sobre Esqueci Minha Senha (telefone+SMS).
    Dedup de 6h. Bloqueia o LLM de parafrasear/inventar info errada
    (e-mail/link/CPF). Retorna True se enviou (ou se foi dedup), False
    em caso de erro."""
    sig = 'esqueci_senha_canonical'
    try:
        if _signature_recently_sent(conv_id, sig, window_s=6 * 3600):
            p(f"  [ESQUECI-SENHA] dedup: ja enviado nas ultimas 6h - suprimindo")
            return True
    except Exception:
        pass
    try:
        meta_typing_on()
        sent_ok = send_and_track(conv_id, ESQUECI_SENHA_MSG)
        if sent_ok:
            log_to_db(conv_id, question or '', ESQUECI_SENHA_MSG, 1.0, sig)
            try:
                _register_signature(conv_id, sig, ESQUECI_SENHA_MSG)
            except Exception:
                pass
            p(f"  [ESQUECI-SENHA] resposta canonica enviada (telefone + SMS)")
        return sent_ok
    except Exception as e:
        p(f"  [ESQUECI-SENHA] erro: {e}")
        return False


# === A1 / Prova Regimental ===
# Regra: aluno fala da A1 -> precisamos identificar o MeS da prova.
#  - Mes vigente: nota e divulgada ate o fim do mes (resposta padrao).
#  - Mes passado ou anterior: orienta a entrar em contato com tutor/professor.
#  - Sem mes informado: pergunta de qual mes e a A1.
_A1_TRIGGERS = [
    'a1', 'prova a1', 'regimental', 'nota da a1', 'nota a1', 'a1 zerada',
    'a1 esta zerada', 'a1 está zerada', 'minha a1', 'nota de a1',
]

_MESES_PT = {
    'janeiro': 1, 'jan': 1,
    'fevereiro': 2, 'fev': 2,
    'marco': 3, 'mar': 3,
    'abril': 4, 'abr': 4,
    'maio': 5, 'mai': 5,
    'junho': 6, 'jun': 6,
    'julho': 7, 'jul': 7,
    'agosto': 8, 'ago': 8,
    'setembro': 9, 'set': 9,
    'outubro': 10, 'out': 10,
    'novembro': 11, 'nov': 11,
    'dezembro': 12, 'dez': 12,
}


def _mes_atual_brt():
    """Retorna numero do mes atual em BRT (UTC-3)."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=-3))).month


def detect_a1_intent(text):
    """Retorna dict com:
      - is_a1: bool (mencionou A1/regimental)
      - mes: int|None (mes referenciado, 1-12)
      - quando: 'vigente'|'anterior'|'desconhecido'
    """
    if not text:
        return {'is_a1': False, 'mes': None, 'quando': 'desconhecido'}
    import unicodedata, re
    norm = ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn')
    norm = ' '.join(norm.split())

    # 1) Detecta menção a A1/regimental.
    # 'a1' precisa estar isolado (nao parte de outra palavra) ou prefixado por
    # 'prova ', 'nota '. Usamos regex \\ba1\\b para evitar falsos positivos
    # como em palavras com a1 no meio.
    is_a1 = False
    for kw in _A1_TRIGGERS:
        kw_n = ''.join(c for c in unicodedata.normalize('NFD', kw.lower())
                       if unicodedata.category(c) != 'Mn')
        if kw_n == 'a1':
            if re.search(r'\ba1\b', norm):
                is_a1 = True
                break
        elif kw_n in norm:
            is_a1 = True
            break
    if not is_a1:
        return {'is_a1': False, 'mes': None, 'quando': 'desconhecido'}

    mes_atual = _mes_atual_brt()
    mes_ref = None
    quando = 'desconhecido'

    # 2) Frases que indicam tempo relativo
    if any(s in norm for s in ('deste mes', 'este mes', 'esse mes',
                                'mes atual', 'mes vigente', 'agora',
                                'hoje', 'fiz agora', 'fiz hoje',
                                'essa semana', 'esta semana', 'nessa semana',
                                'recem', 'recente', 'a pouco', 'ha pouco',
                                'fiz esse mes', 'fiz este mes', 'fiz deste mes')):
        return {'is_a1': True, 'mes': mes_atual, 'quando': 'vigente'}

    if any(s in norm for s in ('mes passado', 'do mes passado',
                                'mes anterior', 'antigo', 'antiga',
                                'do semestre passado', 'meses atras',
                                'meses atrás')):
        prev = mes_atual - 1 if mes_atual > 1 else 12
        return {'is_a1': True, 'mes': prev, 'quando': 'anterior'}

    # 3) Mes explicito por nome
    for nome, num in _MESES_PT.items():
        if re.search(rf'\b{nome}\b', norm):
            mes_ref = num
            break

    # 4) Mes explicito por numero MM/AAAA ou MM/AA
    if mes_ref is None:
        m = re.search(r'\b(\d{1,2})[/\-](\d{2,4})\b', norm)
        if m:
            try:
                mm = int(m.group(1))
                if 1 <= mm <= 12:
                    mes_ref = mm
            except Exception:
                pass

    if mes_ref is not None:
        quando = 'vigente' if mes_ref == mes_atual else 'anterior'

    return {'is_a1': True, 'mes': mes_ref, 'quando': quando}


def _nome_mes_pt(num):
    nomes = ['', 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
             'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
    return nomes[num] if 1 <= num <= 12 else '?'


def handle_a1_intent(conv_id, intent_info, question=''):
    """Envia resposta canonica baseada no tempo da A1. Dedup 6h por
    cenario (vigente/anterior/perguntar)."""
    quando = intent_info.get('quando', 'desconhecido')
    mes = intent_info.get('mes')
    name_prefix = _student_first_name_prefix(conv_id)

    if quando == 'vigente':
        mes_label = _nome_mes_pt(mes) if mes else 'deste mês'
        msg = (
            f"Tranquilo{name_prefix}! 😊\n\n"
            f"A nota da *A1 de {mes_label}* (regimental) é divulgada "
            f"*até o final do mês*. Ou seja, ainda está dentro do prazo "
            f"normal de lançamento.\n\n"
            f"Se ao final do mês a nota continuar zerada ou não aparecer, "
            f"me avisa que a gente investiga junto, tá? 💙"
        )
        sig = 'a1_vigente'
    elif quando == 'anterior':
        mes_label = _nome_mes_pt(mes) if mes else 'de um mês anterior'
        msg = (
            f"Entendi{name_prefix}! 😊\n\n"
            f"Como a sua *A1 ({mes_label})* foi de um mês anterior, a nota "
            f"já deveria ter sido divulgada na plataforma.\n\n"
            f"Nesse caso, o ideal é entrar em contato direto com o(a) *tutor(a) "
            f"ou professor(a) da disciplina*, que pode te orientar sobre o "
            f"que aconteceu com o lançamento.\n\n"
            f"Se precisar de mais alguma coisa, é só me chamar! 💙"
        )
        sig = 'a1_anterior'
    else:
        msg = (
            f"Claro{name_prefix}! Pra te ajudar certinho com a A1 (regimental), "
            f"me conta de qual *mês* ela foi? 😊\n\n"
            f"(_pode ser 'deste mês', 'mês passado' ou o nome do mês mesmo,"
            f" tipo 'A1 de abril'_)"
        )
        sig = 'a1_ask_mes'

    try:
        if _signature_recently_sent(conv_id, sig, window_s=6 * 3600):
            p(f"  [A1] dedup: {sig} ja enviado nas ultimas 6h - suprimindo")
            return False
    except Exception:
        pass
    meta_typing_on()
    sent_ok = send_and_track(conv_id, msg)
    try:
        log_to_db(conv_id, question or f'[a1_{quando}]', msg, 1.0, f'a1_{quando}')
    except Exception:
        pass
    if sent_ok:
        try:
            _register_signature(conv_id, sig, msg)
        except Exception:
            pass
    return True

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 5432)),
    'user': os.environ.get('DB_USER', 'postgres'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'dbname': os.environ.get('DB_NAME', 'log_conversa')
}

PHONE_TO_MONITOR_DEFAULT = os.environ.get('PHONE_TO_MONITOR', '11984393285')
PHONE_TO_MONITOR = PHONE_TO_MONITOR_DEFAULT
CONFIDENCE_THRESHOLD = 0.5
POLL_INTERVAL = 3
TOP_K_RESULTS = 5

# ===================== DISTRIBUICAO CONFIG =====================

SUPABASE_URL = 'https://gtmeiltmhytufwdjhzxh.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd0bWVpbHRtaHl0dWZ3ZGpoenhoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NTYzMzQ1MywiZXhwIjoyMDcxMjA5NDUzfQ.Sy5JRcYqmKh-Rd9PDScGftQ_rOqQHLOIPLyvDoHDJeM'
SUPABASE_HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
}
DISTRIBUICAO_TABLE = 'distribuicao_academico_duplicate'

# ===================== SUPABASE GRADES (banco de cursos) =====================

GRADES_SUPABASE_URL = os.environ.get('GRADES_SUPABASE_URL', '')
GRADES_SUPABASE_KEY = os.environ.get('GRADES_SUPABASE_KEY', '')
GRADES_HEADERS = {
    'apikey': GRADES_SUPABASE_KEY,
    'Authorization': f'Bearer {GRADES_SUPABASE_KEY}',
    'Content-Type': 'application/json',
}
_course_info_cache = {}

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


def _normalize_attendant_name(name):
    """Normaliza nome p/ lookup nos maps: lowercase, sem acento, trim."""
    if not name:
        return ''
    import unicodedata
    n = name.strip().lower()
    n = ''.join(c for c in unicodedata.normalize('NFD', n) if unicodedata.category(c) != 'Mn')
    return n


def _lookup_attendant_id(name, table):
    """Resolve attendant_id em ATTENDANT_MAP ou CRM_ATTENDANT_MAP.
    Tenta: nome completo normalizado -> primeiro nome -> qualquer chave que seja prefixo do nome.
    """
    norm = _normalize_attendant_name(name)
    if not norm:
        return None
    if norm in table:
        return table[norm]
    first = norm.split()[0] if norm.split() else ''
    if first and first in table:
        return table[first]
    for key in table:
        if norm.startswith(key):
            return table[key]
    return None

# Apelidos/aliases que o aluno pode usar para pedir um consultor especifico.
# Chave = forma normalizada do nome completo aceito como preferred_attendant.
ATTENDANT_ALIASES = {
    'wesley':  ['wesley', 'weslei', 'wes ', 'wesly'],
    'felipe':  ['felipe', 'felipi'],
    'mariana': ['mariana', 'mari ', 'maryana'],
    'debora':  ['debora', 'débora'],
    'beatriz': ['beatriz', 'bia ', 'bea '],
    'camila':  ['camila', 'kamila'],
    'marilia': ['marilia', 'marília'],
    'julia':   ['julia', 'júlia'],
    'danubia': ['danubia', 'danúbia'],
    'gustavo': ['gustavo'],
    'joyce':   ['joyce'],
    'emanuel': ['emanuel', 'manuel'],
    'jessica': ['jessica', 'jéssica'],
}

ALMOCO_ANTE_MIN = 20
ALMOCO_DURACAO_MIN = 60
SAIDA_ANTE_MIN = 20

# ============================================================
# BLOQUEIO MANUAL DE CONSULTORES (override de emergencia)
# ============================================================
# (2026-06-01) ESVAZIADO: o controle de ativo/inativo agora eh 100% pelo
# DASHBOARD (campo `ativo_inativo` no Supabase, tabela de distribuicao).
# Tanto get_available_consultant quanto is_attendant_active_now ja
# consultam `ativo_inativo=eq.Ativo`, entao quem o painel marca como
# Inativo NAO recebe leads — sem precisar de lista fixa no codigo.
#
# Este set continua existindo APENAS como override manual de emergencia:
# se for preciso bloquear alguem IMEDIATAMENTE (sem mexer no painel),
# adicione o PRIMEIRO NOME em lowercase aqui e faca rebuild. Em condicoes
# normais deve ficar VAZIO.
_ATTENDANTS_ON_VACATION = set()

# ===================== HORÁRIO DE ATENDIMENTO (sobrescrito por agent_config) =====================
BUSINESS_HOURS_WEEKDAY_START = 8   # Seg-Sex início (mudado de 9 para 8 em 2026-05-21: alguns consultores entram 8h)
BUSINESS_HOURS_WEEKDAY_END = 20    # Seg-Sex fim (exclusivo)
BUSINESS_HOURS_SATURDAY_START = 8  # Sáb início (mantido coerente com weekday)
BUSINESS_HOURS_SATURDAY_END = 13   # Sábado fim (exclusivo)

# Janela "quase abrindo": faltam <= PRE_OPENING_MARGIN_MIN para o expediente comecar
# Quando True, agente NAO manda mensagem padrao after_hours - oferece entrar na fila
# antecipada. Resolve casos onde aluno escreve 8h45 e recebe "fora do horario".
PRE_OPENING_MARGIN_MIN = 60        # 1h antes do inicio
# Limite de quantos alunos pre-fila um consultor pode receber no morning burst
# antes do dispatch ir pro proximo consultor. Evita sobrecarga matinal.
PRE_OPENING_BURST_MAX_PER_ATTENDANT = 5

# Janela em minutos para considerar pedidos repetidos de "falar com atendente"
AFTER_HOURS_INSIST_WINDOW_MIN = 30

# Fila noturna: distribuição automática no horário comercial
AUTO_DISPATCH_MORNING_QUEUE = True
MORNING_DISPATCH_BATCH_SIZE = 25
MORNING_DISPATCH_RETRY_BATCH = 5
MORNING_DISPATCH_RETRY_COOLDOWN_S = 600
_last_pending_dispatch_ts = 0

# Templates de mensagens fora do horário
AFTER_HOURS_FIRST_MSG = (
    "Oii{name}! Nesse momento nosso time de atendimento humano está fora do horário, "
    "mas eu (assistente virtual) sigo por aqui pra tentar te ajudar agora mesmo 😊\n\n"
    "📅 *Segunda a Sexta*: 08h às 20h\n"
    "📅 *Sábado*: 08h às 13h\n\n"
    "Me conta o que você precisa que eu já vou tentando resolver com você."
)

AFTER_HOURS_INSIST_MSG = (
    "Entendi{name}! Para esse caso é melhor falar com um(a) consultor(a) mesmo, "
    "e o nosso time retorna o atendimento *{retorno_label}*. "
    "Vou deixar registrado por aqui pra que assim que abrir o horário, alguém te chame. "
    "Enquanto isso, se quiser, posso te ajudar com outras dúvidas — é só me dizer 😊"
)

RETENTION_AFTER_HOURS_MSG = (
    "Oii{name}, entendi 💙\n\n"
    "Essa é uma decisão importante e a gente quer te ouvir com a atenção que você merece. "
    "Para esse assunto, quem cuida com carinho é o nosso *time de Retenção*, especializado nisso.\n\n"
    "No momento estamos fora do horário de atendimento, mas assim que retomar *{retorno_label}* "
    "alguém da equipe entra em contato com você por aqui mesmo, tá? 😊\n\n"
    "Enquanto isso, se precisar de ajuda com *acesso, boleto, aulas* ou qualquer outra coisa, "
    "é só me chamar — eu sigo por aqui pra te ajudar."
)

# Mensagem dentro do horário, mas sem consultor disponível no momento
HUMAN_BUSY_MSG = (
    "Nossos atendentes estão todos em atendimento agora, mas fica tranquilo{name}! "
    "Em pouquinho alguém vai te chamar aqui 😊"
)

# Mensagens da janela "quase abrindo" — quando faltam minutos para o expediente
PRE_OPENING_MSG = (
    "Oii{name}! Nosso expediente abre {start_label} (em cerca de {mins_left} min). "
    "Quer que eu já te coloque na fila pra ser um dos primeiros a ser atendido(a) "
    "assim que abrir? 😊"
)
PRE_OPENING_BUTTONS = [
    {"id": "pre_opening_yes", "title": "Sim, entrar na fila"},
    {"id": "pre_opening_no",  "title": "Não, obrigado(a)"},
]

PRE_OPENING_ACCEPTED_MSG = (
    "Beleza{name}! Te coloquei aqui na fila ✅\n\n"
    "Assim que abrirmos {start_label}, um(a) consultor(a) vai te chamar por aqui mesmo. "
    "Enquanto isso, se tiver uma dúvida rápida (acesso, boleto, aulas etc), pode me mandar "
    "que eu já vou tentando resolver com você 😊"
)

PRE_OPENING_DECLINED_MSG = (
    "Sem problema{name}! Pode me mandar sua dúvida que eu já vou tentando te ajudar por aqui. "
    "Se preferir falar com um(a) consultor(a), é só me dizer 😊"
)

# Mantido por compatibilidade — usado apenas em logs/fallback genérico
OUT_OF_HOURS_MSG = AFTER_HOURS_FIRST_MSG

# ===================== FLOW CONSTANTS =====================

GREETINGS = {
    'o', 'oi', 'olá', 'ola', 'oii', 'oiii', 'oi!', 'olá!',
    'bom dia', 'boa tarde', 'boa noite', 'e aí', 'eai', 'e ai',
    'hello', 'hi', 'hey', 'fala', 'salve', 'opa', 'eae',
    'tudo bem', 'tudo bom', 'como vai', 'oi boa tarde',
    'oi bom dia', 'oi boa noite', 'bom dia!', 'boa tarde!', 'boa noite!',
    'oie', 'oiee', 'oláa', 'oiii!', 'opa!', 'bom diaa', 'boa tardee',
}

RESOLVED_WORDS = {
    'sim resolveu', 'resolveu', 'resolveu!', 'sim obrigado', 'sim obrigada',
    'resolvido', 'era isso', 'ajudou', 'ajudou!',
    # Variantes de "ja resolvi / consegui" (caso reportado: aluno diz "Ja resolvi"
    # e agente caia em escalate_low_conf por nao reconhecer — distribui errado)
    'resolvi', 'ja resolvi', 'já resolvi', 'eu resolvi', 'consegui resolver',
    'consegui aqui', 'consegui sim', 'consegui resolver aqui', 'consegui resolver agora',
    'ja consegui', 'já consegui',
    'deu certo', 'tudo certo agora', 'deu certo aqui', 'deu certo agora',
    'funcionou', 'ja funcionou', 'já funcionou', 'agora funcionou',
    'ja deu', 'já deu', 'ja foi', 'já foi',
    'consigo agora', 'consigo sim', 'consigo aqui',
}
ESCALATE_WORDS = {'falar com atendente', 'falar com atendimento', 'atendente', 'atendimento', 'humano', 'falar com alguem', 'transferir'}
CLOSING_WORDS = {'obrigado', 'obrigada', 'valeu', 'vlw', 'tchau', 'até mais', 'ate mais', 'brigado', 'brigada'}

FRUSTRATION_WORDS = [
    'não consigo', 'nao consigo', 'impossível', 'impossivel', 'absurdo',
    'problema', 'erro', 'travou', 'travando', 'não funciona', 'nao funciona',
    'urgente', 'urgência', 'demora', 'lentidão', 'reclamação', 'reclamacao',
    'raiva', 'irritado', 'cansado', 'frustrado', 'decepcionado', 'péssimo',
    'horrível', 'horroroso', 'vergonha', 'descaso', 'falta de respeito',
    'já tentei', 'ja tentei', 'várias vezes', 'varias vezes', 'nunca',
]

FOLLOWUP_HIGH_BUTTONS = ['Resolveu!', 'Tenho outra dúvida', 'Falar com atendente']
FOLLOWUP_MED_BUTTONS = ['Resolveu!', 'Tenho outra dúvida', 'Falar com atendente']
RESOLVED_BUTTONS = ['Tenho outra dúvida', 'Não, obrigado!']
_CLOSING_RESPONSES = [
    "Foi ótimo poder te ajudar{name_suffix}! Se precisar, é só chamar de novo. Até mais! 😊",
    "Fico feliz em ter ajudado{name_suffix}! Qualquer coisa, estou por aqui. Até a próxima! 😊",
    "Tudo certo então{name_suffix}! Se surgir algo mais, pode contar comigo. Até mais! 😊",
    "Que bom que deu tudo certo{name_suffix}! Estou sempre por aqui caso precise. Até logo! 😊",
]
CLOSING_RESPONSE_TPL = _CLOSING_RESPONSES[0]
ESCALATION_MSG = "Entendi! Vou te conectar com um dos nossos atendentes que vai poder te ajudar direitinho. Só um instante! 😊"

RETENTION_TAG_ID = '6fcefbd5-3c33-4e5c-b139-7f89718f6f0c'
RETENTION_WESLEY_CRM_ID = 'dd6cbed7-7666-45d1-bd90-368c8b97e217'

# (2026-06-25) Modelo de retenção via automação "Retenção IA" (tag RET-IA + n8n).
#  RET_IA_ALL=True  -> TODOS os alunos: retenção apenas ACIONA a automação (tag) e o
#                      bot SILENCIA (NÃO distribui, NÃO transfere, NÃO fala com o aluno).
#  RET_IA_ALL=False -> só os telefones em RET_IA_TEST_PHONES usam a automação; o
#                      restante segue a DISTRIBUIÇÃO normal (Wesley/Danúbia).
# Para reverter ao modelo de distribuição: defina RET_IA_ALL = False (e, se quiser,
# limpe RET_IA_TEST_PHONES).
RET_IA_TAG_ID = '18a49003-449b-473f-964f-1e0d2935b8e0'
RET_IA_ALL = True
RET_IA_TEST_PHONES = {'5511970617878', '11970617878'}


def _use_ret_ia_automation(phone):
    """True se a retenção deste telefone deve ACIONAR a automação RET-IA (em vez de
    distribuir). Com RET_IA_ALL=True vale para todos; senão só p/ RET_IA_TEST_PHONES."""
    if RET_IA_ALL:
        return True
    pn = ''.join(ch for ch in str(phone or '') if ch.isdigit())
    if not pn:
        return False
    return pn[-11:] in {p[-11:] for p in RET_IA_TEST_PHONES}
RETENTION_PHRASES = [
    'quero cancelar', 'quero trancar', 'vou cancelar', 'vou trancar',
    'cancelar meu curso', 'cancelar minha matrícula', 'cancelar minha matricula',
    'trancar meu curso', 'trancar minha matrícula', 'trancar minha matricula',
    'como faço para cancelar', 'como faco para cancelar',
    'como faço para trancar', 'como faco para trancar',
    'como cancelar', 'como trancar', 'como fazer o cancelamento',
    'como fazer o trancamento', 'como solicitar cancelamento',
    'como solicitar trancamento', 'como funciona o cancelamento',
    'como funciona o trancamento', 'prazo para cancelar', 'prazo para trancar',
    'cancelar o curso', 'trancar o curso', 'cancelar a matrícula', 'cancelar a matricula',
    'trancar a matrícula', 'trancar a matricula',
    'quero desistir', 'vou desistir', 'desistir do curso',
    'preciso cancelar', 'preciso trancar', 'desejo cancelar', 'desejo trancar',
    'gostaria de cancelar', 'gostaria de trancar',
    'quero realizar o cancelamento', 'quero fazer o cancelamento',
    'quero realizar o trancamento', 'quero fazer o trancamento',
    'cancelar matrícula', 'cancelar matricula', 'trancar matrícula', 'trancar matricula',
    # Formas no passado / ja realizado (aluno comunicando que cancelou/trancou)
    # Necessario para casos como "Mas eu cancelei minha matricula" cairem em retencao -> Wesley
    'cancelei minha matrícula', 'cancelei minha matricula',
    'cancelei meu curso', 'cancelei o curso', 'cancelei a matricula', 'cancelei a matrícula',
    'eu cancelei', 'já cancelei', 'ja cancelei',
    'tinha cancelado', 'havia cancelado',
    'tranquei minha matrícula', 'tranquei minha matricula',
    'tranquei meu curso', 'tranquei o curso',
    'eu tranquei', 'já tranquei', 'ja tranquei',
    'desisti do curso', 'eu desisti', 'já desisti', 'ja desisti',
    'fui cancelado', 'foi cancelado', 'minha matrícula foi cancelada', 'minha matricula foi cancelada',
    'foi cancelada', 'matricula cancelada', 'matrícula cancelada',
    'matricula foi cancelada', 'matrícula foi cancelada', 'a matricula foi cancelada',
]
RETENTION_QUESTION_WORDS = []
RETENTION_MSG = "Entendi sua situação. Vou te encaminhar para nosso consultor especializado que poderá te ajudar. Um momento, por favor!"

MAIN_MENU_BUTTONS = ['Acesso Portal/App', 'Financeiro', 'Aulas e Conteúdo', 'Documentos', 'Rematrícula', 'Falar com atendente']

SUBMENU = {
    'financeiro': {
        'text': 'Sobre *Financeiro*, qual sua dúvida?',
        'buttons': ['Boleto / Pagamento', 'Mensalidade / Valores', 'Negociar / Parcelar', 'Reembolso', 'Falar com atendente'],
    },
    'acesso': {
        'text': 'Sobre *Acesso*, qual sua dúvida?',
        'buttons': ['Primeiro acesso', 'Esqueci minha senha', 'App Duda', 'Blackboard / AVA', 'Falar com atendente'],
    },
    'academico': {
        'text': 'Sobre *Aulas e Conteúdo*, qual sua dúvida?',
        'buttons': ['Início das aulas', 'Disciplinas / Grade', 'Provas / Atividades', 'Material didático', 'Falar com atendente'],
    },
    'documentos': {
        'text': 'Sobre *Documentos*, o que precisa?',
        'buttons': ['Declaração de matrícula', 'Histórico escolar', 'Enviar documentos', 'Falar com atendente'],
    },
    'rematricula': {
        'text': 'Sobre *Rematrícula*, qual sua dúvida?',
        'buttons': ['Como rematricular', 'Prazo de rematrícula', 'Falar com atendente'],
    },
}

MAIN_MENU_KEYS = {
    'acesso portal/app': 'acesso', 'acesso': 'acesso',
    'financeiro': 'financeiro',
    'aulas e conteúdo': 'academico', 'aulas': 'academico',
    'documentos': 'documentos',
    'rematrícula': 'rematricula', 'rematricula': 'rematricula',
}

SUBMENU_L3 = {
    'boleto / pagamento': {
        'text': 'Sobre *Boleto / Pagamento*:',
        'buttons': ['Segunda via do boleto', 'Pagar com PIX', 'Boleto vencido', 'Falar com atendente'],
    },
    'boleto': {
        'text': 'Sobre *Boleto / Pagamento*:',
        'buttons': ['Segunda via do boleto', 'Pagar com PIX', 'Boleto vencido', 'Falar com atendente'],
    },
    'mensalidade / valores': {
        'text': 'Sobre *Mensalidade / Valores*:',
        'buttons': ['Valor da mensalidade', 'Desconto / Bolsa', 'Reajuste de mensalidade', 'Falar com atendente'],
    },
    'mensalidade': {
        'text': 'Sobre *Mensalidade / Valores*:',
        'buttons': ['Valor da mensalidade', 'Desconto / Bolsa', 'Reajuste de mensalidade', 'Falar com atendente'],
    },
    'negociação / parcelamento': {
        'text': 'Sobre *Negociação*:',
        'buttons': ['Parcelar dívida', 'Fazer acordo', 'Estou inadimplente', 'Falar com atendente'],
    },
    'negociacao / parcelamento': {
        'text': 'Sobre *Negociação*:',
        'buttons': ['Parcelar dívida', 'Fazer acordo', 'Estou inadimplente', 'Falar com atendente'],
    },
    'negociar / parcelar': {
        'text': 'Sobre *Negociação*:',
        'buttons': ['Parcelar dívida', 'Fazer acordo', 'Estou inadimplente', 'Falar com atendente'],
    },
    'negociar': {
        'text': 'Sobre *Negociação*:',
        'buttons': ['Parcelar dívida', 'Fazer acordo', 'Estou inadimplente', 'Falar com atendente'],
    },
    'primeiro acesso': {
        'text': 'Sobre *Primeiro Acesso*:',
        'buttons': ['Não recebi credenciais', 'Onde me cadastro', 'Email acadêmico', 'Falar com atendente'],
    },
    'provas / atividades': {
        'text': 'Sobre *Provas e Atividades*:',
        'buttons': ['Datas das provas', 'Prazo de atividades', 'Ver minhas notas', 'Falar com atendente'],
    },
    'provas': {
        'text': 'Sobre *Provas e Atividades*:',
        'buttons': ['Datas das provas', 'Prazo de atividades', 'Ver minhas notas', 'Falar com atendente'],
    },
}

SUBMENU_TO_QUESTION = {
    # L3 Financeiro
    'segunda via do boleto': 'como gerar segunda via do boleto de pagamento',
    'segunda via': 'como gerar segunda via do boleto de pagamento',
    'pagar com pix': 'como pagar a mensalidade com PIX',
    'pix': 'como pagar a mensalidade com PIX',
    'boleto vencido': 'meu boleto venceu o que fazer como pagar boleto vencido',
    'valor da mensalidade': 'qual o valor da mensalidade e como consultar valores',
    'desconto': 'como conseguir desconto ou bolsa na mensalidade',
    'bolsa': 'como conseguir desconto ou bolsa na mensalidade',
    'reajuste': 'por que a mensalidade teve reajuste e como contestar',
    'parcelar dívida': 'como parcelar mensalidades em atraso',
    'parcelar divida': 'como parcelar mensalidades em atraso',
    'fazer acordo': 'como fazer acordo de pagamento de dívida',
    'acordo': 'como fazer acordo de pagamento de dívida',
    'estou inadimplente': 'estou inadimplente o que acontece como regularizar',
    'inadimplente': 'estou inadimplente o que acontece como regularizar',
    'reembolso': 'como solicitar reembolso de pagamento',
    # L3 Acesso
    'não recebi credenciais': 'não recebi meus dados de acesso credenciais do portal',
    'nao recebi credenciais': 'não recebi meus dados de acesso credenciais do portal',
    'onde me cadastro': 'onde faço cadastro para acessar o portal do aluno',
    'email acadêmico': 'qual meu email acadêmico e como acessar',
    'email academico': 'qual meu email acadêmico e como acessar',
    'esqueci minha senha': 'esqueci minha senha do portal como redefinir',
    'app duda': 'como baixar e acessar o app Duda',
    'blackboard': 'como acessar o Blackboard ou ambiente virtual de aprendizagem',
    'ava': 'como acessar o Blackboard ou ambiente virtual de aprendizagem',
    # L3 Acadêmico
    'datas das provas': 'quando são as datas das provas do semestre',
    'prazo de atividades': 'qual o prazo para entrega de atividades',
    'ver minhas notas': 'como ver minhas notas e conceitos',
    'início das aulas': 'quando começam as aulas do semestre',
    'inicio das aulas': 'quando começam as aulas do semestre',
    'disciplinas': 'como ver minhas disciplinas e grade curricular',
    'grade': 'como ver minhas disciplinas e grade curricular',
    'material didático': 'como acessar o material didático das aulas',
    'material didatico': 'como acessar o material didático das aulas',
    # L2 direto (sem L3)
    'declaração de matrícula': 'como emitir declaração de matrícula ou vínculo',
    'declaracao': 'como emitir declaração de matrícula ou vínculo',
    'histórico escolar': 'como solicitar histórico escolar',
    'historico': 'como solicitar histórico escolar',
    'enviar documentos': 'como enviar documentos para a secretaria',
    'como rematricular': 'como fazer a rematrícula para o próximo semestre',
    'prazo de rematrícula': 'qual o prazo para rematrícula do semestre',
    'prazo de rematricula': 'qual o prazo para rematrícula do semestre',
}

# ===================== SYSTEM PROMPT =====================

SYSTEM_PROMPT = """Você é uma consultora de suporte da Cruzeiro do Sul Educacional. Seu nome não importa — você é uma pessoa real conversando pelo WhatsApp.

Sua personalidade: simpática, paciente, fala de um jeito leve e natural. Você NÃO é um robô. Você é alguém que realmente quer ajudar e conversa como gente de verdade.

{student_context}

{memory_context}

{sentiment_context}

{active_alerts}

## REGRAS ABSOLUTAS:
1. **NUNCA INVENTE** informações. Use SOMENTE as referências abaixo e alertas ativos.
2. **NUNCA afirme status de sistemas** (instabilidade, fora do ar) A MENOS que exista um ALERTA ATIVO.
3. **NUNCA INVENTE** URLs, valores, prazos ou procedimentos que NÃO estejam nas referências.
4. **NUNCA forneça dados pessoais** (RGM, e-mail acadêmico, senhas).
5. **DADOS ACADÊMICOS E DO CURSO**: Você pode ter acesso a curso, semestre, polo do aluno E informações detalhadas do curso (duração, grau, grade curricular, mercado de trabalho, áreas de atuação). NUNCA mencione esses dados proativamente na saudação. Use internamente para dar respostas mais precisas. Quando o aluno PERGUNTAR sobre grade/disciplinas, mercado de trabalho, duração ou áreas de atuação do curso, aí sim use os dados disponíveis. Se tiver o link da grade curricular, ENVIE quando perguntado sobre disciplinas/grade/matriz.
6. **NUNCA use nomes de atendentes** das referências (Joyce, Camila, Emanuel etc).
7. Use o nome do aluno ao longo da conversa de forma natural (não toda mensagem).
8. Se a referência tiver links ou vídeos, **INCLUA**.
9. **IGNORE** cumprimentos genéricos de atendentes, transcrições "Audio:", e pedidos de CPF. Extraia só informação útil.
10. **NUNCA ofereça transferir para atendente** por conta própria. Isso é controlado pelos botões do sistema.
11. **ENDEREÇO DE POLO — REGRA CRÍTICA**: NUNCA, JAMAIS, INVENTE endereço, rua, número, bairro, ponto de referência, horário ou CEP de polo. Se o aluno perguntar endereço/local de polo e isso NÃO estiver explicitamente no bloco "ENDEREÇOS OFICIAIS DOS POLOS" (quando presente), responda APENAS: "Deixa eu confirmar essa informação com a equipe para te passar certinho, tá?". O sistema cuida da transferência automática quando o aluno expressa intenção de visita ou dificuldade. NÃO mencione metrô, linha, terminal, hospital ou referência geográfica de polo se não estiver nas referências.
12. **INÍCIO DAS AULAS — REGRA CRÍTICA**: A data de início das aulas **depende da turma de ingresso de CADA aluno** (que vem da data de matrícula dele) — NÃO é um mês fixo igual pra todos. NUNCA diga um mês "padrão" (nem "agosto", nem "fevereiro") por conta própria, e NUNCA invente a data. O sistema já calcula e responde isso automaticamente a partir dos dados do aluno antes de você. Se por algum motivo você precisar responder sobre quando as aulas começam e NÃO tiver a data exata da turma daquele aluno nas referências, NÃO chute: responda "Pra te passar a data certinha de início das suas aulas, vou te conectar com um(a) consultor(a), tá?" — a transferência acontece automaticamente.
13. **ESQUECI MINHA SENHA — REGRA CRÍTICA**: O fluxo correto é por **SMS**, NÃO por e-mail. Procedimento oficial: o aluno clica em *Esqueci minha senha* na tela de login → digita o seu *telefone atualizado* → recebe um *código por SMS* → informa o código no campo indicado → cria a *nova senha*. NUNCA diga que ele "recebe um link no e-mail", "informa CPF e e-mail" ou "olha no spam do e-mail" — isso é informação ERRADA. Sempre lembre que o **telefone precisa estar atualizado** no cadastro pra o SMS chegar. Se o aluno disser que não recebeu o SMS, oriente que pode ser telefone desatualizado e ofereça transferir para consultor confirmar o cadastro.
14. **CALENDÁRIO ACADÊMICO — REGRA CRÍTICA**: Quando o aluno perguntar sobre DATAS (prova A1, prova AF, liberação de notas, início das aulas, fim do semestre, prazo de matrícula, transferência, retorno ao curso, dispensa, AC, TCE, ENADE, feriados acadêmicos), use APENAS as datas que aparecem no bloco "CALENDÁRIO ACADÊMICO GRADUAÇÃO 2026" quando ele for fornecido nas referências. NUNCA invente, deduza ou aproxime datas. Se a pergunta envolve um período/data que não está listada no bloco, responda: "Deixa eu confirmar essa data certinho com a equipe, tá? Já vou te conectar com um(a) consultor(a)." e a transferência acontece automaticamente. Para perguntas sobre data específica de uma matéria, oriente o aluno a consultar o Portal do Aluno (cronograma da disciplina). PROIBIDO usar a frase "para não te passar informação errada" — sempre prefira uma transição natural.
15. **BLACKBOARD x ÁREA DO ALUNO — REGRA CRÍTICA**: Os dois ambientes existem e têm finalidades **diferentes**. NUNCA confunda os dois:
   - **Blackboard (AVA)** — é o ambiente virtual de aprendizagem. É lá que o aluno acessa: *conteúdo das disciplinas*, *aulas gravadas*, *aulas ao vivo*, *atividades*, *materiais*, *fóruns*, *módulos*, *trabalhos da disciplina*. Quando o aluno disser que não está conseguindo acessar aula, atividade, material, módulo, conteúdo ou trabalho de uma disciplina, oriente-o a entrar no **Blackboard**.
   - **Área do Aluno (Portal do Aluno)** — é onde ficam: *prova regimental A1*, *prova regimental AF/Substitutiva* (em *Vida Acadêmica → Plataforma de Prova*), *boletos / financeiro*, *documentos*, *protocolos / CAA*, *histórico*, *grade*, *dados cadastrais*.
   NUNCA oriente o aluno a buscar conteúdo de disciplina, aula gravada, atividade ou material na Área do Aluno — isso é **erro**. Se as referências mostrarem algum print do ambiente da disciplina (com nome de matéria, módulo, semana de aula), trata-se do **Blackboard**, não da Área do Aluno. Para problemas de acesso ao Blackboard que não se resolvem com orientações básicas (limpar cache, navegador alternativo, conferir e-mail acadêmico), transfira para consultor.
16. **CONTATO DA COORDENAÇÃO — REGRA CRÍTICA**: NUNCA, JAMAIS, INVENTE e-mail, telefone, ramal, WhatsApp ou qualquer canal de contato da coordenação, secretaria, polo, financeiro ou da instituição. PROIBIDO usar expressões como "geralmente o e-mail é algo como", "deve ser algo como", "normalmente é" — isso é CHUTE e gera contato FALSO. Só informe um e-mail/telefone se ele estiver LITERALMENTE escrito nas referências da base de conhecimento. O contato com a coordenação do curso é feito pelo **ambiente virtual (Blackboard), na parte de _Organizações_** — oriente o aluno a acessar o Blackboard e procurar a opção *Organizações* para falar com a coordenação. Se o aluno NÃO encontrar essa opção ou tiver dificuldade, ofereça transferir para um(a) consultor(a) para ser atendido. NUNCA forneça um endereço de e-mail ou número de telefone "provável" da coordenação.

## COMO CONVERSAR (REGRA MAIS IMPORTANTE):
Você tá no WhatsApp. Ninguém manda textão no zap. Seja breve, natural e direta.

### Jeito de falar:
- Use contrações naturais: "tá", "pra", "vou te", "aí", "aqui", "pro"
- NUNCA comece com "Ei". Em vez disso, varie bastante os inícios: "Opa", "Olá", "Oii", "Ah", "Olha", "Bom", "Então", "Boa pergunta", "Claro", "Com certeza", "Pode deixar"
- NÃO seja formal demais. Nada de "Compreendo", "Informo que", "Segue abaixo" ou "Entendo sua frustração"
- Fale como uma amiga que trabalha na faculdade e tá te ajudando
- Pode usar expressões tipo "tranquilo", "sem stress", "show", "fechou", "beleza"
- Emoji com moderação (1-2 por resposta)
- Seja acolhedora e calorosa. Mostre que se importa com o aluno, não apenas responda a pergunta

### Fluxo de uma PRIMEIRA resposta sobre um problema:
1. **Acolhida rápida** (1 frase): algo leve, tipo "Opa, deixa eu ver isso pra você"
2. **Pergunta investigativa**: ANTES de dar a solução, pergunte o que tá acontecendo.
   Não despeje todas as soluções possíveis de uma vez. Investigue primeiro.

Exemplo - aluno diz "não consigo acessar o portal":

RUIM (textão robótico):
"Opa Marcelo! Vamos resolver. Não há instabilidade. Pelo computador acesse https://novoportal... Pelo celular use o DUDA... Se esqueceu a senha clique em Esqueci... Se trocou de celular revogue o Authenticator..."

BOM (conversa real):
"Opa Marcelo! Deixa eu dar uma olhada aqui... Não tem nenhum alerta de instabilidade, então tá tudo normal com o portal.

Me conta, o que acontece quando você tenta entrar? Dá algum erro, a página não abre, ou esqueceu a senha?"

### Fluxo após o aluno responder com mais detalhes:
Aí sim, dê a orientação ESPECÍFICA pro problema dele. Curta e direta.
Não repita o que já disse. Vá direto ao ponto.

### Quando o aluno já ESPECIFICOU o problema (ex: "esqueci minha senha"):
Ele JÁ te disse o que precisa. Não pergunte de volta. Resolve direto, mas com carinho:
"Opa, Marcelo! Pode ficar tranquilo que vou te ajudar com isso 😊

Lá na tela de login, clica em *Esqueci minha senha*. Ele vai te pedir o seu *telefone atualizado* e enviar um *código por SMS*. É só digitar o código no campo indicado e na sequência você consegue criar a sua *nova senha*.

Importante: o telefone precisa estar atualizado no cadastro pra o SMS chegar. Se não chegar nenhum SMS, me avisa que a gente vê o melhor caminho juntos 💙"

### Quando o aluno pede um link ou caminho específico:
Dê a orientação completa e acolhedora. Não responda de forma seca tipo "Acesse X > Y > Z":
"Olá, Jeniffer! Vou te explicar direitinho como acessar 😊

Pra chegar na prova A1, você vai entrar na sua *Área do Aluno*, depois clica em *Vida Acadêmica* e lá dentro vai ter a *Plataforma de Prova*.

É lá que ficam todas as avaliações regimentais! Se tiver qualquer dificuldade pra encontrar, me manda um print que te ajudo!"

### VARIAÇÃO (muito importante):
- NÃO comece todas as respostas com "Opa". Varie: "Olá", "Oii", "Ah", "Olha", "Bom", "Então", "Com certeza", "Pode deixar", "Claro"
- NUNCA use "Ei" para iniciar — é informal demais para atendimento
- NÃO termine todas com "Qualquer coisa me avisa". Varie: "Tô por aqui se precisar!", "Conta comigo!", "Qualquer dúvida é só chamar!", "Espero ter ajudado! 😊"
- NÃO use as mesmas frases de transição. Seja criativa
- Quando o aluno já disse o que precisa, dê a resposta COMPLETA e acolhedora, não apenas o caminho seco

## FORMATO:
- Separe parágrafos com \\n\\n para ficarem como balões separados no WhatsApp.
- Cada bloco deve ter NO MÁXIMO 2-3 frases.
- Use *negrito* para termos-chave.
- Última linha OBRIGATÓRIA (fica oculta pro aluno): [CONFIANCA:X.X]

## CONFIANÇA:
- Se as referências contêm informação relevante sobre o tema, sua confiança é ALTA (0.8+).
- Se você consegue dar UMA orientação útil, confiança MÉDIA (0.5-0.7).
- Confiança BAIXA (< 0.5) SOMENTE quando as referências NÃO têm NADA sobre o assunto.

## REFERÊNCIAS DA BASE DE CONHECIMENTO:
{references}

## HISTÓRICO DESTA CONVERSA:
{history}"""

# ===================== FOLLOW-UP & ENCERRAMENTO (defaults, sobrescritos pelo banco) =====================

FOLLOWUP_1_DELAY = 300
CLOSE_DELAY      = 900
FOLLOWUP_1_MSG     = "Oii{name}, tá tudo certo por aí? Se precisar de mais alguma coisa, pode mandar! 😊"
FOLLOWUP_1_BUTTONS = ['Tenho outra dúvida', 'Não, obrigado!']
CLOSE_INACTIVITY_MSG     = "Como você não respondeu, vou encerrar por aqui pra não te incomodar, tá? Se precisar de algo depois, é só me chamar de novo! Até mais ✨"
CLOSE_INACTIVITY_BUTTONS = None

# Última mensagem enviada (bot/automação) contém encerramento — Kommo/DataCrazy usam textos variados
LAST_MSG_CLOSE_PHRASES = (
    'muito obrigado por falar', 'encerrar por aqui',
    'encerrando esta conversa', 'não respondeu mais',
    'percebi que você não respondeu', 'atendimento será encerrado',
    'este atendimento foi encerrado', 'este atendimento foi finalizado',
    'atendimento foi encerrado', 'este atendimento está sendo encerrado',
    'atendimento está sendo encerrado', 'se quiser retornar para conversar',
    'se quiser retornar para conversar novamente',
    'foi encerrado por falta', 'foi encerrada por falta', 'encerrada devido',
    'atendimento foi finalizado', 'conversa foi encerrada',
)

# (2026-05-26) Frases que indicam follow-up em andamento — qualquer fonte:
# nosso agente IA, salesbot DCZ (Automacao), templates etc. Quando a ultima
# msg enviada contem qualquer uma destas, a conv ENTRA no monitoramento de
# inatividade e sera encerrada se o aluno nao responder dentro do prazo.
# Inclui frases do bot DCZ ("Veja as opcoes", "Seu e-mail de acesso",
# "Qual plataforma") — reportadas pelo usuario como ficando em loop sem
# encerramento.
_FU_TRIGGER_PHRASES = (
    # ---- Frases do agente IA ----
    'tudo certo por a', 'ainda est', 'não tive retorno', 'nao tive retorno',
    'pode mandar', 'precisar de mais alguma',
    'precisar de algo', 'precisa de algo',
    # ---- Frases do salesbot/automacao DCZ ----
    'veja as opções dispon', 'veja as opcoes dispon',
    'clique em uma das opções', 'clique em uma das opcoes',
    'escolha uma opção', 'escolha uma opcao',
    'qual plataforma você está', 'qual plataforma voce esta',
    'seu e-mail de acesso',
    'veja o tutorial', 'tutorial de primeiro acesso',
    'selecione para dar andamento', 'me conta, por favor',
    'me conta o que você gostaria', 'me conta o que voce gostaria',
    'já um de nossos consultores', 'ja um de nossos consultores',
    'oi, ainda está por aí', 'oi, ainda esta por ai',
    'como posso te ajudar', 'em que posso te ajudar',
)

# ===================== SAUDAÇÕES (defaults, sobrescritos pelo banco) =====================

GREETING_RETURNING = "Olá, *{fname}*! Que bom falar com você novamente 😊\n\nNa última vez que conversamos, você estava com algumas dúvidas sobre *{topic}* — espero que tenha conseguido te ajudar naquele momento.\n\nAgora me conta: como posso te ajudar hoje?\n\nEscolha uma opção abaixo para agilizar seu atendimento 👇"
GREETING_RETURNING_NO_TOPIC = "Olá, *{fname}*! Que bom falar com você novamente 😊\n\nNa última vez que conversamos, você estava com algumas dúvidas — espero que tenha conseguido te ajudar naquele momento.\n\nAgora me conta: como posso te ajudar hoje?\n\nEscolha uma opção abaixo para agilizar seu atendimento 👇"
GREETING_NEW = "Olá, *{fname}*! Bem-vindo(a) ao Suporte da *Cruzeiro do Sul* 😊\n\nComo posso te ajudar?\n\nEscolha uma opção abaixo para agilizar seu atendimento 👇"
GREETING_ANONYMOUS = "Olá! Bem-vindo ao Suporte ao Aluno da *Cruzeiro do Sul* 😊\n\nComo posso te ajudar?\n\nEscolha uma opção abaixo para agilizar seu atendimento 👇"
GREETING_BUTTONS = ['Acesso Portal/App', 'Financeiro', 'Aulas e Conteúdo', 'Documentos', 'Rematrícula', 'Falar com atendente']


def load_agent_config_from_db():
    """Carrega configs da tabela agent_config no PostgreSQL, sobrescrevendo defaults."""
    global FOLLOWUP_1_DELAY, CLOSE_DELAY
    global FOLLOWUP_1_MSG, FOLLOWUP_1_BUTTONS
    global CLOSE_INACTIVITY_MSG, CLOSE_INACTIVITY_BUTTONS
    global POLL_INTERVAL, CONFIDENCE_THRESHOLD, RESPONSE_COOLDOWN
    global GREETING_RETURNING, GREETING_RETURNING_NO_TOPIC, GREETING_NEW, GREETING_ANONYMOUS, GREETING_BUTTONS
    global BUSINESS_HOURS_WEEKDAY_START, BUSINESS_HOURS_WEEKDAY_END
    global BUSINESS_HOURS_SATURDAY_START, BUSINESS_HOURS_SATURDAY_END
    global AFTER_HOURS_FIRST_MSG, AFTER_HOURS_INSIST_MSG, RETENTION_AFTER_HOURS_MSG, HUMAN_BUSY_MSG
    mapping = {
        'followup_1_delay': ('FOLLOWUP_1_DELAY', int),
        'close_delay': ('CLOSE_DELAY', int),
        'followup_1_msg': ('FOLLOWUP_1_MSG', str),
        'followup_1_buttons': ('FOLLOWUP_1_BUTTONS', list),
        'close_msg': ('CLOSE_INACTIVITY_MSG', str),
        'close_buttons': ('CLOSE_INACTIVITY_BUTTONS', list),
        'poll_interval': ('POLL_INTERVAL', int),
        'confidence_threshold': ('CONFIDENCE_THRESHOLD', float),
        'response_cooldown': ('RESPONSE_COOLDOWN', float),
        'greeting_returning': ('GREETING_RETURNING', str),
        'greeting_returning_no_topic': ('GREETING_RETURNING_NO_TOPIC', str),
        'greeting_new': ('GREETING_NEW', str),
        'greeting_anonymous': ('GREETING_ANONYMOUS', str),
        'greeting_buttons': ('GREETING_BUTTONS', list),
        'business_hours_weekday_start': ('BUSINESS_HOURS_WEEKDAY_START', int),
        'business_hours_weekday_end': ('BUSINESS_HOURS_WEEKDAY_END', int),
        'business_hours_saturday_start': ('BUSINESS_HOURS_SATURDAY_START', int),
        'business_hours_saturday_end': ('BUSINESS_HOURS_SATURDAY_END', int),
        'after_hours_first_msg': ('AFTER_HOURS_FIRST_MSG', str),
        'after_hours_insist_msg': ('AFTER_HOURS_INSIST_MSG', str),
        'retention_after_hours_msg': ('RETENTION_AFTER_HOURS_MSG', str),
        'human_busy_msg': ('HUMAN_BUSY_MSG', str),
        'auto_dispatch_morning_queue': ('AUTO_DISPATCH_MORNING_QUEUE', bool),
        'morning_dispatch_batch_size': ('MORNING_DISPATCH_BATCH_SIZE', int),
    }
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS agent_config (
            key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMP DEFAULT NOW())""")
        conn.commit()
        cur.execute("SELECT key, value FROM agent_config")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        count = 0
        for key, value in rows:
            if key in mapping:
                var_name, typ = mapping[key]
                try:
                    parsed = json.loads(value)
                    if typ == list:
                        val = parsed if isinstance(parsed, list) else []
                        if not val:
                            val = None
                    elif typ == int:
                        val = int(parsed)
                    elif typ == float:
                        val = float(parsed)
                    elif typ == bool:
                        val = parsed if isinstance(parsed, bool) else str(parsed).lower() in ('1', 'true', 'yes', 'sim')
                    else:
                        val = str(parsed)
                    globals()[var_name] = val
                    count += 1
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
        if count > 0:
            print(f"[{time.strftime('%H:%M:%S')}]   Config DB carregada: {count} valores", flush=True)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}]   Config DB indisponivel (usando defaults): {e}", flush=True)


SUBMENU_DIRECT_RESPONSE = {}
_last_menu_load = 0

def _clean_menu_key(key):
    """Remove asteriscos e caracteres especiais da chave de menu."""
    return key.replace('*', '').strip().lower()

def load_menus_from_db():
    """Carrega menus da tabela agent_menus e reconstrói as estruturas."""
    global MAIN_MENU_BUTTONS, SUBMENU, MAIN_MENU_KEYS, SUBMENU_L3, SUBMENU_TO_QUESTION
    global SUBMENU_DIRECT_RESPONSE, _last_menu_load
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("SELECT id, parent_id, level, menu_key, label, response_text, rag_question, sort_order, active FROM agent_menus WHERE active = true ORDER BY sort_order, id")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        _last_menu_load = time.time()
        if not rows:
            print(f"[{time.strftime('%H:%M:%S')}]   Menus DB: tabela vazia, usando defaults hardcoded", flush=True)
            return

        by_id = {}
        children_of = {}
        for r in rows:
            mid, pid, level, mkey, label, resp_text, rag_q, sorder, active = r
            mkey = _clean_menu_key(mkey)
            by_id[mid] = {'id': mid, 'parent_id': pid, 'level': level, 'menu_key': mkey,
                          'label': label, 'response_text': resp_text, 'rag_question': rag_q}
            children_of.setdefault(pid, []).append(mid)

        new_buttons = []
        new_submenu = {}
        new_menu_keys = {}
        new_l3 = {}
        new_to_q = {}
        new_direct = {}

        def _register_leaf(item):
            key = item['menu_key']
            label_clean = _clean_menu_key(item['label'])
            if item.get('rag_question'):
                new_to_q[key] = item['rag_question']
                if label_clean != key:
                    new_to_q[label_clean] = item['rag_question']
                short = key.split(' / ')[0].strip()
                if short != key:
                    new_to_q[short] = item['rag_question']
            elif item.get('response_text'):
                new_direct[key] = item['response_text']
                if label_clean != key:
                    new_direct[label_clean] = item['response_text']

        l1_items = [by_id[mid] for mid in children_of.get(None, [])]
        for l1 in l1_items:
            new_buttons.append(l1['label'])
            key_lower = l1['menu_key']
            label_lower = _clean_menu_key(l1['label'])
            new_menu_keys[label_lower] = key_lower
            if label_lower != key_lower:
                new_menu_keys[key_lower] = key_lower

            l2_ids = children_of.get(l1['id'], [])
            l2_labels = []
            for l2id in l2_ids:
                item = by_id[l2id]
                l2_labels.append(item['label'])

                if item['level'] == 'leaf':
                    _register_leaf(item)
                elif item['level'] in ('L2', 'L3'):
                    l3_ids = children_of.get(item['id'], [])
                    l3_labels = []
                    for l3id in l3_ids:
                        leaf = by_id[l3id]
                        l3_labels.append(leaf['label'])
                        if leaf['level'] == 'leaf':
                            _register_leaf(leaf)

                    l3_labels.append('Falar com atendente')
                    l3_entry = {'text': item.get('response_text') or f"Sobre *{item['label']}*:", 'buttons': l3_labels}
                    new_l3[item['menu_key']] = l3_entry
                    label_clean = _clean_menu_key(item['label'])
                    if label_clean != item['menu_key']:
                        new_l3[label_clean] = l3_entry
                    short = item['menu_key'].split(' / ')[0].strip()
                    if short != item['menu_key']:
                        new_l3[short] = l3_entry

            l2_labels.append('Falar com atendente')
            new_submenu[key_lower] = {
                'text': l1.get('response_text') or f"Sobre *{l1['label']}*, qual sua dúvida?",
                'buttons': l2_labels
            }

        new_buttons.append('Falar com atendente')

        MAIN_MENU_BUTTONS = new_buttons
        SUBMENU = new_submenu
        MAIN_MENU_KEYS = new_menu_keys
        SUBMENU_L3 = new_l3
        SUBMENU_TO_QUESTION = new_to_q
        SUBMENU_DIRECT_RESPONSE = new_direct
        print(f"[{time.strftime('%H:%M:%S')}]   Menus DB: {len(l1_items)} cat, {len(new_l3)} L3, {len(new_to_q)} RAG, {len(new_direct)} diretos", flush=True)
        print(f"[{time.strftime('%H:%M:%S')}]   RAG keys: {list(new_to_q.keys())[:10]}...", flush=True)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}]   Menus DB erro (usando defaults): {e}", flush=True)


_last_reload_flag = ''
_last_restart_flag = ''

def maybe_reload():
    """Recarrega menus e configs se flag mudou ou mais de 60s desde última carga. Reinicia se restart solicitado."""
    global _last_menu_load, _last_reload_flag, _last_restart_flag
    force = False
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM agent_config WHERE key IN ('_reload_flag', '_restart_flag')")
        rows = {r[0]: r[1] for r in cur.fetchall()}
        cur.close()
        conn.close()

        restart_val = rows.get('_restart_flag', '')
        if restart_val and restart_val != _last_restart_flag:
            if _last_restart_flag:
                print(f"[{time.strftime('%H:%M:%S')}]   RESTART solicitado via Cockpit — reiniciando...", flush=True)
                lock_path = 'c:/Distribuicao_Academico/agent.lock'
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
                popen_kwargs = {'cwd': os.getcwd()}
                if os.name == 'nt':
                    popen_kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
                subprocess.Popen([sys.executable] + sys.argv, **popen_kwargs)
                sys.exit(0)
            _last_restart_flag = restart_val

        reload_val = rows.get('_reload_flag', '')
        if reload_val and reload_val != _last_reload_flag:
            _last_reload_flag = reload_val
            force = True
            print(f"[{time.strftime('%H:%M:%S')}]   Reload forçado via Cockpit", flush=True)
    except Exception:
        pass
    if force or time.time() - _last_menu_load > 60:
        load_menus_from_db()
        if force:
            load_agent_config_from_db()

# ===================== STATE =====================

processed_msg_ids = set()
conversation_greeted = set()
active_conv_id = None
student_profile = None
conversation_messages = []
last_response_time = 0
RESPONSE_COOLDOWN = 1.0
followup_stage = 0
waiting_for_client = False
inactivity_start = 0
_last_auto_skipped = False
_awaiting_cpf = False
_student_in_base = None
_awaiting_polo_confirm = False

# Estado por conversa para modo multi-atendimento
_conv_states = {}
_current_phone = None  # telefone do aluno sendo processado neste ciclo

def _default_conv_state():
    return {
        'student_profile': None,
        'conversation_messages': [],
        'followup_stage': 0,
        'waiting_for_client': False,
        'inactivity_start': 0,
        '_last_auto_skipped': False,
        '_awaiting_cpf': False,
        '_student_in_base': None,
        '_awaiting_polo_confirm': False,
        'phone': '',
        'greeted': False,
        '_human_took_over': False,
        '_last_responded_ts': 0,
        '_after_hours_escalation_count': 0,
        '_after_hours_escalation_ts': 0,
    }

def _load_conv_state(conv_id):
    """Carrega o estado de uma conversa nas variáveis globais."""
    global student_profile, conversation_messages, followup_stage, waiting_for_client
    global inactivity_start, _last_auto_skipped, _awaiting_cpf, _student_in_base
    global _awaiting_polo_confirm, active_conv_id, _current_phone
    if conv_id not in _conv_states:
        _conv_states[conv_id] = _default_conv_state()
    st = _conv_states[conv_id]
    student_profile = st['student_profile']
    conversation_messages = st['conversation_messages']
    followup_stage = st['followup_stage']
    waiting_for_client = st['waiting_for_client']
    inactivity_start = st['inactivity_start']
    _last_auto_skipped = st['_last_auto_skipped']
    _awaiting_cpf = st['_awaiting_cpf']
    _student_in_base = st['_student_in_base']
    _awaiting_polo_confirm = st['_awaiting_polo_confirm']
    _current_phone = st.get('phone', '')
    active_conv_id = conv_id
    if st.get('greeted'):
        conversation_greeted.add(conv_id)

def _save_conv_state(conv_id):
    """Salva o estado das variáveis globais de volta no dicionário."""
    prev = _conv_states.get(conv_id, {})
    human_flag = prev.get('_human_took_over', False)
    last_resp = prev.get('_last_responded_ts', 0)
    _conv_states[conv_id] = {
        'student_profile': student_profile,
        'conversation_messages': conversation_messages,
        'followup_stage': followup_stage,
        'waiting_for_client': waiting_for_client,
        'inactivity_start': inactivity_start,
        '_last_auto_skipped': _last_auto_skipped,
        '_awaiting_cpf': _awaiting_cpf,
        '_student_in_base': _student_in_base,
        '_awaiting_polo_confirm': _awaiting_polo_confirm,
        'phone': _current_phone,
        'greeted': conv_id in conversation_greeted,
        '_human_took_over': human_flag,
        '_last_responded_ts': last_resp,
    }

# ===================== HELPERS =====================

def p(msg):
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def get_db():
    return psycopg2.connect(**DB_CONFIG)


def is_greeting(text):
    normalized = text.lower().strip().rstrip('!?.,').strip()
    normalized = normalized.replace(',', ' ').replace('  ', ' ').strip()
    if normalized in GREETINGS:
        return True
    words = normalized.split()
    if len(words) <= 4 and any(w.rstrip('!?.,') in GREETINGS for w in words):
        return True
    if len(words) <= 4:
        joined = ' '.join(w.rstrip('!?.,') for w in words)
        if joined in GREETINGS:
            return True
        for i in range(len(words)):
            sub = ' '.join(w.rstrip('!?.,') for w in words[i:])
            if sub in GREETINGS:
                return True
    return False


def detect_sentiment(text):
    t = text.lower()
    frustration_score = sum(1 for w in FRUSTRATION_WORDS if w in t)
    if frustration_score >= 2:
        return 'frustrado'
    elif frustration_score == 1:
        return 'preocupado'
    return 'neutro'


def first_name(full_name):
    if not full_name:
        return None
    name = full_name.strip().split()[0].capitalize()
    if not any(c.isalpha() for c in name):
        return None
    return name


# ===================== FASE 1: IDENTIFICAÇÃO =====================

def identify_student(phone):
    """Busca dados do aluno na DataCrazy CRM pelo telefone."""
    try:
        search_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        r = requests.get(f'{DCZ_CRM}/leads', headers=H,
                        params={'search': search_phone, 'limit': 3}, timeout=10)
        if r.status_code != 200:
            p(f"    CRM lookup failed: {r.status_code}")
            return None

        data = r.json()
        if isinstance(data, list):
            leads = data
        elif isinstance(data, dict):
            leads = data.get('data', [])
        else:
            leads = []
        if not leads:
            p(f"    Aluno nao encontrado no CRM")
            return None

        lead = leads[0]
        profile = {
            'lead_id': lead.get('id') or '',
            'name': lead.get('name') or '',
            'first_name': first_name(lead.get('name') or ''),
            'phone': lead.get('rawPhone') or phone,
            'cpf': lead.get('taxId') or '',
            'email': lead.get('email') or '',
            'tags': [t.get('name', '') for t in (lead.get('tags') or [])],
            'notes': lead.get('notes') or '',
            'metrics': lead.get('metrics') or {},
            'created_at': lead.get('createdAt') or '',
        }
        p(f"    ALUNO: {profile['name']} | CPF: {profile['cpf'][:6]}*** | Tags: {profile['tags']}")
        return profile

    except Exception as e:
        p(f"    Erro CRM lookup: {e}")
        return None


def fetch_academic_data(cpf, phone=None):
    """Busca dados acadêmicos do aluno na mm_matriculados (dcz_sync, atualizada diariamente)."""
    if not cpf and not phone:
        return None
    try:
        acad_config = DB_CONFIG.copy()
        acad_config['dbname'] = 'dcz_sync'
        acad_config['connect_timeout'] = 5
        acad_config['options'] = '-c statement_timeout=10000'
        conn = psycopg2.connect(**acad_config)
        cur = conn.cursor()

        _ACAD_COLS = "nome, curso_limpo, serie, polo_aulas, situacao, tipo_matricula, email_ad, ano_tri_ingresso, tipo, curso_raw, data_matricula"
        _ACAD_KEYS = ['nome', 'curso', 'serie', 'polo', 'situacao', 'tipo_matricula',
                      'email_academico', 'ciclo', 'nivel', 'curso_raw', 'data_matricula']

        rows = []

        if cpf:
            clean_cpf = cpf.replace('.', '').replace('-', '').replace(' ', '').strip()
            if clean_cpf.isdigit() and len(clean_cpf) < 11:
                clean_cpf = clean_cpf.zfill(11)
            if len(clean_cpf) >= 9:
                cur.execute(f"""
                    SELECT {_ACAD_COLS} FROM mm_matriculados
                    WHERE cpf = %s AND situacao = 'Matriculado'
                    ORDER BY serie DESC
                """, (clean_cpf,))
                rows = cur.fetchall()
                if not rows:
                    cur.execute(f"""
                        SELECT {_ACAD_COLS} FROM mm_matriculados
                        WHERE cpf = %s
                        ORDER BY serie DESC LIMIT 1
                    """, (clean_cpf,))
                    rows = cur.fetchall()

        if not rows and phone:
            clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '').strip()
            clean_phone = clean_phone[-11:] if len(clean_phone) >= 11 else clean_phone
            if len(clean_phone) >= 10:
                cur.execute(f"""
                    SELECT {_ACAD_COLS} FROM mm_matriculados
                    WHERE (fone_cel LIKE %s OR fone_res LIKE %s OR fone_com LIKE %s)
                    AND situacao = 'Matriculado'
                    ORDER BY serie DESC
                """, (f'%{clean_phone}', f'%{clean_phone}', f'%{clean_phone}'))
                rows = cur.fetchall()

        conn.close()

        if not rows:
            return None

        def _extract_grau(curso_raw):
            if not curso_raw:
                return ''
            cr = curso_raw.strip().upper()
            if cr.startswith('CST '):
                return 'CST (Tecnólogo)'
            if '(BACHARELADO)' in cr:
                return 'Bacharelado'
            if '(LICENCIATURA)' in cr:
                return 'Licenciatura'
            return ''

        courses = []
        for row in rows:
            acad = dict(zip(_ACAD_KEYS, row))
            if acad.get('polo'):
                acad['polo'] = (acad['polo'] or '').strip()
            acad['grau'] = _extract_grau(acad.pop('curso_raw', ''))
            courses.append(acad)

        result = courses[0].copy()
        if len(courses) > 1:
            result['_all_courses'] = courses
        p(f"    [ACAD] {result.get('curso','?')[:40]} | {result.get('grau','')} | Sem {result.get('serie','?')} | {result.get('situacao','?')} | Polo: {result.get('polo','?')[:20]}" +
          (f" (+{len(courses)-1} cursos)" if len(courses) > 1 else ""))
        return result
    except Exception as e:
        p(f"    [ACAD] Erro ao buscar dados academicos: {e}")
        return None


def fetch_caa_solicitacoes(cpf, limit=15):
    """Busca solicitacoes CAA do aluno no SIAA (snapshot diario).
    Retorna lista ordenada por data_chegada DESC (mais recentes primeiro).
    """
    if not cpf:
        return []
    import re as _re
    clean = _re.sub(r'\D', '', str(cpf))
    if len(clean) < 9:
        return []
    if len(clean) < 11:
        clean = clean.zfill(11)
    if len(clean) > 11:
        clean = clean[-11:]
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT subprocesso, data_chegada, data_previsao, data_conclusao,
                   protocolo, aging_dias, observacao,
                   situacao_atendimento, situacao_deferimento
            FROM caa_solicitacoes
            WHERE cpf = %s
            ORDER BY data_chegada DESC NULLS LAST, id DESC
            LIMIT %s
        """, (clean, limit))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        if rows:
            open_n = sum(1 for r in rows if 'em aberto' in (r.get('situacao_deferimento') or '').lower())
            p(f"    [CAA] {len(rows)} solicitacao(oes) | em aberto: {open_n}")
        return rows
    except Exception as e:
        # Tabela pode nao existir ainda (antes do primeiro upload) - silencioso.
        msg = str(e).lower()
        if 'caa_solicitacoes' not in msg and 'does not exist' not in msg:
            p(f"    [CAA] Erro lookup: {e}")
        return []


# ================================================================
# CALENDARIO ACADEMICO GRADUACAO EaD 2026
# Fonte: PDF oficial (2026-05-25). Apenas graduacao.
# ================================================================

def _ensure_academic_calendar_table(cur):
    """Cria tabela academic_calendar_2026 se ainda nao existir."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS academic_calendar_2026 (
            id SERIAL PRIMARY KEY,
            categoria VARCHAR(40) NOT NULL,
            titulo VARCHAR(255) NOT NULL,
            data_inicio DATE NOT NULL,
            data_fim DATE,
            mes_ref VARCHAR(40),
            semestre VARCHAR(16),
            publico VARCHAR(64) DEFAULT 'todos',
            observacao TEXT,
            ativo BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (categoria, titulo, data_inicio)
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_acad_cal_data ON academic_calendar_2026 (data_inicio)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_acad_cal_cat ON academic_calendar_2026 (categoria, ativo)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_acad_cal_sem ON academic_calendar_2026 (semestre, ativo)"
    )


def _seed_academic_calendar_if_empty():
    """Popula tabela com seed canonico apenas se estiver vazia.

    Roda 1x. Para atualizar/editar eventos, usar o painel admin Cockpit ou
    INSERT manual. Reexecutar este seed nao sobrescreve registros existentes.
    """
    try:
        from calendar_2026_seed import get_seed_events
    except Exception as e:
        p(f"    [CAL] Seed module indisponivel: {e}")
        return
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        _ensure_academic_calendar_table(cur)
        cur.execute("SELECT COUNT(*) FROM academic_calendar_2026")
        n = cur.fetchone()[0] or 0
        if n > 0:
            cur.close()
            conn.close()
            return
        events = get_seed_events()
        inserted = 0
        for ev in events:
            try:
                cur.execute("""
                    INSERT INTO academic_calendar_2026
                        (categoria, titulo, data_inicio, data_fim,
                         mes_ref, semestre, publico, observacao, ativo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (categoria, titulo, data_inicio) DO NOTHING
                """, (
                    ev.get('categoria'), ev.get('titulo'),
                    ev.get('data_inicio'), ev.get('data_fim'),
                    ev.get('mes_ref'), ev.get('semestre'),
                    ev.get('publico') or 'todos',
                    ev.get('observacao') or '',
                ))
                inserted += cur.rowcount or 0
            except Exception as e:
                p(f"    [CAL] Falha INSERT evento '{ev.get('titulo')}': {e}")
        conn.commit()
        cur.close()
        conn.close()
        p(f"    [CAL] Seed inicial concluido: {inserted} eventos inseridos.")
    except Exception as e:
        p(f"    [CAL] Erro no seed: {e}")


def _fetch_calendar_events(filters=None, limit=200):
    """Busca eventos do calendario. filters opcional para refinar query.

    filters keys aceitas: categoria (str ou list), semestre, publico_contains,
    data_min, data_max, ativo.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ensure_academic_calendar_table(cur)
        where = ["ativo = TRUE"]
        args = []
        f = filters or {}
        cat = f.get('categoria')
        if cat:
            if isinstance(cat, (list, tuple, set)):
                where.append("categoria = ANY(%s)")
                args.append(list(cat))
            else:
                where.append("categoria = %s")
                args.append(cat)
        if f.get('semestre'):
            where.append("(semestre = %s OR semestre IS NULL)")
            args.append(f['semestre'])
        if f.get('publico_contains'):
            where.append("(publico ILIKE %s OR publico ILIKE 'todos')")
            args.append(f"%{f['publico_contains']}%")
        if f.get('data_min'):
            where.append("(data_fim IS NULL AND data_inicio >= %s OR data_fim >= %s)")
            args.extend([f['data_min'], f['data_min']])
        if f.get('data_max'):
            where.append("data_inicio <= %s")
            args.append(f['data_max'])
        sql = (
            "SELECT id, categoria, titulo, data_inicio, data_fim, mes_ref, "
            "semestre, publico, observacao FROM academic_calendar_2026 "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY data_inicio ASC LIMIT %s"
        )
        args.append(limit)
        cur.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        msg = str(e).lower()
        if 'academic_calendar_2026' not in msg and 'does not exist' not in msg:
            p(f"    [CAL] Erro fetch: {e}")
        return []


_CALENDAR_TOPIC_KEYWORDS = {
    'prova': ['prova', 'avaliação', 'avaliacao', 'a1', 'af', 'recuperação', 'recuperacao'],
    'nota': ['nota', 'resultado', 'gabarito', 'liberação de nota', 'liberacao de nota'],
    'matricula': ['matrícula', 'matricula', 'rematrícula', 'rematricula', 'inscrição', 'inscricao'],
    'inicio_aulas': ['início das aulas', 'inicio das aulas', 'começam as aulas',
                     'comecam as aulas', 'quando começa', 'quando comeca',
                     'quando inicia', 'inicio do curso', 'início do curso',
                     'data de inicio', 'data de início', 'inicia as aulas',
                     'inicia o curso', 'começa o curso', 'comeca o curso',
                     'em que mês', 'em que mes', 'que mês começa',
                     'que mes comeca', 'que mês inicia', 'que mes inicia',
                     'vou começar', 'vou comecar', 'começo as aulas',
                     'comeco as aulas'],
    'transferencia': ['transferência', 'transferencia', 'segunda graduação',
                      'segunda graduacao', '2a graduação', '2a graduacao'],
    'retorno_curso': ['destrancamento', 'retorno', 'reativação', 'reativacao', 'voltar ao curso'],
    'dispensa': ['dispensa', 'aproveitamento de disciplina'],
    'ac': ['atividade complementar', 'atividades complementares', ' ac ', 'horas complementares'],
    'estagio': ['estágio', 'estagio', 'tce'],
    'feriado': ['feriado', 'recesso'],
    'semestre': ['semestre', 'período letivo', 'periodo letivo'],
    'disciplinas_especiais': ['disciplina especial', 'disciplinas especiais',
                              'concluinte', 'reprovado', 'dp ', 'dependência', 'dependencia'],
    'enade': ['enade'],
    'evento': ['jornada acadêmica', 'jornada academica', 'semana de cursos'],
}


_CATEGORY_BY_TOPIC = {
    'prova': ['prova_a1', 'prova_af', 'recurso_a1', 'recurso_af'],
    'nota': ['liberacao_notas', 'liberacao_notas_af'],
    'matricula': ['matricula'],
    'inicio_aulas': ['aula_inicio', 'periodo_letivo'],
    'transferencia': ['transferencia_externa', 'transferencia_interna'],
    'retorno_curso': ['retorno_curso'],
    'dispensa': ['dispensa'],
    'ac': ['ac'],
    'estagio': ['estagio'],
    'feriado': ['feriado'],
    'semestre': ['periodo_letivo'],
    'disciplinas_especiais': ['disciplinas_especiais'],
    'enade': ['enade'],
    'evento': ['evento'],
}


def _detect_calendar_topic(text):
    """Retorna lista de topicos de calendario detectados na mensagem (str)."""
    if not text:
        return []
    t = text.lower()
    hits = []
    for topic, kws in _CALENDAR_TOPIC_KEYWORDS.items():
        if any(k in t for k in kws):
            hits.append(topic)
    return hits


def _student_semester_hint(student_profile):
    """Inferir semestre relevante (2026.1, 2026.2) a partir do perfil.

    Heuristica: usa o mes/data atual + indicio do tipo_matricula.
    """
    try:
        from datetime import date as _date
        today = _date.today()
        if today.year == 2026:
            return '2026.2' if today.month >= 7 else '2026.1'
        if today.year == 2027 and today.month <= 7:
            return '2027.1'
    except Exception:
        pass
    return None


def _get_relevant_calendar_events(student_profile=None, user_message=None,
                                  max_events=10):
    """Retorna eventos do calendario relevantes para o aluno e topico.

    Estrategia:
    - Filtra por data: somente eventos com data_inicio nas proximas 240 dias
      ou em janela aberta (data_fim >= hoje).
    - Filtra por categoria conforme os topicos detectados na mensagem.
    - NAO filtra por semestre por padrao: os semestres se cruzam ao longo
      do ano (ex: matricula de 2026.2 ja esta aberta dentro de 2026.1).
      O filtro de data ja cuida disso ao excluir eventos passados.
    - Aplica preferencia de publico (calouro/veterano) se identificado no
      perfil, mas sempre inclui eventos publico='todos'.
    """
    try:
        from datetime import date as _date, timedelta as _td
        today = _date.today()
        topics = _detect_calendar_topic(user_message or '')
        # FIX (2026-05-25): se a mensagem NAO bate com nenhum topico de
        # calendario, NAO injeta nada. Bug anterior: sem topico, o filtro
        # nao restringia categoria -> todos os ~80 eventos dos proximos 240
        # dias eram injetados e _mark_calendar_used forcava tema=CALENDARIO
        # em conversas de acesso, financeiro, app DUDA, etc. Resultado: a
        # aba CALENDARIO no Cockpit virou um catch-all errado.
        if not topics:
            return []
        cats = []
        for tp in topics:
            cats.extend(_CATEGORY_BY_TOPIC.get(tp, []))
        categorias = list(dict.fromkeys(cats)) or None

        publico_hint = None
        if student_profile:
            acad = (student_profile or {}).get('academic') or {}
            tipo = (acad.get('tipo_matricula') or '').lower()
            if 'calouro' in tipo:
                publico_hint = 'calouro'
            elif 'veterano' in tipo or 'rematric' in tipo:
                publico_hint = 'veterano'

        filters = {
            'data_min': today,
            'data_max': today + _td(days=240),
        }
        if categorias:
            filters['categoria'] = categorias

        events = _fetch_calendar_events(filters=filters, limit=80)

        if publico_hint:
            events = [
                e for e in events
                if (e.get('publico') or 'todos').lower() == 'todos'
                or publico_hint in (e.get('publico') or '').lower()
            ]
        return events[:max_events]
    except Exception as e:
        p(f"    [CAL] Erro filtro relevantes: {e}")
        return []


# Registro em memoria das conversas onde o calendario foi injetado nas
# ultimas N segundos. Usado pelo tabulador para forcar tema='CALENDARIO'
# sem depender do LLM classificador acertar sozinho.
_calendar_marked_convs = {}
_CALENDAR_MARK_TTL = 1800  # 30 min — janela razoavel ate a tabulacao rodar


def _mark_calendar_used(conv_id):
    if conv_id is None:
        return
    try:
        _calendar_marked_convs[str(conv_id)] = time.time()
    except Exception:
        pass


def _consume_calendar_mark(conv_id):
    """Retorna True se o calendario foi injetado para conv_id na janela TTL.

    A marca eh REMOVIDA apos consumo para nao 'colar' tema CALENDARIO em
    tabulacoes subsequentes que ja nao envolvem datas. Se o aluno fizer
    nova pergunta sobre datas, _mark_calendar_used reescreve a marca.
    """
    if conv_id is None:
        return False
    key = str(conv_id)
    ts = _calendar_marked_convs.pop(key, None)
    if not ts:
        return False
    if (time.time() - ts) > _CALENDAR_MARK_TTL:
        return False
    return True


def _cleanup_calendar_marks():
    """Remove marcas antigas (>TTL) periodicamente para nao crescer indefinido."""
    try:
        now = time.time()
        stale = [k for k, ts in _calendar_marked_convs.items()
                 if (now - ts) > _CALENDAR_MARK_TTL]
        for k in stale:
            _calendar_marked_convs.pop(k, None)
    except Exception:
        pass


def _format_calendar_block(events, header="CALENDARIO ACADEMICO GRADUACAO 2026"):
    """Formata eventos como bloco de texto para injetar no contexto do LLM."""
    if not events:
        return ''
    lines = [header + ':']
    for ev in events:
        di = ev.get('data_inicio')
        df = ev.get('data_fim')
        if hasattr(di, 'strftime'):
            di_s = di.strftime('%d/%m/%Y')
        else:
            di_s = str(di)
        if df:
            if hasattr(df, 'strftime'):
                df_s = df.strftime('%d/%m/%Y')
            else:
                df_s = str(df)
            data_str = f"{di_s} a {df_s}"
        else:
            data_str = di_s
        titulo = ev.get('titulo') or ''
        obs = ev.get('observacao') or ''
        if obs:
            lines.append(f"- {data_str}: {titulo} ({obs})")
        else:
            lines.append(f"- {data_str}: {titulo}")
    lines.append(
        "Use APENAS as datas acima. NUNCA invente datas. Se a pergunta nao "
        "estiver coberta, oriente o aluno a consultar o Portal do Aluno."
    )
    return "\n".join(lines)


def _parse_course_content(content):
    """Faz parse do campo content da tabela documents (formato chave:valor separado por /)."""
    result = {}
    try:
        raw = content.replace('"', '').strip()
        nome_part = raw.split(';')[0].replace('nome', '').strip() if ';' in raw else ''
        info_start = raw.find('info:')
        if info_start >= 0:
            nome_part = raw[info_start + 5:].split(';')[0].strip()
        result['nome'] = nome_part

        def _extract(key):
            k = key + ':'
            idx = raw.find(k)
            if idx < 0:
                return ''
            start = idx + len(k)
            end = len(raw)
            for sep in ['/', '"']:
                pos = raw.find(sep, start)
                if 0 < pos < end:
                    end = pos
            for next_key in ['descricao_curso:', 'mercado_trabalho:', 'area_de_atuacao:',
                             'grade_do_curso:', 'duracao_curso:', 'grau_curso:',
                             'curso tem:', 'area_do_curso:']:
                if next_key != k:
                    pos = raw.find(next_key, start)
                    if 0 < pos < end:
                        end = pos
            return raw[start:end].strip().rstrip(';').strip()

        result['descricao'] = _extract('descricao_curso')
        result['mercado_trabalho'] = _extract('mercado_trabalho')
        result['areas_atuacao'] = _extract('area_de_atuacao')
        result['grade_link'] = _extract('grade_do_curso')
        result['duracao'] = _extract('duracao_curso').split()[0] if _extract('duracao_curso') else ''
        result['grau'] = _extract('grau_curso')
        result['area_curso'] = _extract('area_do_curso')
    except Exception as e:
        p(f"    [GRADE] Erro no parse: {e}")
    return result


def _normalize_for_search(text):
    """Remove acentos para busca case-insensitive."""
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn').lower()


def fetch_course_info(course_name):
    """Busca informações do curso na tabela documents do Supabase de grades.
    Usa cache em memória para evitar chamadas repetidas."""
    if not course_name:
        return None
    if not GRADES_SUPABASE_URL:
        if not getattr(fetch_course_info, '_warned', False):
            p("  [GRADE] GRADES_SUPABASE_URL nao configurada - busca de grade desativada")
            fetch_course_info._warned = True
        return None
    norm_name = course_name.strip().upper()
    if norm_name in _course_info_cache:
        return _course_info_cache[norm_name]
    try:
        clean = course_name.strip().split('(')[0].strip()
        clean = clean.split(' - ')[0].strip() if ' - ' in clean else clean
        words = [w for w in clean.split() if len(w) > 2]
        main_word = words[0] if words else clean

        all_rows = []
        search_variants = [clean]
        if main_word != clean:
            search_variants.append(main_word)
        prefix = clean[:5] if len(clean) >= 5 else clean
        if prefix.lower() != clean.lower() and prefix.lower() != main_word.lower():
            search_variants.append(prefix)
        for search_term in search_variants:
            if all_rows and len(all_rows) >= 5:
                break
            r = requests.get(
                f'{GRADES_SUPABASE_URL}/rest/v1/documents',
                headers=GRADES_HEADERS,
                params={'select': 'id,content', 'content': f'ilike.*{search_term}*', 'limit': '20'},
                timeout=15
            )
            if r.status_code == 200:
                new_rows = r.json()
                seen_ids = {row['id'] for row in all_rows}
                all_rows.extend(row for row in new_rows if row['id'] not in seen_ids)

        if not all_rows:
            p(f"    [GRADE] Nenhum curso encontrado para '{clean}'")
            _course_info_cache[norm_name] = None
            return None

        search_norm = _normalize_for_search(clean)
        search_words = search_norm.split()
        best = None
        best_score = -1
        for row in all_rows:
            content = row.get('content', '')
            info_start = content.find('info:')
            if info_start >= 0:
                nome_in_content = content[info_start+5:].split(';')[0].strip()
            else:
                nome_in_content = content.split(';')[0].replace('nome', '').strip()
            nome_norm = _normalize_for_search(nome_in_content)

            score = 0
            for w in search_words:
                if w in nome_norm:
                    score += 2
            if search_norm in nome_norm or nome_norm.startswith(search_norm):
                score += 20
            nome_first = nome_norm.split(' - ')[0].strip() if ' - ' in nome_norm else nome_norm
            search_first = search_norm.split(' - ')[0].strip() if ' - ' in search_norm else search_norm
            if nome_first == search_first:
                score += 50

            if score > best_score:
                best_score = score
                best = row

        if not best:
            best = all_rows[0]
        if best_score <= 0:
            p(f"    [GRADE] Score muito baixo ({best_score}) para '{clean}' -> ignorando")
            _course_info_cache[norm_name] = None
            return None
        parsed = _parse_course_content(best['content'])
        parsed['_doc_id'] = best.get('id')
        _course_info_cache[norm_name] = parsed
        p(f"    [GRADE] Curso encontrado: {parsed.get('nome','?')[:50]} (score={best_score})")
        return parsed
    except Exception as e:
        p(f"    [GRADE] Erro ao buscar curso: {e}")
        return None


def search_courses_by_query(query, limit=5):
    """Busca cursos por texto livre na tabela documents."""
    if not query:
        return []
    try:
        words = query.strip().split()
        main_word = max(words, key=len) if words else query
        r = requests.get(
            f'{GRADES_SUPABASE_URL}/rest/v1/documents',
            headers=GRADES_HEADERS,
            params={'select': 'id,content', 'content': f'ilike.*{main_word}*', 'limit': '30'},
            timeout=15
        )
        if r.status_code != 200:
            return []
        rows = r.json()
        results = []
        query_lower = query.lower()
        for row in rows:
            parsed = _parse_course_content(row['content'])
            score = sum(1 for w in words if w.lower() in (parsed.get('nome', '') or '').lower())
            if query_lower in (parsed.get('area_curso', '') or '').lower():
                score += 2
            results.append((score, parsed))
        results.sort(key=lambda x: -x[0])
        return [r[1] for r in results[:limit] if r[0] > 0]
    except Exception as e:
        p(f"    [GRADE] Erro busca cursos: {e}")
        return []


def check_lead_has_pipeline(phone, pipeline_id=None):
    """Verifica se o lead tem negócio no pipeline de alunos ativos."""
    if pipeline_id is None:
        pipeline_id = PIPELINE_ALUNOS_ID
    try:
        search_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        r = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                        params={'search': search_phone, 'limit': 10}, timeout=10)
        if r.status_code != 200:
            p(f"    Pipeline check failed: {r.status_code}")
            return False
        data = r.json()
        biz_list = data.get('data', data) if isinstance(data, dict) else data
        if not isinstance(biz_list, list):
            return False
        for biz in biz_list:
            biz_pipeline = biz.get('pipelineId') or biz.get('pipeline', {}).get('id', '')
            if biz_pipeline == pipeline_id:
                p(f"    Lead encontrado no pipeline de alunos ativos")
                return True
        p(f"    Lead NÃO encontrado no pipeline de alunos ({len(biz_list)} negócios verificados)")
        return False
    except Exception as e:
        p(f"    Erro pipeline check: {e}")
        return False


def validate_student_cpf_webhook(cpf, phone, lead_id='', business_id='', name=''):
    """Chama webhook n8n para validar CPF na base acadêmica."""
    try:
        clean_cpf = cpf.replace('.', '').replace('-', '').replace(' ', '').strip()
        clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        payload = {
            'cpf': clean_cpf,
            'telefone': clean_phone,
            'id_lead': lead_id,
            'id_negocio': business_id,
            'Nome': name,
        }
        p(f"    Webhook CPF: enviando CPF={clean_cpf[:6]}*** phone={clean_phone[-4:]}")
        r = requests.post(N8N_WEBHOOK_LEADS_CPF, params=payload, headers=H, timeout=30)
        p(f"    Webhook CPF: status={r.status_code}")
        return r.status_code in (200, 201)
    except Exception as e:
        p(f"    Erro webhook CPF: {e}")
        return False


def check_lead_exists_field(lead_id):
    """Verifica o campo adicional 'Lead Existe?' no lead do DataCrazy."""
    try:
        r = requests.get(f'{DCZ_CRM}/leads/{lead_id}', headers=H, timeout=10)
        if r.status_code != 200:
            p(f"    Lead field check failed: {r.status_code}")
            return None
        lead_data = r.json()
        additional = lead_data.get('additionalFields') or lead_data.get('additional_fields') or {}
        if isinstance(additional, list):
            for field in additional:
                if field.get('name', '').lower().strip() in ('lead existe?', 'lead existe'):
                    val = str(field.get('value', '')).lower().strip()
                    p(f"    Lead Existe? = '{val}'")
                    return val == 'sim'
        elif isinstance(additional, dict):
            for key, val in additional.items():
                if key.lower().strip() in ('lead existe?', 'lead existe'):
                    val_str = str(val).lower().strip()
                    p(f"    Lead Existe? = '{val_str}'")
                    return val_str == 'sim'
        custom = lead_data.get('customFields') or {}
        if isinstance(custom, dict):
            for key, val in custom.items():
                if 'lead' in key.lower() and 'existe' in key.lower():
                    val_str = str(val).lower().strip()
                    p(f"    Lead Existe? (custom) = '{val_str}'")
                    return val_str == 'sim'
        p(f"    Campo 'Lead Existe?' não encontrado no lead")
        return None
    except Exception as e:
        p(f"    Erro check lead exists: {e}")
        return None


def create_lead_and_business(phone, name=''):
    """Cria lead e negócio no DataCrazy (para alunos não encontrados que dizem ser alunos).

    (2026-05-25) Adicionado retry (3 tentativas) com backoff progressivo.
    Caso reportado: alunos eram distribuidos para consultor mas o lead NAO
    era criado no CRM (painel mostrava 'Lead nao encontrado'), porque uma
    unica falha 5xx/timeout do DCZ matava a criacao. Com 3 tries o lead
    persiste mesmo com instabilidade momentanea da API.
    """
    clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
    body = {'phone': clean_phone, 'name': name or clean_phone}
    new_lead_id = ''
    for attempt in (1, 2, 3):
        try:
            r = requests.post(f'{DCZ_CRM}/leads', headers=H, json=body, timeout=12)
            if r.status_code in (200, 201):
                try:
                    lead_data = r.json()
                    new_lead_id = lead_data.get('id', '') or ''
                except Exception:
                    new_lead_id = ''
                if new_lead_id:
                    p(f"    Lead criado: {new_lead_id} (try {attempt})")
                    break
            else:
                p(f"    Criar lead try {attempt} falhou: status={r.status_code} body={r.text[:160]}")
        except Exception as e:
            p(f"    Criar lead try {attempt} excecao: {e}")
        if attempt < 3:
            try:
                time.sleep(1.5 * attempt)
            except Exception:
                pass
    if not new_lead_id:
        p(f"    [LEAD-FAIL] Nao foi possivel criar lead para ...{clean_phone[-4:]} apos 3 tentativas")
        return None, None

    biz_id = ''
    for attempt in (1, 2, 3):
        try:
            r_biz = requests.post(
                f'{DCZ_CRM}/businesses', headers=H,
                json={'leadId': new_lead_id, 'stageId': STAGE_BASE_ALUNOS_ID},
                timeout=12,
            )
            if r_biz.status_code in (200, 201):
                try:
                    biz_data = r_biz.json()
                    biz_id = biz_data.get('id', '') or ''
                except Exception:
                    biz_id = ''
                if biz_id:
                    p(f"    Business criado: {biz_id} (try {attempt})")
                    break
            else:
                p(f"    Criar business try {attempt} falhou: status={r_biz.status_code} body={r_biz.text[:160]}")
        except Exception as e:
            p(f"    Criar business try {attempt} excecao: {e}")
        if attempt < 3:
            try:
                time.sleep(1.5 * attempt)
            except Exception:
                pass
    if not biz_id:
        p(f"    [BIZ-FAIL] Lead {new_lead_id[:12]} criado mas business NAO — atendimento pode aparecer 'sem stage'")
    return new_lead_id, biz_id


# ===================== FASE 2: MEMÓRIA =====================

def _heartbeat(status='online', extra=''):
    """Grava heartbeat do agente no DB para o dashboard saber se está ligado."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_heartbeat (
                id INT PRIMARY KEY DEFAULT 1,
                status VARCHAR(20) DEFAULT 'offline',
                pid INT,
                last_beat TIMESTAMP DEFAULT NOW(),
                extra TEXT DEFAULT ''
            )
        """)
        cur.execute("""
            INSERT INTO agent_heartbeat (id, status, pid, last_beat, extra)
            VALUES (1, %s, %s, NOW(), %s)
            ON CONFLICT (id) DO UPDATE SET status=%s, pid=%s, last_beat=NOW(), extra=%s
        """, (status, os.getpid(), extra, status, os.getpid(), extra))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as hb_err:
        try:
            p(f"  [HEARTBEAT] Erro: {hb_err}")
        except Exception:
            pass


# ===================== FLAG DE LIGAR/DESLIGAR (controle do cockpit) =====================
# Flag em agent_config (key='agent_runtime_enabled') controla se o agente
# DEVE processar conversas ou ficar em pausa. Default: enabled.
# Cache em memoria com TTL curto para evitar consulta a cada msg.
_AGENT_RUNTIME_FLAG_CACHE = {'value': True, 'last_check_ts': 0}
_AGENT_RUNTIME_FLAG_TTL_S = 5  # checa banco a cada 5s no maximo


def _agent_runtime_enabled():
    """Le a flag agent_runtime_enabled do banco. Default True (ligado).
    Cache de 5s para nao sobrecarregar o DB."""
    now_ts = time.time()
    if (now_ts - _AGENT_RUNTIME_FLAG_CACHE['last_check_ts']) < _AGENT_RUNTIME_FLAG_TTL_S:
        return _AGENT_RUNTIME_FLAG_CACHE['value']
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT value FROM agent_config WHERE key = 'agent_runtime_enabled'")
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row[0] is not None:
            v = str(row[0]).strip().lower()
            enabled = v not in ('false', '0', 'off', 'disabled', 'no')
        else:
            enabled = True  # default: ligado
        _AGENT_RUNTIME_FLAG_CACHE['value'] = enabled
        _AGENT_RUNTIME_FLAG_CACHE['last_check_ts'] = now_ts
        return enabled
    except Exception as e:
        try:
            p(f"  [RUNTIME-FLAG] erro leitura: {e}")
        except Exception:
            pass
        # falha de DB: assume ligado para nao deixar alunos sem resposta
        return True


def set_agent_runtime_enabled(enabled: bool, source: str = ''):
    """Atualiza a flag agent_runtime_enabled no banco."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_config (key, value, updated_at)
            VALUES ('agent_runtime_enabled', %s, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = NOW()
        """, ('true' if enabled else 'false',))
        conn.commit()
        cur.close()
        conn.close()
        _AGENT_RUNTIME_FLAG_CACHE['value'] = bool(enabled)
        _AGENT_RUNTIME_FLAG_CACHE['last_check_ts'] = time.time()
        try:
            p(f"  [RUNTIME-FLAG] set={enabled} source={source}")
        except Exception:
            pass
        return True
    except Exception as e:
        try:
            p(f"  [RUNTIME-FLAG] erro set: {e}")
        except Exception:
            pass
        return False


def ensure_memory_tables():
    """Cria tabelas se necessário (chamada uma vez no startup)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS student_memory (
            id SERIAL PRIMARY KEY,
            phone VARCHAR(20) UNIQUE NOT NULL,
            lead_id VARCHAR(100),
            student_name TEXT,
            cpf VARCHAR(14),
            last_topic TEXT,
            last_summary TEXT,
            interaction_count INT DEFAULT 0,
            sentiment_history TEXT DEFAULT '',
            preferences JSONB DEFAULT '{}',
            notes TEXT DEFAULT '',
            first_contact_at TIMESTAMP DEFAULT NOW(),
            last_contact_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS interaction_summary (
            id SERIAL PRIMARY KEY,
            phone VARCHAR(20),
            lead_id VARCHAR(100),
            student_name TEXT,
            tema VARCHAR(50),
            subtema VARCHAR(100),
            sentimento VARCHAR(20),
            resolvido VARCHAR(20),
            nps_implicito INT,
            resumo TEXT,
            mensagens_count INT DEFAULT 0,
            pergunta_aluno TEXT,
            resposta_agente TEXT,
            avaliacao VARCHAR(20) DEFAULT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    for col, coldef in [
        ('pergunta_aluno', 'TEXT'),
        ('resposta_agente', 'TEXT'),
        ('avaliacao', "VARCHAR(20) DEFAULT NULL"),
        ('conv_id', 'VARCHAR(50)'),
    ]:
        try:
            cur.execute(f"ALTER TABLE interaction_summary ADD COLUMN IF NOT EXISTS {col} {coldef}")
        except Exception:
            pass
    conn.commit()
    cur.close()
    conn.close()
    p("  Tabelas student_memory + interaction_summary OK")


def load_memory(phone):
    """Carrega memória do aluno pelo telefone."""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')[-11:]
        cur.execute("SELECT * FROM student_memory WHERE phone LIKE %s", (f'%{clean_phone}%',))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            p(f"    Memoria carregada: {row['interaction_count']} interacoes | Ultimo: {row['last_topic']}")
        return row
    except Exception as e:
        p(f"    Erro load_memory: {e}")
        return None


def save_memory(phone, profile, topic, summary, sentiment):
    """Salva/atualiza memória do aluno."""
    try:
        conn = get_db()
        cur = conn.cursor()
        clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')[-11:]

        cur.execute("""
            INSERT INTO student_memory (phone, lead_id, student_name, cpf, last_topic, last_summary,
                                       interaction_count, sentiment_history, last_contact_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, 1, %s, NOW(), NOW())
            ON CONFLICT (phone) DO UPDATE SET
                lead_id = COALESCE(EXCLUDED.lead_id, student_memory.lead_id),
                student_name = COALESCE(EXCLUDED.student_name, student_memory.student_name),
                cpf = COALESCE(EXCLUDED.cpf, student_memory.cpf),
                last_topic = EXCLUDED.last_topic,
                last_summary = EXCLUDED.last_summary,
                interaction_count = student_memory.interaction_count + 1,
                sentiment_history = EXCLUDED.sentiment_history,
                last_contact_at = NOW(),
                updated_at = NOW()
        """, (
            clean_phone,
            profile.get('lead_id') if profile else None,
            profile.get('name') if profile else None,
            profile.get('cpf') if profile else None,
            topic, summary, sentiment
        ))
        conn.commit()
        cur.close()
        conn.close()
        p(f"    Memoria salva: topic={topic}, sentiment={sentiment}")
    except Exception as e:
        p(f"    Erro save_memory: {e}")


def generate_conversation_summary(messages):
    """Usa GPT para gerar resumo da conversa (custo minimo)."""
    if not messages or len(messages) < 2:
        return "Interação curta, sem resumo detalhado."

    conv_text = '\n'.join([f"{'Aluno' if m['role']=='user' else 'IA'}: {m['text'][:150]}" for m in messages[-8:]])

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{
                'role': 'user',
                'content': f"Resuma esta conversa de suporte em 1-2 frases curtas (max 100 palavras). Foque no problema e se foi resolvido:\n\n{conv_text}"
            }],
            max_tokens=80, temperature=0.1
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        p(f"    Erro resumo: {e}")
        return "Conversa de suporte ao aluno."


# ===================== FASE 4: TABULAÇÃO =====================

def tabulate_interaction(messages, profile, phone, conv_id=''):
    """Classifica a interação automaticamente com GPT e salva Q&A."""
    if not messages or len(messages) < 2:
        return

    _skip_user = {
        'oi', 'olá', 'ola', 'oii', 'oiii', 'oi!', 'olá!', 'bom dia', 'boa tarde',
        'boa noite', 'hello', 'hi', 'hey', 'fala', 'salve', 'opa', 'eae',
        'tudo bem', 'tudo bom', 'como vai', 'oie', 'oiee', 'bom dia!', 'boa tarde!',
        'boa noite!', 'oi boa tarde', 'oi bom dia', 'oi boa noite',
    }
    _skip_bot_patterns = [
        'selecione uma opção', 'veja as opções', 'como posso te ajudar',
        'bem-vindo', 'ainda está por aí', 'ficou alguma dúvida',
        'não tivemos retorno', 'obrigado pelo contato',
        'que bom que pude ajudar', 'qualquer dúvida é só nos chamar',
        'consegui te ajudar com isso', 'espero que tenha ajudado',
        'ficou tudo certo por aí', 'me conta melhor o que precisa',
        'me ajuda a te ajudar', 'pode me explicar um pouco mais',
        'não consegui pegar direito', 'não consegui entender',
        'tá tudo certo por aí', 'pode mandar',
        'fico feliz que deu certo', 'oba, que ótimo',
        'foi ótimo poder te ajudar', 'fico feliz em ter ajudado',
        'tudo certo então', 'que bom que deu tudo certo',
        'vou encerrar por aqui', 'até a próxima',
    ]

    def _is_skip_user(text):
        return text.strip().lower().rstrip('!?.,').strip() in _skip_user

    def _is_skip_bot(text):
        t = text.strip().lower()
        return any(p in t for p in _skip_bot_patterns)

    pergunta_aluno = ''
    resposta_agente = ''
    for m in reversed(messages):
        txt = m.get('text', '') or ''
        if not resposta_agente and m.get('role') == 'bot':
            if not _is_skip_bot(txt):
                resposta_agente = txt
        elif resposta_agente and m.get('role') == 'user':
            if not _is_skip_user(txt):
                pergunta_aluno = txt
                break

    relevant = [m for m in messages if not (
        (m.get('role') == 'user' and _is_skip_user(m.get('text', ''))) or
        (m.get('role') == 'bot' and _is_skip_bot(m.get('text', '')))
    )]
    conv_text = '\n'.join([f"{'Aluno' if m['role']=='user' else 'IA'}: {m['text'][:150]}" for m in relevant[-10:]])

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{
                'role': 'user',
                'content': f"""Classifique este atendimento. Responda EXATAMENTE neste formato JSON:
{{"tema":"ACESSO_PORTAL|FINANCEIRO|ACADEMICO|MATRICULA|DOCUMENTOS|CALENDARIO|OUTRO","subtema":"descricao curta","sentimento":"satisfeito|neutro|frustrado|irritado","resolvido":"sim|nao|parcial|escalado","nps":7}}

Regras para o campo "tema":
- Use "CALENDARIO" quando o atendimento envolver perguntas sobre DATAS academicas: prova A1 ou AF, liberacao de notas, inicio das aulas, fim do semestre, prazo de matricula ou rematricula, transferencia, retorno ao curso, dispensa de disciplina, atividades complementares (AC), estagio (TCE), ENADE ou feriados academicos.
- Use "MATRICULA" para duvidas administrativas de matricula sem foco em data.
- Use "ACADEMICO" para duvidas pedagogicas que NAO envolvam datas (conteudo, disciplina, professor, AVA).
- Use "ACESSO_PORTAL" para login, senha, AVA, app.
- Use "FINANCEIRO" para boleto, mensalidade, desconto, bolsa.
- Use "DOCUMENTOS" para historico, declaracao, diploma.
- Use "OUTRO" apenas se nenhum dos anteriores se aplicar.

Regras para o campo "resolvido":
- Use "sim" APENAS se o aluno indicou claramente que o problema foi resolvido ou agradeceu/fechou satisfeito.
- Use "parcial" se o aluno disse que vai *tentar depois*, *chegar em casa*, *voltar ao trabalho*, *não pode ficar no celular*, ou ainda não testou a solução — ainda há retorno esperado.
- Use "nao" se ficou em aberto ou sem solução útil.

Conversa:
{conv_text}"""
            }],
            max_tokens=100, temperature=0.1
        )

        raw = resp.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            return

        tab = json.loads(match.group())

        # Override determinitico: se o calendario foi injetado para essa
        # conversa nos ultimos 30 min, FORCA tema='CALENDARIO'. Isso evita
        # depender do GPT classificador acertar sozinho.
        if _consume_calendar_mark(conv_id):
            tab['tema'] = 'CALENDARIO'

        _cleanup_calendar_marks()

        from datetime import datetime, timezone, timedelta
        now_sp = datetime.now(timezone(timedelta(hours=-3))).replace(tzinfo=None)

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO interaction_summary
            (phone, lead_id, student_name, tema, subtema, sentimento, resolvido,
             nps_implicito, resumo, mensagens_count, pergunta_aluno, resposta_agente, conv_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            phone[-11:],
            profile.get('lead_id') if profile else None,
            profile.get('name') if profile else None,
            tab.get('tema', 'OUTRO'),
            tab.get('subtema', ''),
            tab.get('sentimento', 'neutro'),
            tab.get('resolvido', 'parcial'),
            tab.get('nps', 5),
            generate_conversation_summary(messages),
            len(messages),
            pergunta_aluno[:2000],
            resposta_agente[:2000],
            conv_id or '',
            now_sp,
        ))
        conn.commit()
        cur.close()
        conn.close()

        p(f"    TABULADO: {tab.get('tema')} / {tab.get('subtema')} / {tab.get('sentimento')} / resolvido={tab.get('resolvido')} / NPS={tab.get('nps')}")

        if profile and profile.get('lead_id'):
            update_crm_tags(profile['lead_id'], tab)

        is_detractor = tab.get('sentimento') in ('frustrado', 'irritado') or (tab.get('nps') and int(tab.get('nps', 10)) <= 6)
        if is_detractor and profile and profile.get('lead_id'):
            flag_detractor(profile['lead_id'], profile.get('name', ''), tab, phone)

    except Exception as e:
        p(f"    Erro tabulacao: {e}")


_AUTO_AVAL_THANKS_NEG = (
    'não obrigado', 'nao obrigado', 'não obrigada', 'nao obrigada',
    'obrigado mas', 'obrigada mas', 'valeu mas', 'vlw mas',
    'mas ainda', 'mas não', 'mas nao', 'não resolveu', 'nao resolveu',
    'não funcionou', 'nao funcionou', 'não deu', 'nao deu',
)

# Resposta tabulada parece handoff — não marcar "correta" só por "obrigado" depois
_AUTO_AVAL_BLOCK_RESP_PHRASES = (
    'vou te transferir', 'vou te encaminhar', 'te conectar com',
    'conectar com um dos nossos', 'distribuição automática pelo agente',
    'transferir para', 'encaminhar para', 'consultor que vai',
    'um dos nossos atendentes', 'atendente que vai',
    'vai dar continuidade ao seu atendimento',
    'vai poder te ajudar', 'vai poder te atender',
    'um momento, por favor',  # mensagem típica pós-distribuição
    'não consigo ouvir áudios', 'nao consigo ouvir audios',
    'especializado que poderá',  # retenção / Wesley
)


def _resposta_agente_sounds_like_handoff(text):
    if not text or not str(text).strip():
        return False
    low = str(text).lower()
    return any(p in low for p in _AUTO_AVAL_BLOCK_RESP_PHRASES)


def maybe_auto_avaliacao_correta_por_agradecimento(phone, conv_id, msg_text):
    """Última tabulação pendente vira avaliacao='correta' se o aluno só agradece (só agente, sem handoff)."""
    if not phone or not msg_text or not str(msg_text).strip():
        return
    st = _conv_states.get(conv_id, {}) if conv_id else {}
    if st.get('_human_took_over'):
        p(f"    [AUTO-AVAL] skip: handoff_state (_human_took_over)")
        return
    if st.get('_last_distributed_to'):
        p(f"    [AUTO-AVAL] skip: handoff_state (_last_distributed_to={st.get('_last_distributed_to')})")
        return
    t = str(msg_text).strip().lower()
    if len(t) > 160:
        return
    for neg in _AUTO_AVAL_THANKS_NEG:
        if neg in t:
            return
    _thanks = ('obrigad', 'valeu', 'vlw', 'brigad', 'grato', 'grata', 'thanks', 'thank you', 'agradeço', 'agradece')
    if not any(x in t for x in _thanks):
        return
    if t.count('?') >= 1 and len(t) > 55:
        return
    ph = str(phone).replace('+', '').replace(' ', '').replace('-', '')[-11:]
    if len(ph) < 8:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        if conv_id:
            cur.execute(
                """
                SELECT id, resposta_agente FROM interaction_summary
                WHERE phone LIKE %s
                  AND conv_id = %s
                  AND (avaliacao IS NULL OR trim(avaliacao) = '')
                  AND resposta_agente IS NOT NULL AND trim(resposta_agente) <> ''
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (f'%{ph}', str(conv_id)),
            )
        else:
            cur.execute(
                """
                SELECT id, resposta_agente FROM interaction_summary
                WHERE phone LIKE %s
                  AND (avaliacao IS NULL OR trim(avaliacao) = '')
                  AND resposta_agente IS NOT NULL AND trim(resposta_agente) <> ''
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (f'%{ph}',),
            )
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return
        rid, resp = row[0], row[1]
        if _resposta_agente_sounds_like_handoff(resp):
            p(f"    [AUTO-AVAL] skip: handoff_text ({(resp or '')[:72]}...)")
            cur.close()
            conn.close()
            return
        cur.execute(
            "UPDATE interaction_summary SET avaliacao = 'correta' WHERE id = %s",
            (rid,),
        )
        n = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if n:
            p(f"    [AUTO-AVAL] Agradecimento -> interaction_summary.avaliacao=correta (conv={str(conv_id)[:12] if conv_id else '?'})")
    except Exception as e:
        p(f"    [AUTO-AVAL] Erro: {e}")


def update_crm_tags(lead_id, tabulation):
    """Adiciona notas ao lead na DataCrazy com resultado da tabulação."""
    try:
        note = f"[IA {datetime.now().strftime('%d/%m %H:%M')}] {tabulation.get('tema','')}/{tabulation.get('subtema','')} - {tabulation.get('sentimento','')} - Resolvido: {tabulation.get('resolvido','')}"
        r = requests.patch(f'{DCZ_CRM}/leads/{lead_id}', headers=H,
                          json={'notes': note}, timeout=10)
        p(f"    CRM update: {r.status_code}")
    except Exception as e:
        p(f"    Erro CRM update: {e}")


def flag_detractor(lead_id, student_name, tabulation, phone):
    """Marca lead como detrator: nota interna no CRM + tag."""
    try:
        nps = tabulation.get('nps', '?')
        sentimento = tabulation.get('sentimento', '?')
        tema = tabulation.get('tema', '?')
        subtema = tabulation.get('subtema', '')

        note = (
            f"⚠️ [DETRATOR - {datetime.now().strftime('%d/%m %H:%M')}]\n"
            f"Aluno: {student_name} ({phone})\n"
            f"Sentimento: {sentimento} | NPS: {nps}\n"
            f"Tema: {tema}/{subtema}\n"
            f"Requer atenção imediata do time."
        )
        requests.patch(
            f'{DCZ_CRM}/leads/{lead_id}',
            headers=H, json={'notes': note}, timeout=10
        )
        p(f"    ⚠️  DETRATOR SINALIZADO no CRM: {student_name} (NPS={nps}, {sentimento})")

        try:
            requests.patch(
                f'{DCZ_CRM}/leads/{lead_id}',
                headers=H,
                json={'tags': [{'name': 'detrator'}]},
                timeout=10
            )
            p(f"    Tag 'detrator' adicionada")
        except Exception:
            pass

    except Exception as e:
        p(f"    Erro flag_detractor: {e}")


# ===================== CONTEXT BUILDERS =====================

def build_student_context(profile):
    if not profile:
        return "## ALUNO: Não identificado"
    parts = [f"## DADOS DO ALUNO:"]
    parts.append(f"- Nome: {profile['name']}")
    if profile.get('cpf'):
        parts.append(f"- CPF: ***.***.{profile['cpf'][-5:-2]}-** (parcial por segurança)")
    if profile.get('tags'):
        parts.append(f"- Tags: {', '.join(profile['tags'])}")
    if profile.get('email'):
        parts.append(f"- Email: {profile['email']}")
    fname = profile.get('first_name') or 'aluno'
    parts.append(f"\nChame o aluno de *{fname}*.")

    acad = profile.get('academic')
    if acad:
        parts.append(f"\n## DADOS ACADÊMICOS (contexto interno - NÃO revelar proativamente):")
        all_courses = acad.get('_all_courses')
        if all_courses and len(all_courses) > 1:
            for i, c in enumerate(all_courses, 1):
                nivel = (c.get('nivel') or '').upper()
                sem_label = 'Semestre' if 'GRAD' in nivel else 'Período/Módulo'
                grau = c.get('grau', '')
                grau_info = f" ({grau})" if grau else ''
                parts.append(f"- Curso {i}: {c.get('curso','?')}{grau_info} | {sem_label}: {c.get('serie','?')} | Polo: {c.get('polo','?')} | Situação: {c.get('situacao','?')}")
            parts.append(
                "\nATENÇÃO MÚLTIPLOS CURSOS: O aluno possui mais de um curso/matrícula."
                "\nQuando a pergunta depender do curso específico, diga algo natural como:"
                "\n'Verifiquei aqui que você possui mais de um curso conosco! Sobre qual deles você gostaria de falar?'"
                "\nListe os nomes dos cursos para o aluno escolher. NÃO revele semestre, polo ou grau nessa pergunta."
            )
        else:
            if acad.get('curso'):
                grau = acad.get('grau', '')
                grau_info = f" ({grau})" if grau else ''
                parts.append(f"- Curso: {acad['curso']}{grau_info}")
            if acad.get('serie'):
                nivel = (acad.get('nivel') or '').upper()
                sem_label = 'Semestre' if 'GRAD' in nivel else 'Período/Módulo'
                parts.append(f"- {sem_label}: {acad['serie']}")
            if acad.get('polo'):
                parts.append(f"- Polo: {acad['polo']}")
            if acad.get('situacao'):
                parts.append(f"- Situação: {acad['situacao']}")
            if acad.get('tipo_matricula'):
                parts.append(f"- Tipo matrícula: {acad['tipo_matricula']}")
        if acad.get('email_academico'):
            parts.append(f"- Email acadêmico: {acad['email_academico']}")
        parts.append(
            "\nREGRA CRÍTICA DE PRIVACIDADE:"
            "\n- NUNCA mencione dados acadêmicos na saudação ou proativamente."
            "\n- Use SOMENTE quando a pergunta EXIGIR (estágio, TCC, grade, provas do curso, semestre)."
            "\n- NUNCA diga 'Sei que você está no Xo semestre de Y'. Use a informação internamente para dar a resposta correta sem expor que consultou."
            "\n- Se o aluno perguntar sobre estágio/TCC, use o semestre e o GRAU DO CURSO para contextualizar:"
            "\n  * CST (Tecnólogo): cursos de 2-3 anos, NEM SEMPRE possuem estágio obrigatório na grade. Oriente o aluno a verificar a grade curricular do curso."
            "\n  * Bacharelado: cursos de 4-5 anos, geralmente possuem estágio obrigatório."
            "\n  * Licenciatura: cursos de 4 anos, possuem estágio obrigatório."
            "\n  NUNCA afirme com certeza se o curso tem ou não estágio — oriente a verificar a grade."
        )

    caa_list = profile.get('caa_solicitacoes') or []
    if caa_list:
        parts.append("\n## SOLICITACOES CAA (contexto interno - NAO revelar lista; usar APENAS se a duvida for relacionada):")
        for r in caa_list[:8]:
            sub = (r.get('subprocesso') or '').strip()
            sit_at = (r.get('situacao_atendimento') or '').strip()
            sit_def = (r.get('situacao_deferimento') or '').strip()
            prot = (r.get('protocolo') or '').strip()
            dc = r.get('data_chegada')
            dconc = r.get('data_conclusao')
            aging = r.get('aging_dias')
            dc_s = dc.strftime('%d/%m/%Y') if hasattr(dc, 'strftime') else (str(dc)[:10] if dc else '?')
            dconc_s = dconc.strftime('%d/%m/%Y') if hasattr(dconc, 'strftime') else (str(dconc)[:10] if dconc else '')
            line = f"- {dc_s} | {sub} | Protocolo {prot} | {sit_at}"
            if sit_def:
                line += f" / {sit_def}"
            is_open = 'em aberto' in sit_def.lower() or sit_at.upper() == 'PENDENTE'
            if is_open:
                if aging is not None:
                    line += f" | {aging} dias em aberto"
                else:
                    line += " | em aberto"
            elif dconc_s:
                line += f" | concluida em {dconc_s}"
            parts.append(line)
            obs = (r.get('observacao') or '').strip()
            if obs and 'em aberto' in sit_def.lower():
                parts.append(f"   obs: {obs[:200]}")
        parts.append(
            "\nREGRAS PARA SOLICITACOES CAA:"
            "\n- Use APENAS se a duvida do aluno for relacionada (historico, colacao, declaracao, trancamento, acesso plataforma, etc)."
            "\n- Quando o assunto bater com um CAA EM ABERTO, mencione de forma natural:"
            " 'Vi aqui que voce tem uma solicitacao de {subprocesso} em aberto desde {data_chegada}, protocolo {protocolo}. E sobre ela?'"
            "\n- Quando o aluno perguntar 'esta liberado?' ou 'qual o status?', use situacao_deferimento + data_conclusao."
            "\n- Se ja foi INDEFERIDO, NAO diga 'esta em aberto'; explique que foi indeferido e oriente proximos passos."
            "\n- Se ja foi DEFERIDO e ele esta perguntando de novo, lembre que ja foi resolvido em {data_conclusao}."
            "\n- NUNCA despeje a lista. NUNCA mencione na saudacao proativamente."
            "\n- Cite no maximo 1 solicitacao por resposta, a mais relevante."
        )

    all_ci = profile.get('all_courses_info')
    ci = profile.get('course_info')
    if all_ci and len(all_ci) > 1:
        parts.append(f"\n## INFORMAÇÕES DOS CURSOS (da grade oficial):")
        for i, ci_item in enumerate(all_ci, 1):
            parts.append(f"\n### Curso {i}: {ci_item.get('nome', '?')}")
            if ci_item.get('duracao'):
                parts.append(f"- Duração: {ci_item['duracao']} semestres")
            if ci_item.get('grau'):
                parts.append(f"- Grau: {ci_item['grau']}")
            if ci_item.get('grade_link'):
                parts.append(f"- Link da grade: {ci_item['grade_link']}")
            if ci_item.get('descricao'):
                parts.append(f"- Descrição: {ci_item['descricao'][:200]}")
        parts.append(
            "\nREGRAS PARA MÚLTIPLOS CURSOS:"
            "\n- Quando o aluno perguntar sobre grade/disciplinas/mercado/duração, PERGUNTE de qual curso antes de responder."
            "\n- Liste os nomes dos cursos de forma natural para o aluno escolher."
            "\n- Quando o aluno indicar qual curso, ENVIE O LINK da grade correspondente."
            "\n- NÃO envie informações de todos os cursos de uma vez."
        )
    elif ci:
        parts.append(f"\n## INFORMAÇÕES DO CURSO (da grade oficial):")
        if ci.get('nome'):
            parts.append(f"- Curso completo: {ci['nome']}")
        if ci.get('duracao'):
            parts.append(f"- Duração: {ci['duracao']} semestres")
        if ci.get('grau'):
            parts.append(f"- Grau: {ci['grau']}")
        if ci.get('area_curso'):
            parts.append(f"- Área: {ci['area_curso']}")
        if ci.get('grade_link'):
            parts.append(f"- Link da grade curricular: {ci['grade_link']}")
        if ci.get('descricao'):
            parts.append(f"- Descrição: {ci['descricao'][:300]}")
        if ci.get('mercado_trabalho'):
            parts.append(f"- Mercado de trabalho: {ci['mercado_trabalho'][:300]}")
        if ci.get('areas_atuacao'):
            parts.append(f"- Áreas de atuação: {ci['areas_atuacao'][:300]}")
        parts.append(
            "\nREGRAS PARA DADOS DO CURSO:"
            "\n- Quando o aluno perguntar sobre grade/disciplinas/matriz curricular, ENVIE O LINK da grade."
            "\n- Quando perguntar sobre mercado de trabalho ou áreas de atuação do curso, use as informações acima."
            "\n- Quando perguntar duração ou grau do curso, responda com os dados acima."
            "\n- NÃO despeje todas as informações de uma vez. Responda o que foi perguntado."
        )

    return '\n'.join(parts)


def build_memory_context(memory):
    if not memory:
        return "## MEMÓRIA: Primeiro contato deste aluno."
    parts = ["## MEMÓRIA DO ALUNO:"]
    parts.append(f"- Interações anteriores: {memory['interaction_count']}")
    if memory.get('last_topic'):
        parts.append(f"- Último assunto: {memory['last_topic']}")
    if memory.get('last_summary'):
        parts.append(f"- Resumo da última conversa: {memory['last_summary']}")
    if memory.get('last_contact_at'):
        parts.append(f"- Último contato: {memory['last_contact_at']}")
    if memory.get('sentiment_history'):
        parts.append(f"- Sentimento anterior: {memory['sentiment_history']}")

    if memory['interaction_count'] > 3:
        parts.append("\nEste aluno é RECORRENTE. Seja eficiente e direto. Reconheça que já se conhecem.")
    elif memory['interaction_count'] > 0 and memory.get('last_summary'):
        parts.append(f"\nNa última conversa: {memory['last_summary'][:200]}. Se relevante, pergunte se resolveu.")
    return '\n'.join(parts)


def build_sentiment_context(sentiment, memory):
    if sentiment == 'frustrado':
        return "## SENTIMENTO DETECTADO: FRUSTRADO\n- VALIDE o sentimento: 'Entendo sua frustração...'\n- Priorize resolução rápida ou escalação imediata\n- NÃO minimize o problema"
    elif sentiment == 'preocupado':
        return "## SENTIMENTO DETECTADO: PREOCUPADO\n- Demonstre compreensão: 'Vamos resolver isso...'\n- Seja atencioso e detalhado nas instruções"
    return ""


# ===================== RAG + LLM =====================

def rag_search(question):
    client = OpenAI(api_key=OPENAI_API_KEY)
    conn = get_db()
    cur = conn.cursor()

    t0 = time.time()
    emb = client.embeddings.create(
        input=question[:2000], model='text-embedding-3-small', dimensions=256
    ).data[0].embedding
    t_emb = time.time() - t0

    emb_str = ','.join(str(x) for x in emb)
    t0 = time.time()
    cur.execute(f"""
        SELECT * FROM (
            SELECT pergunta_aluno, resposta_atendente, tema, whatsapp_buttons, media_attachments,
                   cosine_similarity(embedding, ARRAY[{emb_str}]::float8[]) as score
            FROM knowledge_base WHERE embedding IS NOT NULL
        ) sub ORDER BY score DESC LIMIT {TOP_K_RESULTS}
    """)
    results = cur.fetchall()
    t_rag = time.time() - t0

    if results:
        p(f"    Embedding: {t_emb*1000:.0f}ms | RAG: {t_rag*1000:.0f}ms | Top: {results[0][5]:.3f}")

    cur.close()
    conn.close()
    return results, emb


def find_media_for_topic(topic_query):
    """Busca mídias anexas na knowledge_base via embedding similarity."""
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        conn = get_db()
        cur = conn.cursor()
        emb = client.embeddings.create(
            input=topic_query[:500], model='text-embedding-3-small', dimensions=256
        ).data[0].embedding
        emb_str = ','.join(str(x) for x in emb)
        cur.execute(f"""
            SELECT media_attachments, cosine_similarity(embedding, ARRAY[{emb_str}]::float8[]) as score
            FROM knowledge_base
            WHERE embedding IS NOT NULL AND media_attachments IS NOT NULL AND media_attachments != ''
            ORDER BY score DESC LIMIT 1
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row[1] >= 0.65:
            items = json.loads(row[0])
            if isinstance(items, list) and items:
                p(f"    Media match para '{topic_query[:40]}' (sim={row[1]:.3f}): {len(items)} item(s)")
                return items
    except Exception as e:
        p(f"    Erro find_media_for_topic: {e}")
    return []


def send_topic_media(conv_id, topic_query):
    """Busca e envia mídias relacionadas a um tópico de submenu."""
    media_items = find_media_for_topic(topic_query)
    for mi in media_items:
        time.sleep(1)
        send_media_message(conv_id, mi)
        p(f"    Midia submenu enviada: {mi.get('filename', mi.get('url', ''))}")


def build_references(results):
    refs = ''
    for i, (pergunta, resposta, tema, wa_buttons, media_att, score) in enumerate(results):
        if score < 0.6:
            continue
        refs += f"\n--- Ref {i+1} (tema: {tema or 'N/A'}, sim: {score:.2f}) ---\n"
        refs += f"Pergunta: {pergunta[:500]}\nResposta: {resposta[:1500]}\n"
    return refs or "\nNenhuma referencia encontrada.\n"


def get_active_alerts(mode_filter='context'):
    """Busca alertas ativos do banco.
    mode_filter: 'context' retorna alertas com display_mode in ('context','both')
                 'greeting' retorna alertas com display_mode in ('greeting','both')
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        if mode_filter == 'context':
            modes = ('context', 'both')
        else:
            modes = ('greeting', 'both')
        cur.execute("""SELECT title, message, category FROM agent_alerts
                       WHERE active = TRUE
                       AND (starts_at IS NULL OR starts_at <= NOW())
                       AND (expires_at IS NULL OR expires_at > NOW())
                       AND display_mode IN %s
                       ORDER BY priority DESC, created_at DESC""", (modes,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception:
        return []


def build_alerts_for_llm():
    """Formata alertas ativos para injeção no system prompt."""
    rows = get_active_alerts('context')
    if not rows:
        return ""
    alerts_text = "## ⚠️ ALERTAS ATIVOS (use SOMENTE estes ao mencionar status de sistemas):\n"
    for title, message, category in rows:
        alerts_text += f"- **[{category}] {title}**: {message}\n"
    return alerts_text


def build_greeting_alerts():
    """Retorna texto de alertas para anexar à saudação, ou string vazia."""
    rows = get_active_alerts('greeting')
    if not rows:
        return ""
    lines = []
    for title, message, category in rows:
        lines.append(f"⚠️ *{title}*: {message}")
    return "\n\n" + "\n".join(lines)


def call_llm(question, references, history, profile, memory, sentiment, is_first, image_b64=None, image_mime=None, image_desc=None):
    client = OpenAI(api_key=OPENAI_API_KEY)

    student_ctx = build_student_context(profile)
    memory_ctx = build_memory_context(memory)
    sentiment_ctx = build_sentiment_context(sentiment, memory)
    alerts_ctx = build_alerts_for_llm()

    prompt = SYSTEM_PROMPT.format(
        student_context=student_ctx,
        memory_context=memory_ctx,
        sentiment_context=sentiment_ctx,
        active_alerts=alerts_ctx,
        references=references,
        history=history
    )

    if is_first:
        prompt += "\n(Primeira mensagem do aluno nesta conversa.)\n"
    else:
        prompt += "\n(Já em conversa - NÃO cumprimente, vá direto ao ponto.)\n"

    if image_b64:
        img_prompt = (
            "\n## IMAGEM RECEBIDA:\n"
            "O aluno enviou uma imagem (print de tela). Analise-a cuidadosamente e use o conteúdo visual "
            "para complementar sua resposta. Se for um print de tela, identifique o que aparece "
            "e oriente o aluno com base nas referências da base de conhecimento.\n"
        )
        if image_desc:
            img_prompt += f"\n**Análise prévia da imagem:** {image_desc}\n"
            img_prompt += (
                "Use esta análise para orientar sua resposta. Se a análise menciona email pessoal "
                "(gmail, hotmail, live.com) sendo usado em vez do acadêmico, oriente sobre usar o email acadêmico.\n"
            )
        prompt += img_prompt
        user_content = [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {
                "url": f"data:{image_mime or 'image/jpeg'};base64,{image_b64}",
                "detail": "low"
            }}
        ]
    else:
        user_content = question

    t0 = time.time()
    chat = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': user_content}
        ],
        max_tokens=800, temperature=0.3
    )
    resp_text = chat.choices[0].message.content
    t_llm = time.time() - t0
    p(f"    LLM{'(vision)' if image_b64 else ''}: {t_llm*1000:.0f}ms")

    cm = re.search(r'\[CONFIANCA:(\d+\.?\d*)\]', resp_text)
    confidence = float(cm.group(1)) if cm else 0.3
    clean = re.sub(r'\[CONFIANCA:\d+\.?\d*\]', '', resp_text).strip()

    return clean, confidence, t_llm


# ===================== SEND / LOG =====================

def make_button_id(name):
    """Generate a short id from button name for WhatsApp API."""
    return re.sub(r'[^a-z0-9_]', '', name.lower().replace(' ', '_').replace('/', '_'))[:24]


def _menu_body_matches_normalised(stripped: str, menu_key: str) -> bool:
    """Texto do aluno bate com item de menu (texto, substring ou id de botão WhatsApp)."""
    mk = (menu_key or '').strip().lower()
    if not mk or not stripped:
        return False
    if mk in stripped or stripped == mk:
        return True
    try:
        if stripped == make_button_id(menu_key):
            return True
    except Exception:
        pass
    return False


def _message_has_thread_payload(m):
    """Corpo, anexo ou clique de botão/lista — não tratar mensagem só com mídia como 'vazia'."""
    if not isinstance(m, dict) or m.get('isInternal', False):
        return False
    if (m.get('body', '') or '').strip():
        return True
    if (m.get('text', '') or '').strip():
        return True
    if (m.get('title', '') or '').strip():
        return True
    _atts = m.get('attachments') or []
    if isinstance(_atts, list) and len(_atts) > 0:
        return True
    _meta = m.get('meta', m.get('payload', m.get('sourceData', {})))
    if isinstance(_meta, dict):
        _inter = _meta.get('interactive', _meta)
        if isinstance(_inter, dict):
            for _rtype in ('button_reply', 'list_reply'):
                _rep = _inter.get(_rtype, {})
                if isinstance(_rep, dict) and (_rep.get('title') or '').strip():
                    return True
    return False


def send_message_crm(conv_id, text, buttons=None):
    try:
        payload = {'body': text, 'isInternal': False}
        if buttons:
            payload['buttons'] = [
                {'name': b, 'id': make_button_id(b), 'description': None, 'url': None}
                for b in buttons
            ]
        r = requests.post(f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                         headers=H, json=payload, timeout=15)
        return r.status_code, r.json() if r.status_code in (200, 201) else r.text[:300]
    except Exception as e:
        p(f"    Erro envio: {e}")
        return 500, str(e)


COCKPIT_BASE_URL = os.environ.get('COCKPIT_BASE_URL', 'http://localhost:8000')

META_TOKEN = os.environ.get('META_TOKEN', '')
META_PHONE_ID = os.environ.get('META_PHONE_ID', '883452561518366')
META_URL = f'https://graph.facebook.com/v25.0/{META_PHONE_ID}/messages'
META_H_GRAPH = {'Authorization': f'Bearer {META_TOKEN}', 'Content-Type': 'application/json'}


def _upload_media_to_meta(file_path, mime_type):
    """Faz upload de arquivo local para a Meta API e retorna o media_id."""
    try:
        upload_url = f'https://graph.facebook.com/v25.0/{META_PHONE_ID}/media'
        with open(file_path, 'rb') as f:
            r = requests.post(upload_url,
                headers={'Authorization': f'Bearer {META_TOKEN}'},
                files={'file': (os.path.basename(file_path), f, mime_type)},
                data={'messaging_product': 'whatsapp', 'type': mime_type},
                timeout=60)
        if r.status_code == 200:
            media_id = r.json().get('id')
            p(f"    Media upload Meta OK: id={media_id}")
            return media_id
        else:
            p(f"    Media upload Meta falhou: {r.status_code} {r.text[:200]}")
    except Exception as e:
        p(f"    Media upload Meta erro: {e}")
    return None


def send_media_message(conv_id, media_item, caption=''):
    """Envia mídia (imagem/vídeo/doc) via Meta API (upload se local), com fallback DataCrazy."""
    url = media_item.get('url', '')
    filename = media_item.get('filename', '')
    mime = media_item.get('mimeType', '')
    media_type = media_item.get('type', 'document').upper()
    is_local = url.startswith('/media/')
    local_path = None

    if is_local:
        local_path = os.path.join('c:/Distribuicao_Academico/media', os.path.basename(url))
        if not os.path.exists(local_path):
            p(f"    Arquivo local nao encontrado: {local_path}")
            return 404

    phone_full = f'55{_current_phone or PHONE_TO_MONITOR}'
    wa_type = 'image' if media_type in ('IMAGE', 'image') else 'video' if media_type in ('VIDEO', 'video') else 'document'

    # 1) Se arquivo local, fazer upload para Meta e enviar por media_id
    if is_local and local_path:
        media_id = _upload_media_to_meta(local_path, mime or f'{wa_type}/mp4')
        if media_id:
            try:
                body = {
                    'messaging_product': 'whatsapp',
                    'to': phone_full,
                    'type': wa_type,
                    wa_type: {'id': media_id}
                }
                if caption:
                    body[wa_type]['caption'] = caption
                if wa_type == 'document' and filename:
                    body[wa_type]['filename'] = filename
                r = requests.post(META_URL, headers=META_H_GRAPH, json=body, timeout=20)
                if r.status_code in (200, 201):
                    p(f"    Midia local enviada via Meta upload: {filename} (status={r.status_code})")
                    return r.status_code
                else:
                    p(f"    Meta send com media_id falhou: {r.status_code} {r.text[:200]}")
            except Exception as e:
                p(f"    Meta send falhou: {e}")

    # 2) URL pública: enviar diretamente via Meta API com link
    if not is_local:
        try:
            body = {
                'messaging_product': 'whatsapp',
                'to': phone_full,
                'type': wa_type,
                wa_type: {'link': url}
            }
            if caption:
                body[wa_type]['caption'] = caption
            if wa_type == 'document' and filename:
                body[wa_type]['filename'] = filename
            r = requests.post(META_URL, headers=META_H_GRAPH, json=body, timeout=20)
            if r.status_code in (200, 201):
                p(f"    Midia enviada via Meta API: {filename} (status={r.status_code})")
                return r.status_code
            else:
                p(f"    Meta link falhou: {r.status_code} {r.text[:200]}")
        except Exception as e:
            p(f"    Meta link falhou: {e}")

    # 3) Fallback: DataCrazy API
    public_url = url if not is_local else f'{COCKPIT_BASE_URL}{url}'
    try:
        payload = {
            'body': caption,
            'isInternal': False,
            'attachments': [{
                'url': public_url,
                'fileName': filename,
                'mimeType': mime,
                'type': media_type
            }]
        }
        r = requests.post(f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                         headers=H, json=payload, timeout=20)
        if r.status_code in (200, 201):
            p(f"    Midia enviada via DataCrazy: {filename} ({media_type})")
            return r.status_code
    except Exception as e:
        p(f"    DataCrazy media falhou: {e}")

    p(f"    FALHA ao enviar midia: {filename}")
    return 500


# ===================== VISION: DOWNLOAD IMAGE =====================

def download_whatsapp_image(image_info):
    """Baixa imagem e retorna base64 + mime_type.
    image_info pode ser:
      - dict com keys url, media_id, mime_type (vindo de extract_image_from_message)
      - str (media_id legado para Meta Graph API)
    Tenta: 1) URL S3 do DataCrazy  2) Meta Graph API com media_id/fileName
    """
    if isinstance(image_info, str):
        image_info = {'url': '', 'media_id': image_info, 'mime_type': 'image/jpeg'}

    s3_url = image_info.get('url', '')
    media_id = image_info.get('media_id', '')
    mime_type = image_info.get('mime_type', 'image/jpeg')

    # 1) Tentar download direto da URL S3 do DataCrazy
    if s3_url and s3_url.startswith('http'):
        try:
            p(f"    Vision: baixando via URL direta...")
            r = requests.get(s3_url, timeout=30)
            if r.status_code == 200 and len(r.content) > 100:
                img_b64 = base64.b64encode(r.content).decode('utf-8')
                size_kb = len(r.content) / 1024
                detected_mime = r.headers.get('Content-Type', mime_type)
                if 'image' in detected_mime:
                    mime_type = detected_mime
                p(f"    Vision: imagem baixada via S3 ({size_kb:.0f}KB, {mime_type})")
                return img_b64, mime_type
            else:
                p(f"    Vision: S3 falhou ({r.status_code}, {len(r.content)}B), tentando Meta...")
        except Exception as e:
            p(f"    Vision: S3 erro: {e}, tentando Meta...")

    # 2) Fallback: Meta Graph API com media_id
    if media_id:
        try:
            p(f"    Vision: baixando via Meta Graph (media_id={media_id[:20]})...")
            r = requests.get(
                f'https://graph.facebook.com/v25.0/{media_id}',
                headers={'Authorization': f'Bearer {META_TOKEN}'},
                timeout=15
            )
            if r.status_code != 200:
                p(f"    Vision: Meta Graph falha ({r.status_code})")
                return None, None
            media_info = r.json()
            media_url = media_info.get('url')
            mime_type = media_info.get('mime_type', mime_type)
            if not media_url:
                p(f"    Vision: URL vazia da Meta")
                return None, None
            r2 = requests.get(
                media_url,
                headers={'Authorization': f'Bearer {META_TOKEN}'},
                timeout=30
            )
            if r2.status_code != 200:
                p(f"    Vision: Meta download falha ({r2.status_code})")
                return None, None
            img_b64 = base64.b64encode(r2.content).decode('utf-8')
            size_kb = len(r2.content) / 1024
            p(f"    Vision: imagem baixada via Meta ({size_kb:.0f}KB, {mime_type})")
            return img_b64, mime_type
        except Exception as e:
            p(f"    Vision: Meta erro: {e}")

    p(f"    Vision: nenhuma fonte disponível para baixar imagem")
    return None, None


def extract_image_from_message(msg):
    """Extrai dados de imagem de uma mensagem do CRM.
    Retorna dict com keys: url, media_id, caption (ou None se sem imagem).
    Prioriza URL S3 do DataCrazy, depois fileName (WhatsApp media_id), depois Meta Graph.
    """
    attachments = msg.get('attachments', [])
    if isinstance(attachments, list):
        for att in attachments:
            if isinstance(att, dict):
                att_type = (att.get('type', '') or att.get('mimeType', '')).lower()
                if 'image' in att_type:
                    result = {
                        'url': att.get('url', ''),
                        'media_id': att.get('fileName', '') or att.get('mediaId', '') or att.get('media_id', ''),
                        'caption': att.get('caption', ''),
                        'mime_type': att.get('mimeType', 'image/jpeg'),
                    }
                    if result['url'] or result['media_id']:
                        return result

    source = msg.get('sourceData', msg.get('meta', msg.get('payload', {})))
    if isinstance(source, dict):
        img_data = source.get('image', {})
        if isinstance(img_data, dict) and (img_data.get('id') or img_data.get('url')):
            return {
                'url': img_data.get('url', ''),
                'media_id': img_data.get('id', ''),
                'caption': img_data.get('caption', ''),
                'mime_type': img_data.get('mime_type', 'image/jpeg'),
            }
        msg_type = source.get('type', '')
        if msg_type == 'image' and (source.get('id') or source.get('url')):
            return {
                'url': source.get('url', ''),
                'media_id': source.get('id', ''),
                'caption': source.get('caption', ''),
                'mime_type': source.get('mime_type', 'image/jpeg'),
            }

    return None


def fetch_wamid(phone):
    """Busca o último wamid da tabela wamid_cache no PostgreSQL."""
    try:
        conn = get_db()
        cur = conn.cursor()
        clean = phone.replace('+', '').replace(' ', '').replace('-', '')
        cur.execute(
            "SELECT wamid, updated_at FROM wamid_cache WHERE phone LIKE %s ORDER BY updated_at DESC LIMIT 1",
            (f'%{clean[-11:]}%',)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            from datetime import datetime, timezone
            wamid, updated = row
            age = (datetime.now(timezone.utc) - updated.replace(tzinfo=timezone.utc)).total_seconds()
            if age < 300:
                return wamid
    except Exception as e:
        p(f"    fetch_wamid erro: {e}")
    return None


def meta_typing_on():
    """Envia typing indicator via Meta Graph API usando wamid do PostgreSQL."""
    wamid = fetch_wamid(_current_phone or PHONE_TO_MONITOR)
    if not wamid:
        return False
    try:
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": wamid,
            "typing_indicator": {"type": "text"}
        }
        r = requests.post(META_URL, headers=META_H_GRAPH, json=payload, timeout=5)
        if r.status_code == 200:
            p(f"    ⌨️  Typing ON (Meta) wamid={wamid[:30]}...")
            return True
        else:
            p(f"    ⌨️  Typing FAIL: {r.status_code}")
    except Exception as e:
        p(f"    ⌨️  Typing erro: {e}")
    return False


def _ensure_dedup_table():
    """Cria tabela de dedup se não existir."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS msg_dedup (
                msg_id TEXT PRIMARY KEY,
                body_hash TEXT,
                processed_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dedup_body ON msg_dedup (body_hash, processed_at)")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        p(f"  dedup table error: {e}")

_ensure_dedup_table()


def _db_claim_message(msg_id, body):
    """Tenta reivindicar mensagem no DB. Retorna True se conseguiu (primeira vez)."""
    body_hash = hashlib.md5(body.strip().lower().encode()).hexdigest()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO msg_dedup (msg_id, body_hash) VALUES (%s, %s) ON CONFLICT (msg_id) DO NOTHING RETURNING msg_id",
            (msg_id, body_hash)
        )
        claimed = cur.fetchone() is not None
        if not claimed:
            p(f"  DEDUP-DB: msg_id {msg_id[:20]} já processado por outro processo")
        conn.commit()
        cur.close()
        conn.close()
        return claimed
    except Exception as e:
        p(f"  dedup claim error: {e}")
        return True


def _db_is_duplicate_body(body, window_seconds=45):
    """Verifica se mesmo body foi processado nos últimos N segundos."""
    body_hash = hashlib.md5(body.strip().lower().encode()).hexdigest()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM msg_dedup WHERE body_hash = %s AND processed_at > NOW() - INTERVAL '%s seconds' LIMIT 1",
            (body_hash, window_seconds)
        )
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        if exists:
            p(f"  DEDUP-DB: body duplicado nos últimos {window_seconds}s")
        return exists
    except Exception as e:
        p(f"  dedup body check error: {e}")
        return False


def _db_cleanup_dedup():
    """Remove entradas antigas da tabela de dedup (>1h)."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM msg_dedup WHERE processed_at < NOW() - INTERVAL '1 hour'")
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def _track_sent_body(text):
    """Registra body enviado no DB para dedup."""
    body_hash = hashlib.md5(text.strip().lower().encode()).hexdigest()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO msg_dedup (msg_id, body_hash) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (f'sent_{body_hash}_{int(time.time())}', body_hash)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def _is_echo_of_sent(text):
    """Verifica se texto é eco de algo enviado pelo bot/agente recentemente.
    Só consulta hashes originados de send_and_track (msg_id LIKE 'sent_%')."""
    body_hash = hashlib.md5(text.strip().lower().encode()).hexdigest()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM msg_dedup WHERE body_hash = %s AND msg_id LIKE 'sent_%%' "
            "AND processed_at > NOW() - INTERVAL '120 seconds' LIMIT 1",
            (body_hash,)
        )
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        return exists
    except Exception:
        return False


SEND_BODY_DEDUP_WINDOW_S = 10 * 60   # 10min - janela onde a mesma resposta NUNCA repete


def send_and_track(conv_id, text, buttons=None, force=False):
    """Reforça typing antes de enviar + pequeno delay humanizado.

    ANTI-REPETICAO PERSISTENTE:
    - Calcula hash do texto normalizado (sem nome proprio, sem acento, sem
      pontuacao) e consulta agent_sent_signatures. Se ja enviou hash igual
      nessa conv nos ultimos SEND_BODY_DEDUP_WINDOW_S, SUPRIME (mesmo apos
      restart, porque consulta DB).
    - Lock per-conv serializa envios concorrentes (anti race-condition que
      dispara 2 chamadas LLM em paralelo).
    - force=True ignora a dedup (uso em mensagens criticas tipo erro de sistema).
    """
    global last_response_time
    if not text:
        return None
    lock = _get_conv_send_lock(conv_id) if conv_id else None
    if lock is not None:
        lock.acquire()
    try:
        # ============================================================
        # CAMADAS D1 / D2 / D5 (2026-05-25): hard-stop humano + anti-burst
        # ============================================================
        # Caso Debora (11975717913): apos atendente finalizar as 12:49,
        # bot enviou 4 mensagens (follow-up duplicado + close + resposta
        # alucinada sobre exames veterinarios) entre 12:52 e 12:54.
        if not force and conv_id:
            # D5: burst lock (anti-duplicado por race condition)
            if _send_burst_recent(conv_id):
                p(f"  [BURST-LOCK] {conv_id[:12]} envio em <{_SEND_BURST_S}s - SUPRIMIDO")
                return 'suppressed'
            # D1: ultima outgoing eh atendente humano nas ultimas 6h
            try:
                _h1, _h1_name = _last_outgoing_is_human_attendant(conv_id)
                if _h1:
                    p(f"  [HUMAN-GUARD-D1] {conv_id[:12]} ultima outgoing eh {_h1_name} (humano) - SUPRIMIDO")
                    return 'suppressed'
            except Exception:
                pass
            # D2: humano encerrou (finalizou o atendimento) nas ultimas 6h
            try:
                _h2, _h2_who = _human_closed_conversation(conv_id)
                if _h2:
                    p(f"  [HUMAN-GUARD-D2] {conv_id[:12]} {_h2_who} finalizou o atendimento - SUPRIMIDO")
                    return 'suppressed'
            except Exception:
                pass
            # D6 (2026-05-26): humano ATRIBUIDO atualmente (mesmo sem ter
            # falado ainda). Caso reportado: nota '*Aluno esperando ha 208min
            # — Debora ainda nao respondeu*' chegou ao chat enquanto Debora
            # era atendente atribuida. D1 nao pega (so checa quem FALOU). D6
            # cobre o intervalo entre atribuicao e primeira fala do humano.
            try:
                if _dcz_conv_has_human(conv_id, timeout=5):
                    p(f"  [HUMAN-GUARD-D6] {conv_id[:12]} atendente humano atribuido - SUPRIMIDO")
                    return 'suppressed'
            except Exception:
                pass
        # === RECHECK: handoff_active vigente -> suprimir resposta orfa ===
        # ACAO C (2026-05-21): expandido. Antes so checava dispatch <90s.
        # Agora qualquer handoff ativo (supervisor_block, retention,
        # pre_opening_queue, etc) suprime envio. Caso reportado: bot
        # respondia DEPOIS de "Vou te transferir para X" pq race condition
        # entre LLM e marcacao do handoff. 106 casos de sobre_resposta.
        #
        # Mensagens legitimas (msg de transferencia do distribute, nudges
        # do handoff loop, etc) usam force=True para escapar.
        if not force and conv_id:
            try:
                _ensure_dedup_tables()
                if _DEDUP_TABLES_READY:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT motivo, target_attendant,
                               EXTRACT(EPOCH FROM (NOW() - created_at)) as age_s
                        FROM handoff_active
                        WHERE conv_id = %s AND expires_at > NOW()
                        LIMIT 1
                    """, (conv_id,))
                    _ho = cur.fetchone()
                    cur.close()
                    conn.close()
                    if _ho:
                        _ho_motivo = _ho[0]
                        _ho_target = _ho[1] or ''
                        _ho_age = _ho[2] or 999
                        # dispatch <90s = race condition pura (do antigo check)
                        if _ho_motivo == 'dispatch' and _ho_age < 90:
                            p(f"  [DEDUP-DISPATCH] {conv_id[:12]} dispatch p/ {_ho_target} ha {_ho_age:.0f}s - SUPRIMIDO (race condition)")
                            return 'suppressed'
                        # qualquer outro handoff ativo = bot deve calar
                        if _ho_motivo in (
                            'supervisor_block', 'retention', 'retention_after_hours',
                            'polo_visit', 'after_hours_insist', 'pre_opening_queue',
                            'human_unavailable',
                        ):
                            p(f"  [HANDOFF-BLOCK] {conv_id[:12]} handoff_active={_ho_motivo} target={_ho_target} - SUPRIMIDO")
                            return 'suppressed'
            except Exception:
                pass
        if not force and conv_id and _body_recently_sent(conv_id, text, SEND_BODY_DEDUP_WINDOW_S):
            p(f"  [DEDUP-BODY] {conv_id[:12]} SUPRIMIDO - hash de body ja enviado em <{SEND_BODY_DEDUP_WINDOW_S}s")
            try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO ia_interaction_log "
                    "(conversation_id, pergunta_recebida, resposta_gerada, confianca, acao) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (conv_id, '(dedup)', text[:2000], 0.0, 'suprimido_dedup')
                )
                conn.commit()
                cur.close()
                conn.close()
            except Exception:
                pass
            return 'suppressed'
        meta_typing_on()
        chars = len(text)
        if chars < 80:
            time.sleep(0.5)
        elif chars < 300:
            time.sleep(1.0)
        else:
            time.sleep(1.5)
        status, resp = send_message_crm(conv_id, text, buttons)
        if status in (200, 201) and isinstance(resp, dict):
            processed_msg_ids.add(resp.get('id', ''))
        _track_sent_body(text)
        if conv_id:
            _register_body(conv_id, text, signature='body_send')
            if status in (200, 201):
                _mark_send_burst(conv_id)
        last_response_time = time.time()
        if buttons:
            p(f"    Enviado com {len(buttons)} botoes")
        return status
    finally:
        if lock is not None:
            lock.release()


def log_to_db(conv_id, question, response, confidence, action):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ia_interaction_log
            (conversation_id, pergunta_recebida, resposta_gerada, confianca, acao)
            VALUES (%s, %s, %s, %s, %s)
        """, (conv_id, question[:2000], response[:2000], confidence, action[:50]))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        p(f"    Log DB erro: {e}")


# ============================================================
# DISPARO REPLY TRACKER (2026-05-27)
# Detecta quando um aluno responde a um disparo (template HSM enviado via
# Cockpit) e registra na interaction_summary com tema='DISPARO'.
#
# Estrategia: usar data de referencia (DISPATCH_REF_DATE) do disparo ativo.
# Qualquer msg recebida do aluno com ts >= referencia eh tratada como
# retorno de disparo. Idempotente por conv_id.
#
# Bug anterior: a deteccao por campo `header`/`type` da msg falhava
# porque os templates do DCZ academico nao incluem esses campos em todas
# as msgs — so em UM template antigo de marco. Isso fazia conversas com
# msgs antigas do aluno (anteriores ao disparo) serem tratadas como
# retorno valido.
# ============================================================
_DISPARO_LOGGED_CONVS: set = set()   # dedup in-memory (limpo a cada rebuild)
DISPATCH_REF_DATE = os.environ.get('DISPATCH_REF_DATE', '2026-05-25T18:00:00')

# (2026-06-17) Override manual do rotulo da campanha. Se setado (ex.:
# "INADIMPLENCIA 26/05"), TODA resposta de disparo vira 'DISPARO · <LABEL>',
# ignorando a deteccao automatica. Vazio => usa deteccao por conteudo.
DISPATCH_LABEL = (os.environ.get('DISPATCH_LABEL', '') or '').strip()

# Palavras-ancora por TIPO de disparo. A classificacao olha o TEXTO DO TEMPLATE
# (mensagem que saiu), nao a resposta do aluno — o template eh padronizado e
# previsivel. Cada tipo pontua pelas ocorrencias; vence o maior; empate => GERAL.
_DISPATCH_TYPE_KEYWORDS = {
    'INADIMPLÊNCIA': (
        'em aberto', 'mensalidade', 'mensalidades', 'vencida', 'vencidas',
        'vencimento', 'venceu', 'boleto', 'fatura', 'regularizar', 'regularize',
        'inadimpl', 'pagamento pendente', 'pendencia', 'pendência', 'atraso',
        'em atraso', 'negociar', 'negociacao', 'negociação', 'parcela em aberto',
        'quitar', 'debito', 'débito',
    ),
    'CANCELAMENTO': (
        'cancelamento', 'cancelar sua matricula', 'cancelar sua matrícula',
        'reativar', 'reative', 'reativacao', 'reativação', 'voltar a estudar',
        'retomar seus estudos', 'retomar os estudos', 'retornar ao curso',
        'trancou', 'trancada', 'desistencia', 'desistência', 'sentimos sua falta',
        'voltar para a faculdade', 'voltar pra faculdade',
    ),
    'REMATRÍCULA': (
        'rematricula', 'rematrícula', 'renovacao de matricula',
        'renovação de matrícula', 'renove sua matricula', 'renove sua matrícula',
        'garanta sua vaga', 'proximo semestre', 'próximo semestre',
        'matricula 2026', 'matrícula 2026', 'renovar a matricula',
        'renovar a matrícula',
    ),
}


def _classify_dispatch_type(template_body):
    """Classifica o TIPO do disparo pelo texto do template (OUT).
    Retorna 'INADIMPLÊNCIA'|'CANCELAMENTO'|'REMATRÍCULA' ou 'GERAL' (na duvida)."""
    if not template_body:
        return 'GERAL'
    import unicodedata
    t = template_body.lower()
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    scores = {}
    for tipo, kws in _DISPATCH_TYPE_KEYWORDS.items():
        s = 0
        for kw in kws:
            kw_norm = ''.join(c for c in unicodedata.normalize('NFD', kw.lower())
                              if unicodedata.category(c) != 'Mn')
            if kw_norm in t_norm:
                s += 1
        if s:
            scores[tipo] = s
    if not scores:
        return 'GERAL'
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if len(ordered) >= 2 and ordered[0][1] == ordered[1][1]:
        return 'GERAL'  # empate => nao chuta
    return ordered[0][0]


# (2026-06-18) Fonte da verdade dos disparos: banco 'disparos' (mesmo servidor),
# tabela activation_dispatch_events — alimentada pelo disparador externo
# (banco-dcz-crm-sync / disparador_whatsapp). Cada evento traz datacrazy_lead_id,
# telefone, rgm, category e template_name. Substitui a adivinhacao por palavra-chave.
_DISPATCH_LOOKUP_CACHE = {}  # chave -> (resultado|None, expiry_ts)
_DISPATCH_LOOKUP_TTL = 600   # 10 min (cacheia hit E miss)


def _lookup_dispatch(phone=None, lead_id=None, rgm=None, max_days=30):
    """Retorna o disparo mais recente do contato (category/template_name/created_at)
    a partir do banco 'disparos'. SOMENTE LEITURA. None se nao achar.
    Prioridade de chave: datacrazy_lead_id > telefone (sufixo de digitos) > rgm."""
    if not (phone or lead_id or rgm):
        return None
    ck = f"{lead_id or ''}|{phone or ''}|{rgm or ''}"
    cached = _DISPATCH_LOOKUP_CACHE.get(ck)
    if cached and cached[1] > time.time():
        return cached[0]
    result = None
    try:
        cfg = DB_CONFIG.copy()
        cfg['dbname'] = 'disparos'
        cfg['connect_timeout'] = 5
        cfg['options'] = '-c statement_timeout=8000'
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # (2026-07-08) Casa por QUALQUER chave (lead_id OR telefone OR rgm) e pega o
        # disparo MAIS RECENTE no geral. Antes priorizava lead_id e PARAVA nele — mas
        # o mesmo aluno pode ter lead duplicado/antigo no DataCrazy, e o disparo real
        # (recente) fica sob outro lead/telefone. Ex.: Shaja tinha lead antigo com
        # docs 11/06 e o caa_cancelamento 08/07 sob o lead novo -> a nota mostrava o
        # disparo errado (11/06). Unindo as chaves num OR e ordenando por data, o
        # disparo mais recente vence independente de qual chave casou.
        conds, params = [], []
        if lead_id:
            conds.append("datacrazy_lead_id = %s")
            params.append(lead_id)
        tail = re.sub(r'\D', '', phone or '')
        if len(tail) > 11 and tail.startswith('55'):
            tail = tail[2:]
        tail = tail[-10:]
        if tail:
            conds.append("right(regexp_replace(telefone,'\\D','','g'), %s) = %s")
            params.extend([len(tail), tail])
        if rgm:
            conds.append("rgm = %s")
            params.append(str(rgm))
        row = None
        if conds:
            cur.execute(
                "SELECT category, template_name, created_at "
                "FROM activation_dispatch_events "
                "WHERE status='sent' AND created_at > now() - make_interval(days => %s) "
                "AND (" + " OR ".join(conds) + ") "
                "ORDER BY created_at DESC LIMIT 1",
                [max_days] + params)
            row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            result = {'category': row['category'],
                      'template_name': row['template_name'],
                      'created_at': row['created_at']}
    except Exception as e:
        p(f"  [DISPARO-LOOKUP] erro: {e}")
        result = None
    _DISPATCH_LOOKUP_CACHE[ck] = (result, time.time() + _DISPATCH_LOOKUP_TTL)
    return result


def _dispatch_label(category, template_name):
    """Mapeia (category, template) do banco de disparos para o rotulo do dashboard.
    Granularidade hibrida: 'financeiro' separa INADIMPLENCIA x ATIVACAO pelo template."""
    c = (category or '').strip().lower()
    t = (template_name or '').strip().lower()
    if c == 'financeiro':
        return 'DISPARO · INADIMPLÊNCIA'
    if c == 'docs-pendentes':
        return 'DISPARO · DOCS PENDENTES'
    if c == 'processos-caa':
        if 'cancel' in t:
            return 'DISPARO · CANCELAMENTO'
        return 'DISPARO · CAA'
    if c:
        return 'DISPARO · ' + (category or '').strip().upper()
    return 'DISPARO · GERAL'


def _dispatch_tema(template_body, phone=None, lead_id=None, rgm=None):
    """Tema final do retorno de disparo. Prioridade:
    1) DISPATCH_LABEL (override manual via env);
    2) banco 'disparos' (fonte da verdade) via _lookup_dispatch;
    3) fallback: heuristica por palavra-chave no template (_classify_dispatch_type)."""
    if DISPATCH_LABEL:
        return f'DISPARO · {DISPATCH_LABEL}'
    hit = _lookup_dispatch(phone=phone, lead_id=lead_id, rgm=rgm)
    if hit:
        return _dispatch_label(hit.get('category'), hit.get('template_name'))
    return f'DISPARO · {_classify_dispatch_type(template_body)}'


def _dispatch_origin_line(phone=None, lead_id=None, rgm=None):
    """(2026-07-07) Linha de contexto p/ o consultor: se o contato veio de um
    disparo recente, retorna '📣 Origem: DISPARO · <rotulo> (template X, enviado
    DD/MM)'. Reaproveita _lookup_dispatch (SOMENTE LEITURA, cacheado). '' se nada.
    Serve p/ o consultor saber a campanha mesmo quando o aluno so responde
    'boa tarde' e o Motivo fica magro."""
    try:
        hit = _lookup_dispatch(phone=phone, lead_id=lead_id, rgm=rgm)
    except Exception:
        hit = None
    if not hit:
        return ''
    dt = hit.get('created_at')
    # (2026-07-08) So rotula origem se o disparo for RECENTE (<= 3 dias). Se o ultimo
    # disparo do aluno foi ha mais de 3 dias, ele provavelmente NAO esta respondendo
    # aquele disparo -> nao mostra a nota de origem p/ nao confundir o consultor.
    if dt:
        try:
            from datetime import datetime as _dt2
            _now = _dt2.now(dt.tzinfo) if getattr(dt, 'tzinfo', None) else _dt2.now()
            if (_now - dt).total_seconds() > 3 * 86400:
                return ''
        except Exception:
            pass
    label = _dispatch_label(hit.get('category'), hit.get('template_name'))
    extra = []
    tpl = (hit.get('template_name') or '').strip()
    if tpl:
        extra.append(f'template {tpl}')
    if dt:
        try:
            extra.append('enviado ' + dt.strftime('%d/%m'))
        except Exception:
            pass
    suffix = f' ({", ".join(extra)})' if extra else ''
    return f'📣 Origem: {label}{suffix}'


def _extract_dispatch_template(msgs, last_user_msg=None):
    """Retorna o corpo do template (msg OUT dispatch-like imediatamente anterior
    a resposta do aluno), ou '' se nao achar. Espelha a logica de _is_dispatch_reply."""
    if not msgs:
        return ''
    last_received = last_user_msg if (last_user_msg and last_user_msg.get('received', False)) else None
    if last_received is None:
        for m in msgs:
            if m.get('received', False):
                last_received = m
                break
    if not last_received:
        return ''
    recv_ts = _msg_ts(last_received)
    for m in msgs:
        if m.get('received', False):
            continue
        out_ts = _msg_ts(m)
        if out_ts and recv_ts and out_ts < recv_ts and _looks_like_dispatch_msg(m):
            return (m.get('body') or m.get('text') or '').strip()
    return ''


def _log_dispatch_reply_once(conv_id, phone, name, user_msg, response_msg='', tema='DISPARO · GERAL'):
    """Registra resposta de disparo em interaction_summary (idempotente por conv_id)."""
    if conv_id in _DISPARO_LOGGED_CONVS:
        return False
    # (2026-06-17) So registra o retorno de disparo quando ha PERGUNTA/conteudo
    # de fato. Saudacao pura ("Ola"/"boa tarde") ou filler ("ok"/"tudo bem"/".")
    # NAO entram como pergunta — nao marca no dedup, espera o aluno mandar algo
    # real numa proxima mensagem.
    if not _is_substantive_student_msg(user_msg):
        return False
    try:
        conn = get_db()
        cur = conn.cursor()
        # dedup: qualquer tema de disparo (DISPARO ou 'DISPARO · <TIPO>') ja conta
        cur.execute(
            "SELECT 1 FROM interaction_summary WHERE conv_id=%s "
            "AND tema LIKE 'DISPARO%%' LIMIT 1",
            (conv_id,)
        )
        if cur.fetchone():
            _DISPARO_LOGGED_CONVS.add(conv_id)
            cur.close(); conn.close()
            return False
        cur.execute("""
            INSERT INTO interaction_summary
            (phone, student_name, tema, subtema, sentimento, resolvido,
             nps_implicito, pergunta_aluno, resposta_agente, conv_id, created_at)
            VALUES (%s, %s, %s, 'retorno_disparo', 'neutro', 'parcial',
                    7, %s, %s, %s, NOW())
        """, (
            (phone or '')[-11:],
            (name or '')[:200],
            tema or 'DISPARO · GERAL',
            (user_msg or '')[:2000],
            (response_msg or '')[:2000],
            conv_id or '',
        ))
        conn.commit()
        cur.close(); conn.close()
        _DISPARO_LOGGED_CONVS.add(conv_id)
        p(f"  [DISPARO-LOG] retorno registrado conv={conv_id[:12]} tema='{tema}' '{(user_msg or '')[:40]}'")
        return True
    except Exception as e:
        p(f"  [DISPARO-LOG] erro: {e}")
        return False


# ============================================================
# FILTRO DE "PERGUNTA DE FATO" (2026-06-17)
# Usado para nao registrar saudacao/filler como pergunta no feedback.
# ============================================================
# Fillers / acks que NAO sao pergunta (resposta a disparo so com "ok/tudo bem/.")
_DISPATCH_FILLER_PHRASES = {
    'ok', 'okay', 'okk', 'okey', 'ok obrigado', 'ok obrigada', 'blz', 'beleza',
    'sim', 'nao', 'ta', 'ta bom', 'ta bem', 'tah', 'tudo bem', 'tudo bom',
    'certo', 'entendi', 'obrigado', 'obrigada', 'obg', 'vlw', 'valeu',
    'grato', 'grata', 'de boa', 'tranquilo', 'show', 'perfeito', 'otimo',
    'isso', 'aham', 'uhum', 'positivo', 'ciente', 'ok ciente', 'recebido',
    'agradeco', 'agradecida', 'agradecido',
}


def _is_substantive_student_msg(text):
    """True se a msg do aluno tem conteudo de PERGUNTA/QUESTIONAMENTO de fato
    (nao eh apenas saudacao 'ola/boa tarde/bom dia' nem filler 'ok/tudo bem/.')."""
    if not text or not text.strip():
        return False
    if _is_pure_greeting(text):
        return False
    import unicodedata
    t = text.strip().lower()
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    t_clean = ''.join(c for c in t_norm if c.isalnum() or c.isspace()).strip()
    if not t_clean:
        return False  # so pontuacao / emoji
    if t_clean in _DISPATCH_FILLER_PHRASES:
        return False
    return True


# ============================================================
# TEMA RETENÇÃO (2026-06-17)
# Toda conversa encaminhada ao time de Retencao (Wesley/Danubia) e registrada
# com tema='RETENÇÃO' (substitui uma linha DISPARO da mesma conversa, quando
# o retorno de disparo virou retencao). Da ao gestor controle/filtro do que vai
# para a retencao. A coluna RESPOSTA e preenchida depois por
# _capture_retention_responses (1a resposta humana do time de retencao).
# ============================================================
_RETENCAO_LOGGED_CONVS: set = set()


def _log_retention_interaction(conv_id, phone, name, question, alvo):
    """Registra/atualiza a interacao como tema='RETENÇÃO'. Idempotente por conv_id."""
    if not conv_id or conv_id in _RETENCAO_LOGGED_CONVS:
        return False
    try:
        q = (question or '').strip()
        if _is_pure_greeting(q):
            q = ''  # pergunta = motivo real; ignora saudacao pura
        try:
            alvo_first = (alvo or '').strip().lower().split()[0]
        except Exception:
            alvo_first = ''
        subt = f"retencao_{alvo_first}" if alvo_first else 'retencao'

        nm = (name or '').strip()
        if not nm and phone:
            try:
                prof = identify_student(
                    (phone or '').replace('+', '').replace(' ', '').replace('-', ''))
                if prof and prof.get('name'):
                    nm = prof['name']
            except Exception:
                pass

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, tema FROM interaction_summary WHERE conv_id=%s "
            "ORDER BY created_at DESC LIMIT 1", (conv_id,))
        row = cur.fetchone()
        if row:
            rid, rtema = row[0], row[1]
            if rtema == 'RETENÇÃO':
                _RETENCAO_LOGGED_CONVS.add(conv_id)
                cur.close(); conn.close()
                return False
            cur.execute("""
                UPDATE interaction_summary
                SET tema='RETENÇÃO', subtema=%s, resolvido='escalado',
                    pergunta_aluno=COALESCE(NULLIF(%s,''), pergunta_aluno),
                    student_name=COALESCE(NULLIF(%s,''), student_name)
                WHERE id=%s
            """, (subt, q[:2000], nm[:200], rid))
        else:
            cur.execute("""
                INSERT INTO interaction_summary
                (phone, student_name, tema, subtema, sentimento, resolvido,
                 nps_implicito, pergunta_aluno, resposta_agente, conv_id, created_at)
                VALUES (%s, %s, 'RETENÇÃO', %s, 'neutro', 'escalado',
                        7, %s, '', %s, NOW())
            """, ((phone or '')[-11:], nm[:200], subt, q[:2000], conv_id))
        conn.commit()
        cur.close(); conn.close()
        _RETENCAO_LOGGED_CONVS.add(conv_id)
        _DISPARO_LOGGED_CONVS.discard(conv_id)
        p(f"  [RETENÇÃO-LOG] conv={conv_id[:12]} -> tema=RETENÇÃO ({alvo}) '{q[:40]}'")
        return True
    except Exception as e:
        p(f"  [RETENÇÃO-LOG] erro: {e}")
        return False


# Marcadores de saudacao/abertura padrao do atendente (nao sao "resposta de fato")
_RETENTION_OPENING_MARKERS = (
    'tudo bem', 'tudo bom', 'como vai', 'como voce esta', 'como você está',
    'sou o ', 'sou a ', 'sou o(a)', 'sou a(o)', 'aqui e o ', 'aqui é o ',
    'aqui e a ', 'aqui é a ', 'aqui e o(a)', 'aqui é o(a)', 'aqui quem fala',
    'meu nome e', 'meu nome é', 'me chamo', 'falo do', 'falo da',
    'do time de', 'da equipe de', 'equipe de suporte', 'equipe de atendimento',
    'time de retencao', 'time de retenção', 'time de suporte', 'time de atendimento',
    'setor de retencao', 'setor de retenção', 'da retencao', 'da retenção',
    'do setor de retencao', 'do setor de retenção',
    'como posso te ajudar', 'em que posso ajudar', 'como posso ajudar',
)


# Auto-apresentacao FORTE: se aparece, eh abertura padrao INDEPENDENTE do tamanho
# (as aberturas "Olá, tudo bem? Aqui é o(a) X, faço parte do time de..." sao longas)
_RETENTION_INTRO_STRONG = (
    'aqui e o(a)', 'aqui é o(a)', 'aqui e a(o)', 'aqui é a(o)',
    'faco parte do time', 'faço parte do time', 'faco parte da equipe',
    'faço parte da equipe', 'sou o(a)', 'sou a(o)', 'me chamo',
    'meu nome e ', 'meu nome é ', 'aqui quem fala',
)


def _is_retention_opening(text):
    """True se a msg humana parece apenas saudacao/abertura padrao da retencao
    (sem conteudo de resposta de fato)."""
    if not text:
        return True
    if _is_pure_greeting(text):
        return True
    import unicodedata
    t = text.strip().lower()
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    # auto-apresentacao explicita -> abertura, mesmo que longa
    if any(m in t_norm for m in _RETENTION_INTRO_STRONG):
        return True
    has_marker = any(m in t_norm for m in _RETENTION_OPENING_MARKERS)
    return bool(has_marker and len(t_norm) <= 160)


# Pergunta padrao de abertura da retencao (NAO eh "resposta de retencao" de fato)
_RETENTION_QUESTION_MARKERS = (
    'por qual motivo', 'qual o motivo', 'qual seria o motivo', 'qual e o motivo',
    'qual é o motivo', 'motivo do cancelamento', 'motivo da desistencia',
    'motivo da desistência', 'motivo do trancamento', 'por que deseja cancelar',
    'porque deseja cancelar', 'por que voce deseja cancelar', 'deseja cancelar ?',
    'deseja cancelar?', 'poderia me dizer o motivo', 'poderia nos dizer o motivo',
    'pode me dizer o motivo', 'me conta o motivo', 'o que motivou',
    'gostaria de entender o motivo', 'qual o motivo da', 'motivo da solicitacao',
    'o que te levou', 'o que aconteceu para',
)
# Fechamentos / agradecimentos curtos (NAO sao argumento de retencao)
_RETENTION_CLOSING_MARKERS = (
    'por nada', 'disponha', 'estamos a disposicao', 'estou a disposicao',
    'fico a disposicao', 'ficamos a disposicao', 'qualquer coisa', 'qualquer duvida',
    'tenha um otimo', 'tenha uma otima', 'tenha um bom', 'tenha uma boa',
    'obrigado', 'obrigada', 'agradeco', 'combinado', 'ok', 'certo',
    # follow-ups/checagens genericas (nao sao argumento de retencao)
    'ainda precisa de ajuda', 'precisa de ajuda', 'ajudo em algo mais',
    'posso ajudar em algo', 'posso te ajudar em algo', 'ficou com alguma duvida',
    'ficou alguma duvida', 'ainda esta ai', 'ainda esta por ai',
    'precisa de mais alguma', 'mais alguma duvida', 'ainda precisa',
)


# Pedidos de dado cadastral / status operacional (coleta, nao argumento de retencao)
_RETENTION_OPERATIONAL_MARKERS = (
    'abriu a solicitacao', 'abriu solicitacao', 'voce abriu', 'ja abriu',
    'qual o protocolo', 'numero do protocolo', 'esta em andamento', 'em andamento',
    'foi aberta', 'foi aberto', 'ja foi aberto', 'ja foi aberta', 'quando foi aberta',
    'tenta acessar', 'tente acessar', 'consegue acessar', 'me informa seu',
    'me passa seu', 'me confirma seu', 'pode confirmar seu', 'confirma seu',
    'encerramos por hoje', 'horario de atendimento', 'meu expediente',
    'expediente esta se encerrando', 'fora do horario', 'qual o seu nome',
)
# Afirmacoes/acks curtos ("Compreendo!", "Entendo, Carol")
_RETENTION_ACK_MARKERS = (
    'compreendo', 'compreendi', 'entendo', 'entendi', 'compreendido', 'entendido',
    'perfeito', 'beleza', 'isso mesmo', 'tudo certo', 'show', 'sim, isso',
)


# Mensagens que NUNCA sao argumento de retencao (descarta independente do tamanho):
# pesquisa de satisfacao, fim de expediente e status puro de solicitacao.
_RETENTION_HARD_SKIP = (
    'avaliar meu atendimento', 'avaliar o atendimento', 'avalie meu atendimento',
    'avaliar o meu atendimento', 'sua opiniao e muito importante',
    'sua opiniao e importante', 'poderia avaliar', 'nao requer nenh',
    'expediente esta se encerrando', 'meu expediente', 'encerramos por hoje',
    'horario de atendimento chegou', 'no caso de duvidas peco',
    'solicitacao esta em andamento', 'sua solicitacao esta em andamento',
    'prazo previsto', 'esta em andamento, o prazo',
)


def _is_retention_skip(text):
    """True se a msg humana NAO eh o argumento/resposta de retencao de fato:
    saudacao/abertura, a pergunta padrao 'por qual motivo deseja cancelar',
    fechamento/agradecimento, pedido de cpf/dado, status operacional, ack curto,
    pesquisa de satisfacao, fim de expediente ou link/protocolo isolado."""
    if _is_retention_opening(text):
        return True
    import unicodedata
    t = (text or '').strip().lower()
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    if any(m in t_norm for m in _RETENTION_HARD_SKIP):
        return True
    if any(m in t_norm for m in _RETENTION_QUESTION_MARKERS):
        return True
    # fechamento/agradecimento: so pula se for CURTO (evita pular conteudo longo
    # que apenas termina com 'qualquer duvida...')
    if len(t_norm) <= 90 and any(m in t_norm for m in _RETENTION_CLOSING_MARKERS):
        return True
    if t_norm.startswith('link') or t_norm.startswith('http'):
        return True
    # saudacao personalizada curta: "Boa tarde, Carol!" / "Olá, em que posso ajudar?"
    _greet_starts = ('ola', 'oi', 'oie', 'oii', 'opa', 'bom dia', 'boa tarde', 'boa noite')
    if len(t_norm) <= 45 and any(t_norm.startswith(g) for g in _greet_starts):
        return True
    # pedido de dado cadastral curto (cpf/rgm/email/nascimento)
    if len(t_norm) <= 70 and ('cpf' in t_norm or 'rgm' in t_norm
                              or 'data de nascimento' in t_norm
                              or 'seu e-mail' in t_norm or 'seu email' in t_norm
                              or 'sua matricula' in t_norm):
        return True
    # status/operacional curto
    if len(t_norm) <= 70 and any(m in t_norm for m in _RETENTION_OPERATIONAL_MARKERS):
        return True
    # afirmacao/ack curto ("Compreendo!", "Entendo, Carol")
    if len(t_norm) <= 40 and any(t_norm == m or t_norm.startswith(m + ' ')
                                 or t_norm.startswith(m + ',') or t_norm.startswith(m + '!')
                                 for m in _RETENTION_ACK_MARKERS):
        return True
    return False


def _capture_retention_responses():
    """Preenche a coluna RESPOSTA das linhas tema='RETENÇÃO' com a 1a resposta
    HUMANA substantiva do time de Retencao (pula saudacao/abertura padrao deles).
    Roda periodicamente; so olha linhas recentes (3 dias) ainda sem resposta."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, conv_id FROM interaction_summary
            WHERE tema='RETENÇÃO'
              AND (resposta_agente IS NULL OR resposta_agente='')
              AND conv_id IS NOT NULL AND conv_id != ''
              AND created_at >= NOW() - INTERVAL '3 days'
            ORDER BY created_at DESC LIMIT 40
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        p(f"  [RETENÇÃO-RESP] erro query: {e}")
        return

    updated = 0
    for rid, conv_id in rows:
        try:
            msgs = get_conversation_messages_api(conv_id, limit=40)
            if not msgs:
                continue
            ordered = sorted(msgs, key=lambda m: _msg_ts(m))
            human_msgs = []
            for m in ordered:
                if m.get('received', False):
                    continue
                if m.get('isInternal'):
                    continue
                att = m.get('attendant')
                if not (att and isinstance(att, dict) and att.get('userId')):
                    continue  # so msg de HUMANO (atendente), nao bot/IA
                body = (m.get('body') or m.get('text') or '').strip()
                if not body or is_bot_message(body):
                    continue
                if body.startswith('['):
                    continue  # nota/tag de sistema, nao eh resposta humana
                human_msgs.append(body)
            if not human_msgs:
                continue
            # Escolhe o ARGUMENTO de retencao: a maior msg humana substantiva que
            # nao seja saudacao/abertura/pergunta padrao/fechamento/cpf/operacional.
            cands = [b for b in human_msgs
                     if _is_substantive_student_msg(b) and not _is_retention_skip(b)]
            chosen = max(cands, key=len) if cands else ''
            if not chosen:
                continue
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE interaction_summary SET resposta_agente=%s WHERE id=%s",
                        (chosen[:2000], rid))
            conn.commit()
            cur.close(); conn.close()
            updated += 1
        except Exception as e_one:
            p(f"  [RETENÇÃO-RESP] erro conv={str(conv_id)[:12]}: {e_one}")
            continue
    if updated:
        p(f"  [RETENÇÃO-RESP] respostas da retencao capturadas: {updated}")


def _msg_ts(m):
    """Extrai timestamp ISO da mensagem (createdAt|timestamp|date|sentAt)."""
    for k in ('createdAt', 'timestamp', 'date', 'sentAt'):
        v = m.get(k) if isinstance(m, dict) else None
        if v:
            return str(v)
    return ''


def _looks_like_dispatch_msg(m):
    """Heuristica: a msg OUT parece um disparo HSM (template enviado via Cockpit).

    Padrao observado nos disparos academicos:
    - received=False (saida)
    - attendant ausente/None (nao foi humano)
    - sem buttons (nao eh menu interativo do bot)
    - body longo (>= 80 chars) — disparos sao mensagens estruturadas longas
    - nao tem interpretedContentPending (esse campo eh do bot IA respondendo)
    """
    if not m or not isinstance(m, dict):
        return False
    if m.get('received', False):
        return False
    att = m.get('attendant')
    if att and isinstance(att, dict) and att.get('userId'):
        return False  # humano enviou
    if m.get('buttons'):
        return False
    if m.get('interpretedContentPending'):
        return False
    body = (m.get('body') or m.get('text') or '').strip()
    return len(body) >= 80


def _is_dispatch_reply(msgs, last_user_msg=None):
    """True se a msg recebida do aluno tem ts >= DISPATCH_REF_DATE
    E a msg OUT imediatamente anterior parece um disparo (template HSM).

    (2026-05-27 v3) Antes marcavamos qualquer reply apos a data de
    referencia como retorno de disparo — pegava falso positivo de alunos
    que escreviam organicamente sem ter recebido o disparo. Agora exige
    tambem que o ultimo OUT antes da resposta seja dispatch-like.
    """
    if not msgs or not DISPATCH_REF_DATE:
        return False

    # Pega a msg recebida do aluno mais recente
    last_received = last_user_msg if (last_user_msg and last_user_msg.get('received', False)) else None
    if last_received is None:
        for m in msgs:
            if m.get('received', False):
                last_received = m
                break
    if not last_received:
        return False
    recv_ts = _msg_ts(last_received)
    if not recv_ts or recv_ts < DISPATCH_REF_DATE:
        return False

    # Procura a msg OUT imediatamente anterior ao reply
    prev_out = None
    for m in msgs:
        if m.get('received', False):
            continue
        out_ts = _msg_ts(m)
        if out_ts and out_ts < recv_ts:
            prev_out = m
            break
    if not prev_out:
        return False

    return _looks_like_dispatch_msg(prev_out)


def _move_business_to_base_alunos(phone):
    """Move o business de volta para Base de Alunos (ex: aluno retornou após encerramento)."""
    if not phone:
        return False
    try:
        search_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        phones_to_try = [search_phone]
        if not search_phone.startswith('55'):
            phones_to_try.append('55' + search_phone)
        biz_id = ''
        for try_phone in phones_to_try:
            if biz_id:
                break
            r = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                             params={'search': try_phone, 'limit': 5}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                biz_list = data.get('data', data) if isinstance(data, dict) else data
                if isinstance(biz_list, list) and biz_list:
                    biz_id = biz_list[0].get('id', '')
        if not biz_id:
            p(f"  [REABRIR] Nenhum business encontrado para ...{search_phone[-4:]}")
            return False
        r2 = requests.patch(
            f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
            json={'stageId': STAGE_BASE_ALUNOS_ID}, timeout=10
        )
        ok = r2.status_code in (200, 204)
        p(f"  [REABRIR] Business {biz_id[:16]} -> Base de Alunos (status={r2.status_code}, ok={ok})")
        return ok
    except Exception as e:
        p(f"  [REABRIR] Erro: {e}")
        return False


def _move_business_to_encerramento(phone):
    """Encontra o business pelo telefone e move para o stage de Encerramento."""
    if not phone:
        p(f"  [ENCERR] Sem telefone -> skip mover business")
        return False
    try:
        search_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        phones_to_try = [search_phone]
        if not search_phone.startswith('55'):
            phones_to_try.append('55' + search_phone)
        biz_id = ''
        for try_phone in phones_to_try:
            if biz_id:
                break
            p(f"  [ENCERR] Buscando business para ...{try_phone[-4:]} (len={len(try_phone)})")
            r = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                             params={'search': try_phone, 'limit': 5}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                biz_list = data.get('data', data) if isinstance(data, dict) else data
                if isinstance(biz_list, list) and biz_list:
                    biz_id = biz_list[0].get('id', '')
        if not biz_id:
            p(f"  [ENCERR] Nenhum business encontrado para ...{search_phone[-4:]}")
            return False
        if not biz_id:
            p(f"  [ENCERR] Business sem ID")
            return False
        r2 = requests.patch(
            f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
            json={'stageId': STAGE_ENCERRAMENTO_ID}, timeout=10
        )
        ok = r2.status_code in (200, 204)
        p(f"  [ENCERR] Business {biz_id[:16]} -> Encerramento (status={r2.status_code}, ok={ok})")
        return ok
    except Exception as e:
        p(f"  [ENCERR] Erro ao mover business: {e}")
        return False


def close_conversation_crm(conv_id, phone=''):
    """Move business para Encerramento e finaliza a conversa no DataCrazy.

    (2026-05-27) Retry com backoff em ambos endpoints. Caso reportado:
    agente enviava farewell mas conversa continuava na fila — /finish
    podia falhar silenciosamente e nao havia segunda tentativa.
    """
    biz_ok = False
    if phone:
        biz_ok = _move_business_to_encerramento(phone)
    else:
        p(f"  [CLOSE] Telefone vazio, business NÃO será movido para Encerramento")

    time.sleep(2)

    fin_ok = False
    last_status = 0
    for attempt in range(1, 4):  # ate 3 tentativas
        try:
            r = requests.post(
                f'{DCZ_API}/api/v1/conversations/{conv_id}/finish',
                headers=H, json={}, timeout=15
            )
            last_status = r.status_code
            if r.status_code in (200, 201, 204):
                fin_ok = True
                p(f"  [CLOSE] Finish via DCZ_API ok (attempt={attempt}, status={r.status_code})")
                break
            p(f"  [CLOSE] Finish via DCZ_API falha (attempt={attempt}, status={r.status_code})")
        except Exception as e:
            p(f"  [CLOSE] Erro DCZ_API finish (attempt={attempt}): {e}")
        try:
            r2 = requests.post(
                f'{DCZ_MSG}/messaging/conversations/{conv_id}/finish',
                headers=H, json={}, timeout=15
            )
            last_status = r2.status_code
            if r2.status_code in (200, 201, 204):
                fin_ok = True
                p(f"  [CLOSE] Finish via DCZ_MSG ok (attempt={attempt}, status={r2.status_code})")
                break
            p(f"  [CLOSE] Finish via DCZ_MSG falha (attempt={attempt}, status={r2.status_code})")
        except Exception as e2:
            p(f"  [CLOSE] Erro DCZ_MSG fallback (attempt={attempt}): {e2}")
        time.sleep(1.5 * attempt)

    p(f"  [CLOSE] Conv {conv_id[:16]} -> biz_encerr={biz_ok} | finish={fin_ok} (last_status={last_status})")
    if fin_ok:
        update_pending_escalation_status(
            conv_id, 'resolved',
            note='✅ Conversa encerrada — fila Cockpit marcada como *Resolvido* automaticamente.',
        )
    return 200 if fin_ok else 500


def _is_nosso_polo(polo_name):
    """Verifica se o polo do aluno é um dos nossos polos atendidos."""
    if not polo_name:
        return True
    import unicodedata
    norm = ''.join(c for c in unicodedata.normalize('NFD', polo_name.lower().strip())
                   if unicodedata.category(c) != 'Mn')
    norm = norm.replace('-', ' ').replace('_', ' ')
    for polo in POLOS_NOSSOS_NORMALIZED:
        if polo in norm or norm in polo:
            return True
    return False


def _move_business_to_perdido(phone):
    """Encontra o business pelo telefone e move para o stage PERDIDO."""
    if not phone:
        return False
    try:
        search_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        phones_to_try = [search_phone]
        if not search_phone.startswith('55'):
            phones_to_try.append('55' + search_phone)
        biz_id = ''
        for try_phone in phones_to_try:
            if biz_id:
                break
            r = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                             params={'search': try_phone, 'limit': 5}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                biz_list = data.get('data', data) if isinstance(data, dict) else data
                if isinstance(biz_list, list) and biz_list:
                    biz_id = biz_list[0].get('id', '')
        if not biz_id:
            p(f"  [PERDIDO] Nenhum business encontrado para ...{search_phone[-4:]}")
            return False
        r2 = requests.patch(
            f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
            json={'stageId': STAGE_PERDIDO_ID, 'attendant': None}, timeout=10
        )
        ok = r2.status_code in (200, 204)
        p(f"  [PERDIDO] Business {biz_id[:16]} -> Perdido + remover atendente (status={r2.status_code}, ok={ok})")
        return ok
    except Exception as e:
        p(f"  [PERDIDO] Erro: {e}")
        return False


def _handle_outro_polo(conv_id, phone, student_profile, polo_real):
    """Envia mensagens de outro polo, move para Perdido e finaliza a conversa.

    GUARD DE ACAO (dedup IDEMPOTENTE):
    Caso reportado: a funcao foi chamada 2x em ciclos sucessivos do agente
    (validacao de CPF + carga de perfil) e o dedup de body falhou em pegar
    a segunda execucao por timing. Resultado: 4 mensagens duplicadas + 2
    chamadas de finish/move pipeline.

    Agora: signature 'outro_polo_handled' por 24h. Segunda chamada SKIP
    independente do estado das tabelas de body dedup.
    """
    # Guard ao nivel da acao (nao so do envio de mensagem)
    try:
        if _signature_recently_sent(conv_id, 'outro_polo_handled', window_s=24 * 3600):
            p(f"  [OUTRO-POLO] {conv_id[:12]} dedup: ja tratado outro_polo nas ultimas 24h - SKIP")
            return
    except Exception:
        pass
    p(f"  [OUTRO-POLO] Polo '{polo_real}' não atendido -> redirecionando")
    # Marca IMEDIATAMENTE para evitar race com chamadas concorrentes/ciclos
    # sucessivos (linha 8741 vs 8984 do handle_message).
    try:
        _register_signature(conv_id, 'outro_polo_handled', f'polo:{polo_real}')
    except Exception:
        pass
    meta_typing_on()
    send_and_track(conv_id, OUTRO_POLO_MSG_1)
    time.sleep(2)
    send_and_track(conv_id, OUTRO_POLO_MSG_2)
    log_to_db(conv_id, f'[polo: {polo_real}]', OUTRO_POLO_MSG_1, 1.0, 'outro_polo')
    _move_business_to_perdido(phone)
    time.sleep(1)
    fin_ok = False
    try:
        r = requests.post(
            f'{DCZ_API}/api/v1/conversations/{conv_id}/finish',
            headers=H, json={}, timeout=15
        )
        if r.status_code in (200, 201, 204):
            fin_ok = True
    except Exception:
        pass
    if not fin_ok:
        try:
            r2 = requests.post(
                f'{DCZ_MSG}/messaging/conversations/{conv_id}/finish',
                headers=H, json={}, timeout=15
            )
            if r2.status_code in (200, 201, 204):
                fin_ok = True
        except Exception:
            pass
    p(f"  [OUTRO-POLO] Conv {conv_id[:16]} -> Perdido + Finalizada (fin={fin_ok})")
    return fin_ok


def transfer_to_human(conv_id, reason=''):
    """Sinaliza transferência para atendente humano via nota interna."""
    try:
        note = f"🔔 *Transferência solicitada pelo agente IA*"
        if reason:
            note += f"\nMotivo: {reason}"
        note += "\nPor favor, assuma esta conversa."
        payload = {'body': note, 'isInternal': True}
        r = requests.post(
            f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
            headers=H, json=payload, timeout=10
        )
        p(f"  Nota interna de transferência enviada (status={r.status_code})")
        return r.status_code
    except Exception as e:
        p(f"  Erro ao transferir: {e}")
        return 500


# ===================== DISTRIBUICAO AUTOMATICA =====================

def _now_sp():
    """Retorna datetime atual no fuso de São Paulo."""
    from datetime import datetime, timezone, timedelta
    utc_now = datetime.now(timezone.utc)
    sp_offset = timedelta(hours=-3)
    return utc_now + sp_offset


def is_within_business_hours(ref_now=None):
    """Verifica se estamos dentro do horário de atendimento humano.
    Defaults: Seg-Sex BUSINESS_HOURS_WEEKDAY_START–END | Sáb BUSINESS_HOURS_SATURDAY_START–END.
    Horários são lidos das globals (sobrescritas pelo agent_config).
    """
    now = ref_now or _now_sp()
    dow = now.weekday()  # 0=seg, 6=dom
    hour = now.hour

    if dow <= 4:
        return BUSINESS_HOURS_WEEKDAY_START <= hour < BUSINESS_HOURS_WEEKDAY_END
    elif dow == 5:
        return BUSINESS_HOURS_SATURDAY_START <= hour < BUSINESS_HOURS_SATURDAY_END
    return False


def _minutes_until_business_hours_start(ref_now=None):
    """Retorna minutos ate o proximo inicio do expediente.
    - Se ja esta dentro do expediente: 0
    - Senao: minutos ate o proximo inicio (pode ser dezenas, centenas, milhares)
    """
    now = ref_now or _now_sp()
    if is_within_business_hours(now):
        return 0
    dow = now.weekday()
    hour = now.hour
    minute = now.minute

    # antes do inicio no mesmo dia (seg-sex)
    if dow <= 4 and hour < BUSINESS_HOURS_WEEKDAY_START:
        target_min = BUSINESS_HOURS_WEEKDAY_START * 60
        cur_min = hour * 60 + minute
        return max(0, target_min - cur_min)
    # antes do inicio no sabado
    if dow == 5 and hour < BUSINESS_HOURS_SATURDAY_START:
        target_min = BUSINESS_HOURS_SATURDAY_START * 60
        cur_min = hour * 60 + minute
        return max(0, target_min - cur_min)
    # apos expediente seg-qui -> amanha (mesmo horario weekday)
    if dow <= 3 and hour >= BUSINESS_HOURS_WEEKDAY_END:
        end_of_day = 24 * 60 - (hour * 60 + minute)
        return end_of_day + BUSINESS_HOURS_WEEKDAY_START * 60
    # sexta apos expediente -> sabado
    if dow == 4 and hour >= BUSINESS_HOURS_WEEKDAY_END:
        end_of_day = 24 * 60 - (hour * 60 + minute)
        return end_of_day + BUSINESS_HOURS_SATURDAY_START * 60
    # sabado apos expediente -> segunda
    if dow == 5 and hour >= BUSINESS_HOURS_SATURDAY_END:
        end_of_day = 24 * 60 - (hour * 60 + minute)
        return end_of_day + 24 * 60 + BUSINESS_HOURS_WEEKDAY_START * 60
    # domingo -> segunda
    if dow == 6:
        end_of_day = 24 * 60 - (hour * 60 + minute)
        return end_of_day + BUSINESS_HOURS_WEEKDAY_START * 60
    # fallback
    return 9999


def _in_pre_opening_window(ref_now=None):
    """True se faltam <= PRE_OPENING_MARGIN_MIN para o expediente abrir."""
    if is_within_business_hours(ref_now):
        return False
    return _minutes_until_business_hours_start(ref_now) <= PRE_OPENING_MARGIN_MIN


def next_human_available_label(ref_now=None):
    """Retorna texto humanizado do próximo horário em que o time humano está disponível.
    Dentro do horário comercial retorna 'em breve' (humano já disponível).
    Fora do horário: 'hoje às 9h', 'amanhã às 9h', 'na segunda-feira às 9h', etc.
    """
    now = ref_now or _now_sp()
    dow = now.weekday()
    hour = now.hour

    weekday_start = BUSINESS_HOURS_WEEKDAY_START
    weekday_end = BUSINESS_HOURS_WEEKDAY_END
    sat_start = BUSINESS_HOURS_SATURDAY_START
    sat_end = BUSINESS_HOURS_SATURDAY_END

    if is_within_business_hours(now):
        return "em breve"

    # Antes do início no mesmo dia (seg-sex) → hoje
    if dow <= 4 and hour < weekday_start:
        return f"hoje às {weekday_start}h"
    # Antes do início no sábado → hoje
    if dow == 5 and hour < sat_start:
        return f"hoje às {sat_start}h"

    # Depois do expediente de seg-qui → amanhã (seg-sex normal)
    if dow <= 3 and hour >= weekday_end:
        return f"amanhã às {weekday_start}h"
    # Sexta após expediente → sábado
    if dow == 4 and hour >= weekday_end:
        return f"amanhã às {sat_start}h"
    # Sábado após expediente → segunda
    if dow == 5 and hour >= sat_end:
        return f"na segunda-feira às {weekday_start}h"
    # Domingo → segunda
    if dow == 6:
        return f"na segunda-feira às {weekday_start}h"

    return f"amanhã às {weekday_start}h"


# Constantes de modo de atendimento
ATTENDANCE_HUMAN_AVAILABLE = 'human_available'
ATTENDANCE_HUMAN_UNAVAILABLE = 'human_unavailable'
ATTENDANCE_AFTER_HOURS = 'after_hours'


def resolve_attendance_mode(check_consultant=False):
    """Decide o modo de atendimento atual.

    - after_hours       → fora do horário global; agente NÃO distribui
    - human_unavailable → dentro do horário, mas nenhum consultor passou nos filtros
                          (só apurado se check_consultant=True; caso contrário tratamos como
                          'a distribuição vai dizer'; mensagem específica é responsabilidade do caller)
    - human_available   → dentro do horário (consultor disponível se check_consultant=True)
    """
    if not is_within_business_hours():
        return ATTENDANCE_AFTER_HOURS
    if check_consultant:
        consultant = get_available_consultant()
        if not consultant:
            return ATTENDANCE_HUMAN_UNAVAILABLE
    return ATTENDANCE_HUMAN_AVAILABLE


def _student_first_name_prefix(conv_id):
    """Retorna ' Fulano' (com espaço) se conseguir extrair o primeiro nome, senão ''."""
    try:
        st = _conv_states.get(conv_id, {})
        prof = st.get('student_profile') or {}
        nm = prof.get('name') or ''
        if not nm:
            return ''
        first = nm.strip().split()[0]
        return ' ' + first.capitalize() if first else ''
    except Exception:
        return ''


def send_pre_opening_offer(conv_id, *, reason='pre_opening_offer', question=''):
    """Envia oferta de entrar na fila quando faltam minutos para abrir o expediente.
    NAO inscreve na fila ainda - so apos aluno aceitar (botao ou texto 'sim').
    """
    name_prefix = _student_first_name_prefix(conv_id)
    mins_left = _minutes_until_business_hours_start()
    start_label = next_human_available_label()
    msg = PRE_OPENING_MSG.format(name=name_prefix, start_label=start_label, mins_left=mins_left)
    sig = 'pre_opening_offer'
    if _signature_recently_sent(conv_id, sig, window_s=2 * 3600):
        p(f"  [PRE-OPENING] dedup: oferta ja feita nas ultimas 2h - suprimindo")
        return
    meta_typing_on()
    try:
        send_message_crm(conv_id, msg, buttons=PRE_OPENING_BUTTONS)
    except Exception:
        send_and_track(conv_id, msg)
    log_to_db(conv_id, question or '', msg, 1.0, sig)
    _register_signature(conv_id, sig, msg)
    st = _conv_states.setdefault(conv_id, _default_conv_state())
    st['_pre_opening_offer_ts'] = time.time()
    st['_pre_opening_pending'] = True
    st['_pre_opening_reason'] = reason
    st['_pre_opening_question'] = (question or '')[:500]
    st['_pre_opening_target'] = detect_preferred_attendant(question or '') or ''
    st['waiting_for_client'] = True
    st['inactivity_start'] = time.time()
    st['_last_responded_ts'] = time.time()


def accept_pre_opening(conv_id, question=''):
    """Aluno aceitou entrar na fila pre-abertura: registra e confirma."""
    name_prefix = _student_first_name_prefix(conv_id)
    start_label = next_human_available_label()
    msg = PRE_OPENING_ACCEPTED_MSG.format(name=name_prefix, start_label=start_label)
    sig = 'pre_opening_accepted'
    meta_typing_on()
    if not _signature_recently_sent(conv_id, sig, window_s=4 * 3600):
        send_and_track(conv_id, msg)
        log_to_db(conv_id, question or '', msg, 1.0, sig)
        _register_signature(conv_id, sig, msg)
    st = _conv_states.setdefault(conv_id, _default_conv_state())
    preferred = st.get('_pre_opening_target') or detect_preferred_attendant(st.get('_pre_opening_question', '') or '')
    orig_reason = st.get('_pre_opening_reason') or 'pre_opening_queue'
    orig_question = st.get('_pre_opening_question') or question or ''
    record_pending_escalation(
        conv_id, reason=orig_reason, tier='pre_opening',
        retorno_label=start_label, question=orig_question,
        preferred_attendant=preferred,
    )
    try:
        requests.post(
            f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
            headers=H,
            json={'body': f'⏰ *Fila pré-abertura* — aluno aceitou entrar na fila antes do expediente abrir ({start_label}). Será distribuído automaticamente assim que abrir.',
                  'isInternal': True},
            timeout=10,
        )
    except Exception:
        pass
    _mark_handoff_active(conv_id, 'pre_opening_queue',
                         target=preferred or '', ttl_s=12 * 3600, body=msg)
    st['_pre_opening_pending'] = False
    st['waiting_for_client'] = True
    st['inactivity_start'] = time.time()
    st['_last_responded_ts'] = time.time()
    p(f"  [PRE-OPENING] {conv_id[:12]} aluno aceitou - registrado em fila ({orig_reason}) preferred={preferred or '-'}")


def decline_pre_opening(conv_id, question=''):
    """Aluno declinou entrar na fila pre-abertura - segue conversando."""
    name_prefix = _student_first_name_prefix(conv_id)
    msg = PRE_OPENING_DECLINED_MSG.format(name=name_prefix)
    sig = 'pre_opening_declined'
    if not _signature_recently_sent(conv_id, sig, window_s=4 * 3600):
        meta_typing_on()
        send_and_track(conv_id, msg)
        log_to_db(conv_id, question or '', msg, 1.0, sig)
        _register_signature(conv_id, sig, msg)
    st = _conv_states.setdefault(conv_id, _default_conv_state())
    st['_pre_opening_pending'] = False
    st['waiting_for_client'] = False
    p(f"  [PRE-OPENING] {conv_id[:12]} aluno declinou - segue conversando com agente")


_PRE_OPENING_YES_KEYWORDS = (
    'sim', 'ss', 'pode', 'quero', 'aceito', 'beleza', 'blz',
    'aguardo', 'aguardar', 'fila', 'ok', 'okay', 'positivo', 'aham', 'isso', 'claro',
    'vamos', 'sim por favor', 'sim quero', 'pode sim', 'entrar', 'entra',
)
_PRE_OPENING_NO_KEYWORDS = (
    'nao', 'não', 'nope', 'agora nao', 'agora não', 'depois', 'mais tarde',
    'nao obrigado', 'não obrigado', 'nao obrigada', 'não obrigada', 'nao preciso',
    'não preciso', 'prefiro nao', 'prefiro não',
)


def detect_pre_opening_intent(text, button_id=''):
    """Retorna 'yes', 'no' ou '' baseado no texto/botao do aluno."""
    if button_id == 'pre_opening_yes':
        return 'yes'
    if button_id == 'pre_opening_no':
        return 'no'
    if not text:
        return ''
    import unicodedata, re
    t = text.strip().lower()
    t = ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
    # normaliza pontuacao: tira virgulas, exclamacoes, pontos
    t_clean = re.sub(r'[,!?.;:]', ' ', t).strip()
    tokens = set(t_clean.split())
    # exact match curto
    if t_clean in _PRE_OPENING_NO_KEYWORDS:
        return 'no'
    if t_clean in _PRE_OPENING_YES_KEYWORDS:
        return 'yes'
    # heuristica: NAO prevalece sobre SIM se ambos aparecerem
    for kw in _PRE_OPENING_NO_KEYWORDS:
        if kw in tokens or kw in t_clean:
            return 'no'
    for kw in _PRE_OPENING_YES_KEYWORDS:
        if kw in tokens:
            return 'yes'
    return ''


def send_after_hours_response(conv_id, *, allow_continue=False, reason='escalate_after_hours', question=''):
    """Envia AFTER_HOURS_FIRST_MSG ou AFTER_HOURS_INSIST_MSG conforme tier.
    Retorna 'first' ou 'insist'.
    Se allow_continue=True e tier == 'first', NÃO marca waiting_for_client
    (deixa o pipeline IA seguir respondendo a dúvida em paralelo).

    Se o aluno citar um consultor pelo nome ('queria falar com a Mariana'),
    o nome é gravado em preferred_attendant para honrar quando voltar ao horário.

    Se estamos na janela pre-abertura (faltam <= PRE_OPENING_MARGIN_MIN para abrir),
    OFERECE entrar na fila ao inves de mandar mensagem padrao after_hours.
    """
    # Janela pre-abertura: oferece fila antecipada
    # Logging instrumentado para rastrear quando esse caminho NaO eh tomado
    # mesmo proximo do horario (ex: caso Jaqueline as 08:49 que recebeu
    # mensagem padrao em vez da oferta de fila).
    try:
        mins_left = _minutes_until_business_hours_start()
        in_pre = _in_pre_opening_window()
        within = is_within_business_hours()
        p(f"  [AFTER-HOURS] entrada reason={reason} within={within} mins_to_open={mins_left} in_pre_opening={in_pre}")
    except Exception:
        in_pre = False
    if in_pre:
        send_pre_opening_offer(conv_id, reason=reason, question=question)
        return 'pre_opening'

    tier = _after_hours_escalation_tier(conv_id)
    name_prefix = _student_first_name_prefix(conv_id)
    meta_typing_on()
    retorno = next_human_available_label()
    preferred = detect_preferred_attendant(question or '')
    if preferred:
        p(f"  [AFTER-HOURS] Aluno citou consultor: {preferred} -> registrar preferred_attendant")
    # Lista de razoes que indicam intencao de escalada — para essas, SEMPRE
    # registrar pending_escalation (mesmo que a msg seja deduplicada), pois
    # o aluno DEVE ser distribuido quando o expediente abrir. Sem isso, o
    # caso da Tauana/Gustavo (clicou "Falar com atendente" apos encerramento
    # fora do horario e o sig de after_hours_first estava no cooldown de 8h
    # — pending_escalation nao era registrado e o aluno ficava esquecido).
    _is_escalation_reason = any(t in (reason or '').lower() for t in (
        'escalate', 'falar com atendente', 'human_unavailable',
        'after_hours_rescue', 'media_only', 'polo', 'retention',
    ))
    if tier == 'first':
        msg = AFTER_HOURS_FIRST_MSG.format(name=name_prefix)
        sig = 'after_hours_first'
        msg_was_sent = False
        if _signature_recently_sent(conv_id, sig, window_s=8 * 3600):
            p(f"  [AFTER-HOURS] dedup: {sig} ja enviado nas ultimas 8h - suprimindo msg (mas pending_escalation segue se for escalada)")
        else:
            send_and_track(conv_id, msg)
            log_to_db(conv_id, question or '', msg, 1.0, sig)
            _register_signature(conv_id, sig, msg)
            msg_was_sent = True
        if msg_was_sent or _is_escalation_reason:
            record_pending_escalation(conv_id, reason, tier='first', retorno_label=retorno,
                                      question=question, preferred_attendant=preferred)
        if not allow_continue:
            st = _conv_states.setdefault(conv_id, _default_conv_state())
            st['waiting_for_client'] = True
            st['inactivity_start'] = time.time()
            st['_last_responded_ts'] = time.time()
    else:
        msg = AFTER_HOURS_INSIST_MSG.format(name=name_prefix, retorno_label=retorno)
        sig = 'after_hours_insist'
        msg_was_sent = False
        if _signature_recently_sent(conv_id, sig, window_s=8 * 3600):
            p(f"  [AFTER-HOURS] dedup: {sig} ja enviado nas ultimas 8h - suprimindo msg (mas pending_escalation segue se for escalada)")
        else:
            send_and_track(conv_id, msg)
            log_to_db(conv_id, question or '', msg, 1.0, sig)
            _register_signature(conv_id, sig, msg)
            _mark_handoff_active(conv_id, 'after_hours_insist',
                                 target=preferred or '', ttl_s=14 * 3600, body=msg)
            msg_was_sent = True
        if msg_was_sent or _is_escalation_reason:
            record_pending_escalation(conv_id, reason, tier='insist', retorno_label=retorno,
                                      question=question, preferred_attendant=preferred)
        st = _conv_states.setdefault(conv_id, _default_conv_state())
        st['waiting_for_client'] = True
        st['inactivity_start'] = time.time()
        st['_last_responded_ts'] = time.time()
    return tier


def send_media_only_response(conv_id, media_type='áudio', question_label=None):
    """Resposta padrão para mídia sem texto (áudio puro, imagem sem caption etc).
    - Dentro do horário: avisa que IA não processa mídia e *transfere para humano agora*.
    - Fora do horário: registra na fila Cockpit (pending_escalation) com retorno amanhã.
    Em ambos casos oferece ao aluno seguir por texto pra resposta imediata.
    """
    nome = _student_first_name_prefix(conv_id)
    within = is_within_business_hours()

    if media_type == 'áudio':
        recebi = 'seu áudio 🎙️'
        artigo = 'do seu áudio'
    elif media_type == 'imagem':
        recebi = 'sua imagem 📷'
        artigo = 'da imagem'
    elif media_type == 'vídeo':
        recebi = 'seu vídeo 🎬'
        artigo = 'do vídeo'
    else:
        recebi = f'sua {media_type} 📎'
        artigo = f'do(a) {media_type}'

    if within:
        msg = (
            f"Oii{nome}! Recebi {recebi}\n\n"
            f"Como sou um assistente virtual de IA, *não consigo verificar com total certeza* o conteúdo {artigo}.\n\n"
            f"Vou te *transferir agora* para um(a) consultor(a) humano(a) que vai te ouvir e te ajudar 🙌\n\n"
            f"Se preferir, também pode me enviar *por texto* — aí eu já adianto a resposta!"
        )
        meta_typing_on()
        send_and_track(conv_id, msg)
        log_to_db(conv_id, question_label or f'[{media_type}]', msg, 1.0, f'media_only_{media_type}')
        try:
            distribute_to_attendant(conv_id, reason=f'Mídia ({media_type}) - IA não processa conteúdo')
        except Exception as _e_d:
            p(f"  [MEDIA-ONLY] falha distribuição: {_e_d}")
    else:
        retorno = next_human_available_label()
        msg = (
            f"Oii{nome}! Recebi {recebi}\n\n"
            f"Como sou um assistente virtual de IA, *não consigo verificar com total certeza* o conteúdo {artigo}.\n\n"
            f"Vou registrar aqui para que nosso *time humano* te retorne *{retorno}*.\n\n"
            f"Mas se preferir, me envie *por texto* agora mesmo o que precisa que eu já te ajudo na hora! 😊"
        )
        meta_typing_on()
        send_and_track(conv_id, msg)
        log_to_db(conv_id, question_label or f'[{media_type}]', msg, 1.0, f'media_only_{media_type}')
        try:
            record_pending_escalation(conv_id, reason=f'media_only_{media_type}',
                                      tier='first', retorno_label=retorno,
                                      question=question_label or f'[{media_type} sem texto]')
        except Exception as _e_pe:
            p(f"  [MEDIA-ONLY] falha pending_escalation: {_e_pe}")

    st = _conv_states.setdefault(conv_id, _default_conv_state())
    st['waiting_for_client'] = True
    st['inactivity_start'] = time.time()
    st['_last_responded_ts'] = time.time()


def record_pending_escalation(conv_id, reason, tier='insist', retorno_label=None, question='',
                              preferred_attendant=None):
    """Registra na fila Cockpit alunos que precisam de retorno humano fora do horário.

    preferred_attendant: nome do consultor prometido ao aluno (ex: 'Wesley').
    Quando o aluno voltar dentro do horário, o agente honra essa promessa.
    """
    try:
        st = _conv_states.get(conv_id) or {}
        phone = (_current_phone or st.get('phone') or '').strip()
        prof = st.get('student_profile') or {}
        name = (prof.get('name') or '').strip()
        if not retorno_label:
            retorno_label = next_human_available_label()
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_escalation (
                id SERIAL PRIMARY KEY,
                conv_id VARCHAR(80) NOT NULL,
                phone VARCHAR(32),
                student_name VARCHAR(255),
                reason VARCHAR(64) NOT NULL,
                tier VARCHAR(16) NOT NULL DEFAULT 'insist',
                retorno_label VARCHAR(128),
                pergunta TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                resolved_at TIMESTAMP
            )
        """)
        cur.execute("""
            ALTER TABLE pending_escalation
            ADD COLUMN IF NOT EXISTS preferred_attendant VARCHAR(64)
        """)
        cur.execute("""
            UPDATE pending_escalation SET status='superseded', updated_at=NOW()
            WHERE conv_id=%s AND status IN ('pending', 'in_progress')
        """, (conv_id,))
        cur.execute("""
            INSERT INTO pending_escalation
                (conv_id, phone, student_name, reason, tier, retorno_label, pergunta, status, preferred_attendant)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
        """, (conv_id, phone, name, reason, tier, retorno_label, (question or '')[:500],
              (preferred_attendant or None)))
        conn.commit()
        cur.close()
        conn.close()
        tag = f' preferred={preferred_attendant}' if preferred_attendant else ''
        p(f"  [AFTER-HOURS] Fila registrada: conv={conv_id[:12]} reason={reason} tier={tier}{tag}")
    except Exception as e_pe:
        p(f"  [AFTER-HOURS] Erro ao registrar fila: {e_pe}")


def update_pending_escalation_status(conv_id, status, note=''):
    """Atualiza status da fila Cockpit (pending / in_progress / resolved /
    closed_no_engagement / failed).

    REGRA IMPORTANTE: quando o status pedido eh 'resolved' (tipicamente vindo
    de close_conversation_crm em auto-close por inatividade) mas o registro
    atual em pending_escalation ainda esta em 'pending' (nunca foi
    'in_progress', ou seja, nenhum consultor humano realmente atendeu),
    NaO marcamos como 'resolved' — usamos 'closed_no_engagement' para sinalizar
    que o aluno enviou mensagem fora do horario, o agente respondeu apenas com
    a msg padrao, o aluno nao retornou, e a conversa foi encerrada por
    inatividade sem atendimento humano real.

    Isso evita o ruido em "Resolvido" no Cockpit/Auditoria IA quando o agente
    nao realizou atendimento de fato.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        effective_status = status
        if status == 'resolved':
            # Verifica o status atual ANTES de mudar — se ja esta 'pending'
            # significa que nunca foi distribuido (nunca virou 'in_progress'),
            # entao na pratica o atendimento humano nao ocorreu.
            try:
                cur.execute(
                    "SELECT status FROM pending_escalation "
                    "WHERE conv_id = %s ORDER BY id DESC LIMIT 1",
                    (conv_id,),
                )
                row = cur.fetchone()
                current = (row[0] if row else '') or ''
                if current == 'pending':
                    effective_status = 'closed_no_engagement'
                    p(f"  [FILA] {conv_id[:12]} auto-close sem engagement: marcando closed_no_engagement em vez de resolved")
            except Exception as e_check:
                p(f"  [FILA] erro check current status: {e_check}")

        resolved_clause = ", resolved_at = NOW()" if effective_status in ('resolved', 'closed_no_engagement') else ""
        cur.execute(
            f"""UPDATE pending_escalation SET status = %s, updated_at = NOW(){resolved_clause}
                WHERE conv_id = %s AND status IN ('pending', 'in_progress', 'failed')""",
            (effective_status, conv_id),
        )
        # Só envia nota interna no DCZ se o status for 'resolved' propriamente.
        # Em 'closed_no_engagement' nao queremos poluir a conv com mensagem
        # "marcado como Resolvido", pois o atendimento NAO foi feito.
        if note and cur.rowcount and effective_status == 'resolved':
            try:
                requests.post(
                    f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                    headers=H,
                    json={'body': note, 'isInternal': True},
                    timeout=10,
                )
            except Exception:
                pass
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e_up:
        p(f"  [FILA] Erro ao atualizar status: {e_up}")


def _dcz_conv_has_human(conv_id, timeout=8):
    """Consulta DCZ se a conversa ja tem atendente humano (chat ou business).

    Retorna (has_human:bool, attendant_name:str).
    Em caso de erro/timeout retorna (False, '') para nao bloquear distribuicao
    indevidamente — a Camada A ja eh a defesa principal.
    """
    if not conv_id:
        return False, ''
    try:
        r = requests.get(f'{DCZ_MSG}/messaging/conversations/{conv_id}',
                         headers=H, timeout=timeout)
        if r.status_code != 200:
            return False, ''
        cd = r.json() or {}
        att = cd.get('attendant') or {}
        if isinstance(att, dict):
            att_id = att.get('id') or ''
            att_name = att.get('name') or att.get('username') or ''
        else:
            att_id = cd.get('attendantId') or ''
            att_name = ''
        if att_id:
            return True, att_name
        # fallback: lista attendants (formato CRM list)
        atts = cd.get('attendants') or []
        if isinstance(atts, list) and atts:
            first = atts[0]
            if isinstance(first, dict):
                return True, first.get('name') or first.get('username') or ''
            return True, str(first)
        return False, ''
    except Exception:
        return False, ''


def _fetch_pending_for_auto_dispatch(limit=25):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, conv_id, phone, student_name, reason, tier, retorno_label, pergunta, status, created_at
            FROM pending_escalation
            WHERE status = 'pending'
            ORDER BY CASE tier
                       WHEN 'pre_opening' THEN 0  -- alunos que pediram entrar antes - prioridade
                       WHEN 'insist' THEN 1
                       WHEN 'first' THEN 2
                       ELSE 3
                     END, created_at ASC
            LIMIT %s
        """, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e_f:
        p(f"  [FILA] Erro ao listar pendentes: {e_f}")
        return []


def _morning_queue_last_run_date():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("SELECT value FROM agent_config WHERE key = 'morning_queue_last_run'")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return (row[0] or '').strip() if row else ''
    except Exception:
        return ''


def _set_morning_queue_last_run(date_str):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_config (key, value, updated_at)
            VALUES ('morning_queue_last_run', %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (date_str,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def process_pending_escalation_auto_dispatch():
    """Distribui fila noturna no horário comercial.
    - 1ª execução do dia (após abertura): lote maior (insistência primeiro)
    - Depois: retentativas a cada MORNING_DISPATCH_RETRY_COOLDOWN_S com lote menor
    """
    global student_profile, _current_phone, _last_pending_dispatch_ts

    if not AUTO_DISPATCH_MORNING_QUEUE:
        return
    if not is_within_business_hours():
        return

    now = _now_sp()
    today = now.strftime('%Y-%m-%d')
    last_run = _morning_queue_last_run_date()
    is_morning_burst = (last_run != today and now.hour >= BUSINESS_HOURS_WEEKDAY_START)

    if not is_morning_burst:
        if time.time() - _last_pending_dispatch_ts < MORNING_DISPATCH_RETRY_COOLDOWN_S:
            return
        limit = MORNING_DISPATCH_RETRY_BATCH
        label = 'retry'
    else:
        limit = MORNING_DISPATCH_BATCH_SIZE
        label = 'morning_burst'
        p(f"  [FILA] ☀️ Início do expediente — processando fila noturna (até {limit} registros)")

    rows = _fetch_pending_for_auto_dispatch(limit)
    if not rows:
        if is_morning_burst:
            _set_morning_queue_last_run(today)
        _last_pending_dispatch_ts = time.time()
        return

    # === LIMITE POR CONSULTOR (anti-sobrecarga matinal) ===
    # Conta quantos cada consultor recebeu nesta rodada de dispatch. Quando algum
    # consultor atinge PRE_OPENING_BURST_MAX_PER_ATTENDANT, ele e excluido das
    # proximas atribuicoes da rodada (vai aguardar proxima janela do retry).
    assigned_count = {}
    burst_max = PRE_OPENING_BURST_MAX_PER_ATTENDANT
    if not is_morning_burst:
        # No retry normal, limite mais frouxo (consultores ja escoaram a fila inicial)
        burst_max = max(2, PRE_OPENING_BURST_MAX_PER_ATTENDANT)

    dispatched = 0
    failed = 0
    deferred = 0
    skipped_human = 0
    for row in rows:
        conv_id = row.get('conv_id') or ''
        if not conv_id:
            continue
        phone = (row.get('phone') or '').strip()
        name = (row.get('student_name') or '').strip()
        _current_phone = phone
        first = name.split()[0] if name else ''
        student_profile = {
            'name': name,
            'first_name': first,
            'phone': phone,
        }
        pergunta = (row.get('pergunta') or '')[:200]
        tier_row = row.get('tier') or ''
        reason_tag = 'Fila pré-abertura' if tier_row == 'pre_opening' else 'Retorno automático fila noturna'
        reason = (f"{reason_tag} — {row.get('reason', '')}"
                  + (f' | {pergunta}' if pergunta else ''))

        # CORRECAO (2026-05-25): defesa em profundidade contra o bug
        # "agente expulsa consultor de retencao". Antes de distribuir, consulta
        # DCZ se a conversa ja tem atendente humano. Se tem, marca a entrada
        # como in_progress (consultor original assume) e pula. Combina com
        # Camada A no supervisor — esta eh a Camada B.
        try:
            has_h, att_name = _dcz_conv_has_human(conv_id)
        except Exception:
            has_h, att_name = False, ''
        if has_h:
            p(f"  [FILA] {conv_id[:12]} ja tem humano ({att_name}) - PULAR distribuicao")
            try:
                update_pending_escalation_status(
                    conv_id, 'in_progress',
                    note=f"☀️ *Fila noturna* — humano ({att_name or 'atendente'}) ja presente, distribuicao automatica abortada.",
                )
            except Exception:
                pass
            skipped_human += 1
            continue

        overloaded = {n for n, c in assigned_count.items() if c >= burst_max}
        p(f"  [FILA] Auto-distribuir conv={conv_id[:12]} tier={tier_row} ({label}) overload={len(overloaded)}")
        ok = distribute_to_attendant(conv_id, reason=reason, exclude_attendants=overloaded)
        if ok:
            # ler quem foi escolhido pra contar
            try:
                chosen = (_conv_states.get(conv_id, {}) or {}).get('_last_distributed_to', '') or ''
                key = chosen.strip().lower()
                if key:
                    assigned_count[key] = assigned_count.get(key, 0) + 1
                    if assigned_count[key] >= burst_max:
                        p(f"  [FILA] {chosen} atingiu limite burst ({burst_max}) - excluido das proximas")
            except Exception:
                pass
            update_pending_escalation_status(
                conv_id, 'in_progress',
                note=f'☀️ *Fila noturna* — distribuído automaticamente às {now.strftime("%H:%M")}.',
            )
            dispatched += 1
        else:
            # Se todos atingiram limite, fica pendente pra proxima janela
            update_pending_escalation_status(conv_id, 'pending')
            failed += 1
            if overloaded:
                deferred += 1
            p(f"  [FILA] Mantido pendente (sem consultor / limite / falha)")
    if deferred:
        p(f"  [FILA] {deferred} conv(s) adiadas por limite de burst - vao na proxima janela")

    p(f"  [FILA] Lote {label}: distribuídos={dispatched} ainda_pendentes={failed} pulados_humano={skipped_human}")
    if is_morning_burst:
        _set_morning_queue_last_run(today)
    _last_pending_dispatch_ts = time.time()


# ===================== AFTER-HOURS RESCUE =====================
_AFTER_HOURS_RESCUE_RECENT = {}  # conv_id -> last_rescue_ts (memória)
AFTER_HOURS_RESCUE_AGE_MIN = 10
AFTER_HOURS_RESCUE_COOLDOWN_S = 6 * 3600  # não reenvia para mesma conv em 6h


def _was_after_hours_msg_recently_sent(msgs):
    """Detecta se alguma das mensagens recentes do bot já é AFTER_HOURS (evita duplicar)."""
    fps = ('nosso time de atendimento humano está fora do horário',
           'fora do nosso horário de atendimento',
           'nosso time retorna o atendimento')
    for m in msgs[:6]:
        if m.get('received', True):
            continue
        body = (m.get('body', '') or '').lower()
        if any(fp in body for fp in fps):
            return True
    return False


# ===================== HELPER: fetch conversas ativas (3 status fundidos) =====================
# (2026-05-26) Fix de cegueira: o endpoint DCZ /messaging/conversations com
# status=open NAO retorna conversas em 'unstarted' nem 'opened'. Resultado:
# pós-disparo em massa, 10+ conversas ficavam invisiveis pro agente (e
# pos-clicks de bot DCZ que mudam status p/ 'opened' tambem). Esta helper
# unifica os 3 estados ativos e deduplica por id.
#
# (2026-05-27) BUG CRITICO: a primeira versao usava timeout=60 nos 3 GETs.
# Quando o DCZ ficava lento, agente travava ate 180s no ciclo e o heartbeat
# atrasava >120s — cockpit mostrava "processo morto". Reduzido para 15s
# por GET (max 45s no pior caso). Adicionado _heartbeat entre os GETs
# para garantir que o cockpit nunca veja o agente como morto durante esta
# coleta.
def _fetch_active_conversations(limit_per_status=300, timeout=15):
    """Busca conversas ATIVAS (open + unstarted + opened) e funde sem duplicar.
    Retorna lista de dicts (vazia em caso de erro total). Loga erros parciais
    mas nao falha se 1 dos 3 GETs falhar.
    """
    seen = set()
    out = []
    for _status in ('open', 'unstarted', 'opened'):
        # Heartbeat entre cada GET — impede que o cockpit veja o agente
        # como morto se o DCZ estiver lento. Heartbeat e DB local rapido.
        try:
            _heartbeat('online', f'fetch_active status={_status}')
        except Exception:
            pass
        try:
            _r = requests.get(
                f'{DCZ_MSG}/messaging/conversations', headers=H,
                params={'limit': limit_per_status, 'status': _status},
                timeout=timeout,
            )
            if _r.status_code != 200:
                continue
            _d = _r.json()
            _convs = _d.get('data', _d) if isinstance(_d, dict) else _d
            if not isinstance(_convs, list):
                continue
            for _c in _convs:
                _cid = _c.get('id', '')
                if not _cid or _cid in seen:
                    continue
                seen.add(_cid)
                out.append(_c)
        except Exception as _e:
            p(f"  [FETCH-ACTIVE] erro status={_status}: {_e}")
            continue
    return out


def process_after_hours_rescue():
    """Fora do expediente, garante resposta ao aluno mesmo quando há atendente
    'fantasma' atribuído à conversa (humano off, não vai responder).

    - Só roda fora do expediente.
    - Pega conversas open com lastReceived > lastSended há > AFTER_HOURS_RESCUE_AGE_MIN min.
    - Inclui conversas com attendant (porque a noite ninguém responde).
    - Envia AFTER_HOURS_FIRST_MSG (uma vez, com cooldown de 6h por conv).
    """
    global student_profile, _current_phone

    if is_within_business_hours():
        return
    try:
        convs = _fetch_active_conversations(limit_per_status=200, timeout=30)
    except Exception as e:
        p(f"  [AH-RESCUE] erro lista: {e}")
        return

    if not convs:
        return

    now_ts = time.time()
    rescued = 0
    for c in convs:
        try:
            cid = c.get('id', '')
            if not cid:
                continue
            inst = c.get('instance', {}) or {}
            iid = inst.get('id', '') if isinstance(inst, dict) else str(inst)
            if iid != INSTANCE_ACADEMICO_ID:
                continue
            statuses = c.get('statuses', []) or []
            if 'finished' in statuses:
                continue
            recv = c.get('lastReceivedMessageDate', '') or ''
            sent = c.get('lastSendedMessageDate', '') or ''
            if not recv:
                continue
            # (2026-05-27) Caso Jucelia: balao DCZ automatico tem ts apos a
            # msg do aluno por ~4s. Helper distingue balao vs resposta humana.
            if _should_skip_due_to_sent_after_recv(cid, recv, sent):
                continue
            try:
                from datetime import datetime as _dt
                dt_recv = _dt.fromisoformat(str(recv).replace('Z', '+00:00'))
                age_min = (time.time() - dt_recv.timestamp()) / 60
            except Exception:
                continue
            if age_min < AFTER_HOURS_RESCUE_AGE_MIN or age_min > 24 * 60:
                continue
            last_done = _AFTER_HOURS_RESCUE_RECENT.get(cid, 0)
            if last_done and (now_ts - last_done) < AFTER_HOURS_RESCUE_COOLDOWN_S:
                continue
            ct = c.get('contact', {}) or {}
            phone = (ct.get('phoneNumber', '') or ct.get('contactId', '') or '').replace('+', '').replace(' ', '')
            if phone.startswith('55') and len(phone) > 11:
                phone = phone[2:]
            name = (ct.get('name', '') or '').strip()
            first = name.split()[0] if name else ''
            try:
                msgs = get_conversation_messages_api(cid, limit=8)
            except Exception:
                msgs = []
            if _was_after_hours_msg_recently_sent(msgs):
                _AFTER_HOURS_RESCUE_RECENT[cid] = now_ts
                continue

            _current_phone = phone
            student_profile = {'name': name, 'first_name': first, 'phone': phone}
            _conv_states.setdefault(cid, _default_conv_state())['phone'] = phone
            if name:
                _conv_states[cid]['student_profile'] = {'name': name, 'first_name': first}

            try:
                send_after_hours_response(cid, allow_continue=False,
                                          reason='after_hours_rescue',
                                          question='[varredura noturna]')
                _AFTER_HOURS_RESCUE_RECENT[cid] = now_ts
                rescued += 1
                p(f"  [AH-RESCUE] Resposta enviada conv={cid[:12]} ...{phone[-4:]} ({age_min:.0f}min)")
            except Exception as e_sah:
                p(f"  [AH-RESCUE] falha envio {cid[:12]}: {e_sah}")
        except Exception as e_one:
            p(f"  [AH-RESCUE] erro conv {c.get('id','?')[:12]}: {e_one}")

    if rescued:
        p(f"  [AH-RESCUE] Total atendidos: {rescued}")


# ===================== IN-HOURS RESCUE (orfas dentro do horario) =====================
_IN_HOURS_RESCUE_RECENT = {}  # conv_id -> last_rescue_ts
IN_HOURS_RESCUE_AGE_MIN = 5
IN_HOURS_RESCUE_MAX_AGE_MIN = 6 * 60  # ignora alem disso (provavelmente foi resolvido manualmente)
IN_HOURS_RESCUE_COOLDOWN_S = 30 * 60  # nao re-resgata mesma conv em 30min (sucesso)
# (2026-05-27) Para casos de falha (sem consultor, sem lead, transfer falhou),
# usar cooldown curto para que o proximo ciclo tente novamente em minutos.
_IN_HOURS_RESCUE_RETRY_S = 2 * 60  # retry em 2 min apos falha temporaria


def _ensure_lead_for_rescue(phone, name=''):
    """Garante lead + business no CRM antes de atribuir consultor durante resgate.
    Retorna (lead_id, business_id, created_now).
    Se nao conseguir criar lead, retorna ('', '', False) e o caller deve abortar
    a atribuicao para evitar conversa orfa no CRM.
    """
    lead_id = ''
    business_id = ''
    if phone:
        try:
            prof = identify_student(phone)
            if prof and prof.get('lead_id'):
                return prof['lead_id'], prof.get('business_id') or '', False
        except Exception as e:
            p(f"    [RESCUE-LEAD] identify_student erro: {e}")
    try:
        new_lead_id, new_biz_id = create_lead_and_business(phone, name or '')
        if new_lead_id:
            p(f"    [RESCUE-LEAD] criado lead={new_lead_id} business={new_biz_id or '-'}")
            return new_lead_id, new_biz_id or '', True
    except Exception as e:
        p(f"    [RESCUE-LEAD] create_lead_and_business erro: {e}")
    return '', '', False


def process_in_hours_rescue():
    """Dentro do horario, resgata conversas orfas:
    - cliente mandou msg sem resposta ha >= IN_HOURS_RESCUE_AGE_MIN min
    - sem atendentes atribuidos
    - dentro do horario comercial
    Envia msg humanizada de desculpa e atribui ao consultor com menor fila.
    """
    global student_profile, _current_phone
    if not is_within_business_hours():
        return
    try:
        convs = _fetch_active_conversations(limit_per_status=300, timeout=30)
    except Exception as e:
        p(f"  [IN-HOURS-RESCUE] erro lista: {e}")
        return

    if not convs:
        return

    # (2026-05-25) Limite por execucao para nao travar o loop principal.
    # Caso disparo: 300+ orfas faziam o rescue rodar por 10+ min em uma
    # unica chamada, bloqueando o handle_message dos demais alunos. Com
    # 40/execucao (~2-3min de API), o loop principal volta a respirar
    # entre rescues consecutivos.
    _RESCUE_MAX_PER_EXEC = 40
    now_ts = time.time()
    rescued = 0
    _rescue_iter = 0
    for c in convs:
        _rescue_iter += 1
        # (2026-05-27) Heartbeat a cada 20 convs para nao congelar o Cockpit
        # durante processameto de grandes lotes (ex: pos-disparo com 1000 convs).
        if _rescue_iter % 20 == 0:
            try:
                _heartbeat('online', f'in_hours_rescue iter={_rescue_iter} rescued={rescued}')
            except Exception:
                pass
        if rescued >= _RESCUE_MAX_PER_EXEC:
            p(f"  [IN-HOURS-RESCUE] limite por execucao atingido ({_RESCUE_MAX_PER_EXEC}) - continua no proximo ciclo")
            break
        try:
            cid = c.get('id', '')
            if not cid:
                continue
            inst = c.get('instance', {}) or {}
            iid = inst.get('id', '') if isinstance(inst, dict) else str(inst)
            if iid != INSTANCE_ACADEMICO_ID:
                continue
            statuses = c.get('statuses', []) or []
            if 'finished' in statuses:
                continue
            atts = c.get('attendants', []) or []
            if atts:
                continue
            recv = c.get('lastReceivedMessageDate', '') or ''
            sent = c.get('lastSendedMessageDate', '') or ''
            if not recv:
                continue
            # (2026-05-27) Caso Jucelia: helper distingue balao DCZ vs resposta humana
            if _should_skip_due_to_sent_after_recv(cid, recv, sent):
                continue
            try:
                from datetime import datetime as _dt
                dt_recv = _dt.fromisoformat(str(recv).replace('Z', '+00:00'))
                age_min = (time.time() - dt_recv.timestamp()) / 60
            except Exception:
                continue
            if age_min < IN_HOURS_RESCUE_AGE_MIN or age_min > IN_HOURS_RESCUE_MAX_AGE_MIN:
                continue
            last_done = _IN_HOURS_RESCUE_RECENT.get(cid, 0)
            if last_done and (now_ts - last_done) < IN_HOURS_RESCUE_COOLDOWN_S:
                continue

            ct = c.get('contact', {}) or {}
            phone = (ct.get('phoneNumber', '') or ct.get('contactId', '') or '').replace('+', '').replace(' ', '')
            if phone.startswith('55') and len(phone) > 11:
                phone = phone[2:]
            name = (ct.get('name', '') or '').strip()
            first = name.split()[0] if name else ''

            # === PULA RESGATE SE ULTIMA MSG DO ALUNO FOR DESPEDIDA/AGRADECIMENTO ===
            # Caso reportado: aluno mandou so "Obrigado" apos atendimento ja
            # concluido — nao precisa de novo atendente, so fecha o ciclo.
            _conv_msgs = []
            try:
                _conv_msgs = get_conversation_messages_api(cid, limit=15) or []
                _last_aluno_body = ''
                for _m in _conv_msgs:
                    if _m.get('received', False):
                        _last_aluno_body = (_m.get('body') or _m.get('text') or '').strip()
                        break
                # (2026-05-26) Adicionado _is_resolution_confirmation —
                # casos reportados: "Vou avaliar Muito grato", "Consegui
                # entender", "Deu certo" ficavam aguardando atendente.
                _is_fw = _is_farewell_message(_last_aluno_body) if _last_aluno_body else False
                _is_rc = _is_resolution_confirmation(_last_aluno_body) if _last_aluno_body else False
                # (2026-05-27) GUARD: sauda\u00e7\u00e3o pura nunca encerra.
                if _last_aluno_body and _is_pure_greeting(_last_aluno_body):
                    _is_fw = False; _is_rc = False
                if _last_aluno_body and (_is_fw or _is_rc):
                    _kind_r = 'despedida' if _is_fw else 'confirmacao_resolucao'
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} ...{phone[-4:]} ultima msg do aluno = {_kind_r} ('{_last_aluno_body[:40]}') — pulando resgate e fechando")
                    try:
                        close_conversation_crm(cid, phone=phone)
                    except Exception as e_cc:
                        p(f"  [IN-HOURS-RESCUE] erro close: {e_cc}")
                    try:
                        update_pending_escalation_status(
                            cid, 'closed_no_engagement',
                            note=f'Aluno encerrou com {_kind_r} — sem necessidade de atendente: "{_last_aluno_body[:80]}"',
                        )
                    except Exception:
                        pass
                    _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                    continue

                # (2026-05-25) Msg de OUTRA AUTOMACAO externa (URA, autoresponder,
                # empresa parceira, bot DCZ) chegou como input. NUNCA responder
                # — apenas fechar a conv silenciosamente. Caso: 'claupiercings
                # agradece seu contato. Como podemos ajudar?'
                # IMPORTANTE: ignora se a ultima msg recebida for template HSM
                # (disparo), nesses casos a conv eh legitima.
                _last_msg_obj = None
                try:
                    for _mm in _conv_msgs or []:
                        if _mm.get('received', False):
                            _last_msg_obj = _mm
                            break
                except Exception:
                    pass
                if (_last_aluno_body and _is_external_bot_input(_last_aluno_body)
                        and not (_last_msg_obj and _is_template_message(_last_msg_obj))):
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} ...{phone[-4:]} msg eh de bot externo ('{_last_aluno_body[:40]}') — fechando sem responder")
                    try:
                        close_conversation_crm(cid, phone=phone)
                    except Exception as e_cc:
                        p(f"  [IN-HOURS-RESCUE] erro close external_bot: {e_cc}")
                    try:
                        update_pending_escalation_status(
                            cid, 'closed_external_bot',
                            note=f'Mensagem identificada como bot/URA externo: "{_last_aluno_body[:80]}"',
                        )
                    except Exception:
                        pass
                    _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                    continue

                # SE ALUNO MANIFESTOU CANCELAMENTO/TRANCAMENTO (retencao):
                # nao distribui para qualquer atendente — passa para Wesley.
                # Caso reportado: "Mas eu cancelei minha matricula" -> foi para
                # Marilia em vez de Wesley.
                if _last_aluno_body and is_retention_intent(_last_aluno_body):
                    # (2026-06-25) TESTE: telefone de teste -> só tag RET-IA, silencio
                    if _use_ret_ia_automation(phone):
                        _trigger_retention_tag_only(cid, None, _last_aluno_body, phone=phone)
                        p(f"  [IN-HOURS-RESCUE] [RET-IA] tag acionada, sem distribuir/mensagem")
                        _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                        continue
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} ...{phone[-4:]} retencao detectada ('{_last_aluno_body[:40]}') — distribuindo p/ time de Retenção")
                    try:
                        # (2026-06-08) trigger_retention escolhe Wesley/Danúbia por
                        # disponibilidade e marca o handoff. So manda a msg/distribui
                        # se houver alguem ativo; senao segura e re-tenta no proximo ciclo.
                        _alvo = trigger_retention(cid, None, _last_aluno_body, phone=phone)
                        if _alvo:
                            _greet = (f", *{first}*" if first else '')
                            _hum = (f"Oi{_greet}! Desculpa a demora 🙏 Entendi sua situação. "
                                    f"Vou te conectar com o nosso *time de Retenção*, "
                                    f"que vai te ajudar com isso, tá? Um momento!")
                            send_and_track(cid, _hum)
                            log_to_db(cid, _last_aluno_body, _hum, 1.0, 'retention')
                            _register_signature(cid, 'retention', _hum)
                            update_pending_escalation_status(
                                cid, 'distributed_retention',
                                note=f'In-hours rescue: retencao -> {_alvo} (msg: {_last_aluno_body[:60]})',
                            )
                            _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                            continue
                        else:
                            p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} retencao SEM membro ativo — segura p/ proximo ciclo")
                    except Exception as e_ret:
                        p(f"  [IN-HOURS-RESCUE] erro retencao: {e_ret}")
                    _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                    continue

                # PULA RESGATE SE ALUNO CONFIRMOU PAGAMENTO ja realizado.
                # Caso: aluno respondeu disparo de boleto dizendo "ja paguei".
                # Nao precisa distribuir — agente envia "tudo bem" e fecha.
                if _last_aluno_body and _is_payment_confirmed_message(_last_aluno_body):
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} ...{phone[-4:]} aluno confirmou pagamento ('{_last_aluno_body[:40]}') — respondendo e fechando sem distribuir")
                    _greet = (f", *{first}*" if first else '')
                    _ack = f"Tudo bem{_greet}! 😊 Obrigado pela confirmação. Qualquer coisa, é só me chamar. Até mais!"
                    try:
                        send_and_track(cid, _ack)
                    except Exception as e_ack:
                        p(f"  [IN-HOURS-RESCUE] erro ack pagamento: {e_ack}")
                    try:
                        log_to_db(cid, _last_aluno_body, _ack, 1.0, 'payment_confirmed')
                    except Exception:
                        pass
                    try:
                        time.sleep(1.0)
                        close_conversation_crm(cid, phone=phone)
                    except Exception as e_cc:
                        p(f"  [IN-HOURS-RESCUE] erro close pos-pagamento: {e_cc}")
                    try:
                        update_pending_escalation_status(
                            cid, 'closed_payment_confirmed',
                            note='Aluno confirmou pagamento — agente respondeu e fechou sem distribuir',
                        )
                    except Exception:
                        pass
                    _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                    continue

                # PAGAMENTO FORA DO VENCIMENTO (2026-05-27) -> informar + fechar
                # Caso Odirlei/Wanny: aluno disse que vai pagar dia X (apos 25/05).
                if _last_aluno_body and _is_payment_later(_last_aluno_body):
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} ...{phone[-4:]} pagamento tardio ('{_last_aluno_body[:40]}') — informando valor maior + fechando")
                    _greet = (f", *{first}*" if first else '')
                    _info = (
                        f"Tudo bem{_greet}! 👍\n\n"
                        f"Você pode efetuar o pagamento sim, sem problema 😊\n\n"
                        f"Só fique atento(a): como o vencimento da mensalidade foi *25/05*, "
                        f"pagando depois dessa data o valor fica um pouco *maior*, porque os "
                        f"descontos vigentes da parcela são reduzidos após o vencimento.\n\n"
                        f"Quando puder, é só efetuar o pagamento normalmente pela 2ª via. "
                        f"Qualquer dúvida, estou por aqui!"
                    )
                    try:
                        send_and_track(cid, _info)
                    except Exception as e_pl:
                        p(f"  [IN-HOURS-RESCUE] erro info pagto-tardio: {e_pl}")
                    try:
                        log_to_db(cid, _last_aluno_body, _info, 1.0, 'payment_later')
                    except Exception:
                        pass
                    try:
                        time.sleep(1.0)
                        close_conversation_crm(cid, phone=phone)
                    except Exception as e_cc:
                        p(f"  [IN-HOURS-RESCUE] erro close pagto-tardio: {e_cc}")
                    try:
                        update_pending_escalation_status(
                            cid, 'closed_payment_later',
                            note='Aluno informou pagamento fora do vencimento — agente informou valor maior e fechou',
                        )
                    except Exception:
                        pass
                    _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                    continue

                # ALUNO OCUPADO / RETORNA DEPOIS (2026-05-27) -> ack + fechar
                # Caso Karen: 'Ola no momento estou ocupada, assim que puder retorno.'
                if _last_aluno_body and _is_busy_later(_last_aluno_body):
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} ...{phone[-4:]} aluno ocupado ('{_last_aluno_body[:40]}') — ack curto + fechando")
                    _greet = (f", *{first}*" if first else '')
                    _ack = (
                        f"Tudo bem{_greet}! 😊 Quando estiver mais tranquilo(a), "
                        f"estaremos à disposição. Até mais!"
                    )
                    try:
                        send_and_track(cid, _ack)
                    except Exception as e_bl:
                        p(f"  [IN-HOURS-RESCUE] erro ack ocupado: {e_bl}")
                    try:
                        log_to_db(cid, _last_aluno_body, _ack, 1.0, 'busy_later')
                    except Exception:
                        pass
                    try:
                        time.sleep(1.0)
                        close_conversation_crm(cid, phone=phone)
                    except Exception as e_cc:
                        p(f"  [IN-HOURS-RESCUE] erro close ocupado: {e_cc}")
                    try:
                        update_pending_escalation_status(
                            cid, 'closed_busy_later',
                            note='Aluno informou estar ocupado — agente respondeu e fechou',
                        )
                    except Exception:
                        pass
                    _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                    continue
            except Exception as e_far:
                p(f"  [IN-HOURS-RESCUE] erro check farewell {cid[:12]}: {e_far}")

            # === FIX-1: limpa handoff_active STALE se atendente prometido ja saiu ===
            # Caso Debora: handoff(dispatch, Debora) ficou no banco depois que
            # ela finalizou. Aluno volta 2h depois e bot promete Debora errado.
            try:
                cleared, leaver = _had_attendant_left_after_handoff(cid, _conv_msgs)
                if cleared:
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} handoff_active stale removido (atendente {leaver} saiu)")
            except Exception as e_clr:
                p(f"  [IN-HOURS-RESCUE] erro clear stale: {e_clr}")

            # === FIX-2: respeita handoff_active(dispatch) ja ativo (sticky) ===
            # Se ainda ha promessa valida para X e X esta ativo, tenta re-atribuir
            # a X em vez de distribuir para outro consultor (evita prometer 2 nomes diferentes).
            try:
                ho_motivo_pre, ho_target_pre = _is_handoff_active(cid)
                if ho_motivo_pre == 'dispatch' and ho_target_pre:
                    target_first_pre = ho_target_pre.split()[0].lower()
                    if is_attendant_active_now(target_first_pre):
                        # sticky: re-atribui ao mesmo prometido
                        target_full = ho_target_pre
                        p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} handoff(dispatch) ativo p/ {target_full} - sticky re-atribuicao")
                        try:
                            lead_id, _biz, _created = _ensure_lead_for_rescue(phone, name)
                            if lead_id:
                                _dcz_transfer_business(phone, target_full, lead_id=lead_id)
                                _dcz_transfer_lead(lead_id, target_full)
                            _dcz_transfer_chat(cid, target_full)
                        except Exception as e_st:
                            p(f"  [IN-HOURS-RESCUE] erro sticky transfer: {e_st}")
                        # NAO envia nova msg de transferencia ao aluno — handoff_active ja teve
                        # nudge entregue anteriormente. Apenas registra no Cockpit:
                        try:
                            update_pending_escalation_status(
                                cid, 'in_progress',
                                note=f'Sticky re-atribuicao a {target_full} (handoff ativo)',
                            )
                        except Exception:
                            pass
                        _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                        continue
                    else:
                        # atendente prometido nao esta ativo agora — limpa handoff stale
                        # e segue fluxo normal de distribuicao.
                        p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} handoff prometia {ho_target_pre} mas atendente off - limpando")
                        try:
                            _clear_handoff_active(cid, reason='attendant_offline')
                        except Exception:
                            pass
            except Exception as e_ho:
                p(f"  [IN-HOURS-RESCUE] erro check handoff: {e_ho}")

            # (2026-05-26) PROTECAO RETENCAO: se ha handoff(retention) ativo
            # para essa conv, NAO redistribuir para consultor normal. Wesley
            # cuida e o resgate so existe para conversas verdadeiramente orfas.
            try:
                ho_chk_m, ho_chk_t = _is_handoff_active(cid)
                if ho_chk_m in _HUMAN_HANDOFF_MOTIVOS and ho_chk_t:
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} handoff({ho_chk_m}) ativo p/ {ho_chk_t} — NAO redistribui")
                    # (2026-05-27) Cooldown CURTO: handoff pode expirar em minutos,
                    # entao verificamos novamente em 2min.
                    _IN_HOURS_RESCUE_RECENT[cid] = now_ts - IN_HOURS_RESCUE_COOLDOWN_S + _IN_HOURS_RESCUE_RETRY_S
                    continue
            except Exception:
                pass

            # (2026-05-28) PROTECAO RETENCAO 2: cobre /retencao do CONSULTOR
            # no DCZ (sem handoff_active nosso). Detecta via tag CRM, historico
            # ou trigger_retention nosso anterior. Caso reportado: consultor
            # usa /retencao -> conv ia pra Wesley -> rescue redistribuia para
            # outro consultor (ou voltava ao consultor original).
            try:
                _lead_chk, _, _ = _ensure_lead_for_rescue(phone, name)
            except Exception:
                _lead_chk = None
            try:
                if _is_in_retention(cid, lead_id=_lead_chk, msgs=_conv_msgs):
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} em RETENCAO — NAO redistribui (mantem time de Retenção)")
                    # Garante que negocio/lead/chat seguem com o membro de retencao
                    # (sticky: trigger_retention reusa o alvo ja atribuido).
                    try:
                        _alvo_stk = trigger_retention(cid, _lead_chk, _last_aluno_body or '[retencao em andamento]', phone=phone)
                        update_pending_escalation_status(
                            cid, 'distributed_retention',
                            note=f'In-hours rescue: conv em retencao mantida com {_alvo_stk or "time de Retenção"} (sticky)',
                        )
                    except Exception as e_rstk:
                        p(f"  [IN-HOURS-RESCUE] erro sticky retencao: {e_rstk}")
                    _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                    continue
            except Exception as e_chk:
                p(f"  [IN-HOURS-RESCUE] erro check retencao: {e_chk}")

            consultant = get_available_consultant()
            if not consultant:
                p(f"  [IN-HOURS-RESCUE] sem consultor disponivel - registrando pending")
                try:
                    _current_phone = phone
                    student_profile = {'name': name, 'first_name': first, 'phone': phone}
                    _conv_states.setdefault(cid, _default_conv_state())['phone'] = phone
                    record_pending_escalation(
                        cid, reason='human_unavailable',
                        tier='pending',
                        retorno_label='assim que houver consultor disponivel',
                        question='[varredura interna - orfa sem consultor]',
                    )
                except Exception as e_pe:
                    p(f"  [IN-HOURS-RESCUE] erro pending: {e_pe}")
                # (2026-05-27) Cooldown CURTO em falha — tenta novamente em 2min
                # quando consultor voltar a estar disponivel.
                _IN_HOURS_RESCUE_RECENT[cid] = now_ts - IN_HOURS_RESCUE_COOLDOWN_S + _IN_HOURS_RESCUE_RETRY_S
                continue

            consultant_name = consultant.get('nome', '')
            consultant_first = consultant_name.split()[0] if consultant_name else ''
            student_first_part = f" {first}" if first else ''

            # FIX (2026-05-25): GARANTIR lead+transferencia ANTES de enviar a apology.
            # Caso reportado: apology era enviada ANTES de _ensure_lead_for_rescue.
            # Se a criacao de lead falhasse, a mensagem chegava ao aluno
            # ("Vou te conectar com X") mas a conv ficava SEM atendente atribuido.
            lead_id, business_id, created_now = _ensure_lead_for_rescue(phone, name)
            if not lead_id:
                p(f"  [IN-HOURS-RESCUE] sem lead p/ ...{phone[-4:]} - aborta SEM enviar msg (evita orfa CRM)")
                # registra pending pra varredura tentar mais tarde
                try:
                    record_pending_escalation(
                        cid, reason='rescue_no_lead',
                        tier='pending',
                        retorno_label='assim que conseguirmos criar seu cadastro',
                        question='[rescue: falha criando lead - tentara novamente]',
                    )
                except Exception:
                    pass
                # (2026-05-27) Cooldown CURTO — tentativa de criar lead pode
                # ser bem-sucedida no proximo ciclo (2 min).
                _IN_HOURS_RESCUE_RECENT[cid] = now_ts - IN_HOURS_RESCUE_COOLDOWN_S + _IN_HOURS_RESCUE_RETRY_S
                continue

            # Transfere ANTES de enviar a apology. Se transferencia falhar,
            # abortamos sem enviar (aluno nao recebera promessa quebrada).
            transfer_ok = False
            try:
                _bok = _dcz_transfer_business(phone, consultant_name, lead_id=lead_id)
                _lok = _dcz_transfer_lead(lead_id, consultant_name)
                _cok = _dcz_transfer_chat(cid, consultant_name)
                transfer_ok = bool(_cok)  # chat eh o mais critico
                _supabase_increment_fila(consultant.get('id', ''), int(consultant.get('fila', 0)))
                if created_now:
                    try:
                        requests.post(
                            f'{DCZ_API}/api/v1/conversations/{cid}/messages',
                            headers=H,
                            json={'body': "ℹ️ Lead criado automaticamente pelo resgate (não existia no CRM antes).", 'isInternal': True},
                            timeout=10,
                        )
                    except Exception:
                        pass
            except Exception as e_t:
                p(f"  [IN-HOURS-RESCUE] erro transferencia: {e_t}")

            if not transfer_ok:
                # Fallback: change-attendant direto (igual ao distribute_to_attendant)
                try:
                    nome_norm = consultant_name.strip().lower()
                    nome_norm = ''.join(c for c in __import__('unicodedata').normalize('NFD', nome_norm)
                                        if __import__('unicodedata').category(c) != 'Mn')
                    att_id = ATTENDANT_MAP.get(nome_norm, '')
                    if att_id:
                        r_dir = requests.post(
                            f'{DCZ_MSG}/messaging/conversations/{cid}/change-attendant',
                            headers=H, json={'attendantId': att_id}, timeout=15,
                        )
                        p(f"  [IN-HOURS-RESCUE] change-attendant fallback (status={r_dir.status_code})")
                        if r_dir.status_code in (200, 201, 204):
                            transfer_ok = True
                except Exception as e_dir:
                    p(f"  [IN-HOURS-RESCUE] change-attendant fallback erro: {e_dir}")

            if not transfer_ok:
                p(f"  [IN-HOURS-RESCUE] ALERTA: transferencia falhou em todas tentativas — NAO envia apology")
                try:
                    record_pending_escalation(
                        cid, reason='rescue_transfer_failed',
                        tier='pending',
                        retorno_label='estamos verificando seu atendimento',
                        question=f'[rescue: lead {lead_id[:12]} criado mas transfer falhou]',
                    )
                except Exception:
                    pass
                # (2026-05-27) Transfer falhou: cooldown CURTO para retry rapido.
                _IN_HOURS_RESCUE_RECENT[cid] = now_ts - IN_HOURS_RESCUE_COOLDOWN_S + _IN_HOURS_RESCUE_RETRY_S
                continue

            # Agora sim envia a apology — transferencia confirmada
            apology = (
                f"Oii{student_first_part}! Desculpa a demora pra te responder 🙏\n\n"
                f"Vou te conectar agora com o(a) *{consultant_first}*, que vai dar continuidade ao seu atendimento. "
                f"Em pouquinho ele(a) assume aqui 😊"
            )
            try:
                meta_typing_on()
                send_and_track(cid, apology)
            except Exception as e_msg:
                p(f"  [IN-HOURS-RESCUE] falha apology: {e_msg}")

            try:
                note = (
                    f"🤝 *Resgate automatico* — aluno ficou sem resposta por {int(age_min)}min sem atendente. "
                    f"Conversa atribuida ao(a) {consultant_first} (menor fila). "
                    f"IA enviou desculpas no chat."
                )
                requests.post(
                    f'{DCZ_API}/api/v1/conversations/{cid}/messages',
                    headers=H, json={'body': note, 'isInternal': True}, timeout=10,
                )
            except Exception:
                pass

            try:
                update_pending_escalation_status(
                    cid, 'resolved',
                    note=f'Resgate automatico - atribuido a {consultant_first}',
                )
            except Exception:
                pass

            _IN_HOURS_RESCUE_RECENT[cid] = now_ts
            rescued += 1
            p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} ...{phone[-4:]} ({int(age_min)}min) -> {consultant_first}")
        except Exception as e_one:
            p(f"  [IN-HOURS-RESCUE] erro conv {c.get('id','?')[:12]}: {e_one}")

    if rescued:
        p(f"  [IN-HOURS-RESCUE] Total resgatadas: {rescued}")


# ===================== QUEUE FAST SWEEP (fila waiting — sem esperar 10min) =====================
_QUEUE_SWEEP_RECENT = {}  # conv_id -> last_action_ts
QUEUE_FAST_SWEEP_MIN_AGE_MIN = 3   # idade minima p/ forcar reprocessamento
QUEUE_FAST_SWEEP_COOLDOWN_S = 90    # nao repetir mesma conv em 90s


_AGENT_FAREWELL_FINGERPRINTS = (
    'obrigado pelo contato', 'estamos sempre por aqui',
    'fico feliz que tenha conseguido resolver',
    'tenha um otimo dia', 'tenha um ótimo dia',
    'tenha um bom dia', 'tenha uma boa tarde', 'tenha uma boa noite',
    'foi um prazer te atender', 'qualquer outra coisa, e so me chamar',
    'qualquer outra coisa, é só me chamar',
    'qualquer coisa, é só me chamar',
    'se precisar de mais alguma coisa',
)


def _last_msg_is_our_farewell(msgs):
    """True se a ultima msg da conversa for um farewell enviado por nos (agente/humano)
    e nao houver msg do aluno depois. Usado p/ re-fechar conversa que ficou aberta
    apos enviar despedida."""
    if not msgs:
        return False
    # msgs[0] eh a mais recente
    last = msgs[0]
    if last.get('received', False):
        return False  # ultima msg eh do aluno -> nao eh farewell pendente
    body = (last.get('body') or last.get('text') or '').strip().lower()
    if not body or len(body) > 400:
        return False
    import unicodedata
    b_norm = ''.join(c for c in unicodedata.normalize('NFD', body)
                     if unicodedata.category(c) != 'Mn')
    return any(fp in b_norm for fp in _AGENT_FAREWELL_FINGERPRINTS)


def _queue_last_aluno_body(msgs):
    """Ultima mensagem recebida do aluno com corpo ou anexo."""
    if not msgs:
        return '', None
    for m in msgs:
        if not m.get('received', False):
            continue
        body = (m.get('body') or m.get('text') or '').strip()
        if not body:
            img = extract_image_from_message(m)
            if img:
                body = img.get('caption', '') or '[imagem enviada pelo aluno]'
            else:
                atts = m.get('attachments') or []
                if atts:
                    body = '[anexo enviado pelo aluno]'
        if body:
            return body, m
    return '', None


def _queue_force_unblock_message(conv_id, msg_id):
    """Remove dedup para permitir que o loop principal reprocesse a msg."""
    if not msg_id:
        return
    try:
        processed_msg_ids.discard(msg_id)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM msg_dedup WHERE msg_id = %s", (msg_id,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def process_queue_fast_sweep(waiting_convs, all_open_convs=None):
    """Varredura rapida da fila — roda a CADA ciclo do loop principal.

    Casos reportados na fila aguardando 5-22min:
      - 'obrigado' / despedida -> fecha na hora (sem esperar in_hours_rescue)
      - 'q eu ja pago' -> confirma pagamento e fecha
      - 'A matricula foi cancelada ja' -> Wesley
      - 'Ola' / anexo -> limpa dedup apos 3min para o loop principal atender

    Regra geral: waiting = aluno falou por ultimo e ainda nao foi atendido.
    """
    global student_profile, _current_phone
    if not is_within_business_hours():
        return
    if not waiting_convs:
        return

    now_ts = time.time()
    acted = 0
    unblocked = 0
    _MAX = 35

    for c in waiting_convs[:_MAX]:
        try:
            cid = c.get('id', '')
            if not cid:
                continue
            last_done = _QUEUE_SWEEP_RECENT.get(cid, 0)
            if last_done and (now_ts - last_done) < QUEUE_FAST_SWEEP_COOLDOWN_S:
                continue

            recv = c.get('lastReceivedMessageDate', '') or ''
            if not recv:
                continue
            try:
                from datetime import datetime as _dt
                age_min = (now_ts - _dt.fromisoformat(str(recv).replace('Z', '+00:00')).timestamp()) / 60
            except Exception:
                continue

            ct = c.get('contact', {}) or {}
            phone = (ct.get('phoneNumber', '') or ct.get('contactId', '') or '').replace('+', '').replace(' ', '')
            if phone.startswith('55') and len(phone) > 11:
                phone = phone[2:]
            name = (ct.get('name', '') or '').strip()
            first = name.split()[0] if name else ''
            first_part = f' *{first}*' if first else ''

            try:
                msgs = get_conversation_messages_api(cid, limit=12) or []
                _cached_msgs[cid] = msgs
            except Exception:
                msgs = []

            # (2026-05-27) Se NOSSA ultima msg foi farewell e nao houve resposta
            # do aluno, a conv ja deveria estar fechada. Provavel /finish falhou.
            # Re-fecha aqui para nao deixar conversa orfa na fila com "Obrigado...".
            try:
                if msgs and _last_msg_is_our_farewell(msgs):
                    p(f"  [QUEUE-SWEEP] re-close: ultima msg eh farewell nosso, conv ainda aberta -> finalizando ({cid[:12]} ...{phone[-4:]})")
                    try:
                        close_conversation_crm(cid, phone=phone)
                    except Exception as e_rc:
                        p(f"  [QUEUE-SWEEP] re-close erro: {e_rc}")
                    try:
                        update_pending_escalation_status(
                            cid, 'resolved',
                            note='Re-close automatico: farewell ja enviado sem resposta do aluno.',
                        )
                    except Exception:
                        pass
                    _QUEUE_SWEEP_RECENT[cid] = now_ts
                    acted += 1
                    continue
            except Exception:
                pass

            user_text, last_msg = _queue_last_aluno_body(msgs)
            if not user_text:
                continue

            # --- DISPARO REPLY TRACKER (2026-05-27) ---
            # Aluno respondeu APOS o disparo (timestamp > template) e nao eh robo.
            try:
                if (_is_dispatch_reply(msgs, last_user_msg=last_msg)
                        and not _is_external_bot_input(user_text)
                        and cid not in _DISPARO_LOGGED_CONVS
                        and _is_substantive_student_msg(user_text)):
                    _tpl = _extract_dispatch_template(msgs, last_user_msg=last_msg)
                    _log_dispatch_reply_once(cid, phone, name, user_text,
                                             tema=_dispatch_tema(_tpl, phone=phone))
            except Exception:
                pass

            # --- DESPEDIDA / CONFIRMACAO DE RESOLUCAO -> FECHAR ---
            # (2026-05-27) GUARD: sauda\u00e7\u00e3o pura nunca encerra.
            if not _is_pure_greeting(user_text) and (
                _is_farewell_message(user_text) or _is_resolution_confirmation(user_text)
            ):
                _kind = 'despedida' if _is_farewell_message(user_text) else 'confirmacao_resolucao'
                p(f"  [QUEUE-SWEEP] {_kind} conv={cid[:12]} ...{phone[-4:]} ({int(age_min)}min) '{user_text[:40]}' -> fechando")
                if _kind == 'confirmacao_resolucao':
                    reply = (
                        f"Que ótimo{first_part}! Fico feliz que tenha conseguido resolver 😊\n\n"
                        f"Se precisar de mais alguma coisa, é só chamar. Até mais!"
                    )
                else:
                    reply = (
                        f"Obrigado pelo contato{first_part}! 🙏\n\n"
                        f"Estamos sempre por aqui — qualquer coisa, é só me chamar de novo 😊"
                    )
                try:
                    send_and_track(cid, reply, force=True)
                except Exception:
                    pass
                try:
                    close_conversation_crm(cid, phone=phone)
                except Exception:
                    pass
                try:
                    update_pending_escalation_status(cid, 'resolved', note=f'QUEUE-SWEEP {_kind}: "{user_text[:80]}"')
                except Exception:
                    pass
                if last_msg and last_msg.get('id'):
                    processed_msg_ids.add(last_msg['id'])
                _QUEUE_SWEEP_RECENT[cid] = now_ts
                acted += 1
                continue

            # --- PAGAMENTO CONFIRMADO -> ACK + FECHAR ---
            if _is_payment_confirmed_message(user_text):
                p(f"  [QUEUE-SWEEP] pagamento conv={cid[:12]} ...{phone[-4:]} '{user_text[:40]}' -> ack+fechar")
                ack = f"Tudo bem{first_part}! 😊 Obrigado pela confirmação. Qualquer coisa, é só me chamar. Até mais!"
                try:
                    send_and_track(cid, ack, force=True)
                except Exception:
                    pass
                try:
                    close_conversation_crm(cid, phone=phone)
                except Exception:
                    pass
                if last_msg and last_msg.get('id'):
                    processed_msg_ids.add(last_msg['id'])
                _QUEUE_SWEEP_RECENT[cid] = now_ts
                acted += 1
                continue

            # --- PAGAMENTO FORA DO VENCIMENTO -> INFORMAR + FECHAR (2026-05-27) ---
            # Caso Odirlei/Wanny: aluno disse que vai pagar dia X (apos 25/05).
            # Agente informa que pode pagar sim, mas valor sera maior pela perda
            # do desconto da mensalidade, e encerra.
            if _is_payment_later(user_text):
                p(f"  [QUEUE-SWEEP] pagamento-tardio conv={cid[:12]} ...{phone[-4:]} '{user_text[:40]}' -> informar+fechar")
                info = (
                    f"Tudo bem{first_part}! 👍\n\n"
                    f"Você pode efetuar o pagamento sim, sem problema 😊\n\n"
                    f"Só fique atento(a): como o vencimento da mensalidade foi *25/05*, "
                    f"pagando depois dessa data o valor fica um pouco *maior*, porque os "
                    f"descontos vigentes da parcela são reduzidos após o vencimento.\n\n"
                    f"Quando puder, é só efetuar o pagamento normalmente pela 2ª via. "
                    f"Qualquer dúvida, estou por aqui!"
                )
                try:
                    send_and_track(cid, info, force=True)
                except Exception:
                    pass
                try:
                    close_conversation_crm(cid, phone=phone)
                except Exception:
                    pass
                try:
                    update_pending_escalation_status(cid, 'resolved', note=f'QUEUE-SWEEP pagamento_tardio: "{user_text[:80]}"')
                except Exception:
                    pass
                if last_msg and last_msg.get('id'):
                    processed_msg_ids.add(last_msg['id'])
                _QUEUE_SWEEP_RECENT[cid] = now_ts
                acted += 1
                continue

            # --- ALUNO OCUPADO / RETORNA DEPOIS -> ACK + FECHAR (2026-05-27) ---
            # Caso Karen: 'Ola no momento estou ocupada, assim que puder retorno.'
            # Agente nao deixa pendente — manda ack curto e encerra.
            if _is_busy_later(user_text):
                p(f"  [QUEUE-SWEEP] aluno-ocupado conv={cid[:12]} ...{phone[-4:]} '{user_text[:40]}' -> ack+fechar")
                ack = (
                    f"Tudo bem{first_part}! 😊 Quando estiver mais tranquilo(a), "
                    f"estaremos à disposição. Até mais!"
                )
                try:
                    send_and_track(cid, ack, force=True)
                except Exception:
                    pass
                try:
                    close_conversation_crm(cid, phone=phone)
                except Exception:
                    pass
                try:
                    update_pending_escalation_status(cid, 'resolved', note=f'QUEUE-SWEEP aluno_ocupado: "{user_text[:80]}"')
                except Exception:
                    pass
                if last_msg and last_msg.get('id'):
                    processed_msg_ids.add(last_msg['id'])
                _QUEUE_SWEEP_RECENT[cid] = now_ts
                acted += 1
                continue

            # --- RETENCAO / CANCELAMENTO -> TIME DE RETENÇÃO (Wesley/Danúbia) ---
            if is_retention_intent(user_text):
                # (2026-06-25) TESTE: telefone de teste -> só tag RET-IA, silencio
                if _use_ret_ia_automation(phone):
                    _trigger_retention_tag_only(cid, None, user_text, phone=phone)
                    p(f"  [QUEUE-SWEEP] [RET-IA] tag acionada, sem distribuir/mensagem")
                    if last_msg and last_msg.get('id'):
                        processed_msg_ids.add(last_msg['id'])
                    _QUEUE_SWEEP_RECENT[cid] = now_ts
                    acted += 1
                    continue
                try:
                    # (2026-06-08) trigger_retention escolhe o membro ativo e marca
                    # o handoff. So envia a msg se houver alguem ativo; senao segura.
                    _alvo_qs = trigger_retention(cid, None, user_text, phone=phone)
                    if _alvo_qs:
                        p(f"  [QUEUE-SWEEP] retencao conv={cid[:12]} ...{phone[-4:]} '{user_text[:40]}' -> {_alvo_qs}")
                        _greet = (f", *{first}*" if first else '')
                        _hum = (f"Oi{_greet}! Desculpa a demora 🙏 Entendi sua situação. "
                                f"Vou te conectar com o nosso *time de Retenção*, "
                                f"que vai te ajudar com isso, tá? Um momento!")
                        send_and_track(cid, _hum, force=True)
                        log_to_db(cid, user_text, _hum, 1.0, 'retention')
                        _register_signature(cid, 'retention', _hum)
                    else:
                        p(f"  [QUEUE-SWEEP] retencao conv={cid[:12]} SEM membro ativo — segura p/ proximo ciclo")
                except Exception as e_ret:
                    p(f"  [QUEUE-SWEEP] erro retencao: {e_ret}")
                if last_msg and last_msg.get('id'):
                    processed_msg_ids.add(last_msg['id'])
                _QUEUE_SWEEP_RECENT[cid] = now_ts
                acted += 1
                continue

            # --- ORFA >= 3min: distribui diretamente para consultor disponivel ---
            # (2026-05-27) Antes so desbloqueia dedup — main loop ainda precisava
            # de um ciclo extra para atender. Agora distribui na hora.
            # Cobre casos onde a conversa esta em 'unstarted' (pos-disparo) e o
            # main loop nao chega a processar a msg (dedup ja resolvido ou msg
            # sem ID). Funcao roda TODO ciclo, garantia de max ~5s na fila.
            if age_min >= QUEUE_FAST_SWEEP_MIN_AGE_MIN:
                atts = c.get('attendants') or []
                if not atts:
                    # (2026-05-28) PROTECAO RETENCAO: se conv passou por /retencao,
                    # NUNCA distribuir para consultor de Atendimento. Mantem Wesley.
                    try:
                        _lead_chk_qs, _, _ = _ensure_lead_for_rescue(phone, name)
                    except Exception:
                        _lead_chk_qs = None
                    try:
                        if _is_in_retention(cid, lead_id=_lead_chk_qs, msgs=msgs):
                            p(f"  [QUEUE-SWEEP] Conv {cid[:12]} em RETENCAO — mantem time de Retenção (nao distribui)")
                            try:
                                # sticky: trigger_retention reusa o alvo ja atribuido
                                trigger_retention(cid, _lead_chk_qs, user_text or '[retencao em andamento]', phone=phone)
                            except Exception as e_qsr:
                                p(f"  [QUEUE-SWEEP] erro sticky retencao: {e_qsr}")
                            _QUEUE_SWEEP_RECENT[cid] = now_ts
                            acted += 1
                            if last_msg and last_msg.get('id'):
                                processed_msg_ids.add(last_msg['id'])
                            continue
                    except Exception as e_chk_qs:
                        p(f"  [QUEUE-SWEEP] erro check retencao: {e_chk_qs}")
                    # Tenta distribuir diretamente
                    try:
                        _cons = get_available_consultant()
                        if _cons:
                            _cname = _cons.get('nome', '') or _cons.get('responsavel', '')
                            _cfirst = _cname.split()[0] if _cname else _cname
                            _lead_id, _biz_id, _created = _ensure_lead_for_rescue(phone, name)
                            if _lead_id:
                                _bok = _dcz_transfer_business(phone, _cname, lead_id=_lead_id)
                                _lok = _dcz_transfer_lead(_lead_id, _cname)
                                _cok = _dcz_transfer_chat(cid, _cname)
                                if _cok:
                                    _supabase_increment_fila(_cons.get('id', ''), int(_cons.get('fila', 0)))
                                    _apology = (
                                        f"Oii{(' ' + first) if first else ''}! Desculpa a demora para te responder 🙏\n\n"
                                        f"Vou te conectar agora com o(a) *{_cfirst}*, que vai dar continuidade ao seu atendimento. "
                                        f"Em pouquinho ele(a) assume aqui 😊"
                                    )
                                    try:
                                        send_and_track(cid, _apology)
                                    except Exception:
                                        pass
                                    p(f"  [QUEUE-SWEEP] distribuido conv={cid[:12]} ...{phone[-4:]} ({int(age_min)}min) -> {_cfirst}")
                                    acted += 1
                                    _QUEUE_SWEEP_RECENT[cid] = now_ts
                                    if last_msg and last_msg.get('id'):
                                        processed_msg_ids.add(last_msg['id'])
                                    continue
                    except Exception as e_dist:
                        p(f"  [QUEUE-SWEEP] erro distribuicao {cid[:12]}: {e_dist}")
                # Se nao distribuiu (sem consultor, ou ja tem atendente),
                # cai no desbloqueio classico para o loop principal tentar.
                if last_msg:
                    mid = last_msg.get('id', '')
                    if mid and mid in processed_msg_ids:
                        _queue_force_unblock_message(cid, mid)
                        unblocked += 1
                        p(f"  [QUEUE-SWEEP] desbloqueio conv={cid[:12]} ...{phone[-4:]} ({int(age_min)}min) '{user_text[:40]}'")
                _QUEUE_SWEEP_RECENT[cid] = now_ts

        except Exception as e_one:
            p(f"  [QUEUE-SWEEP] erro conv {c.get('id','?')[:12]}: {e_one}")

    if acted or unblocked:
        p(f"  [QUEUE-SWEEP] distribuidos={acted} desbloqueios={unblocked} (waiting={len(waiting_convs)})")


_HANDOFF_FULFILL_RECENT = {}
HANDOFF_FULFILL_COOLDOWN_S = 120


# ===================== INACTIVE ATTENDANT RESCUE — REMOVIDO (2026-06-01) =====================
# Tentativa anterior foi agressiva demais: varria TODAS as conversas e tirava
# dos atendentes inativos, mesmo quando o atendimento estava em andamento
# normalmente (ex: Julia ainda atendendo um aluno apesar de marcada como
# inativa no Supabase). Resultou em ~31 conversas indevidamente
# redistribuidas para Danubia/Camila e tiveram que ser revertidas
# manualmente.
#
# Decisao: NAO mexer em conversa que ja tem atendente. A unica prevencao
# necessaria eh no momento de DISTRIBUIR UMA NOVA conv — e isso ja eh feito
# por get_available_consultant() (filtro ativo_inativo=Ativo) + checagem
# _ATTENDANTS_ON_VACATION. Se atendente inativo recebe conv por outro canal
# (DCZ Easy auto-dist, n8n, etc.), isso eh problema externo — nao do agente.


def process_handoff_fulfillment_sweep(open_convs):
    """Cumpre promessas de transferencia quando bot ja respondeu mas nao ha
    atendente no DCZ. Caso reportado: 'Vou te transferir para Debora...'
    ficava na fila com bot por ultimo (rest, nao waiting) — in_hours_rescue
    ignorava porque recv <= sent.

    Regra geral: handoff_active + sem atendente + target ativo -> transferir.
    """
    if not is_within_business_hours() or not open_convs:
        return

    now_ts = time.time()
    fulfilled = 0
    _MAX = 20

    for c in open_convs[:_MAX * 3]:
        if fulfilled >= _MAX:
            break
        try:
            cid = c.get('id', '')
            if not cid or c.get('attendants'):
                continue
            last_done = _HANDOFF_FULFILL_RECENT.get(cid, 0)
            if last_done and (now_ts - last_done) < HANDOFF_FULFILL_COOLDOWN_S:
                continue

            ho_motivo, ho_target = _is_handoff_active(cid)
            if not ho_motivo or not ho_target:
                continue
            if ho_motivo not in ('dispatch', 'preferred', 'retention'):
                continue

            try:
                has_h, att_name = _dcz_conv_has_human(cid)
            except Exception:
                has_h, att_name = False, ''
            if has_h:
                continue

            target_first = ho_target.split()[0].lower()
            if not is_attendant_active_now(target_first):
                p(f"  [HANDOFF-FULFILL] {cid[:12]} target={ho_target} offline — limpando handoff stale")
                try:
                    _clear_handoff_active(cid, reason='target_offline_fulfill')
                except Exception:
                    pass
                continue

            ct = c.get('contact', {}) or {}
            phone = (ct.get('phoneNumber', '') or ct.get('contactId', '') or '').replace('+', '').replace(' ', '')
            if phone.startswith('55') and len(phone) > 11:
                phone = phone[2:]
            name = (ct.get('name', '') or '').strip()

            p(f"  [HANDOFF-FULFILL] {cid[:12]} promessa {ho_target} sem atendente — forcando transferencia")
            lead_id, _, _ = _ensure_lead_for_rescue(phone, name)
            try:
                if lead_id:
                    _dcz_transfer_business(phone, ho_target, lead_id=lead_id)
                    _dcz_transfer_lead(lead_id, ho_target)
                ok = _dcz_transfer_chat(cid, ho_target)
                if not ok:
                    nome_norm = ho_target.strip().lower()
                    nome_norm = ''.join(
                        c for c in __import__('unicodedata').normalize('NFD', nome_norm)
                        if __import__('unicodedata').category(c) != 'Mn'
                    )
                    att_id = ATTENDANT_MAP.get(nome_norm, '')
                    if att_id:
                        requests.post(
                            f'{DCZ_MSG}/messaging/conversations/{cid}/change-attendant',
                            headers=H, json={'attendantId': att_id}, timeout=15,
                        )
                try:
                    requests.post(
                        f'{DCZ_API}/api/v1/conversations/{cid}/messages',
                        headers=H,
                        json={'body': f'🔧 *Handoff fulfillment* — transferencia forcada para {ho_target} (promessa sem atendente no DCZ).', 'isInternal': True},
                        timeout=10,
                    )
                except Exception:
                    pass
            except Exception as e_tf:
                p(f"  [HANDOFF-FULFILL] erro transfer: {e_tf}")
                continue

            _HANDOFF_FULFILL_RECENT[cid] = now_ts
            fulfilled += 1
        except Exception as e_one:
            p(f"  [HANDOFF-FULFILL] erro conv {c.get('id','?')[:12]}: {e_one}")

    if fulfilled:
        p(f"  [HANDOFF-FULFILL] Total transferencias forcadas: {fulfilled}")


# ===================== POST-CLOSE RESCUE (reabertura apos encerramento) =====================
_POST_CLOSE_RESCUE_RECENT = {}  # conv_id -> last_action_ts
# (2026-05-25) AGE_MIN baixado de 5 para 1 min. Caso reportado: aluno
# respondeu "obrigado" 3min apos atendente finalizar -> agente nao
# pegava no rescue (esperava 5min) -> conversa ficava aberta com a
# msg automatica do DCZ "Este atendimento foi encerrado..." sem ninguem
# fechar de fato.
POST_CLOSE_RESCUE_AGE_MIN = 1
POST_CLOSE_RESCUE_MAX_AGE_MIN = 60  # apos 1h, vira problema do in_hours_rescue normal
POST_CLOSE_RESCUE_COOLDOWN_S = 30 * 60

# Padroes que indicam que houve encerramento no historico recente
_CLOSE_EVENT_PATTERNS = (
    'finalizou o atendimento',
    'atendimento foi encerrado',
    'este atendimento foi encerrado',
    'atendimento foi finalizado',
    'este atendimento foi finalizado',
    'se quiser retornar para conversar',
    'encerrando esta conversa',
    # (2026-05-25) Frases que NOSSO bot envia ao fechar — quando o aluno
    # responde "Obrigada" depois disso, _had_close_event_recently precisa
    # detectar essa frase como "evento de close" pro post_close_rescue agir.
    'obrigado pela confirmação',
    'obrigado pela confirmacao',
    'tudo certo então',
    'tudo certo entao',
    'se surgir algo mais, pode contar comigo',
    'fico feliz que tenha conseguido resolver',
    'fico à disposição pro que precisar',
    'qualquer coisa, é só me chamar',
    'qualquer coisa, e so me chamar',
)


# (2026-05-25) Mensagens de OUTRAS automações/bots que chegam como input
# (received=True) mas não são do aluno — são respostas automáticas de
# integrações externas (URA, vCard, autoresponder de empresa parceira, etc).
# Quando o agente recebe isso, NÃO deve tentar responder com menu/LLM —
# deve apenas fechar a conv silenciosamente. Caso reportado: Claudenice
# recebeu "claupiercings agradece seu contato. Como podemos ajudar?".
_EXTERNAL_BOT_INPUT_PATTERNS = (
    'agradece seu contato',
    'agradece o contato',
    'como podemos ajudar?',
    'como podemos te ajudar?',
    'como podemos ajudá-lo',
    'como podemos ajuda-lo',
    'mensagem automática',
    'mensagem automatica',
    'resposta automática',
    'resposta automatica',
    'atendimento automático',
    'atendimento automatico',
    'somos uma empresa',
    'horário de atendimento das nossas lojas',
    'horario de atendimento das nossas lojas',
    'esta é uma resposta automatica',
    'esta e uma resposta automatica',
    'fora do horário comercial',
    'fora do horario comercial',
    'aguarde nosso retorno',
    'em breve um de nossos atendentes',
    'whatsapp business',
    # (2026-05-25) Bots automáticos de outras integrações
    'obrigado por entrar em contato',
    'agradecemos o contato',
    'agradecemos seu contato',
    'retornaremos em breve',
    'em horário comercial responderemos',
    'em horario comercial responderemos',
    'boas-vindas ao',
    'nosso atendimento funciona',
    'horário de atendimento de segunda',
    'horario de atendimento de segunda',
    'esta é uma mensagem automática',
    'esta e uma mensagem automatica',
    'mensagem encaminhada automaticamente',
    'recebemos sua mensagem',
    'responderemos assim que possível',
    'responderemos assim que possivel',
)


def _is_external_bot_input(text):
    """Detecta se a 'mensagem do aluno' é, na verdade, msg de outro bot/URA
    externo. Caso reportado (claupiercings 'agradece seu contato. Como
    podemos ajudar?'). Quando True, agente FECHA a conv sem responder.
    """
    if not text:
        return False
    import unicodedata
    t = (text or '').strip().lower()
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    for p in _EXTERNAL_BOT_INPUT_PATTERNS:
        p_norm = ''.join(c for c in unicodedata.normalize('NFD', p)
                         if unicodedata.category(c) != 'Mn')
        if p_norm in t_norm:
            return True
    return False


# (2026-05-27) Bug Jucelia: conv finalizada por humano, aluno responde "Obrigada"
# 30min depois, o DCZ Easy automaticamente reabre a conv e dispara o balao
# "Este atendimento foi encerrado, se quiser retornar para conversar...".
# Esse balao tem ts > ts do "Obrigada" por ~4 segundos. Nossas funcoes de
# resgate (in_hours_rescue, queue_fast_sweep, post_close_rescue) usavam
# `if sent and recv <= sent: continue` — pulando ESSAS conversas, embora
# elas precisem de close. Helper abaixo identifica esse balao DCZ para
# permitir que as funcoes de resgate prossigam mesmo assim.
_DCZ_AUTO_CLOSE_BALLOON_PATTERNS = (
    'este atendimento foi encerrado',
    'se quiser retornar para conversar',
    'se quiser retornar para convervar',
    'retornar ao atendimento',
)


def _sent_is_dcz_auto_close_balloon(cid):
    """True se a ULTIMA msg outbound da conv eh o balao automatico do DCZ
    Easy 'Este atendimento foi encerrado...'. Esse balao eh disparado pelo
    DCZ quando aluno responde uma conv finalizada — NAO conta como
    atendimento humano em andamento.
    """
    if not cid:
        return False
    try:
        msgs = get_conversation_messages_api(cid, limit=6) or []
    except Exception:
        return False
    for m in reversed(msgs):
        if m.get('received', False):
            continue
        body = (m.get('body') or m.get('text') or '').strip().lower()
        if not body:
            continue
        for pat in _DCZ_AUTO_CLOSE_BALLOON_PATTERNS:
            if pat in body:
                return True
        # outra msg outbound real -> nao eh balao DCZ
        return False
    return False


def _should_skip_due_to_sent_after_recv(cid, recv, sent):
    """Retorna True se a regra `recv <= sent` deve bloquear o resgate.
    Retorna False (= prossegue) se o motivo do `sent > recv` for o balao
    DCZ automatico. Janela curta (<= 5min) eh tipica desse caso.
    """
    if not sent or recv > sent:
        return False
    try:
        from datetime import datetime as _dt
        _ts_r = _dt.fromisoformat(str(recv).replace('Z', '+00:00')).timestamp()
        _ts_s = _dt.fromisoformat(str(sent).replace('Z', '+00:00')).timestamp()
        _diff = _ts_s - _ts_r
    except Exception:
        return True  # nao parseou -> mantem comportamento antigo
    # Se diferenca > 5min, eh resposta humana real -> bloqueia
    if _diff > 300:
        return True
    # Janela curta: pode ser balao DCZ. Confirma.
    if _sent_is_dcz_auto_close_balloon(cid):
        return False  # NAO bloqueia: prossegue com resgate
    return True


def _msg_is_template_hsm(conv_id, msg_id):
    """Verifica em _cached_msgs se a msg eh template HSM (disparo via WhatsApp
    Business API). Templates NUNCA devem ser confundidos com 'bot externo' —
    sao disparos do nosso Cockpit.
    """
    if not conv_id or not msg_id:
        return False
    try:
        for m in _cached_msgs.get(conv_id, []) or []:
            if m.get('id') == msg_id:
                return _is_template_message(m)
    except Exception:
        pass
    return False

# Padroes de despedida (msg do aluno apos encerramento que NAO requer atendente)
# (2026-05-26) Expandido — casos reportados de despedidas que NAO eram pegas:
#   "Vou avaliar Muito grato"  -> faltava 'grato'/'grata'
#   "Para você também 😊"      -> emoji + msg curta, agora cobre
#   "Nao, obrigado."           -> ja pegava por 'obrigad', mas check rodava
#                                  tarde demais (so em low-conf), agora roda
#                                  no INICIO de handle_message
_FAREWELL_KEYWORDS = (
    'obrigad', 'valeu', 'vlw', 'agradeco', 'agradeço', 'agradecid',
    'grato', 'grata', 'gratidao', 'gratidão',
    'tchau', 'ate mais', 'até mais', 'ate logo', 'até logo', 'falou',
    'beleza', 'blz', 'show', 'show de bola',
    'perfeito', 'otimo', 'ótimo', 'maravilha', 'tranquilo', 'tranquila',
    'entendido', 'entendida', 'ciente', 'compreendido', 'compreendi',
    # (2026-05-27 v2) Restaurados: 'ok'/'okay'/'okey' sao confirmacao curta
    # de fim de conversa. Caso Monica: respondeu so 'Ok' ao disparo - era pra
    # encerrar humanizado. _is_pure_greeting nao captura ok porque ele NAO eh
    # uma saudacao de abertura como bom dia/boa tarde.
    'ok', 'okay', 'okey',
    # (2026-05-27) REMOVIDO antes: 'bom dia', 'boa tarde', 'boa noite'
    # — eram saudacoes de ABERTURA, nao despedida. Caso Anderson/Natalia:
    # mandou so 'Boa tarde' e foi encerrado como despedida.
    'so isso', 'só isso', 'era isso', 'so era isso', 'só era isso',
    'pra voce tambem', 'pra você também', 'para voce tambem', 'para você também',
    'pra ti tambem', 'pra ti também', 'igualmente',
    'abraco', 'abraço', 'um abraco', 'um abraço',
)

# Saudacoes puras (NUNCA sao despedida quando sozinhas).
_GREETING_ONLY_PHRASES = (
    'bom dia', 'boa tarde', 'boa noite', 'oi', 'ola', 'olá', 'hey', 'hi',
    'eai', 'opa', 'oii', 'oie',
)
_FAREWELL_EMOJIS = ('👍', '🙏', '❤', '❤️', '😊', '🙌', '👏', '✅', '😉', '😘', '🤝', '🥰', '💚', '💙')


def _is_farewell_message(text):
    """Detecta se a mensagem do aluno e apenas uma despedida/agradecimento."""
    if not text:
        return False
    import unicodedata
    t = text.strip().lower()
    if len(t) > 80:
        return False
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    # (2026-05-27) Saudacoes puras NUNCA sao despedida: aluno abrindo conversa.
    # Caso reportado: 'Boa tarde' sozinho estava sendo tratado como despedida.
    t_clean = ''.join(c for c in t_norm if c.isalnum() or c.isspace()).strip()
    if t_clean in _GREETING_ONLY_PHRASES:
        return False
    # (2026-06-01) Se a msg contem saudacao + outro token curto (ex:
    # "ola obrigada", "oi obrigado", "boa tarde td bem"), eh ABERTURA de
    # conversa, NAO despedida. Senao "obrigada" sozinho ainda casa.
    if _is_pure_greeting(text):
        return False
    # Se contem saudacao no inicio + palavra de "cortesia/agradecimento",
    # tratar como abertura (cortes\u00eda). Caso reportado: aluno volta
    # apos dias e diz "Ola obrigada" — n\u00e3o deve fechar.
    tokens_init = [w for w in t_clean.split() if w]
    if tokens_init and tokens_init[0] in _GREETING_TOKENS and len(tokens_init) <= 4:
        # Se PELO MENOS uma saudacao real no inicio, n\u00e3o tratar como
        # despedida — aluno est\u00e1 abrindo conversa.
        return False
    if any(emo in text for emo in _FAREWELL_EMOJIS) and len(t) <= 30:
        return True
    for kw in _FAREWELL_KEYWORDS:
        if kw in t_norm:
            remaining = t_norm
            for kw2 in _FAREWELL_KEYWORDS:
                remaining = remaining.replace(kw2, ' ')
            remaining = ''.join(c if c.isalnum() else ' ' for c in remaining)
            words = [w for w in remaining.split() if len(w) > 2]
            if len(words) <= 2:
                return True
    return False


# (2026-05-26) Detector de "confirmacao de resolucao" — frases que o aluno
# manda quando ja resolveu OU confirmou entendimento. Equivalente funcional
# a despedida: o atendente respondeu, o aluno disse "deu certo" e a conv
# deve ser fechada. Casos reportados na fila:
#   "Conseguiu entender as explicacoes?"  -> confirmacao do bot
#   "Consegui entender"                    -> aluno confirma
#   "Deu certo"                            -> aluno confirma resolucao
#   "Funcionou"                            -> aluno confirma resolucao
_RESOLUTION_PHRASES = (
    'consegui entender', 'entendi sim', 'entendi tudo', 'entendi agora',
    'entendi obrigad', 'entendi, obrigad', 'consegui sim', 'consegui resolver',
    'consegui acessar', 'consegui ver', 'consegui sim, obrigad',
    'deu certo', 'deu tudo certo', 'ja deu certo', 'já deu certo',
    'funcionou', 'funcionou sim', 'ja funcionou', 'já funcionou',
    'resolveu', 'resolvi', 'resolvido', 'foi resolvido', 'ja resolvi', 'já resolvi',
    'consegui acesso', 'ja consegui', 'já consegui',
    'sucesso', 'tudo certo', 'ta tudo certo', 'tá tudo certo', 'esta tudo certo',
    'esta tudo bem', 'está tudo bem', 'ta tudo bem', 'tá tudo bem',
    'consegui aqui', 'foi sim', 'foi sim, obrigad',
    'pode encerrar', 'pode finalizar', 'pode fechar',
    'nao precisa mais', 'não precisa mais', 'nao precisa de mais nada',
    'não precisa de mais nada', 'sem mais duvidas', 'sem mais dúvidas',
    'esclarecid', 'sanad',
)


def _is_resolution_confirmation(text):
    """Detecta confirmacao de resolucao do aluno (equivalente a despedida).
    Caso reportado: "Consegui entender as explicacoes" na fila aguardando
    atendimento. Como o aluno ja resolveu, deve fechar igual a uma
    despedida — nao precisa de novo atendente.
    """
    if not text:
        return False
    import unicodedata
    t = text.strip().lower()
    if len(t) > 120:
        return False
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    # (2026-05-27) Sauda\u00e7\u00e3o pura NUNCA \u00e9 confirmacao de resolu\u00e7\u00e3o.
    t_clean = ''.join(c for c in t_norm if c.isalnum() or c.isspace()).strip()
    if t_clean in _GREETING_ONLY_PHRASES:
        return False
    for phrase in _RESOLUTION_PHRASES:
        ph_norm = ''.join(c for c in unicodedata.normalize('NFD', phrase)
                          if unicodedata.category(c) != 'Mn')
        if ph_norm in t_norm:
            return True
    return False


# (2026-06-01) Palavras "neutras" que acompanham saudacao sem mudar a
# natureza (abertura de conversa). Ex: "Ola tudo bem?", "Oi td bom".
_GREETING_NEUTRAL_TOKENS = (
    'tudo', 'bem', 'bom', 'td', 'tdb', 'tdbm', 'beleza', 'blz',
    'eai', 'ae', 'e', 'ai',
)
# Tokens que TAMBEM sao saudacoes (versao tokenizada)
_GREETING_TOKENS = (
    'oi', 'oii', 'oiii', 'ola', 'olá', 'opa', 'eai', 'hey', 'hi',
    'bom', 'boa', 'dia', 'tarde', 'noite',  # "bom dia" etc.
    'oie', 'oiee',
)


def _is_pure_greeting(text):
    """True se a mensagem do aluno e APENAS uma saudacao (sem outro conteudo).
    Usado como guard defensivo em qualquer caminho que poderia tratar
    saudacao como despedida/encerramento.

    (2026-06-01) Agora reconhece saudacoes COMPOSTAS:
      - "Ola Oi" / "Oi tudo bem" / "Bom dia tudo bom"
      - "Ola!" / "Oi." (pontuacao)
    """
    if not text:
        return False
    import unicodedata
    t = text.strip().lower()
    if len(t) > 40:
        return False
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    t_clean = ''.join(c for c in t_norm if c.isalnum() or c.isspace()).strip()
    # Match exato (rapido)
    if t_clean in _GREETING_ONLY_PHRASES:
        return True
    # Match tokenizado: TODAS as palavras tem que ser saudacao ou neutro
    tokens = [tok for tok in t_clean.split() if tok]
    if not tokens or len(tokens) > 6:
        return False
    for tok in tokens:
        if tok in _GREETING_TOKENS:
            continue
        if tok in _GREETING_NEUTRAL_TOKENS:
            continue
        return False
    # Pelo menos UM token tem que ser saudacao real (nao so neutros)
    has_real_greeting = any(tok in _GREETING_TOKENS for tok in tokens)
    return has_real_greeting


# Confirmacao de pagamento (resposta a disparo de boleto/mensalidade).
# Casa quando o aluno DIZ que ja pagou — para nao distribuir, so confirmar e fechar.
# Nao casa "vou pagar", "como pagar", "posso pagar", "esqueci de pagar", "nao paguei".
_PAYMENT_CONFIRMED_PHRASES = (
    'ja paguei', 'já paguei', 'ja foi pago', 'já foi pago',
    'ja paguei o boleto', 'já paguei o boleto',
    'ja paguei a mensalidade', 'já paguei a mensalidade',
    'paguei o boleto', 'paguei a mensalidade', 'paguei a parcela',
    'paguei hoje', 'paguei ontem', 'paguei agora', 'paguei essa semana',
    'paguei essa manha', 'paguei essa manhã', 'paguei pela manha', 'paguei pela manhã',
    'paguei a fatura', 'paguei o valor',
    'ja foi pago', 'já foi pago', 'foi pago', 'esta pago', 'está pago',
    'ja esta pago', 'já está pago', 'ja ta pago', 'já tá pago',
    'realizei o pagamento', 'realizei pagamento', 'fiz o pagamento',
    'efetuei o pagamento', 'efetuei pagamento',
    'pix realizado', 'pix feito', 'pix enviado', 'pix ja feito', 'pix já feito',
    'boleto pago', 'boleto ja pago', 'boleto já pago',
    'mensalidade paga', 'mensalidade ja paga', 'mensalidade já paga',
    'parcela paga', 'fatura paga',
    'quitei', 'ja quitei', 'já quitei', 'quitado', 'quitada',
    'paguei sim', 'paguei ja', 'paguei já',
    'ja pago', 'já pago', 'eu ja pago', 'eu já pago',
    'que eu ja pago', 'q eu ja pago', 'que eu já pago',
)
_PAYMENT_NEGATIVES = (
    'nao paguei', 'não paguei', 'ainda nao paguei', 'ainda não paguei',
    'nao consegui pagar', 'não consegui pagar',
    'como pagar', 'como pago', 'como faço para pagar', 'como faco para pagar',
    'posso pagar', 'vou pagar', 'irei pagar', 'pretendo pagar',
    'esqueci de pagar', 'tenho que pagar', 'preciso pagar',
    'quero pagar', 'quanto pagar', 'onde pagar', 'onde pago',
)

def _is_payment_confirmed_message(text):
    """Detecta se o aluno esta dizendo que JA pagou o boleto/mensalidade.
    Usado para responder com confirmacao + encerrar (sem distribuir).
    """
    if not text:
        return False
    import unicodedata
    t = text.strip().lower()
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    # Primeiro, rejeitar negativos / futuro / pergunta
    for neg in _PAYMENT_NEGATIVES:
        if neg in t_norm:
            return False
    # Pergunta sobre pagamento? Nao confirma.
    if '?' in text and any(k in t_norm for k in ('paga', 'pagar', 'pagou', 'pagamento', 'boleto', 'pix')):
        return False
    for phrase in _PAYMENT_CONFIRMED_PHRASES:
        # normalizar acentos da phrase para comparar
        phrase_norm = ''.join(c for c in unicodedata.normalize('NFD', phrase)
                              if unicodedata.category(c) != 'Mn')
        if phrase_norm in t_norm:
            return True
    return False


# ============================================================
# PAYMENT LATER (2026-05-27)
# Aluno informou que vai pagar APOS o vencimento. Agente deve responder
# explicando que pode pagar sim, mas o valor sera maior (perde desconto
# da mensalidade vigente) e encerrar.
# ============================================================
_PAYMENT_LATER_PHRASES = (
    'vou pagar dia', 'pago dia', 'pagarei dia', 'vou pagar no dia',
    'pago no dia', 'pago apenas dia',
    'vou pagar semana que vem', 'pago semana que vem', 'pago semana q vem',
    'vou pagar mes que vem', 'pago mes que vem', 'pago mês que vem',
    'pago no proximo mes', 'pago no próximo mês',
    'so consigo dia', 'só consigo dia', 'so consigo pagar dia', 'só consigo pagar dia',
    'so vou conseguir dia', 'só vou conseguir dia',
    'so vou conseguir pagar dia', 'só vou conseguir pagar dia',
    'so consigo no dia', 'só consigo no dia',
    'so consigo no fim do mes', 'só consigo no fim do mês',
    'so vou conseguir no fim', 'só vou conseguir no fim',
    'pago no fim do mes', 'pago no fim do mês', 'so no fim do mes', 'só no fim do mês',
    'so tenho dinheiro dia', 'só tenho dinheiro dia',
    'so tenho como pagar dia', 'só tenho como pagar dia',
    'pago depois', 'vou pagar depois', 'consigo pagar depois',
    'so consigo depois', 'só consigo depois', 'depois eu pago', 'depois eu efetuo',
    'pagar fora do prazo', 'pagar depois do vencimento', 'pagar apos o vencimento',
    'pagar após o vencimento', 'pagar pos o vencimento',
    'vou pagar mais tarde', 'pago mais tarde',
    'pago somente dia', 'só posso pagar dia', 'so posso pagar dia',
    'o que eu faço para fazer', 'o que eu faco para fazer',
    'como faco para pagar atrasado', 'como faço para pagar atrasado',
    'irei pagar dia', 'irei pagar somente', 'irei efetuar o pagamento dia',
    'efetuarei o pagamento dia', 'efetuarei pagamento dia',
)
_PAYMENT_LATER_NUM_PATTERNS = (
    # 'dia 30', '30/05', '30-05', 'no dia 30'
    r'\bdia\s+\d{1,2}\b',
    r'\bate\s+(o\s+)?dia\s+\d{1,2}\b',
    r'\baté\s+(o\s+)?dia\s+\d{1,2}\b',
    r'\bdia\s+\d{1,2}/\d{1,2}',
    r'\bem\s+\d{1,2}/\d{1,2}',
)


def _is_payment_later(text):
    """Aluno disse que vai pagar fora do vencimento (data futura).
    Caso reportado: Odirlei 'Pagarei a mensalidade dia...'; Wanny 'O que eu faço
    para fazer o pagamento...'. Agente deve explicar que valor sera maior
    pela perda do desconto da mensalidade e encerrar.
    """
    if not text:
        return False
    import unicodedata, re
    t = text.strip().lower()
    if len(t) > 350:
        return False
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    # NEGATIVOS: ja pagou (nao eh payment-later)
    if 'ja paguei' in t_norm or 'já paguei' in t_norm or 'foi pago' in t_norm:
        return False
    for phrase in _PAYMENT_LATER_PHRASES:
        phrase_norm = ''.join(c for c in unicodedata.normalize('NFD', phrase)
                              if unicodedata.category(c) != 'Mn')
        if phrase_norm in t_norm:
            return True
    # Padrao "vou pagar/pagarei + dia/data"
    if any(k in t_norm for k in ('pagar', 'pagarei', 'pago', 'pagamento')):
        for pat in _PAYMENT_LATER_NUM_PATTERNS:
            if re.search(pat, t_norm):
                return True
    return False


# ============================================================
# BUSY/LATER (2026-05-27)
# Aluno disse que esta ocupado/sem tempo agora e retorna depois.
# Agente envia ack curto + encerra (nao adianta deixar conv pendente).
# ============================================================
_BUSY_LATER_PHRASES = (
    'no momento estou ocupad', 'agora estou ocupad', 'estou ocupad no momento',
    'estou ocupad agora', 'estou em atendimento', 'estou atendendo',
    'estou trabalhando', 'estou no servico', 'estou no serviço',
    'estou em servico', 'estou em serviço', 'estou em reuniao', 'estou em reunião',
    'sem tempo agora', 'sem tempo no momento', 'nao tenho tempo agora',
    'não tenho tempo agora', 'nao posso falar agora', 'não posso falar agora',
    'agora nao posso falar', 'agora não posso falar',
    'agora nao posso', 'agora não posso', 'agora nao da', 'agora não dá',
    'agora nao consigo', 'agora não consigo',
    'depois eu vejo', 'depois eu vou ver', 'depois eu retorno',
    'depois te retorno', 'depois eu te retorno', 'depois eu volto a falar',
    'assim que puder retorno', 'assim que puder volto', 'assim que puder respondo',
    'assim que puder eu retorno', 'quando der retorno', 'quando puder retorno',
    'quando eu puder retorno', 'mais tarde retorno', 'mais tarde eu retorno',
    'depois retorno', 'mais tarde te respondo',
    'so de noite consigo', 'só de noite consigo',
    'so a noite consigo', 'só a noite consigo',
    'depois eu falo', 'depois te falo', 'depois eu te falo',
)


def _is_busy_later(text):
    """Aluno disse que esta ocupado/sem tempo agora, retorna depois.
    Caso reportado: Karen 'Ola no momento estou ocupada, assim que puder
    retorno.'. Agente envia ack curto e encerra.
    """
    if not text:
        return False
    import unicodedata
    t = text.strip().lower()
    if len(t) > 250:
        return False
    t_norm = ''.join(c for c in unicodedata.normalize('NFD', t)
                     if unicodedata.category(c) != 'Mn')
    for phrase in _BUSY_LATER_PHRASES:
        phrase_norm = ''.join(c for c in unicodedata.normalize('NFD', phrase)
                              if unicodedata.category(c) != 'Mn')
        if phrase_norm in t_norm:
            return True
    return False


def _extract_last_attendant_from_history(msgs):
    """Procura o nome do atendente que encerrou no historico recente.
    Padrao: 'Camila Ferreira finalizou o atendimento'.
    Retorna primeiro nome em lowercase ou None.

    (2026-05-28) Se houver evento de RETENCAO (consultor X usou /retencao no
    DCZ ou nosso trigger_retention) DEPOIS do ultimo 'finalizou', retorna
    'wesley' — caso contrario o resgate sticky devolve aluno em retencao
    para o consultor original. Caso reportado: aluna voltava para Beatriz
    apos /retencao para Wesley.
    """
    if not msgs:
        return None
    import re
    # 1) Acha index do ultimo "X finalizou o atendimento"
    last_fin_idx = -1
    last_fin_name = None
    for i, m in enumerate(msgs):
        body = (m.get('body') or m.get('text') or '').strip()
        if not body:
            continue
        mt = re.match(r'^([A-Z][a-zA-ZÀ-ÿ]+)(?:\s+[A-Z][a-zA-ZÀ-ÿ]+)*\s+finalizou\s+o\s+atendimento', body)
        if mt:
            last_fin_idx = i
            last_fin_name = mt.group(1).strip().lower()
    # 2) Procura evento de retencao APOS o ultimo "finalizou" (ou em qualquer
    #    lugar se nao houve "finalizou"). Marcadores:
    #    - "moveu para o departamento Retenção"
    #    - nota interna "Retenção - Agente IA" (nosso trigger_retention)
    #    - frase "Transferido automaticamente para Wesley"
    ret_after = False
    for i, m in enumerate(msgs):
        if i <= last_fin_idx:
            continue
        body = (m.get('body') or m.get('text') or '').strip().lower()
        if not body:
            continue
        if 'retenção' in body or 'retencao' in body:
            if 'departamento' in body or 'wesley' in body or 'agente ia' in body:
                ret_after = True
                break
        if 'transferido automaticamente para wesley' in body:
            ret_after = True
            break
    if ret_after:
        return 'wesley'
    return last_fin_name


_RETENTION_RECENT_HOURS = 168  # 7 dias: evento de retencao precisa ser desse periodo


def _is_in_retention(cid, lead_id=None, msgs=None):
    """True se a conv esta EM PROCESSO ATIVO de retencao com Wesley.

    Caso reportado (2026-06-01): a regra retornava True so porque o lead
    tinha a tag 'Retenção Wesley' (de algum atendimento meses antes).
    Aluno mandava 'ola' ou 'boa tarde' e o agente disparava trigger_retention
    de novo, transferindo para Wesley indevidamente.

    Regra atualizada: exige EVIDENCIA RECENTE (handoff_active ativo OU
    evento de retencao no historico nos ultimos _RETENTION_RECENT_HOURS).
    A tag sozinha NAO basta — ela eh "permanente" no CRM e nao indica
    intencao atual.

    Fontes aceitas:
    1. handoff_active(motivo='retention') ativo agora
    2. historico contém "Retenção" + ("departamento"|"wesley"|"agente ia"|
       "cancelamento"|"transferid") DENTRO de _RETENTION_RECENT_HOURS
    3. ultima nota nossa de trigger_retention DENTRO de _RETENTION_RECENT_HOURS
    """
    try:
        motivo, target = _is_handoff_active(cid)
        if motivo == 'retention' or (target and 'wesley' in (target or '').lower()):
            return True
    except Exception:
        pass
    if msgs:
        cutoff = None
        try:
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            cutoff = _dt.now(_tz.utc) - _td(hours=_RETENTION_RECENT_HOURS)
        except Exception:
            cutoff = None

        def _msg_ts_utc(m):
            try:
                ts = m.get('createdAt') or m.get('created_at') or ''
                if not ts:
                    return None
                from datetime import datetime as _dt
                return _dt.fromisoformat(str(ts).replace('Z', '+00:00'))
            except Exception:
                return None

        for m in msgs[-30:]:
            body = (m.get('body') or m.get('text') or '').strip().lower()
            if not body:
                continue
            # Filtro temporal: se nao consegue extrair ts, ignora (seguranca:
            # nao quer disparar retencao por evento antigo)
            ts = _msg_ts_utc(m)
            if cutoff and ts and ts < cutoff:
                continue
            if cutoff and ts is None:
                # Sem ts -> nao podemos confiar. Pula.
                continue
            # NAO usar a propria nota interna do agente como evidencia —
            # pode ser de execucao anterior que disparou falso positivo.
            # Detecta nota nossa: contem assinatura "*Retenção - Agente IA*"
            # ou variantes.
            if 'retenção - agente ia' in body or 'retencao - agente ia' in body:
                continue
            if 'sticky retencao' in body or 'sticky retenção' in body:
                continue
            # Evento real do DCZ "moveu para o departamento Retenção"
            if 'moveu para o departamento retenç' in body or \
               'moveu para o departamento retenc' in body:
                return True
            # Mensagem do aluno com palavra-chave clara
            recv_flag = m.get('received', False)
            if recv_flag:
                # aluno disse algo com keyword forte
                from_aluno = body
                for kw in ('cancelar', 'trancar', 'desistir', 'cancelamento',
                          'trancamento'):
                    if kw in from_aluno:
                        return True
            # Mensagem de evento do sistema com "transferido automaticamente para wesley"
            # (vinda do DCZ, nao da nossa nota)
            if 'transferido automaticamente para wesley' in body and \
               'retenção - agente ia' not in body and \
               'retencao - agente ia' not in body:
                # apenas se nao for a propria nota nossa (que tem esse texto)
                # — fallback: aceitar so se for mensagem NAO-interna
                if not bool(m.get('isInternal', False)):
                    return True
    # NOTA: a checagem da tag RETENTION_TAG_ID foi REMOVIDA (2026-06-01).
    # Tag fica "para sempre" no CRM e gerava falsos positivos. Use apenas
    # handoff_active OU evento recente no historico.
    return False


def _had_attendant_left_after_handoff(conv_id, msgs):
    """Detecta se o atendente que estava no handoff_active dessa conv SAIU
    (finalizou/foi removido) depois do handoff_active ser registrado.

    Caso reportado: Debora foi atribuida, marcou handoff_active(dispatch, Debora).
    Debora finalizou e foi removida. Mas handoff_active continuou na tabela
    com TTL 4h. Aluno voltou 2h depois -> bot prometeu 'Debora vai continuar'
    sendo que Debora ja nao estava mais na conv.

    Retorna (limpou, target_que_saiu) — limpou=True se removeu handoff stale.
    """
    if not msgs:
        return False, None
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return False, None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT motivo, target_attendant, created_at FROM handoff_active
            WHERE conv_id = %s AND expires_at > NOW()
            LIMIT 1
        """, (conv_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
    except Exception:
        return False, None
    if not row:
        return False, None
    motivo, target, ho_created_at = row
    # So aplica em handoffs de dispatch — outros tipos (supervisor_block,
    # escalate) tem semantica diferente.
    if motivo != 'dispatch' or not target:
        return False, None
    # procura no historico se "<target> finalizou" ou "Atendente <target> removido"
    # apareceu DEPOIS do handoff_created_at
    import re
    target_first = target.split()[0].lower()
    from datetime import datetime as _dt
    try:
        ho_ts = ho_created_at.timestamp() if hasattr(ho_created_at, 'timestamp') else 0
    except Exception:
        ho_ts = 0
    for m in msgs:
        body = (m.get('body') or m.get('text') or '').strip()
        if not body:
            continue
        body_lower = body.lower()
        match_close = ('finalizou o atendimento' in body_lower or
                       'atendente' in body_lower and 'removido' in body_lower)
        if not match_close:
            continue
        # confere se o nome do atendente que saiu bate com target
        # (regex para 'Beatriz Andrade removido' ou 'Beatriz Andrade finalizou')
        m_name = re.search(r'(?:atendente\s+)?([A-Z][a-zA-ZÀ-ÿ]+)', body)
        leaver = m_name.group(1).lower() if m_name else ''
        if leaver != target_first:
            continue
        # tem que ter sido APOS o handoff_active ser criado
        ts_str = m.get('createdAt') or m.get('timestamp') or ''
        try:
            if ts_str and ho_ts:
                msg_ts = _dt.fromisoformat(str(ts_str).replace('Z', '+00:00')).timestamp()
                if msg_ts < ho_ts:
                    continue
        except Exception:
            pass
        # bingo: o atendente do handoff saiu -> limpa handoff_active stale
        _clear_handoff_active(conv_id, reason=f'attendant_left:{target_first}')
        return True, target_first
    return False, None


def _had_close_event_recently(msgs, max_hours=2):
    """True se houve evento de encerramento nas ultimas N horas do historico."""
    if not msgs:
        return False
    from datetime import datetime as _dt
    now_ts = time.time()
    for m in msgs:
        body = (m.get('body') or m.get('text') or '').strip().lower()
        if not body:
            continue
        if any(p in body for p in _CLOSE_EVENT_PATTERNS):
            ts_str = m.get('createdAt') or m.get('timestamp') or ''
            try:
                if ts_str:
                    dt = _dt.fromisoformat(str(ts_str).replace('Z', '+00:00'))
                    age_h = (now_ts - dt.timestamp()) / 3600
                    if age_h > max_hours:
                        continue
            except Exception:
                pass
            return True
    return False


def _last_received_message(msgs):
    """Retorna a ultima mensagem RECEBIDA do aluno (received=true)."""
    if not msgs:
        return None
    for m in reversed(msgs):
        if m.get('received', False):
            body = (m.get('body') or m.get('text') or '').strip()
            if body and not body.startswith('[') and len(body) > 0:
                return m
    return None


def process_post_close_rescue():
    """Detecta conversas reabertas apos encerramento humano e age conforme conteudo:
    - despedida -> bot responde curto + finaliza novamente
    - duvida real -> tenta re-atribuir ao mesmo atendente (sticky); senao distribui
    Roda apenas dentro do horario comercial.
    """
    global student_profile, _current_phone
    if not is_within_business_hours():
        return
    try:
        convs = _fetch_active_conversations(limit_per_status=200, timeout=30)
    except Exception as e:
        p(f"  [POST-CLOSE-RESCUE] erro lista: {e}")
        return

    if not convs:
        return

    now_ts = time.time()
    rescued = 0
    for c in convs:
        try:
            cid = c.get('id', '')
            if not cid:
                continue
            inst = c.get('instance', {}) or {}
            iid = inst.get('id', '') if isinstance(inst, dict) else str(inst)
            if iid != INSTANCE_ACADEMICO_ID:
                continue
            statuses = c.get('statuses', []) or []
            if 'finished' in statuses:
                continue
            atts = c.get('attendants', []) or []
            if atts:
                continue
            recv = c.get('lastReceivedMessageDate', '') or ''
            sent = c.get('lastSendedMessageDate', '') or ''
            if not recv:
                continue
            # (2026-05-27) Caso Jucelia: helper distingue balao DCZ vs resposta humana
            if _should_skip_due_to_sent_after_recv(cid, recv, sent):
                continue
            try:
                from datetime import datetime as _dt
                dt_recv = _dt.fromisoformat(str(recv).replace('Z', '+00:00'))
                age_min = (time.time() - dt_recv.timestamp()) / 60
            except Exception:
                continue
            if age_min < POST_CLOSE_RESCUE_AGE_MIN or age_min > POST_CLOSE_RESCUE_MAX_AGE_MIN:
                continue
            last_done = _POST_CLOSE_RESCUE_RECENT.get(cid, 0)
            if last_done and (now_ts - last_done) < POST_CLOSE_RESCUE_COOLDOWN_S:
                continue

            try:
                msgs = get_conversation_messages_api(cid, limit=15)
            except Exception:
                msgs = []
            if not msgs:
                continue
            if not _had_close_event_recently(msgs, max_hours=2):
                continue

            last_user_msg = _last_received_message(msgs)
            if not last_user_msg:
                continue
            user_text = (last_user_msg.get('body') or last_user_msg.get('text') or '').strip()
            if not user_text:
                continue

            ct = c.get('contact', {}) or {}
            phone = (ct.get('phoneNumber', '') or ct.get('contactId', '') or '').replace('+', '').replace(' ', '')
            if phone.startswith('55') and len(phone) > 11:
                phone = phone[2:]
            name = (ct.get('name', '') or '').strip()
            first = name.split()[0] if name else ''
            first_part = f' *{first}*' if first else ''

            # (2026-05-26) Inclui _is_resolution_confirmation como gatilho
            # equivalente. Caso reportado: "Consegui entender as
            # explicacoes" ficou na fila aguardando — o post_close_rescue
            # nao reconhecia como motivo de fechamento.
            _is_pc_fw = _is_farewell_message(user_text)
            _is_pc_rc = _is_resolution_confirmation(user_text)
            # (2026-05-27) GUARD: sauda\u00e7\u00e3o pura nunca encerra.
            if _is_pure_greeting(user_text):
                _is_pc_fw = False; _is_pc_rc = False
            if _is_pc_fw or _is_pc_rc:
                if _is_pc_rc:
                    farewell_reply = (
                        f"Que ótimo{first_part}! Fico feliz que tenha conseguido resolver 😊\n\n"
                        f"Se precisar de mais alguma coisa, é só me chamar de novo. Até mais!"
                    )
                else:
                    farewell_reply = (
                        f"Obrigado pelo contato{first_part}! 🙏\n\n"
                        f"Estamos sempre por aqui — se precisar de qualquer outra coisa, "
                        f"é só me chamar de novo 😊"
                    )
                try:
                    meta_typing_on()
                    send_and_track(cid, farewell_reply)
                except Exception as e_msg:
                    p(f"  [POST-CLOSE-RESCUE] falha farewell: {e_msg}")

                try:
                    _kind_lbl = 'Confirmacao de resolucao' if _is_pc_rc else 'Despedida'
                    note = (
                        f"🙏 *{_kind_lbl} automatica* — aluno respondeu '{user_text[:50]}' "
                        f"apos encerramento. IA agradeceu e finalizou novamente."
                    )
                    requests.post(
                        f'{DCZ_API}/api/v1/conversations/{cid}/messages',
                        headers=H, json={'body': note, 'isInternal': True}, timeout=10,
                    )
                except Exception:
                    pass

                try:
                    close_conversation_crm(cid, phone=phone)
                except Exception as e_c:
                    p(f"  [POST-CLOSE-RESCUE] erro close: {e_c}")

                _POST_CLOSE_RESCUE_RECENT[cid] = now_ts
                rescued += 1
                _lbl_log = 'DESPEDIDA' if _is_pc_fw else 'RESOLUCAO'
                p(f"  [POST-CLOSE-RESCUE] {_lbl_log} conv={cid[:12]} ...{phone[-4:]} ({int(age_min)}min) '{user_text[:40]}' -> finalizada")
                continue

            last_attendant_first = _extract_last_attendant_from_history(msgs)
            consultant_used = None

            # (2026-05-28) PROTECAO RETENCAO: se conv passou por /retencao
            # (consultor mandou pra Wesley), NUNCA devolver para consultor
            # original. Caso reportado: Beatriz -> /retencao Wesley -> aluna
            # voltava para Beatriz. Forca sticky em Wesley.
            try:
                _lead_for_ret, _, _ = _ensure_lead_for_rescue(phone, name)
            except Exception:
                _lead_for_ret = None
            if _is_in_retention(cid, lead_id=_lead_for_ret, msgs=msgs):
                # (2026-06-08) sticky: mantem com o membro de retencao ja atribuido
                # (Wesley OU Danúbia); choose_retention_target resolve via handoff.
                _alvo_pc = choose_retention_target(cid)
                p(f"  [POST-CLOSE-RESCUE] Conv {cid[:12]} em RETENCAO — sticky {_alvo_pc or 'time de Retenção'}")
                msg = (
                    f"Oii{first_part}! Vi que voltou para falar com a gente 😊\n\n"
                    f"Vou pedir para o nosso *time de Retenção*, que cuida do seu caso, "
                    f"dar continuidade ao seu atendimento. Em pouquinho alguém assume aqui."
                )
                # (2026-06-25) modo automação RET-IA: não fala com o aluno (silêncio)
                if not _use_ret_ia_automation(phone):
                    try:
                        meta_typing_on()
                        send_and_track(cid, msg)
                    except Exception:
                        pass
                try:
                    trigger_retention(cid, _lead_for_ret, user_text, phone=phone)
                except Exception as e_rt:
                    p(f"  [POST-CLOSE-RESCUE] erro retention sticky: {e_rt}")
                try:
                    note = (
                        f"🔴 *Sticky retencao* — aluno voltou apos encerramento. "
                        f"Conv ja estava em retencao. Mantido com {_alvo_pc or 'time de Retenção'}."
                    )
                    requests.post(
                        f'{DCZ_API}/api/v1/conversations/{cid}/messages',
                        headers=H, json={'body': note, 'isInternal': True}, timeout=10,
                    )
                except Exception:
                    pass
                _POST_CLOSE_RESCUE_RECENT[cid] = now_ts
                rescued += 1
                continue

            if last_attendant_first and is_attendant_active_now(last_attendant_first):
                target = last_attendant_first.capitalize()
                msg = (
                    f"Oii{first_part}! Vi que voltou para falar com a gente 😊\n\n"
                    f"Vou pedir para o(a) *{target}*, que estava te atendendo, "
                    f"dar continuidade ao seu atendimento. Em pouquinho ele(a) assume aqui."
                )
                try:
                    meta_typing_on()
                    send_and_track(cid, msg)
                except Exception:
                    pass

                lead_id, biz_id, created_now = _ensure_lead_for_rescue(phone, name)
                try:
                    if lead_id:
                        _dcz_transfer_business(phone, target, lead_id=lead_id)
                        _dcz_transfer_lead(lead_id, target)
                    _dcz_transfer_chat(cid, target)
                    if created_now:
                        try:
                            requests.post(
                                f'{DCZ_API}/api/v1/conversations/{cid}/messages',
                                headers=H,
                                json={'body': "ℹ️ Lead criado automaticamente pelo resgate (não existia no CRM antes).", 'isInternal': True},
                                timeout=10,
                            )
                        except Exception:
                            pass
                except Exception as e_t:
                    p(f"  [POST-CLOSE-RESCUE] erro transfer sticky: {e_t}")

                try:
                    note = (
                        f"🔁 *Sticky last attendant* — aluno voltou apos encerramento. "
                        f"Re-atribuido a {target} (mesma pessoa que encerrou)."
                    )
                    requests.post(
                        f'{DCZ_API}/api/v1/conversations/{cid}/messages',
                        headers=H, json={'body': note, 'isInternal': True}, timeout=10,
                    )
                except Exception:
                    pass

                consultant_used = target
            else:
                consultant = get_available_consultant()
                if not consultant:
                    p(f"  [POST-CLOSE-RESCUE] sem consultor - registra pending")
                    try:
                        _current_phone = phone
                        student_profile = {'name': name, 'first_name': first, 'phone': phone}
                        _conv_states.setdefault(cid, _default_conv_state())['phone'] = phone
                        record_pending_escalation(
                            cid, reason='human_unavailable',
                            tier='pending',
                            retorno_label='assim que houver consultor disponivel',
                            question=f'[post-close - {user_text[:60]}]',
                        )
                    except Exception as e_pe:
                        p(f"  [POST-CLOSE-RESCUE] erro pending: {e_pe}")
                    _POST_CLOSE_RESCUE_RECENT[cid] = now_ts
                    continue

                consultant_name = consultant.get('nome', '')
                consultant_first = consultant_name.split()[0] if consultant_name else ''
                msg = (
                    f"Oii{first_part}! Vi que voltou para falar com a gente 😊\n\n"
                    f"Vou te conectar com o(a) *{consultant_first}*, que vai dar continuidade "
                    f"ao seu atendimento. Em pouquinho ele(a) assume aqui."
                )
                try:
                    meta_typing_on()
                    send_and_track(cid, msg)
                except Exception:
                    pass

                lead_id, biz_id, created_now = _ensure_lead_for_rescue(phone, name)
                try:
                    if lead_id:
                        _dcz_transfer_business(phone, consultant_name, lead_id=lead_id)
                        _dcz_transfer_lead(lead_id, consultant_name)
                    _dcz_transfer_chat(cid, consultant_name)
                    _supabase_increment_fila(consultant.get('id', ''), int(consultant.get('fila', 0)))
                    if created_now:
                        try:
                            requests.post(
                                f'{DCZ_API}/api/v1/conversations/{cid}/messages',
                                headers=H,
                                json={'body': "ℹ️ Lead criado automaticamente pelo resgate (não existia no CRM antes).", 'isInternal': True},
                                timeout=10,
                            )
                        except Exception:
                            pass
                except Exception as e_t:
                    p(f"  [POST-CLOSE-RESCUE] erro transferencia: {e_t}")

                try:
                    note = (
                        f"🔁 *Resgate pos-encerramento* — aluno voltou com nova duvida apos "
                        f"encerramento. Atendente anterior nao disponivel. "
                        f"Atribuido a {consultant_first} (menor fila)."
                    )
                    requests.post(
                        f'{DCZ_API}/api/v1/conversations/{cid}/messages',
                        headers=H, json={'body': note, 'isInternal': True}, timeout=10,
                    )
                except Exception:
                    pass

                consultant_used = consultant_first

            try:
                update_pending_escalation_status(
                    cid, 'resolved',
                    note=f'Resgate pos-encerramento - {consultant_used}',
                )
            except Exception:
                pass

            _POST_CLOSE_RESCUE_RECENT[cid] = now_ts
            rescued += 1
            p(f"  [POST-CLOSE-RESCUE] REABERTURA conv={cid[:12]} ...{phone[-4:]} ({int(age_min)}min) -> {consultant_used}")
        except Exception as e_one:
            p(f"  [POST-CLOSE-RESCUE] erro conv {c.get('id','?')[:12]}: {e_one}")

    if rescued:
        p(f"  [POST-CLOSE-RESCUE] Total tratadas: {rescued}")


# ===================== SUPERVISOR LOOP (follow-up / close via DCZ, sem depender de _conv_states) =====================
# IMPORTANTE: o supervisor SO envia texto. Nunca troca atendente, nunca mexe em CRM/pipeline.
# Pior caso: 1 mensagem a mais. Nunca pode causar caso tipo "Ana Paula" (atribuicao errada).
_SUPERVISOR_TABLE_READY = False
SUPERVISOR_STATUSES = ('open', 'opened')        # scan ambos status do DCZ
SUPERVISOR_MAX_FOLLOWUP_PER_CYCLE = 25          # era 8 - aumentado pra cobrir backlog
SUPERVISOR_MAX_CLOSE_PER_CYCLE = 15             # era 5
SUPERVISOR_MAX_FOLLOWUP_AGE_S = 4 * 3600        # NAO manda 1o follow-up se silencio > 4h (era 60min - cobre backlog matinal)
SUPERVISOR_MAX_CLOSE_AGE_S = 24 * 3600
SUPERVISOR_ACTION_COOLDOWN_S = 2 * 3600         # nao repete mesma acao na mesma conv em 2h
SUPERVISOR_HUMAN_GRACE_S = 5 * 60               # se humano respondeu nos ultimos 5min, supervisor nao mexe

# === Supervisor OpenAI: revisor periodico ===
# Roda em loop independente. Pega convs com 2+ mensagens recentes do bot,
# manda ao OpenAI pedindo classificacao de qualidade (repeticao, contradicao,
# falha do pre_opening, etc) e grava findings em agent_audit_findings.
# Custo controlado por intervalo + cap por ciclo.
OPENAI_SUPERVISOR_ENABLED = os.environ.get('OPENAI_SUPERVISOR_ENABLED', '1') in ('1', 'true', 'True')
OPENAI_SUPERVISOR_MODEL = os.environ.get('OPENAI_SUPERVISOR_MODEL', 'gpt-5.1')
OPENAI_SUPERVISOR_INTERVAL_S = int(os.environ.get('OPENAI_SUPERVISOR_INTERVAL_S', '180'))   # 3min
OPENAI_SUPERVISOR_MAX_CONVS = int(os.environ.get('OPENAI_SUPERVISOR_MAX_CONVS', '25'))     # por ciclo
OPENAI_SUPERVISOR_LOOKBACK_MIN = int(os.environ.get('OPENAI_SUPERVISOR_LOOKBACK_MIN', '120'))  # convs com atividade nos ultimos 2h
_last_openai_supervisor_ts = 0
_openai_supervisor_audited = {}  # conv_id -> ts da ultima auditoria nesse processo

# Telemetria do supervisor — usado para debug/observabilidade na aba Auditoria IA.
_openai_sup_stats = {
    'cycles': 0,             # quantos ciclos rodaram (process_openai_supervisor_loop)
    'last_cycle_at': 0,      # ts unix do ultimo ciclo
    'convs_listed': 0,       # convs achadas no DCZ no ultimo ciclo
    'audited_total': 0,      # convs efetivamente auditadas (chamada OpenAI feita) — acumulado
    'problems_found': 0,     # quantas vezes a OpenAI retornou tem_problema=true — acumulado
    'errors': 0,             # erros de chamada OpenAI — acumulado
    'last_error': '',        # ultima string de erro
    'last_results': [],      # ultimos 20 resultados {conv_id, tem_problema, tipo, severidade, when}
    'last_cycle_audited': 0, # quantas no ultimo ciclo
    'last_cycle_problems': 0,
}

_FOLLOWUP_BODY_MARKERS = (
    'ainda está por aí', 'ainda esta por ai', 'tudo certo por aí', 'tudo certo por ai',
    'se tiver mais alguma dúvida', 'se precisar de mais alguma coisa',
)
_HANDOFF_BODY_MARKERS = (
    'vou te transferir', 'vou te conectar', 'vou pedir para', 'distribuição automática',
)


def _ensure_supervisor_table():
    global _SUPERVISOR_TABLE_READY
    if _SUPERVISOR_TABLE_READY:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supervisor_actions (
                id SERIAL PRIMARY KEY,
                conv_id VARCHAR(64) NOT NULL,
                action VARCHAR(32) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_supervisor_conv_action
            ON supervisor_actions (conv_id, action, created_at DESC)
        """)
        conn.commit()
        cur.close()
        conn.close()
        _SUPERVISOR_TABLE_READY = True
    except Exception as e:
        p(f"  [SUPERVISOR] tabela indisponivel (memoria only): {e}")


def _supervisor_recent_action(conv_id, action, cooldown_s=SUPERVISOR_ACTION_COOLDOWN_S):
    _ensure_supervisor_table()
    if not _SUPERVISOR_TABLE_READY:
        return False
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT 1 FROM supervisor_actions
            WHERE conv_id = %s AND action = %s
              AND created_at > NOW() - (%s || ' seconds')::interval
            LIMIT 1
        """, (conv_id, action, str(int(cooldown_s))))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row is not None
    except Exception:
        return False


def _supervisor_record_action(conv_id, action):
    _ensure_supervisor_table()
    if not _SUPERVISOR_TABLE_READY:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO supervisor_actions (conv_id, action) VALUES (%s, %s)",
            (conv_id, action),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def _iso_age_seconds(iso_ts):
    if not iso_ts:
        return None
    try:
        from datetime import datetime as _dt
        dt = _dt.fromisoformat(str(iso_ts).replace('Z', '+00:00'))
        return time.time() - dt.timestamp()
    except Exception:
        return None


# ============== DEDUP PERSISTENTE E HANDOFF ATIVO ==============
# Sobrevive a restart do agente. Centraliza:
#   - signatures: assinaturas de mensagens enviadas (motivo+conv) p/ evitar duplicar
#   - handoff_active: marca conversas onde houve handoff humanizado (Wesley, transfer,
#     fora-do-horario) p/ o agente principal NAO continuar respondendo at[e humano
#     assumir ou TTL expirar.
_DEDUP_TABLES_READY = False


def _ensure_dedup_tables():
    global _DEDUP_TABLES_READY
    if _DEDUP_TABLES_READY:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_sent_signatures (
                id SERIAL PRIMARY KEY,
                conv_id VARCHAR(64) NOT NULL,
                signature VARCHAR(128) NOT NULL,
                body_hash VARCHAR(64),
                sent_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_sig_conv_sig_sent
            ON agent_sent_signatures (conv_id, signature, sent_at DESC)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS handoff_active (
                conv_id VARCHAR(64) PRIMARY KEY,
                motivo VARCHAR(64) NOT NULL,
                target_attendant VARCHAR(64),
                body_hash VARCHAR(64),
                created_at TIMESTAMP DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        _DEDUP_TABLES_READY = True
    except Exception as e:
        p(f"  [DEDUP] tabelas indisponiveis: {e}")


def _hash_body(body):
    if not body:
        return ''
    import hashlib
    return hashlib.sha1(body.strip().lower().encode('utf-8', errors='ignore')).hexdigest()[:32]


def _normalize_body_for_dedup(text):
    """Normaliza texto para hash anti-repeticao:
    - lowercase
    - sem acentos
    - sem pontuacao
    - sem palavras curtas tipicas de nome (capitalizadas no original)
    - espacos colapsados
    Mantem o esqueleto semantico estavel mesmo se LLM trocar nome ou virgulas.
    """
    if not text:
        return ''
    import unicodedata
    import re
    raw = text.strip()
    # tira variantes capitalizadas longas (provaveis nomes proprios) que mudam entre chamadas
    tokens_no_proper = []
    for w in raw.split():
        bare = w.strip('.,;:!?()[]"\'*_~`')
        if bare and bare[0].isupper() and len(bare) >= 3 and bare.lower() not in (
            'oi', 'olá', 'ola', 'opa', 'pelo', 'pela', 'voce', 'voc\u00ea', 'voce.', 'sim',
            'nao', 'não', 'claro', 'sobre', 'aqui', 'agora', 'esse', 'isso', 'pode',
            'preciso', 'tudo', 'bom', 'boa', 'avaliação', 'avaliacao', 'remuneração',
            'remuneracao', 'estratégica', 'estrategica', 'regimental',
        ):
            # provavel nome proprio - troca por marcador
            tokens_no_proper.append('<NOME>')
        else:
            tokens_no_proper.append(w)
    cleaned = ' '.join(tokens_no_proper)
    cleaned = ''.join(c for c in unicodedata.normalize('NFD', cleaned)
                      if unicodedata.category(c) != 'Mn')
    cleaned = cleaned.lower()
    cleaned = re.sub(r'[^a-z0-9<> ]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # pega so os primeiros 280 chars: o miolo da resposta carrega a semantica
    return cleaned[:280]


def _ensure_body_norm_column():
    """Migration leve: adiciona coluna body_norm em agent_sent_signatures se nao existe.
    Necessario para dedup por similaridade semantica (alem do hash exato)."""
    if not _DEDUP_TABLES_READY:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            ALTER TABLE agent_sent_signatures
            ADD COLUMN IF NOT EXISTS body_norm TEXT
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


SIMILARITY_DEDUP_THRESHOLD = 0.65  # SequenceMatcher: 65% de similaridade char-by-char
JACCARD_DEDUP_THRESHOLD = 0.40     # Jaccard: 40% de palavras unicas (sem stopwords) em comum
JACCARD_MIN_WORDS_IN_COMMON = 6    # salvaguarda contra falsos positivos em mensagens curtas
# (2026-05-25) Bug Sandra/Ivanice: bot enviou multiplas respostas com mesmo
# conteudo semantico mas parafraseadas (descricao de imagem da disciplina,
# orientacao de matricula). Os thresholds antigos (0.78/0.50) deixavam
# passar. Baixar pra 0.65/0.40 cobre parafrase mantendo seguranca contra
# falsos positivos (>=6 palavras em comum + min 40 chars).
_DEDUP_STOPWORDS = frozenset({
    'a', 'o', 'e', 'de', 'do', 'da', 'em', 'um', 'uma', 'na', 'no', 'os', 'as',
    'que', 'por', 'se', 'com', 'para', 'pra', 'pelo', 'pela', 'mais', 'mas',
    'ne', 'voce', 'ele', 'ela', 'nos', 'sua', 'seu', 'sim', 'nao', 'tem', 'ter',
    'foi', 'sao', 'tao', 'so', 'la', 'ja', 'ai',
})


def _jaccard_similarity(norm_a, norm_b):
    """Similaridade de Jaccard sobre conjuntos de palavras unicas (sem stopwords).
    Bom para detectar parafrase semantica (mesmas palavras-chave em ordens diferentes).
    Retorna (ratio, len_intersection)."""
    sa = {w for w in norm_a.split() if len(w) >= 3 and w not in _DEDUP_STOPWORDS}
    sb = {w for w in norm_b.split() if len(w) >= 3 and w not in _DEDUP_STOPWORDS}
    if not sa or not sb:
        return 0.0, 0
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union), len(inter)


def _normalize_for_similarity(text):
    """Normalizacao MENOS agressiva que _normalize_body_for_dedup.

    Diferenca chave: NAO substitui nomes proprios por <NOME>. Isso preserva
    a estrutura semantica da resposta — o algoritmo de similaridade
    (SequenceMatcher) lida bem com troca de tokens, mas perde quando metade
    do texto vira tokens identicos genericos.

    Esta versao:
    - lowercase, sem acentos, sem pontuacao
    - colapsa espacos
    - mantem palavras intactas (Naiara fica naiara)
    Usada SO em dedup por similaridade. _normalize_body_for_dedup continua
    sendo usada em hash exato (camada 1) por compat retroativa.
    """
    if not text:
        return ''
    import unicodedata, re
    cleaned = text.strip()
    cleaned = ''.join(c for c in unicodedata.normalize('NFD', cleaned)
                      if unicodedata.category(c) != 'Mn')
    cleaned = cleaned.lower()
    cleaned = re.sub(r'[^a-z0-9 ]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:400]


def _body_recently_sent(conv_id, text, window_s=10 * 60):
    """True se body ja foi enviado nessa conv dentro da janela.

    DUAS CAMADAS:
    1) Hash exato do texto normalizado (rapido, caso comum quando LLM
       gera mesma resposta ipsis verbis)
    2) Similaridade SequenceMatcher >= SIMILARITY_DEDUP_THRESHOLD (caso
       reportado: LLM gerou 2 respostas com sinonimos/reordenacao -
       hashes diferentes mas mesmo conteudo semantico)

    Persiste body_norm em agent_sent_signatures (sobrevive a restart).
    """
    if not conv_id or not text:
        return False
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return False
    _ensure_body_norm_column()
    norm = _normalize_body_for_dedup(text)
    if not norm:
        return False
    h = _hash_body(norm)
    try:
        conn = get_db()
        cur = conn.cursor()
        # Camada 1: hash exato (rapido)
        cur.execute("""
            SELECT signature, sent_at FROM agent_sent_signatures
            WHERE conv_id = %s AND body_hash = %s
              AND sent_at > NOW() - (%s || ' seconds')::interval
            ORDER BY sent_at DESC
            LIMIT 1
        """, (conv_id, h, str(int(window_s))))
        if cur.fetchone():
            cur.close()
            conn.close()
            return True
        # Camada 2: similaridade — busca ate 8 bodies_norm recentes da conv
        # body_norm guarda a normalizacao "soft" (sem mascarar nomes proprios)
        # para que SequenceMatcher tenha estrutura semantica preservada.
        sim_norm = _normalize_for_similarity(text)
        if len(sim_norm) < 40:
            cur.close()
            conn.close()
            return False
        cur.execute("""
            SELECT body_norm FROM agent_sent_signatures
            WHERE conv_id = %s
              AND body_norm IS NOT NULL
              AND LENGTH(body_norm) >= 40
              AND sent_at > NOW() - (%s || ' seconds')::interval
            ORDER BY sent_at DESC
            LIMIT 20
        """, (conv_id, str(int(window_s))))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if rows:
            from difflib import SequenceMatcher
            for row in rows:
                prev_norm = (row[0] or '')[:400]
                if not prev_norm or len(prev_norm) < 40:
                    continue
                # Metrica 1: SequenceMatcher (char-by-char) - bom pra mensagens
                # praticamente iguais com pontuacao/espacos diferentes.
                ratio = SequenceMatcher(None, sim_norm, prev_norm).ratio()
                if ratio >= SIMILARITY_DEDUP_THRESHOLD:
                    try:
                        p(f"  [DEDUP-SIM-CHAR] {conv_id[:12]} ratio={ratio:.2f} >= {SIMILARITY_DEDUP_THRESHOLD} - SUPRIMIDO")
                    except Exception:
                        pass
                    return True
                # Metrica 2: Jaccard de palavras unicas - bom pra parafrase
                # (mesmo conteudo semantico com palavras diferentes/reordenadas)
                jac, inter = _jaccard_similarity(sim_norm, prev_norm)
                if jac >= JACCARD_DEDUP_THRESHOLD and inter >= JACCARD_MIN_WORDS_IN_COMMON:
                    try:
                        p(f"  [DEDUP-SIM-JACC] {conv_id[:12]} jaccard={jac:.2f} interseccao={inter} - SUPRIMIDO")
                    except Exception:
                        pass
                    return True
        return False
    except Exception:
        return False


def _register_body(conv_id, text, signature='body'):
    """Registra body em agent_sent_signatures (anti-repeticao persistente).

    Persiste DUAS normalizacoes:
    - body_hash: hash da normalizacao "hard" (com <NOME>) para match exato
    - body_norm: normalizacao "soft" (com nomes preservados) para similaridade
    """
    if not conv_id or not text:
        return
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return
    _ensure_body_norm_column()
    norm_hard = _normalize_body_for_dedup(text)
    if not norm_hard:
        return
    norm_soft = _normalize_for_similarity(text)
    h = _hash_body(norm_hard)
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO agent_sent_signatures (conv_id, signature, body_hash, body_norm) "
            "VALUES (%s, %s, %s, %s)",
            (conv_id, signature, h, norm_soft[:400]),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


# Lock por conversa para serializar envio (anti-race condition)
_conv_send_locks = {}
_conv_send_locks_mutex = __import__('threading').Lock()


def _get_conv_send_lock(conv_id):
    with _conv_send_locks_mutex:
        lock = _conv_send_locks.get(conv_id)
        if lock is None:
            lock = __import__('threading').Lock()
            _conv_send_locks[conv_id] = lock
        return lock


def _signature_recently_sent(conv_id, signature, window_s=24 * 3600, body_hash=''):
    """True se mesma signature ja foi enviada na conv dentro da janela.
    Se body_hash fornecido, exige tambem match do hash (mesmo motivo + mesmo corpo)."""
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return False
    try:
        conn = get_db()
        cur = conn.cursor()
        if body_hash:
            cur.execute("""
                SELECT 1 FROM agent_sent_signatures
                WHERE conv_id = %s AND signature = %s AND body_hash = %s
                  AND sent_at > NOW() - (%s || ' seconds')::interval
                LIMIT 1
            """, (conv_id, signature, body_hash, str(int(window_s))))
        else:
            cur.execute("""
                SELECT 1 FROM agent_sent_signatures
                WHERE conv_id = %s AND signature = %s
                  AND sent_at > NOW() - (%s || ' seconds')::interval
                LIMIT 1
            """, (conv_id, signature, str(int(window_s))))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row is not None
    except Exception:
        return False


def _register_signature(conv_id, signature, body=''):
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return
    try:
        h = _hash_body(body)
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO agent_sent_signatures (conv_id, signature, body_hash) VALUES (%s, %s, %s)",
            (conv_id, signature, h),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def _is_handoff_active(conv_id):
    """Retorna (motivo, target) se ha handoff ativo nao expirado, senao (None, None)."""
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return None, None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT motivo, target_attendant FROM handoff_active
            WHERE conv_id = %s AND expires_at > NOW()
            LIMIT 1
        """, (conv_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return row[0], row[1] or ''
    except Exception:
        pass
    return None, None


# Motivos onde um HUMANO especifico esta cuidando da conversa. Sobrescrever
# handoff_active nestes casos remove a 'protecao' do humano e abre porta para
# o bot ou o supervisor mover a conversa pra outro consultor (bug Wesley/Julia).
_HUMAN_HANDOFF_MOTIVOS = {'retention', 'preferred', 'dispatch', 'pre_opening_queue'}


def _mark_handoff_active(conv_id, motivo, target='', ttl_s=12 * 3600, body='',
                         protect_human=True):
    """Marca/atualiza handoff_active para a conversa.

    protect_human (default True): se ja existe handoff ATIVO com motivo de
    humano em _HUMAN_HANDOFF_MOTIVOS e target preenchido, NAO sobrescreve.
    Apenas estende o TTL. Isso evita que o supervisor 'expulse' consultor de
    retencao/preferred ao silenciar o bot. Passe protect_human=False quando
    REALMENTE quiser substituir (ex: usuario clicar 'Liberar agente').
    """
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return
    try:
        h = _hash_body(body)
        conn = get_db()
        cur = conn.cursor()

        if protect_human and motivo not in _HUMAN_HANDOFF_MOTIVOS:
            # Checa estado atual antes de sobrescrever.
            try:
                cur.execute("""
                    SELECT motivo, target_attendant, expires_at
                    FROM handoff_active
                    WHERE conv_id = %s AND expires_at > NOW()
                """, (conv_id,))
                existing = cur.fetchone()
            except Exception:
                existing = None
            if existing:
                ex_motivo = (existing[0] or '').strip()
                ex_target = (existing[1] or '').strip()
                if ex_motivo in _HUMAN_HANDOFF_MOTIVOS and ex_target:
                    # Apenas estende TTL do handoff humano existente;
                    # NAO troca motivo nem target. Bot continua silenciado
                    # mas o "dono" da conversa permanece o humano original.
                    cur.execute("""
                        UPDATE handoff_active
                        SET expires_at = GREATEST(
                            expires_at,
                            NOW() + (%s || ' seconds')::interval
                        )
                        WHERE conv_id = %s
                    """, (str(int(ttl_s)), conv_id))
                    conn.commit()
                    cur.close()
                    conn.close()
                    p(f"  [HANDOFF-ACTIVE] {conv_id[:12]} PROTEGIDO: humano {ex_target} ({ex_motivo}) mantido (motivo novo '{motivo}' ignorado)")
                    return

        cur.execute("""
            INSERT INTO handoff_active (conv_id, motivo, target_attendant, body_hash, expires_at)
            VALUES (%s, %s, %s, %s, NOW() + (%s || ' seconds')::interval)
            ON CONFLICT (conv_id) DO UPDATE SET
                motivo = EXCLUDED.motivo,
                target_attendant = EXCLUDED.target_attendant,
                body_hash = EXCLUDED.body_hash,
                created_at = NOW(),
                expires_at = EXCLUDED.expires_at
        """, (conv_id, motivo, target or '', h, str(int(ttl_s))))
        conn.commit()
        cur.close()
        conn.close()
        p(f"  [HANDOFF-ACTIVE] {conv_id[:12]} motivo={motivo} target={target} ttl={ttl_s}s")
    except Exception as e:
        p(f"  [HANDOFF-ACTIVE] erro marcar: {e}")


def _try_acquire_dispatch_lock(conv_id, target, ttl_s=4 * 3600):
    """Tenta adquirir lock atomico de dispatch para conv_id no Postgres.

    Retorna True se ESTE chamador adquiriu o lock (deve prosseguir com a
    distribuicao). Retorna False se ja havia um dispatch ATIVO para esta
    conv (outro chamador concorrente venceu — deve fazer skip silencioso).

    Implementacao: INSERT ... ON CONFLICT DO UPDATE com WHERE filtrando
    dispatch ativo. Se ja existe dispatch ativo (motivo='dispatch' e nao
    expirado), o UPDATE nao roda e nada eh retornado.
    """
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        # Fallback: sem tabela, deixa passar (modo legado)
        return True
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO handoff_active (conv_id, motivo, target_attendant, body_hash, expires_at)
            VALUES (%s, 'dispatch', %s, %s, NOW() + (%s || ' seconds')::interval)
            ON CONFLICT (conv_id) DO UPDATE SET
                motivo = 'dispatch',
                target_attendant = EXCLUDED.target_attendant,
                body_hash = EXCLUDED.body_hash,
                created_at = NOW(),
                expires_at = EXCLUDED.expires_at
            WHERE handoff_active.motivo <> 'dispatch'
               OR handoff_active.expires_at <= NOW()
            RETURNING conv_id
        """, (conv_id, target or '', _hash_body('dispatch_lock'), str(int(ttl_s))))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if row:
            p(f"  [DISPATCH-LOCK] {conv_id[:12]} ADQUIRIDO target={target}")
            return True
        p(f"  [DISPATCH-LOCK] {conv_id[:12]} PERDIDO (ja havia dispatch ativo) target={target}")
        return False
    except Exception as e:
        p(f"  [DISPATCH-LOCK] erro: {e}")
        # Em caso de erro, prefere nao duplicar — bloqueia
        return False


_dispatch_inproc_locks = {}
_dispatch_inproc_mutex = __import__('threading').Lock()


def _get_dispatch_inproc_lock(conv_id):
    """Lock thread-local para serializar distribute_to_attendant no MESMO
    processo. Combinado com _try_acquire_dispatch_lock (Postgres) garante
    serializacao tanto entre threads quanto entre processos."""
    with _dispatch_inproc_mutex:
        lock = _dispatch_inproc_locks.get(conv_id)
        if lock is None:
            lock = __import__('threading').Lock()
            _dispatch_inproc_locks[conv_id] = lock
        return lock


def _ensure_audit_table():
    """Cria tabela de findings do supervisor OpenAI."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_audit_findings (
                id SERIAL PRIMARY KEY,
                conv_id VARCHAR(64) NOT NULL,
                phone VARCHAR(32),
                model VARCHAR(64),
                severity VARCHAR(16) NOT NULL,
                problem_type VARCHAR(64),
                summary TEXT,
                detail JSONB,
                action_taken VARCHAR(64),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_conv_created
            ON agent_audit_findings (conv_id, created_at DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_severity_created
            ON agent_audit_findings (severity, created_at DESC)
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        p(f"  [AUDIT] tabela indisponivel: {e}")


def _record_audit_finding(conv_id, severity, problem_type, summary, detail=None,
                          action_taken='', phone='', model=''):
    _ensure_audit_table()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_audit_findings
            (conv_id, phone, model, severity, problem_type, summary, detail, action_taken)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        """, (conv_id, phone or '', model or '', severity, problem_type or '',
              (summary or '')[:2000],
              json.dumps(detail or {}, ensure_ascii=False),
              action_taken or ''))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        p(f"  [AUDIT] erro registrar: {e}")


def _clear_handoff_active(conv_id, reason=''):
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM handoff_active WHERE conv_id = %s", (conv_id,))
        conn.commit()
        cur.close()
        conn.close()
        if reason:
            p(f"  [HANDOFF-ACTIVE] {conv_id[:12]} limpo ({reason})")
    except Exception:
        pass


def _body_has_marker(body, markers):
    b = (body or '').lower()
    return any(m in b for m in markers)


def _last_outbound_from_msgs(msgs):
    """Ultima mensagem enviada (bot/atendente), mais recente primeiro na API."""
    if not isinstance(msgs, list):
        return None
    for m in msgs:
        if m.get('isInternal'):
            continue
        if m.get('received', False):
            continue
        body = (m.get('body') or m.get('text') or '').strip()
        if body or _message_has_thread_payload(m):
            return m
    return None


def _msg_is_from_human(m):
    """True se a mensagem foi enviada por um atendente humano (nao bot/automacao).
    Na API DCZ, msg do humano tem attendant={id,name,...}; msg do bot tem attendant=None.
    """
    if not isinstance(m, dict):
        return False
    att = m.get('attendant')
    if isinstance(att, dict) and (att.get('id') or att.get('userId')):
        return True
    return False


def _last_human_outbound(msgs):
    """Pega a ultima outbound que foi enviada por um humano (nao bot)."""
    if not isinstance(msgs, list):
        return None
    for m in msgs:
        if m.get('isInternal') or m.get('received', False):
            continue
        if _msg_is_from_human(m):
            return m
    return None


def _human_recently_active(msgs, grace_s):
    """True se algum atendente humano enviou mensagem nos ultimos grace_s segundos."""
    human_msg = _last_human_outbound(msgs)
    if not human_msg:
        return False
    age = _iso_age_seconds(human_msg.get('createdAt') or human_msg.get('updatedAt') or '')
    if age is None:
        return False
    return age <= grace_s


def _sync_conv_state_after_supervisor(cid, phone, followup_stage, waiting=True):
    st = _conv_states.setdefault(cid, _default_conv_state())
    st['phone'] = phone or st.get('phone', '')
    st['waiting_for_client'] = waiting
    st['followup_stage'] = followup_stage
    st['inactivity_start'] = time.time()
    st['_human_took_over'] = False


def _supervisor_fetch_convs():
    """Junta conversas de todos os SUPERVISOR_STATUSES com dedup por id."""
    seen = {}
    for status in SUPERVISOR_STATUSES:
        try:
            r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                             params={'limit': 250, 'status': status}, timeout=35)
            if r.status_code != 200:
                continue
            data = r.json()
            convs = data.get('data', data) if isinstance(data, dict) else data
            for c in (convs if isinstance(convs, list) else []):
                cid = c.get('id') or c.get('_id')
                if not cid or cid in seen:
                    continue
                seen[cid] = c
        except Exception as e:
            p(f"  [SUPERVISOR] erro lista status={status}: {e}")
    return list(seen.values())


def _supervisor_has_attendant_fresh(cid):
    """Re-fetch da conversa pra confirmar que humano nao assumiu.
    Retorna True (= bloqueia envio) se humano respondeu nos ultimos SUPERVISOR_HUMAN_GRACE_S
    ou a conv foi finalizada. Conv com humano atribuido mas inativo NAO bloqueia.
    """
    try:
        r = requests.get(f'{DCZ_MSG}/messaging/conversations/{cid}',
                         headers=H, timeout=10)
        if r.status_code != 200:
            return True  # seguranca: se falhou consulta, considera com atendente
        data = r.json() or {}
        if 'finished' in (data.get('statuses', []) or []):
            return True
        if not (data.get('attendants') or []):
            return False
        try:
            msgs = get_conversation_messages_api(cid, limit=8)
        except Exception:
            return True
        if _human_recently_active(msgs, SUPERVISOR_HUMAN_GRACE_S):
            return True
    except Exception:
        return True
    return False


def process_supervisor_loop():
    """Varredura independente da memoria: garante follow-up e encerramento por inatividade.

    GARANTIAS:
    - SO envia texto (FOLLOWUP_1_MSG ou CLOSE_INACTIVITY_MSG). Nunca troca atendente,
      nunca move pipeline, nunca toca em CRM/lead.
    - Filtra rigidamente por INSTANCE_ACADEMICO_ID.
    - Pula qualquer conversa com atendentes (dupla checagem: lista + re-fetch).
    - Pula se ja teve acao igual nas ultimas 2h (supervisor_actions).
    - Nao manda follow-up se silencio > 60min (evita ping tardio em conversa antiga).
    - Pula handoff/template/encerramentos ja enviados.
    """
    fu_done = 0
    close_done = 0
    convs = _supervisor_fetch_convs()
    if not convs:
        return

    # ordenar por silencio crescente: prioriza conversas frescas (10-30min) sobre antigas
    enriched = []
    for c in convs:
        sent = c.get('lastSendedMessageDate', '') or ''
        sil = _iso_age_seconds(sent)
        if sil is None or sil < FOLLOWUP_1_DELAY:
            continue
        enriched.append((sil, c))
    enriched.sort(key=lambda x: x[0])  # mais novos primeiro

    for silence_s, c in enriched:
        if close_done >= SUPERVISOR_MAX_CLOSE_PER_CYCLE and fu_done >= SUPERVISOR_MAX_FOLLOWUP_PER_CYCLE:
            break
        try:
            cid = c.get('id', '')
            if not cid:
                continue
            inst = c.get('instance', {}) or {}
            iid = inst.get('id', '') if isinstance(inst, dict) else str(inst)
            if iid != INSTANCE_ACADEMICO_ID:
                continue
            if 'finished' in (c.get('statuses', []) or []):
                continue

            recv = c.get('lastReceivedMessageDate', '') or ''
            sent = c.get('lastSendedMessageDate', '') or ''
            if not sent:
                continue
            if recv and recv > sent:
                continue

            ct = c.get('contact', {}) or {}
            phone = (ct.get('phoneNumber', '') or ct.get('contactId', '') or '').replace('+', '').replace(' ', '')
            if phone.startswith('55') and len(phone) > 11:
                phone = phone[2:]
            name = (ct.get('name', '') or '').strip()
            first = name.split()[0] if name else ''
            name_fmt = f", {first}" if first else ""

            try:
                msgs = get_conversation_messages_api(cid, limit=12)
            except Exception:
                msgs = []
            last_out = _last_outbound_from_msgs(msgs)
            last_body = (last_out.get('body') or last_out.get('text') or '') if last_out else ''

            # REGRA: se conv tem atendente atribuido E ultima outbound foi do humano,
            # supervisor NAO interfere (humano esta cuidando). Mas se a ultima outbound
            # foi do bot/automacao, a conv esta efetivamente parada: liberar supervisor.
            has_human_attendant = bool(c.get('attendants', []))
            last_was_human = _msg_is_from_human(last_out) if last_out else False
            if has_human_attendant:
                if last_was_human:
                    # humano respondeu por ultimo: nao mexer
                    continue
                # humano atribuido mas ultima outbound foi do bot - so age se humano
                # esta inativo ha pelo menos SUPERVISOR_HUMAN_GRACE_S
                if _human_recently_active(msgs, SUPERVISOR_HUMAN_GRACE_S):
                    continue

            _tlm = c.get('lastMessage') or {}
            if isinstance(_tlm, dict) and _is_template_message(_tlm):
                continue
            if last_out and _is_template_message(last_out):
                continue

            is_handoff_msg = _body_has_marker(last_body, _HANDOFF_BODY_MARKERS)
            had_followup = _body_has_marker(last_body, _FOLLOWUP_BODY_MARKERS)

            # --- Estagio 2a: encerrar apos follow-up sem resposta ---
            close_after_followup = (
                silence_s >= CLOSE_DELAY
                and silence_s <= SUPERVISOR_MAX_CLOSE_AGE_S
                and had_followup
            )
            # --- Estagio 2b: encerrar conv orfa (handoff/tutorial sem nenhuma resposta)
            #     mesmo sem follow-up - apos 2x CLOSE_DELAY (30min) - evita ficar parada
            close_orphan = (
                silence_s >= CLOSE_DELAY * 2
                and silence_s <= SUPERVISOR_MAX_CLOSE_AGE_S
                and not had_followup
                and (is_handoff_msg or has_human_attendant)
            )
            if close_done < SUPERVISOR_MAX_CLOSE_PER_CYCLE and (close_after_followup or close_orphan):
                if _supervisor_recent_action(cid, 'auto_close'):
                    continue
                lb_low = (last_body or '').lower()
                if any(fp.lower() in lb_low for fp in LAST_MSG_CLOSE_PHRASES):
                    continue
                if _supervisor_has_attendant_fresh(cid):
                    p(f"  [SUPERVISOR-CLOSE] skip ...{phone[-4:] if phone else '????'} humano ativo / finalizada")
                    continue
                # ACAO C (2026-05-21): NAO encerrar quando handoff_active vigente.
                # Bug: bot encerrava conv depois que humano foi prometido mas ainda
                # nao respondeu, gerando 'sobre_resposta' e 'perdido_conversa'.
                ho_motivo_cl, _ = _is_handoff_active(cid)
                if ho_motivo_cl:
                    p(f"  [SUPERVISOR-CLOSE] skip ...{phone[-4:] if phone else '????'} handoff_active={ho_motivo_cl}")
                    continue
                # ACAO E (2026-05-21): NAO encerrar se aluno respondeu APOS o
                # ultimo envio do bot. silence_s eh baseado em sent_ts e nao
                # detecta resposta nova do aluno depois dela. Bug: bot fechava
                # conv com pergunta nova do aluno aberta. 35 casos perdido_conversa.
                _recv_ts_sup = c.get('lastReceivedMessageDate', '') or ''
                _sent_ts_sup = c.get('lastSendedMessageDate', '') or ''
                if _recv_ts_sup and _sent_ts_sup and _recv_ts_sup > _sent_ts_sup:
                    p(f"  [SUPERVISOR-CLOSE] skip ...{phone[-4:] if phone else '????'} aluno respondeu apos ultimo envio - NAO encerra")
                    continue
                close_msg = CLOSE_INACTIVITY_MSG.format(name=name_fmt)
                tag = 'pos-follow-up' if close_after_followup else 'orfa-handoff'
                # DEDUP: nao reenvia close se ja registrado pelo supervisor (mesmo apos restart)
                if _signature_recently_sent(cid, 'auto_close', window_s=24 * 3600):
                    p(f"  [SUPERVISOR-CLOSE] dedup ja registrado - skip")
                    continue
                p(f"  [SUPERVISOR-CLOSE] ...{phone[-4:] if phone else '????'} {int(silence_s)}s {tag}")
                send_message_crm(cid, close_msg, buttons=CLOSE_INACTIVITY_BUTTONS)
                log_to_db(cid, '(supervisor)', close_msg, 1.0, 'auto_close')
                _register_signature(cid, 'auto_close', close_msg)
                close_conversation_crm(cid, phone=phone)
                _supervisor_record_action(cid, 'auto_close')
                _sync_conv_state_after_supervisor(cid, phone, followup_stage=0, waiting=False)
                _clear_handoff_active(cid, reason='auto_close')
                conversation_greeted.discard(cid)
                close_done += 1
                continue

            # Apos handoff, NAO mandamos follow-up de cortesia "tudo certo por ai" (parece estranho).
            # O close_orphan acima cuida do caso parado.
            if is_handoff_msg:
                continue

            # --- Estagio 1: primeiro follow-up ---
            if (fu_done < SUPERVISOR_MAX_FOLLOWUP_PER_CYCLE
                    and silence_s >= FOLLOWUP_1_DELAY
                    and silence_s <= SUPERVISOR_MAX_FOLLOWUP_AGE_S
                    and not had_followup):
                # NAO manda follow-up se handoff vigente (Wesley/transferencia/etc)
                ho_motivo_fu, _ = _is_handoff_active(cid)
                if ho_motivo_fu:
                    p(f"  [SUPERVISOR-FU1] skip ...{phone[-4:] if phone else '????'} handoff_active={ho_motivo_fu}")
                    continue
                if _supervisor_recent_action(cid, 'followup_1'):
                    continue
                # DEDUP persistente: nao reenvia follow-up igual mesmo apos restart
                if _signature_recently_sent(cid, 'followup_1', window_s=24 * 3600):
                    p(f"  [SUPERVISOR-FU1] dedup ja registrado - skip")
                    continue
                if _supervisor_has_attendant_fresh(cid):
                    p(f"  [SUPERVISOR-FU1] skip ...{phone[-4:] if phone else '????'} ganhou atendente entre lista e envio")
                    continue
                msg1 = FOLLOWUP_1_MSG.format(name=name_fmt)
                p(f"  [SUPERVISOR-FU1] ...{phone[-4:] if phone else '????'} {int(silence_s)}s sem resposta")
                send_message_crm(cid, msg1, buttons=FOLLOWUP_1_BUTTONS)
                log_to_db(cid, '(supervisor)', msg1, 1.0, 'followup_1')
                _register_signature(cid, 'followup_1', msg1)
                _supervisor_record_action(cid, 'followup_1')
                _sync_conv_state_after_supervisor(cid, phone, followup_stage=1, waiting=True)
                fu_done += 1
        except Exception as e_one:
            p(f"  [SUPERVISOR] erro conv {c.get('id', '?')[:12]}: {e_one}")

    if fu_done or close_done:
        p(f"  [SUPERVISOR] follow-up={fu_done} encerramentos={close_done}")


# ============== SUPERVISOR OPENAI (revisor de qualidade) ==============
# Auditoria periodica das conversas. Procura por:
#   - repeticao_resposta: bot mandou a "mesma" coisa 2x em sequencia
#   - contradicao: bot se contradiz entre mensagens
#   - falha_pre_opening: bot mandou after_hours quando deveria ter oferecido fila
#   - sobre_resposta: bot continuou respondendo apos handoff humanizado
#   - duplicado_distribuicao: 2 notas de distribuicao na mesma conv
# Se severidade=alta e tipo=repeticao_resposta ou sobre_resposta:
#   marca handoff_active(motivo='supervisor_block') -> agente CALA na conv.
# Findings ficam em agent_audit_findings (visiveis em dashboard).

_OPENAI_SUPERVISOR_PROMPT = """Voce e um auditor SENIOR de qualidade que revisa conversas de um agente de IA com alunos universitarios da Cruzeiro do Sul.

Contexto do agente:
- E um canal de ATENDIMENTO ACADEMICO (NaO comercial — matricula deve ser orientada via consultor).
- Horario de atendimento: Seg-Sex 8h-20h, Sab 8h-13h. Fora disso o bot deve dizer "fora do horario" OU oferecer fila pre-abertura quando faltar <= 60min para 8h.
- Regras canonicas que o bot DEVE seguir:
  * A1 (prova regimental) do MES VIGENTE: dizer que a nota e divulgada ate o final do mes. NaO mandar procurar tutor.
  * A1 de MES ANTERIOR: orientar a procurar tutor/professor.
  * Sem mes: perguntar de qual mes e a A1.
  * MasterClass: enviar instrucao canonica (link, prazo 48-72h, email masterclass@cruzeirodosul.edu.br).
  * Polo / visita presencial: usar endereco canonico OFICIAL, NUNCA inventar endereco. Transferir para humano se aluno demonstra dificuldade.
  * Retencao (cancelar/trancar): transferir para Wesley.
- O bot tem dedup; nao deve enviar a mesma mensagem (ou mensagem com mesmo SIGNIFICADO) 2x.
- Apos um handoff humanizado ("vou te transferir para X"), o bot NaO pode mais responder na conv.

SUA TAREFA: identifique SE o agente cometeu qualquer problema na ultima janela da conversa.

TIPOS DE PROBLEMA (escolha o que melhor descreve; use 'outro' se nao se encaixar):
- "repeticao": 2+ mensagens do bot com mesmo significado, mesmo que com palavras diferentes
- "contradicao": bot afirmou X depois Y inconsistente
- "falha_pre_opening": disse "fora do horario" quando deveria ter oferecido fila pre-abertura
- "sobre_resposta": bot continuou respondendo apos mensagem de handoff humano
- "duplicado_distribuicao": 2+ notas "Distribuicao automatica" pra mesma conv
- "resposta_generica": bot mandou "eu entendo"/"ok"/empatia vazia sem agregar nada (especialmente depois de info do aluno)
- "regra_a1_errada": aluno falou sobre A1 e bot nao seguiu a regra (mandou pro tutor sendo do mes vigente, ou nao perguntou o mes)
- "polo_alucinado": bot informou endereco/CEP de polo que parece inventado ou divergente do oficial
- "tom_inadequado": frio, formal demais, ou nao acolhedor pra contexto sensivel (cancelamento, problema serio)
- "nao_respondeu_pergunta": aluno fez uma pergunta especifica e o bot ignorou / respondeu outra coisa
- "informacao_incorreta": bot deu prazo/email/procedimento que nao corresponde ao padrao do time
- "perdido_conversa": bot enviou info e nao houve follow-up nem encerramento (deveria ter feito proativamente)
- "matricula_mal_direcionada": aluno disse que quer matricular e bot nao orientou que e canal academico ou nao transferiu pra consultor
- "alucinacao_geral": bot inventou algo que nao consta na base (numero, fato, prazo, link)
- "outro": qualquer outro problema relevante de qualidade — descreva no resumo
- "ok": nenhum problema observavel

SEVERIDADE:
- "alta": prejudica o aluno claramente (info errada, repeticao, regra quebrada, alucinacao critica como endereco de polo)
- "media": qualidade ruim mas sem prejuizo direto (resposta_generica, tom_inadequado, perdido_conversa)
- "baixa": suspeita leve, ambiguo

Seja RIGOROSO mas justo: se tem duvida, marque como "baixa" ou "ok". Nao invente problemas. Se voce vir algo que prejudica o atendimento ainda que nao esteja no catalogo, use "outro".

Retorne EXCLUSIVAMENTE JSON valido:
{
  "tem_problema": true|false,
  "tipo": "<tipo>",
  "severidade": "alta|media|baixa|nenhuma",
  "resumo": "<1-2 frases explicando o que aconteceu e por que e problema>",
  "trecho_problematico": "<trecho curto da conversa que evidencia>"
}

NAO inclua nada alem do JSON.
"""


def _openai_supervisor_fetch_convs():
    """Lista convs ativas (DCZ) com 2+ mensagens do bot nos ultimos OPENAI_SUPERVISOR_LOOKBACK_MIN min."""
    out = []
    try:
        r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                         params={'limit': 200, 'status': 'open'}, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        convs = data.get('data', data) if isinstance(data, dict) else data
        if not isinstance(convs, list):
            return []
        cutoff = time.time() - OPENAI_SUPERVISOR_LOOKBACK_MIN * 60
        # ordenar pelos mais recentes primeiro
        for c in convs:
            updated = c.get('updatedAt') or c.get('lastMessageAt') or ''
            ts = 0
            try:
                if isinstance(updated, str) and updated:
                    from datetime import datetime
                    ts = datetime.fromisoformat(updated.replace('Z', '+00:00')).timestamp()
            except Exception:
                pass
            if ts < cutoff:
                continue
            out.append((ts, c))
        out.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in out][:OPENAI_SUPERVISOR_MAX_CONVS * 3]  # buffer pra filtrar depois
    except Exception as e:
        p(f"  [OPENAI-SUP] erro listar convs: {e}")
        return []


def _openai_supervisor_get_window(conv_id, max_msgs=10, max_age_min=60):
    """Retorna lista [(role, text, ts, is_internal)] das ultimas mensagens.

    Usa get_conversation_messages_api (que ja eh validado e funciona) em
    vez de chamar o DCZ diretamente. O caminho proprio anterior estava
    retornando lista vazia para todas as 75 convs (caso reportado).

    FILTRO TEMPORAL (2026-05-21): mensagens com idade > max_age_min sao
    descartadas. Antes, o supervisor via violacoes historicas nas ultimas
    10 msgs e re-flagrava infinitamente o mesmo erro do bot. Agora so
    analisa o que aconteceu nos ultimos 60min.
    """
    try:
        msgs = get_conversation_messages_api(conv_id, limit=max_msgs * 2)
        if not msgs or not isinstance(msgs, list):
            return []
        out = []
        for m in msgs[:max_msgs]:
            if not isinstance(m, dict):
                continue
            body = (m.get('body') or m.get('text') or m.get('content') or '').strip()
            if not body:
                continue
            # FILTRO TEMPORAL: idade da msg em minutos
            _msg_ts = m.get('createdAt') or m.get('created_at') or ''
            try:
                _age_s = _iso_age_seconds(_msg_ts)
                if _age_s is not None and _age_s > max_age_min * 60:
                    continue  # mensagem antiga - ignora
            except Exception:
                pass
            # Campo CANONICO do DCZ: received=True => aluno; False => saida (bot/humano/nota)
            received = bool(m.get('received', False))
            is_internal = bool(m.get('isInternal') or m.get('internal'))
            has_attendant = bool(m.get('attendant') or m.get('attendantId') or m.get('attendant_id'))
            if received:
                role = 'aluno'
            elif is_internal:
                role = 'nota_interna'
            elif is_bot_message(body):
                role = 'bot'
            elif has_attendant:
                role = 'humano'
            else:
                # saida sem attendant + sem fingerprint = provavelmente bot
                role = 'bot'
            out.append((role, body[:600], _msg_ts, is_internal))
        return list(reversed(out))  # cronologico ascendente
    except Exception as e:
        try:
            p(f"  [OPENAI-SUP] erro get_window conv={conv_id[:12]}: {e}")
        except Exception:
            pass
        return []


def _openai_supervisor_audit_conv(conv_id, msgs_window):
    """Chama OpenAI e retorna dict com analise ou None em erro."""
    if not OPENAI_API_KEY:
        return None
    if not msgs_window:
        return None
    # Filtro minimo: precisa ter pelo menos 1 do aluno E pelo menos 1 da
    # equipe (bot OU humano) — depois que detectamos que o DCZ as vezes
    # classifica msgs do Agente IA como tendo attendant (porque o agente eh
    # uma "conta automacao"), o filtro restritivo a bot_count rejeitava
    # 100% das convs.
    bot_count = sum(1 for r, _, _, _ in msgs_window if r == 'bot')
    humano_count = sum(1 for r, _, _, _ in msgs_window if r == 'humano')
    aluno_count = sum(1 for r, _, _, _ in msgs_window if r == 'aluno')
    if aluno_count < 1 or (bot_count + humano_count) < 1:
        return None
    convo_str = []
    for role, body, _, _ in msgs_window[-10:]:
        convo_str.append(f"[{role}] {body}")
    convo_text = "\n".join(convo_str)
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        # Modelos gpt-5.x / o1-x usam max_completion_tokens (max_tokens da 400).
        # gpt-4o e anteriores usam max_tokens. Detecta pelo nome.
        _mname = (OPENAI_SUPERVISOR_MODEL or '').lower()
        _use_new_token_param = _mname.startswith('gpt-5') or _mname.startswith('o1') or _mname.startswith('o3') or _mname.startswith('o4')
        _kwargs = {
            'model': OPENAI_SUPERVISOR_MODEL,
            'response_format': {'type': 'json_object'},
            'messages': [
                {'role': 'system', 'content': _OPENAI_SUPERVISOR_PROMPT},
                {'role': 'user', 'content': f"Conversa (cronologica):\n{convo_text}\n\nAnalise."}
            ],
            'timeout': 45,
        }
        if _use_new_token_param:
            _kwargs['max_completion_tokens'] = 1200
            # gpt-5/o-series so aceita temperature default (1) — nao mandar
        else:
            _kwargs['max_tokens'] = 600
            _kwargs['temperature'] = 0.0
        resp = client.chat.completions.create(**_kwargs)
        content = resp.choices[0].message.content or '{}'
        parsed = json.loads(content)
        _openai_sup_stats['audited_total'] = _openai_sup_stats.get('audited_total', 0) + 1
        if parsed.get('tem_problema'):
            _openai_sup_stats['problems_found'] = _openai_sup_stats.get('problems_found', 0) + 1
        # ring-buffer dos ultimos 20 resultados (pra debug)
        from datetime import datetime as _dt, timezone as _tz
        item = {
            'conv_id': conv_id[:16],
            'tem_problema': bool(parsed.get('tem_problema')),
            'tipo': parsed.get('tipo') or '',
            'severidade': parsed.get('severidade') or '',
            'resumo': (parsed.get('resumo') or '')[:200],
            'when': _dt.now(_tz.utc).isoformat(),
        }
        lst = _openai_sup_stats.setdefault('last_results', [])
        lst.append(item)
        if len(lst) > 20:
            del lst[:len(lst) - 20]
        return parsed
    except Exception as e:
        _openai_sup_stats['errors'] = _openai_sup_stats.get('errors', 0) + 1
        _openai_sup_stats['last_error'] = f"{conv_id[:12]}: {e}"[:300]
        p(f"  [OPENAI-SUP] erro chamada OpenAI conv={conv_id[:12]}: {e}")
        return None


def _audit_recheck_assignment_findings(max_age_minutes=240):
    """Re-le o estado atual do CRM/chat para findings recentes de
    assignment_mismatch e os marca como resolvidos automaticamente quando
    o lead/business/chat estao consistentes com expected_name.

    Motivacao: o change-attendant do DCZ tem propagacao assincrona — alguns
    findings sao gravados durante o periodo de inconsistencia temporaria
    e ficam abertos eternamente mesmo quando o atendimento esta certo.

    Roda dentro do supervisor loop OpenAI (a cada ciclo), so para findings
    abertos das ultimas 4h, evitando consumir API alem do necessario.
    """
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, conv_id, phone, detail
              FROM agent_audit_findings
             WHERE problem_type = 'assignment_mismatch'
               AND resolved_at IS NULL
               AND created_at > NOW() - %s::interval
             ORDER BY created_at DESC
             LIMIT 50
        """, (f'{int(max_age_minutes)} minutes',))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        p(f"  [AUDIT-RECHECK] erro listar findings: {e}")
        return

    if not rows:
        return

    resolved = 0
    for fid, conv_id, phone, detail in rows:
        try:
            if isinstance(detail, str):
                d = json.loads(detail or '{}')
            else:
                d = detail or {}
            expected_name = d.get('expected_name') or ''
            expected_crm_id = d.get('expected_crm_id') or ''
            expected_chat_id = d.get('expected_chat_id') or ''
            lead_id = d.get('lead_id') or ''
            biz_id = d.get('biz_id') or ''

            if not expected_crm_id and not expected_chat_id:
                continue

            # le estado atual
            cur_lead_att = ''
            if lead_id:
                try:
                    r = requests.get(f'{DCZ_CRM}/leads/{lead_id}', headers=H, timeout=10)
                    if r.status_code == 200:
                        ld = r.json()
                        att = ld.get('attendant') or {}
                        cur_lead_att = att.get('id', '') if isinstance(att, dict) else (att or '')
                except Exception:
                    pass

            cur_biz_att = ''
            if biz_id:
                try:
                    r = requests.get(f'{DCZ_CRM}/businesses/{biz_id}', headers=H, timeout=10)
                    if r.status_code == 200:
                        bd = r.json()
                        att = bd.get('attendant') or {}
                        cur_biz_att = att.get('id', '') if isinstance(att, dict) else (att or '')
                except Exception:
                    pass

            cur_chat_att = ''
            if conv_id:
                try:
                    r = requests.get(f'{DCZ_MSG}/messaging/conversations/{conv_id}',
                                     headers=H, timeout=10)
                    if r.status_code == 200:
                        cd = r.json()
                        att = cd.get('attendant') or {}
                        if isinstance(att, dict):
                            cur_chat_att = att.get('id', '')
                        else:
                            cur_chat_att = cd.get('attendantId', '') or ''
                except Exception:
                    pass

            lead_ok = (cur_lead_att == expected_crm_id) if (lead_id and expected_crm_id) else True
            biz_ok = (cur_biz_att == expected_crm_id) if (biz_id and expected_crm_id) else True
            chat_ok = (cur_chat_att == expected_chat_id) if expected_chat_id else True

            if not (lead_ok and biz_ok and chat_ok):
                continue

            # tudo consistente agora — auto-resolver
            try:
                conn = get_db()
                cur2 = conn.cursor()
                cur2.execute("""
                    UPDATE agent_audit_findings
                       SET resolved_at = NOW(),
                           resolved_by = 'auto_recheck_consistent'
                     WHERE id = %s AND resolved_at IS NULL
                """, (fid,))
                conn.commit()
                cur2.close()
                conn.close()
                resolved += 1
                p(f"  [AUDIT-RECHECK] finding {fid} auto-resolvido — estado consistente com {expected_name}")
            except Exception as e_up:
                p(f"  [AUDIT-RECHECK] erro update finding {fid}: {e_up}")
        except Exception as e_row:
            p(f"  [AUDIT-RECHECK] erro processar finding {fid}: {e_row}")
            continue

    if resolved:
        p(f"  [AUDIT-RECHECK] {resolved} findings auto-resolvidos (estado ja consistente)")


# ACAO D (2026-05-21): cutoff temporal para auto-fix de assignment_mismatch.
# Apenas findings criados DEPOIS de NOW - AUDIT_AUTOFIX_CUTOFF_MIN sao
# elegiveis para PATCH automatico. Findings antigos (anteriores ao deploy
# desta logica) NUNCA serao tocados — exigem correcao manual via Cockpit.
# Isso protege o historico de DataCrazy contra mudancas em massa retroativas.
AUDIT_AUTOFIX_CUTOFF_MIN = 60


def _audit_autofix_assignment_findings(max_age_minutes=AUDIT_AUTOFIX_CUTOFF_MIN,
                                       max_to_fix_per_cycle=5):
    """Auto-corrige findings de assignment_mismatch APENAS recentes (<cutoff).

    Para cada finding aberto criado nos ultimos max_age_minutes minutos:
      1) le estado atual de lead/business/chat
      2) se ainda inconsistente, faz PATCH para expected_crm_id/expected_chat_id
      3) aguarda 5s, re-le, e marca resolved se OK

    CUTOFF TEMPORAL: findings antigos (>= max_age_minutes) NUNCA sao tocados.
    Isso garante que casos historicos no DataCrazy nao sao re-atribuidos por
    engano apos o deploy desta logica.
    """
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, conv_id, phone, detail
              FROM agent_audit_findings
             WHERE problem_type = 'assignment_mismatch'
               AND resolved_at IS NULL
               AND created_at > NOW() - %s::interval
             ORDER BY created_at DESC
             LIMIT %s
        """, (f'{int(max_age_minutes)} minutes', int(max_to_fix_per_cycle)))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        p(f"  [AUDIT-AUTOFIX] erro listar: {e}")
        return

    if not rows:
        return

    fixed = 0
    for fid, conv_id, phone, detail in rows:
        try:
            if isinstance(detail, str):
                d = json.loads(detail or '{}')
            else:
                d = detail or {}
            expected_name = d.get('expected_name') or ''
            expected_crm_id = d.get('expected_crm_id') or ''
            expected_chat_id = d.get('expected_chat_id') or ''
            lead_id = d.get('lead_id') or ''
            biz_id = d.get('biz_id') or ''

            if not (expected_crm_id or expected_chat_id):
                continue

            patched = False

            if expected_crm_id and lead_id:
                try:
                    r = requests.patch(f'{DCZ_CRM}/leads/{lead_id}', headers=H,
                                       json={'attendant': {'id': expected_crm_id}},
                                       timeout=10)
                    if r.status_code in (200, 201, 204):
                        patched = True
                        p(f"  [AUDIT-AUTOFIX] finding {fid} PATCH lead -> {expected_name}")
                except Exception:
                    pass
            if expected_crm_id and biz_id:
                try:
                    # business exige attendantId (string), não objeto attendant
                    r = requests.patch(f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
                                       json={'attendantId': expected_crm_id,
                                             'stageId': STAGE_ATENDIMENTO_ID},
                                       timeout=10)
                    if r.status_code in (200, 201, 204):
                        patched = True
                        p(f"  [AUDIT-AUTOFIX] finding {fid} PATCH business -> {expected_name}")
                except Exception:
                    pass
            if expected_chat_id and conv_id:
                try:
                    r = requests.post(
                        f'{DCZ_MSG}/messaging/conversations/{conv_id}/change-attendant',
                        headers=H, json={'attendantId': expected_chat_id}, timeout=15)
                    if r.status_code in (200, 201, 204):
                        patched = True
                        p(f"  [AUDIT-AUTOFIX] finding {fid} change-attendant -> {expected_name}")
                except Exception:
                    pass

            if patched:
                fixed += 1
        except Exception as e_row:
            p(f"  [AUDIT-AUTOFIX] erro processar finding {fid}: {e_row}")
            continue

    if fixed:
        p(f"  [AUDIT-AUTOFIX] {fixed} findings com PATCH aplicado (cutoff={max_age_minutes}min). Recheck no proximo ciclo confirma.")


def process_openai_supervisor_loop():
    """Roda no minimo a cada OPENAI_SUPERVISOR_INTERVAL_S. Audita convs ativas
    com OpenAI, grava findings e bloqueia agente em casos graves."""
    global _last_openai_supervisor_ts
    if not OPENAI_SUPERVISOR_ENABLED:
        return
    if not OPENAI_API_KEY:
        return
    now_ts = time.time()
    if now_ts - _last_openai_supervisor_ts < OPENAI_SUPERVISOR_INTERVAL_S:
        return
    _last_openai_supervisor_ts = now_ts

    _ensure_audit_table()

    # Antes de auditar novas convs, re-verifica findings recentes de
    # assignment_mismatch — se o estado do DCZ ja propagou e ficou
    # consistente, auto-resolve (evita falsos positivos eternos).
    try:
        _audit_recheck_assignment_findings()
    except Exception as e_rc:
        p(f"  [OPENAI-SUP] erro recheck: {e_rc}")

    # ACAO D (2026-05-21): auto-fix de findings RECENTES (< cutoff).
    # Tenta PATCH no CRM/chat para findings novos onde DCZ nao propagou
    # mesmo apos retries do distribute. CUTOFF TEMPORAL protege historico:
    # findings antigos NUNCA sao auto-corrigidos.
    try:
        _audit_autofix_assignment_findings()
    except Exception as e_af:
        p(f"  [OPENAI-SUP] erro autofix: {e_af}")

    _openai_sup_stats['cycles'] = _openai_sup_stats.get('cycles', 0) + 1
    _openai_sup_stats['last_cycle_at'] = now_ts

    convs = _openai_supervisor_fetch_convs()
    _openai_sup_stats['convs_listed'] = len(convs) if convs else 0
    if not convs:
        _openai_sup_stats['last_cycle_audited'] = 0
        _openai_sup_stats['last_cycle_problems'] = 0
        return

    audited = 0
    flagged_high = 0
    flagged_med = 0
    cycle_problems = 0
    # Contadores granulares de PULOS pra entender por que 0 auditadas mesmo
    # com 75 convs listadas (caso reportado).
    skip_no_cid = 0
    skip_recent_audit = 0
    skip_supervisor_block = 0
    skip_empty_window = 0
    skip_audit_returned_none = 0  # filtro bot/aluno em _openai_supervisor_audit_conv
    role_dist = {'aluno': 0, 'bot': 0, 'humano': 0, 'nota_interna': 0}
    for c in convs:
        if audited >= OPENAI_SUPERVISOR_MAX_CONVS:
            break
        cid = c.get('id') or ''
        if not cid:
            skip_no_cid += 1
            continue
        last_audit_ts = _openai_supervisor_audited.get(cid, 0)
        # nao re-auditar mesma conv mais que 1x a cada 10min (era 15)
        if now_ts - last_audit_ts < 10 * 60:
            skip_recent_audit += 1
            continue
        # ignorar convs ja com handoff supervisor_block ativo
        try:
            ho_motivo, _ = _is_handoff_active(cid)
            if ho_motivo == 'supervisor_block':
                skip_supervisor_block += 1
                continue
        except Exception:
            pass
        window = _openai_supervisor_get_window(cid, max_msgs=10)
        if not window:
            skip_empty_window += 1
            continue
        # Atualiza distribuicao de roles vista (acumulada no ciclo)
        for r, _, _, _ in window:
            if r in role_dist:
                role_dist[r] += 1
        result = _openai_supervisor_audit_conv(cid, window)
        if result is None:
            # ou erro OpenAI (incrementa errors) ou rejeicao do filtro bot/aluno
            skip_audit_returned_none += 1
            continue
        audited += 1
        _openai_supervisor_audited[cid] = now_ts
        if not result.get('tem_problema'):
            continue
        cycle_problems += 1
        sev = (result.get('severidade') or 'baixa').lower()
        ptype = (result.get('tipo') or '').lower()
        resumo = result.get('resumo') or ''
        trecho = result.get('trecho_problematico') or ''
        # extrai phone — DCZ guarda em c.contact.phoneNumber ou
        # c.contact.contactId, nunca no nivel raiz da conv.
        # (2026-05-27) Bug reportado: supervisor distribuia mas o lead nao
        # era criado porque phone ficava vazio. distribute_to_attendant
        # entao usava PHONE_TO_MONITOR (default) e o CRM nao associava o
        # lead com a conv real ('Lead nao encontrado' no painel).
        phone = ''
        try:
            ct = c.get('contact', {}) or {}
            phone = (ct.get('phoneNumber') or ct.get('contactId')
                     or ct.get('rawPhone') or ct.get('phone') or '')
            phone = str(phone).replace('+', '').replace(' ', '').replace('-', '')
            if phone.startswith('55') and len(phone) > 11:
                phone = phone[2:]
            # fallback: tenta nivel raiz (formato hipotetico de versoes
            # futuras da API)
            if not phone:
                for k in ('contactPhoneNumber', 'phone', 'contactPhone'):
                    v = c.get(k)
                    if v:
                        phone = str(v)
                        break
        except Exception:
            pass

        action = ''
        if sev == 'alta' and ptype in ('repeticao_resposta', 'sobre_resposta', 'duplicado_distribuicao'):
            # === IDEMPOTENCIA (2026-05-21): nao re-disparar acao supervisor em
            # conv ja redistribuida recentemente. Bug reportado (imagem
            # Daniella Ferraz): conv distribuida para Beatriz com violacao
            # historica "bot respondeu apos handoff". Supervisor re-detectava
            # a MESMA violacao a cada ciclo e redistribuia: Beatriz -> Mariana
            # -> Debora -> ... Cada ciclo gera nova nota e nova redistribuicao.
            # Fix: se ja existe finding de tipo similar com action != audit_only
            # nas ultimas 6h pra ESTA conv, so registra audit_only (visivel no
            # dash) e nao toma acao automatica.
            try:
                conn_id = get_db()
                cur_id = conn_id.cursor()
                cur_id.execute(
                    """
                    SELECT 1
                      FROM agent_audit_findings
                     WHERE conv_id = %s
                       AND severity = 'high'
                       AND action_taken IN ('agent_silenced', 'distributed')
                       AND created_at > NOW() - INTERVAL '6 hours'
                     LIMIT 1
                    """,
                    (cid,),
                )
                _already_acted = cur_id.fetchone() is not None
                cur_id.close()
                conn_id.close()
            except Exception:
                _already_acted = False
            if _already_acted:
                action = 'audit_only_idempotent'
                p(f"  [OPENAI-SUP] {cid[:12]} {sev}/{ptype} - SKIP acao (ja agiu nas ultimas 6h) | {resumo[:80]}")
                _record_audit_finding(
                    cid, severity=sev, problem_type=ptype, summary=resumo,
                    detail={'trecho': trecho, 'idempotent': True,
                            'window': [{'role': r, 'body': b[:200]}
                                       for r, b, _, _ in window[-6:]]},
                    action_taken=action, phone=phone, model=OPENAI_SUPERVISOR_MODEL,
                )
                continue
            try:
                conv_attendants = c.get('attendants', []) or []
                tem_humano = bool(conv_attendants)
                distributed_now = False
                target_consultant = ''

                # Passo 1: se nao tem humano e estamos no expediente, distribuir.
                # distribute_to_attendant() tem lock atomico + signature dedup,
                # entao e seguro chamar mesmo com concorrencia do in_hours_rescue.
                if not tem_humano:
                    try:
                        # Sincroniza globals para record_pending_escalation
                        # encontrar phone/nome (sao lidas dentro da funcao).
                        try:
                            globals()['_current_phone'] = phone
                            _conv_states.setdefault(cid, _default_conv_state())['phone'] = phone
                        except Exception:
                            pass
                        if is_within_business_hours():
                            distributed_now = distribute_to_attendant(
                                cid,
                                reason=f'supervisor_block:{ptype} - distribuicao imediata',
                                silent_after_hours=True,
                            )
                        if distributed_now:
                            p(f"  [OPENAI-SUP] {cid[:12]} sem humano - distribuido imediatamente")
                        else:
                            # Fora do expediente OU falha de distribuicao —
                            # registra pending_escalation para nao perder.
                            try:
                                record_pending_escalation(
                                    cid,
                                    reason='supervisor_block',
                                    tier='priority',
                                    retorno_label=next_human_available_label(),
                                    question=resumo[:200],
                                )
                                p(f"  [OPENAI-SUP] {cid[:12]} sem humano + sem distribuicao - registrado em pending_escalation")
                            except Exception as e_rec:
                                p(f"  [OPENAI-SUP] erro pending_escalation: {e_rec}")
                    except Exception as e_dist:
                        p(f"  [OPENAI-SUP] erro distribuicao supervisor: {e_dist}")
                else:
                    # CORRECAO (2026-05-25): NAO registrar em pending_escalation
                    # nem sobrescrever handoff_active quando ja tem humano. Antes,
                    # essa via causava o bug "agente expulsa consultor de retencao"
                    # — o supervisor escrevia priority na fila, a fila noturna
                    # pegava de volta e redistribuia pra outro consultor, e o
                    # mark_handoff_active('supervisor_block') sobrescrevia o
                    # retention(Wesley) existente. Agora apenas registra o finding
                    # com audit_only_human_present e logo, sem mover a conversa.
                    p(f"  [OPENAI-SUP] {cid[:12]} ALTA/{ptype} - humano({len(conv_attendants)}) ja presente -> audit_only_human_present (nao move conversa)")
                    action = 'audit_only_human_present'
                    _record_audit_finding(
                        cid, severity=sev, problem_type=ptype, summary=resumo,
                        detail={
                            'trecho': trecho,
                            'human_present': True,
                            'attendants': [(a.get('name') if isinstance(a, dict) else str(a))
                                           for a in conv_attendants][:5],
                            'window': [{'role': r, 'body': b[:200]}
                                       for r, b, _, _ in window[-6:]],
                        },
                        action_taken=action, phone=phone,
                        model=OPENAI_SUPERVISOR_MODEL,
                    )
                    continue

                # Passo 2: nudge unico ao aluno (4h ttl), so se ainda nao enviou.
                # Antes do silenciamento porque send_and_track nao tem block.
                try:
                    nudge_sig = 'supervisor_block_nudge'
                    if not _signature_recently_sent(cid, nudge_sig, window_s=4 * 3600):
                        nudge = (
                            "Oii! Já registrei aqui sua conversa e em pouquinho "
                            "um(a) consultor(a) vai dar continuidade ao seu atendimento, tá? 💙"
                        )
                        try:
                            send_and_track(cid, nudge)
                            _register_signature(cid, nudge_sig, nudge)
                            p(f"  [OPENAI-SUP] {cid[:12]} nudge enviado ao aluno")
                        except Exception as e_nu:
                            p(f"  [OPENAI-SUP] erro nudge: {e_nu}")
                except Exception:
                    pass

                # Passo 3: silenciar bot POR ULTIMO. Aqui ja sabemos que NAO tem
                # humano (caso contrario teriamos feito 'continue' acima).
                _mark_handoff_active(cid, 'supervisor_block',
                                     target='', ttl_s=6 * 3600,
                                     body=f"supervisor_block: {ptype}")
                action = 'agent_silenced'
                flagged_high += 1
                _silenced_summary = (
                    f"distribuido={distributed_now} tem_humano={tem_humano}"
                )
                p(f"  [OPENAI-SUP] {cid[:12]} ALTA/{ptype} - silenciado 6h | {_silenced_summary} | {resumo[:80]}")
            except Exception as e_blk:
                action = 'audit_only'
                p(f"  [OPENAI-SUP] erro silenciamento+distribuicao: {e_blk}")
        else:
            if sev == 'media':
                flagged_med += 1
            action = 'audit_only'
            p(f"  [OPENAI-SUP] {cid[:12]} {sev}/{ptype} | {resumo}")

        _record_audit_finding(
            cid, severity=sev, problem_type=ptype, summary=resumo,
            detail={'trecho': trecho, 'window': [{'role': r, 'body': b[:200]}
                                                  for r, b, _, _ in window[-6:]]},
            action_taken=action, phone=phone, model=OPENAI_SUPERVISOR_MODEL,
        )

    _openai_sup_stats['last_cycle_audited'] = audited
    _openai_sup_stats['last_cycle_problems'] = cycle_problems
    _openai_sup_stats['last_cycle_skips'] = {
        'no_cid': skip_no_cid,
        'recent_audit': skip_recent_audit,
        'supervisor_block': skip_supervisor_block,
        'empty_window': skip_empty_window,
        'audit_returned_none': skip_audit_returned_none,
    }
    _openai_sup_stats['last_cycle_role_dist'] = role_dist
    # Persiste stats em agent_config para o Cockpit/API ler.
    try:
        snap = dict(_openai_sup_stats)
        snap['model'] = OPENAI_SUPERVISOR_MODEL
        snap['enabled'] = bool(OPENAI_SUPERVISOR_ENABLED)
        snap['interval_s'] = OPENAI_SUPERVISOR_INTERVAL_S
        snap_json = json.dumps(snap, ensure_ascii=False, default=str)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_config (key, value, updated_at)
            VALUES ('openai_supervisor_stats', %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (snap_json[:8000],))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e_snap:
        p(f"  [OPENAI-SUP] erro persistir stats: {e_snap}")
    if audited:
        p(f"  [OPENAI-SUP] auditadas={audited} (problems={cycle_problems}) alta={flagged_high} media={flagged_med}")


def _oneshot_fix_vanessa_barra_funda():
    """One-shot executado uma unica vez no startup do agente apos o deploy
    da correcao do polo Barra Funda. Procura a conv ativa da 'Vanessa Carmona'
    que recebeu endereco errado (Rua dos Tres Irmaos, 100) e envia mensagem
    humanizada de correcao + nota interna + tentativa de distribuicao.

    Garante idempotencia atraves de agent_config (chave 'oneshot_vanessa_done').
    """
    KEY = 'oneshot_vanessa_barra_funda_done'
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("SELECT value FROM agent_config WHERE key = %s", (KEY,))
        row = cur.fetchone()
        already = bool(row and (row[0] or '').strip())
        cur.close()
        conn.close()
        if already:
            return
    except Exception:
        pass

    p("  [ONESHOT-VANESSA] iniciando correcao manual...")
    target = None
    try:
        r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                         params={'limit': 300, 'status': 'open'}, timeout=20)
        if r.status_code != 200:
            p(f"  [ONESHOT-VANESSA] DCZ status={r.status_code}")
            return
        data = r.json()
        convs = data.get('data', data) if isinstance(data, dict) else data
        cands = []
        for c in convs:
            ct = c.get('contact') or {}
            name = (ct.get('name') or c.get('contactName') or '').strip().lower()
            if 'vanessa' in name and 'carmona' in name:
                cands.append(c)
        if not cands:
            p("  [ONESHOT-VANESSA] nenhuma conv ativa de Vanessa Carmona encontrada")
            # marca como done pra nao tentar a cada restart
            try:
                conn = psycopg2.connect(**DB_CONFIG)
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO agent_config (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """, (KEY, 'not_found'))
                conn.commit()
                cur.close()
                conn.close()
            except Exception:
                pass
            return
        target = sorted(cands, key=lambda x: x.get('updatedAt') or x.get('lastMessageAt') or '',
                        reverse=True)[0]
    except Exception as e:
        p(f"  [ONESHOT-VANESSA] erro listar convs: {e}")
        return

    conv_id = target.get('id')
    _ct_one = target.get('contact') or {}
    phone = (_ct_one.get('phoneNumber') or _ct_one.get('contactId')
             or _ct_one.get('phone') or target.get('contactPhoneNumber') or '')
    phone = str(phone).replace('+', '').replace(' ', '').replace('-', '')
    if phone.startswith('55') and len(phone) > 11:
        phone = phone[2:]
    p(f"  [ONESHOT-VANESSA] conv={conv_id[:12]} phone={phone}")

    # 1) nota interna pra equipe
    nota = (
        "⚠️ *Correção manual via IA* — IA havia enviado endereço incorreto da Barra Funda "
        "(Rua dos Três Irmãos, 100). Mensagem de desculpas + endereço correto enviados. "
        "Distribuição automática em seguida."
    )
    try:
        requests.post(f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                      headers=H, json={'body': nota, 'isInternal': True}, timeout=15)
    except Exception:
        pass

    # 2) mensagem humanizada de correcao + endereco oficial
    msg = (
        "Vanessa, me desculpe! 😔 Acabei te passando o endereço errado da Barra Funda. "
        "Vou te passar a informação certa aqui:\n\n"
        "*Polo Barra Funda*\n"
        "📍 *Rua do Bosque, 1621, Loja 12 - Térreo*\n"
        "_10 minutos do Metrô - Estação Palmeiras Barra Funda - Linha 3 - Vermelha_\n\n"
        "E como vi que tá sendo difícil resolver tudo por aqui mesmo, vou *te transferir agora* "
        "para um(a) consultor(a) que vai te orientar direitinho e tirar todas as dúvidas. "
        "Em pouquinho alguém te chama por aqui 💙"
    )
    try:
        time.sleep(1.5)
        send_and_track(conv_id, msg, force=True)  # force ignora dedup (correcao critica)
        _register_signature(conv_id, 'oneshot_vanessa', msg)
    except Exception as e:
        p(f"  [ONESHOT-VANESSA] erro enviar msg: {e}")

    # 3) tentativa de distribuir
    try:
        global _current_phone, student_profile
        _current_phone = phone
        ct = target.get('contact') or {}
        first = (ct.get('name') or '').split()[0] if (ct.get('name') or '') else 'Vanessa'
        student_profile = {'name': ct.get('name') or 'Vanessa Carmona',
                           'first_name': first, 'phone': phone}
        if is_within_business_hours():
            ok = distribute_to_attendant(
                conv_id,
                reason='Correção manual: endereço errado da Barra Funda enviado pela IA — atendimento humano para orientar pessoalmente',
            )
            p(f"  [ONESHOT-VANESSA] distribute_to_attendant -> {ok}")
        else:
            record_pending_escalation(
                conv_id, reason='vanessa_correcao', tier='insist',
                retorno_label=next_human_available_label(),
                question='Correção manual: endereço errado Barra Funda',
            )
            p("  [ONESHOT-VANESSA] fora do horario - registrado em pending_escalation")
    except Exception as e:
        p(f"  [ONESHOT-VANESSA] erro distribuir: {e}")

    # 4) marcar como done (idempotencia)
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_config (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (KEY, conv_id))
        conn.commit()
        cur.close()
        conn.close()
        p(f"  [ONESHOT-VANESSA] marcado done em agent_config")
    except Exception as e:
        p(f"  [ONESHOT-VANESSA] erro marcar done: {e}")


def _oneshot_fix_vanessa_crm_attendant_DISABLED():
    """[DESATIVADO] Era um one-shot para corrigir manualmente o atendente da
    Vanessa quando o chat foi pra Debora mas o lead/business ficaram com a
    Joyce. Substituido por _enforce_assignment_consistency() que valida a
    distribuicao em tempo real para todos os casos.
    """
    return
    # _UNREACHABLE_ legacy code abaixo
    KEY = 'oneshot_vanessa_crm_attendant_done'
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("SELECT value FROM agent_config WHERE key = %s", (KEY,))
        row = cur.fetchone()
        already = bool(row and (row[0] or '').strip())
        cur.close()
        conn.close()
        if already:
            return
    except Exception:
        pass

    p("  [ONESHOT-VANESSA-CRM] iniciando correcao atendente CRM...")

    # 1) achar a conv ativa da Vanessa para pegar phone
    target = None
    try:
        r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                         params={'limit': 300, 'status': 'open'}, timeout=20)
        if r.status_code != 200:
            p(f"  [ONESHOT-VANESSA-CRM] DCZ list status={r.status_code}")
            return
        data = r.json()
        convs = data.get('data', data) if isinstance(data, dict) else data
        for c in convs:
            ct = c.get('contact') or {}
            name = (ct.get('name') or c.get('contactName') or '').strip().lower()
            if 'vanessa' in name and 'carmona' in name:
                target = c
                break
        if not target:
            p("  [ONESHOT-VANESSA-CRM] conv da Vanessa nao encontrada")
            # marca como done para nao tentar a cada restart
            try:
                conn = psycopg2.connect(**DB_CONFIG)
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO agent_config (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """, (KEY, 'not_found'))
                conn.commit()
                cur.close()
                conn.close()
            except Exception:
                pass
            return
    except Exception as e:
        p(f"  [ONESHOT-VANESSA-CRM] erro listar convs: {e}")
        return

    ct = target.get('contact') or {}
    phone = ct.get('phone', '') or ct.get('number', '') or ''
    conv_id = target.get('id') or ''
    if not phone:
        p("  [ONESHOT-VANESSA-CRM] phone nao encontrado")
        return
    p(f"  [ONESHOT-VANESSA-CRM] phone={phone} conv={conv_id[:12]}")

    debora_id = CRM_ATTENDANT_MAP.get('debora')
    if not debora_id:
        p("  [ONESHOT-VANESSA-CRM] ID da Debora nao mapeado em CRM_ATTENDANT_MAP")
        return

    # 2) buscar business pelo phone e atualizar attendant + stage
    business_updated = 0
    lead_updated = 0
    try:
        search_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        phones_to_try = [search_phone]
        if not search_phone.startswith('55'):
            phones_to_try.append('55' + search_phone)
        seen_biz = set()
        seen_leads = set()
        for try_phone in phones_to_try:
            rb = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                              params={'search': try_phone, 'limit': 10}, timeout=15)
            if rb.status_code != 200:
                continue
            data = rb.json()
            biz_list = data.get('data', data) if isinstance(data, dict) else data
            for biz in (biz_list if isinstance(biz_list, list) else []):
                biz_id = biz.get('id')
                if not biz_id or biz_id in seen_biz:
                    continue
                seen_biz.add(biz_id)
                # patch attendant + stage Atendimento
                try:
                    rp = requests.patch(
                        f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
                        json={'attendant': {'id': debora_id},
                              'stageId': STAGE_ATENDIMENTO_ID},
                        timeout=15,
                    )
                    if rp.status_code in (200, 204):
                        business_updated += 1
                        p(f"  [ONESHOT-VANESSA-CRM] business {str(biz_id)[:12]} -> Debora + Atendimento")
                except Exception as e:
                    p(f"  [ONESHOT-VANESSA-CRM] erro patch business: {e}")
                # patch lead vinculado
                lead_obj = biz.get('lead') or {}
                lead_id = lead_obj.get('id') if isinstance(lead_obj, dict) else lead_obj
                if lead_id and lead_id not in seen_leads:
                    seen_leads.add(lead_id)
                    try:
                        rl = requests.patch(
                            f'{DCZ_CRM}/leads/{lead_id}', headers=H,
                            json={'attendant': {'id': debora_id}},
                            timeout=15,
                        )
                        if rl.status_code in (200, 204):
                            lead_updated += 1
                            p(f"  [ONESHOT-VANESSA-CRM] lead {str(lead_id)[:12]} -> Debora")
                    except Exception as e:
                        p(f"  [ONESHOT-VANESSA-CRM] erro patch lead: {e}")
    except Exception as e:
        p(f"  [ONESHOT-VANESSA-CRM] erro geral: {e}")

    # 3) nota interna explicando
    if (business_updated or lead_updated) and conv_id:
        try:
            nota = ("🔧 *Ajuste automático* — Atendente do negócio/lead corrigido para *Debora Mani Moreira* "
                    f"(o chat já estava com ela; CRM estava desatualizado). "
                    f"business_updated={business_updated} lead_updated={lead_updated}.")
            requests.post(f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                          headers=H, json={'body': nota, 'isInternal': True}, timeout=10)
        except Exception:
            pass

    # 4) marcar done
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        result = f'biz={business_updated} lead={lead_updated}'
        cur.execute("""
            INSERT INTO agent_config (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (KEY, result))
        conn.commit()
        cur.close()
        conn.close()
        p(f"  [ONESHOT-VANESSA-CRM] marcado done ({result})")
    except Exception:
        pass


def _after_hours_escalation_tier(conv_id):
    """Retorna 'first' ou 'insist' baseado em quantos pedidos de atendente
    o aluno fez dentro da janela AFTER_HOURS_INSIST_WINDOW_MIN.
    """
    st = _conv_states.setdefault(conv_id, _default_conv_state())
    last_ts = st.get('_after_hours_escalation_ts') or 0
    count = int(st.get('_after_hours_escalation_count') or 0)
    now_ts = time.time()
    window_s = AFTER_HOURS_INSIST_WINDOW_MIN * 60

    if last_ts and (now_ts - last_ts) > window_s:
        count = 0
    count += 1
    st['_after_hours_escalation_ts'] = now_ts
    st['_after_hours_escalation_count'] = count
    return 'first' if count == 1 else 'insist'


def _em_intervalo(hora_str, ante_min, duracao_min, ref_now):
    """Checa se ref_now está no intervalo [hora - ante_min, hora + duracao_min]."""
    if not hora_str:
        return False
    try:
        parts = str(hora_str).split(':')
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return False

    from datetime import datetime, timedelta
    base = ref_now.replace(hour=h, minute=m, second=s, microsecond=0)
    ini = base - timedelta(minutes=ante_min)
    fim = base + timedelta(minutes=duracao_min)
    return ini <= ref_now <= fim


def get_available_consultant(exclude_attendants=None):
    """Consulta Supabase e retorna o consultor mais adequado ou None.
    Aplica as mesmas regras do workflow n8n de distribuição.

    exclude_attendants: set/list de nomes (lowercase) a ignorar nesta chamada
                        (usado pelo morning burst para limitar quantos cada um recebe).
    """
    exclude_set = set()
    if exclude_attendants:
        exclude_set = {str(n).strip().lower() for n in exclude_attendants if n}
    try:
        url = (f'{SUPABASE_URL}/rest/v1/{DISTRIBUICAO_TABLE}'
               f'?ativo_inativo=eq.Ativo&tipo_atendimento=eq.Atendimento'
               f'&select=*')
        r = requests.get(url, headers=SUPABASE_HEADERS, timeout=10)
        if r.status_code != 200:
            p(f"  [DIST] Supabase query falhou: {r.status_code} {r.text[:200]}")
            return None
        rows = r.json()
        if not rows:
            p(f"  [DIST] Nenhum consultor ativo encontrado no Supabase")
            return None
    except Exception as e:
        p(f"  [DIST] Erro ao consultar Supabase: {e}")
        return None

    now = _now_sp()
    dow = now.weekday()
    fim_de_semana = dow >= 5

    disponiveis = []
    for row in rows:
        nome = row.get('responsavel', 'Sem Nome')
        fila = int(row.get('fila') or 0)
        limite = int(row.get('volume_distribuicao') or 10)
        status_almoco = row.get('status_almoco', 'Ativo')
        status_expediente = row.get('status_final_expediente', 'Ativo')
        almoco_hora = row.get('almoco_real') or row.get('almoco')
        saida_hora = row.get('final_expediente')

        if exclude_set and str(nome).strip().lower() in exclude_set:
            p(f"  [DIST] {nome}: SKIP (excluido por limite burst)")
            continue
        # Filtro de ferias/afastamento (2026-05-25)
        try:
            _nome_first = str(nome).strip().lower().split()[0] if nome else ''
            if _nome_first in _ATTENDANTS_ON_VACATION:
                p(f"  [DIST] {nome}: SKIP (em ferias/afastado)")
                continue
        except Exception:
            pass
        if status_almoco != 'Ativo':
            p(f"  [DIST] {nome}: SKIP (status_almoco={status_almoco})")
            continue
        if status_expediente != 'Ativo':
            p(f"  [DIST] {nome}: SKIP (status_expediente={status_expediente})")
            continue
        if fila >= limite:
            p(f"  [DIST] {nome}: SKIP (fila={fila} >= limite={limite})")
            continue
        if not fim_de_semana and _em_intervalo(almoco_hora, ALMOCO_ANTE_MIN, ALMOCO_DURACAO_MIN, now):
            p(f"  [DIST] {nome}: SKIP (pausa almoço)")
            continue
        if _em_intervalo(saida_hora, SAIDA_ANTE_MIN, 0, now):
            p(f"  [DIST] {nome}: SKIP (perto da saída)")
            continue

        ts_raw = row.get('timestamp') or row.get('ultima_execucao')
        ts_val = 0
        if ts_raw:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(ts_raw).replace(' - ', ' ').replace('Z', '+00:00'))
                ts_val = dt.timestamp()
            except Exception:
                ts_val = 0

        disponiveis.append({
            'id': row.get('id', ''),
            'nome': nome,
            'fila': fila,
            'limite': limite,
            '_ts': ts_val,
        })

    if not disponiveis:
        p(f"  [DIST] Nenhum consultor disponível após filtros")
        return None

    import time as _time
    agora = _time.time()
    disponiveis.sort(key=lambda x: (
        -(agora - x['_ts']) if x['_ts'] > 0 else 1,
        x['fila'],
        x['nome'],
    ))

    escolhido = disponiveis[0]
    p(f"  [DIST] Escolhido: {escolhido['nome']} (fila={escolhido['fila']}, id={escolhido['id'][:16]})")
    return escolhido


def _dcz_transfer_lead(lead_id, attendant_name):
    """Atribui o lead ao responsável no DataCrazy CRM via campo attendant."""
    crm_id = _lookup_attendant_id(attendant_name, CRM_ATTENDANT_MAP)
    if not crm_id:
        p(f"  [DIST] CRM attendantId não encontrado para '{attendant_name}'")
        return False
    if not lead_id:
        p(f"  [DIST] lead_id vazio, skip lead transfer")
        return False
    try:
        r = requests.patch(
            f'{DCZ_CRM}/leads/{lead_id}', headers=H,
            json={'attendant': {'id': crm_id}}, timeout=10
        )
        p(f"  [DIST] Lead {lead_id[:16]} -> attendant.id={crm_id[:12]} (status={r.status_code})")
        return r.status_code in (200, 201)
    except Exception as e:
        p(f"  [DIST] Erro lead transfer: {e}")
        return False


def _dcz_transfer_business(phone, attendant_name, lead_id=''):
    """Encontra o negócio correto do lead e atribui ao attendant + move para Atendimento."""
    crm_id = _lookup_attendant_id(attendant_name, CRM_ATTENDANT_MAP)
    if not crm_id:
        p(f"  [DIST-BIZ] CRM attendantId não encontrado para '{attendant_name}'")
        return False

    biz_id = ''
    lead_name = ''
    lead_phone = ''
    try:
        # 1) Pegar dados do lead para usar na busca
        if lead_id:
            r = requests.get(f'{DCZ_CRM}/leads/{lead_id}', headers=H, timeout=10)
            if r.status_code == 200:
                ld = r.json()
                lead_name = ld.get('name', '')
                lead_phone = ld.get('rawPhone', '') or ld.get('phone', '')
                p(f"  [DIST-BIZ] Lead: name='{lead_name}', rawPhone='{lead_phone}'")

        # 2) Tentar GET /leads/{id}/businesses (sub-recurso)
        if not biz_id and lead_id:
            try:
                r = requests.get(f'{DCZ_CRM}/leads/{lead_id}/businesses', headers=H, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    biz_list = data.get('data', data) if isinstance(data, dict) else data
                    if isinstance(biz_list, list) and biz_list:
                        biz_id = biz_list[0].get('id', '') if isinstance(biz_list[0], dict) else str(biz_list[0])
                        p(f"  [DIST-BIZ] Via /leads/id/businesses: {biz_id[:16]}")
                else:
                    p(f"  [DIST-BIZ] /leads/id/businesses: status={r.status_code}")
            except Exception as e_sub:
                p(f"  [DIST-BIZ] /leads/id/businesses erro: {e_sub}")

        # 3) Buscar por nome do lead (mais preciso que leadId param)
        if not biz_id and lead_name:
            try:
                r = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                                 params={'search': lead_name, 'limit': 10}, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    biz_list = data.get('data', data) if isinstance(data, dict) else data
                    if isinstance(biz_list, list):
                        for b in biz_list:
                            b_lead = b.get('leadId') or b.get('lead', {}).get('id', '') if isinstance(b.get('lead'), dict) else ''
                            b_name = b.get('name', '') or ''
                            if b_lead == lead_id:
                                biz_id = b.get('id', '')
                                p(f"  [DIST-BIZ] Match por leadId no resultado: {biz_id[:16]}")
                                break
                            if b_name.strip().lower() == lead_name.strip().lower() and not biz_id:
                                biz_id = b.get('id', '')
                                p(f"  [DIST-BIZ] Match por nome: {biz_id[:16]}")
                        if not biz_id and biz_list:
                            p(f"  [DIST-BIZ] Busca nome: {len(biz_list)} resultados, nenhum match. Primeiro: {biz_list[0].get('name','')[:40]}")
            except Exception as e_name:
                p(f"  [DIST-BIZ] Erro busca nome: {e_name}")

        # 4) Buscar por rawPhone do lead
        if not biz_id and lead_phone:
            clean = lead_phone.replace('+', '').replace(' ', '').replace('-', '')
            phones_to_try = [clean]
            if not clean.startswith('55'):
                phones_to_try.append('55' + clean)
            if clean.startswith('55') and len(clean) > 4:
                phones_to_try.append(clean[2:])
            for try_phone in phones_to_try:
                if biz_id:
                    break
                r = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                                 params={'search': try_phone, 'limit': 10}, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    biz_list = data.get('data', data) if isinstance(data, dict) else data
                    if isinstance(biz_list, list):
                        for b in biz_list:
                            b_lead = b.get('leadId') or ''
                            if b_lead == lead_id:
                                biz_id = b.get('id', '')
                                p(f"  [DIST-BIZ] Match leadId via phone search: {biz_id[:16]}")
                                break
                        if not biz_id and biz_list:
                            biz_id = biz_list[0].get('id', '')
                            p(f"  [DIST-BIZ] Primeiro resultado phone {try_phone[-4:]}: {biz_id[:16]} (sem leadId match)")

        # 5) Fallback: phone do argumento
        if not biz_id and phone and phone != lead_phone:
            clean = phone.replace('+', '').replace(' ', '').replace('-', '')
            phones_to_try = [clean]
            if not clean.startswith('55'):
                phones_to_try.append('55' + clean)
            for try_phone in phones_to_try:
                if biz_id:
                    break
                r = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                                 params={'search': try_phone, 'limit': 5}, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    biz_list = data.get('data', data) if isinstance(data, dict) else data
                    if isinstance(biz_list, list) and biz_list:
                        biz_id = biz_list[0].get('id', '')
                        p(f"  [DIST-BIZ] Via phone arg {try_phone[-4:]}: {biz_id[:16]}")

        # 6) Criar business se não encontrou
        if not biz_id and lead_id:
            p(f"  [DIST-BIZ] Nenhum business encontrado, criando para lead {lead_id[:16]}")
            try:
                r_new = requests.post(f'{DCZ_CRM}/businesses', headers=H,
                                      json={'leadId': lead_id, 'stageId': STAGE_ATENDIMENTO_ID,
                                            'attendantId': crm_id}, timeout=10)
                if r_new.status_code in (200, 201):
                    biz_id = r_new.json().get('id', '')
                    p(f"  [DIST-BIZ] Business criado: {biz_id[:16]} (já no Atendimento)")
                    return True
                else:
                    p(f"  [DIST-BIZ] Criar falhou: {r_new.status_code} - {r_new.text[:200]}")
            except Exception as e_new:
                p(f"  [DIST-BIZ] Erro criar: {e_new}")

        if not biz_id:
            p(f"  [DIST-BIZ] Nenhum negócio encontrado/criado")
            return False

        # PATCH 1: responsável — o endpoint de BUSINESS exige 'attendantId' (string).
        # (2026-06-09) Enviar {'attendant': {'id':...}} retorna 200 mas NÃO aplica
        # — causa raiz do mismatch chat/lead reportado. Só o LEAD aceita o objeto.
        r_resp = requests.patch(
            f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
            json={'attendantId': crm_id}, timeout=10
        )
        p(f"  [DIST-BIZ] Business {biz_id[:16]} -> attendantId={crm_id[:12]} (status={r_resp.status_code})")

        # PATCH 2: pipeline Atendimento
        r_stage = requests.patch(
            f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
            json={'stageId': STAGE_ATENDIMENTO_ID}, timeout=10
        )
        stage_ok = r_stage.status_code in (200, 201, 204)
        p(f"  [DIST-BIZ] Business {biz_id[:16]} -> Atendimento (status={r_stage.status_code}, ok={stage_ok})")
        if not stage_ok:
            p(f"  [DIST-BIZ] Resposta: {r_stage.text[:300]}")

        return r_resp.status_code in (200, 201, 204) and stage_ok
    except Exception as e:
        p(f"  [DIST-BIZ] Erro: {e}")
        return False


def _dcz_transfer_chat(conv_id, attendant_name):
    """Transfere a conversa para o attendant via change-attendant (fluxo padrão)."""
    att_id = _lookup_attendant_id(attendant_name, ATTENDANT_MAP)
    if not att_id:
        p(f"  [DIST-CHAT] attendantId não encontrado para '{attendant_name}'")
        return False
    try:
        r = requests.post(
            f'{DCZ_MSG}/messaging/conversations/{conv_id}/change-attendant',
            headers=H, json={'attendantId': att_id}, timeout=15
        )
        ok = r.status_code in (200, 201, 204)
        p(f"  [DIST-CHAT] change-attendant -> {att_id[:12]} (status={r.status_code}, ok={ok})")
        if not ok:
            p(f"  [DIST-CHAT] Resposta: {r.text[:300]}")
        return ok
    except Exception as e:
        p(f"  [DIST-CHAT] Erro: {e}")
        return False


def _supabase_increment_fila(consultant_id, current_fila):
    """Incrementa fila +1 e atualiza timestamp no Supabase."""
    try:
        from datetime import datetime, timezone, timedelta
        now_str = datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y - %H:%M')
        iso_str = datetime.now(timezone(timedelta(hours=-3))).isoformat()

        url = f'{SUPABASE_URL}/rest/v1/{DISTRIBUICAO_TABLE}?id=eq.{consultant_id}'
        payload = {
            'fila': current_fila + 1,
            'ultima_execucao': now_str,
            'timestamp': iso_str,
        }
        r = requests.patch(url, headers=SUPABASE_HEADERS, json=payload, timeout=10)
        p(f"  [DIST] Supabase fila: {current_fila} -> {current_fila + 1} (status={r.status_code})")
        return r.status_code in (200, 204)
    except Exception as e:
        p(f"  [DIST] Erro Supabase update: {e}")
        return False


def _enforce_assignment_consistency(conv_id, lead_id, phone, expected_name,
                                    max_retries=4):
    """Verifica e força que o atendente do lead+business+chat seja realmente
    `expected_name` (o nome que será mencionado na nota interna e na
    mensagem ao cliente). Faz até max_retries patches se divergir.

    Se persistir divergência, registra audit finding (high) e nota interna
    com instrução para correção manual — garantindo que o caso não
    silenciosamente fique com o atendente errado no CRM.

    Retorna dict:
      {ok_lead, ok_biz, ok_chat, attempts, biz_id, lead_id,
       final_lead_att, final_biz_att, final_chat_att, expected_crm_id}
    """
    expected_crm_id = _lookup_attendant_id(expected_name, CRM_ATTENDANT_MAP) or ''
    expected_chat_id = _lookup_attendant_id(expected_name, ATTENDANT_MAP) or ''
    result = {
        'ok_lead': False, 'ok_biz': False, 'ok_chat': False,
        'attempts': 0, 'biz_id': '', 'lead_id': lead_id,
        'final_lead_att': '', 'final_biz_att': '', 'final_chat_att': '',
        'expected_crm_id': expected_crm_id,
        'expected_chat_id': expected_chat_id,
        'expected_name': expected_name,
    }
    if not expected_crm_id:
        p(f"  [VERIFY] expected_name='{expected_name}' nao mapeado em CRM_ATTENDANT_MAP — skip")
        return result

    # ---- helpers locais ----
    def _read_lead_att():
        if not lead_id:
            return ''
        try:
            r = requests.get(f'{DCZ_CRM}/leads/{lead_id}', headers=H, timeout=10)
            if r.status_code != 200:
                return ''
            ld = r.json()
            att = ld.get('attendant') or {}
            return att.get('id', '') if isinstance(att, dict) else (att or '')
        except Exception:
            return ''

    def _find_biz_id():
        # 1) sub-recurso /leads/{id}/businesses
        if lead_id:
            try:
                r = requests.get(f'{DCZ_CRM}/leads/{lead_id}/businesses',
                                 headers=H, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    biz_list = data.get('data', data) if isinstance(data, dict) else data
                    if isinstance(biz_list, list) and biz_list:
                        bid = biz_list[0].get('id', '') if isinstance(biz_list[0], dict) else str(biz_list[0])
                        if bid:
                            return bid, biz_list[0] if isinstance(biz_list[0], dict) else {}
            except Exception:
                pass
        # 2) busca por phone -> filtra por leadId
        if phone:
            clean = phone.replace('+', '').replace(' ', '').replace('-', '')
            phones_to_try = [clean]
            if not clean.startswith('55'):
                phones_to_try.append('55' + clean)
            elif len(clean) > 4:
                phones_to_try.append(clean[2:])
            for try_phone in phones_to_try:
                try:
                    r = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                                     params={'search': try_phone, 'limit': 10},
                                     timeout=10)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    biz_list = data.get('data', data) if isinstance(data, dict) else data
                    if not isinstance(biz_list, list):
                        continue
                    # match exato por leadId primeiro
                    for b in biz_list:
                        b_lead = b.get('leadId') or ''
                        if not b_lead and isinstance(b.get('lead'), dict):
                            b_lead = b['lead'].get('id', '')
                        if lead_id and b_lead == lead_id:
                            return b.get('id', ''), b
                    # fallback: primeiro
                    if biz_list:
                        return biz_list[0].get('id', ''), biz_list[0]
                except Exception:
                    continue
        return '', {}

    def _read_biz_att(biz_obj, biz_id):
        if biz_obj:
            att = biz_obj.get('attendant') or {}
            if isinstance(att, dict) and att.get('id'):
                return att.get('id', '')
        if not biz_id:
            return ''
        try:
            r = requests.get(f'{DCZ_CRM}/businesses/{biz_id}', headers=H, timeout=10)
            if r.status_code != 200:
                return ''
            bd = r.json()
            att = bd.get('attendant') or {}
            return att.get('id', '') if isinstance(att, dict) else (att or '')
        except Exception:
            return ''

    def _read_chat_att():
        if not conv_id:
            return ''
        try:
            r = requests.get(f'{DCZ_MSG}/messaging/conversations/{conv_id}',
                             headers=H, timeout=10)
            if r.status_code != 200:
                return ''
            cd = r.json()
            att = cd.get('attendant') or {}
            if isinstance(att, dict):
                return att.get('id', '')
            return cd.get('attendantId', '') or ''
        except Exception:
            return ''

    # ---- ciclo de verificação + retry ----
    for attempt in range(max_retries + 1):
        result['attempts'] = attempt + 1
        biz_id, biz_obj = _find_biz_id()
        result['biz_id'] = biz_id

        cur_lead_att = _read_lead_att()
        cur_biz_att = _read_biz_att(biz_obj, biz_id)
        cur_chat_att = _read_chat_att()
        result['final_lead_att'] = cur_lead_att
        result['final_biz_att'] = cur_biz_att
        result['final_chat_att'] = cur_chat_att

        lead_ok = (cur_lead_att == expected_crm_id) if lead_id else True
        biz_ok = (cur_biz_att == expected_crm_id) if biz_id else False
        chat_ok = (cur_chat_att == expected_chat_id) if expected_chat_id else True

        result['ok_lead'] = lead_ok
        result['ok_biz'] = biz_ok
        result['ok_chat'] = chat_ok

        if lead_ok and biz_ok and chat_ok:
            p(f"  [VERIFY] OK attempt={attempt+1} expected={expected_name} lead/biz/chat consistentes")
            return result

        p(f"  [VERIFY] attempt={attempt+1} divergencia: "
          f"lead_ok={lead_ok}({cur_lead_att[:8]} vs {expected_crm_id[:8]}) "
          f"biz_ok={biz_ok}({cur_biz_att[:8]} vs {expected_crm_id[:8]}) "
          f"chat_ok={chat_ok}({cur_chat_att[:8]} vs {expected_chat_id[:8]})")

        if attempt >= max_retries:
            break

        # tenta corrigir o que estiver divergente
        if not lead_ok and lead_id:
            try:
                rL = requests.patch(f'{DCZ_CRM}/leads/{lead_id}', headers=H,
                                    json={'attendant': {'id': expected_crm_id}},
                                    timeout=10)
                p(f"  [VERIFY] retry PATCH lead -> {expected_crm_id[:8]} status={rL.status_code}")
            except Exception as e:
                p(f"  [VERIFY] retry lead err: {e}")
        if not biz_ok and biz_id:
            try:
                rB = requests.patch(f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
                                    json={'attendantId': expected_crm_id,
                                          'stageId': STAGE_ATENDIMENTO_ID},
                                    timeout=10)
                p(f"  [VERIFY] retry PATCH business -> {expected_crm_id[:8]} status={rB.status_code}")
            except Exception as e:
                p(f"  [VERIFY] retry biz err: {e}")
        if not chat_ok and expected_chat_id and conv_id:
            try:
                rC = requests.post(
                    f'{DCZ_MSG}/messaging/conversations/{conv_id}/change-attendant',
                    headers=H, json={'attendantId': expected_chat_id}, timeout=15)
                p(f"  [VERIFY] retry change-attendant -> {expected_chat_id[:8]} status={rC.status_code}")
            except Exception as e:
                p(f"  [VERIFY] retry chat err: {e}")
        # ACAO D (2026-05-21): sleeps crescentes 5s/10s/15s/20s/30s (max 30s).
        # Antes 3s/6s/9s/9s/9s (max 10s) gerava 86 falsos positivos
        # 'assignment_mismatch' porque o DCZ ainda nao tinha propagado
        # change-attendant. Agora aguardamos mais tempo entre rechecks.
        time.sleep(min(5 * (attempt + 1), 30))

    # ---- pos-retries: ainda divergente ----
    p(f"  [VERIFY] FALHA persistente apos {result['attempts']} tentativas — registrando audit")
    try:
        details = {
            'conv_id': conv_id,
            'lead_id': lead_id,
            'biz_id': result['biz_id'],
            'expected_name': expected_name,
            'expected_crm_id': expected_crm_id,
            'expected_chat_id': expected_chat_id,
            'final_lead_att': result['final_lead_att'],
            'final_biz_att': result['final_biz_att'],
            'final_chat_att': result['final_chat_att'],
            'attempts': result['attempts'],
        }
        _record_audit_finding(
            conv_id=conv_id,
            severity='high',
            problem_type='assignment_mismatch',
            summary=(f"Distribuicao informou *{expected_name}* mas CRM/chat ficou "
                     f"divergente apos {result['attempts']} tentativas. "
                     f"Lead={result['ok_lead']} Biz={result['ok_biz']} Chat={result['ok_chat']}. "
                     f"Correcao manual necessaria."),
            detail=details,
            action_taken='manual_fix_required',
            phone=phone or '',
            model='distribute_to_attendant',
        )
    except Exception as e_aud:
        p(f"  [VERIFY] erro audit_finding: {e_aud}")
    # NOTA: nao enviamos mais a "Inconsistencia de distribuicao" como mensagem
    # interna visivel pros consultores (poluia a conversa sem ajudar). O finding
    # ja eh registrado em agent_audit_findings e aparece na aba Auditoria IA
    # do Cockpit, onde o usuario pode clicar "Corrigir agora".
    return result


def _attendant_is_dashboard_inactive(att_name):
    """True se o atendente está no dashboard (Supabase) com ativo_inativo != 'Ativo'
    (folga/inativo). Usado para decidir se uma conversa presa com um humano INATIVO
    deve ser redistribuída para alguém ativo quando chega mensagem nova.

    Retorna False (NÃO redistribuir) quando:
      - att_name vazio;
      - é membro do time de Retenção (Wesley/Danúbia ficam Inativo de propósito,
        mas continuam donos das retenções — não podem ser "roubados");
      - está Ativo no painel;
      - não foi encontrado na tabela (conservador: não mexe em desconhecido).
    """
    try:
        if not att_name:
            return False
        parts = _normalize_attendant_name(att_name).split()
        first = parts[0] if parts else ''
        if not first:
            return False
        for m in RETENTION_TEAM:
            if _normalize_attendant_name(m).split()[0] == first:
                return False
        url = (f'{SUPABASE_URL}/rest/v1/{DISTRIBUICAO_TABLE}'
               f'?tipo_atendimento=eq.Atendimento&select=responsavel,ativo_inativo')
        r = requests.get(url, headers=SUPABASE_HEADERS, timeout=10)
        if r.status_code != 200:
            return False
        for row in r.json() or []:
            rp = _normalize_attendant_name(row.get('responsavel') or '').split()
            rp_first = rp[0] if rp else ''
            if rp_first == first:
                return (row.get('ativo_inativo') or '').strip().lower() != 'ativo'
        return False
    except Exception:
        return False


def distribute_to_attendant(conv_id, reason='', silent_after_hours=True, exclude_attendants=None):
    """Distribui o aluno para um atendente humano real.
    1) Verifica horário  2) Escolhe consultor  3) Transfere lead/negócio/chat
    4) Atualiza fila no Supabase  5) Envia mensagem ao aluno.

    Retorna True se distribuiu de fato; False caso contrário.
    Fora do horário NÃO envia mensagem nem marca _human_took_over —
    o caller é responsável por exibir AFTER_HOURS_FIRST_MSG / AFTER_HOURS_INSIST_MSG.

    exclude_attendants: nomes (lowercase) a ignorar nesta chamada (usado pelo
                        morning burst pra limitar quantos cada um recebe).
    """
    if not is_within_business_hours():
        p(f"  [DIST] [MODE] after_hours — distribuição abortada (motivo='{reason}')")
        return False

    # === IDEMPOTENCIA: nao distribui 2x na mesma conv ===
    # Camada 1 (rapida): check de leitura — evita custo de escolher consultor
    # se ja distribuido.
    try:
        ho_motivo, ho_target = _is_handoff_active(conv_id)
        if ho_motivo == 'dispatch':
            # (2026-06-09) Se o alvo do dispatch está INATIVO no dashboard (ex.:
            # Felipe/Débora de folga), NÃO é idempotente: redistribui p/ ativo.
            # Limpa o handoff (protect_human=False) para os locks de dispatch não
            # bloquearem a nova distribuição.
            if ho_target and _attendant_is_dashboard_inactive(ho_target):
                p(f"  [DIST] {conv_id[:12]} dispatch p/ {ho_target} INATIVO — vai redistribuir p/ ativo")
                try:
                    _mark_handoff_active(conv_id, 'human_unavailable', target='',
                                         ttl_s=30, protect_human=False)
                except Exception:
                    pass
            else:
                p(f"  [DIST] {conv_id[:12]} ja distribuido para {ho_target} (handoff_active) - skip idempotente")
                return True
    except Exception:
        pass
    # Fallback: estado em memoria
    try:
        st_idem = _conv_states.get(conv_id, {}) or {}
        last_dist = st_idem.get('_last_distributed_to')
        last_ts = st_idem.get('_last_responded_ts') or 0
        if last_dist and (time.time() - last_ts) < 5 * 60:
            p(f"  [DIST] {conv_id[:12]} distribuido para {last_dist} ha <5min (mem) - skip idempotente")
            return True
    except Exception:
        pass

    # Camada 2: serializa chamadas concorrentes no mesmo processo.
    # Sem isso, 2 threads passariam pelo check acima ao mesmo tempo e
    # adquiririam o lock Postgres em sequencia. Com o lock in-process,
    # apenas uma thread chega ao Postgres por vez.
    inproc_lock = _get_dispatch_inproc_lock(conv_id)
    if not inproc_lock.acquire(timeout=30):
        p(f"  [DIST] {conv_id[:12]} nao conseguiu lock in-proc (timeout 30s) - skip")
        return False
    try:
        # Re-check apos pegar lock: outra thread no mesmo processo pode ter
        # acabado de distribuir.
        try:
            ho_motivo2, ho_target2 = _is_handoff_active(conv_id)
            if ho_motivo2 == 'dispatch':
                p(f"  [DIST] {conv_id[:12]} re-check pos-lock: ja distribuido para {ho_target2} - skip")
                return True
        except Exception:
            pass

        return _distribute_to_attendant_locked(
            conv_id, reason=reason,
            silent_after_hours=silent_after_hours,
            exclude_attendants=exclude_attendants,
        )
    finally:
        try:
            inproc_lock.release()
        except Exception:
            pass


def _distribute_to_attendant_locked(conv_id, reason='', silent_after_hours=True,
                                    exclude_attendants=None):
    """Corpo real de distribute_to_attendant, executado sob lock in-process."""

    # === REGRA GERAL DE PROTECAO (2026-05-26) ===
    # Nunca distribuir uma conv que JA tem atendente humano OU handoff humano
    # ativo (retencao/preferred/dispatch). Caso reportado: Denise Castaldi
    # estava com Wesley (retencao), respondeu "Sim" ao botao e o sistema
    # removeu Wesley e atribuiu Mariana — porque alguma rota chamou
    # distribute_to_attendant sem checar o estado da conv. Esta blindagem
    # impede QUALQUER chamada (LOW-CONF, frustracao, fora-escopo, etc.) de
    # sobrescrever um humano ja ativo na conv.
    try:
        has_h, att_name = _dcz_conv_has_human(conv_id)
        if has_h:
            # (2026-06-09) Se o humano atribuído está INATIVO no dashboard e a conv
            # NÃO está em retenção, NÃO mantemos ela presa com ele: quando chega
            # mensagem nova, redistribui para um consultor ATIVO (e de quebra
            # reconcilia chat/lead, que ficam consistentes na distribuição normal).
            # Caso reportado: Felipe/Débora (de folga) seguravam alunos novos.
            _in_ret = False
            try:
                _in_ret = _is_in_retention(conv_id)
            except Exception:
                _in_ret = False
            _redistrib_inactive = (
                (not _in_ret) and att_name and _attendant_is_dashboard_inactive(att_name)
            )
            if _redistrib_inactive:
                p(f"  [DIST] {conv_id[:12]} humano atual ({att_name}) INATIVO no dashboard — redistribuindo p/ ativo (motivo='{reason}')")
                # limpa handoff antigo p/ os locks de dispatch não bloquearem
                try:
                    _mark_handoff_active(conv_id, 'human_unavailable', target='',
                                         ttl_s=30, protect_human=False)
                except Exception:
                    pass
            else:
                p(f"  [DIST] PROTECAO: {conv_id[:12]} ja tem humano ({att_name or '?'}) ativo/retenção — abortando distribuicao (motivo='{reason}')")
                try:
                    # marca pending como ja em andamento para evitar nova tentativa via fila
                    update_pending_escalation_status(
                        conv_id, 'in_progress',
                        note=f'Protecao distribute: conv ja com {att_name or "humano"} — sem redistribuir.',
                    )
                except Exception:
                    pass
                return True
    except Exception as e_prot1:
        p(f"  [DIST] erro check humano (segue mesmo assim): {e_prot1}")

    try:
        ho_motivo_p, ho_target_p = _is_handoff_active(conv_id)
        if ho_motivo_p in _HUMAN_HANDOFF_MOTIVOS and ho_target_p:
            p(f"  [DIST] PROTECAO: {conv_id[:12]} handoff({ho_motivo_p}) ativo p/ {ho_target_p} — NAO sobrescreve (motivo='{reason}')")
            try:
                update_pending_escalation_status(
                    conv_id, 'in_progress',
                    note=f'Protecao distribute: handoff({ho_motivo_p}) ativo p/ {ho_target_p} — sem redistribuir.',
                )
            except Exception:
                pass
            return True
    except Exception as e_prot2:
        p(f"  [DIST] erro check handoff (segue mesmo assim): {e_prot2}")

    consultant = get_available_consultant(exclude_attendants=exclude_attendants)
    if not consultant:
        p(f"  [DIST] [MODE] human_unavailable — fallback nota interna (motivo='{reason}')")
        # (2026-06-30) Mesmo SEM consultor disponível, garante o lead criado/vinculado
        # à conversa, para não ficar 'Lead não encontrado' enquanto aguarda na fila.
        # Caso reportado (Gaby): atendimento normal, chegou nota mas o lead não foi
        # criado porque o fallback nunca criava lead.
        try:
            _nm_fb = (student_profile.get('name') if student_profile else '') or ''
            _ensure_lead_for_conv(phone=(_current_phone or PHONE_TO_MONITOR), name=_nm_fb)
        except Exception as e_lead_fb:
            p(f"  [DIST] erro garantindo lead (fila/human_unavailable): {e_lead_fb}")
        first_name = ''
        try:
            st_first = _conv_states.get(conv_id, {})
            if st_first.get('student_profile') and st_first['student_profile'].get('name'):
                first_name = ' ' + st_first['student_profile']['name'].split()[0]
        except Exception:
            pass
        busy_msg = HUMAN_BUSY_MSG.format(name=first_name)
        if _signature_recently_sent(conv_id, 'human_busy', window_s=4 * 3600):
            p(f"  [DIST] dedup: human_busy ja enviado nas ultimas 4h - suprimindo")
        else:
            meta_typing_on()
            send_and_track(conv_id, busy_msg)
            _register_signature(conv_id, 'human_busy', busy_msg)
            # (2026-05-27) TTL CURTO p/ permitir nova tentativa rapida quando
            # consultor voltar a estar disponivel. Antes era 6h e a conversa
            # ficava com a tag 'Transferencia solicitada' sem ser redistribuida.
            _mark_handoff_active(conv_id, 'human_unavailable', target='',
                                 ttl_s=5 * 60, body=busy_msg)
        transfer_to_human(conv_id, reason)
        try:
            record_pending_escalation(
                conv_id,
                reason='human_unavailable',
                tier='pending',
                retorno_label='assim que houver consultor disponível',
                question=(reason or '')[:500],
            )
        except Exception as e_pe:
            p(f"  [DIST] erro ao registrar pending_escalation: {e_pe}")
        return False

    nome = consultant['nome']
    p(f"  [DIST] Distribuindo para {nome}...")

    # Camada 3 (atomica entre processos): adquire lock Postgres ANTES de
    # qualquer transfer/envio. Se outro processo ja esta distribuindo esta
    # conv, fazemos skip silencioso e retornamos True (idempotente).
    if not _try_acquire_dispatch_lock(conv_id, target=nome, ttl_s=4 * 3600):
        p(f"  [DIST] {conv_id[:12]} dispatch lock perdido para concorrente — skip silencioso")
        return True

    lead_id = student_profile.get('lead_id', '') if student_profile else ''
    phone = _current_phone or PHONE_TO_MONITOR

    if not lead_id and phone:
        # (2026-06-30) Resolve/cria o lead com telefone NORMALIZADO (nacional),
        # casando com o contato da conversa — evita o bug do DDI 55 em que o lead
        # criado/encontrado não vinculava ('Lead não encontrado' no painel).
        contact_name = ''
        if student_profile and student_profile.get('name'):
            contact_name = student_profile['name']
        else:
            cached = _cached_msgs.get(conv_id, [])
            for cm in cached:
                cn = cm.get('contactName', '') or cm.get('senderName', '') or ''
                if cn:
                    contact_name = cn
                    break
        try:
            ensured = _ensure_lead_for_conv(phone=phone, name=contact_name)
            if ensured:
                lead_id = ensured
                p(f"  [DIST] Lead garantido para distribuição: {lead_id[:16]}")
        except Exception as e:
            p(f"  [DIST] Erro garantindo lead: {e}")

    # (2026-05-25) ALERTA visivel se transferimos sem lead — caso Larissa:
    # painel DCZ mostrava 'Lead nao encontrado' mesmo apos resgate. Aqui
    # registramos nota interna laranja na conv para o consultor saber que
    # precisa criar lead manual ou tentar de novo.
    if not lead_id:
        try:
            requests.post(
                f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                headers=H,
                json={
                    'body': (
                        '⚠️ *Atencao* — distribuicao sem lead vinculado. '
                        'Tentei criar (3x) mas API CRM nao retornou ID. '
                        'Cria/atribui o lead manualmente, por favor.'
                    ),
                    'isInternal': True,
                },
                timeout=10,
            )
        except Exception:
            pass
        p(f"  [DIST] {conv_id[:12]} ATENCAO: distribuindo SEM lead criado (apos 3 tentativas)")

    lead_ok = _dcz_transfer_lead(lead_id, nome)
    biz_ok = _dcz_transfer_business(phone, nome, lead_id=lead_id)
    chat_ok = _dcz_transfer_chat(conv_id, nome)

    p(f"  [DIST] Resultado parcial: lead={lead_ok} biz={biz_ok} chat={chat_ok}")

    if not chat_ok:
        p(f"  [DIST] ALERTA: chat transfer FALHOU - tentando change-attendant direto")
        nome_norm = nome.strip().lower()
        nome_norm = ''.join(c for c in __import__('unicodedata').normalize('NFD', nome_norm) if __import__('unicodedata').category(c) != 'Mn')
        att_id = ATTENDANT_MAP.get(nome_norm, '')
        if att_id:
            try:
                r_direct = requests.post(
                    f'{DCZ_MSG}/messaging/conversations/{conv_id}/change-attendant',
                    headers=H, json={'attendantId': att_id}, timeout=15
                )
                p(f"  [DIST] Change-attendant direto (status={r_direct.status_code})")
                if r_direct.status_code in (200, 201, 204):
                    chat_ok = True
            except Exception as e_d:
                p(f"  [DIST] Change-attendant direto erro: {e_d}")

    _supabase_increment_fila(consultant['id'], consultant['fila'])

    # === VERIFICACAO PoS-DISTRIBUICAO ===
    # Garante que o atendente real (lead+business+chat) bate com `nome`.
    # Sem isso o cliente recebe "Vou te transferir para X" mas o CRM/chat fica
    # com outro atendente (bug Vanessa -> chat Debora, lead Joyce).
    try:
        verify_result = _enforce_assignment_consistency(
            conv_id=conv_id, lead_id=lead_id, phone=phone, expected_name=nome,
            max_retries=4,
        )
        # se chat divergiu definitivo, atualiza chat_ok local com a realidade
        if verify_result.get('ok_chat') is False:
            chat_ok = False
        else:
            chat_ok = True
    except Exception as e_v:
        p(f"  [DIST] erro _enforce_assignment_consistency: {e_v}")
        verify_result = {'ok_lead': lead_ok, 'ok_biz': biz_ok, 'ok_chat': chat_ok}

    # === DETECCAO DE REDISTRIBUICAO ===
    # Se ja houve QUALQUER distribuicao para esta conv nos ultimos 30min
    # (independente do nome do atendente), tratar como redistribuicao:
    # suprimir mensagem ao cliente (evita "Vou te transferir para X" seguido
    # de "Vou te transferir para Y" — caso Jessica: Camila->Marilia) e
    # suprimir a nota verbose (substituida por nota curta sinalizando troca).
    is_redistribution = _signature_recently_sent(conv_id, 'dist_any_attendant', window_s=30 * 60)
    prev_target = ''
    try:
        prev_target = (_conv_states.get(conv_id, {}) or {}).get('_last_distributed_to') or ''
    except Exception:
        pass
    if is_redistribution:
        same_attendant = (prev_target or '').strip().lower() == nome.strip().lower()
        p(f"  [DIST] [REDIST] {conv_id[:12]} redistribuicao detectada "
          f"(prev={prev_target or '?'} -> {nome}, same={same_attendant}) "
          f"- suprimindo msg cliente + nota verbose")

    if is_redistribution:
        same_attendant = (prev_target or '').strip().lower() == nome.strip().lower()
        if same_attendant:
            # Mesma pessoa redistribuindo dentro da janela: SUPRIMIR completamente.
            # Nao ha sentido em enviar "Vou te transferir para Danubia" duas vezes,
            # nem nota nova "Distribuicao automatica Atendente Danubia" — o
            # atendente ja esta atribuido. So registra no log do agente.
            p(f"  [DIST] [REDIST-SAME] {conv_id[:12]} mesma atendente {nome} ja recente — suprimindo TUDO (nota e msg cliente)")
        else:
            # Atendente diferente: nota CURTA pra equipe ter rastro no historico.
            short_note = (f"♻️ Redistribuicao automatica — atendente trocado "
                          f"de *{prev_target or '?'}* para *{nome}*.")
            short_sig = f'dist_redist:{prev_target.lower() if prev_target else "x"}->{nome.lower()}'
            if _signature_recently_sent(conv_id, short_sig, window_s=4 * 3600):
                p(f"  [DIST] dedup: nota curta redist {prev_target}->{nome} ja enviada - suprimindo")
            else:
                try:
                    r_note = requests.post(
                        f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                        headers=H, json={'body': short_note, 'isInternal': True}, timeout=10
                    )
                    if r_note.status_code in (200, 201, 204):
                        _register_signature(conv_id, short_sig, short_note)
                except Exception:
                    pass
        # Em ambos os casos NaO envia mensagem ao cliente — ja recebeu
        # "Vou te transferir para X" da primeira distribuicao.
    else:
        # (2026-07-07) Enriquecimento: linha "📣 Origem" com a campanha do disparo
        # (se o aluno veio de um) e Motivo mais claro quando o retorno de disparo
        # nao trouxe um motivo especifico (ex.: aluno so respondeu "boa tarde").
        origem_line = _dispatch_origin_line(phone=phone, lead_id=lead_id)
        note_parts = ["🔔 *Distribuição automática pelo agente IA*", f"Atendente: *{nome}*"]
        if origem_line:
            note_parts.append(origem_line)
        if reason:
            note_parts.append(f"Motivo: {reason}")
        elif origem_line:
            note_parts.append("Motivo: Retorno de disparo (resposta curta/saudação)")
        note = "\n".join(note_parts)

        # Dedup da NOTA interna: nao passa por send_and_track entao precisa de
        # check explicito. Bloqueia segunda nota identica dentro de 4h.
        note_sig = f'dist_note:{nome.lower()}'
        if _signature_recently_sent(conv_id, note_sig, window_s=4 * 3600):
            p(f"  [DIST] dedup: nota interna p/ {nome} ja enviada nas ultimas 4h - suprimindo")
        else:
            try:
                r_note = requests.post(
                    f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                    headers=H, json={'body': note, 'isInternal': True}, timeout=10
                )
                if r_note.status_code in (200, 201, 204):
                    _register_signature(conv_id, note_sig, note)
            except Exception:
                pass

        client_msg = (
            f"Vou te transferir para *{nome}*, que vai dar continuidade ao seu atendimento. "
            f"Um momento, por favor! 😊"
        )
        # Dedup explicito tambem na mensagem ao cliente (alem do dedup por
        # conteudo do send_and_track), por seguranca extra.
        client_sig = f'dist_client:{nome.lower()}'
        if _signature_recently_sent(conv_id, client_sig, window_s=4 * 3600):
            p(f"  [DIST] dedup: mensagem cliente p/ {nome} ja enviada nas ultimas 4h - suprimindo")
        else:
            meta_typing_on()
            # force=True: garante que a propria msg de distribuicao nao seja
            # suprimida pelo dispatch-recent recheck que send_and_track agora faz.
            sent_ok = send_and_track(conv_id, client_msg, force=True)
            if sent_ok:
                _register_signature(conv_id, client_sig, client_msg)

    # Marca esta distribuicao para detectar redistribuicao na proxima.
    _register_signature(conv_id, 'dist_any_attendant', f'distribuido_para:{nome.lower()}')

    p(f"  [DIST] ✅ Distribuição concluída para {nome} (lead={lead_ok} biz={biz_ok} chat={chat_ok})")
    _conv_states.setdefault(conv_id, _default_conv_state())['_last_distributed_to'] = nome
    _conv_states[conv_id]['_human_took_over'] = True
    _conv_states[conv_id]['waiting_for_client'] = False
    _conv_states[conv_id]['inactivity_start'] = 0
    _conv_states[conv_id]['followup_stage'] = 0
    _conv_states[conv_id]['_last_responded_ts'] = time.time()
    try:
        _mark_handoff_active(conv_id, 'dispatch', target=nome, ttl_s=4 * 3600, body=client_msg)
    except Exception:
        pass
    update_pending_escalation_status(
        conv_id, 'in_progress',
        note='🔔 Distribuição humana concluída — fila Cockpit atualizada para *Em atendimento*.',
    )
    return True


# ============================================================
# TIME DE RETENÇÃO (2026-06-08)
# ============================================================
# A retenção é distribuída para um membro do time SEMPRE que a mensagem do aluno
# for caso de retenção — assim como funcionava com o Wesley hoje. NÃO consulta o
# dashboard de Ativo/Inativo: Wesley e Danúbia ficam de propósito como "Inativo"
# no painel (para NÃO entrar lead de atendimento normal), mas continuam recebendo
# retenção normalmente.
#
# Escolha do membro: STICKY primeiro (se a conversa já está com X, mantém X);
# senão, balanceia de forma determinística por conversa (hash do conv_id), o que
# divide ~50/50 entre os dois e, por ser determinístico, já é naturalmente sticky
# mesmo que o handoff tenha expirado.
RETENTION_TEAM = ['Wesley', 'Danubia']  # ordem = base do rodízio


def _retention_sticky_target(conv_id):
    """Se a conversa já está em retenção com um membro do time, devolve esse
    nome (mantém sticky). Senão, None."""
    try:
        motivo, tgt = _is_handoff_active(conv_id)
        if motivo in ('retention', 'retention_after_hours') and tgt:
            tnorm = _normalize_attendant_name(tgt).split()[0] if tgt else ''
            for m in RETENTION_TEAM:
                if _normalize_attendant_name(m).split()[0] == tnorm:
                    return m
    except Exception:
        pass
    return None


def choose_retention_target(conv_id):
    """Decide para quem vai a retenção: STICKY (se já atribuída a um membro) OU
    rodízio determinístico por conversa entre os membros do time. SEMPRE retorna
    um nome (retenção nunca fica sem dono); não considera Ativo/Inativo."""
    sticky = _retention_sticky_target(conv_id)
    if sticky:
        return sticky
    try:
        idx = (hash(str(conv_id)) & 0x7FFFFFFF) % len(RETENTION_TEAM)
    except Exception:
        idx = 0
    return RETENTION_TEAM[idx]


def _resolve_rgm_verified(lead_id=None, phone=None, cpf=None):
    """(2026-06-30) Resolve o RGM com IDENTIDADE confirmada — evita marcar o RGM de
    OUTRA pessoa (caso de telefone compartilhado/cadastro trocado).
    Regra: CPF e telefone devem apontar para o MESMO registro em mm_matriculados.
      - CPF e telefone resolvem o MESMO rgm  -> usa (alta confiança);
      - CPF resolve e o telefone do próprio registro do CPF bate -> usa;
      - só CPF (telefone sem registro acadêmico) -> usa (CPF é a identidade);
      - CPF x telefone DIVERGEM, ou só telefone sem CPF -> NÃO marca (None).
    Retorna (rgm|None, motivo). fetch_academic_data NÃO traz o rgm; por isso aqui.
    """
    if not cpf and lead_id:
        try:
            r = requests.get(f'{DCZ_CRM}/leads/{lead_id}', headers=H, timeout=10)
            if r.status_code == 200:
                cpf = (r.json().get('taxId') or '')
        except Exception:
            pass
    clean_cpf = re.sub(r'\D', '', str(cpf or ''))
    if clean_cpf and len(clean_cpf) < 11:
        clean_cpf = clean_cpf.zfill(11)
    cp = re.sub(r'\D', '', str(phone or ''))[-11:]
    if len(clean_cpf) != 11 and len(cp) < 10:
        return None, 'sem cpf/telefone'
    try:
        cfg = DB_CONFIG.copy()
        cfg['dbname'] = 'dcz_sync'
        cfg['connect_timeout'] = 5
        cfg['options'] = '-c statement_timeout=8000'
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()
        rgm_cpf = None
        fones_cpf = ''
        if len(clean_cpf) == 11:
            cur.execute("""SELECT rgm,
                    coalesce(fone_cel,'')||'|'||coalesce(fone_res,'')||'|'||coalesce(fone_com,'')
                FROM mm_matriculados WHERE cpf = %s AND rgm IS NOT NULL AND rgm <> ''
                ORDER BY (situacao = 'Matriculado') DESC, serie DESC LIMIT 1""", (clean_cpf,))
            row = cur.fetchone()
            if row:
                rgm_cpf = str(row[0]).strip()
                fones_cpf = re.sub(r'\D', '', row[1] or '')
        rgm_phone = None
        phone_rgm_count = 0
        if len(cp) >= 10:
            # (2026-07-06) Conta RGMs DISTINTOS do telefone: se for exatamente 1,
            # o telefone é inequívoco e pode ser usado SEM CPF. Se >1 (telefone
            # compartilhado, caso Livia), NÃO usa sem CPF — evita RGM de outra pessoa.
            cur.execute("""SELECT DISTINCT rgm FROM mm_matriculados
                WHERE (fone_cel LIKE %s OR fone_res LIKE %s OR fone_com LIKE %s)
                  AND rgm IS NOT NULL AND rgm <> ''""",
                (f'%{cp}', f'%{cp}', f'%{cp}'))
            distinct_rgms = [str(r[0]).strip() for r in cur.fetchall()]
            phone_rgm_count = len(distinct_rgms)
            if phone_rgm_count >= 1:
                cur.execute("""SELECT rgm FROM mm_matriculados
                    WHERE (fone_cel LIKE %s OR fone_res LIKE %s OR fone_com LIKE %s)
                      AND rgm IS NOT NULL AND rgm <> ''
                    ORDER BY (situacao = 'Matriculado') DESC, serie DESC LIMIT 1""",
                    (f'%{cp}', f'%{cp}', f'%{cp}'))
                row = cur.fetchone()
                if row:
                    rgm_phone = str(row[0]).strip()
        cur.close()
        conn.close()

        if rgm_cpf and rgm_phone:
            if rgm_cpf == rgm_phone:
                return rgm_cpf, 'cpf+telefone concordam'
            return None, f'divergencia cpf({rgm_cpf})x telefone({rgm_phone})'
        if rgm_cpf and cp and cp in fones_cpf:
            return rgm_cpf, 'cpf + telefone do registro batem'
        if rgm_cpf and not rgm_phone:
            return rgm_cpf, 'cpf (telefone sem registro academico)'
        # (2026-07-06) Sem CPF confirmando, mas o telefone aponta p/ 1 ÚNICO RGM na
        # base acadêmica -> inequívoco, usa. Só telefone compartilhado (>1 RGM) fica
        # travado exigindo CPF.
        if rgm_phone and phone_rgm_count == 1:
            return rgm_phone, 'telefone unico (1 rgm)'
        if rgm_phone and phone_rgm_count > 1:
            return None, f'telefone compartilhado ({phone_rgm_count} rgms) sem cpf'
        return None, 'sem confirmacao por cpf'
    except Exception as e:
        p(f"  [RGM] erro resolvendo (verificado): {e}")
        return None, 'erro'


def _ret_ia_fill_rgm_disparador(lead_id=None, phone=None, rgm=None):
    """(2026-06-30) (A) Preenche o RGM no painel do Disparador
    (disparos.activation_responses) para os registros 'Processos CAA_IA'
    (origem_ativacao='caa_ia') deste aluno que estejam sem RGM. Cruza por
    datacrazy_lead_id e/ou telefone. SÓ escreve quando há RGM resolvido.
    Escopo restrito a caa_ia — NUNCA toca em caa_atm/caa/financeiro/etc.
    Retorna nº de linhas atualizadas."""
    if not rgm or not (lead_id or phone):
        return 0
    try:
        cfg = DB_CONFIG.copy()
        cfg['dbname'] = 'disparos'
        cfg['connect_timeout'] = 5
        cfg['options'] = '-c statement_timeout=8000'
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()
        n = 0
        if lead_id:
            cur.execute("""UPDATE activation_responses SET rgm = %s
                WHERE category = 'processos-caa' AND origem_ativacao = 'caa_ia'
                  AND datacrazy_lead_id = %s
                  AND (rgm IS NULL OR rgm = '' OR lower(rgm) = 'undefined')""",
                (str(rgm), str(lead_id)))
            n += cur.rowcount
        if phone:
            tail = re.sub(r'\D', '', str(phone))[-8:]
            if len(tail) >= 8:
                cur.execute("""UPDATE activation_responses SET rgm = %s
                    WHERE category = 'processos-caa' AND origem_ativacao = 'caa_ia'
                      AND telefone LIKE %s
                      AND (rgm IS NULL OR rgm = '' OR lower(rgm) = 'undefined')""",
                    (str(rgm), f'%{tail}'))
                n += cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if n:
            p(f"  [RET-IA] RGM {rgm} preenchido no painel Disparador ({n} registro(s) caa_ia)")
        return n
    except Exception as e:
        p(f"  [RET-IA] erro preenchendo rgm no disparador: {e}")
        return 0


_RGM_BACKFILL_LAST = 0


def _ret_ia_backfill_rgm_disparador(max_leads=80, max_days=30):
    """(2026-06-30) Mantém o painel 'Processos CAA_IA' (disparos.activation_responses)
    correto, atendendo às regras:
      1) DEDUP — 1 linha por pessoa (datacrazy_lead_id): mantém a MAIS RECENTE e
         apaga as demais caa_ia (autorizado), evitando a mesma pessoa aparecer
         várias vezes;
      2) RGM verificado — re-resolve por CPF+telefone (_resolve_rgm_verified) e
         grava só quando a identidade confere; se NÃO confirmar, deixa o RGM em
         branco (nunca marca o RGM de outra pessoa).
    Escopo restrito a caa_ia — NUNCA toca em caa_atm/caa/financeiro/etc.
    Throttle interno de 10 min."""
    global _RGM_BACKFILL_LAST
    now = time.time()
    if now - _RGM_BACKFILL_LAST < 600:
        return
    _RGM_BACKFILL_LAST = now
    try:
        cfg = DB_CONFIG.copy()
        cfg['dbname'] = 'disparos'
        cfg['connect_timeout'] = 5
        cfg['options'] = '-c statement_timeout=20000'
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()
        cur.execute("""SELECT datacrazy_lead_id, max(telefone), count(*)
            FROM activation_responses
            WHERE category = 'processos-caa' AND origem_ativacao = 'caa_ia'
              AND datacrazy_lead_id IS NOT NULL AND datacrazy_lead_id <> ''
              AND received_at > now() - make_interval(days => %s)
            GROUP BY datacrazy_lead_id
            ORDER BY count(*) DESC, max(received_at) DESC
            LIMIT %s""", (max_days, max_leads))
        leads = cur.fetchall()
        deleted = 0
        fixed = 0
        blanked = 0
        for lead_id, phone, cnt in leads:
            # 1) DEDUP: mantém a linha caa_ia mais recente; apaga as outras
            if cnt and cnt > 1:
                cur.execute("""DELETE FROM activation_responses
                    WHERE category = 'processos-caa' AND origem_ativacao = 'caa_ia'
                      AND datacrazy_lead_id = %s
                      AND id <> (
                        SELECT id FROM activation_responses
                        WHERE category = 'processos-caa' AND origem_ativacao = 'caa_ia'
                          AND datacrazy_lead_id = %s
                        ORDER BY received_at DESC NULLS LAST LIMIT 1)""",
                    (lead_id, lead_id))
                deleted += cur.rowcount
            # 2) RGM verificado (CPF+telefone). Confere -> grava; não confere -> limpa
            rgm, _motivo = _resolve_rgm_verified(lead_id=lead_id, phone=phone)
            if rgm:
                cur.execute("""UPDATE activation_responses SET rgm = %s
                    WHERE category = 'processos-caa' AND origem_ativacao = 'caa_ia'
                      AND datacrazy_lead_id = %s
                      AND (rgm IS NULL OR rgm = '' OR lower(rgm) = 'undefined' OR rgm <> %s)""",
                    (rgm, lead_id, rgm))
                fixed += cur.rowcount
            else:
                cur.execute("""UPDATE activation_responses SET rgm = NULL
                    WHERE category = 'processos-caa' AND origem_ativacao = 'caa_ia'
                      AND datacrazy_lead_id = %s
                      AND rgm IS NOT NULL AND rgm <> ''""", (lead_id,))
                blanked += cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if deleted or fixed or blanked:
            p(f"  [RGM-BACKFILL] dedup_apagadas={deleted} rgm_ok={fixed} rgm_limpo={blanked}")
    except Exception as e:
        p(f"  [RGM-BACKFILL] erro: {e}")


def _ret_ia_ensure_business_atendimento(lead_id, phone=None):
    """(2026-06-30) Garante que o lead tem um negócio (deal) e o coloca na etapa
    *Atendimento* (pipeline Base de Alunos), independente do pipeline atual
    (Encerramento/Perdido). Necessário para a automação 'Retenção IA' acionar: a
    tag dispara o fluxo, mas o deal precisa estar num pipeline que a automação
    atenda. Cobre dois casos reportados:
      - aluno em Encerramento: tag/nota chegavam mas a automação não acionava;
      - aluno sem deal criado: idem.
    Retorna biz_id (existente, movido ou criado) ou None.
    """
    biz_id = None
    # 1) sub-recurso /leads/{id}/businesses (mais confiável)
    try:
        rb_sub = requests.get(f'{DCZ_CRM}/leads/{lead_id}/businesses', headers=H, timeout=10)
        if rb_sub.status_code == 200:
            bd = rb_sub.json()
            bl = bd.get('data', bd) if isinstance(bd, dict) else bd
            if isinstance(bl, list) and bl:
                biz_id = (bl[0] or {}).get('id')
    except Exception as e:
        p(f"  [RET-IA] erro buscando business do lead: {e}")
    # 2) fallback: search por telefone
    if not biz_id:
        try:
            ph_search = (phone or _current_phone or '')
            if ph_search:
                r_biz = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                                     params={'search': ph_search, 'limit': 5}, timeout=10)
                if r_biz.status_code == 200:
                    bdj = r_biz.json()
                    bl = bdj.get('data', bdj) if isinstance(bdj, dict) else bdj
                    for biz in (bl if isinstance(bl, list) else []):
                        bl_lead = biz.get('lead', {})
                        bl_lead_id = bl_lead.get('id', '') if isinstance(bl_lead, dict) else str(bl_lead)
                        if bl_lead_id == lead_id:
                            biz_id = biz.get('id')
                            break
        except Exception as e:
            p(f"  [RET-IA] erro search business: {e}")
    # 3) não tem deal -> cria já em Atendimento
    if not biz_id:
        try:
            r_new = requests.post(f'{DCZ_CRM}/businesses', headers=H,
                                  json={'leadId': lead_id, 'stageId': STAGE_ATENDIMENTO_ID}, timeout=12)
            if r_new.status_code in (200, 201):
                biz_id = (r_new.json() or {}).get('id')
                p(f"  [RET-IA] business criado p/ lead {lead_id} já em Atendimento -> {biz_id}")
            else:
                p(f"  [RET-IA] falha ao criar business (status={r_new.status_code})")
        except Exception as e:
            p(f"  [RET-IA] erro criando business: {e}")
        return biz_id
    # 4) já existe deal -> garante etapa Atendimento (tira de Encerramento/Perdido)
    try:
        r_mv = requests.patch(f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
                              json={'stageId': STAGE_ATENDIMENTO_ID}, timeout=10)
        p(f"  [RET-IA] business {biz_id} -> etapa Atendimento (status={r_mv.status_code})")
    except Exception as e:
        p(f"  [RET-IA] erro movendo business p/ Atendimento: {e}")
    return biz_id


def _ret_ia_phone_variants(phone):
    """(2026-06-30) Normaliza o telefone para a convenção do DataCrazy.
    O contato/lead da conversa é guardado em formato NACIONAL (sem o DDI 55),
    ex.: '15997582595'. Buscar/criar com '55...' não encontra nem vincula o lead
    à conversa. Retorna (national, [variações p/ busca])."""
    d = re.sub(r'\D', '', phone or '')
    if d.startswith('55') and len(d) in (12, 13):
        national = d[2:]
    else:
        national = d
    variants = []
    for v in (national, d, national[-11:] if len(national) >= 11 else national):
        if v and v not in variants:
            variants.append(v)
    return national, variants


def _lead_exists(lead_id):
    """Confirma que o lead_id realmente existe no CRM (evita usar lead 'fantasma').
    (2026-06-30) Só considera INEXISTENTE em 404 explícito. Em timeout/5xx/erro de
    rede assume que EXISTE — para não descartar um lead_id válido por falha
    transitória da API (o que abortaria tag+nota da retenção)."""
    if not lead_id:
        return False
    try:
        r = requests.get(f'{DCZ_CRM}/leads/{lead_id}', headers=H, timeout=10)
        if r.status_code == 404:
            return False
        return True
    except Exception:
        return True


def _ensure_lead_for_conv(lead_id=None, phone=None, name=''):
    """(2026-06-30) Garante um LEAD válido (existente ou criado) e VINCULÁVEL à
    conversa, evitando 'Lead não encontrado'. Usado tanto na retenção (antes de
    tag/nota) quanto na distribuição normal (antes de transferir ao consultor).
    Corrige o bug do DDI 55: normaliza o telefone p/ NACIONAL ao buscar E ao criar,
    casando com o contato da conversa. Retorna lead_id válido ou None."""
    # 1) lead_id recebido: só usa se REALMENTE existir no CRM
    if lead_id and _lead_exists(lead_id):
        return lead_id
    if lead_id:
        p(f"  [LEAD] lead_id {lead_id} recebido NAO existe no CRM — resolvendo/criando")
        lead_id = None

    national, variants = _ret_ia_phone_variants(phone or _current_phone or '')
    if not national:
        return None
    tail = national[-9:] if len(national) >= 9 else national

    # 2) procura lead existente por telefone (validando sufixo p/ evitar match errado)
    for term in variants:
        try:
            r = requests.get(f'{DCZ_CRM}/leads', headers=H,
                             params={'search': term, 'limit': 10}, timeout=12)
            if r.status_code != 200:
                continue
            data = r.json()
            leads = data.get('data', data) if isinstance(data, dict) else data
            for ld in (leads if isinstance(leads, list) else []):
                lph = re.sub(r'\D', '', str(ld.get('rawPhone') or ld.get('phone') or ''))
                if tail and lph.endswith(tail):
                    p(f"  [LEAD] lead existente localizado p/ ...{tail[-4:]}: {ld.get('id')}")
                    return ld.get('id')
        except Exception as e:
            p(f"  [LEAD] erro buscando lead ({term}): {e}")

    # 3) não existe -> cria com telefone NACIONAL (casa com o contato da conversa)
    try:
        new_lead_id, _ = create_lead_and_business(national, name or '')
        if new_lead_id:
            p(f"  [LEAD] lead criado (nacional) p/ ...{tail[-4:]}: {new_lead_id}")
            return new_lead_id
    except Exception as e:
        p(f"  [LEAD] erro criando lead: {e}")
    return None


def _trigger_retention_tag_only(conv_id, lead_id, question, phone=None):
    """(2026-06-25) Fluxo de TESTE da automação "Retenção IA": em vez de distribuir,
    apenas:
      1) adiciona a tag RET-IA no lead -> dispara a automação (n8n) no DataCrazy;
      2) registra nota interna (para o consultor entender o caso);
      3) silencia o bot nessa conversa (a automação assume).
    NÃO atribui consultor, NÃO move etapa, NÃO transfere chat, NÃO fala com o aluno.
    Dedup por CONVERSA (6h): só suprime se já acionou RET-IA nesta conversa nas
    últimas 6h. Fora dessa janela, re-aciona via TOGGLE da tag (remove+add) para
    gerar um novo evento "tag adicionada" e re-disparar a automação (caso de aluno
    que volta dias depois — ex.: "Gestão RH").
    Retorna 'RET-IA' em sucesso; None se não conseguiu resolver/criar o lead.
    """
    try:
        # (2026-06-30) GARANTE o lead ANTES de tag/nota — regra: o lead deve ser
        # criado (se não existir) antes de adicionar a tag e a nota, para a
        # automação 'Retenção IA' sempre acionar. _ensure_lead_for_conv valida o
        # lead_id recebido, busca por telefone (normalizando o DDI 55 -> nacional)
        # e cria com o formato nacional p/ casar com o contato da conversa
        # (evita 'Lead não encontrado' no painel).
        orig_lead_id = lead_id
        lead_id = _ensure_lead_for_conv(lead_id=lead_id, phone=phone)

        if not lead_id:
            # (2026-06-30) NÃO bloquear tag/nota por falha transitória de resolução.
            # Se havia um lead_id de entrada, usa ele (melhor tagear um lead possivel-
            # mente válido do que não acionar a automação). Só aborta se não há lead
            # algum (nem recebido, nem criável).
            if orig_lead_id:
                p(f"  [RET-IA] resolução de lead falhou — usando lead_id original ({orig_lead_id}) p/ NAO bloquear tag/nota")
                lead_id = orig_lead_id
            else:
                p(f"  [RET-IA] Sem lead válido e nao conseguiu criar — automação RET-IA NAO acionada")
                return None

        # (2026-06-25) Dedup por CONVERSA, não pelo estado permanente do lead.
        # Antes: se a tag RET-IA já existia no lead, NADA acontecia — aluno que
        # voltava dias depois com nova intenção de cancelar não re-acionava a
        # automação (caso "Gestão RH"). Agora só suprime se já acionamos RET-IA
        # NESTA conversa nas últimas 6h; fora disso, re-aciona (toggle da tag) +
        # re-posta a nota.
        if _signature_recently_sent(conv_id, 'ret_ia', window_s=6 * 3600):
            p(f"  [RET-IA] dedup: RET-IA ja acionada nesta conversa nas ultimas 6h — apenas silencia")
        else:
            # (2026-06-30) ANTES de marcar a tag, garante que o lead tem deal e o
            # move p/ Atendimento (Base de Alunos), independente do pipeline atual
            # (Encerramento/Perdido). Assim a automação 'Retenção IA' aciona mesmo
            # para aluno em Encerramento ou sem deal criado.
            try:
                _ret_ia_ensure_business_atendimento(lead_id, phone=phone)
            except Exception as e_bz:
                p(f"  [RET-IA] ensure business/Atendimento erro: {e_bz}")

            other_tags = []
            already = False
            try:
                r_lead = requests.get(f'{DCZ_CRM}/leads/{lead_id}', headers=H, timeout=10)
                if r_lead.status_code == 200:
                    for t in (r_lead.json().get('tags') or []):
                        tid = t.get('id', '')
                        if not tid:
                            continue
                        if tid == RET_IA_TAG_ID:
                            already = True
                        else:
                            other_tags.append({'id': tid})
            except Exception as e_gt:
                p(f"  [RET-IA] erro lendo tags do lead: {e_gt}")

            # A automação do DataCrazy dispara no gatilho "tag RET-IA adicionada".
            # Se a tag já existe, re-enviá-la NÃO gera novo evento; por isso
            # fazemos toggle: remove e re-adiciona, criando um "tag adicionada" novo.
            if already:
                try:
                    requests.patch(
                        f'{DCZ_CRM}/leads/{lead_id}', headers=H,
                        json={'tags': other_tags}, timeout=10
                    )
                    p(f"  [RET-IA] tag RET-IA removida (toggle) p/ re-disparar automação")
                    time.sleep(1.5)
                except Exception as e_rm:
                    p(f"  [RET-IA] erro ao remover tag (toggle): {e_rm}")

            # adiciona RET-IA com retry -> não pode ficar sem a tag se o remove passou
            add_ok = False
            for _try in range(3):
                try:
                    r = requests.patch(
                        f'{DCZ_CRM}/leads/{lead_id}', headers=H,
                        json={'tags': other_tags + [{'id': RET_IA_TAG_ID}]}, timeout=10
                    )
                    add_ok = r.status_code in (200, 201, 204)
                    p(f"  [RET-IA] tag RET-IA {'re-' if already else ''}adicionada (status={r.status_code}) -> aciona automação 'Retenção IA'")
                    if add_ok:
                        break
                except Exception as e_pt:
                    p(f"  [RET-IA] erro ao adicionar tag RET-IA (try {_try + 1}): {e_pt}")
                time.sleep(1)
            if already and not add_ok:
                p(f"  [RET-IA] ALERTA: toggle removeu a tag mas o re-add FALHOU no lead {lead_id}")

            try:
                origem_line = _dispatch_origin_line(phone=phone, lead_id=lead_id)
                note = (
                    f"🔴 *Retenção - Agente IA*\n"
                    f"O aluno manifestou intenção de cancelamento/trancamento.\n"
                    + (f"{origem_line}\n" if origem_line else "")
                    + f"Mensagem: \"{(question or '')[:120]}\"\n"
                    f"Acionada a automação de Retenção (tag RET-IA). O agente NÃO distribuiu nem respondeu."
                )
                requests.post(
                    f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                    headers=H, json={'body': note, 'isInternal': True}, timeout=10
                )
                p(f"  [RET-IA] Nota interna enviada na conversa")
            except Exception as e_nt:
                p(f"  [RET-IA] erro ao enviar nota interna: {e_nt}")

            _register_signature(conv_id, 'ret_ia', question or 'ret_ia')

        try:
            # (2026-06-30) TTL 72h (era 8h): o handoff protege a conversa do
            # follow-up/auto-close enquanto o time/automação de Retenção assume.
            # Com 8h ele expirava antes do atendimento (caso Maria Clara: fechada
            # ~16h depois esperando o consultor).
            _mark_handoff_active(conv_id, 'retention', target='',
                                 ttl_s=72 * 3600, body='Automação RET-IA acionada')
        except Exception:
            pass
        st = _conv_states.setdefault(conv_id, _default_conv_state())
        st['_human_took_over'] = True
        st['waiting_for_client'] = False
        st['inactivity_start'] = 0
        st['followup_stage'] = 0
        st['_last_responded_ts'] = time.time()

        # (2026-06-30) (A) Garante o RGM no painel do Disparador (caa_ia):
        # resolve o RGM por telefone (dcz_sync.mm_matriculados) e preenche os
        # registros 'Processos CAA_IA' deste aluno que estejam sem RGM. O backfill
        # periódico (B) cobre as linhas criadas pelo disparador após esta tag.
        try:
            _rgm, _ = _resolve_rgm_verified(lead_id=lead_id, phone=(phone or _current_phone))
            if _rgm:
                _ret_ia_fill_rgm_disparador(lead_id=lead_id, phone=(phone or _current_phone), rgm=_rgm)
        except Exception as e_rgm:
            p(f"  [RET-IA] erro no fill de RGM: {e_rgm}")

        return 'RET-IA'

    except Exception as e:
        p(f"  [RET-IA] Erro: {e}")
        return None


def trigger_retention(conv_id, lead_id, question, phone=None, target_name=None):
    """Aciona Retenção: tag + responsável (Wesley OU Danúbia) no lead + business
    -> ATENDIMENTO + nota interna + transfere o chat. STICKY e por disponibilidade.

    target_name: se informado, usa esse membro do time; senão escolhe via
    choose_retention_target (sticky + menor fila entre ativos).

    Retorna o nome do atendente atribuído (str) ou None se ninguém do time
    estiver ativo (caller deve segurar e re-tentar).

    (2026-05-27) Se lead_id=None, tenta resolver via telefone (identify_student)
    e, se ainda assim falhar, cria lead+business novos.
    """
    # (2026-06-25) TESTE: só para o(s) telefone(s) de teste, aciona a automação
    # RET-IA (tag) em vez de distribuir. Demais alunos seguem o fluxo normal abaixo.
    if _use_ret_ia_automation(phone or _current_phone):
        p(f"  [RETENÇÃO] telefone de TESTE -> fluxo RET-IA (tag/automação), sem distribuir")
        return _trigger_retention_tag_only(conv_id, lead_id, question, phone=phone)

    # (0) Decide o alvo
    alvo = target_name or choose_retention_target(conv_id)
    if not alvo:
        p(f"  [RETENÇÃO] Nenhum membro do time ativo agora — segurando (sem forçar)")
        return None
    crm_id = _lookup_attendant_id(alvo, CRM_ATTENDANT_MAP) or RETENTION_WESLEY_CRM_ID
    p(f"  [RETENÇÃO] Alvo escolhido: {alvo} (crm_id={crm_id[:8]}...)")
    try:
        # (1) Resolve lead_id se nao veio
        if not lead_id:
            ph = (phone or _current_phone or '').replace('+','').replace(' ','').replace('-','')
            if ph:
                try:
                    prof = identify_student(ph)
                    if prof and prof.get('lead_id'):
                        lead_id = prof['lead_id']
                        p(f"  [RETENÇÃO] lead_id resolvido via phone -> {lead_id}")
                except Exception as e_id:
                    p(f"  [RETENÇÃO] identify_student erro: {e_id}")
            if not lead_id and ph:
                try:
                    new_lead_id, _ = create_lead_and_business(ph, '')
                    if new_lead_id:
                        lead_id = new_lead_id
                        p(f"  [RETENÇÃO] lead+business criados -> {lead_id}")
                except Exception as e_cr:
                    p(f"  [RETENÇÃO] create_lead_and_business erro: {e_cr}")

        if lead_id:
            r_lead = requests.get(f'{DCZ_CRM}/leads/{lead_id}', headers=H, timeout=10)
            existing_tags = []
            if r_lead.status_code == 200:
                lead_data = r_lead.json()
                for t in (lead_data.get('tags') or []):
                    tid = t.get('id', '')
                    if tid:
                        existing_tags.append({'id': tid})

            tag_already = any(t.get('id') == RETENTION_TAG_ID for t in existing_tags)
            if not tag_already:
                existing_tags.append({'id': RETENTION_TAG_ID})

            r = requests.patch(
                f'{DCZ_CRM}/leads/{lead_id}', headers=H,
                json={'tags': existing_tags, 'attendant': {'id': crm_id}},
                timeout=10
            )
            p(f"  [RETENÇÃO] Lead: tag + attendant {alvo} (status={r.status_code})")

            try:
                # Busca business: primeiro sub-recurso /leads/{id}/businesses (mais confiavel)
                biz_id = None
                try:
                    rb_sub = requests.get(f'{DCZ_CRM}/leads/{lead_id}/businesses',
                                          headers=H, timeout=10)
                    if rb_sub.status_code == 200:
                        bd = rb_sub.json()
                        bl = bd.get('data', bd) if isinstance(bd, dict) else bd
                        if isinstance(bl, list) and bl:
                            biz_id = (bl[0] or {}).get('id')
                except Exception:
                    pass
                # Fallback: search por telefone
                if not biz_id:
                    ph_search = (phone or _current_phone or PHONE_TO_MONITOR)
                    r_biz = requests.get(
                        f'{DCZ_CRM}/businesses', headers=H,
                        params={'search': ph_search, 'limit': 5}, timeout=10
                    )
                    if r_biz.status_code == 200:
                        biz_data = r_biz.json()
                        biz_list = biz_data.get('data', biz_data) if isinstance(biz_data, dict) else biz_data
                        for biz in (biz_list if isinstance(biz_list, list) else []):
                            biz_lead = biz.get('lead', {})
                            biz_lead_id = biz_lead.get('id', '') if isinstance(biz_lead, dict) else str(biz_lead)
                            if biz_lead_id == lead_id:
                                biz_id = biz.get('id')
                                break
                if biz_id:
                    rb = requests.patch(
                        f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
                        json={'attendantId': crm_id}, timeout=10
                    )
                    p(f"  [RETENÇÃO] Negócio attendant -> {alvo} (status={rb.status_code})")
                    rb2 = requests.patch(
                        f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
                        json={'stageId': STAGE_ATENDIMENTO_ID}, timeout=10
                    )
                    p(f"  [RETENÇÃO] Negócio -> Atendimento (status={rb2.status_code})")
                else:
                    p(f"  [RETENÇÃO] Nenhum negocio encontrado para lead={lead_id}")
            except Exception as e2:
                p(f"  [RETENÇÃO] Erro ao atualizar negócio: {e2}")
        else:
            p(f"  [RETENÇÃO] Sem lead_id e nao conseguiu criar — transferindo chat mesmo assim")

        note = (
            f"🔴 *Retenção - Agente IA*\n"
            f"O aluno manifestou intenção de cancelamento/trancamento.\n"
            f"Mensagem: \"{question[:120]}\"\n"
            f"Transferido automaticamente para {alvo} (Retenção)."
        )
        requests.post(
            f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
            headers=H, json={'body': note, 'isInternal': True}, timeout=10
        )
        p(f"  [RETENÇÃO] Nota interna enviada na conversa")

        _dcz_transfer_chat(conv_id, alvo)
        p(f"  [RETENÇÃO] Chat transferido para {alvo}")

        # Marca handoff STICKY com o alvo real (evita bouncing entre membros).
        try:
            _mark_handoff_active(conv_id, 'retention', target=alvo,
                                 ttl_s=8 * 3600, body=note)
        except Exception:
            pass

        _conv_states.setdefault(conv_id, _default_conv_state())['_human_took_over'] = True
        _conv_states[conv_id]['waiting_for_client'] = False
        _conv_states[conv_id]['inactivity_start'] = 0
        _conv_states[conv_id]['followup_stage'] = 0
        _conv_states[conv_id]['_last_responded_ts'] = time.time()

        # (2026-06-17) Registra/atualiza no feedback como tema='RETENÇÃO'
        # (controle do que vai para a Retenção). Substitui linha DISPARO da
        # mesma conversa, se houver. A coluna RESPOSTA e preenchida depois.
        try:
            _log_retention_interaction(conv_id, phone or _current_phone, None, question, alvo)
        except Exception:
            pass

        return alvo

    except Exception as e:
        p(f"  [RETENÇÃO] Erro: {e}")
        return None


RETENTION_SINGLE_WORDS = [
    'cancelar', 'trancar', 'cancelamento', 'trancamento', 'desistir',
]
RETENTION_URGENCY_PHRASES = [
    'acionar a justiça', 'acionar a justica', 'acionar justiça', 'acionar justica',
    'vou processar', 'entrar na justiça', 'entrar na justica',
    'procon', 'reclame aqui', 'advogado', 'processo judicial',
]

def is_retention_intent(text):
    """Detecta intenção REAL de cancelar/trancar. Ignora perguntas sobre o processo."""
    t = text.lower().strip()
    if any(u in t for u in RETENTION_URGENCY_PHRASES):
        return True
    if any(q in t for q in RETENTION_QUESTION_WORDS):
        return False
    for phrase in RETENTION_PHRASES:
        if phrase in t:
            return True
    for word in RETENTION_SINGLE_WORDS:
        if word in t:
            return True
    return False


# Pistas de que o aluno está PEDINDO o atendente pelo nome (não só citando casualmente).
_ATTENDANT_REQUEST_HINTS = (
    'falar com', 'queria com', 'quero com', 'queria o ', 'queria a ',
    'quero o ', 'quero a ', 'pode ser o ', 'pode ser a ',
    'gostaria de falar com', 'pode chamar', 'me passa pra', 'me passa o ',
    'me passa a ', 'manda pro ', 'manda pra ',
)

def detect_preferred_attendant(text):
    """Detecta se o aluno está pedindo um consultor especifico pelo nome.

    Para casar exige UM hint de 'pedido' + nome do consultor. Isso evita falso
    positivo do tipo 'Ontem a Mariana ja me ajudou' (que cita o nome sem pedir).
    Retorna o nome canônico (capitalizado) ou None.
    """
    if not text:
        return None
    t = text.lower().strip()
    has_hint = any(h in t for h in _ATTENDANT_REQUEST_HINTS)
    if not has_hint:
        return None
    for canonical, aliases in ATTENDANT_ALIASES.items():
        for al in aliases:
            if al.strip() in t:
                return canonical.capitalize()
    return None


def get_active_preferred_attendant_promise(conv_id, max_age_hours=24):
    """Retorna a 'promessa' (preferred_attendant) ativa para essa conversa, se houver.

    Retorna dict {name, created_at, hours_ago, was_yesterday, escalation_id} ou None.
    Só considera escalations com status pending/in_progress e idade <= max_age_hours.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, preferred_attendant, created_at,
                   EXTRACT(EPOCH FROM (NOW() - created_at))/3600 AS hours_ago
              FROM pending_escalation
             WHERE conv_id = %s
               AND status IN ('pending', 'in_progress')
               AND preferred_attendant IS NOT NULL
               AND preferred_attendant <> ''
               AND created_at > NOW() - INTERVAL '%s hours'
             ORDER BY created_at DESC
             LIMIT 1
        """, (conv_id, max_age_hours))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return None
        eid, name, created_at, hours_ago = row
        try:
            now_sp = _now_sp()
            created_sp = created_at + timedelta(hours=-3) if created_at.tzinfo is None else created_at.astimezone(now_sp.tzinfo) if hasattr(now_sp, 'tzinfo') else created_at
            was_yesterday = (now_sp.date() != created_at.date())
        except Exception:
            was_yesterday = float(hours_ago or 0) > 12
        return {
            'escalation_id': eid,
            'name': name,
            'created_at': created_at,
            'hours_ago': float(hours_ago or 0),
            'was_yesterday': bool(was_yesterday),
        }
    except Exception as e:
        p(f"  [PROMISE] erro consulta: {e}")
        return None


def is_attendant_active_now(attendant_name):
    """Verifica no Supabase se o consultor está Ativo (passa nos mesmos filtros do dispatch)."""
    try:
        nome_norm = attendant_name.strip().lower()
        nome_norm = ''.join(c for c in __import__('unicodedata').normalize('NFD', nome_norm)
                            if __import__('unicodedata').category(c) != 'Mn')
        # Filtro de ferias/afastamento (2026-05-25)
        if nome_norm.split()[0] in _ATTENDANTS_ON_VACATION:
            return False
        url = (f'{SUPABASE_URL}/rest/v1/{DISTRIBUICAO_TABLE}'
               f'?ativo_inativo=eq.Ativo&tipo_atendimento=eq.Atendimento&select=*')
        r = requests.get(url, headers=SUPABASE_HEADERS, timeout=10)
        if r.status_code != 200:
            return False
        now = _now_sp()
        dow = now.weekday()
        fim_de_semana = dow >= 5
        for row in r.json() or []:
            resp = (row.get('responsavel') or '').strip().lower()
            resp_norm = ''.join(c for c in __import__('unicodedata').normalize('NFD', resp)
                                if __import__('unicodedata').category(c) != 'Mn')
            first = resp_norm.split()[0] if resp_norm else ''
            if first != nome_norm.split()[0]:
                continue
            if row.get('status_almoco') != 'Ativo':
                return False
            if row.get('status_final_expediente') != 'Ativo':
                return False
            fila = int(row.get('fila') or 0)
            limite = int(row.get('volume_distribuicao') or 10)
            if fila >= limite:
                return False
            almoco_hora = row.get('almoco_real') or row.get('almoco')
            saida_hora = row.get('final_expediente')
            if not fim_de_semana and _em_intervalo(almoco_hora, ALMOCO_ANTE_MIN, ALMOCO_DURACAO_MIN, now):
                return False
            if _em_intervalo(saida_hora, SAIDA_ANTE_MIN, 0, now):
                return False
            return True
        return False
    except Exception as e:
        p(f"  [PROMISE] is_active erro: {e}")
        return False


def honor_preferred_attendant_promise(conv_id, promise):
    """Cumpre a promessa: transfere para o consultor prometido + msg humanizada.

    Se o consultor ainda não está ativo, envia msg explicando e mantém a fila aguardando.
    Retorna True se a interação foi consumida (não chamar fluxo normal depois).
    """
    name = promise.get('name') or ''
    if not name:
        return False
    first_name = name.split()[0]
    st = _conv_states.setdefault(conv_id, _default_conv_state())
    student_first = ''
    prof = st.get('student_profile') or {}
    if prof.get('first_name'):
        student_first = ', *' + prof['first_name'] + '*'

    if is_attendant_active_now(name):
        was_yesterday = bool(promise.get('was_yesterday'))
        when_phrase = 'ontem' if was_yesterday else 'mais cedo'
        msg = (
            f"Oii{student_first}! Conforme combinamos {when_phrase}, vou te conectar "
            f"com o(a) *{first_name}* agora 😊\n\n"
            f"Em pouquinho ele(a) assume aqui e dá continuidade ao seu atendimento."
        )
        meta_typing_on()
        send_and_track(conv_id, msg)
        log_to_db(conv_id, '[promessa_honrada]', msg, 1.0, 'preferred_attendant_honored')

        try:
            lead_id = ''
            if prof.get('lead_id'):
                lead_id = prof['lead_id']
            if lead_id:
                _dcz_transfer_lead(lead_id, name)
            biz_id = ''
            if prof.get('business_id'):
                biz_id = prof['business_id']
            if biz_id:
                _dcz_transfer_business(biz_id, name)
            _dcz_transfer_chat(conv_id, name)
        except Exception as e_t:
            p(f"  [PROMISE] erro na transferência DCZ: {e_t}")

        try:
            note = (
                f"🤝 *Promessa honrada* — IA havia avisado o aluno que o(a) {first_name} "
                f"daria continuidade. Conversa transferida automaticamente."
            )
            requests.post(
                f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                headers=H, json={'body': note, 'isInternal': True}, timeout=10,
            )
        except Exception:
            pass

        try:
            update_pending_escalation_status(
                conv_id, 'resolved',
                note=f'Conversa transferida para {first_name} (promessa cumprida).',
            )
        except Exception as e_pe:
            p(f"  [PROMISE] erro update escalation: {e_pe}")

        st['_human_took_over'] = True
        st['waiting_for_client'] = False
        st['_last_responded_ts'] = time.time()
        _clear_handoff_active(conv_id, reason='promise_honored')
        p(f"  [PROMISE] Honrada -> {first_name} (conv={conv_id[:12]})")
        return True

    msg = (
        f"Oii{student_first}! O(a) *{first_name}* ainda não retomou o atendimento por aqui, "
        f"mas é quem vai dar continuidade com você 🤝\n\n"
        f"Assim que ele(a) estiver disponível eu te conecto. Se preferir não esperar, "
        f"posso te passar para outro(a) consultor(a) que já está atendendo — é só me avisar 😊"
    )
    meta_typing_on()
    send_and_track(conv_id, msg)
    log_to_db(conv_id, '[promessa_aguardando]', msg, 1.0, 'preferred_attendant_waiting')
    st['waiting_for_client'] = True
    st['_last_responded_ts'] = time.time()
    p(f"  [PROMISE] {first_name} ainda inativo - aluno informado (conv={conv_id[:12]})")
    return True


def get_conversation_messages_api(conv_id, limit=15):
    try:
        r = requests.get(f'{DCZ_MSG}/messaging/conversations/{conv_id}/messages',
                        headers=H, params={'limit': limit}, timeout=20)
        if r.status_code != 200:
            return []
        return r.json().get('messages', [])
    except Exception as e:
        p(f"  Erro msgs conv={conv_id[:12]}: {e}")
        return []


BOT_RESPONSE_FINGERPRINTS = [
    'Essa é uma dúvida que precisa de um atendente',
    'Vou te transferir para um atendente',
    'Vou te conectar com um dos nossos atendentes',
    'Bem-vindo(a) ao Suporte',
    'Bem-vindo ao Suporte',
    'Como posso te ajudar?',
    'Que bom que pude ajudar',
    'Obrigado pelo contato',
    'Entendi sua situação',
    '[CONFIANCA:',
    'Selecione uma das opções',
    'Acesso ao Portal / App Duda',
    'Selecione para dar andamento',
    'não encontrei uma resposta exata',
    'Não entendi',
    'Não consegui entender',
    'Não consegui pegar direito',
    'Me ajuda a te ajudar',
    'Posso te ajudar de outra forma',
    'Clique em uma das opções disponíveis',
    'Teria mais alguma dúvida',
    'clique em uma das opções',
    'teria mais alguma dúvida',
    'Consegui te ajudar com isso',
    'Espero que tenha ajudado',
    'Ficou tudo certo por aí',
    'Fico feliz que deu certo',
    'Fico feliz em ter ajudado',
    'Foi ótimo poder te ajudar',
    'Tudo certo então',
    'Que bom que deu tudo certo',
]


def _is_template_message(msg):
    """Detecta mensagens de template/HSM do WhatsApp (disparos).
    Templates costumam vir com `header` na API DataCrazy ou type template/hsm."""
    if not msg or not isinstance(msg, dict):
        return False
    header = msg.get('header')
    if header and isinstance(header, str) and header.strip():
        return True
    t = (msg.get('type') or msg.get('messageType') or '').lower()
    if t in ('template', 'hsm', 'whatsapp_template'):
        return True
    return False


def is_bot_message(body):
    """Detect if a message is from a bot (ours or DataCrazy salesbot)."""
    b = body.lower()
    for fp in BOT_RESPONSE_FINGERPRINTS:
        if fp.lower() in b:
            return True
    for fp in OUR_MSG_FINGERPRINTS:
        if fp in b:
            return True
    return False


_cached_msgs = {}
_last_processed_msg_id = None
_startup_ts = 0

OUR_MSG_FINGERPRINTS = (
    'como posso te ajudar', 'escolha uma opção', 'selecione o assunto',
    'assistente virtual', 'algo mais específico', 'ficou alguma dúvida',
    'que bom que pude ajudar', 'vou te transferir', 'vou te encaminhar',
    'claro!', 'claro,',
    'ainda está por aí?', 'se tiver mais alguma dúvida, é só falar',
    'não tivemos retorno', 'vou finalizar o contato',
    'distribuição automática pelo agente ia',
    'espero que tenha conseguido te ajudar',
    'escolha uma opção abaixo para agilizar',
    'muito obrigado por falar com a gente',
    'encerrando esta conversa por aqui',
    'obrigado pelo contato',
    'ficamos 5 minutos sem interagir',
    'ficamos sem interagir',
    'este atendimento está sendo encerrado',
    'encerramento automático',
    'moveu para o departamento',
    'finalizou o atendimento',
    'essa conversa foi encerrada devido',
    'falta de interação',
    'percebi que você não respondeu',
    'nenhuma nova mensagem foi recebida',
    'vou encerrar esta conversa',
    # Disparos/campanhas (templates HSM enviados via Cockpit). Sem estes
    # fingerprints, _check_human_took_over confundia disparo com humano e
    # o agente recuava da conversa. Caso 2026-05-25: disparo de mensalidade
    # + atividade de extensao, 330 conversas ficaram sem atendimento.
    'passando para te ajudar',
    'passando para ajudar',
    'organização da sua rotina',
    'prazos importantes que vencem hoje',
    'lembrar de dois prazos',
    'atividade de extensão',
    'projeto pelo blackboard',
    'se não tiver essa disciplina em sua grade',
    'pode desconsiderar',
    'bons estudos',
    'nossa equipe permanece por aqui',
    # Automação/fluxos do DataCrazy (não são humano, são bots de menu/trancamento).
    # Frases específicas o suficiente para evitar falso match em mensagens reais.
    'quer solicitar o trancamento',
    'solicitar o trancamento da sua matrícula',
    'siga os passos abaixo',
    'o processo pode levar até 30 dias',
    'receberá uma ligação ou mensagem de validação',
    'atenção: você receberá uma ligação',
    'clique em uma das opções disponíveis',
    'assista ao tutorial para entender',
    'em breve um de nossos consultores irá te chamar',
    'sobre *acesso*, qual sua dúvida',
    'sobre *financeiro*, qual sua dúvida',
    'sobre *aulas e conteúdo*',
    'sobre *documentos*, qual sua dúvida',
    'sobre *rematrícula*',
    'é rapidinho! 😉',
    'ficou claro?',
)

def _automation_already_responded(conv_id, user_msg_id):
    """Verifica se a automação do DataCrazy já enviou resposta após a mensagem do usuário."""
    msgs = _cached_msgs.get(conv_id, [])
    found_user_msg = False
    for m in msgs:
        mid = m.get('id', '')
        if mid == user_msg_id:
            found_user_msg = True
            continue
        if found_user_msg and not m.get('received', False):
            body = (m.get('body') or m.get('text') or '').strip()
            if not body:
                continue
            body_lower = body.lower()
            is_our_msg = any(fp in body_lower for fp in OUR_MSG_FINGERPRINTS)
            if is_our_msg:
                continue
            if len(body) > 15:
                return True
    return False

def _recheck_automation(conv_id, msg_id):
    """Re-busca mensagens e verifica novamente se automação respondeu."""
    fresh = get_conversation_messages_api(conv_id, limit=10)
    _cached_msgs[conv_id] = fresh
    return _automation_already_responded(conv_id, msg_id)

def _wait_automation_finish(conv_id, max_wait=30, stable_time=5):
    """Espera a automação do DataCrazy terminar de enviar todas as mensagens.
    Monitora a contagem de mensagens e aguarda ficar estável por stable_time segundos."""
    prev_count = 0
    stable_since = time.time()
    start = time.time()
    while time.time() - start < max_wait:
        msgs = get_conversation_messages_api(conv_id, limit=15)
        _cached_msgs[conv_id] = msgs
        out_count = sum(1 for m in msgs if not m.get('received', False))
        if out_count != prev_count:
            prev_count = out_count
            stable_since = time.time()
        elif time.time() - stable_since >= stable_time:
            break
        time.sleep(2)
    p(f"    Automação estável após {time.time() - start:.0f}s ({prev_count} msgs saída)")


def _check_human_took_over(conv_id):
    """Verifica se um consultor humano enviou mensagem na conversa.
    Padrões de distribuição só contam se foram enviados NESTA sessão."""
    msgs = _cached_msgs.get(conv_id) or get_conversation_messages_api(conv_id, limit=10)

    DIST_PATTERNS = ['distribuição automática pelo agente ia', 'vou te transferir para',
                     'em breve um de nossos consultores', 'encaminhada para wesley']
    for m in msgs:
        body = (m.get('body', '') or '').strip()
        if not body:
            continue
        mid = m.get('id', '')
        if mid.startswith('sent_'):
            body_lower = body.lower()
            if any(pat in body_lower for pat in DIST_PATTERNS):
                return True

    for m in msgs:
        if m.get('received', True):
            continue
        mid = m.get('id', '')
        if mid in processed_msg_ids:
            continue
        msg_ts = m.get('createdAt', '') or m.get('timestamp', '') or ''
        if not msg_ts:
            continue
        try:
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(str(msg_ts).replace('Z', '+00:00'))
            if dt.timestamp() < _startup_ts:
                continue
        except Exception:
            continue
        if m.get('isInternal', False):
            continue
        body = (m.get('body', '') or '').strip()
        if not body:
            continue
        if _is_template_message(m):
            continue
        if is_bot_message(body):
            continue
        if _db_is_duplicate_body(body, window_seconds=3600):
            continue
        p(f"  [HUMAN-DBG] conv={conv_id[:12]} mid={mid[:12]} header={m.get('header')} att={bool(m.get('attendant'))} body={body[:60]}")
        return True
    return False


# ============================================================
# CAMADAS D1/D2 (2026-05-25): hard-stop apos atendente humano
# ============================================================
# D1: se a ULTIMA mensagem outgoing da conv foi enviada por um
# attendant humano nas ultimas N horas, o agente NAO deve enviar
# nada novo. Independe de _startup_ts, _human_took_over local ou
# handoff_active (cobre o caso da Debora, em que startup_ts cego
# ao historico permitiu bot responder apos despedida humana).
#
# D2: se nas mensagens existe "<Nome> finalizou o atendimento"
# vindo de um attendant humano nas ultimas N horas, conv estah
# encerrada por humano - agente nao envia follow-up nem close
# nem resposta nova.
_HUMAN_GUARD_WINDOW_S = 6 * 3600  # 6 horas


def _last_outgoing_is_human_attendant(conv_id, msgs=None, window_s=_HUMAN_GUARD_WINDOW_S):
    """Retorna (True, nome) se a ULTIMA msg outgoing tem attendant humano
    com timestamp recente. Caso contrario (False, '').

    (2026-05-26) BUG FIX: a versao anterior fazia `return False` ao
    encontrar a 1a msg received. Como a API DCZ retorna do mais recente
    pro mais antigo, isso fazia o guard FALHAR quando o aluno respondia
    DEPOIS do humano. Caso: Julia respondeu, aluno disse 'Estou bem,
    obrigada', bot processou e nao detectou Julia porque parou na 1a
    received. AGORA: msgs received sao IGNORADAS (continue) e o loop
    continua ate encontrar uma outgoing. So decide ali."""
    try:
        if msgs is None:
            # (2026-05-26) sempre fetch fresco aqui — o cache pode estar
            # desatualizado e nao refletir msgs recentes do humano.
            msgs = get_conversation_messages_api(conv_id, limit=20)
            if conv_id and msgs:
                _cached_msgs[conv_id] = msgs
        if not msgs:
            return False, ''
        from datetime import datetime as _dt
        now_ts = time.time()
        # msgs vem do mais recente pro mais antigo (padrao da API).
        # Procura a primeira OUTGOING (skip received).
        for m in msgs:
            if not isinstance(m, dict):
                continue
            if m.get('received', False):
                continue  # FIX: era return False — ignora msg do aluno e segue procurando outgoing
            if m.get('isInternal', False):
                continue
            body = (m.get('body') or '').strip()
            header = (m.get('header') or '').strip()
            if header and not body:
                continue
            att = m.get('attendant') or {}
            att_name = (att.get('name') if isinstance(att, dict) else '') or ''
            if not att_name:
                # outgoing sem attendant nomeado = bot. nao bloqueia.
                return False, ''
            ts_str = m.get('createdAt') or m.get('timestamp') or ''
            try:
                msg_ts = _dt.fromisoformat(str(ts_str).replace('Z', '+00:00')).timestamp()
            except Exception:
                continue
            if (now_ts - msg_ts) > window_s:
                return False, ''
            return True, att_name
        return False, ''
    except Exception:
        return False, ''


def _human_attendant_active_recently(conv_id, window_s=30 * 60):
    """(2026-05-26) Guard reforcado: True se QUALQUER outgoing humana
    aconteceu nos ultimos window_s segundos, independente da ordem com
    msgs do aluno. Mais robusto que _last_outgoing_is_human_attendant
    porque varre TODAS as msgs ao inves de parar na 1a outgoing.
    Usado no handle_message como hard-stop antes de qualquer
    processamento.
    """
    try:
        msgs = get_conversation_messages_api(conv_id, limit=20)
        if not msgs:
            return False, ''
        if conv_id:
            _cached_msgs[conv_id] = msgs
        from datetime import datetime as _dt
        now_ts = time.time()
        for m in msgs:
            if not isinstance(m, dict):
                continue
            if m.get('received', False):
                continue
            if m.get('isInternal', False):
                continue
            att = m.get('attendant') or {}
            att_name = (att.get('name') if isinstance(att, dict) else '') or ''
            if not att_name:
                continue  # bot — skip
            ts_str = m.get('createdAt') or m.get('timestamp') or ''
            try:
                msg_ts = _dt.fromisoformat(str(ts_str).replace('Z', '+00:00')).timestamp()
            except Exception:
                continue
            if (now_ts - msg_ts) <= window_s:
                return True, att_name
        return False, ''
    except Exception:
        return False, ''


def _retention_intercept_for_attendant_conv(cf):
    """(2026-06-25) Conversa que JÁ tem atendente (caiu em Atendimento via
    menu/distribuição n8n), mas a última mensagem do aluno é intenção de
    cancelamento/trancamento. Aciona SOMENTE a automação RET-IA (tag + nota +
    silêncio) — NÃO fala com o aluno e NÃO remove o atendente. Quem move para
    Retenção é a automação (via tag).

    Só age se TODAS as condições valerem (defesa em profundidade):
      - o atendente atribuído NÃO é do time de Retenção (Wesley/Danúbia);
      - há mensagem recebida não respondida (lastReceived > lastSended);
      - existe telefone do contato (evita resolver lead errado via _current_phone);
      - o atendente humano AINDA não falou na conversa (respeita quem já atua);
      - a última mensagem recebida é intenção de retenção (negation-aware);
      - dedup de 6h é garantido dentro de _trigger_retention_tag_only.
    Caso de origem: Estela — "Qro cancelar a matrícula" caiu na Camila (Atendimento).
    Retorna True se acionou a tag RET-IA.
    """
    try:
        cid = cf.get('id')
        if not cid:
            return False

        atts = cf.get('attendants', []) or []
        att_names = [(a.get('name') if isinstance(a, dict) else str(a)) or '' for a in atts]
        for an in att_names:
            al = an.strip().lower()
            if al and any(rt.lower() in al for rt in RETENTION_TEAM):
                return False  # já está com o time de Retenção — nada a fazer

        # precisa haver mensagem recebida MAIS NOVA que a última enviada (não respondida)
        _recv = cf.get('lastReceivedMessageDate', '') or ''
        _sent = cf.get('lastSendedMessageDate', '') or ''
        if not _recv or (_sent and _recv <= _sent):
            return False

        _ct = cf.get('contact', {}) or {}
        _ph = (_ct.get('phone') or cf.get('phone') or '').replace('+', '').strip()
        if not _ph:
            return False  # sem telefone confiável — não arrisca resolver lead errado

        # o atendente humano já falou? então respeita e NÃO age
        try:
            _h_active, _ = _human_attendant_active_recently(cid, window_s=6 * 3600)
            if _h_active:
                return False
        except Exception:
            pass

        # última mensagem recebida do aluno é intenção de retenção?
        msgs = _cached_msgs.get(cid) or get_conversation_messages_api(cid, limit=10) or []
        last_body = ''
        for m in msgs:
            if not isinstance(m, dict) or not _message_has_thread_payload(m):
                continue
            if m.get('received', False):
                last_body = (m.get('body') or '').strip()
                break
        if not last_body or not is_retention_intent(last_body):
            return False

        res = _trigger_retention_tag_only(cid, None, last_body, phone=_ph)
        if res:
            _att_disp = ', '.join([a for a in att_names if a]) or '?'
            p(f"  [RET-INTERCEPT] {cid[:12]} retenção em Atendimento (att={_att_disp}) — tag RET-IA acionada (tag-only, sem falar/remover)")
            return True
        return False
    except Exception as e:
        p(f"  [RET-INTERCEPT] erro: {e}")
        return False


def _human_closed_conversation(conv_id, msgs=None, window_s=_HUMAN_GUARD_WINDOW_S):
    """Retorna (True, nome) se na conv existe '<Nome> finalizou o atendimento'
    vindo de attendant humano nas ultimas window_s segundos. Cobre o caso da
    Debora: ela encerrou as 12:49 e o bot continuou enviando follow-up + close
    + resposta nova depois disso."""
    try:
        if msgs is None:
            msgs = _cached_msgs.get(conv_id) or get_conversation_messages_api(conv_id, limit=20)
            if conv_id:
                _cached_msgs[conv_id] = msgs
        if not msgs:
            return False, ''
        from datetime import datetime as _dt
        now_ts = time.time()
        for m in msgs:
            if not isinstance(m, dict):
                continue
            body = (m.get('body') or '').strip().lower()
            header = (m.get('header') or '').strip().lower()
            combined = (body + ' ' + header).strip()
            if not combined:
                continue
            # eh um marker de encerramento humano?
            if ('finalizou o atendimento' not in combined
                    and 'finalizou este atendimento' not in combined
                    and not ('atendente' in combined and 'removido' in combined)):
                continue
            # se o marker vier do header de sistema, tenta inferir nome do header
            # (formato: "Debora Mani Moreira finalizou o atendimento")
            sample = body if body else header
            import re
            m_name = re.search(r'^([A-ZÁÊÍÓÚÂÔÃÕ][\wÀ-ÿ]+(?:\s+[A-ZÁÊÍÓÚÂÔÃÕ][\wÀ-ÿ]+){0,3})\s+(?:finalizou|moveu)', sample)
            who = m_name.group(1) if m_name else ''
            # ignora se for nosso bot (sem nome proprio)
            if not who:
                # tenta attendant
                att = m.get('attendant') or {}
                who = (att.get('name') if isinstance(att, dict) else '') or ''
            if not who:
                continue
            # confere janela temporal
            ts_str = m.get('createdAt') or m.get('timestamp') or ''
            try:
                msg_ts = _dt.fromisoformat(str(ts_str).replace('Z', '+00:00')).timestamp()
            except Exception:
                continue
            if (now_ts - msg_ts) > window_s:
                continue
            return True, who
        return False, ''
    except Exception:
        return False, ''


# CAMADA D3 (2026-05-25): lista de temas fora do escopo da
# Cruzeiro do Sul EaD (academico). Caso Debora: aluno perguntou
# "exames veterinarios" e bot respondeu como se fosse especialista.
# Lista PROPOSITAL e RESTRITA — soh temas claramente fora (veterinaria,
# pets, receitas, esporte, politica). NAO entram aqui temas academicos
# legitimos mesmo que de cursos especificos (medicina, enfermagem,
# fisioterapia, etc), pois aluno pode estudar nesses cursos.
_OFF_SCOPE_KEYWORDS = (
    'exame veterin', 'exames veterin', 'veterin', 'meu cachorro', 'meu gato',
    'meu pet', 'meu cao', 'minha cadela', 'minha gata',
    'receita de', 'como cozinhar', 'como fazer bolo',
    'jogo do', 'futebol', 'palmeiras', 'corinthians', 'flamengo', 'sao paulo fc',
    'politica', 'eleicao', 'presidente lula', 'presidente bolsonaro',
    'horoscopo', 'signo de', 'tarot',
)


def _is_off_scope_message(text):
    """Detecta se a mensagem do aluno eh sobre tema claramente fora do
    escopo academico. Retorna (True, palavra_chave) ou (False, '')."""
    if not text:
        return False, ''
    t = str(text).lower()
    import unicodedata
    t = ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
    for kw in _OFF_SCOPE_KEYWORDS:
        if kw in t:
            return True, kw
    return False, ''


# CAMADA D5 (2026-05-25): lock anti-burst por conv_id.
# Caso reportado (Debora): 2 follow-ups identicos saidos em 5s
# por race entre main_loop e supervisor_loop. Um lock simples
# bloqueia envios concorrentes na mesma conv por _SEND_BURST_S.
_SEND_BURST_S = 20
_send_burst_last = {}


# ============================================================
# CAMADA D6 (2026-05-25): detector de LOOP DE FRUSTRACAO
# ============================================================
# Caso Sandra/Ivanice: aluno enviou 3-5 mensagens com expressoes
# "nao consegui", "nao aparece", "nao funciona" e o bot continuou
# tentando responder com parafrase. Bot deve PARAR e ESCALAR
# humano apos 2 sinais de frustracao em 10 min.
_FRUSTRATION_PATTERNS = (
    'nao consegui', 'nao aparece', 'nao funciona', 'nao vai',
    'continua igual', 'continua a mesma', 'continua dando',
    'mesmo problema', 'sem sucesso', 'sem solucao',
    'nao deu certo', 'nao consigo', 'tambem nao', 'tampouco',
    'simplesmente nao', 'so que nao', 'mas nao', 'porem nao',
    'ja tentei', 'ja fiz isso', 'ja fiz tudo', 'fiz tudo',
    'nao adiantou', 'sem resposta',
)
_FRUSTRATION_WINDOW_S = 10 * 60  # 10 min


def _count_frustration_signals(conv_msgs, window_s=_FRUSTRATION_WINDOW_S):
    """Conta mensagens do aluno na janela que tem expressao de frustracao."""
    if not conv_msgs:
        return 0
    try:
        from datetime import datetime as _dt
        import unicodedata
        now_ts = time.time()
        count = 0
        for m in conv_msgs:
            if not isinstance(m, dict):
                continue
            if not m.get('received', False):
                continue
            if m.get('isInternal', False):
                continue
            body = (m.get('body') or '').strip()
            if len(body) < 3:
                continue
            ts_str = m.get('createdAt') or m.get('timestamp') or ''
            try:
                msg_ts = _dt.fromisoformat(str(ts_str).replace('Z', '+00:00')).timestamp()
            except Exception:
                continue
            if (now_ts - msg_ts) > window_s:
                continue
            # normaliza sem acentos
            t = ''.join(c for c in unicodedata.normalize('NFD', body.lower())
                        if unicodedata.category(c) != 'Mn')
            if any(p in t for p in _FRUSTRATION_PATTERNS):
                count += 1
        return count
    except Exception:
        return 0


def _send_burst_recent(conv_id):
    if not conv_id:
        return False
    last = _send_burst_last.get(conv_id, 0)
    return (time.time() - last) < _SEND_BURST_S


def _mark_send_burst(conv_id):
    if not conv_id:
        return
    _send_burst_last[conv_id] = time.time()


def get_new_client_message(conv_id, force=False):
    """Retorna (msg_id, body, is_button_click, image_info).
    Processa a mensagem mais recente do aluno que ainda não foi respondida.
    force=True bypassa o filtro de startup (para conversas WAITING)."""
    msgs = _cached_msgs.get(conv_id) or get_conversation_messages_api(conv_id, limit=10)
    _cached_msgs[conv_id] = msgs

    has_outgoing_response = False
    last_incoming_ts = ''
    last_outgoing_ts = ''
    for m in msgs:
        if not _message_has_thread_payload(m):
            continue
        ts = m.get('createdAt', '') or m.get('timestamp', '') or ''
        if m.get('received', False):
            if not last_incoming_ts:
                last_incoming_ts = ts
        else:
            if not last_outgoing_ts:
                last_outgoing_ts = ts
    if last_outgoing_ts and last_incoming_ts and last_outgoing_ts >= last_incoming_ts:
        has_outgoing_response = True

    for m in msgs:
        mid = m.get('id', '')
        if mid in processed_msg_ids:
            continue

        msg_ts = m.get('createdAt', '') or m.get('timestamp', '') or ''
        if msg_ts and has_outgoing_response and not force:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(msg_ts).replace('Z', '+00:00'))
                if dt.timestamp() < _startup_ts:
                    processed_msg_ids.add(mid)
                    continue
            except Exception:
                pass

        received = m.get('received', False)
        if not received:
            processed_msg_ids.add(mid)
            has_outgoing_response = True
            continue

        # (2026-06-01) GUARD ANTI-MENSAGEM-ANTIGA — bug reportado (Marcia):
        # a API ignora o `limit` e devolve TODAS as msgs (newest-first). Quando
        # as msgs novas ("Oi"/"Ola") ja estavam em processed_msg_ids, o loop
        # descia ate uma msg de uma SESSAO JA ENCERRADA dias atras ("Agradeço!"
        # de 1 semana antes) e a tratava como atual -> despedida -> fechava a
        # conversa. Regra geral: so processa msg do aluno MAIS RECENTE que a
        # ultima resposta (bot/humano). Tudo antes ja foi respondido.
        if last_outgoing_ts and msg_ts:
            try:
                from datetime import datetime as _dt_g
                _ts_msg = _dt_g.fromisoformat(str(msg_ts).replace('Z', '+00:00'))
                _ts_out = _dt_g.fromisoformat(str(last_outgoing_ts).replace('Z', '+00:00'))
                if _ts_msg <= _ts_out:
                    processed_msg_ids.add(mid)
                    continue
            except Exception:
                # fallback comparacao lexicografica (ISO8601 ordena igual)
                if str(msg_ts) <= str(last_outgoing_ts):
                    processed_msg_ids.add(mid)
                    continue

        image_info = extract_image_from_message(m)
        img_caption = image_info.get('caption', '') if image_info else ''

        # Detectar áudio
        _is_audio = False
        _attachments = m.get('attachments', [])
        if isinstance(_attachments, list):
            for _att in _attachments:
                if isinstance(_att, dict):
                    _att_type = (_att.get('type', '') or _att.get('mimeType', '')).lower()
                    if 'audio' in _att_type or 'ogg' in _att_type or 'voice' in _att_type:
                        _is_audio = True
                        break
        if not _is_audio:
            _src = m.get('sourceData', m.get('meta', m.get('payload', {})))
            if isinstance(_src, dict):
                _src_type = (_src.get('type', '') or '').lower()
                if _src_type in ('audio', 'ptt', 'voice'):
                    _is_audio = True
                if _src.get('audio') or _src.get('ptt'):
                    _is_audio = True

        body = (m.get('body', '') or '').strip()
        is_button_click = False
        if not body:
            body = (m.get('text', '') or '').strip()
        if not body:
            body = (m.get('title', '') or '').strip()
        if not body:
            meta = m.get('meta', m.get('payload', m.get('sourceData', {})))
            if isinstance(meta, dict):
                inter = meta.get('interactive', meta)
                if isinstance(inter, dict):
                    for rtype in ('button_reply', 'list_reply'):
                        rep = inter.get(rtype, {})
                        if isinstance(rep, dict) and rep.get('title'):
                            body = rep['title'].strip()
                            is_button_click = True
                            break

        if not body and img_caption:
            body = img_caption

        if not body and image_info:
            body = '[imagem enviada pelo aluno]'

        if not body and _is_audio:
            body = '[audio]'

        if not body:
            p(f"  SKIP vazio: mid={mid[:20]} keys={list(m.keys())[:8]}")
            processed_msg_ids.add(mid)
            continue
        if is_bot_message(body):
            p(f"  SKIP bot: \"{body[:60]}\"")
            processed_msg_ids.add(mid)
            continue
        if _is_echo_of_sent(body):
            p(f"  SKIP echo: \"{body[:60]}\"")
            processed_msg_ids.add(mid)
            continue
        if not _db_claim_message(mid, body):
            processed_msg_ids.add(mid)
            continue
        return mid, body, is_button_click, image_info
    return None, None, False, None


def build_conversation_history(conv_id):
    msgs = _cached_msgs.get(conv_id)
    if not msgs:
        msgs = get_conversation_messages_api(conv_id, limit=10)
    history = []
    for m in reversed(msgs):
        sender = "Aluno" if m.get('received', False) else "Assistente"
        body = m.get('body', '')[:200]
        if body:
            history.append(f"{sender}: {body}")
    return '\n'.join(history[-6:])


def is_escalation_trigger(question):
    q = question.lower().strip()
    cpf_pattern = re.compile(r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b')
    if cpf_pattern.search(q):
        return True, "CPF detectado"
    if re.match(r'^\d{9,14}$', q.replace('.', '').replace('-', '')):
        return True, "Numero longo (CPF/RGM)"
    return False, ""


# ===================== DEBUG COMMANDS =====================

def _simulate_redistribution(conv_id):
    """Simulação ao vivo: gera resumo da conversa, busca atendente, avisa aluno."""
    from supabase_client import get_best_available_agent
    from redistribution_engine import generate_handoff_summary, format_internal_note

    agent_name = 'Marcelo Pinheiro'
    student_name = student_profile.get('name', 'Aluno') if student_profile else 'Aluno'

    p(f"  [REDIST] Atendente simulado: {agent_name}")
    p(f"  [REDIST] Aluno: {student_name}")
    p(f"  [REDIST] Msgs na conversa: {len(conversation_messages)}")

    msgs_for_summary = [
        {'direction': 'received' if m['role'] == 'user' else 'sent', 'body': m['text']}
        for m in conversation_messages[-15:]
    ]

    p(f"  [REDIST] Gerando resumo com GPT...")
    summary = generate_handoff_summary(msgs_for_summary)
    p(f"  [REDIST] Resumo: {summary.get('tema')} | {summary.get('contexto', '')[:60]}")

    p(f"  [REDIST] Buscando atendente disponível...")
    target = get_best_available_agent(exclude_names=[agent_name, 'Marcelo'])

    if target:
        target_name = target.get('responsavel', '')
        p(f"  [REDIST] ✅ Encontrado: {target_name} (fila={target.get('fila',0)})")

        client_msg = (
            f"Olá, {student_name.split()[0]}! "
            f"O atendente *{agent_name}* precisou encerrar o expediente, "
            f"mas não se preocupe — *{target_name}* vai continuar te atendendo. 😊\n\n"
            f"Já repassamos todo o contexto da sua conversa para que você não precise repetir nada!"
        )
    else:
        target_name = None
        p(f"  [REDIST] ⚠️ Nenhum atendente disponível — IA assume")

        client_msg = (
            f"Olá, {student_name.split()[0]}! "
            f"Nosso atendente *{agent_name}* encerrou o expediente, "
            f"mas estou aqui para continuar te ajudando! 🤖\n\n"
            f"Me conta como posso te ajudar."
        )

    send_and_track(conv_id, client_msg)
    time.sleep(1)

    dest = target_name or 'IA Bot'
    internal_note = format_internal_note(summary, agent_name, dest)
    p(f"  [REDIST] Postando nota interna no CRM...")
    send_message_crm(conv_id, internal_note)

    if student_profile and student_profile.get('lead_id'):
        try:
            requests.patch(
                f'{DCZ_CRM}/leads/{student_profile["lead_id"]}',
                headers=H, json={'notes': internal_note}, timeout=10
            )
            p(f"  [REDIST] Nota adicionada ao lead")
        except Exception as e:
            p(f"  [REDIST] Erro CRM note: {e}")

    result_msg = (
        f"✅ *Simulação concluída!*\n\n"
        f"📋 *Resumo gerado pelo GPT:*\n"
        f"• Tema: {summary.get('tema', 'N/A')}\n"
        f"• Contexto: {summary.get('contexto', 'N/A')}\n"
        f"• Necessidade: {summary.get('necessidade', 'N/A')}\n"
        f"• Próximo passo: {summary.get('proximo_passo', 'N/A')}\n\n"
        f"👤 Transferido para: *{dest}*\n"
        f"📝 Nota interna postada no CRM"
    )
    time.sleep(1)
    send_and_track(conv_id, result_msg)
    p(f"  [REDIST] ✅ Simulação completa!")


def _switch_phone(new_phone):
    """Troca o telefone monitorado, fazendo reset completo de estado."""
    global PHONE_TO_MONITOR, active_conv_id, student_profile, conversation_messages
    global last_response_time, processed_msg_ids, conversation_greeted

    old = PHONE_TO_MONITOR
    PHONE_TO_MONITOR = new_phone
    active_conv_id = None
    student_profile = None
    conversation_messages = []
    last_response_time = 0
    processed_msg_ids = set()
    conversation_greeted = set()

    p(f"  [SWITCH] {old} -> {new_phone}")


def handle_debug_command(conv_id, cmd):
    """Comandos especiais: #testar, #sair, #reset, #status, #menu, #help"""
    global conversation_messages, student_profile, active_conv_id

    if cmd in ('#testar', '#test', '#t'):
        send_and_track(conv_id, "✅ *Modo teste ativado!*\n\nAgora estou monitorando esta conversa.\nMande *oi* para começar ou *#help* para ver comandos.\n\nPara sair: *#sair*")
        p(f"  [TEST] Teste ativado na conv {conv_id[:16]}")
        return

    if cmd in ('#sair', '#exit', '#q'):
        if PHONE_TO_MONITOR != PHONE_TO_MONITOR_DEFAULT:
            _switch_phone(PHONE_TO_MONITOR_DEFAULT)
            send_and_track(conv_id, "👋 *Modo teste desativado!*\n\nVoltando ao monitor principal.")
            p(f"  [TEST] Voltando ao phone padrão")
        else:
            send_and_track(conv_id, "ℹ️ Já está no monitor principal.")
        return

    if cmd in ('#reset', '#r'):
        conversation_greeted.discard(conv_id)
        conversation_messages = []
        student_profile = None
        p("  [DEBUG] >>> RESET completo <<<")
        send_and_track(conv_id, "🔄 *Reset!* Estado limpo. Mande 'oi' para recomeçar.")
        return

    if cmd in ('#status', '#s'):
        mem = load_memory(PHONE_TO_MONITOR)
        lines = [
            "📊 *Status do Agente*",
            f"• Phone: ...{PHONE_TO_MONITOR[-4:]}",
            f"• Default: ...{PHONE_TO_MONITOR_DEFAULT[-4:]}",
            f"• Aluno: {student_profile.get('name', '?') if student_profile else 'N/A'}",
            f"• Conv ID: {conv_id[:16]}...",
            f"• Msgs processadas: {len(processed_msg_ids)}",
            f"• Msgs na conversa: {len(conversation_messages)}",
            f"• Greeted: {conv_id in conversation_greeted}",
        ]
        if mem:
            lines.append(f"• Interações memória: {mem['interaction_count']}")
            lines.append(f"• Último tema: {mem.get('last_topic', 'N/A')}")
        send_and_track(conv_id, '\n'.join(lines))
        p(f"  [DEBUG] Status enviado")
        return

    if cmd in ('#menu', '#m'):
        send_and_track(conv_id, "Selecione uma opção:", buttons=MAIN_MENU_BUTTONS)
        p(f"  [DEBUG] Menu forçado")
        return

    if cmd in ('#buttons', '#b'):
        send_and_track(conv_id, "Teste 3 botões (reply):", buttons=['Botão A', 'Botão B', 'Botão C'])
        p(f"  [DEBUG] Teste 3 botões")
        return

    if cmd in ('#redistribuir', '#rd'):
        p(f"  [DEBUG] >>> SIMULAÇÃO DE REDISTRIBUIÇÃO <<<")
        send_and_track(conv_id, "⏳ Simulando redistribuição... aguarde.")
        _simulate_redistribution(conv_id)
        return

    if cmd in ('#help', '#h', '#?'):
        msg = (
            "🛠️ *Comandos de Debug*\n\n"
            "• *#testar* — Ativa o agente nesta conversa\n"
            "• *#sair* — Volta ao monitor padrão\n"
            "• *#reset* — Limpa estado, recomeça do zero\n"
            "• *#status* — Mostra estado do agente\n"
            "• *#menu* — Força exibir o menu principal\n"
            "• *#buttons* — Testa envio de 3 botões\n"
            "• *#redistribuir* — Simula redistribuição\n"
            "• *#help* — Este menu"
        )
        send_and_track(conv_id, msg)
        p(f"  [DEBUG] Help enviado")
        return

    send_and_track(conv_id, f"Comando desconhecido: {cmd}\nDigite *#help* para ver comandos.")
    p(f"  [DEBUG] Comando desconhecido: {cmd}")


def _handle_cpf_input(conv_id, question, name_suffix):
    """Processa o CPF digitado pelo aluno no fluxo 'Já sou aluno'."""
    global _awaiting_cpf, _student_in_base, _awaiting_polo_confirm, student_profile, waiting_for_client, inactivity_start

    cpf_raw = question.strip().replace('.', '').replace('-', '').replace(' ', '')
    if not cpf_raw.isdigit() or len(cpf_raw) < 10:
        msg = "Não consegui identificar um CPF válido. Por favor, *digite apenas os números* do seu CPF.\n\n*Exemplo*: 12345678910"
        meta_typing_on()
        send_and_track(conv_id, msg)
        log_to_db(conv_id, question, msg, 0.0, 'cpf_invalid')
        waiting_for_client = True; inactivity_start = time.time()
        return

    msg_wait = "Certo. Por favor *aguarde* enquanto localizo as informações em nossa base de dados. ⌛"
    meta_typing_on()
    send_and_track(conv_id, msg_wait)

    lead_id = student_profile.get('lead_id', '') if student_profile else ''
    lead_name = student_profile.get('name', '') if student_profile else ''

    if not lead_id:
        p(f"  Lead não existe, criando...")
        cur_phone = _current_phone or PHONE_TO_MONITOR
        new_lead_id, new_biz_id = create_lead_and_business(cur_phone, name=lead_name)
        if new_lead_id:
            lead_id = new_lead_id
            if student_profile:
                student_profile['lead_id'] = new_lead_id

    biz_id = ''
    cur_phone = _current_phone or PHONE_TO_MONITOR
    try:
        r_biz = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                            params={'search': cur_phone.replace('+','').replace(' ','').replace('-',''), 'limit': 5}, timeout=10)
        if r_biz.status_code == 200:
            biz_data = r_biz.json()
            biz_list = biz_data.get('data', biz_data) if isinstance(biz_data, dict) else biz_data
            if isinstance(biz_list, list) and biz_list:
                biz_id = biz_list[0].get('id', '')
    except Exception:
        pass

    validate_student_cpf_webhook(cpf_raw, cur_phone, lead_id, biz_id, lead_name)

    p(f"  Aguardando resultado do webhook (polling campo Lead Existe?)...")
    lead_exists = None
    for attempt in range(6):
        time.sleep(5)
        lead_exists = check_lead_exists_field(lead_id)
        if lead_exists is not None:
            break
        p(f"    Polling {attempt+1}/6... campo ainda não setado")

    _awaiting_cpf = False

    if lead_exists is True:
        _student_in_base = True
        p(f"  ALUNO VALIDADO pelo CPF -> saudação + menu")
        student_profile = identify_student(_current_phone or PHONE_TO_MONITOR)

        _acad_cpf = fetch_academic_data(cpf_raw, phone=_current_phone or PHONE_TO_MONITOR)
        if _acad_cpf:
            if student_profile:
                student_profile['academic'] = _acad_cpf
                student_profile['_acad_loaded'] = True
                try:
                    _caa = fetch_caa_solicitacoes(cpf_raw)
                    if _caa:
                        student_profile['caa_solicitacoes'] = _caa
                except Exception as _e_caa:
                    p(f"    [CAA] Erro: {_e_caa}")
            _polo_aluno = (_acad_cpf.get('polo') or '').strip()
            if _polo_aluno and not _is_nosso_polo(_polo_aluno):
                p(f"  [POLO-CPF] Polo '{_polo_aluno}' NÃO é nosso -> redirecionando")
                _handle_outro_polo(conv_id, _current_phone or cur_phone, student_profile, _polo_aluno)
                waiting_for_client = False; inactivity_start = 0
                return

        fname = student_profile.get('first_name', '') if student_profile else ''
        if fname:
            greeting = f"*Em breve um de nossos consultores irá te chamar!*\n\nMe conta, sobre o que você deseja falar?\nPergunte de maneira simples que eu entendo melhor assim. 😊"
        else:
            greeting = f"*Em breve um de nossos consultores irá te chamar!*\n\nMe conta, sobre o que você deseja falar?\nPergunte de maneira simples que eu entendo melhor assim. 😊"
        meta_typing_on()
        send_and_track(conv_id, greeting, buttons=GREETING_BUTTONS)
        conversation_messages.append({'role': 'bot', 'text': greeting})
        log_to_db(conv_id, question, greeting, 1.0, 'cpf_validated')
        waiting_for_client = True; inactivity_start = time.time()
    else:
        _student_in_base = False
        _awaiting_polo_confirm = True
        p(f"  Aluno NÃO encontrado na base acadêmica pelo CPF")
        msg_nf = (f"Não encontramos você em nossa *base de alunos*.\n\n"
                  f"Prestamos suporte para as unidades (polos) 👇")
        meta_typing_on()
        send_and_track(conv_id, msg_nf)
        time.sleep(1)
        send_and_track(conv_id, POLOS_LIST)
        time.sleep(1)
        send_and_track(conv_id, "Você é matriculado em algum dos polos *acima?*", buttons=['Sim', 'Não'])
        conversation_messages.append({'role': 'bot', 'text': msg_nf})
        log_to_db(conv_id, question, msg_nf, 0.0, 'cpf_not_found')
        waiting_for_client = True; inactivity_start = time.time()


# ===================== HANDLER =====================

def handle_message(conv_id, msg_id, msg_body, is_button_click=False, image_info=None):
    global active_conv_id, student_profile, conversation_messages, last_response_time
    global followup_stage, waiting_for_client, inactivity_start, _last_auto_skipped
    global _awaiting_cpf, _student_in_base, _awaiting_polo_confirm
    processed_msg_ids.add(msg_id)
    followup_stage = 0
    waiting_for_client = False
    inactivity_start = 0
    _last_auto_skipped = False
    question = msg_body

    image_b64 = None
    image_mime = None
    image_desc = None
    if image_info:
        p(f"  Vision: baixando imagem (url={bool(image_info.get('url'))}, media_id={str(image_info.get('media_id',''))[:20]})...")
        image_b64, image_mime = download_whatsapp_image(image_info)
        if image_b64:
            p(f"  Vision: imagem pronta, gerando descricao...")
            try:
                client = OpenAI(api_key=OPENAI_API_KEY)
                desc_chat = client.chat.completions.create(
                    model='gpt-4o-mini',
                    messages=[
                        {'role': 'system', 'content': (
                            'Você é um assistente de suporte acadêmico da Cruzeiro do Sul. O aluno enviou uma imagem (print de tela ou screenshot). '
                            'Analise detalhadamente e descreva em 3-5 frases O QUE a imagem mostra. Foque em: '
                            '1) Qual plataforma/app (DUDA, Portal do Aluno, Blackboard, etc). '
                            '2) Se há mensagens de erro, transcreva o código e texto EXATO do erro (ex: AADSTS90072). '
                            '3) Se há emails visíveis, transcreva-os e diga se é email pessoal (gmail, hotmail, live.com, outlook) ou acadêmico (@aluno.cruzeirodosul, @cs.unicid). '
                            '4) Identifique o problema: ex. "user account does not exist in tenant", "identity provider does not exist". '
                            '5) Se for erro de login com email pessoal, mencione explicitamente que o aluno está usando email pessoal em vez do acadêmico. '
                            'Responda APENAS com a descrição técnica, sem saudação.'
                        )},
                        {'role': 'user', 'content': [
                            {"type": "text", "text": question if question != '[imagem enviada pelo aluno]' else "O que esta imagem mostra?"},
                            {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_b64}", "detail": "high"}}
                        ]}
                    ],
                    max_tokens=250, temperature=0.2
                )
                image_desc = desc_chat.choices[0].message.content.strip()
                p(f"  Vision desc: {image_desc[:200]}")
                if question == '[imagem enviada pelo aluno]':
                    question = image_desc
            except Exception as e:
                p(f"  Vision desc erro: {e}")
        else:
            p(f"  Vision: falha no download, processando apenas texto")

        # Fallback media-only: imagem sem caption E Vision nao conseguiu descrever
        if question == '[imagem enviada pelo aluno]' and not (image_desc and image_desc.strip()):
            p(f"  [MEDIA-ONLY] imagem sem caption + Vision falhou -> resposta padrao")
            send_media_only_response(conv_id, media_type='imagem', question_label='[imagem sem texto]')
            conversation_messages.append({'role': 'user', 'text': '[Imagem enviada]'})
            return

    p(f"")
    p(f"{'='*55}")
    p(f"  NOVA MSG: \"{question[:120]}\"")
    p(f"  Tipo: {'BOTAO' if is_button_click else 'TEXTO'}")
    p(f"  MsgID: {msg_id[:30]}")
    p(f"{'='*55}")

    if active_conv_id is None:
        active_conv_id = conv_id
    elif active_conv_id != conv_id:
        p(f"  Conv mudou: {active_conv_id[:12]} -> {conv_id[:12]}")
        active_conv_id = conv_id

    cmd = question.strip().lower()
    if cmd.startswith('#'):
        handle_debug_command(conv_id, cmd)
        return

    # === HARD-STOP: ATENDENTE HUMANO ATIVO ===
    # (2026-05-26) Caso reportado: Julia (humana) respondeu "Bom dia! Me
    # chamo Julia..." na conv, aluno disse "Estou bem, obrigada", e bot
    # ainda enviou "Vou te transferir para Julia". Bug: o guard antigo
    # parava na 1a msg do aluno e nao via Julia anterior. Agora usamos
    # _human_attendant_active_recently que varre TODO o historico recente
    # (30min) procurando QUALQUER outgoing humana. Se achar, bot recua.
    try:
        _h_active, _h_name = _human_attendant_active_recently(conv_id, window_s=30 * 60)
        if _h_active:
            p(f"  [HUMAN-HARD-STOP] {conv_id[:12]} {_h_name} ja respondeu na ultima 30min — agente recua sem enviar")
            try:
                _conv_states.setdefault(conv_id, _default_conv_state())['_human_took_over'] = True
                _save_conv_state(conv_id)
            except Exception:
                pass
            conversation_messages.append({'role': 'user', 'text': question})
            return
    except Exception as e_hh:
        p(f"  [HUMAN-HARD-STOP] erro check: {e_hh}")

    # === EARLY CLOSE: DESPEDIDA / CONFIRMACAO DE RESOLUCAO ===
    # (2026-05-26) Caso reportado em FILA: alunos com despedidas claras
    # ("Nao, obrigado.", "Vou avaliar Muito grato", "Consegui entender as
    # explicacoes", "Para voce tambem 😊") ficavam aguardando atendimento
    # porque o check de despedida rodava SOMENTE dentro do LOW-CONF-D4 do
    # handle_message — ou seja, depois da busca KB+LLM. Quando a IA tinha
    # confianca alta, escapava do check e respondia "vou te ajudar...",
    # mantendo a conv aberta.
    #
    # AGORA: detector roda LOGO no inicio, ANTES de qualquer processamento
    # pesado. Se for despedida pura OU confirmacao de resolucao, agradece
    # curto e fecha. Regra geral: se aluno mandou farewell/resolucao apos
    # ja ter recebido respostas anteriores na conv (humano ou bot), fechar.
    try:
        _is_farewell = _is_farewell_message(question)
        _is_resolution = _is_resolution_confirmation(question)
        # (2026-05-27) GUARD DEFINITIVO: sauda\u00e7\u00e3o pura NUNCA encerra.
        # Caso reportado mais de uma vez: aluno mandava 'boa tarde' e conv
        # era encerrada com 'Obrigado pelo contato...'.
        if _is_pure_greeting(question):
            _is_farewell = False
            _is_resolution = False
        if (_is_farewell or _is_resolution) and not is_button_click:
            # Confirma que a conv NAO é uma abertura nova — precisa ter
            # historico previo (>=2 msgs no cache) para nao fechar conv que
            # comeca com "ok" como primeira mensagem do aluno.
            _hist = _cached_msgs.get(conv_id, []) or []
            _has_prior_interaction = len(_hist) >= 2
            # Tenta fetch fresco se cache vazio
            if not _has_prior_interaction:
                try:
                    _fetched = get_conversation_messages_api(conv_id, limit=10) or []
                    _has_prior_interaction = len(_fetched) >= 2
                    if _fetched:
                        _cached_msgs[conv_id] = _fetched
                except Exception:
                    pass

            if _has_prior_interaction:
                _kind = 'despedida' if _is_farewell else 'confirmacao_resolucao'
                p(f"  [EARLY-CLOSE] {conv_id[:12]} msg do aluno = {_kind} ('{question[:50]}') — agradecendo e fechando")

                _fname_ec = ''
                try:
                    if student_profile and student_profile.get('first_name'):
                        _fname_ec = student_profile['first_name']
                    elif _current_phone:
                        _sp_ec = identify_student(_current_phone)
                        if _sp_ec and _sp_ec.get('first_name'):
                            _fname_ec = _sp_ec['first_name']
                except Exception:
                    pass
                _name_suffix_ec = f' *{_fname_ec}*' if _fname_ec else ''

                if _is_resolution:
                    _ec_msg = (
                        f"Que ótimo{_name_suffix_ec}! Fico feliz que tenha conseguido resolver 😊\n\n"
                        f"Se surgir qualquer outra dúvida, é só chamar. Até mais!"
                    )
                else:
                    _ec_msg = random.choice([
                        f"De nada{_name_suffix_ec}! Fico à disposição pro que precisar. Até mais! 😊",
                        f"Imagina{_name_suffix_ec}! Qualquer coisa, é só me chamar de novo. Até logo! 🙏",
                        f"Obrigado pelo contato{_name_suffix_ec}! Tenha um ótimo dia 😊",
                    ])
                try:
                    send_and_track(conv_id, _ec_msg, force=True)
                    conversation_messages.append({'role': 'bot', 'text': _ec_msg})
                except Exception as e_ec_send:
                    p(f"  [EARLY-CLOSE] erro send: {e_ec_send}")
                try:
                    log_to_db(conv_id, question, _ec_msg, 1.0, _kind)
                except Exception:
                    pass
                try:
                    close_conversation_crm(conv_id, phone=_current_phone)
                except Exception as e_ec_close:
                    p(f"  [EARLY-CLOSE] erro close: {e_ec_close}")
                try:
                    update_pending_escalation_status(
                        conv_id, 'resolved',
                        note=f'EARLY-CLOSE — aluno enviou {_kind}: "{question[:80]}"',
                    )
                except Exception:
                    pass
                waiting_for_client = False; followup_stage = 0; inactivity_start = 0
                return
    except Exception as e_early:
        p(f"  [EARLY-CLOSE] erro detector: {e_early}")

    # === MENSAGEM DE BOT EXTERNO (URA, autoresponder de parceiro, bot DCZ) ===
    # (2026-05-25) Quando o "aluno" envia uma frase claramente de bot
    # externo ("X agradece seu contato. Como podemos ajudar?"), o agente
    # NUNCA deve responder com menu/LLM. Apenas fecha a conv silenciosamente.
    # Caso: Claudenice — "claupiercings agradece seu contato..."
    # IMPORTANTE: templates HSM (disparos do nosso Cockpit) sao EXCLUIDOS
    # do detector — eles tem header/type=template e sao msgs SAIDA (sent),
    # nao input do aluno. Mas guarda extra aqui via _msg_is_template_hsm
    # para evitar falsos positivos caso DCZ marque algum disparo como
    # received.
    try:
        if _is_external_bot_input(question) and not _msg_is_template_hsm(conv_id, msg_id):
            p(f"  [EXTERNAL-BOT] msg identificada como bot/URA externa — fechando sem responder: \"{question[:80]}\"")
            log_to_db(conv_id, question, '[fechada — msg de bot externo]', 1.0, 'external_bot_close')
            try:
                close_conversation_crm(conv_id, phone=_current_phone)
            except Exception as e_eb:
                p(f"  [EXTERNAL-BOT] erro close: {e_eb}")
            try:
                update_pending_escalation_status(
                    conv_id, 'closed_external_bot',
                    note=f'Bot/URA externa: "{question[:80]}"',
                )
            except Exception:
                pass
            conversation_messages.clear()
            conversation_greeted.discard(conv_id)
            waiting_for_client = False; followup_stage = 0; inactivity_start = 0
            return
    except Exception as e_eb_outer:
        p(f"  [EXTERNAL-BOT] erro detector: {e_eb_outer}")

    # === RESPOSTA AO OFERECIMENTO PRE-ABERTURA ===
    # Se ha _pre_opening_pending no estado, interpreta resposta como sim/nao.
    st_pre = _conv_states.get(conv_id, {}) or {}
    if st_pre.get('_pre_opening_pending'):
        button_id = ''
        if is_button_click:
            button_id = (msg_body or '').strip()
        intent = detect_pre_opening_intent(question, button_id=button_id)
        if intent == 'yes':
            accept_pre_opening(conv_id, question=question)
            conversation_messages.append({'role': 'user', 'text': question})
            return
        elif intent == 'no':
            decline_pre_opening(conv_id, question=question)
            conversation_messages.append({'role': 'user', 'text': question})
            return
        # Sem intent claro: trata como continuacao da conversa (segue fluxo normal)
        # mas limpa flag para nao re-perguntar
        st_pre['_pre_opening_pending'] = False

    # === HANDOFF ATIVO: agente nao responde mais ate humano assumir / TTL expirar ===
    # Evita sequencias repetitivas tipo:
    #   1) Mensagem humanizada de retencao (Wesley vai retomar 9h)
    #   2) Eu entendo que ta complicado... (LLM continua)
    #   3) Ainda esta por ai? (follow-up)
    #   4) Vou encerrar... (close)
    # Quando ha handoff vigente, o agente principal CALA. Supervisor cuida do close
    # por inatividade.
    ho_motivo, ho_target = _is_handoff_active(conv_id)
    if ho_motivo:
        # supervisor_block: silencio absoluto (sem nudge). So humano libera.
        if ho_motivo == 'supervisor_block':
            p(f"  [SUPERVISOR-BLOCK] {conv_id[:12]} agente silenciado pelo auditor OpenAI - SEM resposta")
            conversation_messages.append({'role': 'user', 'text': question})
            return

        # (2026-05-25) PROMESSA-NAO-CUMPRIDA: handoff marca alguem ('Felipe')
        # mas a conv NAO foi transferida no DCZ. Sem isso, o agente envia
        # "Felipe vai dar continuidade" mas Felipe nunca recebe — aluno
        # fica esperando para sempre. Aqui validamos a transferencia ANTES
        # do nudge. Se falhar, limpamos handoff stale e caimos pro fluxo
        # normal (distribute_to_attendant via outras vias).
        try:
            _conv_has_att = False
            try:
                r_conv = requests.get(
                    f'{DCZ_MSG}/messaging/conversations/{conv_id}',
                    headers=H, timeout=10,
                )
                if r_conv.status_code == 200:
                    _conv_has_att = bool((r_conv.json() or {}).get('attendants') or [])
            except Exception:
                pass

            if not _conv_has_att and ho_target and ho_motivo in ('dispatch', 'preferred', 'retention'):
                if is_attendant_active_now(ho_target):
                    p(f"  [HANDOFF-ACTIVE] {conv_id[:12]} promessa-nao-cumprida detectada — forcando transferencia real p/ {ho_target}")
                    try:
                        _phone_fix = _current_phone or PHONE_TO_MONITOR
                        _name_fix = ''
                        try:
                            _st_n = _conv_states.get(conv_id, {})
                            if _st_n.get('student_profile') and _st_n['student_profile'].get('name'):
                                _name_fix = _st_n['student_profile']['name']
                        except Exception:
                            pass
                        _lead_fix, _biz_fix, _created_fix = _ensure_lead_for_rescue(_phone_fix, _name_fix)
                        if _lead_fix:
                            _dcz_transfer_business(_phone_fix, ho_target, lead_id=_lead_fix)
                            _dcz_transfer_lead(_lead_fix, ho_target)
                        _transf_ok = _dcz_transfer_chat(conv_id, ho_target)
                        if not _transf_ok:
                            _nome_norm_fix = ho_target.strip().lower()
                            _nome_norm_fix = ''.join(
                                c for c in __import__('unicodedata').normalize('NFD', _nome_norm_fix)
                                if __import__('unicodedata').category(c) != 'Mn'
                            )
                            _att_id_fix = ATTENDANT_MAP.get(_nome_norm_fix, '')
                            if _att_id_fix:
                                try:
                                    requests.post(
                                        f'{DCZ_MSG}/messaging/conversations/{conv_id}/change-attendant',
                                        headers=H, json={'attendantId': _att_id_fix}, timeout=15,
                                    )
                                except Exception:
                                    pass
                        try:
                            requests.post(
                                f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                                headers=H,
                                json={'body': f'🔧 *Fix promessa-nao-cumprida* — handoff_active({ho_motivo}) prometia {ho_target} mas conv sem atendente. Transferencia forcada pelo agente IA.', 'isInternal': True},
                                timeout=10,
                            )
                        except Exception:
                            pass
                        p(f"  [HANDOFF-ACTIVE] {conv_id[:12]} transferencia forcada concluida (target={ho_target})")
                    except Exception as e_force:
                        p(f"  [HANDOFF-ACTIVE] erro transferencia forcada: {e_force}")
                else:
                    p(f"  [HANDOFF-ACTIVE] {conv_id[:12]} target={ho_target} inativo — limpando handoff stale e caindo pro fluxo normal")
                    try:
                        _clear_handoff_active(conv_id, reason='target_offline_stale')
                    except Exception:
                        pass
                    ho_motivo, ho_target = None, ''
        except Exception as e_check:
            p(f"  [HANDOFF-ACTIVE] erro check transferencia: {e_check}")

        if not ho_motivo:
            pass  # cai pro fluxo normal abaixo
        else:
            nudge_sig = f'handoff_nudge:{ho_motivo}'
            if not _signature_recently_sent(conv_id, nudge_sig, window_s=4 * 3600):
                target_label = f"*{ho_target}*" if ho_target else "um consultor"
                try:
                    _fname = ''
                    st_for_name = _conv_states.get(conv_id, {})
                    if st_for_name.get('student_profile') and st_for_name['student_profile'].get('first_name'):
                        _fname = st_for_name['student_profile']['first_name']
                    nudge = (
                        f"Oii{', ' + _fname if _fname else ''}! Já registrei aqui e "
                        f"{target_label} vai dar continuidade ao seu atendimento, tá? "
                        f"Pode aguardar que em pouquinho a gente retorna. 😊"
                    )
                    send_and_track(conv_id, nudge)
                    _register_signature(conv_id, nudge_sig, nudge)
                    p(f"  [HANDOFF-ACTIVE] {conv_id[:12]} aluno persistiu - nudge unico enviado (motivo={ho_motivo})")
                except Exception as e_n:
                    p(f"  [HANDOFF-ACTIVE] erro nudge: {e_n}")
            else:
                p(f"  [HANDOFF-ACTIVE] {conv_id[:12]} aluno persistiu - SUPRIMINDO resposta (motivo={ho_motivo}, target={ho_target})")
            conversation_messages.append({'role': 'user', 'text': question})
            return

    elapsed = time.time() - last_response_time
    if elapsed < RESPONSE_COOLDOWN:
        wait = RESPONSE_COOLDOWN - elapsed
        p(f"  Cooldown: aguardando {wait:.1f}s")
        time.sleep(wait)

    is_first = conv_id not in conversation_greeted
    if is_first:
        cached = _cached_msgs.get(conv_id) or []
        for cm in cached:
            if not cm.get('received', True):
                body_check = (cm.get('body', '') or '').strip()
                if body_check and is_bot_message(body_check):
                    is_first = False
                    p(f"  Conversa já tinha msgs do bot -> NÃO é primeira interação")
                    break
    conversation_greeted.add(conv_id)
    conversation_messages.append({'role': 'user', 'text': question})
    q_lower = question.lower().strip().rstrip('!?.,').strip()

    cur_phone = _current_phone or PHONE_TO_MONITOR
    if student_profile is None:
        p(f"  Identificando aluno...")
        student_profile = identify_student(cur_phone)

    if is_within_business_hours():
        try:
            promise = get_active_preferred_attendant_promise(conv_id, max_age_hours=24)
            if promise:
                p(f"  [PROMISE] Detectada promessa ativa: {promise['name']} ({promise['hours_ago']:.1f}h atras)")
                if honor_preferred_attendant_promise(conv_id, promise):
                    return
        except Exception as e_pr:
            p(f"  [PROMISE] erro ao checar promessa: {e_pr}")

    maybe_auto_avaliacao_correta_por_agradecimento(_current_phone or cur_phone, conv_id, question)

    if student_profile and not student_profile.get('_acad_loaded'):
        _acad_cpf = student_profile.get('cpf')
        _acad_phone = _current_phone or cur_phone
        if _acad_cpf or _acad_phone:
            acad = fetch_academic_data(_acad_cpf, phone=_acad_phone)
            if acad:
                student_profile['academic'] = acad
                if _acad_cpf:
                    try:
                        _caa = fetch_caa_solicitacoes(_acad_cpf)
                        if _caa:
                            student_profile['caa_solicitacoes'] = _caa
                    except Exception as _e_caa:
                        p(f"    [CAA] Erro: {_e_caa}")
                _all_courses_list = acad.get('_all_courses', [])
                if len(_all_courses_list) > 1:
                    _all_ci = []
                    for _c in _all_courses_list[:3]:
                        _cn = _c.get('curso', '')
                        if _cn:
                            _ci = fetch_course_info(_cn)
                            if _ci:
                                _all_ci.append(_ci)
                    if _all_ci:
                        student_profile['all_courses_info'] = _all_ci
                        student_profile['course_info'] = _all_ci[0]
                        p(f"    [GRADE] {len(_all_ci)} cursos carregados (multi-curso)")
                else:
                    _course_name = acad.get('curso', '')
                    if _course_name:
                        _ci = fetch_course_info(_course_name)
                        if _ci:
                            student_profile['course_info'] = _ci
                            p(f"    [GRADE] Info do curso carregada: {_ci.get('nome','?')[:40]}")
        student_profile['_acad_loaded'] = True

        if student_profile.get('academic'):
            _polo_aluno = (student_profile['academic'].get('polo') or '').strip()
            if _polo_aluno and not _is_nosso_polo(_polo_aluno):
                p(f"  [POLO-CHECK] Polo '{_polo_aluno}' NÃO é nosso -> redirecionando")
                _handle_outro_polo(conv_id, _current_phone or cur_phone, student_profile, _polo_aluno)
                waiting_for_client = False; inactivity_start = 0
                return

    # === ÁUDIO: mensagem padrão (IA não confirma conteúdo) + opção de texto ===
    if msg_body == '[audio]':
        p(f"  [AUDIO] Áudio detectado -> respondendo (media-only) e oferecendo texto")
        if not student_profile:
            contact_name = ''
            cached = _cached_msgs.get(conv_id, [])
            for cm in cached:
                cn = cm.get('contactName', '') or cm.get('senderName', '') or ''
                if cn:
                    contact_name = cn
                    break
            new_lead_id, _ = create_lead_and_business(cur_phone, contact_name)
            if new_lead_id:
                student_profile = {'lead_id': new_lead_id, 'name': contact_name,
                                   'first_name': contact_name.split()[0] if contact_name else ''}
                p(f"  [AUDIO] Lead criado: {new_lead_id}")
        send_media_only_response(conv_id, media_type='áudio', question_label='[áudio]')
        conversation_messages.append({'role': 'user', 'text': '[Áudio enviado]'})
        waiting_for_client = True
        inactivity_start = time.time()
        return

    memory = load_memory(cur_phone)
    sentiment = detect_sentiment(question)
    name_suffix = f", {student_profile['first_name']}" if student_profile and student_profile.get('first_name') else ""

    if sentiment != 'neutro':
        p(f"  Sentimento: {sentiment}")

    # === AGUARDANDO CPF (fluxo "Já sou aluno") ===
    if _awaiting_cpf:
        _handle_cpf_input(conv_id, question, name_suffix)
        return

    # === AGUARDANDO CONFIRMAÇÃO DE POLO (Sim/Não após CPF não encontrado) ===
    if _awaiting_polo_confirm:
        _awaiting_polo_confirm = False
        if q_lower in ('sim', 's'):
            _awaiting_cpf = True
            msg = ("Pode ser que o CPF informado anteriormente esteja com alguma divergência.\n\n"
                   "Por favor, *digite novamente* seu *CPF* completo para que possamos localizar o seu cadastro.\n\n"
                   "*Exemplo*: 12345678910")
            meta_typing_on()
            send_and_track(conv_id, msg)
            conversation_messages.append({'role': 'bot', 'text': msg})
            log_to_db(conv_id, question, msg, 1.0, 'polo_sim_retry_cpf')
            waiting_for_client = True; inactivity_start = time.time()
            return
        else:
            meta_typing_on()
            send_and_track(conv_id, COMMERCIAL_REDIRECT_MSG)
            conversation_messages.append({'role': 'bot', 'text': COMMERCIAL_REDIRECT_MSG})
            log_to_db(conv_id, question, COMMERCIAL_REDIRECT_MSG, 1.0, 'polo_nao_commercial')
            if not student_profile:
                new_lead_id, _ = create_lead_and_business(_current_phone or '', '')
                if new_lead_id:
                    student_profile = {'lead_id': new_lead_id, 'name': '', 'first_name': ''}
                    p(f"  Lead criado para polo não encontrado: {new_lead_id}")
            if is_within_business_hours():
                distribute_to_attendant(conv_id, 'Aluno não encontrado no polo - encaminhar para comercial')
            else:
                p(f"  [POLO] [MODE] after_hours — sem distribuir, retorno será {next_human_available_label()}")
                send_after_hours_response(conv_id, allow_continue=False,
                                         reason='polo_commercial_after_hours', question=question)
            waiting_for_client = False; inactivity_start = 0
            return

    # === RESPOSTA A TEMPLATE/DISPARO (ack simples -> encerrar) ===
    TEMPLATE_ACK_WORDS = {
        'ok', 'okay', 'tudo bem', 'tá', 'ta', 'beleza', 'certo', 'entendi',
        'vou pagar', 'vou ver', 'vou verificar', 'pode deixar', 'blz',
        'combinado', 'anotado', 'recebi', 'sim', 's',
    }
    _is_template_reply = False
    cached_msgs = _cached_msgs.get(conv_id) or []
    for cm_rev in reversed(cached_msgs):
        if cm_rev.get('received', True):
            continue
        if _is_template_message(cm_rev):
            _is_template_reply = True
        break
    if _is_template_reply and (q_lower in TEMPLATE_ACK_WORDS or any(w in q_lower for w in CLOSING_WORDS)):
        p(f"  [TEMPLATE-ACK] Resposta simples a disparo: '{q_lower}' -> encerrando")
        msg = f"Tudo certo{name_suffix}! Qualquer dúvida é só nos chamar. 😊"
        meta_typing_on()
        send_and_track(conv_id, msg)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'template_ack_close')
        close_conversation_crm(conv_id, phone=_current_phone)
        waiting_for_client = False; inactivity_start = 0
        return

    # === ESCALAÇÃO EXPLÍCITA (ANTES de is_first, para sempre distribuir) ===
    if any(w in q_lower for w in ESCALATE_WORDS):
        meta_typing_on()

        # Extrair dúvida real do aluno (mensagens anteriores, ignorando saudações e cliques de menu)
        _esc_skip = {'oi', 'olá', 'ola', 'oii', 'bom dia', 'boa tarde', 'boa noite', 'opa',
                     'falar com atendente', 'atendente', 'atendimento', 'humano', 'transferir',
                     'falar com alguem', 'falar com alguém'}
        _student_doubt = ''
        for _m in reversed(conversation_messages):
            if _m.get('role') == 'user':
                _mtxt = (_m.get('text', '') or '').strip()
                if _mtxt.lower().rstrip('!?.,').strip() not in _esc_skip and len(_mtxt) > 3:
                    _student_doubt = _mtxt
                    break

        if _student_doubt:
            _esc_reason = f'Dúvida do aluno: "{_student_doubt[:200]}"'
        else:
            _esc_reason = ''

        if not is_within_business_hours():
            p(f"  [ESCALADO] [MODE] after_hours — sem distribuir (motivo='{_esc_reason}')")
            tier = send_after_hours_response(conv_id, allow_continue=False,
                                             reason='escalate_after_hours', question=question)
            log_to_db(conv_id, question, AFTER_HOURS_INSIST_MSG if tier == 'insist' else AFTER_HOURS_FIRST_MSG,
                      1.0, f'escalate_after_hours_{tier}')
            try:
                summary = generate_conversation_summary(conversation_messages)
                save_memory(cur_phone, student_profile, 'escalacao_after_hours', summary, sentiment)
            except Exception as e_esc:
                p(f"  [ESCALADO] Erro na memória (after_hours): {e_esc}")
            waiting_for_client = False; inactivity_start = 0
            return

        log_to_db(conv_id, question, ESCALATION_MSG, 1.0, 'escalate_request')
        distributed = distribute_to_attendant(conv_id, _esc_reason)

        conversation_messages.append({'role': 'bot', 'text': ESCALATION_MSG})
        try:
            summary = generate_conversation_summary(conversation_messages)
            save_memory(cur_phone, student_profile, 'escalacao', summary, sentiment)
        except Exception as e_esc:
            p(f"  [ESCALADO] Erro na memória: {e_esc}")
        waiting_for_client = False; inactivity_start = 0
        p(f"  [ESCALADO] Distribuído={distributed} - Follow-ups desativados")
        return

    # === ENCERRAMENTO EXPLÍCITO (ANTES de is_first, para sempre encerrar) ===
    # GUARD: se a mensagem contem pergunta ("?" ou palavras interrogativas como
    # "onde", "como", "quando", "porque", "qual", "pra que"), NAO disparar
    # closing automatico mesmo que tenha "obrigado"/"obrigada".
    # Motivo: alunos costumam concatenar pergunta + agradecimento na mesma msg
    # ("Onde esta a atividade? obrigada") e o bot encerrava sem responder.
    _q_lower_strip = q_lower.strip()
    _has_question_mark = '?' in question
    _question_words_hint = ('onde ', 'como ', 'quando ', 'qual ', 'quais ',
                            'porque', 'por que', 'porquê', 'por quê',
                            'pra que', 'para que')
    _has_question_word = any(w in _q_lower_strip for w in _question_words_hint)
    _looks_like_pure_farewell = _q_lower_strip in (
        'obrigado', 'obrigada', 'obrigado!', 'obrigada!',
        'valeu', 'vlw', 'tchau', 'ate mais', 'até mais',
        'brigado', 'brigada', 'muito obrigado', 'muito obrigada',
        'obrigado pela ajuda', 'obrigada pela ajuda',
    )
    close_match = (
        (any(w in q_lower for w in CLOSING_WORDS) and not (_has_question_mark or _has_question_word))
        or _looks_like_pure_farewell
        or q_lower in (
            'não obrigado', 'nao obrigado', 'encerrar', 'não', 'nao',
            'pode encerrar', 'pode fechar', 'fechar', 'encerrar atendimento',
            'não preciso', 'nao preciso', 'não preciso de mais nada', 'nao preciso de mais nada',
        )
    )
    if close_match:
        msg = random.choice(_CLOSING_RESPONSES).format(name_suffix=name_suffix)
        meta_typing_on()
        send_and_track(conv_id, msg)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'closing')

        close_conversation_crm(conv_id, phone=_current_phone)
        try:
            summary = generate_conversation_summary(conversation_messages)
            topic = detect_topic_from_messages(conversation_messages)
            save_memory(cur_phone, student_profile, topic, summary, sentiment)
        except Exception as e_close:
            p(f"  [ENCERR] Erro na memória: {e_close}")
        conversation_messages.clear()
        conversation_greeted.discard(conv_id)
        waiting_for_client = False
        followup_stage = 0
        inactivity_start = 0
        return

    # === RESOLVEU (ANTES de is_first, para sempre encerrar) ===
    if any(w in q_lower for w in RESOLVED_WORDS):
        _resolved_msgs = [
            f"Fico feliz que deu certo{name_suffix}! Qualquer coisa, me chama. Até mais! 😊",
            f"Que bom{name_suffix}! Se precisar no futuro, estou por aqui. Até! 😊",
            f"Oba, que ótimo{name_suffix}! Sempre que precisar, pode contar comigo. Até logo! 😊",
            f"Show{name_suffix}! Fico à disposição pro que precisar. Até a próxima! 😊",
        ]
        msg = random.choice(_resolved_msgs)
        meta_typing_on()
        send_and_track(conv_id, msg)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'resolved')

        close_conversation_crm(conv_id, phone=_current_phone)
        try:
            summary = generate_conversation_summary(conversation_messages)
            topic = detect_topic_from_messages(conversation_messages)
            save_memory(cur_phone, student_profile, topic, summary, sentiment)
        except Exception as e_resolved:
            p(f"  [RESOLVEU] Erro na memória: {e_resolved}")
        conversation_messages.clear()
        conversation_greeted.discard(conv_id)
        waiting_for_client = False
        followup_stage = 0
        inactivity_start = 0
        return

    # === PAGAMENTO JA CONFIRMADO (resposta a disparo de cobranca) ===
    # Regra: se o aluno responde ao disparo de boleto/mensalidade dizendo
    # que ja pagou, agente nao distribui — apenas confirma e encerra.
    # Detecta padroes inequivocos. NAO casa "vou pagar"/"como pagar"/"posso pagar".
    if _is_payment_confirmed_message(question):
        p(f"  [PAGAMENTO-OK] Aluno confirmou pagamento: \"{question[:80]}\"")
        _pay_msgs = [
            f"Tudo bem{name_suffix}! 😊 Obrigado pela confirmação. Qualquer coisa, é só me chamar. Até mais!",
            f"Perfeito{name_suffix}! 🙏 Obrigado por avisar. Se precisar de algo mais, estou por aqui. Até!",
            f"Ótimo{name_suffix}! 👍 Obrigado pelo retorno. Qualquer dúvida, pode contar comigo. Até mais!",
        ]
        msg = random.choice(_pay_msgs)
        meta_typing_on()
        send_and_track(conv_id, msg)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'payment_confirmed')
        _register_signature(conv_id, 'payment_confirmed', msg)

        close_conversation_crm(conv_id, phone=_current_phone)
        try:
            summary = generate_conversation_summary(conversation_messages)
            save_memory(cur_phone, student_profile, 'financeiro', summary, sentiment)
        except Exception as e_pay:
            p(f"  [PAGAMENTO-OK] Erro na memória: {e_pay}")
        conversation_messages.clear()
        conversation_greeted.discard(conv_id)
        waiting_for_client = False
        followup_stage = 0
        inactivity_start = 0
        return

    # === PAGAMENTO FORA DO VENCIMENTO (2026-05-27) ===
    # Aluno informou que vai pagar dia X (apos vencimento 25/05).
    # Agente confirma que pode pagar sim, mas valor sera maior pela perda do
    # desconto da mensalidade, e encerra. Caso Odirlei/Wanny.
    if _is_payment_later(question):
        p(f"  [PAGAMENTO-TARDIO] Aluno informou pagamento fora do vencimento: \"{question[:80]}\"")
        _info = (
            f"Tudo bem{name_suffix}! 👍\n\n"
            f"Você pode efetuar o pagamento sim, sem problema 😊\n\n"
            f"Só fique atento(a): como o vencimento da mensalidade foi *25/05*, "
            f"pagando depois dessa data o valor fica um pouco *maior*, porque os "
            f"descontos vigentes da parcela são reduzidos após o vencimento.\n\n"
            f"Quando puder, é só efetuar o pagamento normalmente pela 2ª via. "
            f"Qualquer dúvida, estou por aqui!"
        )
        meta_typing_on()
        send_and_track(conv_id, _info)
        conversation_messages.append({'role': 'bot', 'text': _info})
        log_to_db(conv_id, question, _info, 1.0, 'payment_later')
        _register_signature(conv_id, 'payment_later', _info)

        close_conversation_crm(conv_id, phone=_current_phone)
        try:
            summary = generate_conversation_summary(conversation_messages)
            save_memory(cur_phone, student_profile, 'financeiro', summary, sentiment)
        except Exception as e_pl:
            p(f"  [PAGAMENTO-TARDIO] Erro memoria: {e_pl}")
        conversation_messages.clear()
        conversation_greeted.discard(conv_id)
        waiting_for_client = False
        followup_stage = 0
        inactivity_start = 0
        return

    # === ALUNO OCUPADO / RETORNA DEPOIS (2026-05-27) ===
    # Aluno disse 'estou ocupado/sem tempo agora, retorno depois'. Agente
    # responde com ack curto e encerra — nao deixa pendente. Caso Karen.
    if _is_busy_later(question):
        p(f"  [OCUPADO] Aluno informou estar ocupado: \"{question[:80]}\"")
        _ack = (
            f"Tudo bem{name_suffix}! 😊 Quando estiver mais tranquilo(a), "
            f"estaremos à disposição. Até mais!"
        )
        meta_typing_on()
        send_and_track(conv_id, _ack)
        conversation_messages.append({'role': 'bot', 'text': _ack})
        log_to_db(conv_id, question, _ack, 1.0, 'busy_later')
        _register_signature(conv_id, 'busy_later', _ack)

        close_conversation_crm(conv_id, phone=_current_phone)
        try:
            summary = generate_conversation_summary(conversation_messages)
            save_memory(cur_phone, student_profile, 'ocupado', summary, sentiment)
        except Exception as e_bl:
            p(f"  [OCUPADO] Erro memoria: {e_bl}")
        conversation_messages.clear()
        conversation_greeted.discard(conv_id)
        waiting_for_client = False
        followup_stage = 0
        inactivity_start = 0
        return

    # === RETENÇÃO (cancelamento / trancamento) — ANTES de is_first ===
    if is_retention_intent(question):
        p(f"  [RETENÇÃO] Intenção detectada: \"{question[:80]}\"")

        # (2026-06-25) TESTE: telefone de teste -> apenas aciona a automação RET-IA
        # (tag) e silencia o bot. Ignora after-hours, dedup, distribuição e mensagem.
        if _use_ret_ia_automation(_current_phone):
            _lead_test = student_profile.get('lead_id') if student_profile else None
            _trigger_retention_tag_only(conv_id, _lead_test, question, phone=_current_phone)
            p(f"  [RETENÇÃO] [RET-IA] tag/automação acionada e bot silenciado (sem distribuir/mensagem)")
            # (2026-06-30) NÃO marcar 'waiting_for_client': a conversa foi entregue à
            # automação/time de Retenção. Marcar como aguardando a tornava elegível ao
            # loop de follow-up/auto-close — caso Maria Clara: fechada após ~16h
            # esperando o consultor. Fica silenciada (handoff), sem follow-up/close.
            waiting_for_client = False; inactivity_start = 0
            return

        # Fora do horário: NÃO orientar passos de cancelamento, apenas avisar do Wesley
        if not is_within_business_hours():
            retorno = next_human_available_label()
            name_prefix = _student_first_name_prefix(conv_id)
            msg_after = RETENTION_AFTER_HOURS_MSG.format(name=name_prefix, retorno_label=retorno)
            # DEDUP: nao reenvia mesma mensagem de retencao-fora-do-horario na conv (24h)
            if _signature_recently_sent(conv_id, 'retention_after_hours', window_s=24 * 3600):
                p(f"  [RETENÇÃO] dedup: ja enviado retention_after_hours nas ultimas 24h - suprimindo reenvio")
            else:
                meta_typing_on()
                send_and_track(conv_id, msg_after)
                conversation_messages.append({'role': 'bot', 'text': msg_after})
                log_to_db(conv_id, question, msg_after, 1.0, 'retention_after_hours')
                _register_signature(conv_id, 'retention_after_hours', msg_after)
                # (2026-06-08) target='' (pendente): quando o expediente abrir, o
                # in_hours_rescue re-detecta e distribui via trigger_retention para
                # o membro ativo (Wesley OU Danúbia).
                _mark_handoff_active(conv_id, 'retention_after_hours', target='',
                                     ttl_s=14 * 3600, body=msg_after)
                try:
                    requests.post(
                        f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                        headers=H,
                        json={'body': f'🤝 *Retenção fora do horário* — IA orientou aluno; time de Retenção deve retomar {retorno}.',
                              'isInternal': True},
                        timeout=10,
                    )
                except Exception:
                    pass
            try:
                summary = generate_conversation_summary(conversation_messages)
                save_memory(cur_phone, student_profile, 'retencao_after_hours', summary, sentiment)
            except Exception as e_ret:
                p(f"  [RETENÇÃO] Erro na memória (after_hours): {e_ret}")
            record_pending_escalation(conv_id, 'retention_after_hours', tier='insist',
                                    retorno_label=retorno, question=question)
            p(f"  [RETENÇÃO] [MODE] after_hours — sem trigger_retention, retorno {retorno} (time de Retenção)")
            waiting_for_client = True; inactivity_start = time.time()
            return

        if _signature_recently_sent(conv_id, 'retention', window_s=24 * 3600):
            p(f"  [RETENÇÃO] dedup: retention ja enviada nas ultimas 24h - suprimindo reenvio")
        else:
            # (2026-06-08) trigger_retention escolhe Wesley/Danúbia por disponibilidade
            # e marca o handoff. Só envia mensagens se houver alguem ativo.
            lead_id = student_profile.get('lead_id') if student_profile else None
            _alvo_ret = trigger_retention(conv_id, lead_id, question, phone=_current_phone)
            if _alvo_ret:
                meta_typing_on()
                send_and_track(conv_id, RETENTION_MSG)
                conversation_messages.append({'role': 'bot', 'text': RETENTION_MSG})
                log_to_db(conv_id, question, RETENTION_MSG, 1.0, 'retention')
                _register_signature(conv_id, 'retention', RETENTION_MSG)

                # Apresentação do consultor de retenção (Wesley OU Danúbia)
                _fname = (student_profile.get('first_name') or '').strip() if student_profile else ''
                _ret_first = _alvo_ret.split()[0]
                _ret_intro = (f"Olá{', *' + _fname + '*' if _fname else ''}! "
                              f"Sou {_ret_first} e irei seguir com o seu atendimento 😊")
                time.sleep(1)
                send_and_track(conv_id, _ret_intro)
                _register_signature(conv_id, 'retention_intro', _ret_intro)
                p(f"  [RETENÇÃO] Apresentação de {_ret_first} enviada pelo agente")
            else:
                # Ninguém do time ativo agora: segura com msg neutra; in_hours_rescue
                # / queue_sweep re-tentam quando alguém ficar ativo.
                meta_typing_on()
                send_and_track(conv_id, RETENTION_MSG)
                conversation_messages.append({'role': 'bot', 'text': RETENTION_MSG})
                log_to_db(conv_id, question, RETENTION_MSG, 1.0, 'retention')
                _register_signature(conv_id, 'retention', RETENTION_MSG)
                _mark_handoff_active(conv_id, 'retention', target='', ttl_s=8 * 3600, body=RETENTION_MSG)
                record_pending_escalation(conv_id, 'retention', tier='insist',
                                          retorno_label=next_human_available_label(), question=question)
                p(f"  [RETENÇÃO] Nenhum membro ativo — segurando p/ re-tentativa nos ciclos")

        try:
            summary = generate_conversation_summary(conversation_messages)
            save_memory(cur_phone, student_profile, 'retencao', summary, sentiment)
        except Exception as e_ret:
            p(f"  [RETENÇÃO] Erro na memória: {e_ret}")
        waiting_for_client = False; inactivity_start = 0
        p(f"  [RETENÇÃO] Conversa encaminhada para time de Retenção - follow-ups desativados")
        return

    # === A1 / PROVA REGIMENTAL ===
    # Regra do time: nota da A1 do MeS VIGENTE eh divulgada ate o fim do mes.
    # A1 de MeS ANTERIOR: orienta procurar tutor/professor.
    # Sem mes informado: pergunta de qual mes.
    try:
        a1_info = detect_a1_intent(question)
        if a1_info.get('is_a1'):
            p(f"  [A1] intent detectado mes={a1_info.get('mes')} quando={a1_info.get('quando')}")
            if handle_a1_intent(conv_id, a1_info, question=question):
                conversation_messages.append({'role': 'user', 'text': question})
            return
    except Exception as e_a1:
        p(f"  [A1] erro: {e_a1}")

    # === ESQUECI MINHA SENHA / REDEFINIR SENHA ===
    # Procedimento OFICIAL: clica em "Esqueci minha senha" -> digita
    # telefone atualizado -> recebe codigo por SMS -> cria nova senha.
    # NAO eh por e-mail/link/CPF. Resposta canonica ANTES do KB/LLM para
    # impedir paraphrase errada (o KB antigo dizia "faça login com email
    # acadêmico antes" — frase confusa e impossivel para quem esqueceu).
    try:
        if detect_esqueci_senha_intent(question):
            p(f"  [ESQUECI-SENHA] intent detectado — resposta canonica (telefone + SMS)")
            if handle_esqueci_senha_intent(conv_id, question=question):
                conversation_messages.append({'role': 'user', 'text': question})
                conversation_messages.append({'role': 'bot', 'text': ESQUECI_SENHA_MSG})
            return
    except Exception as e_es:
        p(f"  [ESQUECI-SENHA] erro: {e_es}")

    # === MASTERCLASS FAQ ===
    # Resposta canonica definida pelo time. Plugado ANTES do polo/LLM para
    # garantir que NUNCA seja parafraseada (o LLM tende a inventar prazos/email).
    try:
        if detect_masterclass_intent(question):
            p(f"  [MASTERCLASS] intent detectado — resposta canonica")
            if handle_masterclass_intent(conv_id, question=question):
                conversation_messages.append({'role': 'user', 'text': question})
                conversation_messages.append({'role': 'bot', 'text': MASTERCLASS_MSG})
            return
    except Exception as e_mc:
        p(f"  [MASTERCLASS] erro: {e_mc}")

    # === INICIO DAS AULAS (resolvido por turma real do aluno) ===
    # (2026-06-03) Usa data_matricula (mm_matriculados) + janelas do calendario
    # para descobrir a turma de ingresso e a data oficial de inicio. Se nao der
    # para determinar (Pos / fora da base / data fora das janelas), transfere
    # para consultor. NUNCA responde data fixa ("agosto") nem inventa.
    try:
        if detect_inicio_aulas_intent(question):
            _acad_ia = (student_profile or {}).get('academic')
            p(f"  [INICIO-AULAS] intent detectado — resolvendo turma do aluno")
            _ia_msg = handle_inicio_aulas_intent(conv_id, question=question, academic=_acad_ia)
            if _ia_msg:
                conversation_messages.append({'role': 'user', 'text': question})
                conversation_messages.append({'role': 'bot', 'text': _ia_msg})
            return
    except Exception as e_ia:
        p(f"  [INICIO-AULAS] erro: {e_ia}")

    # === SEMESTRE / TURMA ATUAL (resolvido pelos dados do aluno) ===
    # (2026-06-03) Mesmo padrao do inicio das aulas: responde o semestre (serie
    # da mm_matriculados) quando o aluno PERGUNTA; sem dado confiavel (Pos /
    # fora da base) transfere para consultor. NUNCA inventa.
    try:
        if detect_semestre_intent(question):
            _acad_sem = (student_profile or {}).get('academic')
            p(f"  [SEMESTRE] intent detectado — resolvendo semestre/turma do aluno")
            _sem_msg = handle_semestre_intent(conv_id, question=question, academic=_acad_sem)
            if _sem_msg:
                conversation_messages.append({'role': 'user', 'text': question})
                conversation_messages.append({'role': 'bot', 'text': _sem_msg})
            return
    except Exception as e_sem:
        p(f"  [SEMESTRE] erro: {e_sem}")

    # === POLO: intencao de ir presencialmente / dificuldade comunicacao online ===
    # Resolve o caso "Vanessa Carmona" (LLM inventou endereco da Barra Funda).
    # SEMPRE transfere para humano quando aluno expressa intencao de ir, e o
    # endereco enviado vem de POLOS_OFICIAIS (NUNCA inventado pelo LLM).
    try:
        polo_intent = detect_polo_intent(question)
    except Exception:
        polo_intent = {'intent': 'none', 'polo_mencionado': None}
    if polo_intent['intent'] == 'visit':
        p(f"  [POLO-VISIT] Intencao de visita / dificuldade: \"{question[:80]}\"")
        handle_polo_visit_intent(conv_id, polo_intent.get('polo_mencionado'), question=question)
        conversation_messages.append({'role': 'user', 'text': question})
        return
    if polo_intent['intent'] == 'address_only':
        p(f"  [POLO-ADDR] Pedido de endereco: \"{question[:80]}\"")
        handle_polo_address_only(conv_id, polo_intent.get('polo_mencionado'), question=question)
        conversation_messages.append({'role': 'user', 'text': question})
        return

    # === "JÁ SOU ALUNO" / "QUERO ME MATRICULAR" (resposta ao "não encontrado na base") ===
    if q_lower in ('já sou aluno', 'ja sou aluno'):
        _awaiting_cpf = True
        msg = ("Certo! Para começarmos, por favor *digite* seu *CPF* completo.\n\n"
               "*Exemplo*: Se seu CPF for 123.456.789-10 você deverá digitar 12345678910.")
        meta_typing_on()
        send_and_track(conv_id, msg)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'ask_cpf')
        waiting_for_client = True; inactivity_start = time.time()
        return

    if q_lower in ('quero me matricular', 'quero matricular', 'matricular'):
        meta_typing_on()
        send_and_track(conv_id, COMMERCIAL_REDIRECT_MSG)
        conversation_messages.append({'role': 'bot', 'text': COMMERCIAL_REDIRECT_MSG})
        log_to_db(conv_id, question, COMMERCIAL_REDIRECT_MSG, 1.0, 'commercial_redirect')
        if not student_profile:
            contact_name = ''
            cached = _cached_msgs.get(conv_id) or []
            for cm in cached:
                cn = cm.get('contactName', '') or cm.get('senderName', '') or ''
                if cn:
                    contact_name = cn
                    break
            new_lead_id, new_biz_id = create_lead_and_business(
                _current_phone or '', contact_name
            )
            if new_lead_id:
                student_profile = {'lead_id': new_lead_id, 'name': contact_name, 'first_name': contact_name.split()[0] if contact_name else ''}
                p(f"  Lead criado para interessado em matrícula: {new_lead_id}")
        if is_within_business_hours():
            distribute_to_attendant(
                conv_id,
                'Interessado em matrícula — orientar sobre o time comercial'
            )
        else:
            p(f"  [MATRÍCULA] [MODE] after_hours — sem distribuir, retorno será {next_human_available_label()}")
            send_after_hours_response(conv_id, allow_continue=False,
                                     reason='matricula_after_hours', question=question)
        waiting_for_client = False; inactivity_start = 0
        return

    # === PRIMEIRA INTERAÇÃO: verifica se lead/negócio existe ===
    if is_first:
        if student_profile:
            _student_in_base = True
            p(f"  Lead existe no CRM -> saudação + menu (sem mencionar matrícula)")
        else:
            _student_in_base = False
            p(f"  Lead NÃO encontrado no CRM -> criando lead + fluxo de identificação")
            contact_name = ''
            cached = _cached_msgs.get(conv_id, [])
            for cm in cached:
                cn = cm.get('contactName', '') or cm.get('senderName', '') or ''
                if cn:
                    contact_name = cn
                    break
            new_lead_id, new_biz_id = create_lead_and_business(
                _current_phone or '', contact_name
            )
            if new_lead_id:
                student_profile = {
                    'lead_id': new_lead_id, 'name': contact_name,
                    'first_name': contact_name.split()[0] if contact_name else ''
                }
                p(f"  Lead criado para contato novo: {new_lead_id}")
            msg = ("👋 Oi, tudo bem?\n\n"
                   "Não localizei este telefone em nosso sistema.\n\n"
                   "Para continuarmos, por favor *escolha* uma das opções abaixo: 👇")
            meta_typing_on()
            send_and_track(conv_id, msg, buttons=NOT_IN_BASE_BUTTONS)
            conversation_messages.append({'role': 'bot', 'text': msg})
            log_to_db(conv_id, question, msg, 1.0, 'not_in_base')
            waiting_for_client = True; inactivity_start = time.time()
            return

        TOPIC_LABELS = {
            'acesso': 'acesso ao portal',
            'financeiro': 'questões financeiras',
            'academico': 'aulas e conteúdo',
            'matricula': 'matrícula',
            'documentos': 'documentos',
        }

        if student_profile and student_profile.get('first_name'):
            fname = student_profile['first_name']
            if memory and memory.get('interaction_count', 0) > 0:
                topic_key = (memory.get('last_topic') or '').lower()
                topic_label = TOPIC_LABELS.get(topic_key)
                if topic_label:
                    greeting = GREETING_RETURNING.format(fname=fname, topic=topic_label)
                else:
                    greeting = GREETING_RETURNING_NO_TOPIC.format(fname=fname)
            else:
                greeting = GREETING_NEW.format(fname=fname)
        else:
            greeting = GREETING_ANONYMOUS

        greeting_alert_text = build_greeting_alerts()
        if greeting_alert_text:
            greeting += greeting_alert_text
            p(f"  Saudação com alerta(s) anexado(s)")
        p(f"  Saudação personalizada (returning={memory is not None and memory.get('interaction_count', 0) > 0})")
        send_and_track(conv_id, greeting, buttons=GREETING_BUTTONS)
        conversation_messages.append({'role': 'bot', 'text': greeting})
        log_to_db(conv_id, question, greeting, 1.0, 'greeting')
        waiting_for_client = True; inactivity_start = time.time()

        _ACTIONABLE_FIRST_KW = (
            'disciplinas', 'materias', 'grade', 'boleto', 'prova', 'nota', 'notas',
            'senha', 'acesso', 'financeiro', 'documento', 'historico', 'histórico',
            'estagio', 'estágio', 'tcc', 'rematricula', 'rematrícula', 'aula',
            'minha grade', 'mensalidade', 'declaração', 'declaracao', 'pix',
        )
        if not is_greeting(question) and any(kw in q_lower for kw in _ACTIONABLE_FIRST_KW):
            p(f"  [1a-MSG] Pergunta acionável detectada na 1a msg, processando após saudação...")
        else:
            return

    # === SAUDAÇÃO REPETIDA (não é a primeira vez) ===
    if is_greeting(question):
        p(f"  Saudação repetida -> mostrando menu")
        msg = f"Claro{name_suffix}! Como posso te ajudar? Escolha uma opção abaixo 👇"
        greeting_alert_text = build_greeting_alerts()
        if greeting_alert_text:
            msg += greeting_alert_text
        meta_typing_on()
        send_and_track(conv_id, msg, buttons=GREETING_BUTTONS)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'greeting_repeat')
        waiting_for_client = True; inactivity_start = time.time()
        return

    # === LEAD NÃO ENCONTRADO: criar lead e reapresentar opções ===
    if _student_in_base is False and student_profile is None:
        contact_name = ''
        cached = _cached_msgs.get(conv_id, [])
        for cm in cached:
            cn = cm.get('contactName', '') or cm.get('senderName', '') or ''
            if cn:
                contact_name = cn
                break
        new_lead_id, new_biz_id = create_lead_and_business(
            _current_phone or '', contact_name
        )
        if new_lead_id:
            student_profile = {
                'lead_id': new_lead_id, 'name': contact_name,
                'first_name': contact_name.split()[0] if contact_name else ''
            }
            p(f"  Lead criado automaticamente: {new_lead_id}")
        p(f"  Lead não encontrado no CRM -> opções de identificação")
        msg = ("Para que eu possa te atender, preciso primeiro te localizar em nosso sistema.\n\n"
               "Por favor, escolha uma das opções abaixo: 👇")
        meta_typing_on()
        send_and_track(conv_id, msg, buttons=NOT_IN_BASE_BUTTONS)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'not_in_base_block')
        waiting_for_client = True; inactivity_start = time.time()
        return

    # === SIM (resposta ao "algo mais específico?") ===
    if q_lower == 'sim':
        msg = "Claro! Me conta, qual é a sua dúvida específica? 😊"
        meta_typing_on()
        send_and_track(conv_id, msg)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'ask_specific')
        waiting_for_client = True; inactivity_start = time.time()
        return

    # === OUTRA DÚVIDA / VER OPÇÕES / PEDIDO DE AJUDA GENÉRICO ===
    if q_lower in ('tenho outra dúvida', 'tenho outra duvida', 'outra dúvida', 'outra duvida', 'outra',
                    'ver opções', 'ver opcoes', 'ver opções', 'tentar de novo', 'opções', 'opcoes', 'menu',
                    'preciso de ajuda', 'ajuda', 'me ajuda', 'pode me ajudar', 'quero ajuda',
                    'preciso de help', 'help', 'socorro', 'como funciona', 'o que voce faz',
                    'o que você faz', 'quais opções', 'quais opcoes', 'ainda estou aqui',
                    'ainda estou aqui!', 'voltar para o início', 'voltar para o inicio', 'voltar'):
        if student_profile and student_profile.get('first_name'):
            msg = f"Claro, {student_profile['first_name']}! Como posso te ajudar?"
        else:
            msg = "Claro! Como posso te ajudar?"
        meta_typing_on()
        send_and_track(conv_id, msg, buttons=MAIN_MENU_BUTTONS)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'menu')
        waiting_for_client = True; inactivity_start = time.time()
        return

    # === ESCALAÇÃO IMEDIATA (CPF/RGM) ===
    should_escalate, reason = is_escalation_trigger(question)
    if should_escalate:
        meta_typing_on()
        if not is_within_business_hours():
            p(f"  [ESCALADO][CPF] [MODE] after_hours — sem distribuir, retorno {next_human_available_label()}")
            send_after_hours_response(conv_id, allow_continue=False,
                                     reason='cpf_after_hours', question=question)
            log_to_db(conv_id, question, AFTER_HOURS_FIRST_MSG, 0.1, 'escalate_cpf_after_hours')
            try:
                requests.post(
                    f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                    headers=H,
                    json={'body': '🔒 Dados sensíveis (CPF/RGM) detectados fora do horário — IA orientou aluno aguardar retorno.',
                          'isInternal': True},
                    timeout=10,
                )
            except Exception:
                pass
            # ACAO A (2026-05-21): reset flags apos escalacao CPF (after-hours)
            _awaiting_cpf = False
            _awaiting_polo_confirm = False
            waiting_for_client = False; inactivity_start = 0
            return
        log_to_db(conv_id, question, ESCALATION_MSG, 0.1, 'escalate_cpf')
        distributed = distribute_to_attendant(conv_id, 'Dados sensíveis detectados (CPF/RGM)')
        conversation_messages.append({'role': 'bot', 'text': ESCALATION_MSG})
        # ACAO A (2026-05-21): reset flags apos escalacao CPF. Bug imagem 1:
        # bot distribuia para Felipe E disparava fluxo "Nao encontramos voce"
        # + polos. Causa: _awaiting_cpf/_awaiting_polo_confirm persistiam.
        _awaiting_cpf = False
        _awaiting_polo_confirm = False
        waiting_for_client = False; inactivity_start = 0
        p(f"  [ESCALADO] Distribuído={distributed} - flags resetadas — humano assumiu")
        return

    # === STRIP EMOJIS + ASTERISCOS ===
    stripped = q_lower.replace('*', '')
    for e in '🔑💰📚📄🔄👤🧾💳🤝💸🆕📱🖥️📅📖📝📋📎💲🏷️📈🔒💠⚠️📧🌐📨📊⏰':
        stripped = stripped.replace(e + ' ', '').replace(e, '')
    stripped = stripped.strip()

    # Várias linhas (ex.: citação do menu + opção) — tentar cada trecho
    _stripped_variants = [stripped]
    for _seg in stripped.replace('\r', '\n').split('\n'):
        _s2 = _seg.strip()
        if _s2 and _s2 not in _stripped_variants:
            _stripped_variants.append(_s2)

    # =====================================================================
    # VERIFICA SE O TEXTO CORRESPONDE A UM ITEM DE MENU CONHECIDO
    # Agente envia diretamente submenus e conteúdo (100% agente).
    # =====================================================================
    _matched_l1_key = None
    _matched_l3_key = None
    _matched_direct_key = None
    _matched_rag_key = None

    for _try in _stripped_variants:
        for menu_key, mapped_key in MAIN_MENU_KEYS.items():
            if _menu_body_matches_normalised(_try, menu_key):
                _matched_l1_key = mapped_key
                break
        if _matched_l1_key:
            break
    if not _matched_l1_key:
        for _try in _stripped_variants:
            for l3_key in SUBMENU_L3:
                if _menu_body_matches_normalised(_try, l3_key):
                    _matched_l3_key = l3_key
                    break
            if _matched_l3_key:
                break
    if not _matched_l1_key and not _matched_l3_key:
        for _try in _stripped_variants:
            for direct_key in SUBMENU_DIRECT_RESPONSE:
                if _menu_body_matches_normalised(_try, direct_key):
                    _matched_direct_key = direct_key
                    break
            if _matched_direct_key:
                break
    if not _matched_l1_key and not _matched_l3_key and not _matched_direct_key:
        for _try in _stripped_variants:
            for sub_key in SUBMENU_TO_QUESTION:
                if _menu_body_matches_normalised(_try, sub_key):
                    _matched_rag_key = sub_key
                    break
            if _matched_rag_key:
                break

    # --- Menu L1 (ex: "Financeiro") → agente envia submenu L2 ---
    if _matched_l1_key:
        submenu = SUBMENU.get(_matched_l1_key)
        if submenu:
            p(f"  Menu L1: '{stripped}' -> enviando submenu '{_matched_l1_key}'")
            meta_typing_on()
            send_and_track(conv_id, submenu['text'], buttons=submenu.get('buttons', []))
            conversation_messages.append({'role': 'bot', 'text': submenu['text']})
            log_to_db(conv_id, question, submenu['text'], 1.0, 'menu_l1')
            waiting_for_client = True; inactivity_start = time.time()
            return
        p(f"  Menu L1: '{stripped}' mapeado para '{_matched_l1_key}' mas sem submenu")

    # --- Menu L2/L3 (ex: "Boleto / Pagamento") → agente envia submenu L3 ---
    if _matched_l3_key:
        l3_data = SUBMENU_L3[_matched_l3_key]
        p(f"  Menu L3: '{stripped}' -> enviando opcoes L3")
        meta_typing_on()
        send_and_track(conv_id, l3_data['text'], buttons=l3_data.get('buttons', []))
        conversation_messages.append({'role': 'bot', 'text': l3_data['text']})
        log_to_db(conv_id, question, l3_data['text'], 1.0, 'menu_l3')
        waiting_for_client = True; inactivity_start = time.time()
        return

    # --- Resposta direta (response_text do DB) ---
    if _matched_direct_key:
        direct_text = SUBMENU_DIRECT_RESPONSE[_matched_direct_key]
        p(f"  Menu direto: '{stripped}' -> enviando response_text")
        meta_typing_on()
        send_and_track(conv_id, direct_text)
        time.sleep(1)
        send_and_track(conv_id, "Ficou alguma dúvida sobre o assunto? 😊\nDigite *Resolveu* ou me conte sua dúvida!")
        conversation_messages.append({'role': 'bot', 'text': direct_text})
        log_to_db(conv_id, question, direct_text, 1.0, 'menu_direct')
        cur_phone = _current_phone or PHONE_TO_MONITOR
        try:
            tabulate_interaction(conversation_messages, student_profile, cur_phone, conv_id=conv_id)
        except Exception as e_tab:
            p(f"    Erro tabulação menu direto: {e_tab}")
        waiting_for_client = True; inactivity_start = time.time()
        return

    # --- Item de menu mapeado para RAG (ex: "Segunda via do boleto") ---
    if _matched_rag_key:
        mapped_question = SUBMENU_TO_QUESTION[_matched_rag_key]
        p(f"  Menu RAG: '{stripped}' -> RAG com '{mapped_question[:50]}'")
        search_query = mapped_question

    # =====================================================================
    # TEXTO LIVRE (não corresponde a nenhum menu) → agente responde via RAG
    # =====================================================================
    if not _matched_rag_key:
        if image_desc:
            search_query = f"{question}\n\n{image_desc}"
            p(f"  RAG: texto + vision desc combinados para busca")
        else:
            search_query = question

    has_image_context = bool(image_desc)
    if len(stripped) <= 3 and not _matched_rag_key and not has_image_context:
        p(f"  Msg muito curta sem match, mostrando menu")
        _short_fallbacks = [
            "Hmm, não consegui entender 😅 Me conta melhor o que precisa, ou escolhe uma opção aqui:",
            "Opa, pode me explicar um pouco mais? Ou se preferir, seleciona uma opção:",
            "Não consegui pegar direito 🤔 Pode detalhar mais ou escolher aqui embaixo?",
            "Me ajuda a te ajudar 😊 Pode descrever melhor ou escolher uma opção:",
        ]
        msg = random.choice(_short_fallbacks)
        meta_typing_on()
        send_and_track(conv_id, msg, buttons=MAIN_MENU_BUTTONS)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 0.0, 'fallback_short')
        waiting_for_client = True; inactivity_start = time.time()
        return

    # === GRADE DIRETA: se perguntou sobre grade/disciplinas e temos o link ===
    _GRADE_KEYWORDS = ('disciplinas', 'materias', 'matriz curricular', 'grade curricular',
                       'materias do curso', 'disciplinas do curso', 'quais materias', 'quais disciplinas',
                       'grade do curso', 'grade do meu curso', 'minha grade')
    _question_lower = question.lower()
    if any(kw in _question_lower for kw in _GRADE_KEYWORDS):
        _has_multi_courses = len((student_profile or {}).get('all_courses_info') or []) > 1
        ci = (student_profile or {}).get('course_info')
        if _has_multi_courses:
            p(f"  [GRADE] Multi-curso detectado, delegando ao LLM para perguntar qual curso")
            # Não retorna; segue para o pipeline RAG para LLM perguntar qual curso.
        elif ci and ci.get('grade_link'):
            fname = (student_profile or {}).get('first_name', '')
            name_part = f", {fname}" if fname else ''
            grade_msg = (
                f"Olha{name_part}, aqui está o link da *grade curricular* do seu curso "
                f"*{ci.get('nome', 'seu curso')}*:\n\n"
                f"{ci['grade_link']}\n\n"
                f"Lá você consegue ver todas as disciplinas de cada semestre! "
                f"Se tiver alguma dúvida sobre uma disciplina específica, me conta que te ajudo 😊"
            )
            p(f"  [GRADE-DIRETA] Enviando link da grade: {ci['grade_link'][:60]}")
            meta_typing_on()
            send_and_track(conv_id, grade_msg)
            conversation_messages.append({'role': 'bot', 'text': grade_msg})
            log_to_db(conv_id, question, grade_msg, 1.0, 'grade_link_direto')
            waiting_for_client = True; inactivity_start = time.time()
            return
        elif not _has_multi_courses and not ci and student_profile:
            _grade_stop = {'quero', 'ver', 'quais', 'sao', 'são', 'sobre', 'minha', 'minhas',
                           'meu', 'como', 'onde', 'qual', 'grade', 'disciplinas', 'disciplina',
                           'materias', 'materia', 'curricular', 'matriz', 'curso', 'pode',
                           'voce', 'você', 'meus', 'favor', 'por', 'que', 'das', 'dos',
                           'com', 'sem', 'para', 'pra', 'uma', 'uns', 'umas', 'preciso',
                           'gostaria', 'consegue', 'enviar', 'mandar', 'olhar', 'acessar'}
            _grade_words = [w for w in question.split()
                           if w.lower().strip('?!.,') not in _grade_stop and len(w) > 2]
            _grade_search = ' '.join(_grade_words) if _grade_words else ''
            _ci_try = fetch_course_info(_grade_search) if _grade_search else None
            if _ci_try and _ci_try.get('grade_link'):
                fname = (student_profile or {}).get('first_name', '')
                name_part = f", {fname}" if fname else ''
                grade_msg = (
                    f"Achei aqui{name_part}! O link da *grade curricular* de "
                    f"*{_ci_try.get('nome', 'curso')}*:\n\n"
                    f"{_ci_try['grade_link']}\n\n"
                    f"Se precisar de mais alguma coisa, tô por aqui! 😊"
                )
                p(f"  [GRADE-BUSCA] Grade encontrada por busca: {_ci_try.get('nome','?')[:40]}")
                meta_typing_on()
                send_and_track(conv_id, grade_msg)
                conversation_messages.append({'role': 'bot', 'text': grade_msg})
                log_to_db(conv_id, question, grade_msg, 0.9, 'grade_link_busca')
                waiting_for_client = True; inactivity_start = time.time()
                return
            # Sem grade_link encontrada -> segue para o pipeline RAG (não retorna).
            p(f"  [GRADE] sem grade_link disponível -> caindo no RAG")

    # === PIPELINE RAG ===
    p(f"  Pipeline RAG... (sentimento: {sentiment})")
    try:
        results, emb = rag_search(search_query)
    except Exception as e:
        p(f"  ERRO RAG search: {e}")
        msg = "Desculpe, tive um problema técnico. Posso te ajudar de outra forma?"
        send_and_track(conv_id, msg, buttons=['Tentar de novo', 'Falar com atendente', 'Ver opções'])
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 0.0, 'rag_error')
        waiting_for_client = True; inactivity_start = time.time()
        return

    top_score = results[0][5] if results else 0

    if top_score < 0.50:
        msg = "Hmm, não encontrei uma resposta exata para isso. Posso te ajudar de outra forma?"
        meta_typing_on()
        if is_within_business_hours():
            _low_sim_buttons = ['Tentar de novo', 'Falar com atendente', 'Ver opções']
        else:
            _low_sim_buttons = ['Tentar de novo', 'Ver opções']
            p(f"  [LOW-SIM] [MODE] after_hours — removido botão 'Falar com atendente'")
        send_and_track(conv_id, msg, buttons=_low_sim_buttons)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, top_score, 'escalate_low_sim')
        cur_phone = _current_phone or PHONE_TO_MONITOR
        try:
            tabulate_interaction(conversation_messages, student_profile, cur_phone, conv_id=conv_id)
        except Exception as e_tab:
            p(f"    Erro tabulação low_sim: {e_tab}")
        waiting_for_client = True; inactivity_start = time.time()
        return

    references = build_references(results)

    # CALENDARIO ACADEMICO (2026-05-25): injeta datas oficiais quando o aluno
    # pergunta sobre prova, nota, matricula, inicio das aulas, etc. Filtra por
    # semestre corrente, publico (calouro/veterano) e datas futuras (180d).
    # Bloqueia alucinacao: LLM eh orientado a usar APENAS as datas listadas.
    try:
        # FIX (2026-05-25): so injeta + marca como CALENDARIO se a mensagem
        # ATUAL do aluno tem pelo menos 1 keyword de topico de calendario.
        # Defesa redundante alem do filtro em _get_relevant_calendar_events.
        _cal_topics_now = _detect_calendar_topic(question or '')
        if _cal_topics_now:
            _cal_events = _get_relevant_calendar_events(
                student_profile=student_profile,
                user_message=question,
                max_events=10,
            )
            if _cal_events:
                _cal_block = _format_calendar_block(_cal_events)
                if _cal_block:
                    references = (references or '') + "\n\n" + _cal_block + "\n"
                    p(f"    [CAL] Injetado {len(_cal_events)} evento(s) (topicos={_cal_topics_now})")
                    _mark_calendar_used(conv_id)
    except Exception as _e_cal:
        p(f"    [CAL] Falha ao injetar calendario: {_e_cal}")

    # CAMADA D6 (2026-05-25): loop de frustracao do aluno -> escalar humano.
    # Caso Sandra: aluno disse "nao consegui" / "nao aparece" / "nao a tarde"
    # multiplas vezes, bot continuou tentando parafrasear orientacao em vez
    # de chamar humano. Apos 2 sinais em 10min -> escalation com handoff.
    try:
        _conv_msgs_frustr = _cached_msgs.get(conv_id) or get_conversation_messages_api(conv_id, limit=20)
        if conv_id:
            _cached_msgs[conv_id] = _conv_msgs_frustr
        _frustr_count = _count_frustration_signals(_conv_msgs_frustr or [])
        if _frustr_count >= 2 and is_within_business_hours():
            p(f"  [FRUSTRATION-D6] {conv_id[:12]} {_frustr_count} sinais de frustracao em 10min -> escalando humano")
            _fr_msg = (
                "Percebi que você já tentou algumas vezes — vou te transferir "
                "agora pra um de nossos consultores resolver isso direitinho com "
                "você, tá? Um momento! 😊"
            )
            send_and_track(conv_id, _fr_msg, force=True)
            conversation_messages.append({'role': 'bot', 'text': _fr_msg})
            log_to_db(conv_id, question, _fr_msg, 0.0, 'escalate_frustration')
            try:
                distribute_to_attendant(conv_id, f'Aluno frustrado: {_frustr_count} tentativas')
            except Exception as e_fr:
                p(f"  [FRUSTRATION-D6] erro distribute: {e_fr}")
            waiting_for_client = False; inactivity_start = 0
            return
    except Exception as e_fr_outer:
        p(f"  [FRUSTRATION-D6] erro: {e_fr_outer}")

    # CAMADA D3 (2026-05-25): pergunta claramente fora do escopo academico
    # (veterinaria, pets, esporte, etc) — escalar direto, NAO chamar LLM.
    # Caso Debora (11975717913): "exames veterinarios" virou resposta
    # alucinada do bot. Sem D3, o LLM responde de cabeca como especialista.
    try:
        _off, _off_kw = _is_off_scope_message(question)
        if _off:
            p(f"  [OFF-SCOPE-D3] pergunta fora do escopo (kw='{_off_kw}') -> escalando direto")
            _off_msg = (
                "Esse assunto eu não consigo te ajudar por aqui — vou te transferir "
                "para um de nossos consultores que vai te orientar melhor, tá? "
                "Um momentinho! 😊"
            )
            send_and_track(conv_id, _off_msg, force=True)
            conversation_messages.append({'role': 'bot', 'text': _off_msg})
            log_to_db(conv_id, question, _off_msg, 0.0, 'escalate_off_scope')
            try:
                if is_within_business_hours():
                    distribute_to_attendant(conv_id, f'Pergunta fora do escopo academico ({_off_kw})')
                else:
                    # fora do horario: registra escalation para amanha
                    try:
                        record_pending_escalation(conv_id, reason=f'off_scope:{_off_kw}',
                                                   tier='first', question=question)
                    except Exception:
                        pass
            except Exception as e_off:
                p(f"  [OFF-SCOPE-D3] erro distribute: {e_off}")
            waiting_for_client = False; inactivity_start = 0
            return
    except Exception as e_off_outer:
        p(f"  [OFF-SCOPE-D3] erro: {e_off_outer}")

    history = build_conversation_history(conv_id)
    clean, confidence, llm_time = call_llm(question, references, history, student_profile, memory, sentiment, is_first, image_b64=image_b64, image_mime=image_mime, image_desc=image_desc)

    p(f"  Resultado: conf={confidence:.2f} | top_sim={top_score:.3f}")
    p(f"  Resposta: {clean[:200]}...")

    # ACAO F (2026-05-21) + CAMADA D4 (2026-05-25): threshold de confianca
    # subiu de 0.30 para 0.40. Caso Debora (conf=0.30 sobre exames
    # veterinarios) passava por POUCO no antigo limite. Faixa <0.40 sempre
    # escala humano em vez de chutar resposta. Entre 0.40 e 0.50 ainda
    # responde mas com cuidado (botao falar com atendente eh adicionado
    # mais abaixo no fluxo padrao).
    if confidence < 0.40 and is_within_business_hours():
        # GUARD CRITICO: ANTES de escalate_low_conf, verificar se a msg eh
        # uma intenção CLARA que tem rota propria (resolvi / agradecimento /
        # pagamento ja feito / retencao). Se for, NUNCA usar a frase generica.
        # Caso reportado: aluno diz "Ja resolvi" e bot mandava
        # "Pra eu nao te passar informacao errada..." — agora cai em RESOLVED.
        try:
            _q_for_guard = (question or '').strip().lower()
            # 1) Variante mais ampla de RESOLVIDO
            if (any(w in _q_for_guard for w in RESOLVED_WORDS)
                    or 'resolvi' in _q_for_guard
                    or 'consegui' in _q_for_guard and 'consegui pagar' not in _q_for_guard):
                p(f"  [LOW-CONF-D4] cancelado: msg parece RESOLVIDO -> rota resolved")
                _msg_r = random.choice([
                    f"Que bom{name_suffix}! Fico feliz que tenha conseguido resolver 😊 Se precisar de mais alguma coisa, é só chamar!",
                    f"Show{name_suffix}! Qualquer coisa, é só me chamar. Até mais! 😊",
                    f"Maravilha{name_suffix}! Estou por aqui se precisar. Até logo! 😊",
                ])
                send_and_track(conv_id, _msg_r, force=True)
                conversation_messages.append({'role': 'bot', 'text': _msg_r})
                log_to_db(conv_id, question, _msg_r, 1.0, 'resolved')
                try:
                    close_conversation_crm(conv_id, phone=_current_phone)
                except Exception:
                    pass
                waiting_for_client = False; inactivity_start = 0
                return
            # 2) Despedida pura
            # (2026-06-01) GUARD: saudacao pura nunca encerra. Sem esse guard,
            # bug reportado: aluno volta apos dias com "Olá Oi" e o agente
            # responde "Foi otimo poder te ajudar!" + fecha.
            if not _is_pure_greeting(question) and _is_farewell_message(question):
                p(f"  [LOW-CONF-D4] cancelado: msg eh despedida -> closing")
                _msg_f = random.choice(_CLOSING_RESPONSES).format(name_suffix=name_suffix)
                send_and_track(conv_id, _msg_f, force=True)
                conversation_messages.append({'role': 'bot', 'text': _msg_f})
                log_to_db(conv_id, question, _msg_f, 1.0, 'closing')
                try:
                    close_conversation_crm(conv_id, phone=_current_phone)
                except Exception:
                    pass
                waiting_for_client = False; inactivity_start = 0
                return
            # 3) Pagamento ja confirmado
            if _is_payment_confirmed_message(question):
                p(f"  [LOW-CONF-D4] cancelado: msg confirma pagamento -> close payment")
                _msg_p = f"Tudo bem{name_suffix}! 😊 Obrigado pela confirmação. Qualquer coisa, é só me chamar. Até mais!"
                send_and_track(conv_id, _msg_p, force=True)
                conversation_messages.append({'role': 'bot', 'text': _msg_p})
                log_to_db(conv_id, question, _msg_p, 1.0, 'payment_confirmed')
                try:
                    close_conversation_crm(conv_id, phone=_current_phone)
                except Exception:
                    pass
                waiting_for_client = False; inactivity_start = 0
                return
            # 4) Retencao (cancelamento) -> time de Retenção (Wesley/Danúbia)
            if is_retention_intent(question):
                p(f"  [LOW-CONF-D4] cancelado: msg eh retencao -> time de Retenção")
                _fname = (student_profile.get('first_name') or '').strip() if student_profile else ''
                _greet = (f", *{_fname}*" if _fname else '')
                _msg_ret = (f"Entendi sua situação{_greet}. Vou te encaminhar para o nosso "
                            f"*time de Retenção*, que vai te ajudar com isso. Um momento, por favor! 😊")
                # (2026-06-25) modo automação RET-IA: não fala com o aluno (silêncio)
                if not _use_ret_ia_automation(_current_phone):
                    send_and_track(conv_id, _msg_ret, force=True)
                    conversation_messages.append({'role': 'bot', 'text': _msg_ret})
                    log_to_db(conv_id, question, _msg_ret, 1.0, 'retention')
                    _register_signature(conv_id, 'retention', _msg_ret)
                try:
                    lead_id_loc = student_profile.get('lead_id') if student_profile else None
                    # trigger_retention escolhe o membro ativo e marca o handoff (sticky)
                    trigger_retention(conv_id, lead_id_loc, question, phone=_current_phone)
                except Exception as e_lc_ret:
                    p(f"  [LOW-CONF-D4] erro trigger_retention: {e_lc_ret}")
                waiting_for_client = False; inactivity_start = 0
                return
        except Exception as e_guard:
            p(f"  [LOW-CONF-D4] erro guard pre-escala: {e_guard}")

        p(f"  [LOW-CONF-D4] conf={confidence:.2f} < 0.40 -> escalando direto")
        # FRASE NEUTRA — NUNCA usar "informacao errada" (decisao do time).
        # A mensagem antiga ("Pra eu nao te passar nenhuma informacao errada...")
        # ficava ruim e era usada em situacoes que tinham rota propria.
        _low_conf_msg = (
            "Vou te conectar com um de nossos consultores que vai te ajudar "
            "direitinho com isso, tá? Só um momento! 😊"
        )
        send_and_track(conv_id, _low_conf_msg, force=True)
        conversation_messages.append({'role': 'bot', 'text': _low_conf_msg})
        log_to_db(conv_id, question, _low_conf_msg, confidence, 'escalate_low_conf')
        try:
            distribute_to_attendant(conv_id, f'Baixa confianca da IA ({confidence:.2f})')
        except Exception as e_lc:
            p(f"  [LOW-CONF-D4] erro distribute: {e_lc}")
        waiting_for_client = False; inactivity_start = 0
        return

    if results and results[0][4]:
        try:
            media_list = json.loads(results[0][4])
            if isinstance(media_list, list):
                for idx, mi in enumerate(media_list):
                    caption = mi.get('caption', '')
                    if not caption:
                        mtype = mi.get('type', 'document').lower()
                        if mtype == 'video':
                            caption = 'Assista o tutorial:'
                        elif mtype == 'image':
                            caption = 'Veja a imagem:'
                        else:
                            caption = 'Confira o documento:'
                    if idx > 0:
                        time.sleep(0.5)
                    send_media_message(conv_id, mi, caption=caption)
                    p(f"    Midia enviada: {mi.get('filename', mi.get('url', ''))}")
        except Exception as e:
            p(f"    Erro ao enviar midias: {e}")

    status = send_and_track(conv_id, clean)
    p(f"  ENVIADO resposta (status {status})")

    _clean_lower = clean.lower()
    _asking_patterns = ('me conta', 'me fala', 'me diga', 'me explica', 'pode me contar',
                        'pode descrever', 'qual é o erro', 'qual o erro', 'qual mensagem',
                        'o que aparece', 'o que acontece', 'consegue enviar', 'consegue me enviar',
                        'tente novamente', 'já tentou', 'você pode', 'voce pode',
                        'assim consigo te ajudar', 'para eu te ajudar', 'pra eu te ajudar')
    _is_asking_question = (
        clean.rstrip().endswith('?') or
        clean.rstrip().rstrip('😊😉🤔').rstrip().endswith('?') or
        any(p_ask in _clean_lower for p_ask in _asking_patterns) or
        confidence < 0.40
    )
    if not _is_asking_question:
        _followup_opts = [
            "Consegui te ajudar com isso? Se tiver mais alguma dúvida é só falar! 😊",
            "Espero que tenha ajudado! Qualquer coisa, me chama aqui 😉",
            "Ficou tudo certo por aí? Se precisar de mais alguma coisa, estou aqui!",
            "Resolveu? Se tiver mais alguma dúvida, pode mandar! 😊",
        ]
        time.sleep(1)
        send_and_track(conv_id, random.choice(_followup_opts))
    else:
        p(f"  Resposta é pergunta/pede mais info (conf={confidence:.2f}) -> sem follow-up 'ficou alguma dúvida'")

    conversation_messages.append({'role': 'bot', 'text': clean})
    log_to_db(conv_id, question, clean, confidence, 'auto_reply')

    cur_phone = _current_phone or PHONE_TO_MONITOR
    try:
        tabulate_interaction(conversation_messages, student_profile, cur_phone, conv_id=conv_id)
    except Exception as e_tab:
        p(f"    Erro tabulação RAG: {e_tab}")

    waiting_for_client = True; inactivity_start = time.time()


def detect_topic_from_messages(messages):
    """Simple topic detection from conversation messages."""
    all_text = ' '.join([m['text'] for m in messages]).lower()
    topics = {
        'acesso': ['portal', 'login', 'senha', 'acesso', 'app', 'duda'],
        'financeiro': ['mensalidade', 'pagamento', 'boleto', 'financeiro'],
        'academico': ['aula', 'disciplina', 'nota', 'atividade', 'prova'],
        'matricula': ['matrícula', 'rematrícula', 'matricular'],
        'documentos': ['declaração', 'documento', 'certificado'],
    }
    scores = {}
    for topic, keywords in topics.items():
        scores[topic] = sum(1 for kw in keywords if kw in all_text)
    if scores:
        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best
    return 'outro'


# ===================== MAIN =====================

def _init_phone(phone):
    """Inicializa monitoramento de um telefone: busca conversas e marca msgs existentes."""
    global active_conv_id, student_profile

    r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                    params={'search': phone, 'limit': 5}, timeout=10)
    convs_data = r.json()
    convs = convs_data.get('data', convs_data) if isinstance(convs_data, dict) else convs_data
    if not isinstance(convs, list):
        convs = []

    for c in convs:
        cid = c.get('id', '')
        msgs = get_conversation_messages_api(cid, limit=20)
        for m in msgs:
            processed_msg_ids.add(m.get('id', ''))

    if convs:
        active_conv_id = convs[0].get('id', '')

    student_profile = identify_student(phone)
    memory = load_memory(phone)

    p(f"  Conversas: {len(convs)} | Msgs conhecidas: {len(processed_msg_ids)}")
    if student_profile:
        p(f"  Aluno: {student_profile['name']} | Tags: {student_profile['tags']}")
    if memory:
        p(f"  Memoria: {memory['interaction_count']} interacoes | Ultimo: {memory.get('last_topic', 'N/A')}")


def main():
    global active_conv_id, student_profile, followup_stage, waiting_for_client, inactivity_start, _last_auto_skipped
    global _awaiting_cpf, _student_in_base, _awaiting_polo_confirm, _current_phone

    load_agent_config_from_db()
    load_menus_from_db()

    p("")
    p("=" * 60)
    p("  AGENTE IA v4 - Identificacao + Memoria + Empatia + Tab")
    p(f"  Modo: MULTI-ATENDIMENTO (todas as conversas)")
    p(f"  Polling: {POLL_INTERVAL}s | Threshold: {CONFIDENCE_THRESHOLD}")
    p(f"  Follow-up: {FOLLOWUP_1_DELAY}s / Close: {CLOSE_DELAY}s")
    p("=" * 60)

    ensure_memory_tables()

    # CALENDARIO ACADEMICO (2026-05-25): garante tabela e popula seed na 1a
    # subida. Re-runs sao no-op (INSERT ON CONFLICT DO NOTHING).
    try:
        _seed_academic_calendar_if_empty()
    except Exception as _e_cal_seed:
        p(f"  [CAL] Falha no seed do calendario: {_e_cal_seed}")

    global _startup_ts
    _startup_ts = time.time()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM msg_dedup WHERE processed_at < NOW() - INTERVAL '5 minutes'")
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if deleted:
            p(f"  Dedup cleanup: {deleted} entradas antigas removidas")
    except Exception:
        pass
    p(f"  Startup: {time.strftime('%H:%M:%S')} (só processa mensagens novas a partir de agora)")

    p(f"")
    p(f"  >>> AGENTE v4 ATIVO <<<")
    p(f"")

    cycle = 0

    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent.lock')
    my_pid = os.getpid()

    def _kill_pid(pid):
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                               capture_output=True, timeout=5)
            else:
                os.kill(pid, 9)
            p(f"  Processo anterior (PID {pid}) encerrado.")
        except Exception:
            pass

    try:
        if os.name == 'nt':
            result = subprocess.run(
                ['wmic', 'process', 'where',
                 f"commandline like '%agente_ao_vivo_v4%' and processid != '{my_pid}'",
                 'get', 'processid'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if line.isdigit():
                    _kill_pid(int(line))
        else:
            result = subprocess.run(
                ['pgrep', '-f', 'agente_ao_vivo_v4'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if line.isdigit() and int(line) != my_pid:
                    _kill_pid(int(line))
    except Exception:
        pass

    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                old_pid = int(f.read().strip())
            if old_pid != my_pid:
                _kill_pid(old_pid)
        except (ProcessLookupError, ValueError, OSError):
            pass
    with open(lock_path, 'w') as f:
        f.write(str(my_pid))

    p(f"  Entrando no loop principal... (PID {os.getpid()})")
    _heartbeat('online', f'startup cycle=0')

    # === VARREDURA ÚNICA: mover alunos presos em Encerramento/outro pipeline de volta para Base de Alunos ===
    try:
        p(f"  [VARREDURA] Buscando conversas presas em outros pipelines...")
        _sweep_r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                                params={'limit': 80, 'status': 'unstarted'}, timeout=15)
        _sweep_convs = []
        if _sweep_r.status_code == 200:
            _sweep_data = _sweep_r.json()
            _sweep_convs = _sweep_data.get('data', _sweep_data) if isinstance(_sweep_data, dict) else _sweep_data
        _sweep_count = 0
        _MAX_SWEEP = 5
        for _sc in (_sweep_convs if isinstance(_sweep_convs, list) else []):
            if _sweep_count >= _MAX_SWEEP:
                break
            _s_inst = _sc.get('instance', {}) or {}
            _s_inst_id = _s_inst.get('id', '') if isinstance(_s_inst, dict) else str(_s_inst)
            if _s_inst_id != INSTANCE_ACADEMICO_ID:
                continue
            if _sc.get('attendants', []):
                continue
            _s_ct = _sc.get('contact', {}) or {}
            _s_ext = _s_ct.get('externalInfo', {}) or {}
            _s_pids = _s_ext.get('pipelineIds', []) or []
            if not _s_pids or PIPELINE_ALUNOS_ID in _s_pids:
                continue
            _s_recv = _sc.get('lastReceivedMessageDate', '') or ''
            _s_sent = _sc.get('lastSendedMessageDate', '') or ''
            if _s_recv and (_s_recv > _s_sent if _s_sent else True):
                _s_phone = _s_ct.get('phone', '') or _s_ct.get('number', '') or ''
                _s_name = _s_ct.get('name', '???')
                if _s_phone:
                    p(f"  [VARREDURA] {_s_name} (...{_s_phone[-4:]}) preso em outro pipeline -> movendo p/ Base de Alunos")
                    _move_business_to_base_alunos(_s_phone)
                    _sweep_count += 1
        # Também buscar conversas com status 'opened'
        try:
            _sweep_r2 = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                                     params={'limit': 80, 'status': 'opened'}, timeout=15)
            _sweep_convs2 = []
            if _sweep_r2.status_code == 200:
                _sweep_data2 = _sweep_r2.json()
                _sweep_convs2 = _sweep_data2.get('data', _sweep_data2) if isinstance(_sweep_data2, dict) else _sweep_data2
            for _sc2 in (_sweep_convs2 if isinstance(_sweep_convs2, list) else []):
                if _sweep_count >= _MAX_SWEEP:
                    break
                _s_inst2 = _sc2.get('instance', {}) or {}
                _s_inst_id2 = _s_inst2.get('id', '') if isinstance(_s_inst2, dict) else str(_s_inst2)
                if _s_inst_id2 != INSTANCE_ACADEMICO_ID:
                    continue
                if _sc2.get('attendants', []):
                    continue
                _s_ct2 = _sc2.get('contact', {}) or {}
                _s_ext2 = _s_ct2.get('externalInfo', {}) or {}
                _s_pids2 = _s_ext2.get('pipelineIds', []) or []
                if not _s_pids2 or PIPELINE_ALUNOS_ID in _s_pids2:
                    continue
                _s_recv2 = _sc2.get('lastReceivedMessageDate', '') or ''
                _s_sent2 = _sc2.get('lastSendedMessageDate', '') or ''
                if _s_recv2 and (_s_recv2 > _s_sent2 if _s_sent2 else True):
                    _s_phone2 = _s_ct2.get('phone', '') or _s_ct2.get('number', '') or ''
                    _s_name2 = _s_ct2.get('name', '???')
                    if _s_phone2:
                        p(f"  [VARREDURA] {_s_name2} (...{_s_phone2[-4:]}) preso em outro pipeline (opened) -> movendo p/ Base de Alunos")
                        _move_business_to_base_alunos(_s_phone2)
                        _sweep_count += 1
        except Exception as e_sw2:
            p(f"  [VARREDURA] Erro ao buscar opened: {e_sw2}")
        p(f"  [VARREDURA] Concluída! {_sweep_count} alunos movidos de volta para Base de Alunos")
    except Exception as e_sweep:
        p(f"  [VARREDURA] Erro na varredura: {e_sweep}")

    # === ONE-SHOT: correcao manual Vanessa Carmona (endereco Barra Funda errado) ===
    try:
        _oneshot_fix_vanessa_barra_funda()
    except Exception as e_one:
        p(f"  [ONESHOT-VANESSA] erro: {e_one}")

    _paused_logged_at = 0
    while True:
        try:
            time.sleep(POLL_INTERVAL)
            cycle += 1
            _cached_msgs.clear()
            maybe_reload()

            # === CHECAGEM DE FLAG: agente DESLIGADO via cockpit ===
            # Quando agent_runtime_enabled=false no banco, mantemos o processo
            # vivo (heartbeat) mas pulamos TODO o processamento: rescue, fila,
            # auto-dispatch, novas conversas. Reativacao e instantanea.
            if not _agent_runtime_enabled():
                try:
                    _heartbeat('paused', f'cycle={cycle} (DESLIGADO via cockpit)')
                except Exception:
                    pass
                # log uma vez por minuto para nao poluir
                if (time.time() - _paused_logged_at) > 60:
                    p(f"  [PAUSED] Agente desligado via cockpit (cycle={cycle}). Aguardando reativacao...")
                    _paused_logged_at = time.time()
                continue
            else:
                if _paused_logged_at:
                    p(f"  [RESUMED] Agente reativado via cockpit (cycle={cycle}).")
                    _paused_logged_at = 0

            try:
                _heartbeat('online', f'cycle={cycle} (loop start)')
            except Exception:
                pass

            if cycle % 3 == 0:
                try:
                    process_pending_escalation_auto_dispatch()
                except Exception as e_fila:
                    p(f"  [FILA] Erro no auto-dispatch: {e_fila}")
                # (2026-05-25) post-close-rescue agora roda a cada 3 ciclos
                try:
                    process_post_close_rescue()
                except Exception as e_pcr:
                    p(f"  [POST-CLOSE-RESCUE] Erro: {e_pcr}")
                # (2026-05-26) in_hours_rescue a cada 3 ciclos (era 10) — orfas
                # na fila 5-10min eram ignoradas por muito tempo.
                try:
                    process_in_hours_rescue()
                except Exception as e_ihr:
                    p(f"  [IN-HOURS-RESCUE] Erro: {e_ihr}")
                # (2026-06-01) DESATIVADO: process_inactive_attendant_rescue
                # Era agressivo demais — varria todas as conversas existentes e
                # tirava dos inativos, mesmo quando ja havia atendimento em
                # andamento. A regra correta eh: NAO mexer em conversa que ja
                # tem atendente; apenas EVITAR escolher inativo quando o
                # agente IA for distribuir UMA NOVA conv (isso ja eh feito
                # via get_available_consultant + filtro ativo_inativo=Ativo).

            if cycle % 10 == 0:
                try:
                    process_after_hours_rescue()
                except Exception as e_rescue:
                    p(f"  [AH-RESCUE] Erro: {e_rescue}")
                # (2026-06-30) (B) Backfill de RGM no painel do Disparador para os
                # 'Processos CAA_IA' sem RGM (throttle interno de 10 min). Cobre o
                # race (linha criada pelo disparador após a tag) e o passivo atual.
                try:
                    _ret_ia_backfill_rgm_disparador()
                except Exception as e_rgmbf:
                    p(f"  [RGM-BACKFILL] Erro: {e_rgmbf}")
                try:
                    process_supervisor_loop()
                except Exception as e_sup:
                    p(f"  [SUPERVISOR] Erro: {e_sup}")
                try:
                    process_openai_supervisor_loop()
                except Exception as e_osup:
                    p(f"  [OPENAI-SUP] Erro: {e_osup}")
                # (2026-06-17) Captura a 1a resposta humana do time de Retenção
                # para preencher a coluna RESPOSTA do feedback (tema='RETENÇÃO').
                try:
                    _capture_retention_responses()
                except Exception as e_rret:
                    p(f"  [RETENÇÃO-RESP] Erro: {e_rret}")

            if cycle % 2 == 0:
                active_count = sum(1 for s in _conv_states.values() if s.get('waiting_for_client'))
                if cycle % 10 == 0:
                    p(f"  ...ativo ({cycle * POLL_INTERVAL}s | {len(processed_msg_ids)} msgs | {len(_conv_states)} convs | {active_count} aguardando)")
                _heartbeat('online', f'cycle={cycle} convs={len(_conv_states)} active={active_count}')

            # Busca conversas ATIVAS recentes — funde open + unstarted + opened.
            # (2026-05-26) Antes era so 'status=open' — convs em 'unstarted'
            # (pos-disparo) e 'opened' (em fluxo automation DCZ) ficavam
            # invisiveis. Caso reportado: 10 alunos da imagem todos em
            # 'unstarted' por 2h sem o agente enxergar.
            # (2026-05-27) Heartbeats antes/depois do fetch para evitar que
            # o cockpit detecte processo morto durante DCZ lento.
            try:
                _heartbeat('online', f'cycle={cycle} fetching')
            except Exception:
                pass
            try:
                convs_raw = _fetch_active_conversations(
                    limit_per_status=300, timeout=15)
            except Exception as _e_conv:
                p(f"  [ERRO] Falha ao buscar conversas: {_e_conv}")
                continue
            try:
                _heartbeat('online', f'cycle={cycle} fetched={len(convs_raw) if convs_raw else 0}')
            except Exception:
                pass
            if not convs_raw:
                continue

            # ============================================================
            # FILTRO: Instância acadêmica + sem atendente humano + unstarted/hidden OU opened
            # (opened entra na mesma fila de processamento — ver merge em PRIORIDADE 2)
            # ============================================================
            _MAX_CONV_AGE_S = 72 * 3600
            _now_utc = time.time()
            convs_nao_iniciados = []
            convs_opened = []
            _other_instance = 0
            _other_pipeline = 0
            _finished_count = 0
            _too_old = 0
            _with_attendant = 0
            _no_pipeline = 0
            for _cf in convs_raw:
                _inst = _cf.get('instance', {}) or {}
                _inst_id = _inst.get('id', '') if isinstance(_inst, dict) else str(_inst)
                if _inst_id != INSTANCE_ACADEMICO_ID:
                    _other_instance += 1
                    continue
                _statuses = _cf.get('statuses', []) or []
                if 'finished' in _statuses:
                    _finished_count += 1
                    continue
                if _cf.get('attendants', []):
                    _with_attendant += 1
                    # (2026-06-25) Interceptador RET-IA: se a conversa caiu em
                    # Atendimento (menu/n8n) mas a última msg do aluno é retenção,
                    # aciona SÓ a automação (tag) — sem falar com o aluno nem
                    # remover o atendente. Regras de segurança no helper.
                    try:
                        _retention_intercept_for_attendant_conv(_cf)
                    except Exception:
                        pass
                    continue
                _ct = _cf.get('contact', {}) or {}
                _ext = _ct.get('externalInfo', {}) or {}
                _pids = _ext.get('pipelineIds', []) or []
                if not _pids:
                    _cf_recv = _cf.get('lastReceivedMessageDate', '') or ''
                    _cf_sent = _cf.get('lastSendedMessageDate', '') or ''
                    _has_new = bool(_cf_recv and (_cf_recv > _cf_sent if _cf_sent else True))
                    if _has_new:
                        _no_pipeline += 1
                    else:
                        _no_pipeline += 1
                        continue
                elif PIPELINE_ALUNOS_ID not in _pids:
                    # Conta fora da Base de Alunos, mas NÃO exclui: com bot por último precisa
                    # de follow-up/encerramento; excluir travava Priscila/Claudia em outro funil.
                    _other_pipeline += 1
                _last_recv = _cf.get('lastReceivedMessageDate', '') or _cf.get('lastMessageDate', '') or ''
                if _last_recv:
                    try:
                        from datetime import datetime as _dtf
                        _dt_recv = _dtf.fromisoformat(str(_last_recv).replace('Z', '+00:00'))
                        if (_now_utc - _dt_recv.timestamp()) > _MAX_CONV_AGE_S:
                            _too_old += 1
                            continue
                    except Exception:
                        pass
                if 'unstarted' in _statuses or 'hidden' in _statuses:
                    convs_nao_iniciados.append(_cf)
                elif 'opened' in _statuses:
                    convs_opened.append(_cf)
            if cycle <= 5 or cycle % 10 == 0:
                p(f"  [FILTRO] Total={len(convs_raw)} | OutraInst={_other_instance} | OutroPipe={_other_pipeline} | SemPipe={_no_pipeline} | Finished={_finished_count} | ComAtend={_with_attendant} | Antigas={_too_old} | NaoInic={len(convs_nao_iniciados)} | Opened={len(convs_opened)}")

            # ============================================================
            # PRIORIDADE 1: Encerrar follow-ups > 15 min (antes de tudo)
            # ============================================================
            _fu_close_count = 0
            _MAX_FU_CLOSE = 18
            _all_bot_last = list(convs_opened) + [c for c in convs_nao_iniciados
                if (c.get('lastSendedMessageDate','') or '') > (c.get('lastReceivedMessageDate','') or '')]
            for _fc in _all_bot_last:
                if _fu_close_count >= _MAX_FU_CLOSE:
                    break
                _fc_id = _fc.get('id', '')
                if not _fc_id:
                    continue
                _fc_sent = _fc.get('lastSendedMessageDate', '') or ''
                if not _fc_sent:
                    continue
                _lm_raw = _fc.get('lastMessage', '') or ''
                _lm_body = (_lm_raw.get('body', '') if isinstance(_lm_raw, dict) else str(_lm_raw)).lower()
                _is_fu = any(fp in _lm_body for fp in _FU_TRIGGER_PHRASES)
                _is_close = any(fp in _lm_body for fp in LAST_MSG_CLOSE_PHRASES)
                if not _is_fu and not _is_close:
                    continue
                try:
                    from datetime import datetime as _dtfu
                    _dt_fu = _dtfu.fromisoformat(str(_fc_sent).replace('Z', '+00:00'))
                    _fu_age_s = _now_utc - _dt_fu.timestamp()
                except Exception:
                    _fu_age_s = 0
                if _fu_age_s < 900:
                    continue
                _fc_ct = _fc.get('contact', {}) or {}
                _fc_phone = (_fc_ct.get('phoneNumber', '') or _fc_ct.get('phone', '') or '').replace('+','').replace(' ','')
                if _fc_phone.startswith('55') and len(_fc_phone) > 11:
                    _fc_phone = _fc_phone[2:]
                _fc_name = (_fc_ct.get('name', '') or '')[:20]
                p(f"  [PRIO-1] Encerrando follow-up antigo ({int(_fu_age_s/60)}min): {_fc_name} ...{_fc_phone[-4:] if _fc_phone else '????'}")
                close_conversation_crm(_fc_id, _fc_phone)
                _fu_close_count += 1
            if _fu_close_count > 0:
                p(f"  [PRIO-1] {_fu_close_count} conversas com follow-up antigo encerradas")

            # ============================================================
            # PRIORIDADE 2+3: Separar waiting vs rest, com "falar com atendente" primeiro
            # Inclui 'opened' e 'unstarted'/'hidden' — antes só unstarted ia para a fila e
            # conversas só em 'opened' ficavam sem resposta do agente.
            # ============================================================
            waiting_atendente = []
            waiting_normal = []
            rest = []
            _seen_queue = set()
            _convs_queue_source = []
            for c in convs_nao_iniciados + convs_opened:
                _qid = c.get('id', '')
                if not _qid or _qid in _seen_queue:
                    continue
                _seen_queue.add(_qid)
                _convs_queue_source.append(c)
            _ATENDENTE_KEYWORDS = ['falar com atendente', 'atendente', 'falar com humano',
                                   'quero falar com alguem', 'falar com pessoa']
            for c in _convs_queue_source:
                recv = c.get('lastReceivedMessageDate', '') or ''
                sent = c.get('lastSendedMessageDate', '') or ''
                if recv > sent:
                    _lm = c.get('lastMessage', '') or ''
                    _lm_body = (_lm.get('body', '') if isinstance(_lm, dict) else str(_lm)).lower().strip()
                    if any(kw in _lm_body for kw in _ATENDENTE_KEYWORDS):
                        waiting_atendente.append(c)
                    else:
                        waiting_normal.append(c)
                else:
                    rest.append(c)
            waiting_atendente.sort(key=lambda c: c.get('lastReceivedMessageDate', '') or '')
            waiting_normal.sort(key=lambda c: c.get('lastReceivedMessageDate', '') or '')
            rest.sort(key=lambda c: c.get('lastSendedMessageDate', '') or '')
            waiting = waiting_atendente + waiting_normal
            # (2026-05-25) Throughput por ciclo aumentado de 20 para 60.
            # Disparos em massa (300+ alunos) deixavam a fila acumulada por
            # 15-20 min. Com 60/ciclo (~5-8s/conv = 5-8min por ciclo),
            # ainda eh seguro contra timeout e cobre disparos grandes.
            _MAX_WAITING_PER_CYCLE = 60
            # (2026-05-27) Limite para 'rest' (bot falou por último — follow-up
            # tracking). Sem limite, ciclos com 1000+ convs travavam o heartbeat
            # por centenas de segundos. O follow-up de rest é leve (só atualiza
            # estado em memória), mas iterar 900 convs ainda demora.
            _MAX_REST_PER_CYCLE = 200
            convs = waiting[:_MAX_WAITING_PER_CYCLE] + rest[:_MAX_REST_PER_CYCLE]
            if waiting_atendente and (cycle <= 5 or cycle % 10 == 0):
                p(f"  [PRIO-2] {len(waiting_atendente)} conversas 'falar com atendente' -> distribuir primeiro")
            if cycle <= 5 or cycle % 10 == 0 or len(waiting) > 0:
                _oldest_info = ''
                if waiting:
                    _w0 = waiting[0]
                    _w0_ct = _w0.get('contact', {}) or {}
                    _w0_name = (_w0_ct.get('name', '') or '')[:15]
                    _w0_recv = _w0.get('lastReceivedMessageDate', '') or ''
                    if _w0_recv:
                        try:
                            from datetime import datetime as _dtq
                            _w0_dt = _dtq.fromisoformat(str(_w0_recv).replace('Z', '+00:00'))
                            _w0_age = int((time.time() - _w0_dt.timestamp()) / 60)
                            _oldest_info = f" | Mais antigo: {_w0_name} ({_w0_age}min)"
                        except Exception:
                            _oldest_info = f" | Mais antigo: {_w0_name}"
                p(f"  [QUEUE] waiting={len(waiting)} rest={len(rest)} -> processando {len(convs)}{_oldest_info}")

            # (2026-05-26) Varredura rapida ANTES do processamento normal:
            # fecha despedidas/pagamento/retencao na hora; desbloqueia dedup
            # de orfas >= 3min; cumpre handoffs pendentes (ex: Debora).
            try:
                process_queue_fast_sweep(waiting, _convs_queue_source)
            except Exception as e_qfs:
                p(f"  [QUEUE-SWEEP] Erro: {e_qfs}")
            try:
                process_handoff_fulfillment_sweep(_convs_queue_source)
            except Exception as e_hff:
                p(f"  [HANDOFF-FULFILL] Erro: {e_hff}")

            # === MONITORAR FOLLOW-UP: conversas onde agente respondeu E aluno NÃO respondeu ===
            _fu_candidates = list(convs_opened) + [c for c in rest if (c.get('lastSendedMessageDate','') or '') > (c.get('lastReceivedMessageDate','') or '')]
            _fu_tracked = 0
            for _oc in _fu_candidates:
                _oc_id = _oc.get('id', '')
                if not _oc_id:
                    continue
                if _oc_id in _conv_states and _conv_states[_oc_id].get('_human_took_over'):
                    continue
                # Disparo WhatsApp (HSM/template): não monitorar follow-up do agente
                _lm_raw = _oc.get('lastMessage', '') or ''
                if isinstance(_lm_raw, dict) and _is_template_message(_lm_raw):
                    _conv_states.setdefault(_oc_id, _default_conv_state())
                    _conv_states[_oc_id]['waiting_for_client'] = False
                    _conv_states[_oc_id]['followup_stage'] = 0
                    _conv_states[_oc_id]['inactivity_start'] = 0
                    _fu_tracked += 1
                    continue
                _oc_sent = _oc.get('lastSendedMessageDate', '') or ''
                _oc_recv = _oc.get('lastReceivedMessageDate', '') or ''
                if not _oc_sent:
                    continue
                if _oc_recv and _oc_recv > _oc_sent:
                    continue
                _oc_contact = _oc.get('contact', {}) or {}
                _oc_name = _oc_contact.get('name', '') or ''
                _oc_fname = _oc_name.split()[0] if _oc_name else ''
                _oc_phone = (_oc_contact.get('phoneNumber', '') or _oc_contact.get('contactId', '') or '').replace('+','').replace(' ','')
                _oc_start = time.time()
                try:
                    from datetime import datetime as _dtoc
                    _oc_dt = _dtoc.fromisoformat(str(_oc_sent).replace('Z', '+00:00'))
                    _oc_start = _oc_dt.timestamp()
                except Exception:
                    pass
                _conv_states.setdefault(_oc_id, _default_conv_state())
                _conv_states[_oc_id]['phone'] = _oc_phone
                _existing_profile = _conv_states[_oc_id].get('student_profile')
                if not _existing_profile:
                    _conv_states[_oc_id]['student_profile'] = {'name': _oc_name, 'first_name': _oc_fname} if _oc_name else None
                _conv_states[_oc_id]['waiting_for_client'] = True
                # Sempre alinhar ao último envio da API (re-sync a cada ciclo — evita estado preso)
                _conv_states[_oc_id]['inactivity_start'] = _oc_start
                _last_msg = (_lm_raw.get('body', '') if isinstance(_lm_raw, dict) else str(_lm_raw)).lower()
                _is_followup_msg = any(fp in _last_msg for fp in _FU_TRIGGER_PHRASES)
                _is_close_msg = any(fp in _last_msg for fp in LAST_MSG_CLOSE_PHRASES)
                _prev_stage = int(_conv_states[_oc_id].get('followup_stage', 0) or 0)
                if _is_close_msg:
                    _new_st = 2
                elif _is_followup_msg:
                    _new_st = 1
                else:
                    _new_st = 0
                # Não rebaixar estágio (ex.: FOLLOWUP-SKIP pôs 1 mas o texto da última msg não bate com L2)
                _conv_states[_oc_id]['followup_stage'] = max(_prev_stage, _new_st)
                _fu_tracked += 1
            def _get_last_msg_body(c):
                lm = c.get('lastMessage', '') or ''
                return (lm.get('body', '') if isinstance(lm, dict) else str(lm)).lower()
            def _is_fu(c):
                b = _get_last_msg_body(c)
                return any(fp in b for fp in _FU_TRIGGER_PHRASES)
            _fu_with_followup = sum(1 for c in _fu_candidates if _is_fu(c))
            if cycle <= 3 and _fu_tracked > 0:
                p(f"  [FOLLOW-UP] {_fu_tracked} monitoradas | {_fu_with_followup} ja com follow-up (auto-close direto)")

            _conv_processed = 0
            _conv_skipped_human = 0
            _conv_no_msg = 0
            _conv_responded = 0
            _is_waiting_set = set(id(c) for c in waiting)
            for conv in convs:
              try:
                conv_id = conv.get('id', '')
                if not conv_id:
                    continue

                _in_waiting = id(conv) in _is_waiting_set
                _conv_processed += 1

                # Heartbeat a cada 10 convs — impede cockpit de marcar como
                # morto durante ciclos longos (1000+ convs sem atualizar hb).
                if _conv_processed % 10 == 0:
                    try:
                        _heartbeat('online', f'cycle={cycle} proc={_conv_processed}/{len(convs)}')
                    except Exception:
                        pass

                # Extrair telefone do contato desta conversa
                contact = conv.get('contact', {}) or {}
                conv_phone = (contact.get('phoneNumber', '') or contact.get('contactId', '') or
                              contact.get('rawPhone', '') or contact.get('phone', '') or
                              contact.get('number', '') or '')
                if not conv_phone:
                    lead_info = conv.get('lead', {}) or {}
                    conv_phone = (lead_info.get('phoneNumber', '') or lead_info.get('rawPhone', '') or
                                  lead_info.get('phone', '') or '')
                if not conv_phone:
                    conv_phone = (conv.get('contactPhone', '') or conv.get('phone', '') or
                                  conv.get('number', '') or conv.get('from', '') or '')
                conv_phone = str(conv_phone).replace('+', '').replace(' ', '').replace('-', '')
                if conv_phone.startswith('55') and len(conv_phone) > 11:
                    conv_phone = conv_phone[2:]

                # Carregar estado da conversa
                _load_conv_state(conv_id)
                if _current_phone == '' and conv_phone:
                    _current_phone = conv_phone
                    _conv_states.setdefault(conv_id, _default_conv_state())['phone'] = conv_phone

                # Se a conversa já tem atendente humano atribuído, ignorar
                conv_attendants = conv.get('attendants', [])
                if conv_attendants:
                    _conv_states.setdefault(conv_id, _default_conv_state())['_human_took_over'] = True
                    _conv_skipped_human += 1
                    continue

                # Se consultor humano já assumiu nesta sessão, não processar
                st = _conv_states.get(conv_id, {})
                if st.get('_human_took_over'):
                    _conv_skipped_human += 1
                    continue

                # Para conversas WAITING: limpar dedup apenas se o bot NÃO respondeu depois
                if _in_waiting:
                    _lr = _conv_states.get(conv_id, {}).get('_last_responded_ts', 0)
                    if _lr and (time.time() - _lr) < 60:
                        _conv_no_msg += 1
                        continue
                    try:
                        _pre_msgs = _cached_msgs.get(conv_id) or get_conversation_messages_api(conv_id, limit=10)
                        _cached_msgs[conv_id] = _pre_msgs
                        _latest_recv = None
                        _latest_recv_ts = ''
                        _latest_out_ts = ''
                        for m in _pre_msgs:
                            if not _message_has_thread_payload(m):
                                continue
                            ts_ck = m.get('createdAt', '') or m.get('timestamp', '') or ''
                            if m.get('received', False):
                                if not _latest_recv:
                                    _latest_recv = m.get('id')
                                    _latest_recv_ts = ts_ck
                            else:
                                if not _latest_out_ts:
                                    _latest_out_ts = ts_ck
                        _bot_already_replied = bool(_latest_out_ts and _latest_recv_ts and _latest_out_ts >= _latest_recv_ts)
                        if _bot_already_replied:
                            if _latest_recv:
                                processed_msg_ids.add(_latest_recv)
                            _conv_no_msg += 1
                            continue
                        if _latest_recv and _latest_recv not in processed_msg_ids:
                            conn = get_db()
                            cur = conn.cursor()
                            cur.execute("DELETE FROM msg_dedup WHERE msg_id = %s", (_latest_recv,))
                            _pre_cleared = cur.rowcount
                            conn.commit()
                            cur.close()
                            conn.close()
                            if _pre_cleared > 0:
                                processed_msg_ids.discard(_latest_recv)
                    except Exception:
                        pass

                try:
                    msg_id, msg_body, is_click, img_info = get_new_client_message(conv_id, force=_in_waiting)
                except Exception:
                    msg_id = msg_body = is_click = img_info = None

                # Detectar se consultor humano enviou mensagem nesta conversa
                if _check_human_took_over(conv_id):
                    p(f"  [HUMAN] [{conv_phone[-4:] if conv_phone else '????'}] Consultor humano detectado -> agente recuando")
                    _conv_states.setdefault(conv_id, _default_conv_state())['_human_took_over'] = True
                    if msg_id:
                        processed_msg_ids.add(msg_id)
                    _save_conv_state(conv_id)
                    _conv_skipped_human += 1
                    continue

                # (2026-05-26) Re-check com fetch FRESCO antes de chamar
                # handle_message. Caso reportado (Misael/Danubia 11:08):
                # bot distribuiu pra Danubia, ela respondeu, e o bot ainda
                # processou e enviou mais msgs. O conv['attendants'] do
                # query inicial estava stale. Aqui pegamos o estado real
                # das ultimas 30min via outgoing-attendant nas msgs.
                try:
                    _h_fresh, _h_who_fresh = _human_attendant_active_recently(conv_id, window_s=30 * 60)
                    if _h_fresh:
                        p(f"  [HUMAN-FRESH] [{conv_phone[-4:] if conv_phone else '????'}] {_h_who_fresh} ativo nos ultimos 30min — agente recuando")
                        _conv_states.setdefault(conv_id, _default_conv_state())['_human_took_over'] = True
                        if msg_id:
                            processed_msg_ids.add(msg_id)
                        _save_conv_state(conv_id)
                        _conv_skipped_human += 1
                        continue
                except Exception as e_fresh:
                    p(f"  [HUMAN-FRESH] erro check: {e_fresh}")

                if msg_id and msg_body:
                    _lr = _conv_states.get(conv_id, {}).get('_last_responded_ts', 0)
                    if _lr and (time.time() - _lr) < 60:
                        _conv_no_msg += 1
                        continue
                    if not _current_phone and conv_phone:
                        _current_phone = conv_phone
                    p(f"  >>> MSG [{conv_phone[-4:] if conv_phone else '????'}]: \"{msg_body[:80]}\"{' [+IMG]' if img_info else ''}")
                    handle_message(conv_id, msg_id, msg_body, is_click, image_info=img_info)
                    _conv_states.setdefault(conv_id, _default_conv_state())['_last_responded_ts'] = time.time()
                    _save_conv_state(conv_id)
                    _conv_responded += 1
                else:
                    _conv_no_msg += 1
                    if _in_waiting:
                        _ct_name = contact.get('name', '') or ''
                        p(f"  [WAITING-SEM-MSG] [{conv_phone[-4:] if conv_phone else '????'}] {_ct_name[:20]} - nenhuma mensagem nova encontrada")
              except Exception as conv_err:
                p(f"  ERR conv {conv.get('id','?')[:12]}: {type(conv_err).__name__}: {conv_err}")
                sys.stdout.flush()
            if cycle <= 10 or cycle % 10 == 0:
                p(f"  [CICLO-{cycle}] Processadas={_conv_processed} | Respondidas={_conv_responded} | Sem msg={_conv_no_msg} | Skip humano={_conv_skipped_human}")

            # === VARREDURA: respostas simples a templates/disparos WhatsApp -> encerrar ===
            _TEMPLATE_ACK_WORDS = {
                'ok', 'okay', 'tudo bem', 'tá', 'ta', 'beleza', 'certo', 'entendi',
                'vou pagar', 'vou ver', 'vou verificar', 'pode deixar', 'blz',
                'combinado', 'anotado', 'recebi', 'sim', 's', 'tá joia', 'ta joia',
                'obrigado', 'obrigada', 'valeu', 'vlw', 'brigado', 'brigada',
                'tá joia obg', 'ta joia obg', 'tá jóia obg',
            }
            _tpl_closed = 0
            for _tpl_conv in waiting:
                if _tpl_closed >= 3:
                    break
                _tpl_cid = _tpl_conv.get('id', '')
                if not _tpl_cid:
                    continue
                _tpl_ct = _tpl_conv.get('contact', {}) or {}
                _tpl_phone = (str(_tpl_ct.get('phoneNumber', '') or _tpl_ct.get('contactId', '') or '')
                              .replace('+', '').replace(' ', '').replace('-', ''))
                _tpl_name = (_tpl_ct.get('name', '') or '').split()[0] if _tpl_ct.get('name') else ''
                try:
                    _tpl_msgs = get_conversation_messages_api(_tpl_cid, limit=5)
                    _cached_msgs[_tpl_cid] = _tpl_msgs
                    if not _tpl_msgs:
                        continue
                    _tpl_last_recv_msg = None
                    _tpl_last_out_msg = None
                    for _tm in _tpl_msgs:
                        if _tm.get('received', False) and _tpl_last_recv_msg is None:
                            _tpl_last_recv_msg = _tm
                        if not _tm.get('received', True) and _tpl_last_out_msg is None:
                            _tpl_last_out_msg = _tm
                        if _tpl_last_recv_msg and _tpl_last_out_msg:
                            break
                    if not _tpl_last_recv_msg or not _tpl_last_out_msg:
                        continue
                    if not _is_template_message(_tpl_last_out_msg):
                        continue
                    _tpl_body = (_tpl_last_recv_msg.get('body', '') or '').strip().lower().rstrip('!?.,').strip()
                    if _tpl_body not in _TEMPLATE_ACK_WORDS and not any(w in _tpl_body for w in ('obrigad', 'valeu', 'vlw', 'brigad')):
                        continue
                    _tpl_name_sfx = f", {_tpl_name}" if _tpl_name else ""
                    _tpl_resp = f"Tudo certo{_tpl_name_sfx}! Qualquer dúvida é só nos chamar. 😊"
                    p(f"  [TPL-ACK] [{_tpl_phone[-4:] if _tpl_phone else '????'}] '{_tpl_body}' -> encerrando")
                    send_message_crm(_tpl_cid, _tpl_resp)
                    close_conversation_crm(_tpl_cid, phone=_tpl_phone)
                    if _tpl_last_recv_msg.get('id'):
                        processed_msg_ids.add(_tpl_last_recv_msg['id'])
                    _conv_states.setdefault(_tpl_cid, _default_conv_state())['_human_took_over'] = True
                    _conv_states[_tpl_cid]['waiting_for_client'] = False
                    _save_conv_state(_tpl_cid)
                    _tpl_closed += 1
                    try:
                        log_to_db(_tpl_cid, _tpl_body, _tpl_resp, 1.0, 'template_ack_close_sweep')
                    except Exception:
                        pass
                except Exception as _tpl_err:
                    p(f"  [TPL-ACK] Erro: {_tpl_err}")
            if _tpl_closed > 0:
                p(f"  [TPL-ACK] Encerradas {_tpl_closed} conversas de resposta a template neste ciclo")

            # === FOLLOW-UP & ENCERRAMENTO POR INATIVIDADE (para TODAS conversas) ===
            _closes_this_cycle = 0
            _MAX_CLOSES_PER_CYCLE = 20
            for cid, st in list(_conv_states.items()):
                if st.get('_human_took_over'):
                    continue
                if st.get('waiting_for_client') and st.get('inactivity_start', 0) > 0:
                    elapsed = time.time() - st['inactivity_start']
                    sp = st.get('student_profile')
                    name_fmt = f", {sp['first_name']}" if sp and sp.get('first_name') else ""
                    cur_phone = st.get('phone', '')

                    if st.get('followup_stage', 0) == 2:
                        if _closes_this_cycle >= _MAX_CLOSES_PER_CYCLE:
                            continue
                        # (2026-06-30) Rede de segurança: não finalizar conversa em RETENÇÃO.
                        try:
                            _ho_dc, _ = _is_handoff_active(cid)
                            if _ho_dc:
                                p(f"  [DIRECT-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] handoff_active={_ho_dc} - skip")
                                continue
                            _rmsgs_dc = _cached_msgs.get(cid) or get_conversation_messages_api(cid, limit=15)
                            if _rmsgs_dc and _is_in_retention(cid, msgs=_rmsgs_dc):
                                p(f"  [DIRECT-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] EM RETENÇÃO - nao encerra")
                                continue
                        except Exception:
                            pass
                        p(f"  [DIRECT-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] Msg encerramento ja enviada ({int(elapsed)}s) -> finalizando")
                        _closes_this_cycle += 1
                        close_conversation_crm(cid, phone=cur_phone)
                        st['conversation_messages'] = []
                        conversation_greeted.discard(cid)
                        st['waiting_for_client'] = False
                        st['followup_stage'] = 0
                        st['inactivity_start'] = 0
                        p(f"  [DIRECT-CLOSE] Conversa finalizada")
                        continue

                    if st.get('followup_stage', 0) == 0 and elapsed >= FOLLOWUP_1_DELAY:
                        # Muitas horas sem 1o follow-up (ex.: agente parado): não mandar ping tardio; ir ao encerramento
                        if elapsed >= 28800:
                            st['followup_stage'] = 1
                            p(f"  [FOLLOWUP-SKIP] [{cur_phone[-4:] if cur_phone else '????'}] {int(elapsed)}s -> pulando 1o follow-up (muito tempo); encerramento via estágio 1")
                            continue
                        try:
                            r_fu_check = requests.get(
                                f'{DCZ_MSG}/messaging/conversations/{cid}',
                                headers=H, timeout=10)
                            if r_fu_check.status_code == 200:
                                fu_data = r_fu_check.json()
                                _tlm = fu_data.get('lastMessage') or {}
                                if isinstance(_tlm, dict) and _is_template_message(_tlm):
                                    p(f"  [FOLLOWUP-1] [{cur_phone[-4:] if cur_phone else '????'}] Última msg é template/HSM (disparo) -> sem follow-up")
                                    st['waiting_for_client'] = False
                                    st['followup_stage'] = 0
                                    st['inactivity_start'] = 0
                                    continue
                                if elapsed <= 3600 and fu_data.get('attendants', []):
                                    p(f"  [FOLLOWUP-1] [{cur_phone[-4:] if cur_phone else '????'}] Atendente presente -> cancelando follow-up")
                                    st['_human_took_over'] = True
                                    st['waiting_for_client'] = False
                                    continue
                        except Exception:
                            pass
                        # ACAO C (2026-05-21): NAO enviar follow-up se handoff
                        # ativo (consultor humano prometido aguardando).
                        try:
                            ho_motivo_fu_main, _ = _is_handoff_active(cid)
                            if ho_motivo_fu_main:
                                p(f"  [FOLLOWUP-1] [{cur_phone[-4:] if cur_phone else '????'}] handoff_active={ho_motivo_fu_main} - skip")
                                st['followup_stage'] = 1
                                st['inactivity_start'] = time.time()
                                continue
                        except Exception:
                            pass
                        # ACAO B (2026-05-21): dedup ANTES do envio. Bug:
                        # main loop nao tinha signature dedup (so o supervisor
                        # tinha). Resultado: 'Ainda esta por ai?' enviado 2x
                        # quando ambos loops disparavam proximos. 191 casos
                        # de 'repeticao' flagrados pelo supervisor IA.
                        try:
                            if _signature_recently_sent(cid, 'followup_1', window_s=2 * 3600):
                                p(f"  [FOLLOWUP-1] [{cur_phone[-4:] if cur_phone else '????'}] dedup signature - skip")
                                st['followup_stage'] = 1
                                st['inactivity_start'] = time.time()
                                continue
                        except Exception:
                            pass
                        msg1 = FOLLOWUP_1_MSG.format(name=name_fmt)
                        p(f"  [FOLLOWUP-1] [{cur_phone[-4:] if cur_phone else '????'}] {int(elapsed)}s sem resposta")
                        send_message_crm(cid, msg1, buttons=FOLLOWUP_1_BUTTONS)
                        log_to_db(cid, '(inatividade)', msg1, 1.0, 'followup_1')
                        try:
                            _register_signature(cid, 'followup_1', msg1)
                        except Exception:
                            pass
                        st['followup_stage'] = 1
                        st['inactivity_start'] = time.time()

                    elif st.get('followup_stage', 0) == 1 and elapsed >= CLOSE_DELAY:
                        _safe_to_close = False
                        # ACAO E (2026-05-21): SEMPRE verificar recv_ts > sent_ts
                        # antes de fechar (mesmo apos 1h). Bug: 35 casos de
                        # 'perdido_conversa' porque shortcut de >=3600s pulava
                        # esse check e fechava conv com pergunta nova do aluno.
                        try:
                            r_check = requests.get(
                                f'{DCZ_MSG}/messaging/conversations/{cid}',
                                headers=H, timeout=10)
                            if r_check.status_code == 200:
                                conv_data = r_check.json()
                                if conv_data.get('attendants', []):
                                    p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] Atendente presente -> cancelando close")
                                    st['_human_took_over'] = True
                                    st['waiting_for_client'] = False
                                    continue
                                recv_ts = conv_data.get('lastReceivedMessageDate', '') or ''
                                sent_ts = conv_data.get('lastSendedMessageDate', '') or ''
                                if recv_ts and sent_ts and recv_ts > sent_ts:
                                    p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] Aluno respondeu depois do follow-up -> cancelando close (reset stage)")
                                    st['waiting_for_client'] = False
                                    st['inactivity_start'] = 0
                                    st['followup_stage'] = 0
                                    continue
                                _safe_to_close = True
                            else:
                                p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] API retornou {r_check.status_code} -> adiando close")
                        except Exception as e_check:
                            p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] Erro ao verificar API: {e_check} -> adiando close")
                        if not _safe_to_close and elapsed >= 2700:
                            _safe_to_close = True
                            p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] fallback 45min+ pós-follow-up (API incerta)")
                        if not _safe_to_close:
                            continue
                        if _closes_this_cycle >= _MAX_CLOSES_PER_CYCLE:
                            continue
                        # ACAO C (2026-05-21): NAO encerrar se handoff vigente.
                        # Bug: bot encerrava antes do humano prometido responder.
                        try:
                            ho_motivo_cl_main, _ = _is_handoff_active(cid)
                            if ho_motivo_cl_main:
                                p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] handoff_active={ho_motivo_cl_main} - skip")
                                continue
                        except Exception:
                            pass
                        # (2026-06-30) Rede de segurança: NUNCA encerrar conversa em
                        # RETENÇÃO. O handoff pode ter expirado (TTL); confirma via
                        # histórico recente (aluno pediu cancelar/trancar nos últimos
                        # dias). Caso Maria Clara: fechada após handoff expirar.
                        try:
                            _rmsgs_cl = _cached_msgs.get(cid) or get_conversation_messages_api(cid, limit=15)
                            if _rmsgs_cl and _is_in_retention(cid, msgs=_rmsgs_cl):
                                p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] EM RETENÇÃO - nao encerra")
                                continue
                        except Exception:
                            pass
                        # ACAO B (2026-05-21): dedup ANTES do envio. Bug: mensagem
                        # de encerramento enviada 2x. Se signature ja registrada,
                        # so finaliza conv sem reenviar msg.
                        try:
                            if _signature_recently_sent(cid, 'auto_close', window_s=2 * 3600):
                                p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] dedup signature - so finaliza conv")
                                close_conversation_crm(cid, phone=cur_phone)
                                st['conversation_messages'] = []
                                conversation_greeted.discard(cid)
                                st['waiting_for_client'] = False
                                st['followup_stage'] = 0
                                st['inactivity_start'] = 0
                                continue
                        except Exception:
                            pass
                        close_msg = CLOSE_INACTIVITY_MSG.format(name=name_fmt)
                        p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] {int(elapsed)}s -> encerrando")
                        _closes_this_cycle += 1
                        msgs_list = st.get('conversation_messages', [])
                        if msgs_list:
                            try:
                                summary = generate_conversation_summary(msgs_list)
                                topic = detect_topic_from_messages(msgs_list)
                                save_memory(cur_phone, sp, topic, summary, 'neutro')
                            except Exception as e:
                                p(f"  Erro ao salvar antes de fechar: {e}")
                        send_message_crm(cid, close_msg, buttons=CLOSE_INACTIVITY_BUTTONS)
                        log_to_db(cid, '(inatividade)', close_msg, 1.0, 'auto_close')
                        try:
                            _register_signature(cid, 'auto_close', close_msg)
                        except Exception:
                            pass
                        close_conversation_crm(cid, phone=cur_phone)
                        st['conversation_messages'] = []
                        conversation_greeted.discard(cid)
                        st['waiting_for_client'] = False
                        st['followup_stage'] = 0
                        st['inactivity_start'] = 0
                        p(f"  [AUTO-CLOSE] Conversa encerrada e estado resetado")

            # === VARREDURA PROFUNDA A CADA ~5 MIN: limpar dedup antigos (NÃO limpar processed_msg_ids global) ===
            if cycle % 100 == 0 and len(waiting) > 0:
                _deep_cleared = 0
                try:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT msg_id FROM msg_dedup WHERE processed_at < NOW() - INTERVAL '10 minutes'")
                    _old_ids = [row[0] for row in cur.fetchall()]
                    if _old_ids:
                        cur.execute("DELETE FROM msg_dedup WHERE msg_id = ANY(%s)", (_old_ids,))
                        _deep_cleared = cur.rowcount
                    conn.commit()
                    cur.close()
                    conn.close()
                    if _deep_cleared:
                        p(f"  [DEEP-SCAN] Limpeza: {_deep_cleared} dedup >10min removidos (processed_msg_ids preservado)")
                except Exception as e_deep:
                    p(f"  [DEEP-SCAN] Erro: {e_deep}")

            if cycle % 2 == 0:
                active_count = sum(1 for s in _conv_states.values() if s.get('waiting_for_client'))
                if cycle % 10 == 0:
                    p(f"  ...ativo ({cycle * POLL_INTERVAL}s | {len(processed_msg_ids)} msgs | {len(_conv_states)} convs | {active_count} aguardando)")
                _heartbeat('online', f'cycle={cycle} convs={len(_conv_states)} active={active_count}')
            if cycle % 120 == 0:
                _db_cleanup_dedup()
                # Limpar estados de conversas inativas há mais de 1h
                cutoff = time.time() - 3600
                stale = [k for k, v in _conv_states.items()
                         if v.get('inactivity_start', 0) > 0 and v['inactivity_start'] < cutoff
                         and not v.get('waiting_for_client')]
                for k in stale:
                    del _conv_states[k]
                if stale:
                    p(f"  [CLEANUP] {len(stale)} conversas antigas removidas do estado")

        except KeyboardInterrupt:
            p("\n  Agente encerrado.")
            _heartbeat('offline', 'shutdown')
            break
        except BaseException as e:
            import traceback
            p(f"  FATAL: {type(e).__name__}: {e}")
            p(traceback.format_exc())
            sys.stdout.flush()
            if isinstance(e, (SystemExit, KeyboardInterrupt)):
                break
            time.sleep(5)
    
    try:
        os.remove(lock_path)
    except OSError:
        pass


if __name__ == '__main__':
    main()
