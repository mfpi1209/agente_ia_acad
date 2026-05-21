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
H = {'Authorization': f'Bearer {DCZ_TOKEN}', 'Content-Type': 'application/json'}

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
    """
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
    Se polo nao foi identificado, pergunta qual polo."""
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

INICIO_AULAS_MSG = (
    "Sobre o início das aulas, deixa eu te explicar 😊\n\n"
    "Quem está se matriculando *agora* em graduação ingressa na "
    "*turma do 2º semestre*, então as aulas começam em *agosto*.\n\n"
    "Se precisar de mais informações sobre cronograma ou calendário "
    "acadêmico, posso te transferir pra um(a) consultor(a) — é só me avisar!"
)


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


def handle_inicio_aulas_intent(conv_id, question=''):
    """Envia a resposta canonica sobre inicio das aulas (turma de agosto).
    Dedup de 6h. Bloqueia o LLM de parafrasear/inventar informacao errada."""
    sig = 'inicio_aulas_canonical'
    if _signature_recently_sent(conv_id, sig, window_s=6 * 3600):
        p(f"  [INICIO-AULAS] dedup: ja enviado nas ultimas 6h - suprimindo")
        return True
    try:
        meta_typing_on()
        sent_ok = send_and_track(conv_id, INICIO_AULAS_MSG)
        if sent_ok:
            log_to_db(conv_id, question or '', INICIO_AULAS_MSG, 1.0, sig)
            _register_signature(conv_id, sig, INICIO_AULAS_MSG)
            p(f"  [INICIO-AULAS] resposta canonica enviada (turma de agosto)")
        return sent_ok
    except Exception as e:
        p(f"  [INICIO-AULAS] erro: {e}")
        return False


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

# ===================== HORÁRIO DE ATENDIMENTO (sobrescrito por agent_config) =====================
BUSINESS_HOURS_WEEKDAY_START = 9   # Seg-Sex início
BUSINESS_HOURS_WEEKDAY_END = 20    # Seg-Sex fim (exclusivo)
BUSINESS_HOURS_SATURDAY_START = 9
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
    "📅 *Segunda a Sexta*: 09h às 20h\n"
    "📅 *Sábado*: 09h às 13h\n\n"
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
    "Para esse assunto, quem cuida com carinho é o *Wesley*, nosso consultor especializado.\n\n"
    "No momento ele está fora do horário de atendimento, mas assim que retomar *{retorno_label}* "
    "ele entra em contato com você por aqui mesmo, tá? 😊\n\n"
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

RESOLVED_WORDS = {'sim resolveu', 'resolveu', 'resolveu!', 'sim obrigado', 'sim obrigada', 'resolvido', 'era isso', 'ajudou', 'ajudou!'}
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
12. **INÍCIO DAS AULAS — REGRA CRÍTICA**: Quem se matricular AGORA em graduação ingressa na **turma do 2º semestre (agosto)**. NUNCA diga que as aulas começam em "fevereiro" ou "janeiro" para alunos novos/matriculados agora — isso é informação ERRADA. Se o aluno perguntar quando as aulas começam (perguntas tipo "quando começa", "quando inicia", "em que mês", "vou começar em agosto?", "fevereiro?"), responda que para quem está se matriculando agora as aulas iniciam em **agosto** (2º semestre). Se houver dúvida sobre cronograma detalhado ou calendário acadêmico, transfira para consultor.

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

Lá na tela de login, clica em *Esqueci minha senha*. Vai pedir seu CPF e e-mail cadastrado, aí você recebe um link pra criar uma senha nova.

Se o e-mail não chegar, dá uma olhada no spam. Tô por aqui se precisar de mais alguma coisa!"

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

        _ACAD_COLS = "nome, curso_limpo, serie, polo_aulas, situacao, tipo_matricula, email_ad, ano_tri_ingresso, tipo, curso_raw"
        _ACAD_KEYS = ['nome', 'curso', 'serie', 'polo', 'situacao', 'tipo_matricula',
                      'email_academico', 'ciclo', 'nivel', 'curso_raw']

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
    """Cria lead e negócio no DataCrazy (para alunos não encontrados que dizem ser alunos)."""
    try:
        clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        r = requests.post(f'{DCZ_CRM}/leads', headers=H,
                         json={'phone': clean_phone, 'name': name or clean_phone}, timeout=10)
        if r.status_code not in (200, 201):
            p(f"    Criar lead falhou: {r.status_code}")
            return None, None
        lead_data = r.json()
        new_lead_id = lead_data.get('id', '')
        p(f"    Lead criado: {new_lead_id}")

        r_biz = requests.post(f'{DCZ_CRM}/businesses', headers=H,
                             json={'leadId': new_lead_id, 'stageId': STAGE_BASE_ALUNOS_ID}, timeout=10)
        biz_id = ''
        if r_biz.status_code in (200, 201):
            biz_data = r_biz.json()
            biz_id = biz_data.get('id', '')
            p(f"    Business criado: {biz_id}")
        return new_lead_id, biz_id
    except Exception as e:
        p(f"    Erro criar lead/business: {e}")
        return None, None


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
{{"tema":"ACESSO_PORTAL|FINANCEIRO|ACADEMICO|MATRICULA|DOCUMENTOS|OUTRO","subtema":"descricao curta","sentimento":"satisfeito|neutro|frustrado|irritado","resolvido":"sim|nao|parcial|escalado","nps":7}}

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
    """Move business para Encerramento e finaliza a conversa no DataCrazy."""
    biz_ok = False
    if phone:
        biz_ok = _move_business_to_encerramento(phone)
    else:
        p(f"  [CLOSE] Telefone vazio, business NÃO será movido para Encerramento")

    time.sleep(2)

    fin_ok = False
    try:
        r = requests.post(
            f'{DCZ_API}/api/v1/conversations/{conv_id}/finish',
            headers=H, json={}, timeout=15
        )
        p(f"  [CLOSE] Finish via DCZ_API (status={r.status_code})")
        if r.status_code in (200, 201, 204):
            fin_ok = True
    except Exception as e:
        p(f"  [CLOSE] Erro DCZ_API finish: {e}")

    if not fin_ok:
        try:
            r2 = requests.post(
                f'{DCZ_MSG}/messaging/conversations/{conv_id}/finish',
                headers=H, json={}, timeout=15
            )
            p(f"  [CLOSE] Finish via DCZ_MSG fallback (status={r2.status_code})")
            if r2.status_code in (200, 201, 204):
                fin_ok = True
        except Exception as e2:
            p(f"  [CLOSE] Erro DCZ_MSG fallback: {e2}")

    p(f"  [CLOSE] Conv {conv_id[:16]} -> biz_encerr={biz_ok} | finish={fin_ok}")
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
    """Envia mensagens de outro polo, move para Perdido e finaliza a conversa."""
    p(f"  [OUTRO-POLO] Polo '{polo_real}' não atendido -> redirecionando")
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

    p(f"  [FILA] Lote {label}: distribuídos={dispatched} ainda_pendentes={failed}")
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
        r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                         params={'limit': 200, 'status': 'open'}, timeout=30)
        if r.status_code != 200:
            return
        data = r.json()
        convs = data.get('data', data) if isinstance(data, dict) else data
    except Exception as e:
        p(f"  [AH-RESCUE] erro lista: {e}")
        return

    if not isinstance(convs, list):
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
            if not recv or (sent and recv <= sent):
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
IN_HOURS_RESCUE_AGE_MIN = 10
IN_HOURS_RESCUE_MAX_AGE_MIN = 6 * 60  # ignora alem disso (provavelmente foi resolvido manualmente)
IN_HOURS_RESCUE_COOLDOWN_S = 30 * 60  # nao re-resgata mesma conv em 30min


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
        r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                         params={'limit': 300, 'status': 'open'}, timeout=30)
        if r.status_code != 200:
            return
        data = r.json()
        convs = data.get('data', data) if isinstance(data, dict) else data
    except Exception as e:
        p(f"  [IN-HOURS-RESCUE] erro lista: {e}")
        return

    if not isinstance(convs, list):
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
            if sent and recv <= sent:
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
            try:
                _msgs = get_conversation_messages_api(cid, limit=6)
                _last_aluno_body = ''
                for _m in (_msgs or []):
                    if _m.get('received', False):
                        _last_aluno_body = (_m.get('body') or _m.get('text') or '').strip()
                        break
                if _last_aluno_body and _is_farewell_message(_last_aluno_body):
                    p(f"  [IN-HOURS-RESCUE] Conv {cid[:12]} ...{phone[-4:]} ultima msg do aluno e despedida ('{_last_aluno_body[:30]}') — pulando resgate e fechando")
                    try:
                        close_conversation_crm(cid, phone=phone)
                    except Exception as e_cc:
                        p(f"  [IN-HOURS-RESCUE] erro close: {e_cc}")
                    try:
                        update_pending_escalation_status(
                            cid, 'closed_no_engagement',
                            note='Aluno encerrou com agradecimento/despedida — sem necessidade de atendente',
                        )
                    except Exception:
                        pass
                    _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                    continue
            except Exception as e_far:
                p(f"  [IN-HOURS-RESCUE] erro check farewell {cid[:12]}: {e_far}")

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
                _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                continue

            consultant_name = consultant.get('nome', '')
            consultant_first = consultant_name.split()[0] if consultant_name else ''
            student_first_part = f" {first}" if first else ''
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

            lead_id, business_id, created_now = _ensure_lead_for_rescue(phone, name)
            if not lead_id:
                p(f"  [IN-HOURS-RESCUE] sem lead p/ ...{phone[-4:]} - aborta atribuicao (evita orfa CRM)")
                _IN_HOURS_RESCUE_RECENT[cid] = now_ts
                continue
            try:
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
                p(f"  [IN-HOURS-RESCUE] erro transferencia: {e_t}")

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


# ===================== POST-CLOSE RESCUE (reabertura apos encerramento) =====================
_POST_CLOSE_RESCUE_RECENT = {}  # conv_id -> last_action_ts
POST_CLOSE_RESCUE_AGE_MIN = 5
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
)

# Padroes de despedida (msg do aluno apos encerramento que NAO requer atendente)
_FAREWELL_KEYWORDS = (
    'obrigad', 'valeu', 'vlw', 'agradeco', 'agradeço',
    'tchau', 'ate mais', 'até mais', 'ate logo', 'até logo',
    'beleza', 'blz', 'ok', 'okay', 'okey', 'show',
    'perfeito', 'otimo', 'ótimo', 'maravilha', 'tranquilo',
    'entendido', 'ciente', 'bom dia', 'boa tarde', 'boa noite',
    'nada', 'so isso', 'só isso', 'era isso',
)
_FAREWELL_EMOJIS = ('👍', '🙏', '❤', '❤️', '😊', '🙌', '👏', '✅', '😉', '😘')


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


def _extract_last_attendant_from_history(msgs):
    """Procura o nome do atendente que encerrou no historico recente.
    Padrao: 'Camila Ferreira finalizou o atendimento'.
    Retorna primeiro nome em lowercase ou None.
    """
    if not msgs:
        return None
    import re
    for m in reversed(msgs):
        body = (m.get('body') or m.get('text') or '').strip()
        if not body:
            continue
        match = re.match(r'^([A-Z][a-zA-ZÀ-ÿ]+)(?:\s+[A-Z][a-zA-ZÀ-ÿ]+)*\s+finalizou\s+o\s+atendimento',
                         body)
        if match:
            return match.group(1).strip().lower()
    return None


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
        r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                         params={'limit': 200, 'status': 'open'}, timeout=30)
        if r.status_code != 200:
            return
        data = r.json()
        convs = data.get('data', data) if isinstance(data, dict) else data
    except Exception as e:
        p(f"  [POST-CLOSE-RESCUE] erro lista: {e}")
        return

    if not isinstance(convs, list):
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
            if sent and recv <= sent:
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

            if _is_farewell_message(user_text):
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
                    note = (
                        f"🙏 *Despedida automatica* — aluno respondeu '{user_text[:50]}' "
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
                p(f"  [POST-CLOSE-RESCUE] DESPEDIDA conv={cid[:12]} ...{phone[-4:]} ({int(age_min)}min) '{user_text[:40]}' -> finalizada")
                continue

            last_attendant_first = _extract_last_attendant_from_history(msgs)
            consultant_used = None

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


def _body_recently_sent(conv_id, text, window_s=10 * 60):
    """True se body NORMALIZADO ja foi enviado nessa conv dentro da janela.
    Persiste em agent_sent_signatures (sobrevive a restart)."""
    if not conv_id or not text:
        return False
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return False
    norm = _normalize_body_for_dedup(text)
    if not norm:
        return False
    h = _hash_body(norm)
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT signature, sent_at FROM agent_sent_signatures
            WHERE conv_id = %s AND body_hash = %s
              AND sent_at > NOW() - (%s || ' seconds')::interval
            ORDER BY sent_at DESC
            LIMIT 1
        """, (conv_id, h, str(int(window_s))))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row is not None
    except Exception:
        return False


def _register_body(conv_id, text, signature='body'):
    """Registra body normalizado em agent_sent_signatures (anti-repeticao persistente)."""
    if not conv_id or not text:
        return
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return
    norm = _normalize_body_for_dedup(text)
    if not norm:
        return
    h = _hash_body(norm)
    try:
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


def _mark_handoff_active(conv_id, motivo, target='', ttl_s=12 * 3600, body=''):
    _ensure_dedup_tables()
    if not _DEDUP_TABLES_READY:
        return
    try:
        h = _hash_body(body)
        conn = get_db()
        cur = conn.cursor()
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
- Horario de atendimento: Seg-Sex 9h-20h, Sab 9h-13h. Fora disso o bot deve dizer "fora do horario" OU oferecer fila pre-abertura quando faltar <= 60min para 9h.
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


def _openai_supervisor_get_window(conv_id, max_msgs=10):
    """Retorna lista [(role, text, ts, is_internal)] das ultimas mensagens.

    Usa get_conversation_messages_api (que ja eh validado e funciona) em
    vez de chamar o DCZ diretamente. O caminho proprio anterior estava
    retornando lista vazia para todas as 75 convs (caso reportado).
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
            out.append((role, body[:600], m.get('createdAt') or m.get('created_at') or '', is_internal))
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
        # extrai phone se possivel
        phone = ''
        try:
            for k in ('contactPhoneNumber', 'phone', 'contactPhone'):
                v = c.get(k)
                if v:
                    phone = str(v)
                    break
        except Exception:
            pass

        action = ''
        if sev == 'alta' and ptype in ('repeticao_resposta', 'sobre_resposta', 'duplicado_distribuicao'):
            # === Silenciar bot + FECHAR O CICLO (gap detectado pelo usuario) ===
            # Antes: so silenciava e finding ficava no dashboard. Gap: se a conv
            # nao tinha atendente, aluno ficava ate 10min sem resposta ate o
            # in_hours_rescue pegar. Agora distribui IMEDIATAMENTE + nudge.
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
                    # Ja tem humano - registra como priority para destacar na fila.
                    try:
                        globals()['_current_phone'] = phone
                        _conv_states.setdefault(cid, _default_conv_state())['phone'] = phone
                        record_pending_escalation(
                            cid,
                            reason='supervisor_block_with_human',
                            tier='priority',
                            retorno_label='consultor ja esta na conversa',
                            question=resumo[:200],
                        )
                        p(f"  [OPENAI-SUP] {cid[:12]} ja com humano({len(conv_attendants)}) - registrado priority na fila")
                    except Exception:
                        pass

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

                # Passo 3: silenciar bot POR ULTIMO. handoff_active e 1 linha por
                # conv (chave primaria), entao isso sobrescreve um eventual
                # 'dispatch' deixado por distribute_to_attendant. Bot fica
                # silenciado de fato ate humano clicar 'Liberar agente'.
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
    phone = (target.get('contact') or {}).get('phone') or target.get('contactPhoneNumber') or ''
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
                                            'attendant': {'id': crm_id}}, timeout=10)
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

        # PATCH 1: responsável (campo attendant com objeto {id: CRM_UUID})
        r_resp = requests.patch(
            f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
            json={'attendant': {'id': crm_id}}, timeout=10
        )
        p(f"  [DIST-BIZ] Business {biz_id[:16]} -> attendant.id={crm_id[:12]} (status={r_resp.status_code})")

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
                                    max_retries=2):
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
                                    json={'attendant': {'id': expected_crm_id},
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
        # change-attendant no DCZ tem propagacao assincrona (eventual consistency).
        # Sleeps crescentes: 3s, 6s, 9s. Dao tempo do DCZ refletir antes de
        # marcar como divergente — evita falsos positivos do tipo "Chat=False"
        # quando o atendimento foi de fato iniciado segundos depois.
        time.sleep(min(3 * (attempt + 1), 10))

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
    consultant = get_available_consultant(exclude_attendants=exclude_attendants)
    if not consultant:
        p(f"  [DIST] [MODE] human_unavailable — fallback nota interna (motivo='{reason}')")
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
            _mark_handoff_active(conv_id, 'human_unavailable', target='',
                                 ttl_s=6 * 3600, body=busy_msg)
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
        try:
            search_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
            r_lead = requests.get(f'{DCZ_CRM}/leads', headers=H,
                                  params={'search': search_phone, 'limit': 5}, timeout=10)
            if r_lead.status_code == 200:
                ld = r_lead.json()
                leads_list = ld.get('data', ld) if isinstance(ld, dict) else ld
                if isinstance(leads_list, list) and leads_list:
                    lead_id = leads_list[0].get('id', '')
                    p(f"  [DIST] Lead encontrado por telefone: {lead_id[:16]}")
        except Exception as e:
            p(f"  [DIST] Erro buscando lead por telefone: {e}")

    if not lead_id and phone:
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
        new_lead_id, new_biz_id = create_lead_and_business(phone, contact_name)
        if new_lead_id:
            lead_id = new_lead_id
            p(f"  [DIST] Lead criado para distribuição: {lead_id[:16]}")

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
            max_retries=2,
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
        note = (f"🔔 *Distribuição automática pelo agente IA*\n"
                f"Atendente: *{nome}*\n"
                f"Motivo: {reason}" if reason else
                f"🔔 *Distribuição automática pelo agente IA*\n"
                f"Atendente: *{nome}*")

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
            sent_ok = send_and_track(conv_id, client_msg)
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


def trigger_retention(conv_id, lead_id, question):
    """Aciona Retenção: tag + responsável Wesley no lead + nota interna."""
    try:
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
                json={'tags': existing_tags, 'attendant': {'id': RETENTION_WESLEY_CRM_ID}},
                timeout=10
            )
            p(f"  [RETENÇÃO] Lead: tag + attendant Wesley (status={r.status_code})")

            try:
                r_biz = requests.get(
                    f'{DCZ_CRM}/businesses', headers=H,
                    params={'search': _current_phone or PHONE_TO_MONITOR, 'limit': 5}, timeout=10
                )
                if r_biz.status_code == 200:
                    biz_data = r_biz.json()
                    biz_list = biz_data.get('data', biz_data) if isinstance(biz_data, dict) else biz_data
                    for biz in (biz_list if isinstance(biz_list, list) else []):
                        biz_lead = biz.get('lead', {})
                        biz_lead_id = biz_lead.get('id', '') if isinstance(biz_lead, dict) else str(biz_lead)
                        if biz_lead_id == lead_id:
                            biz_id = biz.get('id')
                            rb = requests.patch(
                                f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
                                json={'attendant': {'id': RETENTION_WESLEY_CRM_ID}}, timeout=10
                            )
                            p(f"  [RETENÇÃO] Negócio attendant -> Wesley (status={rb.status_code})")
                            rb2 = requests.patch(
                                f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
                                json={'stageId': STAGE_ATENDIMENTO_ID}, timeout=10
                            )
                            p(f"  [RETENÇÃO] Negócio -> Atendimento (status={rb2.status_code})")
                            break
            except Exception as e2:
                p(f"  [RETENÇÃO] Erro ao atualizar negócio: {e2}")
        else:
            p(f"  [RETENÇÃO] Sem lead_id, pulando transferência")

        note = (
            f"🔴 *Retenção - Agente IA*\n"
            f"O aluno manifestou intenção de cancelamento/trancamento.\n"
            f"Mensagem: \"{question[:120]}\"\n"
            f"Transferido automaticamente para Wesley Guerreiro (Retenção)."
        )
        requests.post(
            f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
            headers=H, json={'body': note, 'isInternal': True}, timeout=10
        )
        p(f"  [RETENÇÃO] Nota interna enviada na conversa")

        _dcz_transfer_chat(conv_id, 'Wesley')
        p(f"  [RETENÇÃO] Chat transferido para Wesley")

        _conv_states.setdefault(conv_id, _default_conv_state())['_human_took_over'] = True
        _conv_states[conv_id]['waiting_for_client'] = False
        _conv_states[conv_id]['inactivity_start'] = 0
        _conv_states[conv_id]['followup_stage'] = 0
        _conv_states[conv_id]['_last_responded_ts'] = time.time()

    except Exception as e:
        p(f"  [RETENÇÃO] Erro: {e}")


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
    close_match = any(w in q_lower for w in CLOSING_WORDS) or q_lower in (
        'não obrigado', 'nao obrigado', 'encerrar', 'não', 'nao',
        'pode encerrar', 'pode fechar', 'fechar', 'encerrar atendimento',
        'não preciso', 'nao preciso', 'não preciso de mais nada', 'nao preciso de mais nada',
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

    # === RETENÇÃO (cancelamento / trancamento) — ANTES de is_first ===
    if is_retention_intent(question):
        p(f"  [RETENÇÃO] Intenção detectada: \"{question[:80]}\"")

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
                _mark_handoff_active(conv_id, 'retention_after_hours', target='Wesley',
                                     ttl_s=14 * 3600, body=msg_after)
                try:
                    requests.post(
                        f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
                        headers=H,
                        json={'body': f'🤝 *Retenção fora do horário* — IA orientou aluno; Wesley deve retomar {retorno}.',
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
                                    retorno_label=retorno, question=question,
                                    preferred_attendant='Wesley')
            p(f"  [RETENÇÃO] [MODE] after_hours — sem trigger_retention, retorno {retorno}, preferred=Wesley")
            waiting_for_client = True; inactivity_start = time.time()
            return

        if _signature_recently_sent(conv_id, 'retention', window_s=24 * 3600):
            p(f"  [RETENÇÃO] dedup: retention ja enviada nas ultimas 24h - suprimindo reenvio")
        else:
            meta_typing_on()
            send_and_track(conv_id, RETENTION_MSG)
            conversation_messages.append({'role': 'bot', 'text': RETENTION_MSG})
            log_to_db(conv_id, question, RETENTION_MSG, 1.0, 'retention')
            _register_signature(conv_id, 'retention', RETENTION_MSG)
            _mark_handoff_active(conv_id, 'retention', target='Wesley',
                                 ttl_s=8 * 3600, body=RETENTION_MSG)

            lead_id = student_profile.get('lead_id') if student_profile else None
            trigger_retention(conv_id, lead_id, question)

            # Apresentação do Wesley enviada pelo agente
            _fname = (student_profile.get('first_name') or '').strip() if student_profile else ''
            _wesley_intro = (f"Olá{', *' + _fname + '*' if _fname else ''}! "
                             f"Sou o Wesley e irei seguir com o seu atendimento 😊")
            time.sleep(1)
            send_and_track(conv_id, _wesley_intro)
            _register_signature(conv_id, 'retention_intro', _wesley_intro)
            p(f"  [RETENÇÃO] Apresentação do Wesley enviada pelo agente")

        try:
            summary = generate_conversation_summary(conversation_messages)
            save_memory(cur_phone, student_profile, 'retencao', summary, sentiment)
        except Exception as e_ret:
            p(f"  [RETENÇÃO] Erro na memória: {e_ret}")
        waiting_for_client = False; inactivity_start = 0
        p(f"  [RETENÇÃO] Conversa encaminhada para Wesley - follow-ups desativados")
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

    # === INICIO DAS AULAS (matricula nova -> turma de agosto) ===
    # Regra critica do time: quem se matricula agora ingressa na turma do
    # 2o semestre (agosto). O LLM ja errou dizendo "fevereiro". Resposta
    # canonica ANTES do LLM para nunca paragrafar errado.
    try:
        if detect_inicio_aulas_intent(question):
            p(f"  [INICIO-AULAS] intent detectado — resposta canonica (turma de agosto)")
            if handle_inicio_aulas_intent(conv_id, question=question):
                conversation_messages.append({'role': 'user', 'text': question})
                conversation_messages.append({'role': 'bot', 'text': INICIO_AULAS_MSG})
            return
    except Exception as e_ia:
        p(f"  [INICIO-AULAS] erro: {e_ia}")

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
            waiting_for_client = False; inactivity_start = 0
            return
        log_to_db(conv_id, question, ESCALATION_MSG, 0.1, 'escalate_cpf')
        distributed = distribute_to_attendant(conv_id, 'Dados sensíveis detectados (CPF/RGM)')
        conversation_messages.append({'role': 'bot', 'text': ESCALATION_MSG})
        waiting_for_client = False; inactivity_start = 0
        p(f"  [ESCALADO] Distribuído={distributed} - Follow-ups desativados")
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
    history = build_conversation_history(conv_id)
    clean, confidence, llm_time = call_llm(question, references, history, student_profile, memory, sentiment, is_first, image_b64=image_b64, image_mime=image_mime, image_desc=image_desc)

    p(f"  Resultado: conf={confidence:.2f} | top_sim={top_score:.3f}")
    p(f"  Resposta: {clean[:200]}...")

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

            if cycle % 10 == 0:
                try:
                    process_after_hours_rescue()
                except Exception as e_rescue:
                    p(f"  [AH-RESCUE] Erro: {e_rescue}")
                try:
                    process_in_hours_rescue()
                except Exception as e_ihr:
                    p(f"  [IN-HOURS-RESCUE] Erro: {e_ihr}")
                try:
                    process_post_close_rescue()
                except Exception as e_pcr:
                    p(f"  [POST-CLOSE-RESCUE] Erro: {e_pcr}")
                try:
                    process_supervisor_loop()
                except Exception as e_sup:
                    p(f"  [SUPERVISOR] Erro: {e_sup}")
                try:
                    process_openai_supervisor_loop()
                except Exception as e_osup:
                    p(f"  [OPENAI-SUP] Erro: {e_osup}")

            if cycle % 2 == 0:
                active_count = sum(1 for s in _conv_states.values() if s.get('waiting_for_client'))
                if cycle % 10 == 0:
                    p(f"  ...ativo ({cycle * POLL_INTERVAL}s | {len(processed_msg_ids)} msgs | {len(_conv_states)} convs | {active_count} aguardando)")
                _heartbeat('online', f'cycle={cycle} convs={len(_conv_states)} active={active_count}')

            # Busca conversas abertas recentes
            try:
                r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                                    params={'limit': 300, 'status': 'open'}, timeout=60)
            except Exception as _e_conv:
                p(f"  [ERRO] Falha ao buscar conversas: {_e_conv}")
                continue
            if r.status_code != 200:
                p(f"  [ERRO] API retornou {r.status_code}")
                continue

            convs_data = r.json()
            convs_raw = convs_data.get('data', convs_data) if isinstance(convs_data, dict) else convs_data
            if not isinstance(convs_raw, list) or not convs_raw:
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
                _is_fu = any(fp in _lm_body for fp in [
                    'tudo certo por a', 'ainda est', 'não tive retorno',
                    'pode mandar', 'precisar de mais alguma',
                ])
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
            _MAX_WAITING_PER_CYCLE = 20
            convs = waiting[:_MAX_WAITING_PER_CYCLE] + rest
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
                _is_followup_msg = any(fp in _last_msg for fp in [
                    'tudo certo por a', 'ainda est', 'não tive retorno',
                    'pode mandar', 'precisar de mais alguma',
                ])
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
                return any(fp in b for fp in ['tudo certo por a', 'ainda est', 'não tive retorno', 'pode mandar', 'precisar de mais alguma'])
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
                        msg1 = FOLLOWUP_1_MSG.format(name=name_fmt)
                        p(f"  [FOLLOWUP-1] [{cur_phone[-4:] if cur_phone else '????'}] {int(elapsed)}s sem resposta")
                        send_message_crm(cid, msg1, buttons=FOLLOWUP_1_BUTTONS)
                        log_to_db(cid, '(inatividade)', msg1, 1.0, 'followup_1')
                        st['followup_stage'] = 1
                        st['inactivity_start'] = time.time()

                    elif st.get('followup_stage', 0) == 1 and elapsed >= CLOSE_DELAY:
                        _safe_to_close = False
                        if elapsed >= 3600:
                            _safe_to_close = True
                        else:
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
                                        p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] Aluno respondeu depois do follow-up -> cancelando close")
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
