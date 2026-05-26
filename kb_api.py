"""
Cockpit IA - Backend API
FastAPI + PostgreSQL + OpenAI
"""
import os
import re
import io
import csv
import sys
import time
import json
import hashlib
import secrets
import threading
import subprocess
import psycopg2
import psycopg2.extras
import requests as http_requests
from contextlib import contextmanager, asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Optional, List
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'media')
os.makedirs(MEDIA_DIR, exist_ok=True)

ALLOWED_MEDIA_TYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'video/mp4', 'video/quicktime', 'video/webm',
    'application/pdf',
}
MAX_UPLOAD_SIZE = 16 * 1024 * 1024


def _run_migrations_background():
    """Roda migrations + indexes em background para não bloquear o startup."""
    try:
        print("[API/BG] Iniciando migrations...", flush=True)
        run_migrations()
        print("[API/BG] Migrations OK", flush=True)
    except Exception as e:
        print(f"[API/BG] ERRO nas migrations: {e}", flush=True)

    try:
        print("[API/BG] Criando indexes...", flush=True)
        _create_indexes()
        print("[API/BG] Indexes OK", flush=True)
    except Exception as e:
        print(f"[API/BG] ERRO nos indexes: {e}", flush=True)


def _create_indexes():
    """Cria indexes com autocommit (necessário para não travar em transação)."""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(autocommit=True)
    cur = conn.cursor()
    cur.execute("SET statement_timeout = 0")

    indexes = [
        ("idx_kb_tema", "knowledge_base", "tema"),
        ("idx_kb_conversation_id", "knowledge_base", "conversation_id"),
        ("idx_il_acao", "ia_interaction_log", "acao"),
        ("idx_il_confianca", "ia_interaction_log", "confianca"),
        ("idx_il_created_at", "ia_interaction_log", "created_at"),
        ("idx_il_conversation_id", "ia_interaction_log", "conversation_id"),
        ("idx_ce_avaliacao", "chat_evaluations", "avaliacao"),
        ("idx_ce_created_at", "chat_evaluations", "created_at"),
        ("idx_ce_prompt_version", "chat_evaluations", "prompt_version_id"),
    ]
    for idx_name, table, cols in indexes:
        try:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({cols})")
            print(f"[API/BG]   Index {idx_name} OK", flush=True)
        except Exception as e:
            print(f"[API/BG]   Index {idx_name} FALHOU: {e}", flush=True)
            conn.rollback()

    cur.close()
    conn.close()


def _agent_watchdog_loop():
    """Verifica heartbeat do agente a cada 60s.
    Se status='online' mas last_beat > 5 min, reinicia automaticamente."""
    import time as _t
    import psycopg2 as _pg
    threshold_min = float(os.environ.get('AGENT_WATCHDOG_THRESHOLD_MIN', '10'))
    interval_s = int(os.environ.get('AGENT_WATCHDOG_INTERVAL_S', '60'))
    while True:
        try:
            _t.sleep(interval_s)
            conn = _pg.connect(**DB_CONFIG)
            cur = conn.cursor()
            cur.execute("""
                SELECT status, EXTRACT(EPOCH FROM (NOW() - last_beat))/60, pid
                FROM agent_heartbeat WHERE id=1
            """)
            row = cur.fetchone()
            cur.close()
            conn.close()
            if not row:
                continue
            status, age_min, pid = row
            if status != 'online':
                continue
            if age_min is None or age_min < threshold_min:
                continue
            print(f"[WATCHDOG] Heartbeat parado ha {age_min:.1f} min (pid={pid}) -> reiniciando agente", flush=True)
            _restart_agent_internal(reason=f'watchdog: heartbeat {age_min:.1f} min de atraso')
        except Exception as _e:
            print(f"[WATCHDOG] erro: {_e}", flush=True)


def _restart_agent_internal(reason='watchdog'):
    """Para e inicia o agente; registra alerta na fila Cockpit."""
    global _agent_process, _agent_log_file
    try:
        if _agent_process is not None and _agent_process.poll() is None:
            try:
                _agent_process.terminate()
                _agent_process.wait(timeout=5)
            except Exception:
                try:
                    _agent_process.kill()
                except Exception:
                    pass
        _agent_process = None
        if _agent_log_file:
            try:
                _agent_log_file.close()
            except Exception:
                pass
            _agent_log_file = None

        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM msg_dedup WHERE processed_at > NOW() - INTERVAL '2 hours'")
                conn.commit()
                cur.close()
        except Exception:
            pass

        env = os.environ.copy()
        env['PHONE_TO_MONITOR'] = _agent_test_phone
        env['PYTHONUNBUFFERED'] = '1'
        _agent_log_file = open(_AGENT_LOG_PATH, 'a', encoding='utf-8', errors='replace')
        _agent_log_file.write(f"\n\n=== [WATCHDOG] Restart automatico: {reason} ===\n")
        _agent_log_file.flush()
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        _agent_process = subprocess.Popen(
            [sys.executable, '-u', 'agente_ao_vivo_v4.py'],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=env,
            stdout=_agent_log_file, stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        print(f"[WATCHDOG] Agente reiniciado pid={_agent_process.pid}", flush=True)

        try:
            from datetime import datetime as _dt
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO agent_config (key, value, updated_at)
                    VALUES ('agent_last_auto_restart', %s, NOW())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """, (json.dumps({
                    'when': _dt.utcnow().isoformat() + 'Z',
                    'reason': reason,
                    'new_pid': _agent_process.pid,
                }),))
                conn.commit()
                cur.close()
        except Exception:
            pass
    except Exception as e:
        print(f"[WATCHDOG] falha ao reiniciar agente: {e}", flush=True)


@asynccontextmanager
async def lifespan(app):
    skip = os.environ.get('SKIP_MIGRATIONS', 'false').lower() == 'true'
    if skip:
        print("[API] Migrations puladas (SKIP_MIGRATIONS=true)", flush=True)
    else:
        t = threading.Thread(target=_run_migrations_background, daemon=True)
        t.start()
        print("[API] Migrations iniciadas em background", flush=True)
    if os.environ.get('AGENT_WATCHDOG_ENABLED', 'true').lower() == 'true':
        wd = threading.Thread(target=_agent_watchdog_loop, daemon=True)
        wd.start()
        print("[API] Watchdog do agente iniciado", flush=True)
    print("[API] Startup completo", flush=True)
    yield

app = FastAPI(title="Cockpit IA - Cruzeiro do Sul", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
security = HTTPBasic(auto_error=False)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 5432)),
    'user': os.environ.get('DB_USER', 'postgres'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'dbname': os.environ.get('DB_NAME', 'log_conversa'),
    'connect_timeout': 10,
}
print(f"[API] DB_CONFIG: host={DB_CONFIG['host']}, port={DB_CONFIG['port']}, user={DB_CONFIG['user']}, dbname={DB_CONFIG['dbname']}", flush=True)
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', '')
AUTH_ENABLED = os.environ.get('AUTH_ENABLED', 'false').lower() == 'true'

DCZ_MSG = 'https://messaging.g1.datacrazy.io/api'
DCZ_TOKEN = os.environ.get('DCZ_TOKEN', '')

TEMAS = [
    'ACESSO_PORTAL', 'ACESSO_APP', 'MATRICULA', 'FINANCEIRO_MENSALIDADE',
    'FINANCEIRO_BOLETO', 'FINANCEIRO_REEMBOLSO', 'ACADEMICO_NOTAS',
    'ACADEMICO_DISCIPLINAS', 'ACADEMICO_ESTAGIO', 'ACADEMICO_TCC',
    'CERTIFICADO_DIPLOMA', 'AULAS_PRESENCIAIS', 'CANCELAMENTO',
    'TRANSFERENCIA', 'COMERCIAL', 'OUTRO'
]

MODEL_PRICING = {
    'gpt-4o':       {'input': 2.50, 'output': 10.00},
    'gpt-4o-mini':  {'input': 0.15, 'output': 0.60},
    'gpt-3.5-turbo':{'input': 0.50, 'output': 1.50},
}

DEFAULT_PROMPT = """Você é a assistente virtual de suporte da Cruzeiro do Sul Educacional.
Seu nome é "Assistente Virtual Cruzeiro do Sul". Você NÃO é um atendente humano.

{student_context}

{memory_context}

{sentiment_context}

## REGRAS ABSOLUTAS:
1. **NUNCA INVENTE** URLs, valores, prazos ou procedimentos que NÃO apareçam nas referências abaixo.
2. **NUNCA forneça dados pessoais** (RGM, e-mail acadêmico, senhas). Só um atendente humano pode fazer isso.
3. Informações CONSISTENTES em MÚLTIPLAS referências são confiáveis.
4. **NUNCA use nomes de atendentes** das referências (ex: Joyce, Camila). Você é a assistente virtual.
5. Se conhecer o nome do aluno, **USE-O** para personalizar.
6. **Quando o aluno responder a uma opção/botão**, interprete NO CONTEXTO do histórico.

## EMPATIA:
- Se o aluno parece frustrado, **valide o sentimento** antes de responder.
- Se é retorno, seja eficiente e direto.
- Se já tentou resolver, priorize escalação.

## 3 NÍVEIS DE CONFIANÇA:
### ALTO (0.8-1.0) → Responda normalmente
### MÉDIO (0.5-0.7) → Responda E ofereça atendente
### BAIXO (0.0-0.4) → Escale para humano

## COMO RESPONDER:
- Dê uma resposta COMPLETA e ÚTIL: inclua o passo a passo ou orientação prática que o aluno precisa para resolver.
- Não seja vago. Se as referências têm detalhes (links, caminhos no portal, prazos), INCLUA.
- Separe em blocos curtos (2-3 frases por bloco) usando quebras de linha para facilitar leitura.
- Use *negrito* para termos-chave (formatação WhatsApp). Máximo 1 emoji por bloco.
- Mantenha entre 3 e 6 frases no total. Nem telegráfico, nem textão.
- Última linha OBRIGATÓRIA (oculta para o aluno): [CONFIANCA:X.X]

## CONVERSAS DE REFERÊNCIA:
{references}

## HISTÓRICO DESTA CONVERSA:
{history}"""


# --- DB helpers ---

@contextmanager
def get_db():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.cursor().execute("SET statement_timeout = 0")
    except Exception as e:
        print(f"[API] ERRO ao conectar DB: {e}", flush=True)
        raise
    try:
        yield conn
    finally:
        conn.close()


def _seed_default_menus(cur):
    """Popula agent_menus com a árvore padrão de menus."""
    def ins(parent_id, level, key, label, text=None, rag=None, order=0):
        cur.execute(
            "INSERT INTO agent_menus (parent_id, level, menu_key, label, response_text, rag_question, sort_order) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (parent_id, level, key, label, text, rag, order))
        return cur.fetchone()[0]

    # L1 categories
    l1_acesso = ins(None, 'L1', 'acesso', 'Acesso Portal/App', 'Sobre *Acesso*, qual sua dúvida?', None, 0)
    l1_fin = ins(None, 'L1', 'financeiro', 'Financeiro', 'Sobre *Financeiro*, qual sua dúvida?', None, 1)
    l1_acad = ins(None, 'L1', 'academico', 'Aulas e Conteúdo', 'Sobre *Aulas e Conteúdo*, qual sua dúvida?', None, 2)
    l1_doc = ins(None, 'L1', 'documentos', 'Documentos', 'Sobre *Documentos*, o que precisa?', None, 3)
    l1_remat = ins(None, 'L1', 'rematricula', 'Rematrícula', 'Sobre *Rematrícula*, qual sua dúvida?', None, 4)

    # Acesso -> L2
    l2_primeiro = ins(l1_acesso, 'L2', 'primeiro acesso', 'Primeiro acesso', 'Sobre *Primeiro Acesso*:', None, 0)
    ins(l1_acesso, 'leaf', 'esqueci minha senha', 'Esqueci minha senha', None, 'esqueci minha senha do portal como redefinir', 1)
    ins(l1_acesso, 'leaf', 'app duda', 'App Duda', None, 'como baixar e acessar o app Duda', 2)
    ins(l1_acesso, 'leaf', 'blackboard / ava', 'Blackboard / AVA', None, 'como acessar o Blackboard ou ambiente virtual de aprendizagem', 3)
    # Primeiro acesso -> leaf
    ins(l2_primeiro, 'leaf', 'não recebi credenciais', 'Não recebi credenciais', None, 'não recebi meus dados de acesso credenciais do portal', 0)
    ins(l2_primeiro, 'leaf', 'onde me cadastro', 'Onde me cadastro', None, 'onde faço cadastro para acessar o portal do aluno', 1)
    ins(l2_primeiro, 'leaf', 'email acadêmico', 'Email acadêmico', None, 'qual meu email acadêmico e como acessar', 2)

    # Financeiro -> L2
    l2_boleto = ins(l1_fin, 'L2', 'boleto / pagamento', 'Boleto / Pagamento', 'Sobre *Boleto / Pagamento*:', None, 0)
    l2_mensal = ins(l1_fin, 'L2', 'mensalidade / valores', 'Mensalidade / Valores', 'Sobre *Mensalidade / Valores*:', None, 1)
    l2_negoc = ins(l1_fin, 'L2', 'negociar / parcelar', 'Negociar / Parcelar', 'Sobre *Negociação*:', None, 2)
    ins(l1_fin, 'leaf', 'reembolso', 'Reembolso', None, 'como solicitar reembolso de pagamento', 3)
    # Boleto -> leaf
    ins(l2_boleto, 'leaf', 'segunda via do boleto', 'Segunda via do boleto', None, 'como gerar segunda via do boleto de pagamento', 0)
    ins(l2_boleto, 'leaf', 'pagar com pix', 'Pagar com PIX', None, 'como pagar a mensalidade com PIX', 1)
    ins(l2_boleto, 'leaf', 'boleto vencido', 'Boleto vencido', None, 'meu boleto venceu o que fazer como pagar boleto vencido', 2)
    # Mensalidade -> leaf
    ins(l2_mensal, 'leaf', 'valor da mensalidade', 'Valor da mensalidade', None, 'qual o valor da mensalidade e como consultar valores', 0)
    ins(l2_mensal, 'leaf', 'desconto / bolsa', 'Desconto / Bolsa', None, 'como conseguir desconto ou bolsa na mensalidade', 1)
    ins(l2_mensal, 'leaf', 'reajuste de mensalidade', 'Reajuste de mensalidade', None, 'por que a mensalidade teve reajuste e como contestar', 2)
    # Negociar -> leaf
    ins(l2_negoc, 'leaf', 'parcelar dívida', 'Parcelar dívida', None, 'como parcelar mensalidades em atraso', 0)
    ins(l2_negoc, 'leaf', 'fazer acordo', 'Fazer acordo', None, 'como fazer acordo de pagamento de dívida', 1)
    ins(l2_negoc, 'leaf', 'estou inadimplente', 'Estou inadimplente', None, 'estou inadimplente o que acontece como regularizar', 2)

    # Acadêmico -> L2/leaf
    ins(l1_acad, 'leaf', 'início das aulas', 'Início das aulas', None, 'quando começam as aulas do semestre', 0)
    ins(l1_acad, 'leaf', 'disciplinas / grade', 'Disciplinas / Grade', None, 'como ver minhas disciplinas e grade curricular', 1)
    l2_provas = ins(l1_acad, 'L2', 'provas / atividades', 'Provas / Atividades', 'Sobre *Provas e Atividades*:', None, 2)
    ins(l1_acad, 'leaf', 'material didático', 'Material didático', None, 'como acessar o material didático das aulas', 3)
    # Provas -> leaf
    ins(l2_provas, 'leaf', 'datas das provas', 'Datas das provas', None, 'quando são as datas das provas do semestre', 0)
    ins(l2_provas, 'leaf', 'prazo de atividades', 'Prazo de atividades', None, 'qual o prazo para entrega de atividades', 1)
    ins(l2_provas, 'leaf', 'ver minhas notas', 'Ver minhas notas', None, 'como ver minhas notas e conceitos', 2)

    # Documentos -> leaf
    ins(l1_doc, 'leaf', 'declaração de matrícula', 'Declaração de matrícula', None, 'como emitir declaração de matrícula ou vínculo', 0)
    ins(l1_doc, 'leaf', 'histórico escolar', 'Histórico escolar', None, 'como solicitar histórico escolar', 1)
    ins(l1_doc, 'leaf', 'enviar documentos', 'Enviar documentos', None, 'como enviar documentos para a secretaria', 2)

    # Rematrícula -> leaf
    ins(l1_remat, 'leaf', 'como rematricular', 'Como rematricular', None, 'como fazer a rematrícula para o próximo semestre', 0)
    ins(l1_remat, 'leaf', 'prazo de rematrícula', 'Prazo de rematrícula', None, 'qual o prazo para rematrícula do semestre', 1)


def run_migrations():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(autocommit=True)
    cur = conn.cursor()
    cur.execute("SET statement_timeout = 0")

    ddl_statements = [
        ("prompt_versions", """CREATE TABLE IF NOT EXISTS prompt_versions (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, system_prompt TEXT NOT NULL,
            is_active BOOLEAN DEFAULT FALSE, model TEXT DEFAULT 'gpt-4o-mini',
            temperature FLOAT DEFAULT 0.2, max_tokens INT DEFAULT 400,
            notes TEXT DEFAULT '', created_at TIMESTAMP DEFAULT NOW())"""),
        ("chat_evaluations", """CREATE TABLE IF NOT EXISTS chat_evaluations (
            id SERIAL PRIMARY KEY, pergunta TEXT NOT NULL, resposta_ia TEXT,
            confianca FLOAT, avaliacao TEXT NOT NULL, resposta_corrigida TEXT,
            prompt_version_id INT, model TEXT, latency_ms INT, tokens_used INT,
            created_at TIMESTAMP DEFAULT NOW())"""),
        ("kb_whatsapp_buttons", """DO $$ BEGIN
            ALTER TABLE knowledge_base ADD COLUMN whatsapp_buttons TEXT DEFAULT NULL;
            EXCEPTION WHEN duplicate_column THEN NULL; END $$"""),
        ("kb_media_attachments", """DO $$ BEGIN
            ALTER TABLE knowledge_base ADD COLUMN media_attachments TEXT DEFAULT NULL;
            EXCEPTION WHEN duplicate_column THEN NULL; END $$"""),
        ("student_memory", """CREATE TABLE IF NOT EXISTS student_memory (
            id SERIAL PRIMARY KEY, phone VARCHAR(20) UNIQUE NOT NULL,
            lead_id VARCHAR(100), student_name TEXT, cpf VARCHAR(14),
            last_topic TEXT, last_summary TEXT, interaction_count INT DEFAULT 0,
            sentiment_history TEXT DEFAULT '', preferences JSONB DEFAULT '{}',
            notes TEXT DEFAULT '', first_contact_at TIMESTAMP DEFAULT NOW(),
            last_contact_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW())"""),
        ("interaction_summary", """CREATE TABLE IF NOT EXISTS interaction_summary (
            id SERIAL PRIMARY KEY, phone VARCHAR(20), lead_id VARCHAR(100),
            student_name TEXT, tema VARCHAR(50), subtema VARCHAR(100),
            sentimento VARCHAR(20), resolvido VARCHAR(20), nps_implicito INT,
            resumo TEXT, mensagens_count INT DEFAULT 0, created_at TIMESTAMP DEFAULT NOW())"""),
        ("agent_alerts", """CREATE TABLE IF NOT EXISTS agent_alerts (
            id SERIAL PRIMARY KEY, title VARCHAR(200) NOT NULL, message TEXT NOT NULL,
            category VARCHAR(50) DEFAULT 'geral', active BOOLEAN DEFAULT TRUE,
            priority INT DEFAULT 0, starts_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP, created_at TIMESTAMP DEFAULT NOW())"""),
        ("alerts_display_mode", """DO $$ BEGIN
            ALTER TABLE agent_alerts ADD COLUMN display_mode VARCHAR(20) DEFAULT 'context';
            EXCEPTION WHEN duplicate_column THEN NULL; END $$"""),
        ("agent_menus", """CREATE TABLE IF NOT EXISTS agent_menus (
            id SERIAL PRIMARY KEY, parent_id INT REFERENCES agent_menus(id) ON DELETE CASCADE,
            level VARCHAR(10) NOT NULL, menu_key VARCHAR(100) NOT NULL,
            label VARCHAR(100) NOT NULL, response_text TEXT, rag_question TEXT,
            sort_order INT DEFAULT 0, active BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW())"""),
        ("interaction_summary_conv_id", """DO $$ BEGIN
            ALTER TABLE interaction_summary ADD COLUMN conv_id VARCHAR(50);
            EXCEPTION WHEN duplicate_column THEN NULL; END $$"""),
    ]

    for name, sql in ddl_statements:
        try:
            cur.execute(sql)
            print(f"[MIGRATION] {name} OK", flush=True)
        except Exception as e:
            print(f"[MIGRATION] {name} FALHOU: {e}", flush=True)

    try:
        cur.execute("SELECT count(*) FROM agent_menus")
        if cur.fetchone()[0] == 0:
            _seed_default_menus(cur)
            print("[MIGRATION] Menus seed OK", flush=True)
    except Exception as e:
        print(f"[MIGRATION] Menus seed FALHOU: {e}", flush=True)

    try:
        cur.execute("SELECT count(*) FROM prompt_versions")
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT INTO prompt_versions (name, system_prompt, is_active, model, temperature, max_tokens, notes)
                VALUES (%s, %s, true, 'gpt-4o-mini', 0.2, 400, 'Prompt inicial v1')
            """, ('Prompt v1 - Original', DEFAULT_PROMPT))
            print("[MIGRATION] Prompt seed OK", flush=True)
    except Exception as e:
        print(f"[MIGRATION] Prompt seed FALHOU: {e}", flush=True)

    cur.close()
    conn.close()
    print("[MIGRATION] Tables OK", flush=True)




# --- Auth ---

def check_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    if not AUTH_ENABLED:
        return True
    if not credentials:
        raise HTTPException(401, "Auth required", headers={"WWW-Authenticate": "Basic"})
    if not (secrets.compare_digest(credentials.username, ADMIN_USER) and
            secrets.compare_digest(credentials.password, ADMIN_PASS)):
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Basic"})
    return True


# --- OpenAI helpers ---

def generate_embedding(text: str) -> list[float]:
    client = OpenAI(api_key=OPENAI_API_KEY)
    for attempt in range(3):
        try:
            resp = client.embeddings.create(input=text[:2000], model='text-embedding-3-small', dimensions=256)
            return resp.data[0].embedding
        except Exception as e:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
                continue
            raise


def rag_search(question: str, top_k: int = 5):
    embedding = generate_embedding(question)
    emb_str = ','.join(str(x) for x in embedding)
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""
            SELECT * FROM (
                SELECT id, pergunta_aluno, resposta_atendente, tema, whatsapp_buttons,
                       cosine_similarity(embedding, ARRAY[{emb_str}]::float8[]) as score
                FROM knowledge_base WHERE embedding IS NOT NULL
            ) sub ORDER BY score DESC LIMIT {top_k}
        """)
        return cur.fetchall()


def build_refs(results, min_score=0.5):
    refs = ''
    for i, r in enumerate(results):
        if float(r['score']) < min_score:
            continue
        refs += f"\n--- Ref {i+1} (tema: {r['tema'] or 'N/A'}, sim: {float(r['score']):.2f}) ---\n"
        refs += f"Pergunta: {str(r['pergunta_aluno'])[:400]}\nResposta: {str(r['resposta_atendente'])[:600]}\n"
    return refs or "Nenhuma referência encontrada."


def call_llm(question: str, system_prompt: str, model: str = 'gpt-4o-mini',
             temperature: float = 0.2, max_tokens: int = 400,
             history: Optional[List[dict]] = None,
             image_b64: str = None, image_mime: str = None):
    client = OpenAI(api_key=OPENAI_API_KEY)
    if image_b64:
        system_prompt += (
            "\n\n## IMAGEM RECEBIDA:\n"
            "O aluno enviou uma imagem. Analise-a cuidadosamente e use o conteúdo visual "
            "para complementar sua resposta. Se for um print de tela, identifique o que aparece "
            "e oriente o aluno. Se não conseguir interpretar, peça mais detalhes.\n"
        )
    messages = [{'role': 'system', 'content': system_prompt}]
    if history:
        for h in history[-6:]:
            role = 'user' if h.get('role') == 'user' else 'assistant'
            messages.append({'role': role, 'content': h.get('text', '')})
    if image_b64:
        user_content = [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {
                "url": f"data:{image_mime or 'image/jpeg'};base64,{image_b64}",
                "detail": "low"
            }}
        ]
        messages.append({'role': 'user', 'content': user_content})
    else:
        messages.append({'role': 'user', 'content': question})
    t0 = time.time()
    last_err = None
    for attempt in range(3):
        try:
            chat = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens, temperature=temperature
            )
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
                continue
            raise
    latency = int((time.time() - t0) * 1000)
    resp_text = chat.choices[0].message.content
    usage = chat.usage

    cm = re.search(r'\[CONFIANCA:(\d+\.?\d*)\]', resp_text)
    confidence = float(cm.group(1)) if cm else 0.0
    clean = re.sub(r'\[CONFIANCA:\d+\.?\d*\]', '', resp_text).strip()

    pricing = MODEL_PRICING.get(model, MODEL_PRICING['gpt-4o-mini'])
    cost = (usage.prompt_tokens * pricing['input'] + usage.completion_tokens * pricing['output']) / 1_000_000

    return {
        'resposta': clean,
        'confianca': confidence,
        'latency_ms': latency,
        'tokens_prompt': usage.prompt_tokens,
        'tokens_completion': usage.completion_tokens,
        'tokens_total': usage.total_tokens,
        'cost_usd': round(cost, 6),
        'model': model,
    }


def get_active_prompt():
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM prompt_versions WHERE is_active = true LIMIT 1")
        row = cur.fetchone()
        if row:
            return row
    return {'id': 0, 'system_prompt': DEFAULT_PROMPT, 'model': 'gpt-4o-mini', 'temperature': 0.3, 'max_tokens': 600}


# --- Models ---

class QACreate(BaseModel):
    pergunta: str
    resposta: str
    tema: Optional[str] = None
    whatsapp_buttons: Optional[str] = None
    media_attachments: Optional[str] = None

class QAUpdate(BaseModel):
    pergunta: Optional[str] = None
    resposta: Optional[str] = None
    tema: Optional[str] = None
    whatsapp_buttons: Optional[str] = None
    media_attachments: Optional[str] = None

class PlaygroundRequest(BaseModel):
    pergunta: str
    model: str = 'gpt-4o-mini'
    system_prompt: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 400
    prompt_id: Optional[int] = None

class PromptCreate(BaseModel):
    name: str
    system_prompt: str
    model: str = 'gpt-4o-mini'
    temperature: float = 0.2
    max_tokens: int = 400
    notes: str = ''

class PromptUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    notes: Optional[str] = None

class EvalCreate(BaseModel):
    pergunta: str
    resposta_ia: str
    confianca: float
    avaliacao: str
    resposta_corrigida: Optional[str] = None
    prompt_version_id: Optional[int] = None
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    tokens_used: Optional[int] = None

class TestRequest(BaseModel):
    pergunta: str
    model: Optional[str] = None
    prompt_id: Optional[int] = None
    history: Optional[List[dict]] = None
    phone: Optional[str] = None
    image_b64: Optional[str] = None
    image_mime: Optional[str] = None


# --- Routes: Static ---

@app.get("/api/health")
async def health_check():
    import traceback
    try:
        print("[API] health_check: tentando conectar ao DB...", flush=True)
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
        print("[API] health_check: DB OK", flush=True)
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[API] health_check ERRO: {e}\n{tb}", flush=True)
        return JSONResponse(status_code=500, content={"status": "error", "db": str(e), "detail": tb})


@app.get("/")
async def serve_frontend():
    return FileResponse("kb_admin.html")


# --- Routes: Stats ---

@app.get("/api/stats")
async def get_stats():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = 'knowledge_base'")
            row = cur.fetchone()
            total = max(row[0], 0) if row else 0
            cur.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = 'ia_interaction_log'")
            row = cur.fetchone()
            interactions = max(row[0], 0) if row else 0
            with_emb = 0
            temas = []
            sem_tema = 0
            actions = []
            avg_conf = 0
            try:
                cur.execute("SELECT count(*) FROM knowledge_base WHERE embedding IS NOT NULL")
                with_emb = cur.fetchone()[0]
            except Exception:
                pass
            try:
                cur.execute("SELECT tema, count(*) FROM knowledge_base WHERE tema IS NOT NULL GROUP BY tema ORDER BY count(*) DESC LIMIT 50")
                temas = [{'tema': r[0], 'count': r[1]} for r in cur.fetchall()]
            except Exception:
                pass
            try:
                cur.execute("SELECT acao, count(*) FROM ia_interaction_log GROUP BY acao ORDER BY count(*) DESC")
                actions = [{'action': r[0], 'count': r[1]} for r in cur.fetchall()]
            except Exception:
                pass
            try:
                cur.execute("SELECT avg(confianca) FROM ia_interaction_log WHERE confianca IS NOT NULL")
                avg_conf = cur.fetchone()[0] or 0
            except Exception:
                pass
            return {
                'total_qa': total, 'with_embedding': with_emb, 'without_embedding': total - with_emb,
                'temas': temas, 'sem_tema': sem_tema, 'total_interactions': interactions,
                'interactions_by_action': actions, 'avg_confidence': round(float(avg_conf), 3)
            }
    except Exception as e:
        print(f"[API] ERRO em /api/stats: {e}", flush=True)
        return {
            'total_qa': 0, 'with_embedding': 0, 'without_embedding': 0,
            'temas': [], 'sem_tema': 0, 'total_interactions': 0,
            'interactions_by_action': [], 'avg_confidence': 0,
            'error': str(e)
        }


@app.get("/api/temas")
async def get_temas():
    return TEMAS


@app.get("/api/models")
async def get_models():
    return [
        {'id': 'gpt-4o', 'name': 'GPT-4o', 'cost_input': 2.50, 'cost_output': 10.00},
        {'id': 'gpt-4o-mini', 'name': 'GPT-4o Mini', 'cost_input': 0.15, 'cost_output': 0.60},
        {'id': 'gpt-3.5-turbo', 'name': 'GPT-3.5 Turbo', 'cost_input': 0.50, 'cost_output': 1.50},
    ]


# --- Routes: Q&A CRUD ---

@app.get("/api/qa")
async def list_qa(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
                  tema: Optional[str] = None, search: Optional[str] = None,
                  sort: str = Query('recent', pattern='^(recent|oldest|tema)$')):
    try:
        offset = (page - 1) * per_page
        conditions, params = [], []
        if tema:
            conditions.append("tema = %s"); params.append(tema)
        if search:
            conditions.append("(pergunta_aluno ILIKE %s OR resposta_atendente ILIKE %s)")
            params.extend([f'%{search}%', f'%{search}%'])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        order = {'recent': 'created_at DESC', 'oldest': 'created_at ASC', 'tema': 'tema ASC, created_at DESC'}[sort]
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if conditions:
                cur.execute(f"SELECT count(*) as cnt FROM knowledge_base {where}", params)
                total = cur.fetchone()['cnt']
            else:
                cur.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = 'knowledge_base'")
                row = cur.fetchone()
                total = max(row['reltuples'], 0) if row else 0
            cur.execute(f"""SELECT id, conversation_id, pergunta_aluno, resposta_atendente, tema,
                       embedding IS NOT NULL as has_embedding, whatsapp_buttons, media_attachments, created_at FROM knowledge_base {where}
                       ORDER BY {order} LIMIT %s OFFSET %s""", params + [per_page, offset])
            items = cur.fetchall()
            for item in items:
                if item['created_at']: item['created_at'] = item['created_at'].isoformat()
            return {'items': items, 'total': total, 'page': page, 'per_page': per_page, 'pages': max((total + per_page - 1) // per_page, 1)}
    except Exception as e:
        print(f"[API] ERRO em /api/qa: {e}", flush=True)
        return {'items': [], 'total': 0, 'page': 1, 'per_page': per_page, 'pages': 0, 'error': str(e)}


@app.get("/api/qa/{qa_id}")
async def get_qa(qa_id: int):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, conversation_id, pergunta_aluno, resposta_atendente, tema, embedding IS NOT NULL as has_embedding, whatsapp_buttons, media_attachments, created_at FROM knowledge_base WHERE id = %s", (qa_id,))
        item = cur.fetchone()
        if not item: raise HTTPException(404, "Q&A não encontrado")
        if item['created_at']: item['created_at'] = item['created_at'].isoformat()
        return item


@app.post("/api/qa")
async def create_qa(data: QACreate):
    if not data.pergunta.strip() or not data.resposta.strip():
        raise HTTPException(400, "Pergunta e resposta são obrigatórias")
    try:
        embedding = generate_embedding(data.pergunta)
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar embedding: {e}")
    with get_db() as conn:
        cur = conn.cursor()
        emb_str = '{' + ','.join(str(x) for x in embedding) + '}'
        cur.execute("INSERT INTO knowledge_base (pergunta_aluno, resposta_atendente, tema, embedding, conversation_id, whatsapp_buttons, media_attachments) VALUES (%s,%s,%s,%s::float8[],%s,%s,%s) RETURNING id",
                    (data.pergunta.strip(), data.resposta.strip(), data.tema, emb_str, 'manual', data.whatsapp_buttons, data.media_attachments))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {'id': new_id, 'message': 'Q&A criado com embedding'}


@app.put("/api/qa/{qa_id}")
async def update_qa(qa_id: int, data: QAUpdate):
    updates, params = [], []
    if data.pergunta is not None: updates.append("pergunta_aluno = %s"); params.append(data.pergunta.strip())
    if data.resposta is not None: updates.append("resposta_atendente = %s"); params.append(data.resposta.strip())
    if data.tema is not None: updates.append("tema = %s"); params.append(data.tema)
    if data.whatsapp_buttons is not None: updates.append("whatsapp_buttons = %s"); params.append(data.whatsapp_buttons if data.whatsapp_buttons else None)
    if data.media_attachments is not None: updates.append("media_attachments = %s"); params.append(data.media_attachments if data.media_attachments else None)
    if not updates: raise HTTPException(400, "Nenhum campo para atualizar")
    regen = data.pergunta is not None
    if regen:
        try:
            emb = generate_embedding(data.pergunta)
            updates.append("embedding = %s::float8[]"); params.append('{' + ','.join(str(x) for x in emb) + '}')
        except Exception: pass
    params.append(qa_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE knowledge_base SET {', '.join(updates)} WHERE id = %s", params)
        if cur.rowcount == 0: raise HTTPException(404)
        conn.commit()
        return {'message': 'Atualizado', 'embedding_regenerated': regen}


@app.delete("/api/qa/{qa_id}")
async def delete_qa(qa_id: int):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM knowledge_base WHERE id = %s", (qa_id,))
        if cur.rowcount == 0: raise HTTPException(404)
        conn.commit()
        return {'message': 'Deletado'}


# --- Flow Logic (Hybrid Bot+IA) ---

GREETING_WORDS = {
    'oi', 'olá', 'ola', 'oii', 'oiii', 'hello', 'hi', 'hey', 'fala', 'salve', 'opa', 'eae',
    'bom dia', 'boa tarde', 'boa noite', 'tudo bem', 'tudo bom', 'como vai',
    'oi bom dia', 'oi boa tarde', 'oi boa noite',
}

RESOLVED_WORDS = {'sim resolveu', 'resolveu', 'sim obrigado', 'sim obrigada', 'sim!', 'resolvido', 'era isso'}
ESCALATE_WORDS = {'falar com atendente', 'atendente', 'humano', 'falar com alguem', 'transferir'}
CLOSING_WORDS = {'obrigado', 'obrigada', 'valeu', 'vlw', 'tchau', 'até mais', 'ate mais', 'brigado', 'brigada'}

GREETING_MENU = {
    'type': 'flow_menu',
    'text': 'Olá! Bem-vindo ao Suporte ao Aluno da *Cruzeiro do Sul* 😊\n\nComo posso te ajudar?',
    'buttons': [
        {'id': 'acesso', 'title': '🔑 Acesso Portal/App'},
        {'id': 'financeiro', 'title': '💰 Financeiro'},
        {'id': 'academico', 'title': '📚 Aulas e Conteúdo'},
    ],
    'buttons2': [
        {'id': 'documentos', 'title': '📄 Documentos'},
        {'id': 'rematricula', 'title': '🔄 Rematrícula'},
        {'id': 'atendente', 'title': '👤 Falar com atendente'},
    ]
}

SUBMENU = {
    'financeiro': {
        'type': 'flow_submenu',
        'text': 'Sobre *Financeiro*, qual sua dúvida?',
        'buttons': [
            {'id': 'boleto', 'title': '🧾 Boleto / Pagamento'},
            {'id': 'mensalidade', 'title': '💳 Mensalidade / Valores'},
            {'id': 'negociacao', 'title': '🤝 Negociação / Parcelamento'},
        ],
        'buttons2': [
            {'id': 'reembolso', 'title': '💸 Reembolso'},
            {'id': 'fin_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
    'acesso': {
        'type': 'flow_submenu',
        'text': 'Sobre *Acesso*, qual sua dúvida?',
        'buttons': [
            {'id': 'primeiro_acesso', 'title': '🆕 Primeiro acesso'},
            {'id': 'esqueci_senha', 'title': '🔑 Esqueci minha senha'},
            {'id': 'app_duda', 'title': '📱 App Duda'},
        ],
        'buttons2': [
            {'id': 'blackboard', 'title': '🖥️ Blackboard / AVA'},
            {'id': 'acesso_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
    'academico': {
        'type': 'flow_submenu',
        'text': 'Sobre *Aulas e Conteúdo*, qual sua dúvida?',
        'buttons': [
            {'id': 'inicio_aulas', 'title': '📅 Início das aulas'},
            {'id': 'disciplinas', 'title': '📖 Disciplinas / Grade'},
            {'id': 'provas', 'title': '📝 Provas / Atividades'},
        ],
        'buttons2': [
            {'id': 'material', 'title': '📚 Material didático'},
            {'id': 'acad_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
    'documentos': {
        'type': 'flow_submenu',
        'text': 'Sobre *Documentos*, o que precisa?',
        'buttons': [
            {'id': 'declaracao', 'title': '📋 Declaração de matrícula'},
            {'id': 'historico', 'title': '📄 Histórico escolar'},
            {'id': 'enviar_doc', 'title': '📎 Enviar documentos'},
        ],
        'buttons2': [
            {'id': 'doc_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
    'rematricula': {
        'type': 'flow_submenu',
        'text': 'Sobre *Rematrícula*, qual sua dúvida?',
        'buttons': [
            {'id': 'como_rematricular', 'title': '🔄 Como rematricular'},
            {'id': 'prazo_rematricula', 'title': '📅 Prazo de rematrícula'},
            {'id': 'rematricula_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
}

SUBMENU_L3 = {
    'boleto': {
        'type': 'flow_submenu',
        'text': 'Sobre *Boleto / Pagamento*:',
        'buttons': [
            {'id': 'boleto_2via', 'title': '📄 Segunda via do boleto'},
            {'id': 'boleto_pix', 'title': '💠 Pagar com PIX'},
            {'id': 'boleto_vencido', 'title': '⚠️ Boleto vencido'},
        ],
        'buttons2': [
            {'id': 'boleto_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
    'mensalidade': {
        'type': 'flow_submenu',
        'text': 'Sobre *Mensalidade / Valores*:',
        'buttons': [
            {'id': 'mens_valor', 'title': '💲 Valor da mensalidade'},
            {'id': 'mens_desconto', 'title': '🏷️ Desconto / Bolsa'},
            {'id': 'mens_reajuste', 'title': '📈 Reajuste de mensalidade'},
        ],
        'buttons2': [
            {'id': 'mens_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
    'negociacao': {
        'type': 'flow_submenu',
        'text': 'Sobre *Negociação / Parcelamento*:',
        'buttons': [
            {'id': 'neg_parcelar', 'title': '💳 Parcelar dívida'},
            {'id': 'neg_acordo', 'title': '🤝 Fazer acordo'},
            {'id': 'neg_inadimplente', 'title': '🔒 Estou inadimplente'},
        ],
        'buttons2': [
            {'id': 'neg_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
    'primeiro_acesso': {
        'type': 'flow_submenu',
        'text': 'Sobre *Primeiro Acesso*:',
        'buttons': [
            {'id': 'pa_credenciais', 'title': '📧 Não recebi credenciais'},
            {'id': 'pa_onde', 'title': '🌐 Onde me cadastro'},
            {'id': 'pa_email', 'title': '📨 Email acadêmico'},
        ],
        'buttons2': [
            {'id': 'pa_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
    'provas': {
        'type': 'flow_submenu',
        'text': 'Sobre *Provas e Atividades*:',
        'buttons': [
            {'id': 'prova_data', 'title': '📅 Datas das provas'},
            {'id': 'prova_prazo', 'title': '⏰ Prazo de atividades'},
            {'id': 'prova_nota', 'title': '📊 Ver minhas notas'},
        ],
        'buttons2': [
            {'id': 'prova_atendente', 'title': '👤 Falar com atendente'},
        ]
    },
}

SUBMENU_TO_QUESTION = {
    'boleto_2via': 'como gerar segunda via do boleto de pagamento',
    'boleto_pix': 'como pagar a mensalidade com PIX',
    'boleto_vencido': 'meu boleto venceu o que fazer como pagar boleto vencido',
    'mens_valor': 'qual o valor da mensalidade e como consultar valores',
    'mens_desconto': 'como conseguir desconto ou bolsa na mensalidade',
    'mens_reajuste': 'por que a mensalidade teve reajuste e como contestar',
    'neg_parcelar': 'como parcelar mensalidades em atraso',
    'neg_acordo': 'como fazer acordo de pagamento de dívida',
    'neg_inadimplente': 'estou inadimplente o que acontece como regularizar',
    'reembolso': 'como solicitar reembolso de pagamento',
    'pa_credenciais': 'não recebi meus dados de acesso credenciais do portal',
    'pa_onde': 'onde faço cadastro para acessar o portal do aluno',
    'pa_email': 'qual meu email acadêmico e como acessar',
    'esqueci_senha': 'esqueci minha senha do portal como redefinir',
    'app_duda': 'como baixar e acessar o app Duda',
    'blackboard': 'como acessar o Blackboard ou ambiente virtual de aprendizagem',
    'inicio_aulas': 'quando começam as aulas do semestre',
    'disciplinas': 'como ver minhas disciplinas e grade curricular',
    'prova_data': 'quando são as datas das provas do semestre',
    'prova_prazo': 'qual o prazo para entrega de atividades',
    'prova_nota': 'como ver minhas notas e conceitos',
    'material': 'como acessar o material didático das aulas',
    'declaracao': 'como emitir declaração de matrícula ou vínculo',
    'historico': 'como solicitar histórico escolar',
    'enviar_doc': 'como enviar documentos para a secretaria',
    'como_rematricular': 'como fazer a rematrícula para o próximo semestre',
    'prazo_rematricula': 'qual o prazo para rematrícula do semestre',
}

MAIN_MENU_KEYS = {
    'acesso portal/app': 'acesso', 'acesso': 'acesso',
    'financeiro': 'financeiro',
    'aulas e conteúdo': 'academico', 'aulas': 'academico',
    'documentos': 'documentos',
    'rematrícula': 'rematricula', 'rematricula': 'rematricula',
}

L2_TO_L3_KEYS = {
    'boleto / pagamento': 'boleto', 'boleto': 'boleto',
    'mensalidade / valores': 'mensalidade', 'mensalidade': 'mensalidade',
    'negociação / parcelamento': 'negociacao', 'negociacao / parcelamento': 'negociacao',
    'primeiro acesso': 'primeiro_acesso',
    'provas / atividades': 'provas', 'provas': 'provas',
}


def generate_flow_buttons(pergunta: str, confianca: float, history: list = None):
    q = pergunta.lower().strip().rstrip('!?.,').strip()
    stripped = q.replace('🔑 ', '').replace('💰 ', '').replace('📚 ', '').replace('📄 ', '').replace('🔄 ', '').replace('👤 ', '').replace('🧾 ', '').replace('💳 ', '').replace('🤝 ', '').replace('💸 ', '').replace('🆕 ', '').replace('📱 ', '').replace('🖥️ ', '').replace('📅 ', '').replace('📖 ', '').replace('📝 ', '').replace('📚 ', '').replace('📋 ', '').replace('📎 ', '').replace('💬 ', '').replace('✅ ', '').replace('💬', '').replace('✅', '').lower().strip()

    # Greeting detection
    words = q.split()
    is_greet = q in GREETING_WORDS or (len(words) <= 3 and any(w in GREETING_WORDS for w in words))
    if is_greet and (not history or len(history) <= 1):
        return GREETING_MENU

    # Pre-check: if text matches a leaf button (SUBMENU_TO_QUESTION), let RAG handle it
    for cat in list(SUBMENU.values()) + list(SUBMENU_L3.values()):
        for b in cat.get('buttons', []) + cat.get('buttons2', []):
            if b['id'] in SUBMENU_TO_QUESTION:
                clean = b['title'].lower()
                for em in '🔑💰📚📄🔄👤🧾💳🤝💸🆕📱🖥️📅📖📝📋📎💲🏷️📈🔒💠⚠️📧🌐📨📊⏰':
                    clean = clean.replace(em + ' ', '').replace(em, '')
                if clean.strip() and clean.strip() in stripped:
                    return None

    # Main menu -> sub-menu (level 2)
    for menu_key, submenu_key in MAIN_MENU_KEYS.items():
        if menu_key in stripped:
            return SUBMENU[submenu_key]

    # Level 2 -> Level 3 sub-menu
    for l2_key, l3_key in L2_TO_L3_KEYS.items():
        if l2_key in stripped and l3_key in SUBMENU_L3:
            return SUBMENU_L3[l3_key]

    # "Sim, resolveu" / resolved
    if any(w in q for w in RESOLVED_WORDS) or (q in ('sim', 'si', 'sím') and history and len(history) >= 2):
        return {
            'type': 'flow_resolved',
            'text': 'Que bom que pude ajudar! 😊\n\nTem mais alguma dúvida?',
            'buttons': [
                {'id': 'outra', 'title': '💬 Tenho outra dúvida'},
                {'id': 'encerrar', 'title': '✅ Não, obrigado!'},
            ]
        }

    # Explicit escalation request
    if any(w in q for w in ESCALATE_WORDS):
        return {
            'type': 'flow_escalate',
            'text': 'Vou te transferir para um atendente agora. Por favor, aguarde um momento.',
            'buttons': [
                {'id': 'outra', 'title': '💬 Tenho outra dúvida'},
            ]
        }

    # Retention (cancelamento / trancamento) — somente intenção real
    retention_phrases = [
        'quero cancelar', 'quero trancar', 'vou cancelar', 'vou trancar',
        'cancelar meu curso', 'cancelar minha matrícula', 'cancelar minha matricula',
        'trancar meu curso', 'trancar minha matrícula', 'trancar minha matricula',
        'cancelar o curso', 'trancar o curso', 'quero desistir', 'vou desistir',
        'preciso cancelar', 'preciso trancar', 'desejo cancelar', 'desejo trancar',
        'quero realizar o cancelamento', 'quero fazer o cancelamento',
        'cancelar matrícula', 'cancelar matricula', 'trancar matrícula', 'trancar matricula',
    ]
    retention_question_words = [
        'prazo', 'data', 'quando', 'como funciona', 'como solicitar', 'quanto custa',
        'valor', 'taxa', 'multa', 'processo', 'procedimento', 'posso solicitar',
        'até que', 'ate que', 'qual o prazo',
    ]
    is_question_about = any(w in q for w in retention_question_words)
    if not is_question_about and any(w in q for w in retention_phrases):
        return {
            'type': 'flow_retention',
            'text': 'Entendi sua situação. Vou te encaminhar para nosso consultor especializado que poderá te ajudar. Um momento, por favor!',
            'buttons': []
        }

    # "Não, obrigado" / closing
    close_match = any(w in q for w in CLOSING_WORDS) or q in ('não obrigado', 'nao obrigado', 'encerrar', 'não', 'nao',
                                                               'não preciso', 'nao preciso', 'pode encerrar', 'fechar')
    if close_match and history and len(history) >= 2:
        already_closed = any('Obrigado pelo contato' in h.get('text', '') or 'Até logo' in h.get('text', '') for h in history if h.get('role') == 'bot')
        if already_closed:
            return {
                'type': 'flow_close',
                'text': 'Até logo! Quando precisar, é só chamar. 😊',
                'buttons': []
            }
        return {
            'type': 'flow_close',
            'text': 'Obrigado pelo contato! Qualquer dúvida é só nos chamar novamente. Até mais! 😊',
            'buttons': []
        }

    # "Outra dúvida" / restart — vai direto pro menu de categorias
    outra_phrases = ('outra dúvida', 'outra duvida', 'tenho outra', 'outra pergunta', 'mais uma duvida', 'mais uma dúvida')
    if any(p in stripped for p in outra_phrases) or stripped in ('outra', 'menu', 'opcoes', 'opções'):
        return {
            'type': 'flow_menu',
            'text': 'Escolha o assunto abaixo 👇',
            'buttons': GREETING_MENU['buttons'],
            'buttons2': GREETING_MENU['buttons2'],
        }

    # After AI response - add follow-up buttons based on confidence
    if confianca >= 0.7:
        return {
            'type': 'flow_followup',
            'buttons': [
                {'id': 'resolveu', 'title': '✅ Resolveu!'},
                {'id': 'outra', 'title': '💬 Outra dúvida'},
                {'id': 'atendente', 'title': '👤 Falar com atendente'},
            ]
        }
    elif confianca >= 0.4:
        return {
            'type': 'flow_followup',
            'buttons': [
                {'id': 'resolveu', 'title': '✅ Ajudou!'},
                {'id': 'atendente', 'title': '👤 Falar com atendente'},
            ]
        }
    else:
        return {
            'type': 'flow_followup',
            'buttons': [
                {'id': 'outra', 'title': '💬 Tenho outra dúvida'},
            ]
        }


# --- Routes: Test / Playground ---

def describe_image_for_rag(image_b64: str, image_mime: str, user_text: str = '') -> str:
    """Usa GPT-4o-mini vision para descrever a imagem e gerar uma query de busca RAG."""
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        prompt_text = user_text or ''
        chat = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': (
                    'Você é um assistente de suporte acadêmico. O aluno enviou uma imagem (possivelmente um print de tela). '
                    'Descreva em 1-2 frases curtas O QUE a imagem mostra (ex: "tela do portal do aluno mostrando erro de acesso ao Blackboard"). '
                    'Foque em identificar: qual plataforma/tela é, qual problema ou dúvida o aluno pode ter. '
                    'Responda APENAS com a descrição, sem saudação.'
                )},
                {'role': 'user', 'content': [
                    {"type": "text", "text": prompt_text or "O que esta imagem mostra?"},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{image_mime or 'image/jpeg'};base64,{image_b64}",
                        "detail": "low"
                    }}
                ]}
            ],
            max_tokens=100, temperature=0.2
        )
        desc = chat.choices[0].message.content.strip()
        return desc
    except Exception as e:
        return user_text or 'dúvida do aluno sobre plataforma acadêmica'


@app.post("/api/test")
async def test_question(data: TestRequest):
    if not data.pergunta.strip() and not data.image_b64:
        raise HTTPException(400, "Pergunta ou imagem é obrigatória")

    try:
        q = data.pergunta.strip() or '[imagem enviada]'
        q_lower = q.lower().rstrip('!?.,').strip()

        # --- Pre-check: Flow-only responses (no LLM needed) ---
        pre_flow = generate_flow_buttons(q, 1.0, data.history)

        if pre_flow and pre_flow.get('type') in ('flow_resolved', 'flow_close', 'flow_escalate', 'flow_submenu', 'flow_retention'):
            return {
                'resposta': pre_flow['text'],
                'confianca': 1.0,
                'latency_ms': 0,
                'tokens_prompt': 0, 'tokens_completion': 0, 'tokens_total': 0,
                'cost_usd': 0,
                'model': 'flow',
                'whatsapp_buttons': None,
                'flow_buttons': pre_flow,
                'referencias': []
            }

        if pre_flow and pre_flow.get('type') == 'flow_menu':
            return {
                'resposta': pre_flow['text'],
                'confianca': 1.0,
                'latency_ms': 0,
                'tokens_prompt': 0, 'tokens_completion': 0, 'tokens_total': 0,
                'cost_usd': 0,
                'model': 'flow',
                'whatsapp_buttons': None,
                'flow_buttons': pre_flow,
                'referencias': []
            }

        # --- Vision: se tem imagem, gera descrição para melhorar busca RAG ---
        image_description = None
        if data.image_b64:
            image_description = describe_image_for_rag(data.image_b64, data.image_mime, q if q != '[imagem enviada]' else '')
            if q == '[imagem enviada]':
                q = image_description

        # --- Translate button clicks (L2/L3) to real questions for RAG ---
        search_query = image_description or q
        stripped_q = q_lower
        for emoji in '🔑💰📚📄🔄👤🧾💳🤝💸🆕📱🖥️📅📖📝📋📎💲🏷️📈🔒💠⚠️📧🌐📨📊⏰':
            stripped_q = stripped_q.replace(emoji + ' ', '').replace(emoji, '')
        stripped_q = stripped_q.strip()

        all_buttons = {}
        for cat in list(SUBMENU.values()) + list(SUBMENU_L3.values()):
            for b in cat.get('buttons', []) + cat.get('buttons2', []):
                if b['id'] in SUBMENU_TO_QUESTION:
                    clean = b['title'].lower()
                    for emoji in '🔑💰📚📄🔄👤🧾💳🤝💸🆕📱🖥️📅📖📝📋📎💲🏷️📈🔒💠⚠️📧🌐📨📊⏰':
                        clean = clean.replace(emoji + ' ', '').replace(emoji, '')
                    all_buttons[clean.strip()] = SUBMENU_TO_QUESTION[b['id']]

        for btn_text, real_question in all_buttons.items():
            if btn_text and btn_text in stripped_q:
                search_query = real_question
                break

        # For short replies with history context
        if data.history and len(search_query) < 30 and search_query == q:
            last_bot = None
            for h in reversed(data.history or []):
                if h.get('role') == 'bot':
                    last_bot = h.get('text', '')[:200]
                    break
            if last_bot:
                search_query = f"{last_bot} {search_query}"

        results = rag_search(search_query)
        refs = build_refs(results)

        history_text = ''
        if data.history:
            for h in data.history[-4:]:
                role = 'Aluno' if h.get('role') == 'user' else 'Assistente'
                history_text += f"{role}: {h.get('text', '')[:200]}\n"

        # Build student/memory/sentiment context
        student_ctx = "## ALUNO: Modo simulador (sem telefone)"
        memory_ctx = ""
        sentiment_ctx = ""
        student_info = None

        if data.phone:
            clean_phone = data.phone.replace('+', '').replace(' ', '').replace('-', '')
            h_crm = {'Authorization': f'Bearer {DCZ_TOKEN}', 'Content-Type': 'application/json'}
            try:
                r_crm = http_requests.get(f'https://crm.g1.datacrazy.io/api/crm/leads',
                                         headers=h_crm, params={'search': clean_phone, 'limit': 1}, timeout=10)
                if r_crm.status_code == 200:
                    leads = r_crm.json().get('data', [])
                    if leads:
                        ld = leads[0]
                        student_info = {'name': ld.get('name', ''), 'cpf': ld.get('taxId', ''),
                                       'tags': [t.get('name', '') for t in ld.get('tags', [])]}
                        fname = ld.get('name', '').split()[0].capitalize() if ld.get('name') else 'aluno'
                        student_ctx = f"## DADOS DO ALUNO:\n- Nome: {ld.get('name','')}\n- Tags: {', '.join(student_info['tags'])}\n\nChame o aluno de *{fname}*."
            except Exception:
                pass

            with get_db() as conn:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM student_memory WHERE phone LIKE %s", (f'%{clean_phone[-11:]}%',))
                mem = cur.fetchone()
            if mem:
                memory_ctx = f"## MEMÓRIA DO ALUNO:\n- Interações: {mem['interaction_count']}\n- Último assunto: {mem.get('last_topic','')}\n- Resumo anterior: {mem.get('last_summary','')}"

        # Sentiment detection
        frustration_words = ['não consigo', 'nao consigo', 'impossível', 'absurdo', 'problema', 'erro',
                            'urgente', 'raiva', 'frustrado', 'horrível', 'já tentei', 'nunca funciona']
        q_text = data.pergunta.lower()
        frust = sum(1 for w in frustration_words if w in q_text)
        if frust >= 2:
            sentiment_ctx = "## SENTIMENTO: FRUSTRADO\n- Valide o sentimento antes de responder. Priorize resolução."
        elif frust == 1:
            sentiment_ctx = "## SENTIMENTO: PREOCUPADO\n- Seja atencioso e detalhado."

        if data.prompt_id:
            with get_db() as conn:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM prompt_versions WHERE id = %s", (data.prompt_id,))
                pv = cur.fetchone()
            if pv:
                prompt_text = pv['system_prompt'].replace('{references}', refs).replace('{history}', history_text)
                prompt_text = prompt_text.replace('{student_context}', student_ctx).replace('{memory_context}', memory_ctx).replace('{sentiment_context}', sentiment_ctx)
                model = data.model or pv['model']
                llm = call_llm(data.pergunta, prompt_text, model, pv['temperature'], pv['max_tokens'], data.history, image_b64=data.image_b64, image_mime=data.image_mime)
            else:
                raise HTTPException(404, "Prompt não encontrado")
        else:
            active = get_active_prompt()
            prompt_text = active['system_prompt'].replace('{references}', refs).replace('{history}', history_text)
            prompt_text = prompt_text.replace('{student_context}', student_ctx).replace('{memory_context}', memory_ctx).replace('{sentiment_context}', sentiment_ctx)
            model = data.model or active['model']
            llm = call_llm(data.pergunta, prompt_text, model, active['temperature'], active['max_tokens'], data.history, image_b64=data.image_b64, image_mime=data.image_mime)

        # Determine follow-up buttons based on flow state
        flow_buttons = generate_flow_buttons(data.pergunta, llm['confianca'], data.history)

        # KB-defined buttons (from bot flow import) - only if no flow buttons
        best_wa = None
        if not flow_buttons:
            for r in results:
                if r.get('whatsapp_buttons') and float(r['score']) >= 0.75:
                    best_wa = r['whatsapp_buttons']
                    break

        return {
            **llm,
            'whatsapp_buttons': best_wa,
            'flow_buttons': flow_buttons,
            'referencias': [
                {'id': r['id'], 'pergunta': str(r['pergunta_aluno'])[:200], 'resposta': str(r['resposta_atendente'])[:300],
                 'tema': r['tema'], 'score': round(float(r['score']), 3),
                 'whatsapp_buttons': r.get('whatsapp_buttons')} for r in results
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=200, content={
            'resposta': 'Desculpe, ocorreu um erro temporário ao processar sua pergunta. Por favor, tente novamente.',
            'confianca': 0,
            'latency_ms': 0,
            'tokens_prompt': 0, 'tokens_completion': 0, 'tokens_total': 0,
            'cost_usd': 0,
            'model': 'erro',
            'whatsapp_buttons': None,
            'flow_buttons': None,
            'referencias': [],
            'erro': str(e)
        })


@app.post("/api/playground")
async def playground(data: PlaygroundRequest):
    if not data.pergunta.strip(): raise HTTPException(400, "Pergunta é obrigatória")
    results = rag_search(data.pergunta)
    refs = build_refs(results)

    empty_ctx = {'student_context': '', 'memory_context': '', 'sentiment_context': ''}
    if data.system_prompt:
        prompt_text = data.system_prompt.replace('{references}', refs).replace('{history}', '')
    elif data.prompt_id:
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT system_prompt FROM prompt_versions WHERE id = %s", (data.prompt_id,))
            row = cur.fetchone()
        prompt_text = (row['system_prompt'] if row else DEFAULT_PROMPT).replace('{references}', refs).replace('{history}', '')
    else:
        prompt_text = DEFAULT_PROMPT.replace('{references}', refs)
    for k, v in empty_ctx.items():
        prompt_text = prompt_text.replace('{' + k + '}', v)

    llm = call_llm(data.pergunta, prompt_text, data.model, data.temperature, data.max_tokens)
    return {
        **llm,
        'referencias': [
            {'id': r['id'], 'pergunta': str(r['pergunta_aluno'])[:200], 'resposta': str(r['resposta_atendente'])[:300],
             'tema': r['tema'], 'score': round(float(r['score']), 3)} for r in results
        ]
    }


# --- Routes: Prompts CRUD ---

@app.get("/api/prompts")
async def list_prompts():
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, is_active, model, temperature, max_tokens, notes, created_at, length(system_prompt) as prompt_length FROM prompt_versions ORDER BY created_at DESC")
        items = cur.fetchall()
        for i in items:
            if i['created_at']: i['created_at'] = i['created_at'].isoformat()
            i['temperature'] = float(i['temperature']) if i['temperature'] else 0.2
        return items


@app.get("/api/prompts/{pid}")
async def get_prompt(pid: int):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM prompt_versions WHERE id = %s", (pid,))
        item = cur.fetchone()
        if not item: raise HTTPException(404)
        if item['created_at']: item['created_at'] = item['created_at'].isoformat()
        item['temperature'] = float(item['temperature']) if item['temperature'] else 0.2
        return item


@app.post("/api/prompts")
async def create_prompt(data: PromptCreate):
    if not data.name.strip() or not data.system_prompt.strip(): raise HTTPException(400, "Nome e prompt obrigatórios")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO prompt_versions (name, system_prompt, model, temperature, max_tokens, notes) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (data.name.strip(), data.system_prompt, data.model, data.temperature, data.max_tokens, data.notes))
        pid = cur.fetchone()[0]
        conn.commit()
        return {'id': pid, 'message': 'Prompt criado'}


@app.put("/api/prompts/{pid}")
async def update_prompt(pid: int, data: PromptUpdate):
    updates, params = [], []
    if data.name is not None: updates.append("name=%s"); params.append(data.name)
    if data.system_prompt is not None: updates.append("system_prompt=%s"); params.append(data.system_prompt)
    if data.model is not None: updates.append("model=%s"); params.append(data.model)
    if data.temperature is not None: updates.append("temperature=%s"); params.append(data.temperature)
    if data.max_tokens is not None: updates.append("max_tokens=%s"); params.append(data.max_tokens)
    if data.notes is not None: updates.append("notes=%s"); params.append(data.notes)
    if not updates: raise HTTPException(400)
    params.append(pid)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE prompt_versions SET {', '.join(updates)} WHERE id=%s", params)
        if cur.rowcount == 0: raise HTTPException(404)
        conn.commit()
        return {'message': 'Atualizado'}


@app.post("/api/prompts/{pid}/activate")
async def activate_prompt(pid: int):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE prompt_versions SET is_active = false WHERE is_active = true")
        cur.execute("UPDATE prompt_versions SET is_active = true WHERE id = %s", (pid,))
        if cur.rowcount == 0: raise HTTPException(404)
        conn.commit()
        return {'message': 'Prompt ativado'}


@app.post("/api/prompts/{pid}/duplicate")
async def duplicate_prompt(pid: int):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM prompt_versions WHERE id = %s", (pid,))
        orig = cur.fetchone()
        if not orig: raise HTTPException(404)
        cur.execute("INSERT INTO prompt_versions (name, system_prompt, model, temperature, max_tokens, notes) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (f"{orig['name']} (cópia)", orig['system_prompt'], orig['model'], orig['temperature'], orig['max_tokens'], orig['notes']))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {'id': new_id, 'message': 'Duplicado'}


@app.delete("/api/prompts/{pid}")
async def delete_prompt(pid: int):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT is_active FROM prompt_versions WHERE id = %s", (pid,))
        row = cur.fetchone()
        if not row: raise HTTPException(404)
        if row[0]: raise HTTPException(400, "Não é possível deletar o prompt ativo")
        cur.execute("DELETE FROM prompt_versions WHERE id = %s", (pid,))
        conn.commit()
        return {'message': 'Deletado'}


# --- Routes: Evaluations ---

@app.post("/api/evaluations")
async def create_evaluation(data: EvalCreate):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO chat_evaluations (pergunta, resposta_ia, confianca, avaliacao, resposta_corrigida, prompt_version_id, model, latency_ms, tokens_used)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (data.pergunta, data.resposta_ia, data.confianca, data.avaliacao, data.resposta_corrigida,
                     data.prompt_version_id, data.model, data.latency_ms, data.tokens_used))
        eid = cur.fetchone()[0]
        conn.commit()
        return {'id': eid}


@app.get("/api/evaluations")
async def list_evaluations(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):
    offset = (page - 1) * per_page
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT count(*) as cnt FROM chat_evaluations")
        total = cur.fetchone()['cnt']
        cur.execute("SELECT * FROM chat_evaluations ORDER BY created_at DESC LIMIT %s OFFSET %s", (per_page, offset))
        items = cur.fetchall()
        for i in items:
            if i['created_at']: i['created_at'] = i['created_at'].isoformat()
            if i['confianca']: i['confianca'] = float(i['confianca'])
        return {'items': items, 'total': total, 'page': page, 'pages': (total + per_page - 1) // per_page}


# --- Routes: Analytics ---

@app.get("/api/analytics")
async def get_analytics():
    empty = {
        'eval_stats': {}, 'timeline': [], 'tema_failures': [],
        'top_escalations': [], 'total_tokens': 0, 'estimated_cost_usd': 0,
        'prompt_comparison': [],
    }
    try:
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            cur.execute("SELECT avaliacao, count(*) as cnt FROM chat_evaluations GROUP BY avaliacao")
            eval_stats = {r['avaliacao']: r['cnt'] for r in cur.fetchall()}

            cur.execute("""
                SELECT date_trunc('day', created_at)::date as day, avg(confianca) as avg_conf, count(*) as cnt
                FROM ia_interaction_log WHERE confianca IS NOT NULL
                GROUP BY day ORDER BY day DESC LIMIT 30
            """)
            timeline = [{'day': str(r['day']), 'avg_conf': round(float(r['avg_conf']), 3), 'count': r['cnt']} for r in cur.fetchall()]

            cur.execute("""
                SELECT kb.tema, count(*) as total,
                       count(*) FILTER (WHERE il.confianca < 0.5) as low_conf
                FROM ia_interaction_log il
                LEFT JOIN knowledge_base kb ON kb.conversation_id = il.conversation_id
                WHERE kb.tema IS NOT NULL
                GROUP BY kb.tema ORDER BY low_conf DESC LIMIT 15
            """)
            tema_failures = [{'tema': r['tema'], 'total': r['total'], 'low_conf': r['low_conf']} for r in cur.fetchall()]

            cur.execute("""
                SELECT pergunta_recebida, confianca, acao, created_at
                FROM ia_interaction_log
                WHERE acao LIKE 'escalate%%'
                ORDER BY created_at DESC LIMIT 10
            """)
            escalations = []
            for r in cur.fetchall():
                escalations.append({
                    'pergunta': r['pergunta_recebida'][:150] if r['pergunta_recebida'] else '',
                    'confianca': float(r['confianca']) if r['confianca'] else 0,
                    'acao': r['acao'],
                    'created_at': r['created_at'].isoformat() if r['created_at'] else ''
                })

            cur.execute("SELECT sum(tokens_used) as total_tokens, count(*) as cnt FROM chat_evaluations WHERE tokens_used > 0")
            cost_row = cur.fetchone()
            total_tokens = cost_row['total_tokens'] or 0
            est_cost = (total_tokens * 0.60) / 1_000_000

            cur.execute("""
                SELECT prompt_version_id, model, avg(confianca) as avg_conf, count(*) as cnt
                FROM chat_evaluations WHERE prompt_version_id IS NOT NULL
                GROUP BY prompt_version_id, model
            """)
            prompt_comparison = [{'prompt_id': r['prompt_version_id'], 'model': r['model'],
                                 'avg_conf': round(float(r['avg_conf']), 3) if r['avg_conf'] else 0,
                                 'count': r['cnt']} for r in cur.fetchall()]

            return {
                'eval_stats': eval_stats,
                'timeline': timeline,
                'tema_failures': tema_failures,
                'top_escalations': escalations,
                'total_tokens': total_tokens,
                'estimated_cost_usd': round(est_cost, 4),
                'prompt_comparison': prompt_comparison,
            }
    except Exception as e:
        print(f"[API] ERRO em /api/analytics: {e}", flush=True)
        empty['error'] = str(e)
        return empty


# --- Routes: Interactions ---

@app.get("/api/interactions")
async def list_interactions(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):
    offset = (page - 1) * per_page
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT count(*) as cnt FROM ia_interaction_log")
        total = cur.fetchone()['cnt']
        cur.execute("SELECT * FROM ia_interaction_log ORDER BY created_at DESC LIMIT %s OFFSET %s", (per_page, offset))
        items = cur.fetchall()
        for i in items:
            if i['created_at']: i['created_at'] = i['created_at'].isoformat()
            if i.get('confianca'): i['confianca'] = float(i['confianca'])
        return {'items': items, 'total': total, 'page': page, 'pages': (total + per_page - 1) // per_page}


# --- Routes: Import ---

@app.post("/api/import/csv")
async def import_csv(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows: raise HTTPException(400, "CSV vazio")

    required = {'pergunta', 'resposta'}
    headers = set(rows[0].keys())
    if not required.issubset(headers):
        raise HTTPException(400, f"CSV deve ter colunas: pergunta, resposta. Encontradas: {headers}")

    imported, errors = 0, 0
    with get_db() as conn:
        cur = conn.cursor()
        for row in rows:
            p, r = row.get('pergunta', '').strip(), row.get('resposta', '').strip()
            tema = row.get('tema', '').strip() or None
            if not p or not r: errors += 1; continue
            try:
                emb = generate_embedding(p)
                emb_str = '{' + ','.join(str(x) for x in emb) + '}'
                cur.execute("INSERT INTO knowledge_base (pergunta_aluno, resposta_atendente, tema, embedding, conversation_id) VALUES (%s,%s,%s,%s::float8[],%s)",
                            (p, r, tema, emb_str, 'csv_import'))
                imported += 1
                if imported % 10 == 0: conn.commit()
            except Exception:
                errors += 1
        conn.commit()
    return {'imported': imported, 'errors': errors, 'total_rows': len(rows)}


@app.post("/api/import/templates")
async def import_templates():
    h = {'Authorization': f'Bearer {DCZ_TOKEN}', 'Content-Type': 'application/json'}
    r = http_requests.get(f'{DCZ_MSG}/messaging/templates', headers=h, timeout=15)
    if r.status_code != 200:
        raise HTTPException(502, f"DataCrazy retornou {r.status_code}")
    templates = r.json().get('data', [])

    preview = []
    for t in templates:
        name = t.get('name', '').strip()
        body = t.get('body', '').strip()
        tid = t.get('id', '')
        attachments = t.get('attachments', [])
        if not body or len(body) < 20 or name.lower() in ('teste', 'test'):
            continue
        preview.append({
            'id': tid, 'name': name, 'body': body[:300],
            'full_body': body, 'attachments_count': len(attachments),
            'attachments': [a.get('filename', a.get('url', '')) for a in attachments]
        })
    return {'count': len(preview), 'templates': preview}


@app.post("/api/import/templates/confirm")
async def confirm_import_templates(template_ids: List[str]):
    h = {'Authorization': f'Bearer {DCZ_TOKEN}', 'Content-Type': 'application/json'}
    r = http_requests.get(f'{DCZ_MSG}/messaging/templates', headers=h, timeout=15)
    templates = {t['id']: t for t in r.json().get('data', [])}

    imported, errors = 0, 0
    with get_db() as conn:
        cur = conn.cursor()
        for tid in template_ids:
            t = templates.get(tid)
            if not t: continue
            name, body = t.get('name', ''), t.get('body', '')
            pergunta = f"Informação sobre: {name}"
            att_info = ''
            atts = t.get('attachments', [])
            if atts:
                att_info = '\n\n[Anexos: ' + ', '.join(a.get('filename', a.get('url', '')) for a in atts) + ']'
            try:
                emb = generate_embedding(pergunta)
                emb_str = '{' + ','.join(str(x) for x in emb) + '}'
                cur.execute("INSERT INTO knowledge_base (pergunta_aluno, resposta_atendente, tema, embedding, conversation_id) VALUES (%s,%s,%s,%s::float8[],%s)",
                            (pergunta, body + att_info, 'OUTRO', emb_str, f'template:{tid}'))
                imported += 1
            except Exception:
                errors += 1
        conn.commit()
    return {'imported': imported, 'errors': errors}


# --- Routes: Gaps & Duplicates ---

@app.get("/api/gaps")
async def find_gaps():
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT pergunta_recebida, confianca, acao, created_at
            FROM ia_interaction_log
            WHERE confianca IS NOT NULL AND confianca < 0.5
            ORDER BY created_at DESC LIMIT 50
        """)
        gaps = []
        for r in cur.fetchall():
            gaps.append({
                'pergunta': r['pergunta_recebida'][:200] if r['pergunta_recebida'] else '',
                'confianca': float(r['confianca']) if r['confianca'] else 0,
                'acao': r['acao'],
                'created_at': r['created_at'].isoformat() if r['created_at'] else ''
            })
        return gaps


@app.get("/api/duplicates")
async def find_duplicates():
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT a.id as id_a, b.id as id_b,
                   a.pergunta_aluno as pergunta_a, b.pergunta_aluno as pergunta_b,
                   cosine_similarity(a.embedding, b.embedding) as similarity
            FROM knowledge_base a, knowledge_base b
            WHERE a.id < b.id
            AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
            AND cosine_similarity(a.embedding, b.embedding) > 0.95
            ORDER BY similarity DESC
            LIMIT 50
        """)
        results = []
        for r in cur.fetchall():
            results.append({
                'id_a': r['id_a'], 'id_b': r['id_b'],
                'pergunta_a': str(r['pergunta_a'])[:150], 'pergunta_b': str(r['pergunta_b'])[:150],
                'similarity': round(float(r['similarity']), 4)
            })
        return results


# --- Routes: Student & Tabulation ---

DCZ_CRM = 'https://crm.g1.datacrazy.io/api/crm'

# === Maps de atendentes (espelho do agente_ao_vivo_v4) ===
# Mantidos aqui para o endpoint de auto-fix poder executar PATCHes diretamente
# no DCZ sem depender do processo do agente. Se atualizar la, atualizar aqui.
STAGE_ATENDIMENTO_ID = 'ce42afe6-757f-405c-aa34-6668f4a75d07'
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


def _norm_attendant_name(name):
    import unicodedata
    if not name:
        return ''
    n = name.strip().lower()
    n = ''.join(c for c in unicodedata.normalize('NFD', n)
                if unicodedata.category(c) != 'Mn')
    return n


def _lookup_attendant_id_api(name, table):
    """Resolve attendant_id por nome (full -> first -> prefix-match)."""
    norm = _norm_attendant_name(name)
    if not norm:
        return None
    if norm in table:
        return table[norm]
    first = norm.split()[0] if norm else ''
    if first and first in table:
        return table[first]
    for k, v in table.items():
        if norm.startswith(k) or k in norm.split():
            return v
    return None


def _dcz_perform_assignment_fix(conv_id, lead_id, phone, expected_name,
                                max_retries=5):
    """Re-aplica PATCH lead + business + change-attendant ate convergir.
    Versao da API (paralela ao _enforce_assignment_consistency do agente).

    Retorna dict com {ok_lead, ok_biz, ok_chat, attempts, biz_id, lead_id,
    final_lead_att, final_biz_att, final_chat_att, expected_crm_id,
    expected_chat_id, expected_name, error_log[]}.
    """
    h = {'Authorization': f'Bearer {DCZ_TOKEN}', 'Content-Type': 'application/json'}
    expected_crm_id = _lookup_attendant_id_api(expected_name, CRM_ATTENDANT_MAP) or ''
    expected_chat_id = _lookup_attendant_id_api(expected_name, ATTENDANT_MAP) or ''
    result = {
        'ok_lead': False, 'ok_biz': False, 'ok_chat': False,
        'attempts': 0, 'biz_id': '', 'lead_id': lead_id,
        'final_lead_att': '', 'final_biz_att': '', 'final_chat_att': '',
        'expected_crm_id': expected_crm_id,
        'expected_chat_id': expected_chat_id,
        'expected_name': expected_name,
        'error_log': [],
    }
    if not expected_crm_id and not expected_chat_id:
        result['error_log'].append(f"nome '{expected_name}' nao mapeado")
        return result

    def _read_lead_att():
        if not lead_id:
            return ''
        try:
            r = http_requests.get(f'{DCZ_CRM}/leads/{lead_id}', headers=h, timeout=10)
            if r.status_code != 200:
                result['error_log'].append(f"GET lead status={r.status_code}")
                return ''
            ld = r.json()
            att = ld.get('attendant') or {}
            return att.get('id', '') if isinstance(att, dict) else (att or '')
        except Exception as e:
            result['error_log'].append(f"GET lead err: {e}")
            return ''

    def _find_biz():
        if lead_id:
            try:
                r = http_requests.get(f'{DCZ_CRM}/leads/{lead_id}/businesses',
                                      headers=h, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    bl = data.get('data', data) if isinstance(data, dict) else data
                    if isinstance(bl, list) and bl:
                        b = bl[0]
                        return (b.get('id', '') if isinstance(b, dict) else str(b),
                                b if isinstance(b, dict) else {})
            except Exception as e:
                result['error_log'].append(f"sub-biz err: {e}")
        if phone:
            clean = phone.replace('+', '').replace(' ', '').replace('-', '')
            for try_phone in [clean, ('55' + clean) if not clean.startswith('55') else clean[2:]]:
                try:
                    r = http_requests.get(f'{DCZ_CRM}/businesses', headers=h,
                                          params={'search': try_phone, 'limit': 10},
                                          timeout=10)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    bl = data.get('data', data) if isinstance(data, dict) else data
                    if not isinstance(bl, list):
                        continue
                    for b in bl:
                        b_lead = b.get('leadId') or ''
                        if not b_lead and isinstance(b.get('lead'), dict):
                            b_lead = b['lead'].get('id', '')
                        if lead_id and b_lead == lead_id:
                            return b.get('id', ''), b
                    if bl:
                        return bl[0].get('id', ''), bl[0]
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
            r = http_requests.get(f'{DCZ_CRM}/businesses/{biz_id}', headers=h, timeout=10)
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
            r = http_requests.get(f'{DCZ_MSG}/messaging/conversations/{conv_id}',
                                  headers=h, timeout=10)
            if r.status_code != 200:
                return ''
            cd = r.json()
            att = cd.get('attendant') or {}
            if isinstance(att, dict):
                return att.get('id', '')
            return cd.get('attendantId', '') or ''
        except Exception:
            return ''

    backoff = [0.5, 1.0, 2.0, 3.0, 4.0]
    for attempt in range(max_retries + 1):
        result['attempts'] = attempt + 1
        biz_id, biz_obj = _find_biz()
        result['biz_id'] = biz_id

        cur_lead = _read_lead_att()
        cur_biz = _read_biz_att(biz_obj, biz_id)
        cur_chat = _read_chat_att()
        result['final_lead_att'] = cur_lead
        result['final_biz_att'] = cur_biz
        result['final_chat_att'] = cur_chat

        lead_ok = (cur_lead == expected_crm_id) if lead_id else True
        biz_ok = (cur_biz == expected_crm_id) if biz_id else False
        chat_ok = (cur_chat == expected_chat_id) if expected_chat_id else True

        result['ok_lead'] = lead_ok
        result['ok_biz'] = biz_ok
        result['ok_chat'] = chat_ok

        if lead_ok and biz_ok and chat_ok:
            return result

        if attempt >= max_retries:
            break

        if not lead_ok and lead_id and expected_crm_id:
            try:
                http_requests.patch(f'{DCZ_CRM}/leads/{lead_id}', headers=h,
                                    json={'attendant': {'id': expected_crm_id}},
                                    timeout=10)
            except Exception as e:
                result['error_log'].append(f"PATCH lead err: {e}")
        if not biz_ok and biz_id and expected_crm_id:
            try:
                http_requests.patch(f'{DCZ_CRM}/businesses/{biz_id}', headers=h,
                                    json={'attendant': {'id': expected_crm_id},
                                          'stageId': STAGE_ATENDIMENTO_ID},
                                    timeout=10)
            except Exception as e:
                result['error_log'].append(f"PATCH biz err: {e}")
        if not chat_ok and expected_chat_id and conv_id:
            try:
                http_requests.post(
                    f'{DCZ_MSG}/messaging/conversations/{conv_id}/change-attendant',
                    headers=h, json={'attendantId': expected_chat_id}, timeout=15)
            except Exception as e:
                result['error_log'].append(f"change-attendant err: {e}")
        time.sleep(backoff[min(attempt, len(backoff) - 1)])

    return result

@app.get("/api/student/{phone}")
async def get_student(phone: str):
    """Busca dados do aluno no DataCrazy CRM + memória local."""
    h = {'Authorization': f'Bearer {DCZ_TOKEN}', 'Content-Type': 'application/json'}
    clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')

    lead = None
    try:
        r = http_requests.get(f'{DCZ_CRM}/leads', headers=h,
                             params={'search': clean_phone, 'limit': 3}, timeout=10)
        if r.status_code == 200:
            leads = r.json().get('data', [])
            if leads:
                lead = leads[0]
    except Exception:
        pass

    memory = None
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM student_memory WHERE phone LIKE %s", (f'%{clean_phone[-11:]}%',))
        memory = cur.fetchone()
        if memory:
            if memory.get('last_contact_at'):
                memory['last_contact_at'] = memory['last_contact_at'].isoformat()
            if memory.get('first_contact_at'):
                memory['first_contact_at'] = memory['first_contact_at'].isoformat()

    return {
        'lead': {
            'id': lead.get('id', '') if lead else None,
            'name': lead.get('name', '') if lead else None,
            'phone': lead.get('rawPhone', '') if lead else None,
            'cpf': lead.get('taxId', '') if lead else None,
            'email': lead.get('email', '') if lead else None,
            'tags': [t.get('name', '') for t in lead.get('tags', [])] if lead else [],
        } if lead else None,
        'memory': memory,
    }


@app.get("/api/tabulation/stats")
async def tabulation_stats():
    """Estatísticas de tabulação dos atendimentos."""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("SELECT count(*) as total FROM interaction_summary")
        total = cur.fetchone()['total']

        cur.execute("SELECT tema, count(*) as cnt FROM interaction_summary GROUP BY tema ORDER BY cnt DESC")
        by_tema = [{'tema': r['tema'], 'count': r['cnt']} for r in cur.fetchall()]

        cur.execute("SELECT sentimento, count(*) as cnt FROM interaction_summary GROUP BY sentimento ORDER BY cnt DESC")
        by_sentiment = [{'sentimento': r['sentimento'], 'count': r['cnt']} for r in cur.fetchall()]

        cur.execute("SELECT resolvido, count(*) as cnt FROM interaction_summary GROUP BY resolvido ORDER BY cnt DESC")
        by_resolved = [{'resolvido': r['resolvido'], 'count': r['cnt']} for r in cur.fetchall()]

        cur.execute("SELECT avg(nps_implicito) as avg_nps FROM interaction_summary WHERE nps_implicito IS NOT NULL")
        avg_nps = cur.fetchone()['avg_nps'] or 0

        cur.execute("""
            SELECT phone, student_name, count(*) as cnt, avg(nps_implicito) as avg_nps
            FROM interaction_summary WHERE student_name IS NOT NULL
            GROUP BY phone, student_name ORDER BY cnt DESC LIMIT 10
        """)
        top_students = [{'phone': r['phone'], 'name': r['student_name'], 'count': r['cnt'],
                        'avg_nps': round(float(r['avg_nps']), 1) if r['avg_nps'] else 0} for r in cur.fetchall()]

        cur.execute("""
            SELECT * FROM interaction_summary ORDER BY created_at DESC LIMIT 20
        """)
        recent = []
        for r in cur.fetchall():
            item = dict(r)
            if item.get('created_at'):
                item['created_at'] = item['created_at'].isoformat()
            recent.append(item)

        return {
            'total': total,
            'by_tema': by_tema,
            'by_sentiment': by_sentiment,
            'by_resolved': by_resolved,
            'avg_nps': round(float(avg_nps), 1),
            'top_students': top_students,
            'recent': recent,
        }


def _ensure_caa_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS caa_solicitacoes (
            id SERIAL PRIMARY KEY,
            cpf VARCHAR(14),
            rgm VARCHAR(32),
            aluno_nome VARCHAR(255),
            polo VARCHAR(255),
            subprocesso VARCHAR(255),
            data_chegada DATE,
            data_previsao DATE,
            data_conclusao DATE,
            protocolo VARCHAR(64),
            aging_dias INT,
            observacao TEXT,
            situacao_atendimento VARCHAR(64),
            situacao_deferimento VARCHAR(64),
            celular VARCHAR(20),
            email VARCHAR(255),
            curso VARCHAR(255),
            instituicao VARCHAR(255),
            qtd_protocolos INT,
            imported_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_caa_cpf ON caa_solicitacoes (cpf)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_caa_situacao "
        "ON caa_solicitacoes (situacao_atendimento, situacao_deferimento)"
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS caa_import_history (
            id SERIAL PRIMARY KEY,
            imported_at TIMESTAMP DEFAULT NOW(),
            total_rows INT,
            open_count INT,
            pending_count INT,
            concluded_count INT,
            filename VARCHAR(255),
            uploaded_by VARCHAR(64)
        )
    """)


def _ensure_pending_escalation_table(cur):
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
        CREATE INDEX IF NOT EXISTS idx_pending_escalation_status
        ON pending_escalation (status, created_at DESC)
    """)


def _load_business_hours_config():
    """Lê horários do agent_config com fallback nos defaults do Cockpit."""
    defaults = {
        'business_hours_weekday_start': 9,
        'business_hours_weekday_end': 20,
        'business_hours_saturday_start': 9,
        'business_hours_saturday_end': 13,
    }
    cfg = dict(defaults)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            keys = tuple(defaults.keys())
            cur.execute(
                "SELECT key, value FROM agent_config WHERE key IN %s",
                (keys,),
            )
            for key, value in cur.fetchall():
                try:
                    cfg[key] = int(json.loads(value))
                except (json.JSONDecodeError, TypeError, ValueError):
                    try:
                        cfg[key] = int(value)
                    except (TypeError, ValueError):
                        pass
            cur.close()
    except Exception:
        pass
    return cfg


def _now_sp_dt():
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone.utc) + timedelta(hours=-3)


def _is_within_business_hours_api(ref_now=None):
    cfg = _load_business_hours_config()
    now = ref_now or _now_sp_dt()
    dow = now.weekday()
    hour = now.hour
    wd_s = cfg['business_hours_weekday_start']
    wd_e = cfg['business_hours_weekday_end']
    sat_s = cfg['business_hours_saturday_start']
    sat_e = cfg['business_hours_saturday_end']
    if dow <= 4:
        return wd_s <= hour < wd_e
    if dow == 5:
        return sat_s <= hour < sat_e
    return False


def _next_human_available_label_api(ref_now=None):
    cfg = _load_business_hours_config()
    now = ref_now or _now_sp_dt()
    dow = now.weekday()
    hour = now.hour
    wd_s = cfg['business_hours_weekday_start']
    wd_e = cfg['business_hours_weekday_end']
    sat_s = cfg['business_hours_saturday_start']
    if dow <= 4 and hour < wd_s:
        return f"hoje às {wd_s}h"
    if dow == 5 and hour < sat_s:
        return f"hoje às {sat_s}h"
    if dow <= 3 and hour >= wd_e:
        return f"amanhã às {wd_s}h"
    if dow == 4 and hour >= wd_e:
        return f"amanhã às {sat_s}h"
    if dow == 5 and hour >= cfg['business_hours_saturday_end']:
        return f"na segunda-feira às {wd_s}h"
    if dow == 6:
        return f"na segunda-feira às {wd_s}h"
    # Dentro do horário (abertura de hoje já passou) → quem insistir à noite retorna amanhã
    if dow <= 3:
        return f"amanhã às {wd_s}h"
    if dow == 4:
        return f"amanhã às {sat_s}h"
    if dow == 5:
        return f"na segunda-feira às {wd_s}h"
    return f"amanhã às {wd_s}h"


def _load_after_hours_dispatch_config():
    """Lê flags da fila matinal (agent_config + defaults)."""
    cfg = {
        'auto_dispatch_morning_queue': AGENT_CONFIG_DEFAULTS.get('auto_dispatch_morning_queue', True),
        'morning_dispatch_batch_size': AGENT_CONFIG_DEFAULTS.get('morning_dispatch_batch_size', 25),
        'morning_queue_last_run': '',
    }
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT key, value FROM agent_config WHERE key IN %s",
                (('auto_dispatch_morning_queue', 'morning_dispatch_batch_size', 'morning_queue_last_run'),),
            )
            for key, value in cur.fetchall():
                try:
                    parsed = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    parsed = value
                if key == 'auto_dispatch_morning_queue':
                    cfg[key] = parsed if isinstance(parsed, bool) else str(parsed).lower() in ('1', 'true', 'yes', 'sim')
                elif key == 'morning_dispatch_batch_size':
                    cfg[key] = int(parsed)
                else:
                    cfg[key] = str(parsed).strip().strip('"')
            cur.close()
    except Exception:
        pass
    return cfg


def _load_agent_watchdog_info():
    """Lê status do heartbeat + último auto-restart."""
    info = {'heartbeat_status': 'unknown', 'heartbeat_age_min': None, 'last_auto_restart': None}
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT status, EXTRACT(EPOCH FROM (NOW() - last_beat))/60
                FROM agent_heartbeat WHERE id=1
            """)
            row = cur.fetchone()
            if row:
                info['heartbeat_status'] = row[0]
                info['heartbeat_age_min'] = round(float(row[1]), 1) if row[1] is not None else None
            cur.execute("SELECT value FROM agent_config WHERE key = 'agent_last_auto_restart'")
            row = cur.fetchone()
            if row and row[0]:
                try:
                    info['last_auto_restart'] = json.loads(row[0])
                except Exception:
                    info['last_auto_restart'] = None
            cur.close()
    except Exception:
        pass
    return info


@app.get("/api/after-hours/status")
async def after_hours_status():
    """Horário atual (SP), modo de atendimento e próximo retorno humano."""
    now = _now_sp_dt()
    cfg = _load_business_hours_config()
    dispatch = _load_after_hours_dispatch_config()
    watchdog = _load_agent_watchdog_info()
    within = _is_within_business_hours_api(now)
    return {
        'now_iso': now.isoformat(),
        'now_display': now.strftime('%d/%m/%Y %H:%M:%S'),
        'timezone': 'America/Sao_Paulo (UTC-3)',
        'within_business_hours': within,
        'mode': 'human_available' if within else 'after_hours',
        'next_return_label': _next_human_available_label_api(now),
        'business_hours': {
            'weekday': f"{cfg['business_hours_weekday_start']}h–{cfg['business_hours_weekday_end']}h (Seg–Sex)",
            'saturday': f"{cfg['business_hours_saturday_start']}h–{cfg['business_hours_saturday_end']}h",
            'sunday': 'fechado',
        },
        'auto_dispatch': {
            'enabled': dispatch['auto_dispatch_morning_queue'],
            'batch_size': dispatch['morning_dispatch_batch_size'],
            'last_run_date': dispatch['morning_queue_last_run'] or None,
            'description': (
                'À abertura do expediente o agente distribui a fila (insistência primeiro), '
                'transfere no DataCrazy e marca Em atendimento no Cockpit; retenta a cada 10 min. '
                'Ao encerrar a conversa, marca Resolvido.'
            ),
        },
        'watchdog': watchdog,
    }


@app.get("/api/after-hours/pending")
async def after_hours_pending(
    status: str = Query('pending'),
    tier: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Lista fila de retornos fora do horário."""
    allowed_status = {'pending', 'in_progress', 'resolved', 'closed_no_engagement', 'all', 'active'}
    if status not in allowed_status:
        status = 'pending'
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ensure_pending_escalation_table(cur)
        conn.commit()
        where = ["status != 'superseded'"]
        params = []
        if status == 'active':
            # NaO inclui closed_no_engagement em 'ativos' — esses sao encerrados
            # sem atendimento (nao precisam mais de acao, mas tampouco sao
            # 'resolvidos').
            where.append("status IN ('pending', 'in_progress')")
        elif status != 'all':
            where.append("status = %s")
            params.append(status)
        if tier in ('first', 'insist'):
            where.append("tier = %s")
            params.append(tier)
        w = " AND ".join(where)
        cur.execute(f"""
            SELECT id, conv_id, phone, student_name, reason, tier, retorno_label,
                   pergunta, status, created_at, updated_at, resolved_at
            FROM pending_escalation
            WHERE {w}
            ORDER BY
              CASE tier WHEN 'insist' THEN 0 WHEN 'first' THEN 1 ELSE 2 END,
              created_at DESC
            LIMIT %s
        """, params + [limit])
        rows = []
        for r in cur.fetchall():
            item = dict(r)
            for k in ('created_at', 'updated_at', 'resolved_at'):
                if item.get(k):
                    item[k] = item[k].isoformat()
            rows.append(item)
        cur.execute(f"""
            SELECT status, COUNT(*) as cnt FROM pending_escalation
            WHERE status != 'superseded' GROUP BY status
        """)
        counts = {r['status']: r['cnt'] for r in cur.fetchall()}
        cur.execute("""
            SELECT COUNT(*) as cnt FROM pending_escalation
            WHERE status IN ('pending', 'in_progress') AND tier = 'insist'
        """)
        priority = cur.fetchone()['cnt']
        return {
            'items': rows,
            'counts': counts,
            'priority_pending': priority,
        }


@app.patch("/api/after-hours/pending/{item_id}")
async def after_hours_pending_update(item_id: int, request: Request):
    """Atualiza status da fila (pending → in_progress → resolved)."""
    data = await request.json()
    new_status = (data.get('status') or '').strip()
    if new_status not in ('pending', 'in_progress', 'resolved', 'dismissed', 'closed_no_engagement'):
        raise HTTPException(400, 'status inválido')
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ensure_pending_escalation_table(cur)
        resolved_clause = ", resolved_at = NOW()" if new_status in ('resolved', 'closed_no_engagement') else ", resolved_at = NULL"
        cur.execute(
            f"UPDATE pending_escalation SET status = %s, updated_at = NOW(){resolved_clause} WHERE id = %s RETURNING id",
            (new_status, item_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, 'Registro não encontrado')
        conn.commit()
        return {'ok': True, 'id': item_id, 'status': new_status}


# ===================== AUDITORIA IA (supervisor OpenAI) =====================

def _ensure_audit_table_api():
    """Garante que agent_audit_findings existe (caso o agente nunca tenha
    criado ainda - acontece em ambiente novo)."""
    try:
        with get_db() as conn:
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
                    resolved_at TIMESTAMP NULL,
                    resolved_by VARCHAR(64) NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            # Garantir colunas novas mesmo se a tabela ja existir
            cur.execute("""
                ALTER TABLE agent_audit_findings
                ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP NULL
            """)
            cur.execute("""
                ALTER TABLE agent_audit_findings
                ADD COLUMN IF NOT EXISTS resolved_by VARCHAR(64) NULL
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
    except Exception as e:
        print(f"[API] _ensure_audit_table_api: {e}", flush=True)


@app.get("/api/audit/findings")
async def audit_findings_list(
    limit: int = Query(50, ge=1, le=500),
    severity: Optional[str] = None,
    problem_type: Optional[str] = None,
    status: str = Query('all', pattern='^(all|open|resolved)$'),
    hours: int = Query(72, ge=1, le=720),
):
    """Lista findings do supervisor OpenAI nas ultimas N horas."""
    _ensure_audit_table_api()
    try:
        conds = ["created_at > NOW() - (%s || ' hours')::interval"]
        params = [str(hours)]
        if severity:
            conds.append("severity = %s")
            params.append(severity)
        if problem_type:
            conds.append("problem_type = %s")
            params.append(problem_type)
        if status == 'open':
            conds.append("resolved_at IS NULL")
        elif status == 'resolved':
            conds.append("resolved_at IS NOT NULL")
        where = " AND ".join(conds)
        # Prefix de WHERE para o sub-select de student_memory (sem precisar repetir CONDS la)
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # LEFT JOIN com student_memory pelo phone (normalizado: so digitos)
            # para enriquecer o finding com o nome do aluno quando disponivel.
            cur.execute(f"""
                SELECT
                    f.id, f.conv_id, f.phone, f.model, f.severity, f.problem_type,
                    f.summary, f.detail, f.action_taken, f.resolved_at, f.resolved_by,
                    f.created_at,
                    sm.student_name
                FROM agent_audit_findings f
                LEFT JOIN student_memory sm
                    ON RIGHT(regexp_replace(COALESCE(sm.phone, ''), '\\D', '', 'g'), 11)
                       = RIGHT(regexp_replace(COALESCE(f.phone, ''), '\\D', '', 'g'), 11)
                   AND length(regexp_replace(COALESCE(f.phone, ''), '\\D', '', 'g')) >= 10
                WHERE {where}
                ORDER BY f.created_at DESC
                LIMIT %s
            """, params + [limit])
            rows = cur.fetchall()
            # Normaliza timestamps marcando UTC explicitamente para o browser
            # converter para o timezone local (resolvendo o "19:01" que era 16:01 BRT).
            from datetime import timezone as _tz
            for r in rows:
                if r.get('created_at'):
                    ts = r['created_at']
                    if hasattr(ts, 'tzinfo') and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_tz.utc)
                    r['created_at'] = ts.isoformat()
                if r.get('resolved_at'):
                    ts = r['resolved_at']
                    if hasattr(ts, 'tzinfo') and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_tz.utc)
                    r['resolved_at'] = ts.isoformat()
                # extrai primeiro nome p/ exibicao compacta no frontend
                full = (r.get('student_name') or '').strip()
                r['student_first_name'] = full.split()[0] if full else ''
            # contagem global por severidade — normaliza en->pt (high/medium/low -> alta/media/baixa)
            # pois o supervisor OpenAI grava em pt e a verificacao de distribuicao em en.
            cur.execute(f"""
                SELECT severity, COUNT(*) as cnt
                FROM agent_audit_findings
                WHERE {where}
                GROUP BY severity
            """, params)
            raw_counts = {r['severity']: r['cnt'] for r in cur.fetchall()}
            sev_map = {
                'high': 'alta', 'alta': 'alta',
                'medium': 'media', 'media': 'media',
                'low': 'baixa', 'baixa': 'baixa',
            }
            counts = {'alta': 0, 'media': 0, 'baixa': 0}
            for sev, cnt in raw_counts.items():
                norm = sev_map.get((sev or '').lower(), (sev or '').lower() or 'baixa')
                counts[norm] = counts.get(norm, 0) + cnt
            # tambem normaliza nos items para o frontend renderizar badge consistente
            for r in rows:
                r['severity'] = sev_map.get((r.get('severity') or '').lower(),
                                            (r.get('severity') or '').lower() or 'baixa')
            cur.close()
            return {'items': rows, 'counts': counts, 'total': len(rows)}
    except Exception as e:
        print(f"[API] /api/audit/findings ERRO: {e}", flush=True)
        return {'items': [], 'counts': {}, 'total': 0, 'error': str(e)}


@app.post("/api/audit/findings/{finding_id}/resolve")
async def audit_findings_resolve(finding_id: int, request: Request):
    """Marca finding como resolvido. Opcionalmente libera o handoff_active
    supervisor_block da conv (se body.unblock=true)."""
    _ensure_audit_table_api()
    try:
        body = await request.json()
    except Exception:
        body = {}
    unblock = bool(body.get('unblock'))
    resolved_by = (body.get('resolved_by') or 'cockpit').strip()[:64]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE agent_audit_findings
            SET resolved_at = NOW(), resolved_by = %s
            WHERE id = %s
            RETURNING conv_id
        """, (resolved_by, finding_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, 'finding não encontrado')
        conv_id = row[0]
        if unblock and conv_id:
            try:
                cur.execute(
                    "DELETE FROM handoff_active WHERE conv_id = %s AND motivo = 'supervisor_block'",
                    (conv_id,),
                )
            except Exception:
                pass
        conn.commit()
        cur.close()
    return {'ok': True, 'id': finding_id, 'conv_id': conv_id, 'unblocked': unblock}


@app.get("/api/diag/dcz-msgs/{conv_id}")
async def diag_dcz_msgs(conv_id: str):
    """Endpoint diagnostico — chama o DCZ /messaging/conversations/{id}/messages
    e retorna o JSON cru pra ver exatamente quais chaves o DCZ usa.
    Usado para debugar o caso empty_window=75 no supervisor."""
    import os, requests
    DCZ_MSG = os.environ.get('DCZ_MSG', 'https://api.datacrazy.com.br')
    DCZ_TOKEN = os.environ.get('DCZ_TOKEN', '')
    if not DCZ_TOKEN:
        return {'ok': False, 'error': 'DCZ_TOKEN nao configurado'}
    H = {'Authorization': f'Bearer {DCZ_TOKEN}', 'Content-Type': 'application/json'}
    try:
        r = requests.get(
            f'{DCZ_MSG}/messaging/conversations/{conv_id}/messages',
            headers=H, params={'limit': 5}, timeout=15
        )
        out = {'status': r.status_code, 'headers': dict(r.headers)}
        try:
            j = r.json()
            out['type'] = str(type(j).__name__)
            if isinstance(j, dict):
                out['keys'] = list(j.keys())
                out['sample'] = {}
                for k in list(j.keys())[:5]:
                    v = j[k]
                    if isinstance(v, list):
                        out['sample'][k] = f'<list len={len(v)}>'
                        if v and isinstance(v[0], dict):
                            out['sample'][f'{k}[0]_keys'] = list(v[0].keys())
                            out['sample'][f'{k}[0]'] = {kk: str(vv)[:80] for kk, vv in list(v[0].items())[:10]}
                    elif isinstance(v, dict):
                        out['sample'][k] = {'keys': list(v.keys())[:10]}
                    else:
                        out['sample'][k] = str(v)[:120]
            elif isinstance(j, list):
                out['list_len'] = len(j)
                if j and isinstance(j[0], dict):
                    out['first_item_keys'] = list(j[0].keys())
                    out['first_item'] = {k: str(v)[:80] for k, v in list(j[0].items())[:10]}
            else:
                out['raw_short'] = str(j)[:300]
        except Exception as e_json:
            out['json_error'] = str(e_json)
            out['text_short'] = r.text[:500]
        return out
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@app.get("/api/diag/time")
async def diag_time():
    """Endpoint diagnostico — retorna a percepcao do servidor sobre o
    horario atual e regras de janela. Util para confirmar se _in_pre_opening_window()
    esta funcionando (caso Jaqueline 08:49: deveria ter retornado True)."""
    from datetime import datetime, timezone, timedelta
    import time as _t
    utc_now = datetime.now(timezone.utc)
    sp_now = utc_now + timedelta(hours=-3)
    dow = sp_now.weekday()
    hour = sp_now.hour
    minute = sp_now.minute
    BUSINESS_HOURS_WEEKDAY_START = 9
    BUSINESS_HOURS_WEEKDAY_END = 20
    BUSINESS_HOURS_SATURDAY_START = 9
    BUSINESS_HOURS_SATURDAY_END = 13
    PRE_OPENING_MARGIN_MIN = 60
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM agent_config WHERE key IN "
                        "('business_hours_weekday_start','business_hours_weekday_end',"
                        "'business_hours_saturday_start','business_hours_saturday_end',"
                        "'pre_opening_enabled','pre_opening_margin_min')")
            for k, v in cur.fetchall():
                try:
                    if k == 'business_hours_weekday_start': BUSINESS_HOURS_WEEKDAY_START = int(v)
                    elif k == 'business_hours_weekday_end': BUSINESS_HOURS_WEEKDAY_END = int(v)
                    elif k == 'business_hours_saturday_start': BUSINESS_HOURS_SATURDAY_START = int(v)
                    elif k == 'business_hours_saturday_end': BUSINESS_HOURS_SATURDAY_END = int(v)
                    elif k == 'pre_opening_margin_min': PRE_OPENING_MARGIN_MIN = int(v)
                except Exception:
                    pass
            cur.close()
    except Exception:
        pass
    if dow <= 4:
        within = BUSINESS_HOURS_WEEKDAY_START <= hour < BUSINESS_HOURS_WEEKDAY_END
        target_start_hour = BUSINESS_HOURS_WEEKDAY_START
    elif dow == 5:
        within = BUSINESS_HOURS_SATURDAY_START <= hour < BUSINESS_HOURS_SATURDAY_END
        target_start_hour = BUSINESS_HOURS_SATURDAY_START
    else:
        within = False
        target_start_hour = BUSINESS_HOURS_WEEKDAY_START
    mins_until = 9999
    if not within and dow <= 5 and hour < target_start_hour:
        mins_until = (target_start_hour * 60) - (hour * 60 + minute)
    in_pre = (not within) and (mins_until <= PRE_OPENING_MARGIN_MIN)
    return {
        'ok': True,
        'utc_now': utc_now.isoformat(),
        'sp_now': sp_now.isoformat(),
        'unix_ts': _t.time(),
        'system_tz_offset_min': _t.timezone // 60 * -1,
        'dow': dow,
        'dow_label': ['seg','ter','qua','qui','sex','sab','dom'][dow],
        'hour': hour,
        'minute': minute,
        'business_hours_weekday_start': BUSINESS_HOURS_WEEKDAY_START,
        'business_hours_weekday_end': BUSINESS_HOURS_WEEKDAY_END,
        'business_hours_saturday_start': BUSINESS_HOURS_SATURDAY_START,
        'business_hours_saturday_end': BUSINESS_HOURS_SATURDAY_END,
        'pre_opening_margin_min': PRE_OPENING_MARGIN_MIN,
        'is_within_business_hours': within,
        'minutes_until_business_hours_start': mins_until,
        'in_pre_opening_window': in_pre,
    }


@app.get("/api/audit/supervisor-status")
async def audit_supervisor_status():
    """Retorna o estado atual do supervisor OpenAI (telemetria gravada pelo
    agente em agent_config.openai_supervisor_stats). Util para debug:
    se 'cycles' aumenta mas 'problems_found' fica 0, significa que o
    modelo esta rodando e nao detectando problemas. Se 'errors' > 0,
    indica falha na chamada OpenAI (ver last_error)."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM agent_config WHERE key = 'openai_supervisor_stats'")
            row = cur.fetchone()
            cur.close()
            if not row or not row[0]:
                return {'ok': True, 'stats': None,
                        'hint': 'Supervisor ainda nao rodou ou nao persistiu stats'}
            try:
                stats = json.loads(row[0])
            except Exception:
                return {'ok': False, 'error': 'invalid_stats_json'}
            return {'ok': True, 'stats': stats}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@app.get("/api/audit/findings/{finding_id}/conversation")
async def audit_finding_conversation(finding_id: int, limit: int = Query(30, ge=5, le=100)):
    """Retorna o trecho da conversa associada a um finding, lendo de
    ia_interaction_log (logs locais do agente). Cada item:
      {created_at, role:'aluno'|'bot', body, action}

    Permite ao revisor ver a conversa direto no card sem precisar abrir
    o lead no DataCrazy.
    """
    _ensure_audit_table_api()
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT conv_id, phone, detail, created_at
            FROM agent_audit_findings WHERE id = %s
        """, (finding_id,))
        f = cur.fetchone()
        if not f:
            cur.close()
            raise HTTPException(404, 'finding nao encontrado')
        conv_id = f['conv_id'] or ''
        finding_ts = f.get('created_at')
        detail = f.get('detail') or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}

        # Janela: ate `limit` interacoes em torno do momento do finding.
        # Pegamos por conv_id, ordenadas por created_at, pegando as mais
        # recentes ATe o momento do finding (com pequeno buffer +5min depois).
        items = []
        if conv_id:
            cur.execute("""
                SELECT created_at, pergunta_recebida, resposta_gerada, acao
                FROM ia_interaction_log
                WHERE conversation_id = %s
                  AND (
                    %s::timestamp IS NULL
                    OR created_at <= %s::timestamp + interval '5 minutes'
                  )
                ORDER BY created_at DESC
                LIMIT %s
            """, (conv_id, finding_ts, finding_ts, limit))
            rows = cur.fetchall()
            # Inverte para cronologico ascendente
            rows = list(reversed(rows))
            from datetime import timezone as _tz
            for r in rows:
                ts = r['created_at']
                if hasattr(ts, 'tzinfo') and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_tz.utc)
                ts_iso = ts.isoformat() if ts else ''
                q = (r['pergunta_recebida'] or '').strip()
                a = (r['resposta_gerada'] or '').strip()
                act = r.get('acao') or ''
                # Cada interacao = mensagem do aluno (se houver) + resposta do bot
                if q and q != '(dedup)':
                    items.append({'created_at': ts_iso, 'role': 'aluno',
                                  'body': q, 'action': ''})
                if a:
                    items.append({'created_at': ts_iso, 'role': 'bot',
                                  'body': a, 'action': act})

        # tambem inclui o `window` do detail se existir (do supervisor OpenAI)
        win = detail.get('window') if isinstance(detail, dict) else None
        cur.close()
        return {
            'ok': True,
            'finding_id': finding_id,
            'conv_id': conv_id,
            'items': items,
            'supervisor_window': win if isinstance(win, list) else None,
        }


@app.post("/api/audit/findings/{finding_id}/fix")
async def audit_findings_auto_fix(finding_id: int, request: Request):
    """Auto-corrige o problema descrito no finding (sem precisar de
    intervencao manual no CRM). Por enquanto suporta:
    - assignment_mismatch: reaplica PATCH lead+business+change-attendant
      ate convergir (5 retries com backoff).

    Se a correcao convergir, marca o finding como resolved_by='auto-fix:<tipo>'.
    Se nao convergir, mantem em aberto e retorna o estado parcial p/ o front
    decidir se mostra como sucesso parcial / pede intervencao humana.
    """
    _ensure_audit_table_api()
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, conv_id, phone, problem_type, detail, resolved_at
            FROM agent_audit_findings WHERE id = %s
        """, (finding_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            raise HTTPException(404, 'finding nao encontrado')
        if row['resolved_at']:
            cur.close()
            return {'ok': True, 'already_resolved': True, 'id': finding_id}

        ptype = row['problem_type'] or ''
        detail = row['detail'] or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}

        conv_id = detail.get('conv_id') or row['conv_id'] or ''
        lead_id = detail.get('lead_id', '') or ''
        phone = detail.get('phone') or row['phone'] or ''
        expected_name = detail.get('expected_name', '') or ''

        # ---- DISPATCH ----
        if ptype == 'assignment_mismatch':
            if not expected_name or (not lead_id and not conv_id):
                cur.close()
                return {'ok': False, 'error': 'detail_incompleto',
                        'detail_keys': list(detail.keys())}
            fix_result = _dcz_perform_assignment_fix(
                conv_id=conv_id, lead_id=lead_id, phone=phone,
                expected_name=expected_name, max_retries=5,
            )
            success = bool(fix_result.get('ok_lead')
                           and fix_result.get('ok_biz')
                           and fix_result.get('ok_chat'))
            if success:
                cur.execute("""
                    UPDATE agent_audit_findings
                    SET resolved_at = NOW(), resolved_by = %s
                    WHERE id = %s
                """, (f'auto-fix:{ptype}', finding_id))
                conn.commit()
            cur.close()
            return {'ok': True, 'fixed': success, 'result': fix_result,
                    'id': finding_id}

        cur.close()
        return {'ok': False, 'error': 'no_handler_for_problem_type',
                'problem_type': ptype}


@app.post("/api/audit/findings/bulk-resolve")
async def audit_findings_bulk_resolve(request: Request):
    """Marca multiplos findings como resolvidos."""
    _ensure_audit_table_api()
    body = await request.json()
    ids = body.get('ids') or []
    unblock = bool(body.get('unblock'))
    resolved_by = (body.get('resolved_by') or 'cockpit').strip()[:64]
    if not ids:
        return {'ok': True, 'count': 0}
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE agent_audit_findings
            SET resolved_at = NOW(), resolved_by = %s
            WHERE id = ANY(%s)
            RETURNING conv_id
        """, (resolved_by, ids))
        conv_ids = [r[0] for r in cur.fetchall() if r[0]]
        if unblock and conv_ids:
            try:
                cur.execute(
                    "DELETE FROM handoff_active WHERE conv_id = ANY(%s) AND motivo = 'supervisor_block'",
                    (conv_ids,),
                )
            except Exception:
                pass
        conn.commit()
        cur.close()
    return {'ok': True, 'count': len(conv_ids), 'unblocked': unblock}


# ===================== CAA SOLICITAÇÕES =====================

def _clean_cpf(value) -> str:
    if value is None:
        return ''
    s = re.sub(r'\D', '', str(value))
    if not s:
        return ''
    if len(s) < 11:
        s = s.zfill(11)
    return s[:14]


def _to_date(value):
    if value is None or value == '':
        return None
    if hasattr(value, 'date'):
        try:
            return value.date()
        except Exception:
            pass
    if hasattr(value, 'year'):
        return value
    s = str(value).strip()
    if not s:
        return None
    from datetime import datetime as _dt
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d %H:%M:%S'):
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_int(value):
    if value is None or value == '':
        return None
    try:
        return int(float(str(value).replace(',', '.')))
    except (TypeError, ValueError):
        return None


# Header esperado (case-insensitive, aceita variações)
_CAA_COLS = {
    'rgm': ['RGM'],
    'aluno_nome': ['Aluno', 'Nome'],
    'cpf': ['CPF'],
    'polo': ['Polo'],
    'subprocesso': ['Subprocesso', 'Sub processo', 'Tipo'],
    'data_chegada': ['Data Chegada', 'Data de Chegada'],
    'data_previsao': ['Data Previsao', 'Data Previsão'],
    'data_conclusao': ['Data Conclusao', 'Data Conclusão'],
    'protocolo': ['Protocolo'],
    'aging_dias': ['Aging Dias', 'Aging', 'Dias'],
    'observacao': ['Observacao', 'Observação', 'Obs'],
    'situacao_atendimento': ['Situacao Atendimento', 'Situação Atendimento'],
    'situacao_deferimento': ['Situacao Deferimento', 'Situação Deferimento'],
    'celular': ['Celular', 'Telefone'],
    'email': ['Email', 'E-mail'],
    'curso': ['Curso'],
    'instituicao': ['Instituicao', 'Instituição'],
    'qtd_protocolos': ['Qtd. Protocolos', 'Qtd Protocolos', 'Quantidade'],
}


def _normalize_header(h):
    if not h:
        return ''
    import unicodedata
    s = str(h).strip()
    s = ''.join(c for c in unicodedata.normalize('NFD', s)
                if unicodedata.category(c) != 'Mn')
    return s.lower()


def _build_col_index(header_row):
    """Mapeia nome lógico -> índice da coluna na planilha."""
    normalized = [_normalize_header(h) for h in header_row]
    idx = {}
    for logical, candidates in _CAA_COLS.items():
        for cand in candidates:
            cand_norm = _normalize_header(cand)
            if cand_norm in normalized:
                idx[logical] = normalized.index(cand_norm)
                break
    return idx


@app.post("/api/caa/upload")
async def caa_upload(file: UploadFile = File(...)):
    """Recebe XLSX do SIAA, faz TRUNCATE + INSERT em transacao."""
    if not file.filename.lower().endswith(('.xlsx', '.xlsm')):
        raise HTTPException(400, "Arquivo deve ser .xlsx")
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(500, "openpyxl nao instalado no servidor")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Arquivo vazio")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "Arquivo maior que 50MB")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        raise HTTPException(400, f"Falha ao abrir XLSX: {e}")

    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        raise HTTPException(400, "Planilha sem cabeçalho")

    col_idx = _build_col_index(header)
    missing_required = [k for k in ('cpf', 'subprocesso', 'situacao_atendimento') if k not in col_idx]
    if missing_required:
        raise HTTPException(
            400,
            f"Colunas obrigatórias ausentes: {', '.join(missing_required)}. "
            f"Cabeçalho recebido: {[str(h) for h in header]}"
        )

    parsed = []
    open_count = pending_count = concluded_count = 0
    skipped = 0
    for row in rows_iter:
        if not row:
            continue
        try:
            cpf = _clean_cpf(row[col_idx['cpf']]) if 'cpf' in col_idx else ''
            sub = row[col_idx['subprocesso']] if 'subprocesso' in col_idx else None
        except IndexError:
            skipped += 1
            continue
        if not cpf and not sub:
            skipped += 1
            continue

        def g(key):
            i = col_idx.get(key)
            if i is None or i >= len(row):
                return None
            return row[i]

        sit_at = (g('situacao_atendimento') or '')
        sit_def = (g('situacao_deferimento') or '')
        sit_at_s = str(sit_at).strip().upper()
        sit_def_s = str(sit_def).strip().lower()

        if sit_at_s == 'PENDENTE':
            pending_count += 1
        if 'em aberto' in sit_def_s:
            open_count += 1
        if sit_at_s == 'CONCLUIDO' or sit_at_s == 'CONCLUÍDO':
            concluded_count += 1

        parsed.append((
            cpf,
            str(g('rgm') or '')[:32],
            str(g('aluno_nome') or '')[:255],
            str(g('polo') or '')[:255],
            str(sub or '')[:255],
            _to_date(g('data_chegada')),
            _to_date(g('data_previsao')),
            _to_date(g('data_conclusao')),
            str(g('protocolo') or '')[:64],
            _to_int(g('aging_dias')),
            str(g('observacao') or '')[:8000],
            str(sit_at or '')[:64],
            str(sit_def or '')[:64],
            str(g('celular') or '')[:20],
            str(g('email') or '')[:255],
            str(g('curso') or '')[:255],
            str(g('instituicao') or '')[:255],
            _to_int(g('qtd_protocolos')),
        ))

    if not parsed:
        raise HTTPException(400, "Nenhuma linha válida na planilha")

    try:
        with get_db() as conn:
            cur = conn.cursor()
            _ensure_caa_table(cur)
            cur.execute("TRUNCATE TABLE caa_solicitacoes")
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO caa_solicitacoes
                    (cpf, rgm, aluno_nome, polo, subprocesso,
                     data_chegada, data_previsao, data_conclusao,
                     protocolo, aging_dias, observacao,
                     situacao_atendimento, situacao_deferimento,
                     celular, email, curso, instituicao, qtd_protocolos)
                VALUES %s
                """,
                parsed,
                page_size=500,
            )
            cur.execute(
                """
                INSERT INTO caa_import_history
                    (total_rows, open_count, pending_count, concluded_count, filename, uploaded_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, imported_at
                """,
                (len(parsed), open_count, pending_count, concluded_count, file.filename[:255], 'cockpit'),
            )
            hist_row = cur.fetchone()
            conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Erro ao importar: {e}")

    return {
        'ok': True,
        'total': len(parsed),
        'open': open_count,
        'pending': pending_count,
        'concluded': concluded_count,
        'skipped': skipped,
        'import_id': hist_row[0],
        'imported_at': hist_row[1].isoformat() if hist_row[1] else None,
        'filename': file.filename,
    }


@app.get("/api/caa/status")
async def caa_status():
    """Retorna status da ultima importacao + contagens atuais."""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ensure_caa_table(cur)
        conn.commit()
        cur.execute("SELECT COUNT(*) AS cnt FROM caa_solicitacoes")
        total = cur.fetchone()['cnt']
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE LOWER(situacao_deferimento) LIKE %s) AS open_count,
                COUNT(*) FILTER (WHERE UPPER(situacao_atendimento) = 'PENDENTE') AS pending_count,
                COUNT(*) FILTER (WHERE UPPER(situacao_atendimento) IN ('CONCLUIDO','CONCLUÍDO')) AS concluded_count,
                COUNT(*) FILTER (WHERE UPPER(situacao_atendimento) = 'CANCELADO') AS cancelled_count
            FROM caa_solicitacoes
        """, ('%em aberto%',))
        agg = dict(cur.fetchone() or {})
        cur.execute("""
            SELECT id, imported_at, total_rows, open_count, pending_count, concluded_count,
                   filename, uploaded_by
            FROM caa_import_history
            ORDER BY imported_at DESC
            LIMIT 5
        """)
        history = []
        for r in cur.fetchall():
            item = dict(r)
            if item.get('imported_at'):
                item['imported_at'] = item['imported_at'].isoformat()
            history.append(item)
        return {
            'total': total,
            'open': agg.get('open_count', 0),
            'pending': agg.get('pending_count', 0),
            'concluded': agg.get('concluded_count', 0),
            'cancelled': agg.get('cancelled_count', 0),
            'last_import': history[0] if history else None,
            'history': history,
        }


@app.get("/api/caa/by-cpf/{cpf}")
async def caa_by_cpf(cpf: str, limit: int = Query(20, ge=1, le=100)):
    """Lista solicitacoes de um CPF (debug/preview)."""
    clean = _clean_cpf(cpf)
    if not clean or len(clean) != 11:
        raise HTTPException(400, "CPF inválido")
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ensure_caa_table(cur)
        conn.commit()
        cur.execute("""
            SELECT id, cpf, rgm, aluno_nome, polo, subprocesso,
                   data_chegada, data_previsao, data_conclusao,
                   protocolo, aging_dias, observacao,
                   situacao_atendimento, situacao_deferimento,
                   celular, email, curso, instituicao, qtd_protocolos
            FROM caa_solicitacoes WHERE cpf = %s
            ORDER BY data_chegada DESC NULLS LAST
            LIMIT %s
        """, (clean, limit))
        items = []
        for r in cur.fetchall():
            item = dict(r)
            for k in ('data_chegada', 'data_previsao', 'data_conclusao'):
                if item.get(k):
                    item[k] = item[k].isoformat()
            items.append(item)
        return {'cpf': clean, 'count': len(items), 'items': items}


@app.get("/api/caa/list")
async def caa_list(
    search: str = Query('', max_length=120),
    situacao: str = Query('', max_length=32),
    deferimento: str = Query('', max_length=32),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
):
    """Lista paginada para o painel."""
    offset = (page - 1) * per_page
    where = ['1=1']
    params = []
    if search:
        clean_search = re.sub(r'\D', '', search)
        if len(clean_search) >= 6:
            where.append("cpf LIKE %s")
            params.append('%' + clean_search + '%')
        else:
            where.append("(LOWER(aluno_nome) LIKE %s OR LOWER(subprocesso) LIKE %s OR protocolo LIKE %s)")
            like = '%' + search.lower() + '%'
            params.extend([like, like, '%' + search + '%'])
    if situacao:
        where.append("UPPER(situacao_atendimento) = %s")
        params.append(situacao.upper())
    if deferimento:
        where.append("LOWER(situacao_deferimento) LIKE %s")
        params.append('%' + deferimento.lower() + '%')
    w = ' AND '.join(where)
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ensure_caa_table(cur)
        conn.commit()
        cur.execute(f"SELECT COUNT(*) AS cnt FROM caa_solicitacoes WHERE {w}", params)
        total = cur.fetchone()['cnt']
        cur.execute(f"""
            SELECT id, cpf, rgm, aluno_nome, polo, subprocesso,
                   data_chegada, aging_dias,
                   situacao_atendimento, situacao_deferimento, protocolo, curso
            FROM caa_solicitacoes
            WHERE {w}
            ORDER BY data_chegada DESC NULLS LAST, id DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])
        items = []
        for r in cur.fetchall():
            item = dict(r)
            if item.get('data_chegada'):
                item['data_chegada'] = item['data_chegada'].isoformat()
            items.append(item)
        return {
            'items': items,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page if total else 0,
        }


# ===================== CALENDARIO ACADEMICO 2026 =====================
# Endpoints CRUD para a tabela academic_calendar_2026 (Graduacao EaD).
# Tabela criada/seed automaticamente pelo agente no startup; aqui apenas
# expomos leitura/edicao para o Cockpit.

def _ensure_academic_calendar_table_local(cur):
    """Espelho de agente_ao_vivo_v4._ensure_academic_calendar_table.

    Garante existencia da tabela aqui no kb_api para que o admin consiga
    listar/editar mesmo se o agente nao tiver subido ainda.
    """
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


@app.get("/api/calendar")
async def calendar_list(
    categoria: str = Query('', max_length=64),
    semestre: str = Query('', max_length=16),
    search: str = Query('', max_length=120),
    ativo: str = Query('1', max_length=2),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """Lista paginada dos eventos do calendario."""
    offset = (page - 1) * per_page
    where = []
    params = []
    if ativo in ('1', 'true', 'True'):
        where.append("ativo = TRUE")
    elif ativo in ('0', 'false', 'False'):
        where.append("ativo = FALSE")
    if categoria:
        where.append("categoria = %s")
        params.append(categoria)
    if semestre:
        where.append("semestre = %s")
        params.append(semestre)
    if search:
        where.append("(LOWER(titulo) LIKE %s OR LOWER(observacao) LIKE %s)")
        like = '%' + search.lower() + '%'
        params.extend([like, like])
    w = ' AND '.join(where) if where else '1=1'
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ensure_academic_calendar_table_local(cur)
        conn.commit()
        cur.execute(f"SELECT COUNT(*) AS cnt FROM academic_calendar_2026 WHERE {w}", params)
        total = cur.fetchone()['cnt']
        cur.execute(f"""
            SELECT id, categoria, titulo, data_inicio, data_fim,
                   mes_ref, semestre, publico, observacao, ativo,
                   created_at, updated_at
            FROM academic_calendar_2026
            WHERE {w}
            ORDER BY data_inicio ASC, id ASC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])
        items = []
        for r in cur.fetchall():
            item = dict(r)
            for k in ('data_inicio', 'data_fim', 'created_at', 'updated_at'):
                if item.get(k) is not None:
                    item[k] = item[k].isoformat()
            items.append(item)
        return {
            'items': items,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page if total else 0,
        }


@app.get("/api/calendar/summary")
async def calendar_summary():
    """Resumo: total ativos, por categoria, por semestre."""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ensure_academic_calendar_table_local(cur)
        conn.commit()
        cur.execute("SELECT COUNT(*) AS cnt FROM academic_calendar_2026 WHERE ativo = TRUE")
        total = cur.fetchone()['cnt']
        cur.execute("""
            SELECT categoria, COUNT(*) AS cnt
            FROM academic_calendar_2026 WHERE ativo = TRUE
            GROUP BY categoria ORDER BY cnt DESC
        """)
        by_cat = [dict(r) for r in cur.fetchall()]
        cur.execute("""
            SELECT semestre, COUNT(*) AS cnt
            FROM academic_calendar_2026
            WHERE ativo = TRUE AND semestre IS NOT NULL
            GROUP BY semestre ORDER BY semestre
        """)
        by_sem = [dict(r) for r in cur.fetchall()]
        cur.execute("""
            SELECT id, categoria, titulo, data_inicio, data_fim, semestre
            FROM academic_calendar_2026
            WHERE ativo = TRUE AND data_inicio >= CURRENT_DATE
            ORDER BY data_inicio ASC LIMIT 6
        """)
        upcoming = []
        for r in cur.fetchall():
            item = dict(r)
            for k in ('data_inicio', 'data_fim'):
                if item.get(k):
                    item[k] = item[k].isoformat()
            upcoming.append(item)
        return {
            'total_ativos': total,
            'por_categoria': by_cat,
            'por_semestre': by_sem,
            'proximos': upcoming,
        }


@app.post("/api/calendar")
async def calendar_create(req: Request):
    """Cria um novo evento."""
    body = await req.json()
    categoria = (body.get('categoria') or '').strip()
    titulo = (body.get('titulo') or '').strip()
    data_inicio = body.get('data_inicio')
    if not categoria or not titulo or not data_inicio:
        raise HTTPException(400, "categoria, titulo e data_inicio sao obrigatorios")
    data_fim = body.get('data_fim') or None
    mes_ref = (body.get('mes_ref') or '').strip() or None
    semestre = (body.get('semestre') or '').strip() or None
    publico = (body.get('publico') or 'todos').strip()
    observacao = (body.get('observacao') or '').strip()
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ensure_academic_calendar_table_local(cur)
        try:
            cur.execute("""
                INSERT INTO academic_calendar_2026
                    (categoria, titulo, data_inicio, data_fim, mes_ref,
                     semestre, publico, observacao, ativo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (categoria, titulo, data_inicio) DO UPDATE SET
                    data_fim = EXCLUDED.data_fim,
                    mes_ref = EXCLUDED.mes_ref,
                    semestre = EXCLUDED.semestre,
                    publico = EXCLUDED.publico,
                    observacao = EXCLUDED.observacao,
                    ativo = TRUE,
                    updated_at = NOW()
                RETURNING id
            """, (categoria, titulo, data_inicio, data_fim, mes_ref,
                  semestre, publico, observacao))
            new_id = cur.fetchone()['id']
            conn.commit()
            return {'ok': True, 'id': new_id}
        except Exception as e:
            conn.rollback()
            raise HTTPException(500, f"Erro ao criar evento: {e}")


@app.put("/api/calendar/{event_id}")
async def calendar_update(event_id: int, req: Request):
    """Edita um evento existente."""
    body = await req.json()
    fields = []
    params = []
    for k in ('categoria', 'titulo', 'data_inicio', 'data_fim',
              'mes_ref', 'semestre', 'publico', 'observacao', 'ativo'):
        if k in body:
            fields.append(f"{k} = %s")
            params.append(body[k])
    if not fields:
        raise HTTPException(400, "Nenhum campo para atualizar")
    fields.append("updated_at = NOW()")
    params.append(event_id)
    with get_db() as conn:
        cur = conn.cursor()
        _ensure_academic_calendar_table_local(cur)
        try:
            cur.execute(
                f"UPDATE academic_calendar_2026 SET {', '.join(fields)} WHERE id = %s",
                params,
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Evento nao encontrado")
            conn.commit()
            return {'ok': True}
        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(500, f"Erro ao atualizar evento: {e}")


@app.delete("/api/calendar/{event_id}")
async def calendar_delete(event_id: int):
    """Soft-delete: marca ativo=FALSE."""
    with get_db() as conn:
        cur = conn.cursor()
        _ensure_academic_calendar_table_local(cur)
        cur.execute(
            "UPDATE academic_calendar_2026 SET ativo = FALSE, updated_at = NOW() WHERE id = %s",
            (event_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Evento nao encontrado")
        conn.commit()
        return {'ok': True}


@app.post("/api/calendar/seed")
async def calendar_seed():
    """Forca recarga do seed canonico (INSERT ON CONFLICT DO NOTHING).

    Util quando se quer garantir presenca dos eventos oficiais. Nao
    sobrescreve eventos ja existentes nem reativa os desativados.
    """
    try:
        from calendar_2026_seed import get_seed_events
    except Exception as e:
        raise HTTPException(500, f"Seed module indisponivel: {e}")
    events = get_seed_events()
    inserted = 0
    with get_db() as conn:
        cur = conn.cursor()
        _ensure_academic_calendar_table_local(cur)
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
            except Exception:
                pass
        conn.commit()
    return {'ok': True, 'seed_size': len(events), 'inserted': inserted}


@app.get("/api/memory/list")
async def list_memories(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):
    """Lista memórias de alunos."""
    offset = (page - 1) * per_page
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT count(*) as cnt FROM student_memory")
        total = cur.fetchone()['cnt']
        cur.execute("SELECT * FROM student_memory ORDER BY last_contact_at DESC LIMIT %s OFFSET %s", (per_page, offset))
        items = cur.fetchall()
        for i in items:
            for k in ('first_contact_at', 'last_contact_at', 'updated_at'):
                if i.get(k):
                    i[k] = i[k].isoformat()
        return {'items': items, 'total': total, 'page': page, 'pages': (total + per_page - 1) // per_page}


# ===================== BLOCO A: GESTÃO DE CONTEÚDO =====================

@app.get("/api/export/csv")
async def export_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', 'pergunta_aluno', 'resposta_atendente', 'tema', 'whatsapp_buttons'])
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, pergunta_aluno, resposta_atendente, tema, whatsapp_buttons, media_attachments FROM knowledge_base ORDER BY id")
        for row in cur.fetchall():
            writer.writerow(row)
    content = output.getvalue().encode('utf-8-sig')
    return JSONResponse(content={'csv': output.getvalue()}, headers={
        'Content-Disposition': 'attachment; filename=knowledge_base.csv'
    })


@app.get("/api/export/json")
async def export_json():
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, pergunta_aluno, resposta_atendente, tema, whatsapp_buttons, media_attachments FROM knowledge_base ORDER BY id")
        items = cur.fetchall()
    return {'items': items, 'total': len(items)}


class BulkDeleteRequest(BaseModel):
    ids: List[int]

class BulkUpdateTemaRequest(BaseModel):
    ids: List[int]
    tema: str

class MergeRequest(BaseModel):
    keep_id: int
    delete_id: int
    merged_question: Optional[str] = None
    merged_answer: Optional[str] = None


@app.post("/api/qa/bulk-delete")
async def bulk_delete_qa(data: BulkDeleteRequest):
    if not data.ids:
        raise HTTPException(400, "Nenhum ID fornecido")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM knowledge_base WHERE id = ANY(%s)", (data.ids,))
        deleted = cur.rowcount
        conn.commit()
    return {'deleted': deleted}


@app.post("/api/qa/bulk-update-tema")
async def bulk_update_tema(data: BulkUpdateTemaRequest):
    if not data.ids:
        raise HTTPException(400, "Nenhum ID fornecido")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE knowledge_base SET tema = %s WHERE id = ANY(%s)", (data.tema, data.ids))
        updated = cur.rowcount
        conn.commit()
    return {'updated': updated}


@app.post("/api/qa/regenerate-embeddings")
async def regenerate_embeddings():
    client = OpenAI(api_key=OPENAI_API_KEY)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, pergunta_aluno FROM knowledge_base")
        rows = cur.fetchall()
        count = 0
        for rid, pergunta in rows:
            try:
                emb = client.embeddings.create(input=pergunta[:2000], model='text-embedding-3-small', dimensions=256).data[0].embedding
                emb_str = ','.join(str(x) for x in emb)
                cur.execute(f"UPDATE knowledge_base SET embedding = ARRAY[{emb_str}]::float8[] WHERE id = %s", (rid,))
                count += 1
            except Exception:
                pass
        conn.commit()
    return {'regenerated': count, 'total': len(rows)}


@app.post("/api/qa/merge")
async def merge_qa(data: MergeRequest):
    client = OpenAI(api_key=OPENAI_API_KEY)
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM knowledge_base WHERE id = %s", (data.keep_id,))
        keep = cur.fetchone()
        cur.execute("SELECT * FROM knowledge_base WHERE id = %s", (data.delete_id,))
        delete_row = cur.fetchone()
        if not keep or not delete_row:
            raise HTTPException(404, "Q&A não encontrado")

        merged_q = data.merged_question or keep['pergunta_aluno']
        merged_a = data.merged_answer or keep['resposta_atendente']

        emb = client.embeddings.create(input=merged_q[:2000], model='text-embedding-3-small', dimensions=256).data[0].embedding
        emb_str = ','.join(str(x) for x in emb)
        cur.execute(f"UPDATE knowledge_base SET pergunta_aluno=%s, resposta_atendente=%s, embedding=ARRAY[{emb_str}]::float8[] WHERE id=%s",
                    (merged_q, merged_a, data.keep_id))
        cur.execute("DELETE FROM knowledge_base WHERE id = %s", (data.delete_id,))
        conn.commit()
    return {'message': 'Mesclado com sucesso', 'kept_id': data.keep_id}


@app.post("/api/import/csv/preview")
async def preview_csv(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for i, row in enumerate(reader):
        if i >= 10:
            break
        rows.append(row)
    total_lines = text.count('\n')
    return {'preview': rows, 'total_estimated': total_lines, 'columns': reader.fieldnames or []}


# ===================== BLOCO A: FILTROS =====================

@app.get("/api/analytics/filtered")
async def get_analytics_filtered(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    date_filter = ""
    params = []
    if start_date:
        date_filter += " AND created_at >= %s"
        params.append(start_date)
    if end_date:
        date_filter += " AND created_at <= %s"
        params.append(end_date + ' 23:59:59')

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(f"SELECT count(*) as total FROM ia_interaction_log WHERE 1=1 {date_filter}", params)
        total = cur.fetchone()['total']

        cur.execute(f"SELECT acao, count(*) as cnt FROM ia_interaction_log WHERE 1=1 {date_filter} GROUP BY acao ORDER BY cnt DESC", params)
        by_action = [dict(r) for r in cur.fetchall()]

        cur.execute(f"SELECT avg(confianca) as avg_conf FROM ia_interaction_log WHERE confianca IS NOT NULL {date_filter}", params)
        avg_conf = cur.fetchone()['avg_conf'] or 0

        cur.execute(f"""SELECT date(created_at) as day, count(*) as cnt, avg(confianca) as avg_conf
            FROM ia_interaction_log WHERE 1=1 {date_filter}
            GROUP BY date(created_at) ORDER BY day DESC LIMIT 30""", params)
        timeline = [{'day': str(r['day']), 'count': r['cnt'], 'avg_conf': round(float(r['avg_conf'] or 0), 2)} for r in cur.fetchall()]

        cur.execute(f"""SELECT avaliacao, count(*) as cnt FROM chat_evaluations WHERE 1=1 {date_filter}
            GROUP BY avaliacao ORDER BY cnt DESC""", params)
        evals = [dict(r) for r in cur.fetchall()]

        return {'total': total, 'by_action': by_action, 'avg_confidence': round(float(avg_conf), 2),
                'timeline': timeline, 'evaluations': evals}


@app.get("/api/interactions/filtered")
async def list_interactions_filtered(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    acao: Optional[str] = None,
    min_conf: Optional[float] = None,
    max_conf: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    offset = (page - 1) * per_page
    where = ["1=1"]
    params = []
    if search:
        where.append("(pergunta_recebida ILIKE %s OR resposta_gerada ILIKE %s)")
        params.extend([f'%{search}%', f'%{search}%'])
    if acao:
        where.append("acao = %s")
        params.append(acao)
    if min_conf is not None:
        where.append("confianca >= %s")
        params.append(min_conf)
    if max_conf is not None:
        where.append("confianca <= %s")
        params.append(max_conf)
    if start_date:
        where.append("created_at >= %s")
        params.append(start_date)
    if end_date:
        where.append("created_at <= %s")
        params.append(end_date + ' 23:59:59')

    where_sql = " AND ".join(where)
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT count(*) as cnt FROM ia_interaction_log WHERE {where_sql}", params)
        total = cur.fetchone()['cnt']
        cur.execute(f"SELECT * FROM ia_interaction_log WHERE {where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [per_page, offset])
        items = cur.fetchall()
        for i in items:
            if i.get('created_at'):
                i['created_at'] = i['created_at'].isoformat()
        return {'items': items, 'total': total, 'page': page, 'pages': (total + per_page - 1) // per_page}


# ===================== BLOCO B: SENTIMENTO =====================

@app.get("/api/sentiment/dashboard")
async def sentiment_dashboard(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    date_filter = ""
    params = []
    if start_date:
        date_filter += " AND created_at >= %s"
        params.append(start_date)
    if end_date:
        date_filter += " AND created_at <= %s"
        params.append(end_date + ' 23:59:59')

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(f"SELECT count(*) as total FROM interaction_summary WHERE 1=1 {date_filter}", params)
        total = cur.fetchone()['total']

        cur.execute(f"SELECT count(*) as cnt FROM interaction_summary WHERE sentimento = 'frustrado' {date_filter}", params)
        frustrated = cur.fetchone()['cnt']

        cur.execute(f"SELECT count(*) as cnt FROM interaction_summary WHERE nps_implicito <= 6 {date_filter}", params)
        detractors = cur.fetchone()['cnt']

        cur.execute(f"SELECT count(*) as cnt FROM interaction_summary WHERE nps_implicito >= 9 {date_filter}", params)
        promoters = cur.fetchone()['cnt']

        cur.execute(f"SELECT avg(nps_implicito) as avg_nps FROM interaction_summary WHERE nps_implicito IS NOT NULL {date_filter}", params)
        avg_nps = cur.fetchone()['avg_nps'] or 0

        cur.execute(f"SELECT count(*) as cnt FROM interaction_summary WHERE resolvido = 'sim' {date_filter}", params)
        resolved = cur.fetchone()['cnt']

        cur.execute(f"""SELECT date(created_at) as day, sentimento, count(*) as cnt
            FROM interaction_summary WHERE sentimento IS NOT NULL {date_filter}
            GROUP BY date(created_at), sentimento ORDER BY day""", params)
        sentiment_timeline = []
        for r in cur.fetchall():
            sentiment_timeline.append({'day': str(r['day']), 'sentimento': r['sentimento'], 'count': r['cnt']})

        cur.execute(f"""SELECT
            CASE WHEN nps_implicito <= 6 THEN 'detrator'
                 WHEN nps_implicito <= 8 THEN 'neutro'
                 ELSE 'promotor' END as grupo,
            count(*) as cnt
            FROM interaction_summary WHERE nps_implicito IS NOT NULL {date_filter}
            GROUP BY grupo ORDER BY grupo""", params)
        nps_distribution = [dict(r) for r in cur.fetchall()]

        cur.execute(f"""SELECT tema, count(*) as cnt
            FROM interaction_summary WHERE sentimento = 'frustrado' {date_filter}
            GROUP BY tema ORDER BY cnt DESC LIMIT 10""", params)
        frustrated_topics = [dict(r) for r in cur.fetchall()]

        cur.execute(f"""SELECT phone, student_name, count(*) as cnt, avg(nps_implicito) as avg_nps
            FROM interaction_summary
            WHERE (sentimento = 'frustrado' OR nps_implicito <= 6) {date_filter}
            GROUP BY phone, student_name HAVING count(*) >= 2
            ORDER BY cnt DESC LIMIT 20""", params)
        repeat_detractors = []
        for r in cur.fetchall():
            repeat_detractors.append({
                'phone': r['phone'], 'name': r['student_name'],
                'count': r['cnt'], 'avg_nps': round(float(r['avg_nps'] or 0), 1)
            })

        cur.execute(f"""SELECT * FROM interaction_summary
            WHERE (sentimento = 'frustrado' OR nps_implicito <= 6) {date_filter}
            ORDER BY created_at DESC LIMIT 20""", params)
        recent_alerts = []
        for r in cur.fetchall():
            item = dict(r)
            if item.get('created_at'):
                item['created_at'] = item['created_at'].isoformat()
            recent_alerts.append(item)

        resolution_rate = round(resolved / total * 100, 1) if total > 0 else 0

        return {
            'total': total,
            'frustrated': frustrated,
            'detractors': detractors,
            'promoters': promoters,
            'avg_nps': round(float(avg_nps), 1),
            'resolution_rate': resolution_rate,
            'sentiment_timeline': sentiment_timeline,
            'nps_distribution': nps_distribution,
            'frustrated_topics': frustrated_topics,
            'repeat_detractors': repeat_detractors,
            'recent_alerts': recent_alerts,
        }


# ===================== AVALIAÇÃO DE RESPOSTAS =====================

@app.get("/api/sentiment/responses")
async def sentiment_responses(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    avaliacao: Optional[str] = None,
    tema: Optional[str] = None,
    sentimento: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    filters = " WHERE pergunta_aluno IS NOT NULL AND pergunta_aluno != ''"
    params = []
    if avaliacao == 'pendente':
        filters += " AND (avaliacao IS NULL OR avaliacao = '')"
    elif avaliacao:
        filters += " AND avaliacao = %s"
        params.append(avaliacao)
    if tema:
        filters += " AND tema = %s"
        params.append(tema)
    if sentimento:
        filters += " AND sentimento = %s"
        params.append(sentimento)
    if search:
        filters += " AND (student_name ILIKE %s OR pergunta_aluno ILIKE %s OR resposta_agente ILIKE %s OR phone ILIKE %s)"
        params.extend([f'%{search}%'] * 4)
    if start_date:
        filters += " AND created_at >= %s"
        params.append(start_date)
    if end_date:
        filters += " AND created_at <= %s"
        params.append(end_date + ' 23:59:59')

    offset = (page - 1) * per_page
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT count(*) as cnt FROM interaction_summary {filters}", params)
        total = cur.fetchone()['cnt']

        cur.execute(f"""SELECT count(*) FILTER (WHERE avaliacao IS NULL OR avaliacao = '') as pendentes,
            count(*) FILTER (WHERE avaliacao = 'aprovada') as aprovadas,
            count(*) FILTER (WHERE avaliacao = 'reprovada') as reprovadas
            FROM interaction_summary WHERE pergunta_aluno IS NOT NULL AND pergunta_aluno != ''""")
        counters = dict(cur.fetchone())

        cur.execute(f"""SELECT id, phone, student_name, tema, subtema, sentimento, resolvido,
            nps_implicito, pergunta_aluno, resposta_agente, avaliacao, conv_id, created_at
            FROM interaction_summary {filters}
            ORDER BY created_at DESC LIMIT %s OFFSET %s""", params + [per_page, offset])
        rows = cur.fetchall()
        for r in rows:
            if r.get('created_at'):
                r['created_at'] = r['created_at'].isoformat()

        return {
            'total': total, 'page': page, 'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'counters': counters, 'rows': rows,
        }


@app.post("/api/sentiment/responses/{record_id}/avaliar")
async def avaliar_resposta(record_id: int, request: Request):
    body = await request.json()
    avaliacao = body.get('avaliacao', '')
    if avaliacao not in ('aprovada', 'reprovada', ''):
        raise HTTPException(400, "Avaliação deve ser 'aprovada', 'reprovada' ou vazio")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE interaction_summary SET avaliacao = %s WHERE id = %s",
                    (avaliacao or None, record_id))
        conn.commit()
    return {'ok': True, 'id': record_id, 'avaliacao': avaliacao}


# ===================== CONVERSAS ANALYTICS =====================

@app.get("/api/conversations/analytics")
async def conversations_analytics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    tema: Optional[str] = None,
    acao: Optional[str] = None,
    sentimento: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    filters = " WHERE 1=1"
    params = []

    if start_date:
        filters += " AND s.created_at >= %s"
        params.append(start_date)
    if end_date:
        filters += " AND s.created_at <= %s"
        params.append(end_date + ' 23:59:59')
    if tema:
        filters += " AND s.tema = %s"
        params.append(tema)
    if sentimento:
        filters += " AND s.sentimento = %s"
        params.append(sentimento)

    log_filters = " WHERE 1=1"
    log_params = []
    if start_date:
        log_filters += " AND created_at >= %s"
        log_params.append(start_date)
    if end_date:
        log_filters += " AND created_at <= %s"
        log_params.append(end_date + ' 23:59:59')
    if acao:
        log_filters += " AND acao = %s"
        log_params.append(acao)

    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(f"SELECT count(*) as total FROM interaction_summary s {filters}", params)
        total = cur.fetchone()['total']

        cur.execute(f"SELECT count(*) as cnt FROM interaction_summary s {filters} AND resolvido = 'sim'", params)
        resolved = cur.fetchone()['cnt']

        cur.execute(f"SELECT avg(nps_implicito) as avg_nps FROM interaction_summary s {filters} AND nps_implicito IS NOT NULL", params)
        avg_nps = cur.fetchone()['avg_nps'] or 0

        cur.execute(f"SELECT count(*) as cnt FROM ia_interaction_log {log_filters} AND acao = 'retention'", log_params)
        retentions = cur.fetchone()['cnt']

        cur.execute(f"""SELECT tema, count(*) as cnt FROM interaction_summary s {filters}
            AND tema IS NOT NULL GROUP BY tema ORDER BY cnt DESC""", params)
        by_tema = [{'tema': r['tema'], 'count': r['cnt']} for r in cur.fetchall()]

        cur.execute(f"""SELECT acao, count(*) as cnt FROM ia_interaction_log {log_filters}
            GROUP BY acao ORDER BY cnt DESC""", log_params)
        by_acao = [{'acao': r['acao'], 'count': r['cnt']} for r in cur.fetchall()]

        cur.execute(f"""SELECT date(s.created_at) as day, count(*) as cnt
            FROM interaction_summary s {filters}
            GROUP BY date(s.created_at) ORDER BY day""", params)
        by_day = [{'day': str(r['day']), 'count': r['cnt']} for r in cur.fetchall()]

        cur.execute(f"""SELECT sentimento, count(*) as cnt FROM interaction_summary s {filters}
            AND sentimento IS NOT NULL GROUP BY sentimento ORDER BY cnt DESC""", params)
        by_sentimento = [{'sentimento': r['sentimento'], 'count': r['cnt']} for r in cur.fetchall()]

        cur.execute(f"""SELECT tema, subtema, count(*) as cnt FROM interaction_summary s {filters}
            AND subtema IS NOT NULL GROUP BY tema, subtema ORDER BY cnt DESC LIMIT 15""", params)
        top_subtemas = [{'tema': r['tema'], 'subtema': r['subtema'], 'count': r['cnt']} for r in cur.fetchall()]

        offset = (page - 1) * per_page
        cur.execute(f"""SELECT s.* FROM interaction_summary s {filters}
            ORDER BY s.created_at DESC LIMIT %s OFFSET %s""", params + [per_page, offset])
        details = []
        for r in cur.fetchall():
            item = dict(r)
            if item.get('created_at'):
                item['created_at'] = item['created_at'].isoformat()
            details.append(item)

        resolution_rate = round(resolved / total * 100, 1) if total > 0 else 0

        temas_list = [t['tema'] for t in by_tema]
        sentimentos_list = list(set(s['sentimento'] for s in by_sentimento))
        acoes_list = [a['acao'] for a in by_acao]

        return {
            'summary': {
                'total': total,
                'resolved': resolved,
                'resolution_rate': resolution_rate,
                'avg_nps': round(float(avg_nps), 1),
                'retentions': retentions,
            },
            'by_tema': by_tema,
            'by_acao': by_acao,
            'by_day': by_day,
            'by_sentimento': by_sentimento,
            'top_subtemas': top_subtemas,
            'details': details,
            'details_total': total,
            'details_page': page,
            'details_per_page': per_page,
            'filter_options': {
                'temas': temas_list,
                'sentimentos': sentimentos_list,
                'acoes': acoes_list,
            }
        }


# ===================== BLOCO C: EQUIPE (SUPABASE) =====================

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')
SUPABASE_HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
SUPABASE_TABLE = 'distribuicao_academico_duplicate'


def supabase_get(endpoint, params=None):
    r = http_requests.get(f'{SUPABASE_URL}/rest/v1/{endpoint}', headers=SUPABASE_HEADERS, params=params or {}, timeout=10)
    return r.json() if r.status_code == 200 else []


@app.get("/api/agents/status")
async def agents_status():
    from datetime import datetime
    agents = supabase_get(SUPABASE_TABLE, {'select': '*', 'order': 'responsavel'})
    now = datetime.now().strftime('%H:%M:%S')
    result = []
    for a in agents:
        almoco = a.get('almoco', '12:00:00')
        almoco_fim = a.get('almoco_real', '13:00:00')
        fim_exp = a.get('final_expediente', '18:00:00')
        status = 'inativo'
        if a.get('ativo_inativo') == 'Ativo':
            if almoco and almoco_fim and almoco <= now <= almoco_fim:
                status = 'almoco'
            elif fim_exp and now > fim_exp:
                status = 'encerrado'
            elif fim_exp and now >= fim_exp.replace(fim_exp[-2:], str(max(0, int(fim_exp[-5:-3]) - 0)).zfill(2)):
                mins_left = 0
                try:
                    from datetime import datetime as dt
                    t_now = dt.strptime(now, '%H:%M:%S')
                    t_end = dt.strptime(fim_exp, '%H:%M:%S')
                    mins_left = (t_end - t_now).total_seconds() / 60
                except Exception:
                    pass
                if mins_left <= 15 and mins_left > 0:
                    status = 'encerrando'
                else:
                    status = 'ativo'
            else:
                status = 'ativo'
        result.append({
            'nome': a.get('responsavel', ''),
            'status': status,
            'ativo_inativo': a.get('ativo_inativo', ''),
            'almoco': almoco,
            'almoco_real': almoco_fim,
            'final_expediente': fim_exp,
            'fila': a.get('fila', 0),
            'tipo_atendimento': a.get('tipo_atendimento', ''),
            'pausa_distribuicao': a.get('pausa_distribuicao', 0),
            'volume_distribuicao': a.get('volume_distribuicao', 0),
            'status_final': a.get('status_final', ''),
            'status_almoco': a.get('status_almoco', ''),
            'ultima_execucao': a.get('ultima_execucao', ''),
        })
    return {'agents': result, 'server_time': now}


# ===================== BLOCO D: ALUNOS =====================

@app.delete("/api/memory/{phone}")
async def delete_memory(phone: str):
    clean = phone.replace('+', '').replace(' ', '').replace('-', '')[-11:]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM student_memory WHERE phone LIKE %s", (f'%{clean}%',))
        deleted = cur.rowcount
        conn.commit()
    return {'deleted': deleted}


@app.get("/api/memory/search")
async def search_memories(q: str = Query(..., min_length=2)):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM student_memory WHERE student_name ILIKE %s OR phone LIKE %s ORDER BY last_contact_at DESC LIMIT 20",
                    (f'%{q}%', f'%{q}%'))
        items = cur.fetchall()
        for i in items:
            for k in ('first_contact_at', 'last_contact_at', 'updated_at'):
                if i.get(k):
                    i[k] = i[k].isoformat()
        return {'items': items}


@app.get("/api/memory/{phone}/interactions")
async def memory_interactions(phone: str):
    clean = phone.replace('+', '').replace(' ', '').replace('-', '')[-11:]
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM interaction_summary WHERE phone LIKE %s ORDER BY created_at DESC LIMIT 50",
                    (f'%{clean}%',))
        items = cur.fetchall()
        for i in items:
            if i.get('created_at'):
                i['created_at'] = i['created_at'].isoformat()
        return {'items': items}


# ===================== WEBHOOK PROXY META (Typing Indicator) =====================

import httpx
from collections import OrderedDict

N8N_WEBHOOK_URL = 'https://n8n-new-n8n.ca31ey.easypanel.host/webhook/583eb17d-3455-4d64-ab68-67996fdb30af/webhook'
CALL_HANDLER_WEBHOOK_URL = os.environ.get('CALL_HANDLER_WEBHOOK_URL', 'https://banco-whats-calling.6tqx2r.easypanel.host/webhook/calls')
META_VERIFY_TOKEN = os.environ.get('META_VERIFY_TOKEN', 'tokenmetaacad2026')

_wamid_cache = OrderedDict()
_WAMID_CACHE_MAX = 500


def _store_wamid(phone: str, wamid: str):
    _wamid_cache[phone] = {'wamid': wamid, 'ts': time.time()}
    if len(_wamid_cache) > _WAMID_CACHE_MAX:
        _wamid_cache.popitem(last=False)


def _extract_wamids(payload: dict):
    """Extrai phone->wamid de um payload de webhook da Meta."""
    try:
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                for msg in value.get('messages', []):
                    wamid = msg.get('id', '')
                    phone = msg.get('from', '')
                    if wamid and phone:
                        _store_wamid(phone, wamid)
    except Exception:
        pass


@app.get("/webhook/meta")
async def meta_webhook_verify(request: Request):
    """Verificação do webhook (challenge) — Meta envia GET na configuração."""
    params = dict(request.query_params)
    mode = params.get('hub.mode', '')
    token = params.get('hub.verify_token', '')
    challenge = params.get('hub.challenge', '')

    if mode == 'subscribe' and token == META_VERIFY_TOKEN:
        return JSONResponse(content=int(challenge), status_code=200)

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(N8N_WEBHOOK_URL, params=params)
        return JSONResponse(content=r.text, status_code=r.status_code)


def _has_calls_event(payload: dict) -> bool:
    """Verifica se o payload contém eventos de chamada."""
    try:
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                if change.get('field') == 'calls':
                    return True
    except Exception:
        pass
    return False


@app.post("/webhook/meta")
async def meta_webhook_receive(request: Request):
    """Recebe webhook da Meta, roteia calls para call_handler e messages para n8n."""
    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception:
        payload = {}

    if _has_calls_event(payload):
        print(f"[WEBHOOK PROXY] Evento de CHAMADA detectado, encaminhando para call_handler", flush=True)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(
                    CALL_HANDLER_WEBHOOK_URL,
                    content=body,
                    headers={'Content-Type': 'application/json'}
                )
        except Exception as e:
            print(f"[WEBHOOK PROXY] Erro ao encaminhar chamada: {e}", flush=True)
        return JSONResponse(content={"status": "ok"}, status_code=200)

    _extract_wamids(payload)

    for phone, entry in _wamid_cache.items():
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO wamid_cache (phone, wamid, updated_at) VALUES (%s, %s, NOW()) ON CONFLICT (phone) DO UPDATE SET wamid = EXCLUDED.wamid, updated_at = NOW()",
                    (phone, entry['wamid'])
                )
                conn.commit()
        except Exception:
            pass

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                N8N_WEBHOOK_URL,
                content=body,
                headers={'Content-Type': 'application/json'}
            )
    except Exception as e:
        print(f"[WEBHOOK PROXY] Erro ao repassar para n8n: {e}")

    return JSONResponse(content={"status": "ok"}, status_code=200)


@app.get("/api/wamid/{phone}")
async def get_wamid(phone: str):
    """Retorna o último wamid — tenta cache em memória, depois PostgreSQL."""
    clean = phone.replace('+', '').replace(' ', '').replace('-', '')

    entry = _wamid_cache.get(clean)
    if entry:
        return {'wamid': entry['wamid'], 'age_seconds': time.time() - entry['ts'], 'source': 'memory'}
    for key, val in reversed(_wamid_cache.items()):
        if key.endswith(clean[-11:]):
            return {'wamid': val['wamid'], 'age_seconds': time.time() - val['ts'], 'source': 'memory'}

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT wamid, EXTRACT(EPOCH FROM (NOW() - updated_at)) as age FROM wamid_cache WHERE phone LIKE %s ORDER BY updated_at DESC LIMIT 1",
                (f'%{clean[-11:]}%',)
            )
            row = cur.fetchone()
            if row and row[1] < 300:
                _store_wamid(clean, row[0])
                return {'wamid': row[0], 'age_seconds': row[1], 'source': 'postgres'}
    except Exception:
        pass

    return {'wamid': None}


class WamidStoreRequest(BaseModel):
    phone: str
    wamid: str


@app.post("/api/wamid/store")
async def store_wamid_endpoint(req: WamidStoreRequest):
    """Recebe wamid do n8n e armazena em memória + PostgreSQL."""
    clean = req.phone.replace('+', '').replace(' ', '').replace('-', '')
    _store_wamid(clean, req.wamid)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO wamid_cache (phone, wamid, updated_at) VALUES (%s, %s, NOW()) ON CONFLICT (phone) DO UPDATE SET wamid = EXCLUDED.wamid, updated_at = NOW()",
                (clean, req.wamid)
            )
            conn.commit()
    except Exception:
        pass
    return {'stored': True, 'phone': clean, 'wamid': req.wamid[:40]}


# ===================== AGENT CONFIG =====================

AGENT_CONFIG_DEFAULTS = {
    "followup_1_delay": 300,
    "followup_1_msg": "Oi{name}! Ainda está por aí? Se tiver mais alguma dúvida, é só falar 😊",
    "followup_1_buttons": ["Tenho outra dúvida", "Não, obrigado!"],
    "close_delay": 600,
    "close_msg": "Como não tivemos retorno, vou finalizar o contato por aqui para te deixar seguir com seus compromissos. Estaremos à disposição caso precise retomar o assunto depois! ✨",
    "close_buttons": [],
    "poll_interval": 3,
    "confidence_threshold": 0.5,
    "response_cooldown": 2.0,
    "greeting_returning": "Olá, *{fname}*! Que bom falar com você novamente 😊\n\nNa última vez que conversamos, você estava com algumas dúvidas sobre *{topic}* — espero que tenha conseguido te ajudar naquele momento.\n\nAgora me conta: como posso te ajudar hoje?\n\nEscolha uma opção abaixo para agilizar seu atendimento 👇",
    "greeting_returning_no_topic": "Olá, *{fname}*! Que bom falar com você novamente 😊\n\nNa última vez que conversamos, você estava com algumas dúvidas — espero que tenha conseguido te ajudar naquele momento.\n\nAgora me conta: como posso te ajudar hoje?\n\nEscolha uma opção abaixo para agilizar seu atendimento 👇",
    "greeting_new": "Olá, *{fname}*! Bem-vindo(a) ao Suporte da *Cruzeiro do Sul* 😊\n\nComo posso te ajudar?\n\nEscolha uma opção abaixo para agilizar seu atendimento 👇",
    "greeting_anonymous": "Olá! Bem-vindo ao Suporte ao Aluno da *Cruzeiro do Sul* 😊\n\nComo posso te ajudar?\n\nEscolha uma opção abaixo para agilizar seu atendimento 👇",
    "greeting_buttons": ["Acesso Portal/App", "Financeiro", "Aulas e Conteúdo", "Documentos", "Rematrícula", "Falar com atendente"],
    "business_hours_weekday_start": 9,
    "business_hours_weekday_end": 20,
    "business_hours_saturday_start": 9,
    "business_hours_saturday_end": 13,
    "after_hours_first_msg": (
        "Oii{name}! Nesse momento nosso time de atendimento humano está fora do horário, "
        "mas eu (assistente virtual) sigo por aqui pra tentar te ajudar agora mesmo 😊\n\n"
        "📅 *Segunda a Sexta*: 09h às 20h\n"
        "📅 *Sábado*: 09h às 13h\n\n"
        "Me conta o que você precisa que eu já vou tentando resolver com você."
    ),
    "after_hours_insist_msg": (
        "Entendi{name}! Para esse caso é melhor falar com um(a) consultor(a) mesmo, "
        "e o nosso time retorna o atendimento *{retorno_label}*. "
        "Vou deixar registrado por aqui pra que assim que abrir o horário, alguém te chame. "
        "Enquanto isso, se quiser, posso te ajudar com outras dúvidas — é só me dizer 😊"
    ),
    "retention_after_hours_msg": (
        "Oii{name}, entendi 💙\n\n"
        "Essa é uma decisão importante e a gente quer te ouvir com a atenção que você merece. "
        "Para esse assunto, quem cuida com carinho é o *Wesley*, nosso consultor especializado.\n\n"
        "No momento ele está fora do horário de atendimento, mas assim que retomar *{retorno_label}* "
        "ele entra em contato com você por aqui mesmo, tá? 😊\n\n"
        "Enquanto isso, se precisar de ajuda com *acesso, boleto, aulas* ou qualquer outra coisa, "
        "é só me chamar — eu sigo por aqui pra te ajudar."
    ),
    "human_busy_msg": (
        "Nossos atendentes estão todos em atendimento agora, mas fica tranquilo{name}! "
        "Em pouquinho alguém vai te chamar aqui 😊"
    ),
    "auto_dispatch_morning_queue": True,
    "morning_dispatch_batch_size": 25,
}


def ensure_agent_config_table():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_config (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

ensure_agent_config_table()


@app.get("/api/agent-config")
async def get_agent_config():
    """Retorna todas as configs do agente (DB + defaults)."""
    config = dict(AGENT_CONFIG_DEFAULTS)
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM agent_config")
        for key, value in cur.fetchall():
            try:
                config[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                config[key] = value
        cur.close()
        conn.close()
    except Exception:
        pass
    return config


@app.post("/api/agent-config")
async def save_agent_config(request: Request):
    """Salva configs do agente no banco."""
    data = await request.json()
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        for key, value in data.items():
            if key not in AGENT_CONFIG_DEFAULTS:
                continue
            val_str = json.dumps(value) if isinstance(value, (list, dict)) else json.dumps(value)
            cur.execute("""
                INSERT INTO agent_config (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, (key, val_str))
        conn.commit()
        cur.close()
        conn.close()
        return {"saved": True, "keys": list(data.keys())}
    except Exception as e:
        return {"saved": False, "error": str(e)}


# ===================== MENUS CRUD =====================

def _build_menu_tree(rows):
    """Constrói árvore aninhada a partir de lista flat de rows."""
    nodes = {}
    for r in rows:
        nodes[r['id']] = {**r, 'children': []}
    roots = []
    for n in nodes.values():
        pid = n.get('parent_id')
        if pid and pid in nodes:
            nodes[pid]['children'].append(n)
        elif not pid:
            roots.append(n)
    return roots


@app.get("/api/menus")
async def get_menus():
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, parent_id, level, menu_key, label, response_text, rag_question, sort_order, active FROM agent_menus ORDER BY sort_order, id")
        rows = cur.fetchall()
    return {"tree": _build_menu_tree(rows), "flat": rows}


@app.post("/api/menus")
async def create_menu(request: Request):
    data = await request.json()
    label = data.get('label', '').strip()
    if not label:
        raise HTTPException(400, "Label obrigatório")
    menu_key = data.get('menu_key', label.lower()).strip().lower().replace('*', '')
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO agent_menus (parent_id, level, menu_key, label, response_text, rag_question, sort_order, active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (data.get('parent_id'), data.get('level', 'leaf'), menu_key, label,
             data.get('response_text'), data.get('rag_question'),
             data.get('sort_order', 0), data.get('active', True)))
        new_id = cur.fetchone()[0]
        conn.commit()
    return {"id": new_id, "created": True}


@app.put("/api/menus/{menu_id}")
async def update_menu(menu_id: int, request: Request):
    data = await request.json()
    updates, params = [], []
    for field in ('label', 'menu_key', 'response_text', 'rag_question', 'sort_order', 'active', 'level', 'parent_id'):
        if field in data:
            updates.append(f"{field} = %s")
            val = data[field]
            if field == 'menu_key' and isinstance(val, str):
                val = val.strip().lower()
            params.append(val)
    if not updates:
        raise HTTPException(400, "Nenhum campo para atualizar")
    updates.append("updated_at = NOW()")
    params.append(menu_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE agent_menus SET {', '.join(updates)} WHERE id = %s", params)
        if cur.rowcount == 0:
            raise HTTPException(404)
        conn.commit()
    return {"updated": True}


@app.delete("/api/menus/{menu_id}")
async def delete_menu(menu_id: int):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM agent_menus WHERE id = %s", (menu_id,))
        if cur.rowcount == 0:
            raise HTTPException(404)
        conn.commit()
    return {"deleted": True}


@app.post("/api/menus/reorder")
async def reorder_menus(request: Request):
    data = await request.json()
    items = data.get('items', [])
    with get_db() as conn:
        cur = conn.cursor()
        for item in items:
            cur.execute("UPDATE agent_menus SET sort_order = %s, parent_id = %s, updated_at = NOW() WHERE id = %s",
                       (item['sort_order'], item.get('parent_id'), item['id']))
        conn.commit()
    return {"reordered": len(items)}


@app.post("/api/menus/seed")
async def seed_menus():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM agent_menus")
        _seed_default_menus(cur)
        conn.commit()
    return {"seeded": True}


@app.post("/api/agent/reload")
async def agent_reload():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_config (key, value, updated_at)
            VALUES ('_reload_flag', %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (str(time.time()),))
        conn.commit()
    return {"reload_requested": True}


_agent_process = None
_agent_log_file = None
_agent_test_phone = '11970617878'
_AGENT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent_live.log')

def _read_runtime_flag():
    """Le agent_config.agent_runtime_enabled. Default: True (ligado)."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM agent_config WHERE key = 'agent_runtime_enabled'")
            row = cur.fetchone()
            cur.close()
        if row and row[0] is not None:
            v = str(row[0]).strip().lower()
            return v not in ('false', '0', 'off', 'disabled', 'no')
        return True
    except Exception:
        return True


def _set_runtime_flag(enabled: bool):
    """Atualiza agent_config.agent_runtime_enabled."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO agent_config (key, value, updated_at)
                VALUES ('agent_runtime_enabled', %s, NOW())
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
            """, ('true' if enabled else 'false',))
            conn.commit()
            cur.close()
        return True
    except Exception as e:
        print(f'[FLAG] erro set: {e}', flush=True)
        return False


def _read_heartbeat_status():
    """Le heartbeat para saber se o processo do agente esta vivo (independente da flag)."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT status, pid, EXTRACT(EPOCH FROM (NOW() - last_beat)) as seconds_ago
                FROM agent_heartbeat WHERE id = 1
            """)
            row = cur.fetchone()
            cur.close()
        if not row:
            return {'process_alive': False, 'pid': None, 'seconds_ago': None, 'hb_status': 'offline'}
        secs = float(row[2] or 9999)
        return {
            'process_alive': secs < 120,
            'pid': row[1],
            'seconds_ago': round(secs),
            'hb_status': row[0],
        }
    except Exception:
        return {'process_alive': False, 'pid': None, 'seconds_ago': None, 'hb_status': 'offline'}


@app.get("/api/agent/live/status")
async def agent_live_status():
    """Status do agente PRINCIPAL (start.sh) baseado em flag de runtime + heartbeat.
    Antes esse endpoint refletia o subprocess de teste, o que enganava o usuario:
    o agente real do start.sh continuava atendendo mesmo com 'Desligado' no dashboard.
    Agora: running = enabled flag E heartbeat recente."""
    flag = _read_runtime_flag()
    hb = _read_heartbeat_status()
    process_alive = hb['process_alive']
    running = bool(flag and process_alive)
    return {
        "running": running,
        "enabled": flag,
        "process_alive": process_alive,
        "pid": hb.get('pid'),
        "heartbeat_seconds_ago": hb.get('seconds_ago'),
        "heartbeat_status": hb.get('hb_status'),
    }


@app.get("/api/agent/live/logs")
async def agent_live_logs(lines: int = 80):
    try:
        with open(_AGENT_LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.readlines()
        return {"lines": all_lines[-lines:]}
    except FileNotFoundError:
        return {"lines": []}


@app.post("/api/agent/live/start")
async def agent_live_start():
    """Liga o agente PRINCIPAL via flag. Nao inicia subprocess novo —
    o agente do start.sh ja esta vivo e respeita essa flag automaticamente."""
    ok = _set_runtime_flag(True)
    if not ok:
        return {"ok": False, "msg": "Erro ao atualizar flag no banco"}
    hb = _read_heartbeat_status()
    return {
        "ok": True,
        "msg": "Agente ligado (processamento ativado).",
        "process_alive": hb['process_alive'],
        "pid": hb.get('pid'),
    }


@app.post("/api/agent/live/stop")
async def agent_live_stop():
    """Desliga o agente PRINCIPAL via flag. NAO mata o processo — apenas
    pausa o processamento. Em ate ~5s o agente para de responder."""
    ok = _set_runtime_flag(False)
    if not ok:
        return {"ok": False, "msg": "Erro ao atualizar flag no banco"}
    return {
        "ok": True,
        "msg": "Agente desligado (processamento pausado em ate 5s).",
    }

@app.post("/api/agent/restart")
async def agent_restart():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_config (key, value, updated_at)
            VALUES ('_restart_flag', %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (str(time.time()),))
        conn.commit()
    return {"restart_requested": True}


@app.post("/api/media/upload")
async def upload_media(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(400, f"Tipo não suportado: {file.content_type}. Permitidos: imagens, vídeos, PDF")
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(400, f"Arquivo muito grande ({len(content)//1024//1024}MB). Máximo: 16MB")
    ext = os.path.splitext(file.filename or '')[1] or '.bin'
    safe_name = f"{int(time.time())}_{secrets.token_hex(6)}{ext}"
    filepath = os.path.join(MEDIA_DIR, safe_name)
    with open(filepath, 'wb') as f:
        f.write(content)
    media_type = 'image' if file.content_type.startswith('image') else 'video' if file.content_type.startswith('video') else 'document'
    return {
        "url": f"/media/{safe_name}",
        "filename": file.filename,
        "type": media_type,
        "mimeType": file.content_type,
        "size": len(content)
    }


ALERT_CATEGORIES = ['geral', 'instabilidade', 'manutencao', 'aviso', 'evento', 'urgente']


@app.get("/api/alerts")
async def list_alerts(active_only: bool = False):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if active_only:
            cur.execute("""SELECT * FROM agent_alerts
                           WHERE active = TRUE AND (starts_at IS NULL OR starts_at <= NOW())
                           AND (expires_at IS NULL OR expires_at > NOW())
                           ORDER BY priority DESC, created_at DESC""")
        else:
            cur.execute("SELECT * FROM agent_alerts ORDER BY active DESC, priority DESC, created_at DESC")
        rows = cur.fetchall()
        for r in rows:
            for k in ('starts_at', 'expires_at', 'created_at'):
                if r.get(k):
                    r[k] = r[k].isoformat()
        return {"items": rows, "categories": ALERT_CATEGORIES}


@app.post("/api/alerts")
async def create_alert(request: Request):
    data = await request.json()
    title = data.get('title', '').strip()
    message = data.get('message', '').strip()
    if not title or not message:
        raise HTTPException(400, "title e message são obrigatórios")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO agent_alerts (title, message, category, active, priority, starts_at, expires_at, display_mode)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (title, message,
                     data.get('category', 'geral'),
                     data.get('active', True),
                     data.get('priority', 0),
                     data.get('starts_at') or None,
                     data.get('expires_at') or None,
                     data.get('display_mode', 'context')))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id, "created": True}


@app.put("/api/alerts/{alert_id}")
async def update_alert(alert_id: int, request: Request):
    data = await request.json()
    with get_db() as conn:
        cur = conn.cursor()
        fields = []
        vals = []
        for col in ('title', 'message', 'category', 'active', 'priority', 'starts_at', 'expires_at', 'display_mode'):
            if col in data:
                fields.append(f"{col} = %s")
                v = data[col]
                if col in ('starts_at', 'expires_at') and v == '':
                    v = None
                vals.append(v)
        if not fields:
            raise HTTPException(400, "Nenhum campo para atualizar")
        vals.append(alert_id)
        cur.execute(f"UPDATE agent_alerts SET {', '.join(fields)} WHERE id = %s", vals)
        conn.commit()
        return {"updated": cur.rowcount > 0}


@app.delete("/api/alerts/{alert_id}")
async def delete_alert(alert_id: int):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM agent_alerts WHERE id = %s", (alert_id,))
        conn.commit()
        return {"deleted": cur.rowcount > 0}


# ====================================================================
# (2026-05-26) ROTAS DO DASHBOARD AGENTE IA - migradas de
# dashboard_server.py para o mesmo processo. Antes dependiam de outro
# servidor em :8050 que nao subia em producao. Agora tudo num so lugar.
# Prefixo /api/aia/* — o JS do Cockpit usa const AIA='/api/aia'.
# ====================================================================
from psycopg2.extras import RealDictCursor as _AiaRDC


def _aia_conn():
    """Conexao psycopg2 dedicada para rotas AIA (similar a dashboard_server)."""
    import psycopg2 as _aia_pg
    return _aia_pg.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=int(os.environ.get('DB_PORT', 5432)),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', ''),
        dbname=os.environ.get('DB_NAME', 'log_conversa'),
    )


@app.get("/api/aia/api/stats")
def aia_stats(days: int = 7):
    days = max(1, min(int(days or 7), 90))
    conn = _aia_conn()
    cur = conn.cursor(cursor_factory=_AiaRDC)
    from datetime import datetime as _dt, timedelta as _td
    since = (_dt.now() - _td(days=days)).strftime('%Y-%m-%d')
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
    cur.execute("""
        SELECT EXTRACT(HOUR FROM created_at)::int as h, COUNT(*) as cnt
        FROM interaction_summary WHERE created_at >= %s
        GROUP BY h ORDER BY h
    """, (since,))
    por_hora = cur.fetchall()
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE avaliacao = 'correta') as corretas,
               COUNT(*) FILTER (WHERE avaliacao = 'incorreta') as incorretas,
               COUNT(*) FILTER (WHERE avaliacao IS NULL OR trim(avaliacao) = '') as pendentes
        FROM interaction_summary WHERE created_at >= %s
    """, (since,))
    av = cur.fetchone()
    avaliadas = (av['corretas'] or 0) + (av['incorretas'] or 0)
    taxa_acerto = round((av['corretas'] or 0) / avaliadas * 100, 1) if avaliadas > 0 else 0
    cur.execute("""
        SELECT phone, student_name, COUNT(*) as cnt,
               ROUND(AVG(nps_implicito)::numeric, 1) as avg_nps
        FROM interaction_summary
        WHERE created_at >= %s AND student_name IS NOT NULL AND trim(student_name) <> ''
        GROUP BY phone, student_name
        ORDER BY cnt DESC LIMIT 8
    """, (since,))
    top_alunos = cur.fetchall()
    kb_total = kb_emb = 0
    try:
        cur.execute("SELECT COUNT(*) as c FROM knowledge_base")
        kb_total = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM knowledge_base WHERE embedding IS NOT NULL")
        kb_emb = cur.fetchone()['c']
    except Exception:
        pass
    conn.close()
    return {
        'total': total, 'temas': temas, 'sentimentos': sentimentos, 'nps': nps,
        'resolvido': resolvido, 'por_dia': por_dia, 'subtemas': subtemas,
        'avg_nps': float(avg_nps) if avg_nps else 0, 'taxa_resolucao': taxa_res,
        'nps_score': nps_score, 'por_hora': por_hora,
        'promotores': nps_row['promotores'], 'neutros': nps_row['neutros'],
        'detratores': nps_row['detratores'],
        'avaliacoes': {
            'corretas': av['corretas'] or 0,
            'incorretas': av['incorretas'] or 0,
            'pendentes': av['pendentes'] or 0,
            'taxa_acerto': taxa_acerto,
        },
        'top_alunos': [
            {'phone': r['phone'], 'name': r['student_name'], 'cnt': r['cnt'],
             'avg_nps': float(r['avg_nps']) if r['avg_nps'] is not None else None}
            for r in top_alunos
        ],
        'knowledge_base': {'total': kb_total, 'com_embedding': kb_emb},
    }


@app.get("/api/aia/api/alerts")
def aia_alerts():
    conn = _aia_conn()
    cur = conn.cursor(cursor_factory=_AiaRDC)
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


@app.get("/api/aia/api/recent")
def aia_recent(limit: int = 30, page: int = 1, tema: str = None,
               sentimento: str = None, search: str = None, avaliacao: str = None):
    limit = max(1, min(int(limit or 30), 200))
    page = max(1, int(page or 1))
    conn = _aia_conn()
    cur = conn.cursor(cursor_factory=_AiaRDC)
    where = ["1=1"]
    params = []
    if tema:
        where.append("tema = %s"); params.append(tema)
    if sentimento:
        where.append("sentimento = %s"); params.append(sentimento)
    if avaliacao == 'pendente':
        where.append("(avaliacao IS NULL OR trim(avaliacao) = '')")
    elif avaliacao in ('correta', 'incorreta'):
        where.append("avaliacao = %s"); params.append(avaliacao)
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


@app.post("/api/aia/api/avaliar/{record_id}")
def aia_avaliar(record_id: int, avaliacao: str):
    if avaliacao not in ('correta', 'incorreta', None, ''):
        return {"error": "Valor invalido"}
    conn = _aia_conn()
    cur = conn.cursor()
    cur.execute("UPDATE interaction_summary SET avaliacao = %s WHERE id = %s",
                (avaliacao or None, record_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/aia/api/corrigir/{record_id}")
async def aia_corrigir(record_id: int, request: Request):
    body_json = await request.json()
    resposta = (body_json.get('resposta_correta') or '').strip()
    if not resposta:
        return {"error": "Resposta vazia"}
    conn = _aia_conn()
    cur = conn.cursor(cursor_factory=_AiaRDC)
    cur.execute("UPDATE interaction_summary SET avaliacao = 'incorreta' WHERE id = %s", (record_id,))
    cur.execute("SELECT pergunta_aluno, tema FROM interaction_summary WHERE id = %s", (record_id,))
    row = cur.fetchone()
    if not row or not row.get('pergunta_aluno'):
        conn.commit(); conn.close()
        return {"error": "Registro nao encontrado"}
    pergunta = row['pergunta_aluno']
    tema = row.get('tema') or 'OUTRO'
    try:
        import openai as _openai_aia
        client = _openai_aia.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        emb_resp = client.embeddings.create(
            input=pergunta[:2000], model='text-embedding-3-small', dimensions=256,
        )
        embedding = emb_resp.data[0].embedding
        emb_str = '[' + ','.join(str(x) for x in embedding) + ']'
        cur.execute("""
            INSERT INTO knowledge_base (pergunta_aluno, resposta_atendente, tema, embedding, created_at)
            VALUES (%s, %s, %s, %s::float8[], NOW())
        """, (pergunta, resposta, tema, emb_str))
    except Exception as e:
        print(f"  [aia_corrigir] Erro KB: {e}")
    conn.commit(); conn.close()
    return {"ok": True, "msg": "Correcao salva na base de conhecimento"}


if __name__ == '__main__':
    import uvicorn
    print("Cockpit IA rodando em http://localhost:8000")
    uvicorn.run(app, host='0.0.0.0', port=8000)
