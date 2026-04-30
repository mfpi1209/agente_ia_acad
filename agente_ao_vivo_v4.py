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
import hashlib
import base64
from datetime import datetime
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
N8N_WEBHOOK_LEADS_CPF = 'https://n8n-new-n8n.ca31ey.easypanel.host/webhook/leads_cpf_csv'
STAGE_ATENDIMENTO_ID = '7e89e4a3-09ca-4e5a-976b-35f7f041ccf6'
STAGE_EM_ATENDIMENTO_ID = '742714eb-ac5a-435f-8680-97e6ab8f2f6e'

POLOS_LIST = ("Barra Funda\nVila Prudente\nVila Mariana\nFreguesia do Ó (Moinho Velho)\n"
              "Vila Ema (Sapopemba)\nIbirapuera (Indianópolis)\nTaboão da Serra - Jardim Mituzi\n"
              "Taboão da Serra - Centro\nCampinas (Ouro Verde)\nItapira (Santo Antônio)\n"
              "Capivari (Centro)\nMorumbi (Vila Progedior)\nSantana 2")

NOT_IN_BASE_BUTTONS = ['Já sou aluno', 'Quero me matricular']
COMMERCIAL_REDIRECT_MSG = ("Certo!\n\nEste canal é dedicado ao atendimento dos nossos alunos.\n\n"
                           "Vamos transferir esta conversa para nosso time comercial e em breve, "
                           "você receberá uma mensagem de um(a) de nossos consultores(as) que vai "
                           "te orientar e tirar todas as suas dúvidas. 😉\nAté mais!")

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

ALMOCO_ANTE_MIN = 20
ALMOCO_DURACAO_MIN = 60
SAIDA_ANTE_MIN = 20

OUT_OF_HOURS_MSG = (
    "Nosso time de atendimento está disponível nos seguintes horários:\n\n"
    "📅 *Segunda a Sexta*: 09h às 20h\n"
    "📅 *Sábado*: 09h às 13h\n\n"
    "Fora desse horário, eu (assistente virtual) continuo por aqui para te ajudar! 😊\n"
    "Caso precise falar com um atendente, por favor retorne dentro do horário de atendimento."
)

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
CLOSING_RESPONSE_TPL = "Obrigado pelo contato{name_suffix}! Qualquer dúvida é só nos chamar novamente. Até mais! 😊"
ESCALATION_MSG = "Entendi sua situação. Vou te transferir para um atendente que pode te ajudar diretamente. Um momento, por favor."

RETENTION_TAG_ID = '6fcefbd5-3c33-4e5c-b139-7f89718f6f0c'
RETENTION_WESLEY_CRM_ID = 'dd6cbed7-7666-45d1-bd90-368c8b97e217'
RETENTION_PHRASES = [
    'quero cancelar', 'quero trancar', 'vou cancelar', 'vou trancar',
    'cancelar meu curso', 'cancelar minha matrícula', 'cancelar minha matricula',
    'trancar meu curso', 'trancar minha matrícula', 'trancar minha matricula',
    'cancelar o curso', 'trancar o curso', 'cancelar a matrícula', 'cancelar a matricula',
    'trancar a matrícula', 'trancar a matricula',
    'quero desistir', 'vou desistir', 'desistir do curso',
    'preciso cancelar', 'preciso trancar', 'desejo cancelar', 'desejo trancar',
    'gostaria de cancelar', 'gostaria de trancar',
    'quero realizar o cancelamento', 'quero fazer o cancelamento',
    'quero realizar o trancamento', 'quero fazer o trancamento',
    'cancelar matrícula', 'cancelar matricula', 'trancar matrícula', 'trancar matricula',
]
RETENTION_QUESTION_WORDS = [
    'prazo', 'data', 'quando', 'como funciona', 'como solicitar', 'quanto custa',
    'valor', 'taxa', 'multa', 'processo', 'procedimento', 'posso solicitar',
    'até que', 'ate que', 'qual o prazo', 'como faço para solicitar',
]
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

SYSTEM_PROMPT = """Você é a consultora virtual de suporte da Cruzeiro do Sul Educacional.
Fale de forma natural e humana, como um consultor real pelo WhatsApp. Converse, não despeje informação.

{student_context}

{memory_context}

{sentiment_context}

{active_alerts}

## REGRAS ABSOLUTAS:
1. **NUNCA INVENTE** informações. Use SOMENTE as referências abaixo e alertas ativos.
2. **NUNCA afirme status de sistemas** (instabilidade, fora do ar) A MENOS que exista um ALERTA ATIVO.
3. **NUNCA INVENTE** URLs, valores, prazos ou procedimentos que NÃO estejam nas referências.
4. **NUNCA forneça dados pessoais** (RGM, e-mail acadêmico, senhas).
5. **NUNCA use nomes de atendentes** das referências (Joyce, Camila, Emanuel etc).
6. Use o nome do aluno ao longo da conversa.
7. Se a referência tiver links ou vídeos, **INCLUA**.
8. **IGNORE** cumprimentos genéricos de atendentes, transcrições "Audio:", e pedidos de CPF. Extraia só informação útil.
9. **NUNCA ofereça transferir para atendente** por conta própria. Isso é controlado pelos botões do sistema.

## COMO CONVERSAR (REGRA MAIS IMPORTANTE):
Você conversa pelo WhatsApp. Ninguém manda um textão no WhatsApp. Separe sua resposta em blocos curtos usando \\n\\n (dois enters).

### Fluxo de uma PRIMEIRA resposta sobre um problema:
1. **Acolhida + verificação** (1-2 frases): cumprimente, diga que vai verificar, cheque alertas.
2. **Pergunta investigativa**: ANTES de dar a solução, pergunte o que está acontecendo.
   Não despeje todas as soluções possíveis de uma vez. Investigue.

Exemplo - aluno diz "não consigo acessar o portal":

RUIM (textão com tudo de uma vez):
"Opa Marcelo! Vamos resolver. Não há instabilidade. Pelo computador acesse https://novoportal... Pelo celular use o DUDA... Se esqueceu a senha clique em Esqueci... Se trocou de celular revogue o Authenticator..."

BOM (conversa, pergunta primeiro):
"Opa Marcelo, deixa eu verificar aqui... Não tem nenhum alerta de instabilidade, então o portal tá funcionando normal.

Me conta, o que acontece quando você tenta acessar? Aparece alguma mensagem de erro, a página não carrega, ou você esqueceu a senha?"

### Fluxo após o aluno responder com mais detalhes:
Aí sim, dê a orientação ESPECÍFICA pro problema dele, de forma curta e direta.
Não repita o que já disse. Vá direto ao ponto.

### Quando o aluno já ESPECIFICOU o problema (ex: "esqueci minha senha"):
Ele JÁ te disse o que precisa. Não pergunte de volta. Resolva direto:
"Opa Marcelo, sem problemas! Vou te ajudar a redefinir sua senha.

Na tela de login do portal, clica em *Esqueci minha senha*. Vai pedir seu CPF e e-mail cadastrado. Você vai receber um link pra criar uma senha nova.

Se não receber o e-mail, verifica a caixa de spam. Qualquer coisa, me avisa aqui!"

### Tom:
- Nunca "Entendo sua frustração". Seja natural: "Opa", "Vamos resolver", "Deixa eu ver"
- Fale com confiança, como quem sabe o que está fazendo
- Emoji com moderação (máximo 1 por bloco)

## FORMATO:
- Separe parágrafos com \\n\\n para ficarem como mensagens separadas no WhatsApp.
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
CLOSE_DELAY      = 600
FOLLOWUP_1_MSG     = "Oi{name}! Ainda está por aí? Se tiver mais alguma dúvida, é só falar 😊"
FOLLOWUP_1_BUTTONS = ['Tenho outra dúvida', 'Não, obrigado!']
CLOSE_INACTIVITY_MSG     = "Como não tivemos retorno, vou finalizar o contato por aqui para te deixar seguir com seus compromissos. Estaremos à disposição caso precise retomar o assunto depois! ✨"
CLOSE_INACTIVITY_BUTTONS = None

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
    }

def _load_conv_state(conv_id):
    """Carrega o estado de uma conversa nas variáveis globais."""
    global student_profile, conversation_messages, followup_stage, waiting_for_client
    global inactivity_start, _last_auto_skipped, _awaiting_cpf, _student_in_base
    global _awaiting_polo_confirm, active_conv_id, _current_phone
    st = _conv_states.get(conv_id) or _default_conv_state()
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
        '_human_took_over': prev.get('_human_took_over', False),
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
                             json={'leadId': new_lead_id, 'stageId': STAGE_ATENDIMENTO_ID}, timeout=10)
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
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
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

def tabulate_interaction(messages, profile, phone):
    """Classifica a interação automaticamente com GPT."""
    if not messages or len(messages) < 2:
        return

    conv_text = '\n'.join([f"{'Aluno' if m['role']=='user' else 'IA'}: {m['text'][:150]}" for m in messages[-10:]])

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{
                'role': 'user',
                'content': f"""Classifique este atendimento. Responda EXATAMENTE neste formato JSON:
{{"tema":"ACESSO_PORTAL|FINANCEIRO|ACADEMICO|MATRICULA|DOCUMENTOS|OUTRO","subtema":"descricao curta","sentimento":"satisfeito|neutro|frustrado|irritado","resolvido":"sim|nao|parcial|escalado","nps":7}}

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

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO interaction_summary
            (phone, lead_id, student_name, tema, subtema, sentimento, resolvido, nps_implicito, resumo, mensagens_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            len(messages)
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


def send_and_track(conv_id, text, buttons=None):
    """Reforça typing antes de enviar + pequeno delay humanizado."""
    global last_response_time
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
    last_response_time = time.time()
    if buttons:
        p(f"    Enviado com {len(buttons)} botoes")
    return status


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


def close_conversation_crm(conv_id):
    """Fecha/finaliza a conversa no DataCrazy via POST /finish."""
    try:
        r = requests.post(
            f'{DCZ_API}/api/v1/conversations/{conv_id}/finish',
            headers=H, json={}, timeout=10
        )
        p(f"  Conv {conv_id[:12]} finalizada no DataCrazy (status={r.status_code})")
        return r.status_code
    except Exception as e:
        p(f"  Erro ao fechar conv: {e}")
        return 500


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


def is_within_business_hours():
    """Verifica se estamos dentro do horário de atendimento humano.
    Seg-Sex: 09:00–20:00 | Sáb: 09:00–13:00 | Dom: sem atendimento.
    """
    now = _now_sp()
    dow = now.weekday()  # 0=seg, 6=dom
    hour = now.hour

    if dow <= 4:
        return 9 <= hour < 20
    elif dow == 5:
        return 9 <= hour < 13
    return False


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


def get_available_consultant():
    """Consulta Supabase e retorna o consultor mais adequado ou None.
    Aplica as mesmas regras do workflow n8n de distribuição.
    """
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
    """Atribui o lead ao responsável no DataCrazy CRM."""
    nome_norm = attendant_name.strip().lower()
    nome_norm = ''.join(c for c in __import__('unicodedata').normalize('NFD', nome_norm) if __import__('unicodedata').category(c) != 'Mn')
    att_id = ATTENDANT_MAP.get(nome_norm)
    if not att_id:
        p(f"  [DIST] attendantId não encontrado para '{attendant_name}' (norm='{nome_norm}')")
        return False
    if not lead_id:
        p(f"  [DIST] lead_id vazio, skip lead transfer")
        return False
    try:
        r = requests.patch(
            f'{DCZ_CRM}/leads/{lead_id}', headers=H,
            json={'responsibleId': att_id}, timeout=10
        )
        p(f"  [DIST] Lead {lead_id[:16]} -> responsibleId={att_id[:12]} (status={r.status_code})")
        return r.status_code in (200, 201)
    except Exception as e:
        p(f"  [DIST] Erro lead transfer: {e}")
        return False


def _dcz_transfer_business(phone, attendant_name):
    """Encontra o negócio do aluno e atribui ao attendant."""
    nome_norm = attendant_name.strip().lower()
    nome_norm = ''.join(c for c in __import__('unicodedata').normalize('NFD', nome_norm) if __import__('unicodedata').category(c) != 'Mn')
    att_id = ATTENDANT_MAP.get(nome_norm)
    if not att_id:
        return False
    try:
        search_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        r = requests.get(f'{DCZ_CRM}/businesses', headers=H,
                         params={'search': search_phone, 'limit': 5}, timeout=10)
        if r.status_code != 200:
            p(f"  [DIST] Business search falhou: {r.status_code}")
            return False
        data = r.json()
        biz_list = data.get('data', data) if isinstance(data, dict) else data
        if not isinstance(biz_list, list) or not biz_list:
            p(f"  [DIST] Nenhum negócio encontrado para {search_phone}")
            return False
        biz = biz_list[0]
        biz_id = biz.get('id', '')
        if not biz_id:
            return False
        r2 = requests.patch(
            f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
            json={'responsibleId': att_id}, timeout=10
        )
        p(f"  [DIST] Business {biz_id[:16]} -> responsibleId={att_id[:12]} (status={r2.status_code})")
        # Mover para o stage de Atendimento
        try:
            r3 = requests.patch(
                f'{DCZ_CRM}/businesses/{biz_id}', headers=H,
                json={'stageId': STAGE_ATENDIMENTO_ID}, timeout=10
            )
            p(f"  [DIST] Business {biz_id[:16]} -> stageId=Atendimento (status={r3.status_code})")
        except Exception:
            pass
        return r2.status_code in (200, 201)
    except Exception as e:
        p(f"  [DIST] Erro business transfer: {e}")
        return False


def _dcz_transfer_chat(conv_id, attendant_name):
    """Transfere a conversa para o attendant via change-attendant."""
    nome_norm = attendant_name.strip().lower()
    nome_norm = ''.join(c for c in __import__('unicodedata').normalize('NFD', nome_norm) if __import__('unicodedata').category(c) != 'Mn')
    att_id = ATTENDANT_MAP.get(nome_norm)
    if not att_id:
        return False
    try:
        r = requests.post(
            f'{DCZ_MSG}/messaging/conversations/{conv_id}/change-attendant',
            headers=H, json={'attendantId': att_id}, timeout=10
        )
        p(f"  [DIST] Chat {conv_id[:16]} -> attendant={att_id[:12]} (status={r.status_code})")
        return r.status_code in (200, 201)
    except Exception as e:
        p(f"  [DIST] Erro chat transfer: {e}")
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


def distribute_to_attendant(conv_id, reason=''):
    """Distribui o aluno para um atendente humano real.
    1) Verifica horário  2) Escolhe consultor  3) Transfere lead/negócio/chat
    4) Atualiza fila no Supabase  5) Envia mensagem ao aluno.
    Retorna True se distribuiu, False se não conseguiu.
    """
    if not is_within_business_hours():
        p(f"  [DIST] Fora do horário de atendimento")
        meta_typing_on()
        send_and_track(conv_id, OUT_OF_HOURS_MSG)
        return False

    consultant = get_available_consultant()
    if not consultant:
        p(f"  [DIST] Nenhum consultor disponível -> fallback nota interna")
        msg = ("No momento, todos os nossos atendentes estão ocupados. "
               "Mas não se preocupe — em breve um deles irá te atender! 😊")
        meta_typing_on()
        send_and_track(conv_id, msg)
        transfer_to_human(conv_id, reason)
        return False

    nome = consultant['nome']
    p(f"  [DIST] Distribuindo para {nome}...")

    lead_id = student_profile.get('lead_id', '') if student_profile else ''
    phone = _current_phone or PHONE_TO_MONITOR

    _dcz_transfer_lead(lead_id, nome)
    _dcz_transfer_business(phone, nome)
    _dcz_transfer_chat(conv_id, nome)
    _supabase_increment_fila(consultant['id'], consultant['fila'])

    note = (f"🔔 *Distribuição automática pelo agente IA*\n"
            f"Atendente: *{nome}*\n"
            f"Motivo: {reason}" if reason else
            f"🔔 *Distribuição automática pelo agente IA*\n"
            f"Atendente: *{nome}*")
    try:
        requests.post(
            f'{DCZ_API}/api/v1/conversations/{conv_id}/messages',
            headers=H, json={'body': note, 'isInternal': True}, timeout=10
        )
    except Exception:
        pass

    client_msg = (
        f"Vou te transferir para *{nome}*, que vai dar continuidade ao seu atendimento. "
        f"Um momento, por favor! 😊"
    )
    meta_typing_on()
    send_and_track(conv_id, client_msg)

    p(f"  [DIST] ✅ Distribuição concluída para {nome}")
    if conv_id in _conv_states:
        _conv_states[conv_id]['_human_took_over'] = True
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
                json={'tags': existing_tags, 'responsibleId': RETENTION_WESLEY_CRM_ID},
                timeout=10
            )
            p(f"  [RETENÇÃO] Lead: tag + responsável Wesley (status={r.status_code})")

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
                                json={'responsibleId': RETENTION_WESLEY_CRM_ID}, timeout=10
                            )
                            p(f"  [RETENÇÃO] Negócio responsável -> Wesley (status={rb.status_code})")
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

        if conv_id in _conv_states:
            _conv_states[conv_id]['_human_took_over'] = True

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


def get_conversation_messages_api(conv_id, limit=15):
    try:
        r = requests.get(f'{DCZ_MSG}/messaging/conversations/{conv_id}/messages',
                        headers=H, params={'limit': limit}, timeout=10)
        if r.status_code != 200:
            return []
        return r.json().get('messages', [])
    except Exception as e:
        p(f"  Erro msgs: {e}")
        return []


BOT_RESPONSE_FINGERPRINTS = [
    'Essa é uma dúvida que precisa de um atendente',
    'Vou te transferir para um atendente',
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
    'Posso te ajudar de outra forma',
    'Clique em uma das opções disponíveis',
    'Teria mais alguma dúvida',
    'clique em uma das opções',
    'teria mais alguma dúvida',
]


def is_bot_message(body):
    """Detect if a message is from a bot (ours or DataCrazy salesbot)."""
    for fp in BOT_RESPONSE_FINGERPRINTS:
        if fp.lower() in body.lower():
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
    Só analisa mensagens enviadas APÓS o startup do agente que NÃO
    foram enviadas pelo próprio agente (tracked por ID ou body hash)."""
    msgs = _cached_msgs.get(conv_id) or get_conversation_messages_api(conv_id, limit=10)
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
        if is_bot_message(body):
            continue
        if _db_is_duplicate_body(body, window_seconds=3600):
            continue
        p(f"  [HUMAN-DBG] conv={conv_id[:12]} mid={mid[:12]} body={body[:60]}")
        return True
    return False


def get_new_client_message(conv_id):
    """Retorna (msg_id, body, is_button_click, image_info).
    Processa a mensagem mais recente do aluno que ainda não foi respondida."""
    msgs = get_conversation_messages_api(conv_id, limit=10)
    _cached_msgs[conv_id] = msgs

    # Determinar se existe resposta REAL do bot APÓS a última msg do aluno.
    # Compara timestamps: só conta como "respondido" se outgoing é mais recente que incoming.
    has_outgoing_response = False
    last_incoming_ts = ''
    last_outgoing_ts = ''
    for m in msgs:
        body_check = (m.get('body', '') or '').strip()
        if not body_check or m.get('isInternal', False):
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

        # Para msgs anteriores ao startup: processar se NÃO houver resposta do bot
        # (aluno esperando). Limitar a 2h para evitar processar msgs muito antigas.
        if _startup_ts > 0:
            msg_ts = m.get('createdAt', '') or m.get('timestamp', '') or ''
            if msg_ts:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(str(msg_ts).replace('Z', '+00:00'))
                    msg_age = _startup_ts - dt.timestamp()
                    if dt.timestamp() < _startup_ts:
                        if has_outgoing_response or msg_age > 7200:
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
            distribute_to_attendant(conv_id, 'Aluno não encontrado no polo - encaminhar para comercial')
            waiting_for_client = False; inactivity_start = 0
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
        distribute_to_attendant(conv_id, 'Interessado em matrícula - encaminhar para comercial')
        waiting_for_client = False; inactivity_start = 0
        return

    # === PRIMEIRA INTERAÇÃO: verifica se lead/negócio existe ===
    if is_first:
        if student_profile:
            _student_in_base = True
            p(f"  Lead existe no CRM -> saudação + menu (sem mencionar matrícula)")
        else:
            _student_in_base = False
            p(f"  Lead NÃO encontrado no CRM -> fluxo de identificação")
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

    # === BLOQUEIO: lead NÃO encontrado no CRM ===
    if _student_in_base is False and student_profile is None:
        p(f"  Lead não encontrado -> reapresentando opções de identificação")
        msg = ("Para que eu possa te atender, preciso primeiro te localizar em nosso sistema.\n\n"
               "Por favor, escolha uma das opções abaixo: 👇")
        meta_typing_on()
        send_and_track(conv_id, msg, buttons=NOT_IN_BASE_BUTTONS)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'not_in_base_block')
        waiting_for_client = True; inactivity_start = time.time()
        return

    # === RESOLVEU ===
    if any(w in q_lower for w in RESOLVED_WORDS):
        msg = f"Que bom que pude ajudar{name_suffix}! Se precisar de algo no futuro, estou à disposição. Até mais! 😊"
        meta_typing_on()
        send_and_track(conv_id, msg)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'resolved')

        summary = generate_conversation_summary(conversation_messages)
        topic = detect_topic_from_messages(conversation_messages)
        save_memory(cur_phone, student_profile, topic, summary, sentiment)
        tabulate_interaction(conversation_messages, student_profile, cur_phone)
        close_conversation_crm(conv_id)
        conversation_messages.clear()
        conversation_greeted.discard(conv_id)
        waiting_for_client = False
        followup_stage = 0
        inactivity_start = 0
        return

    # === ENCERRAMENTO ===
    close_match = any(w in q_lower for w in CLOSING_WORDS) or q_lower in (
        'não obrigado', 'nao obrigado', 'encerrar', 'não', 'nao',
        'pode encerrar', 'pode fechar', 'fechar', 'encerrar atendimento',
        'não preciso', 'nao preciso', 'não preciso de mais nada', 'nao preciso de mais nada',
    )
    if close_match and not is_first:
        msg = CLOSING_RESPONSE_TPL.format(name_suffix=name_suffix)
        meta_typing_on()
        send_and_track(conv_id, msg)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 1.0, 'closing')

        summary = generate_conversation_summary(conversation_messages)
        topic = detect_topic_from_messages(conversation_messages)
        save_memory(cur_phone, student_profile, topic, summary, sentiment)
        tabulate_interaction(conversation_messages, student_profile, cur_phone)
        close_conversation_crm(conv_id)
        conversation_messages.clear()
        conversation_greeted.discard(conv_id)
        waiting_for_client = False
        followup_stage = 0
        inactivity_start = 0
        return

    # === RETENÇÃO (cancelamento / trancamento) — ANTES da escalação ===
    if is_retention_intent(question):
        p(f"  [RETENÇÃO] Intenção detectada: \"{question[:80]}\"")
        meta_typing_on()
        send_and_track(conv_id, RETENTION_MSG)
        conversation_messages.append({'role': 'bot', 'text': RETENTION_MSG})
        log_to_db(conv_id, question, RETENTION_MSG, 1.0, 'retention')

        lead_id = student_profile.get('lead_id') if student_profile else None
        trigger_retention(conv_id, lead_id, question)

        summary = generate_conversation_summary(conversation_messages)
        save_memory(cur_phone, student_profile, 'retencao', summary, sentiment)
        tabulate_interaction(conversation_messages, student_profile, cur_phone)
        waiting_for_client = False; inactivity_start = 0
        p(f"  [RETENÇÃO] Conversa encaminhada para Wesley - follow-ups desativados")
        return

    # === ESCALAÇÃO EXPLÍCITA ===
    if any(w in q_lower for w in ESCALATE_WORDS):
        meta_typing_on()
        log_to_db(conv_id, question, ESCALATION_MSG, 1.0, 'escalate_request')
        distributed = distribute_to_attendant(conv_id, f'Solicitação explícita do aluno: "{question[:80]}"')
        conversation_messages.append({'role': 'bot', 'text': ESCALATION_MSG})

        summary = generate_conversation_summary(conversation_messages)
        save_memory(cur_phone, student_profile, 'escalacao', summary, sentiment)
        tabulate_interaction(conversation_messages, student_profile, cur_phone)
        waiting_for_client = False; inactivity_start = 0
        p(f"  [ESCALADO] Distribuído={distributed} - Follow-ups desativados")
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

    # =====================================================================
    # VERIFICA SE O TEXTO CORRESPONDE A UM ITEM DE MENU CONHECIDO
    # Agente envia diretamente submenus e conteúdo (100% agente).
    # =====================================================================
    _matched_l1_key = None
    _matched_l3_key = None
    _matched_direct_key = None
    _matched_rag_key = None

    for menu_key, mapped_key in MAIN_MENU_KEYS.items():
        if menu_key in stripped or stripped == menu_key:
            _matched_l1_key = mapped_key
            break
    if not _matched_l1_key:
        for l3_key in SUBMENU_L3:
            if l3_key in stripped or stripped == l3_key:
                _matched_l3_key = l3_key
                break
    if not _matched_l1_key and not _matched_l3_key:
        for direct_key in SUBMENU_DIRECT_RESPONSE:
            if direct_key in stripped or stripped == direct_key:
                _matched_direct_key = direct_key
                break
    if not _matched_l1_key and not _matched_l3_key and not _matched_direct_key:
        for sub_key in SUBMENU_TO_QUESTION:
            if sub_key in stripped or stripped == sub_key:
                _matched_rag_key = sub_key
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
        msg = "Não entendi 🤔 Selecione uma opção:"
        meta_typing_on()
        send_and_track(conv_id, msg, buttons=MAIN_MENU_BUTTONS)
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, 0.0, 'fallback_short')
        return

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
        send_and_track(conv_id, msg, buttons=['Tentar de novo', 'Falar com atendente', 'Ver opções'])
        conversation_messages.append({'role': 'bot', 'text': msg})
        log_to_db(conv_id, question, msg, top_score, 'escalate_low_sim')
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
        time.sleep(1)
        send_and_track(conv_id, "Ficou alguma dúvida sobre o assunto? 😊\nDigite *Resolveu* ou me conte sua dúvida!")
    else:
        p(f"  Resposta é pergunta/pede mais info (conf={confidence:.2f}) -> sem follow-up 'ficou alguma dúvida'")

    conversation_messages.append({'role': 'bot', 'text': clean})
    log_to_db(conv_id, question, clean, confidence, 'auto_reply')

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

    while True:
        try:
            time.sleep(POLL_INTERVAL)
            cycle += 1
            maybe_reload()

            # Busca conversas abertas recentes
            try:
                r = requests.get(f'{DCZ_MSG}/messaging/conversations', headers=H,
                                params={'limit': 20, 'status': 'open'}, timeout=10)
            except Exception:
                continue
            if r.status_code != 200:
                continue

            convs_data = r.json()
            convs = convs_data.get('data', convs_data) if isinstance(convs_data, dict) else convs_data
            if not isinstance(convs, list) or not convs:
                continue

            # Separar: conversas onde aluno espera resposta (prioridade) vs resto
            # Filtrar waiting para apenas as últimas 2h (matching limite em get_new_client_message)
            from datetime import datetime, timezone
            cutoff_iso = datetime.fromtimestamp(
                time.time() - 7200, tz=timezone.utc
            ).strftime('%Y-%m-%dT%H:%M:%S')
            waiting = []
            rest = []
            for c in convs:
                recv = c.get('lastReceivedMessageDate', '') or ''
                sent = c.get('lastSendedMessageDate', '') or ''
                if recv > sent and recv >= cutoff_iso:
                    waiting.append(c)
                else:
                    rest.append(c)
            waiting.sort(key=lambda c: c.get('lastReceivedMessageDate', ''))
            rest.sort(key=lambda c: c.get('lastReceivedMessageDate', ''), reverse=True)
            convs = waiting + rest[:5]

            for conv in convs:
              try:
                conv_id = conv.get('id', '')
                if not conv_id:
                    continue

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

                # Se consultor humano já assumiu, não processar mais nada
                st = _conv_states.get(conv_id, {})
                if st.get('_human_took_over'):
                    continue

                try:
                    msg_id, msg_body, is_click, img_info = get_new_client_message(conv_id)
                except Exception:
                    msg_id = msg_body = is_click = img_info = None

                # Detectar se consultor humano enviou mensagem nesta conversa
                if _check_human_took_over(conv_id):
                    p(f"  [HUMAN] [{conv_phone[-4:] if conv_phone else '????'}] Consultor humano detectado -> agente recuando")
                    _conv_states.setdefault(conv_id, _default_conv_state())['_human_took_over'] = True
                    if msg_id:
                        processed_msg_ids.add(msg_id)
                    _save_conv_state(conv_id)
                    continue

                if msg_id and msg_body:
                    if not _current_phone and conv_phone:
                        _current_phone = conv_phone
                    p(f"  >>> MSG [{conv_phone[-4:] if conv_phone else '????'}]: \"{msg_body[:80]}\"{' [+IMG]' if img_info else ''}")
                    handle_message(conv_id, msg_id, msg_body, is_click, image_info=img_info)
                    _save_conv_state(conv_id)
              except Exception as conv_err:
                p(f"  ERR conv {conv.get('id','?')[:12]}: {type(conv_err).__name__}: {conv_err}")
                sys.stdout.flush()

            # === FOLLOW-UP & ENCERRAMENTO POR INATIVIDADE (para TODAS conversas) ===
            for cid, st in list(_conv_states.items()):
                if st.get('_human_took_over'):
                    continue
                if st.get('waiting_for_client') and st.get('inactivity_start', 0) > 0:
                    elapsed = time.time() - st['inactivity_start']
                    sp = st.get('student_profile')
                    name_fmt = f", {sp['first_name']}" if sp and sp.get('first_name') else ""
                    cur_phone = st.get('phone', '')

                    if st.get('followup_stage', 0) == 0 and elapsed >= FOLLOWUP_1_DELAY:
                        msg1 = FOLLOWUP_1_MSG.format(name=name_fmt)
                        p(f"  [FOLLOWUP-1] [{cur_phone[-4:] if cur_phone else '????'}] {int(elapsed)}s sem resposta")
                        send_message_crm(cid, msg1, buttons=FOLLOWUP_1_BUTTONS)
                        log_to_db(cid, '(inatividade)', msg1, 1.0, 'followup_1')
                        st['followup_stage'] = 1

                    elif st.get('followup_stage', 0) == 1 and elapsed >= CLOSE_DELAY:
                        close_msg = CLOSE_INACTIVITY_MSG.format(name=name_fmt)
                        p(f"  [AUTO-CLOSE] [{cur_phone[-4:] if cur_phone else '????'}] {int(elapsed)}s -> encerrando")
                        msgs_list = st.get('conversation_messages', [])
                        if msgs_list:
                            try:
                                summary = generate_conversation_summary(msgs_list)
                                topic = detect_topic_from_messages(msgs_list)
                                save_memory(cur_phone, sp, topic, summary, 'neutro')
                                tabulate_interaction(msgs_list, sp, cur_phone)
                            except Exception as e:
                                p(f"  Erro ao salvar antes de fechar: {e}")
                        send_message_crm(cid, close_msg, buttons=CLOSE_INACTIVITY_BUTTONS)
                        log_to_db(cid, '(inatividade)', close_msg, 1.0, 'auto_close')
                        close_conversation_crm(cid)
                        st['conversation_messages'] = []
                        conversation_greeted.discard(cid)
                        st['waiting_for_client'] = False
                        st['followup_stage'] = 0
                        st['inactivity_start'] = 0
                        p(f"  [AUTO-CLOSE] Conversa encerrada e estado resetado")

            if cycle % 10 == 0:
                active_count = sum(1 for s in _conv_states.values() if s.get('waiting_for_client'))
                p(f"  ...ativo ({cycle * POLL_INTERVAL}s | {len(processed_msg_ids)} msgs | {len(_conv_states)} convs | {active_count} aguardando)")
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
