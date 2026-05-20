# AGENT.md — registro de decisões estruturais

## Convenções

- Decisões registradas em ordem cronológica decrescente.
- Cada entrada: **Decisão**, **Contexto**, **Alternativas descartadas**, **Impacto**.

---

### [2026-05-20] - Anti-repetição "à prova de tudo" + supervisor OpenAI

**Decisão**
Três camadas independentes que se reforçam para impedir repetições do agente
mesmo após restart e cobrir falhas que regex/signature não pegam:

**Camada 1 — Dedup de conteúdo persistente em `send_and_track`**
- Novo `_normalize_body_for_dedup(text)`: normaliza texto (lowercase, sem
  acentos, sem pontuação, **nomes próprios viram `<nome>`**, espaços colapsados,
  280 chars). Permite considerar "Vou te transferir para *Wesley*" e
  "Vou te transferir para *Marília*" como **mesma mensagem** para fins de dedup.
- `_body_recently_sent(conv_id, text, window_s=600)` consulta
  `agent_sent_signatures.body_hash` — **persistente, sobrevive restart**.
- `send_and_track` ganha:
  - **Lock por `conv_id`** (`_conv_send_locks` global) — serializa envios
    concorrentes (era a porta de entrada do bug "LLM responde 2x").
  - Verificação `_body_recently_sent` **antes** de enviar; se bate, **SUPRIME**
    e loga em `ia_interaction_log` com `acao='suprimido_dedup'`.
  - Parâmetro `force=True` para mensagens críticas que devem passar.
- `_register_body(conv_id, text)` chamado após envio bem-sucedido.

**Camada 2 — Idempotência de `distribute_to_attendant`**
- No início, checa `_is_handoff_active(conv_id)` com `motivo='dispatch'` →
  retorna `True` direto sem refazer nota interna nem "Vou te transferir".
- Fallback in-memory: se `_last_distributed_to` está setado há < 5min, skip.
- No fim do dispatch com sucesso, chama
  `_mark_handoff_active(conv_id, 'dispatch', target=nome, ttl_s=4*3600)`.
- Resolve o bug da Imagem 2 (duplo "Distribuição automática" + duplo
  "Vou te transferir para Marília").

**Camada 3 — Supervisor OpenAI revisor periódico**
- Loop independente `process_openai_supervisor_loop()` rodando junto com o
  supervisor existente (a cada `cycle % 10 == 0`, mas com cooldown próprio
  de `OPENAI_SUPERVISOR_INTERVAL_S=300s`).
- Pega conversas com atividade nos últimos `OPENAI_SUPERVISOR_LOOKBACK_MIN=60min`
  e que tenham ≥2 mensagens do bot.
- Chama `OPENAI_SUPERVISOR_MODEL=gpt-4o` (configurável via env) com
  `response_format=json_object` e prompt em PT-BR que classifica em:
  - `repeticao_resposta`, `contradicao`, `falha_pre_opening`,
    `sobre_resposta`, `duplicado_distribuicao`, `ok`.
- Findings gravados em nova tabela `agent_audit_findings` (com `severity`,
  `problem_type`, `summary`, `detail` JSON, `action_taken`).
- **Auto-correção**: se `severidade=alta` e tipo em
  `(repeticao_resposta, sobre_resposta, duplicado_distribuicao)`, marca
  `handoff_active(motivo='supervisor_block', ttl=6h)` → agente fica em
  **silêncio absoluto** na conv (sem nudge) até intervenção humana.
- Cap por ciclo: `OPENAI_SUPERVISOR_MAX_CONVS=15`; mesma conv só re-auditada
  a cada 15min. Custo controlado.

**Contexto**
Usuária mandou 2 prints:
- Imagem 1: Bot mandou 2 respostas quase idênticas para a mesma pergunta sobre
  nota (LLM chamado 2x em paralelo, com palavras um pouco diferentes).
- Imagem 2: 2 notas internas "Distribuição automática" + 2 mensagens
  "Vou te transferir para Marília" (`distribute_to_attendant` chamada 2x).
- Disse "impeça a qualquer custo o agente responder a mesma coisa mais de uma
  vez, mesmo após reiniciá-lo" e pediu supervisor OpenAI explicitamente,
  preferindo o "melhor mesmo que mais caro".

**Alternativas descartadas**
- Apenas mais signatures `_signature_recently_sent` em cada call site →
  não pega LLM gerando texto livre com pequenas variações.
- Hash exato do body sem normalização → não pega "Vou te transferir para X"
  vs "Vou te transferir para Y" (atendentes diferentes em chamadas duplas).
- Mutex global no envio → estrangula throughput de convs paralelas; lock
  por conv é suficiente.
- `gpt-4o-mini` para o supervisor → mais barato, mas pediram o melhor;
  `gpt-4o` é exposto via env e pode ser trocado sem deploy.
- LLM-as-judge em cada envio → custo proibitivo; revisão periódica é
  suficiente porque dedup hash já cobre os casos óbvios em tempo real.

**Impacto**
- Bug "Adriano" (Imagem 1): em tempo real, o segundo envio do LLM passa pelo
  lock, e quando o body normalizado bate é suprimido. Quando palavras divergem
  o bastante para escapar, o supervisor OpenAI pega em até 5min, registra
  finding e CALA o agente nessa conv.
- Bug "Tauana" (Imagem 2): idempotência impede `distribute_to_attendant` de
  rodar 2x. Mesmo se rodar, o body_hash idêntico de "Vou te transferir" é
  suprimido.
- Dashboard ganha endpoint potencial para `agent_audit_findings` (tabela
  pronta; UI pode listar findings recentes).
- Custo: ~15 convs × 1 chamada gpt-4o curta (300 tokens) a cada 5min.

---

### [2026-05-20] - Janela pré-abertura + limite por consultor (anti-sobrecarga)

**Decisão**
Adicionar nova janela "quase abrindo" (`PRE_OPENING_MARGIN_MIN = 60`) e mensagem
específica antes do expediente. Quando faltam <= 60min para abrir:
1. Agente NÃO envia AFTER_HOURS_FIRST_MSG / AFTER_HOURS_INSIST_MSG.
2. Em vez disso, manda `PRE_OPENING_MSG` com botões "Sim, entrar na fila" / "Não, obrigado(a)".
3. Aluno aceita (botão ou texto "sim", "ok", "aguardo", etc) → registra em
   `pending_escalation` com `tier='pre_opening'`, marca `handoff_active` e calado
   até abrir. Bandeira priorizada na fila do morning dispatch.
4. Aluno recusa → `decline_pre_opening` libera o fluxo IA normal.

**Limite por consultor no morning burst**
- Novo `PRE_OPENING_BURST_MAX_PER_ATTENDANT = 5`.
- `get_available_consultant(exclude_attendants=...)` aceita exclusão por nome.
- `distribute_to_attendant(..., exclude_attendants=...)` propaga.
- `process_pending_escalation_auto_dispatch` mantém `assigned_count` por rodada
  e exclui consultor que já recebeu 5; os excedentes ficam `pending` e entram
  na próxima janela de retry. Tier `pre_opening` tem prioridade máxima na ordem
  de despacho.

**Contexto**
Usuária reportou:
- Aluno escreveu às 08h45 e recebeu mensagem de "fora do horário" — gerava
  sensação ruim e não oferecia alternativa.
- Aluno escreveu às 9h00 em ponto e ainda recebeu "fora" — latência/diferença
  de minuto entre o instante do envio e o processamento; com a janela de 60min
  esse caso passa automaticamente para o fluxo pre_opening.
- Quando muitos alunos entravam na fila noturna, o 1º consultor do dia recebia
  todos os leads de uma vez → sobrecarga.

**Alternativas descartadas**
- Margem só de 15min → ainda gera "fora" pra aluno que escreve às 8h45.
- Sem botões (só texto sim/não) → mais ambíguo. Adotamos botões + fallback texto.
- Distribuir tudo igualmente → não respeita `volume_distribuicao` do Supabase;
  o limite burst é estritamente adicional, não substitui.
- Aumentar `volume_distribuicao` no Supabase → afeta todo o resto do dia.

**Impacto**
- Janela pre-opening cobre o ponto cego "8h45-9h00" e elimina o bug do "9h em ponto".
- Aluno tem opção explícita de entrar na fila vs continuar com a IA.
- Morning burst nunca dá mais que 5 leads de uma vez ao mesmo consultor.
- Tier `pre_opening` é prioridade 0 (na frente de insist=1 e first=2).

---

### [2026-05-20] - Dedup persistente e handoff_active (anti-repetição)

**Decisão**
Adicionar duas tabelas persistentes para eliminar mensagens repetitivas/duplicadas:

1. **`agent_sent_signatures(conv_id, signature, body_hash, sent_at)`**
   - Toda mensagem importante registra uma "assinatura" do motivo
     (`retention_after_hours`, `retention`, `after_hours_first`, `after_hours_insist`,
     `human_busy`, `followup_1`, `auto_close`, `handoff_nudge:<motivo>`).
   - `_signature_recently_sent(conv_id, sig, window_s)` checa antes de enviar.
   - Sobrevive a restart do agente — não depende de `_conv_states` em memória.

2. **`handoff_active(conv_id, motivo, target_attendant, expires_at)`**
   - Quando o agente faz handoff humanizado (retenção Wesley, after-hours insist,
     human_unavailable), grava `handoff_active` com TTL (8-14h).
   - `handle_message`: se `_is_handoff_active(cid)`, **agente principal NÃO chama LLM,
     NÃO responde**. Manda só um `nudge` único ("o *Wesley* vai dar continuidade,
     pode aguardar") deduplicado por 4h, e CALA.
   - `process_supervisor_loop`: se handoff_active, **NÃO manda follow-up** (mas
     ainda pode executar close_orphan após 30min de silêncio).
   - Limpo automaticamente em: promessa honrada (humano assume), close por
     inatividade, ou TTL expira.

**Contexto**
Caso "Isabel" reportado pela usuária mostrou sequência repetitiva:
  1. Mensagem humanizada Wesley fora-do-horário
  2. LLM gerou "Eu entendo que tá complicado..." (cortesia)
  3. Follow-up "Ainda está por aí?"
  4. Close

Mesmo com supervisor v3 evitando follow-up após handoff via marker no body, o LLM do
agente principal continuava respondendo qualquer mensagem subsequente do aluno. Após
restart, o agente também perdia memória dos timers e podia reenviar mesma resposta.

**Alternativas descartadas**
- Detectar repetição só por hash da mensagem → não pega variações do LLM (mesmo
  motivo, texto diferente).
- Marcar `_human_took_over=True` no estado em memória → não sobrevive a restart.
- Bloquear LLM apenas se última msg do bot tinha marker handoff → frágil; LLM
  podia mandar uma cortesia entre handoff e nova msg do aluno.

**Impacto**
- Mensagens repetidas após restart: eliminadas (signature em DB).
- Sequência "humanizada → eu entendo → follow-up → close": elimina passos 2 e 3.
- Aluno pode mandar várias mensagens insistindo após handoff: recebe 1 nudge
  ("Wesley vai assumir, aguarde"), depois silêncio até humano assumir ou close.
- Risco residual: se TTL handoff_active expira sem humano assumir e sem close, agente
  volta a responder — mitigado por close_orphan do supervisor (30min).

---

### [2026-05-20] - Resgate cria lead + Supervisor loop v3 (humano-inativo + close órfão)

**Decisão**
1. Rescues (`process_in_hours_rescue`, `process_post_close_rescue`) agora **criam lead+business
   no CRM antes de atribuir consultor** via novo helper `_ensure_lead_for_rescue(phone, name)`.
   Se falha em criar, aborta a atribuição (não deixa conv órfã com atendente sem lead).
2. Chamadas a `_dcz_transfer_business` corrigidas: passam `phone` como 1º arg e `lead_id` como
   3º (estava passando business_id em phone, que só funcionava por sorte via lookup interno).
3. Helper `_lookup_attendant_id(name, table)` aceita nome completo (`Wesley Guerreiro`) e cai
   para primeiro nome (`wesley`) automaticamente — antes o map só batia com primeiro nome.
4. **Supervisor v3**: substitui o check binário `c.attendants != []` por:
   - Se humano respondeu por último → não mexer.
   - Se humano atribuído mas última outbound foi do bot e humano inativo há > 5min → liberar.
   - Novo `_msg_is_from_human(m)` distingue por `m.attendant != None`.
   - `_supervisor_has_attendant_fresh` agora também aceita conv com humano-inativo.
5. **Close órfão**: além do close pós-follow-up, supervisor encerra conv parada após
   2x CLOSE_DELAY (30min) quando a última msg do bot foi handoff/tutorial e nenhum humano atuou.
6. `SUPERVISOR_MAX_FOLLOWUP_AGE_S` 60min → 4h (cobre backlog matinal sem mandar ping tardio absurdo).

**Contexto**
Usuária reportou repetidamente conversas paradas: bot envia tutorial, atendente é
atribuído (resgate/distribuição), mas humano não atua. Sem o fix, supervisor pulava
todas as conversas com `attendants != []` e nenhum follow-up/close acontecia. Também
houve casos de resgate atribuindo conv ao consultor sem criar lead no CRM (Ana Paula,
Fabiane, Neythan), deixando "Lead não encontrado" no painel e fora do pipeline.

**Alternativas descartadas**
- Forçar bot a sempre responder mesmo com atendente humano ativo → atropelaria humano.
- Removendo a verificação de atendente totalmente → bot manda follow-up por cima
  de humano atuando.
- Notificar dashboard externo em vez de close orfão → exige nova UI e ação manual,
  contraria pedido explícito ("não precisar ficar pedindo").

**Impacto**
- Cobertura de follow-up sobe muito (cobre convs com atendente atribuído mas inativo).
- Encerramento garantido em até ~30min após handoff sem resposta.
- Risco de bot responder em cima de humano: mitigado pelos 5min de grace + re-fetch.
- Resgates passam a sempre criar lead/business + mover stage corretamente.

---

### [2026-05-20] - Supervisor loop v2 (estritamente send-only, multi-status, dupla checagem)

**Atualização (v2)**
- `SUPERVISOR_STATUSES = ('open', 'opened')`: agora varre os dois status do DCZ com
  dedup por `conv_id`. Cobria só `open`, ficando cego pra metade da fila.
- `SUPERVISOR_MAX_FOLLOWUP_PER_CYCLE = 25` (era 8) e `MAX_CLOSE = 15` (era 5),
  pra escoar backlog após restarts.
- `SUPERVISOR_MAX_FOLLOWUP_AGE_S = 60 min` (era 8h): NÃO manda follow-up tardio em
  conversas antigas (evita "Ainda está por aí?" depois de horas, que parece estranho).
- Ordena enriquecidos por silêncio crescente (prioriza 10-30 min antes de 50-60 min).
- **Re-fetch antes de enviar** (`_supervisor_has_attendant_fresh`): elimina race entre
  listagem e envio — se humano assumiu nesse meio, o supervisor desiste.
- **Estritamente send-only**: nunca troca atendente, nunca move pipeline, nunca toca
  CRM/lead. Pior caso possível = uma mensagem de texto a mais. Não pode reproduzir
  problemas como o caso da Ana Paula (que era do `process_in_hours_rescue`, distinto).

### [2026-05-20] - Supervisor loop (follow-up / close independente da memória)

**Decisão**
Adicionar `process_supervisor_loop()` que roda a cada 10 ciclos do agente (~30s),
consultando o DCZ diretamente (timestamps + últimas mensagens) para garantir:
1. **Follow-up 1** quando o agente respondeu e o aluno ficou em silêncio ≥ `FOLLOWUP_1_DELAY`
   sem mensagem de follow-up já enviada.
2. **Encerramento** quando o último envio foi follow-up e o silêncio ≥ `CLOSE_DELAY`.

Dedup persistente em `supervisor_actions` (Postgres). Ao agir, sincroniza
`_conv_states` para evitar duplicata no loop em memória.

**Contexto**
Após restarts (watchdog, rebuild Easypanel), `_conv_states` era zerado e conversas
em espera de follow-up/close ficavam paradas — o usuário reportou alunos com 10–40min
sem "Ainda está por aí?" mesmo com agente online. Resgates manuais (`_send_followup_image.py`)
resolveram o sintoma pontual, mas não a causa.

**Alternativas descartadas**
- *Persistir `_conv_states` inteiro no banco:* mais escrita e ainda perde estado em crash
  entre snapshots.
- *Só aumentar `IN_HOURS_RESCUE_MAX_AGE`:* não cobre follow-up (aluno já foi respondido).
- *Supervisor só follow-up sem close:* deixaria conversas eternas após follow-up.

**Impacto**
- Arquivo: `agente_ao_vivo_v4.py` — bloco `SUPERVISOR LOOP`, hook em `cycle % 10`.
- Tabela nova: `supervisor_actions` (auto-criada no primeiro ciclo).
- Não substitui resgates (`IN-HOURS`, `AH`, `POST-CLOSE`); complementa o fluxo normal.
- Ignora conversas com atendente humano atribuído (mesma regra do follow-up em memória).

---

### [2026-05-19] - Integração CAA SIAA (snapshot diário de solicitações)

**Decisão**
Adicionar pipeline de ingestão das solicitações do SIAA (centro de atendimento
ao aluno) para que o agente possa cruzar por CPF e mencionar solicitações
existentes de forma natural quando a dúvida do aluno for relacionada.

Arquitetura:
1. **Storage:** tabelas `caa_solicitacoes` e `caa_import_history` no DB
   principal `agente_ia` (Postgres). Cada upload é um snapshot completo
   (`TRUNCATE` + `INSERT` em transação). Histórico em `caa_import_history`.
2. **Upload:** endpoint `POST /api/caa/upload` em `kb_api.py` recebe XLSX,
   parseia com `openpyxl` em `read_only=True` (streaming, suporta planilhas
   grandes), normaliza CPF (`re.sub` + `zfill(11)`) e faz bulk insert
   transacional. Endpoints auxiliares: `GET /api/caa/status`,
   `GET /api/caa/by-cpf/{cpf}`, `GET /api/caa/list?...`.
3. **UI:** nova aba **"Solicitações CAA"** no Cockpit (`kb_admin.html`) com
   card de status (último upload + contagens), botão de upload, tabela
   paginada com filtros (nome/CPF/protocolo, situação, deferimento) e
   histórico das 5 últimas importações.
4. **Agente:** função `fetch_caa_solicitacoes(cpf)` em
   `agente_ao_vivo_v4.py`, chamada nos mesmos pontos onde
   `fetch_academic_data` é invocada (em `handle_message` após
   identificação do aluno e no fluxo de validação por CPF). Resultado vai
   em `profile['caa_solicitacoes']`.
5. **Contexto LLM:** `build_student_context` ganhou bloco
   `## SOLICITACOES CAA` com até 8 itens (data, subprocesso, protocolo,
   situação, observação resumida das em aberto) + regras estritas:
   menção APENAS quando a dúvida for relacionada; uma solicitação por
   resposta; nunca proativo na saudação; tratamento diferente para
   `Em aberto` / `Deferido` / `Indeferido`.

**Contexto**
A operação acadêmica usa o SIAA como sistema de protocolos de solicitações
(histórico escolar, colação, declarações, trancamento, acesso etc.). Hoje
o aluno frequentemente abre a conversa via WhatsApp já tendo uma solicitação
em andamento ou recém-resolvida no SIAA, e o agente respondia genericamente
sem saber disso — gerando atrito (aluno achando que ninguém viu, ou pedindo
algo que já está deferido). A planilha do SIAA é exportada diariamente (~150k
linhas no histórico atual), com 18 colunas incluindo RGM, CPF, subprocesso,
datas, observação e situação.

**Alternativas descartadas**
- *Sync direto com SIAA (API/DB):* exigiria credenciais e contrato com
  o time do SIAA, custo desproporcional. Snapshot diário via XLSX é o que
  o usuário já tem disponível.
- *Pasta monitorada para auto-import:* exige rotina paralela e introduz
  pontos de falha (arquivo corrompido, encoding etc.). Upload manual via
  Cockpit dá controle direto e feedback imediato (contagens, erros).
- *Mencionar proativamente na saudação:* viraria poluição quando o aluno
  abre conversa sobre outro assunto. Decisão: LLM decide com base no
  contexto da pergunta (`smart`).
- *Estratégia INCREMENTAL (upsert):* dados do SIAA mudam de status, dias
  em aberto recalculam, etc. Snapshot completo via TRUNCATE+INSERT é mais
  simples e fiel ao estado de verdade do dia.

**Impacto**
Primeira importação: **149.999 linhas** (881 em aberto, 902 pendentes,
147.657 concluídas, 1.440 canceladas). Contagens batem com a planilha
fonte (apenas 3 linhas sem CPF/subprocesso foram skipped).

Em runtime, o lookup é por `cpf` indexado e custa < 5ms por mensagem.
Log mostra `[CAA] N solicitacao(oes) | em aberto: K` para cada aluno
identificado, dando visibilidade imediata.

Arquivos:
- [kb_api.py](kb_api.py): `_ensure_caa_table`, `_clean_cpf`, `_to_date`,
  `_to_int`, `_normalize_header`, `_build_col_index`, endpoints
  `/api/caa/{upload,status,by-cpf/{cpf},list}`.
- [kb_admin.html](kb_admin.html): aba `tab-caa`, funções `initCaa`,
  `loadCaaStatus`, `loadCaaList`, `caaUpload`.
- [agente_ao_vivo_v4.py](agente_ao_vivo_v4.py): `fetch_caa_solicitacoes`,
  hook em `handle_message` (CPF validado + path normal), bloco
  `SOLICITACOES CAA` em `build_student_context`.

Operação: usuário substitui o snapshot diariamente arrastando o
`data.xlsx` na aba CAA do Cockpit. Não é necessário reiniciar agente —
o lookup lê direto da tabela.

---

### [2026-05-19] - Resgate automático pós-encerramento (process_post_close_rescue)

**Decisão**
Criar rotina `process_post_close_rescue()` que detecta conversas reabertas
após encerramento de atendente humano (sem atendente atribuído + cliente
mandou mensagem 5 a 60 min atrás + histórico recente contém evento de
encerramento). Para cada caso:
1. Se a última msg do aluno for **despedida** (obrigado, valeu, ok, tchau,
   blz, 👍, 🙏, etc., detectado por `_is_farewell_message`): o bot envia
   agradecimento humanizado curto e finaliza a conversa novamente
   (`close_conversation_crm`). NÃO atribui atendente — não tem sentido
   ocupar um humano com uma despedida.
2. Se for **dúvida real**: tenta sticky last-attendant — extrai o nome
   do atendente que encerrou via regex no histórico
   (`_extract_last_attendant_from_history`, padrão "Camila Ferreira
   finalizou o atendimento") e, se ele estiver ativo agora
   (`is_attendant_active_now`), re-atribui com mensagem "Vou pedir para a
   *Camila*, que estava te atendendo, dar continuidade". Se o atendente
   anterior não está ativo, cai no fluxo normal de distribuição (menor
   fila). Se nenhum consultor disponível, registra `human_unavailable`
   em `pending_escalation` para visibilidade no Cockpit.

**Contexto**
Em 2026-05-19, o usuário reportou caso do aluno **Angelo Antonio Junior**:
- 13:34 - Camila Ferreira finalizou o atendimento
- 13:36 - Aluno respondeu (DCZ reabriu a conversa em "Atendimento" SEM atendente)
- DCZ enviou card automático "Este atendimento foi encerrado, se quiser
  retornar..."
- Conversa ficou parada sem atendente

O `process_in_hours_rescue` (criado mais cedo) pegaria isso em 10 min e
atribuiria um consultor qualquer. Mas:
1. Se for só despedida, ocupa um humano sem necessidade
2. Se for dúvida real, perde continuidade ao trocar de atendente
3. 10 min é muito para uma despedida onde 2 min basta para fechar

**Alternativas descartadas**
- *Tratar dentro do `handle_message` quando mensagem chega*: o fluxo
  natural já tenta tratar, mas conversas reabertas pelo DCZ entram com
  filtros diferentes (status finished migra para open, etc.) e às vezes
  são silenciosas. Função dedicada é mais robusta.
- *Sempre finalizar quando última msg parece despedida, sem analisar*:
  arriscado — "obrigado, queria saber sobre X" começaria com despedida
  mas tem dúvida real. A heurística `_is_farewell_message` exige que
  após remover keywords sobrem ≤2 palavras significativas.
- *Sempre re-atribuir ao mesmo atendente, mesmo offline*: criaria órfã
  permanente. Fallback para menor fila é necessário.

**Impacto**
- Despedidas pós-encerramento são fechadas em até 10s do próximo ciclo
  (cycle % 10), com agradecimento humanizado. Aluno não fica olhando
  "sem resposta" e a equipe não vê órfã.
- Reaberturas com dúvida real preservam continuidade do atendente
  (sticky), respeitando a relação humana já estabelecida.
- Padrão de detecção de "atendente que encerrou" é regex simples no
  body do evento DCZ; funciona com formato atual "<Nome Sobrenome>
  finalizou o atendimento".
- Caso manual do Angelo: tratado em paralelo pelo `_fix_angelo.py`
  (Camila offline → transferido para Felipe).

Constantes (em `agente_ao_vivo_v4.py`):
- `POST_CLOSE_RESCUE_AGE_MIN = 5`
- `POST_CLOSE_RESCUE_MAX_AGE_MIN = 60`
- `POST_CLOSE_RESCUE_COOLDOWN_S = 1800`
- `_FAREWELL_KEYWORDS` e `_FAREWELL_EMOJIS` listam padrões de despedida

---

### [2026-05-19] - Resgate automático de órfãs dentro do horário (process_in_hours_rescue)

**Decisão**
Criar rotina `process_in_hours_rescue()` que roda a cada 10 ciclos do loop
principal e, dentro do horário comercial, detecta conversas órfãs (sem
atendente, cliente sem resposta ≥ 10 min, idade ≤ 6h) e:
1. Atribui ao consultor ativo com menor fila (mesma lógica de `get_available_consultant`).
2. Envia mensagem humanizada de desculpa ao aluno no chat público.
3. Registra nota interna explicando o resgate.
4. Incrementa `fila` no Supabase e marca `pending_escalation` como resolved
   (se existir).
5. Se não houver consultor disponível, registra em `pending_escalation` com
   `reason='human_unavailable'` para aparecer no painel.

**Contexto**
Em 2026-05-19, mesmo após correções pontuais (media-only dentro do horário,
human_unavailable em pending_escalation, fix do grade_link, preferred_attendant),
o usuário continuou identificando conversas órfãs na aba "Não iniciados" do
DCZ — alunos que mandaram mensagem e ficaram sem resposta por 10-70 min sem
atendente atribuído. Foram resgatadas 8 conversas manualmente como base do
levantamento. O padrão é recorrente porque os bugs/edge cases do agente são
diversos (RAG falha silenciosa, dedup mata mensagem, watchdog reinicia no meio
de um ciclo, fluxo entra em estado não previsto, etc.), e isolar caso a caso
é jogo de Whac-A-Mole.

**Alternativas descartadas**
- *Reprocessar internamente cada conversa órfã via handle_message*:
  arriscado — pode duplicar respostas, re-acionar fluxos travados pelo mesmo
  bug que causou a órfã, ou cair em loop. Não vale a complexidade.
- *Apenas notificar no painel sem atribuir*: depende de alguém olhar o
  painel ativamente, o que é exatamente o problema que o usuário pediu para
  evitar ("não precisar ficar sempre pedindo"). Descartado.
- *Threshold de 5 min*: agressivo demais — pegaria conversas que o bot
  ainda iria processar no próximo ciclo. Descartado em favor de 10 min.

**Impacto**
Conversas órfãs deixam de depender de inspeção manual. Risco controlado:
não interfere em conversas com atendente atribuído, respeita cooldown de
30 min por conversa, ignora finished/finalized, só age dentro do horário,
e a mensagem ao aluno é humanizada (não delata o resgate como falha
do sistema). Se não houver consultor disponível, ainda assim a conversa
fica registrada no painel `pending_escalation` para ação manual.

Constantes principais (em `agente_ao_vivo_v4.py`):
- `IN_HOURS_RESCUE_AGE_MIN = 10`
- `IN_HOURS_RESCUE_MAX_AGE_MIN = 360`
- `IN_HOURS_RESCUE_COOLDOWN_S = 1800`

---

### [2026-05-19] - Consultor preferido sticky (preferred_attendant)

**Decisão**
Adicionar coluna `preferred_attendant VARCHAR(64)` na tabela `pending_escalation`
e, dentro do horário comercial, antes do fluxo normal, honrar promessas anteriores
de um consultor específico feito ao aluno fora do horário.

**Contexto**
Em 2026-05-19, a aluna Edna pediu trancar matrícula às 08:23 (fora do horário).
O agente respondeu corretamente prometendo o Wesley e registrou
`pending_escalation` com `reason='retention_after_hours'`, mas SEM marcar Wesley
como preferred. Quando a aluna voltou após as 9h, o fluxo normal de distribuição
rodou e ela foi enviada para outro consultor, contradizendo a promessa.

**Alternativas descartadas**
- *Tabela nova `conv_preferences`*: limpa, mas duplica a fila de escalation e
  adiciona ponto de leitura/escrita. Preferi reutilizar `pending_escalation`
  porque ela já rastreia "promessa feita ao aluno".
- *Estado só em memória (`_conv_states`)*: perde após restart/watchdog;
  no contexto noturno o agente reinicia algumas vezes, então é inviável.

**Impacto**
- Schema: nova coluna `preferred_attendant` (auto-aplicada via
  `ALTER TABLE ADD COLUMN IF NOT EXISTS`).
- Comportamento novo:
  - **Retenção fora do horário** → marca `preferred_attendant='Wesley'`.
  - **Aluno cita consultor pelo nome** + pista de pedido (ex: "queria falar com
    a Mariana") fora do horário → marca o nome detectado.
  - Próxima mensagem do aluno dentro do horário (até 24h depois):
    - Se consultor está ATIVO no Supabase → transfere + msg humanizada
      ("Conforme combinamos mais cedo/ontem, vou te conectar com X agora").
    - Se ainda INATIVO → msg humanizada explicando que está aguardando e
      oferecendo alternativa.
- Janela de expiração: **24 horas**. Após isso, o vínculo é ignorado e o aluno
  segue o fluxo normal.
- Detecção de pedido nominal exige **um hint de pedido** + **alias do nome** para
  evitar falsos positivos (ex: "Ontem a Mariana já me ajudou" não dispara).
- Funções novas em `agente_ao_vivo_v4.py`:
  - `detect_preferred_attendant(text)`
  - `get_active_preferred_attendant_promise(conv_id, max_age_hours=24)`
  - `is_attendant_active_now(attendant_name)`
  - `honor_preferred_attendant_promise(conv_id, promise)`
- Constante nova: `ATTENDANT_ALIASES` (mapeia nome canônico → variantes
  aceitas).

---

### [2026-05-19] - Fallback `human_unavailable` grava em `pending_escalation`

**Decisão**
Quando o agente cai no fallback "nenhum consultor ativo" durante o horário
comercial, além de enviar `HUMAN_BUSY_MSG` e nota interna, também registrar
em `pending_escalation` com `reason='human_unavailable'` e `tier='pending'`.

**Contexto**
Em 18/05/2026 (segunda) à noite, ~40 conversas caíram nesse fallback porque a
equipe foi marcando-se Inativo antes do fim do expediente (20h). Conversas com
atendente histórico recebiam a nota interna direcionada; um lead novo (Quero
me matricular, telefone 11986769527) ficou órfão: sem atendente, sem pipeline,
sem entrada na fila do Cockpit. Ninguém viu.

**Alternativas descartadas**
- *Tratar como after-hours quando ninguém disponível*: confundiria o aluno
  ("amanhã às 9h" quando ainda é 19h e o expediente formal é até 20h).
- *Forçar distribuição a um consultor Inativo*: ele pode estar offline o resto
  do dia; conversa fica parada do mesmo jeito.

**Impacto**
- Casos `human_unavailable` agora aparecem no painel "Fora do Horário" com
  rótulo "Sem consultor disponível".
- Watchdog/fila do Cockpit cobre o caso e a equipe vê pela manhã.

---

### [2026-05-19] - Bug `next_human_available_label` dentro do horário

**Decisão**
Função `next_human_available_label()` agora retorna `"em breve"` quando
`is_within_business_hours()` é verdadeiro.

**Contexto**
A função retornava "amanhã às 9h" mesmo dentro do horário comercial — caso de
borda esquecido. O bug se materializou via `send_media_only_response()` na
manhã do dia 19/05, fazendo a aluna Paula Chioratto (já frustrada/detrator)
receber "te retorno amanhã" quando o atendimento já estava ativo.

**Alternativas descartadas**
- *Corrigir só em `send_media_only_response`*: deixaria a função genérica
  vulnerável a outros chamadores. Optei por defesa em profundidade.

**Impacto**
- Mensagens "fora do horário" dentro do horário não acontecem mais.
- `send_media_only_response` também foi corrigida: dentro do horário,
  distribui ao humano imediatamente; fora, mantém o registro em pending.

---

### [2026-05-19] - Watchdog do agente + after-hours rescue

**Decisão**
1. `kb_api` mantém um thread watchdog que reinicia o agente quando o heartbeat
   passa de **10 min** sem update.
2. Agente roda `process_after_hours_rescue()` a cada 10 ciclos fora do horário,
   pegando conversas com "atendente fantasma" e enviando a mensagem padrão de
   "fora do horário" (dedup persistente via histórico).

**Contexto**
Conversas com atendente atribuído ficavam mudas à noite porque o agente
respeitava o `attendants` e não respondia, mas o humano estava offline. O
sistema precisava cobrir esse buraco autonomamente.

**Impacto**
- Heartbeat a cada 2 ciclos + uma vez no início de cada loop.
- Threshold do watchdog: env `AGENT_WATCHDOG_THRESHOLD_MIN`, default 10.
- Rescue ignora conversas <10 min e >24h de idade; cooldown de 6h por
  conversa em memória; dedup pelos fingerprints da mensagem.
