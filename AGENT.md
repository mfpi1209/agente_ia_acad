# AGENT.md — registro de decisões estruturais

## Convenções

- Decisões registradas em ordem cronológica decrescente.
- Cada entrada: **Decisão**, **Contexto**, **Alternativas descartadas**, **Impacto**.

---

### [2026-07-08] - Saneamento dos findings do supervisor (45k → backlog controlado)

**Decisão**
Corrigido o acúmulo descontrolado de `agent_audit_findings` (45.317 findings, só 15
resolvidos). Quatro mudanças no supervisor + limpeza única:
- **A) Dedup na gravação** (`_record_audit_finding`): não cria nova linha se já existe
  finding ABERTO do mesmo `conv_id+problem_type` nas últimas 24h.
- **B) Auto-expirar** (`_expire_old_audit_findings`, 1x/h no loop): arquiva findings
  abertos com mais de 7 dias (`resolved_by='auto_expirado'`).
- **C) Cooldown persistente** (tabela `supervisor_audit_seen` + `_load_audit_seen`/
  `_persist_audit_seen`): a janela de 10min de não-reauditar sobrevive a restart.
- **D) Normalização de severidade** na gravação (`high/medium/low → alta/media/baixa`)
  e correção da query de idempotência (`severity IN ('alta','high')`).
- **E) Limpeza única** (script descartável): normalizou severidade histórica e arquivou
  (sem deletar, `resolved_by='cleanup_2026_07_08'`) todos os abertos exceto o mais
  recente por `conv_id+problem_type`; depois auto-expirou os >7 dias.

**Contexto / diagnóstico**
Investigação no banco: 45.317 findings em 10.555 conversas (média 4,3/conv, pior caso
46 numa só conversa re-auditada por ~39 dias). Duplicata exata era pouca (117 linhas) —
a inflação vinha de re-auditar a MESMA conversa a cada ciclo (cooldown só em memória,
zerava no restart) e nada nunca expirava. Severidade convivia em 2 vocabulários
('alta' 9.722 + 'high' 4.758), quebrando contagem.

**Resultado**
Findings abertos: 45.317 → **3.867** (últimos 7 dias, deduplicados). 21.095 arquivados
por dedup + 20.356 por expiração; 4.758 normalizados high→alta. Nada foi deletado.

**Alternativas descartadas**
- Deletar findings antigos: descartado — arquivar (resolved_at) preserva histórico/auditoria.
- Dedup por duplicata exata (conv+tipo+summary): pegava só 117 — o problema era temporal.

**Impacto**
Backlog do supervisor passa a ser sustentável e a taxa/severidade viram sinal confiável
de qualidade. Requer deploy p/ A–D valerem em produção.

---

### [2026-07-08] - Expansão da aba Desempenho: telemetria real + confiabilidade dos dados

**Decisão**
Aprovada a Fase 1 da expansão do cockpit para dar visibilidade REAL do agente:
1. **Item 0 — Instrumentação**: novas colunas em `ia_interaction_log` (`t_total_ms`,
   `t_espera_ms`, `t_rag_ms`, `t_llm_ms`, `tokens_in`, `tokens_out`, `custo_usd`, `model`)
   gravadas pelo agente no caminho principal de resposta. Custo calculado com preço real
   do gpt-4o-mini (input $0.15/1M, output $0.60/1M).
2. **Item 6 — Avaliação**: unificado o vocabulário do campo `interaction_summary.avaliacao`
   para `correta`/`incorreta` (migra `aprovada→correta`, `reprovada→incorreta`).
3. **Item 5 — Limpar lixo**: agente deixa de tabular conversas só de saudação (pergunta e
   resposta vazias); listagens filtram linhas sem resposta.
4. **Item 4 — Qualidade confiável**: novo agregado de `agent_audit_findings` (findings do
   supervisor OpenAI por severidade + taxa de problema) como sinal de qualidade objetivo.
5. **Repensar aba "Agente IA"**: sentimento (IA) e ✓/✗ manual saem de KPI — sentimento vira
   contexto rotulado "estimado por IA"; qualidade passa a ser os findings do supervisor.
6. **Regra transversal de UX**: toda métrica não-100%-confiável ou estimada exibe aviso
   ("estimativa"/"aproximado") no cockpit.

**Contexto**
Auditoria dos dados existentes revelou que: sentimento é classificado por gpt-4o-mini com
default `neutro` (por isso "quase tudo neutro"); a "taxa de acerto" depende de rótulo manual
(NULL por padrão) sobre uma lista poluída por saudações/linhas sem resposta; e o custo/token
do Dashboard vinha de `chat_evaluations` (só Playground/testes manuais), não da produção —
número enganoso. Faltava o essencial e confiável: tempo real de resposta e custo real.

**Alternativas descartadas**
- Melhorar o classificador de sentimento para virar KPI: continua sendo estimativa subjetiva.
- Manter dois vocabulários de `avaliacao` (aprovada/reprovada vs correta/incorreta): é bug,
  contava taxa de acerto errado conforme a aba de origem.
- Duplicar custo/token em Dashboard e Desempenho: cada métrica tem fonte única; custo real
  (Item 0) vira dono, remove-se a fonte de Playground.

**Impacto**
Cockpit passa a separar sinal objetivo (tempo/custo/findings) de sinal estimado (sentimento).
Item 0 só acumula a partir do deploy → visualizações de tempo/custo entram na Fase 2, após
dados existirem. Colunas nullable e migração de dados idempotente = baixo risco.

---

### [2026-07-06] - Framework de avaliação de desempenho (Fase 0) + descoberta de método

**Decisão**
Criado `avaliacao_desempenho.py`: relatório-baseline que compara a era ANTIGA (bot de
menu/saudação) vs a ERA DO AGENTE (IA), a partir de `log_conversa`, `ia_interaction_log`
e `disparos`. Métrica-âncora = **tempo até um HUMANO real responder** (não "tempo até
1ª resposta"). Corte de produção configurável via `AGENT_PROD_CUTOFF` (default 2026-05-20).

**Contexto / descoberta**
A comparação ingênua de "tempo até 1ª resposta" dava resultado invertido (era antiga
"mais rápida"). Investigando o conteúdo, descobriu-se que a **era antiga NÃO era manual**:
tinha um bot de menu/saudação (DataCrazy) que respondia em ~4s ("Bem vindo ao Suporte",
"Veja as opções"). A conta compartilhada `Suporte`/`Administrador` = automação nas DUAS
eras; humano = atendente com nome próprio. Logo, a métrica justa é "tempo até humano".

**Resultado do baseline (jul/2026)**
- Tempo mediano até humano: ANTIGA 15,6min → AGENTE 8,0min (~metade).
- Cauda p90 até humano: ANTIGA 14,6h → AGENTE 1,8h (jun/jul chegou a ~1h) — maior ganho.
- % de conversas que chegam a um humano subiu (88-91% → 95-98% em jun/jul).
- IA resolve sozinha ~7,6% (baixo → oportunidade de FAQ/base). IA envia 33-47% das msgs.
- Mix de ação: 56% follow-up/auto-close, 22% menu, 8% escala, 5% resolve, 0,6% retenção.
- Disparos: 67k enviados, 7,2% de resposta; `activation_manual_outcomes` quase vazia
  (só 76 desfechos) → rastreio de resultado de retenção é um gap.

**Alternativas descartadas**
- "Tempo até 1ª resposta" cru: enganava por causa do auto-greeting da era antiga.
- Atribuir IA via `agent_sent_signatures` por timestamp: descartado por risco de timezone;
  o nome do atendente em `log_conversa` já separa automação de humano de forma limpa.

**Impacto**
Base numérica confiável para acompanhar o agente e priorizar melhorias. Próximas fases
(a aprovar): views + job diário de métricas materializadas e aba no cockpit.

---

### [2026-07-06] - User-Agent obrigatório nas chamadas DataCrazy (Cloudflare 403)

**Decisão**
Adicionado `User-Agent` (e `Accept`) ao header `H` usado em TODAS as chamadas ao
DataCrazy no agente.

**Contexto**
O DataCrazy (Cloudflare) passou a responder **HTTP 403 "Attention Required"** para
requests sem User-Agent / com o UA padrão do `python-requests`. Resultado: o agente
ficava com `fetched=0` em todo ciclo (loop vivo, mas sem puxar conversas) e **parava
de distribuir** — a fila subiu muito. Teste direto confirmou: **sem UA = 403, com
qualquer UA = 200** (retornou as conversas normalmente).

**Alternativas descartadas**
- Esperar o DataCrazy "voltar": não era instabilidade transitória, e sim bloqueio por
  falta de UA; sem o header o problema persistiria.
- Mexer em cada chamada individual: todas já usam o `H` central, então bastou o `H`.

**Impacto**
Restaura o acesso do agente ao DataCrazy (leitura de conversas, distribuição, tags,
notas). OBS: o cockpit (`kb_api.py`) tem chamadas DataCrazy com header próprio sem UA —
precisa do mesmo ajuste se apresentar 403.

---

### [2026-07-06] - RGM: telefone único (1 RGM) preenche sem exigir CPF

**Decisão**
Ajuste no `_resolve_rgm_verified`: além das regras por CPF, passa a preencher o RGM
quando **não há CPF confirmando, mas o telefone aponta para exatamente 1 RGM** na
`mm_matriculados` (telefone inequívoco). Telefone compartilhado (**>1 RGM**) continua
**travado** exigindo CPF (protege contra RGM de outra pessoa — caso Livia).

**Contexto**
A regra estrita de CPF (30/06) derrubou a cobertura de RGM no painel de ~95% para ~7%
(6 de 88 em 7 dias), porque a maioria dos leads no DataCrazy **não tem CPF**. Medição:
dos 89 telefones sem RGM, **66 apontavam para 1 único RGM** (seguros) e **0 eram
compartilhados** — ou seja, a regra estrita bloqueava casos sem risco algum. O problema
original da Livia era especificamente telefone compartilhado (2 RGMs no mesmo número).

**Alternativas descartadas**
- Manter estrito só com CPF: mantém a cobertura baixíssima (usuário reclamou).
- Voltar ao `_fetch_rgm` (telefone puro, LIMIT 1): reintroduz o bug do telefone
  compartilhado (Livia pegaria o RGM do Gustavo).

**Impacto**
Recupera ~66 casos atuais e os futuros, sem risco de RGM trocado (telefone
compartilhado segue exigindo CPF). Aplicado no fill imediato e no backfill periódico.
Backfill único executado para preencher os casos já existentes no painel dentro da regra.

---

### [2026-06-30] - Criação de lead robusta também na distribuição normal

**Decisão**
Generalizado o helper `_ret_ia_ensure_lead` → `_ensure_lead_for_conv(lead_id, phone, name)`
e passado a usá-lo em **toda** a distribuição (não só na retenção):
1. **Caminho com consultor** (`_distribute_to_attendant_locked`): substituída a busca/
   criação antiga (que usava o telefone com DDI 55) pelo helper, que normaliza p/
   nacional ao buscar (match por sufixo) e ao criar — evita lead duplicado/não
   vinculado.
2. **Fallback sem consultor** (`human_unavailable`): agora também cria/garante o lead
   antes de postar a nota e enfileirar. Antes, esse caminho **nunca criava lead**.

**Contexto**
Caso reportado (Gaby): atendimento normal escalado por baixa confiança (0.30); a
conversa caiu no fallback `human_unavailable` (sem consultor) — chegou nota interna,
mas o painel mostrava "Lead não encontrado" porque o fallback não criava lead. Além
disso, o caminho com consultor sofria do mesmo bug do DDI 55 da retenção (criava/
buscava com `55...` e não vinculava ao contato da conversa, em formato nacional).

**Alternativas descartadas**
- Criar lead só no caminho com consultor: deixaria as conversas em fila (sem consultor)
  sem lead, mantendo o "Lead não encontrado".
- Manter helper separado para retenção e distribuição: duplicação e risco de divergir.

**Impacto**
Toda conversa escalada (com ou sem consultor disponível) passa a ter lead criado/
vinculado em formato nacional. Cria mais leads no fallback, mas são contatos reais já
em escalonamento — consistente com o caminho que já criava lead na distribuição.

---

### [2026-06-30] - RGM verificado por CPF+telefone + dedup (1 linha por pessoa)

**Decisão**
Revisão do preenchimento de RGM no painel do Disparador (caa_ia):
1. **RGM com identidade confirmada** (`_resolve_rgm_verified`): só grava o RGM
   quando **CPF e telefone apontam para o MESMO** registro em `mm_matriculados`
   (ou CPF confirmado pelo telefone do próprio registro / CPF sem registro de
   telefone). Se CPF e telefone **divergem**, ou se só há telefone sem CPF, **não
   marca** (evita pegar o RGM de outra pessoa em telefone compartilhado).
   Substitui o `_fetch_rgm` (que resolvia só por telefone).
2. **Dedup (1 linha por pessoa)**: o backfill mantém a linha `caa_ia` **mais
   recente** por `datacrazy_lead_id` e **apaga** as demais (autorizado), evitando
   a mesma pessoa aparecer várias vezes no painel.
3. **Limpeza**: RGM não confirmado é deixado **em branco** (volta ao estado
   anterior, sem marcar pessoa errada).
Aplicada limpeza única no passivo: 8 duplicatas removidas, 8 RGM não confirmados
limpos; 61/73 leads caa_ia com RGM verificado.

**Contexto**
Caso Livia: o painel mostrava a pessoa 4× e com RGM de outra pessoa. Diagnóstico:
(a) o `_fetch_rgm` resolvia por **telefone**, e o telefone do lead pertencia a
outro aluno na base acadêmica → RGM errado; (b) preencher o RGM disparava uma
fan-out na consulta do painel → multiplicação visual. Obs.: no caso específico
da Livia, o lead no CRM tem **nome "Livia" mas CPF e telefone do "Gustavo"**
(RGM 48960632) — inconsistência de cadastro do lead, não do matching.

**Alternativas descartadas**
- *Reverter tudo (remover RGM)*: o usuário optou por manter, mas correto.
- *Reverter só o código*: deixaria o passivo de RGM errado/duplicado no painel.
- *Resolver RGM só por telefone*: causa o erro de identidade (telefone
  compartilhado/trocado).

**Impacto**
- Ninguém recebe RGM de outra pessoa; cada pessoa aparece 1× no painel caa_ia.
- `_resolve_rgm_verified` faz 1 GET /leads/{id} (CPF) por lead — backfill
  limitado a 80 leads/passada, throttle 10 min.
- Leads sem CPF confiável ficam sem RGM (como era antes), em vez de errado.

---

### [2026-06-30] - RET-IA: garantir lead válido (DDI 55 → nacional) antes da tag/nota

**Decisão**
Em `_trigger_retention_tag_only`, antes de adicionar a tag RET-IA e a nota, o
agente passa a **garantir um lead válido** via `_ret_ia_ensure_lead`:
1. valida o `lead_id` recebido (`GET /leads/{id}`) — descarta "lead fantasma";
2. busca lead existente por telefone testando variações (nacional sem `55`,
   com `55`, últimos 11) e validando o **sufixo** do telefone p/ não casar com
   outra pessoa;
3. se não existir, **cria** o lead com o telefone em **formato nacional**
   (sem DDI 55), casando com o contato da conversa.
Só com lead válido segue para tag + nota; sem lead, não tagueia (loga e silencia).

**Contexto**
Caso Francieli: a nota de retenção chegou mas o painel mostrava "Lead não
encontrado" e a automação não acionava. Diagnóstico confirmado via API: o
DataCrazy guarda o contato/lead da conversa em **formato nacional** (ex.:
`15997582595`), mas o agente buscava/criava com `5515997582595` (com DDI 55).
Resultado: `identify_student` não achava o lead existente e
`create_lead_and_business` criava um lead com telefone `55...` que **não vincula**
ao contato da conversa → "Lead não encontrado" e automação não disparava.
(Busca por `5515997582595` → 0; por `15997582595` → 1 lead.)

**Alternativas descartadas**
- *Vincular lead↔conversa por endpoint dedicado*: o DataCrazy vincula
  contato↔lead automaticamente pelo telefone (`contact.externalId`); basta o
  telefone do lead casar. Não há necessidade (nem endpoint claro) de associação
  manual.
- *Manter `identify_student` (busca só com o telefone cru)*: continuaria
  falhando para números com DDI 55.
- *Normalizar o telefone globalmente*: risco de efeito colateral em outros
  fluxos; mantive a normalização contida no fluxo de retenção.

**Impacto**
- Retenção sempre acaba com um lead válido e vinculado à conversa antes de
  tag/nota → automação 'Retenção IA' aciona de forma confiável.
- Evita leads duplicados (acha o existente por sufixo de telefone).
- Helpers novos: `_ret_ia_phone_variants`, `_lead_exists`, `_ret_ia_ensure_lead`.

---

### [2026-06-30] - RGM no painel do Disparador para casos RET-IA (CAA_IA)

**Decisão**
Sempre que o agente aciona a tag RET-IA, ele passa a garantir o RGM no painel
"Leads para marcar" do Disparador (banco `disparos`, tabela
`activation_responses`, coluna `rgm`). Duas frentes:
1. **(A) Imediata** — em `_trigger_retention_tag_only`, resolve o RGM via
   `_fetch_rgm` (telefone/CPF → `dcz_sync.mm_matriculados`) e atualiza
   (`_ret_ia_fill_rgm_disparador`) os registros do aluno com
   `origem_ativacao='caa_ia'` e `rgm` nulo/`undefined`.
2. **(B) Backfill periódico** — `_ret_ia_backfill_rgm_disparador` roda no laço
   principal (a cada 10 ciclos, com throttle interno de 10 min), cruzando por
   telefone os `caa_ia` ainda sem RGM dos últimos 30 dias.
Escopo **restrito a `origem_ativacao='caa_ia'`** (distribuídos pelo agente);
nunca toca em `caa_atm`/`caa`/`financeiro`/`rematricula`/etc.

**Contexto**
No painel do Disparador, leads de retenção do fluxo da IA (BASE "Processos
CAA_IA") apareciam com RGM `undefined` (77 de 79). O RGM em
`activation_responses` só é preenchido quando a mensagem chega com
`master_key='RGM:xxxx'` (disparos já chaveados por RGM) — o que não acontece no
inbound espontâneo/IA. Porém o RGM existe e é recuperável por telefone em
`dcz_sync.mm_matriculados` (confirmado: telefone 11966153426 → RGM 47535229).

**Alternativas descartadas**
- *Preencher o campo customizado RGM no lead do DataCrazy*: o disparador resolve
  o RGM via `master_key` do inbound, não pelo lead nem pela tabela `students`
  (telefone testado não existe em `students`); não corrigiria o painel.
- *Só (A)*: sujeito a race — a linha em `activation_responses` costuma ser criada
  pelo disparador após a tag; ficaria `undefined`. Por isso (A)+(B).
- *Backfill em qualquer category*: rejeitado a pedido — manter escopo só `caa_ia`
  para não alterar dados de outras origens (ex.: `caa_atm` manual).

**Impacto**
- Novos casos RET-IA sobem no painel já com RGM (ou são corrigidos em ≤10 min).
- Limpa o passivo de `caa_ia` sem RGM (escopo: últimos 30 dias por execução).
- Helper `_fetch_rgm` criado (o `fetch_academic_data` não retornava `rgm`).
- Escrita somente-RGM e idempotente no banco `disparos` (sistema externo do
  disparador); só atualiza linhas `caa_ia` com RGM ausente.

---

### [2026-06-30] - RET-IA: garantir deal + etapa Atendimento antes da tag (qualquer pipeline)

**Decisão**
Novo helper `_ret_ia_ensure_business_atendimento(lead_id, phone)` chamado em
`_trigger_retention_tag_only` ANTES de aplicar a tag RET-IA. Ele:
1. busca o negócio (deal) do lead (`/leads/{id}/businesses`, fallback search por telefone);
2. se não existe, **cria** um deal já em `STAGE_ATENDIMENTO_ID`;
3. se existe, **move** o deal para `STAGE_ATENDIMENTO_ID` (pipeline Base de Alunos),
   tirando-o de Encerramento/Perdido.
Só roda no caminho de disparo real (fora do dedup de 6h) e antes da tag, para a
automação 'Retenção IA' acionar já com o deal no pipeline correto.

**Contexto**
Dois casos reportados de retenção real (aluno mandou "quero cancelar/trancar") em que
a automação NÃO acionava mesmo com tag+nota:
- Aline Jenefer (#56108): deal em pipeline **Encerramento** — a automação não atende
  esse pipeline, então a tag entrava mas nada acontecia.
- Alunos **sem deal criado**: tag/nota na conversa, mas sem negócio para a automação agir.

**Alternativas descartadas**
- *Ajustar só no n8n (remover filtro de pipeline)*: a automação ficaria atendendo
  qualquer pipeline, inclusive casos não desejados; é trabalho no n8n e fora do controle
  do agente. Mover o deal para Atendimento é determinístico e replica o que a antiga
  distribuição já fazia (`trigger_retention` setava `STAGE_ATENDIMENTO_ID`).

**Impacto**
- Retenção real passa a acionar a automação independente do pipeline de origem.
- Salvaguarda: só acontece quando a mensagem ATUAL é intenção de retenção
  (`is_retention_intent`), então não "ressuscita" cancelamentos antigos sem novo pedido.
- Risco residual: um aluno em Encerramento/Perdido que mande novo "quero cancelar" é
  movido para Atendimento — comportamento desejado pelo pedido ("independente do pipeline").

---

### [2026-06-30] - Agente não encerra mais conversa em RETENÇÃO (RET-IA)

**Decisão**
Três ajustes para o agente NUNCA dar follow-up nem auto-close em conversa entregue
à automação/time de Retenção:
1. No fluxo principal, após `_trigger_retention_tag_only`, marcar
   `waiting_for_client=False; inactivity_start=0` (antes marcava `True`, o que tornava
   a conversa elegível ao loop de follow-up/encerramento).
2. TTL do handoff de retenção em `_trigger_retention_tag_only`: 8h → **72h**. Os guards
   de FOLLOWUP-1 e AUTO-CLOSE já pulam quando há handoff ativo; com 8h ele expirava
   antes do atendimento.
3. Rede de segurança no AUTO-CLOSE (stage 1) e DIRECT-CLOSE (stage 2): além do
   `_is_handoff_active`, checa `_is_in_retention(cid, msgs=...)` (histórico recente do
   aluno pedindo cancelar/trancar) e NÃO encerra se estiver em retenção.

**Contexto**
Caso Maria Clara (#185134): aluna pediu cancelamento, o agente aplicou a tag RET-IA +
nota e silenciou. A conversa ficou ~16h aguardando o time. O handoff de retenção (TTL
8h) expirou e o loop de auto-close enviou "Como não tivemos retorno, vou finalizar..."
e encerrou a conversa — sendo que a aluna nem falou com o consultor. A distribuição
foi manual (a automação n8n não conectou um consultor em tempo).

**Alternativas descartadas**
- *Só estender o TTL*: o `waiting_for_client=True` ainda deixava a conversa no loop;
  e se o time demorasse mais que o TTL, fecharia de novo. Por isso também (1) e (3).
- *Só checar histórico no close*: custo de fetch por ciclo; combinado com (1)+(2) o
  fetch só ocorre nos poucos casos que chegam ao estágio de close.

**Impacto**
- Conversa em retenção não recebe mais follow-up nem é encerrada pelo agente.
- O encerramento fica por conta do consultor/automação de Retenção.
- Custo extra mínimo: 1 fetch de mensagens só quando uma conversa chega ao estágio de
  auto-close/direct-close.

---

### [2026-06-25] - Interceptador RET-IA p/ retenção que cai em Atendimento (tag-only)

**Decisão**
Novo helper `_retention_intercept_for_attendant_conv(cf)` chamado no **filtro de
fetch** (onde conversas com atendente são descartadas). Quando uma conversa já tem
atendente (foi para Atendimento via menu/distribuição n8n) mas a **última mensagem
do aluno é intenção de retenção**, o agente aciona **SOMENTE** a automação RET-IA
(tag + nota + silêncio via `_trigger_retention_tag_only`). NÃO fala com o aluno e
NÃO remove o atendente — quem move para Retenção é a automação (via tag).

Guardas de segurança (só age se TODAS valerem):
- atendente atribuído NÃO é do time de Retenção (Wesley/Danúbia);
- há mensagem recebida não respondida (`lastReceived > lastSended`);
- existe telefone do contato (evita resolver lead errado via `_current_phone`);
- o atendente humano AINDA não falou na conversa (`_human_attendant_active_recently`
  6h = False) — respeita quem já está atuando;
- a última recebida é retenção (negation-aware via `is_retention_intent`);
- dedup de 6h garantido dentro de `_trigger_retention_tag_only`.

**Contexto**
Caso Estela: "Ola" + menu rotearam a conversa para o departamento Atendimento e a
distribuição n8n atribuiu a Camila. O agente exclui do processamento qualquer
conversa com atendente (filtro de fetch `if _cf.get('attendants')` + guard no loop),
então a "Qro cancelar a matrícula" nunca foi avaliada como retenção — sem tag, sem
automação. A correção p/ Wesley (16:24) veio da automação/manual.

**Alternativas descartadas**
- *Deixar o agente processar normalmente conversas com atendente*: arriscado, o bot
  poderia falar por cima de um humano. Por isso o tag-only (não fala/não remove).
- *Só corrigir no n8n/menu (rotear cancelamento p/ Retenção)*: é o fix de causa raiz
  mais limpo (e elimina o churn Atendimento→Retenção), mas é trabalho no n8n; fica
  como melhoria paralela recomendada. O interceptador é a mitigação no agente.

**Impacto**
- Retenção que cai em Atendimento passa a acionar a tag/automação mesmo com atendente.
- Sem mensagem ao aluno e sem remover atendente → preserva a regra D1/D2.
- Se o consultor de Atendimento já respondeu, o agente NÃO age (respeita o humano).
- Persiste o "churn" Atendimento→Retenção (n8n atribui Atendimento e a tag move depois);
  só some com o fix de origem no n8n/menu.
- Custo: 1 fetch de mensagens por conversa-candidata (apenas as não respondidas e com
  atendente fora do time de Retenção).

---

### [2026-06-25] - RET-IA: dedup por conversa (6h) + toggle da tag p/ re-disparar automação

**Decisão**
`_trigger_retention_tag_only` deixou de ser idempotente pelo **estado permanente do lead**
(tag presente) e passou a deduplicar por **conversa**: só suprime se já acionou RET-IA
naquela conversa nas **últimas 6h** (`_signature_recently_sent(conv_id, 'ret_ia', 6h)`).
Fora dessa janela, re-aciona a automação via **toggle da tag** (PATCH removendo a RET-IA →
`sleep 1.5s` → PATCH re-adicionando, com retry 3x no add) e re-posta a nota interna.

**Contexto**
Caso "Gestão de Recursos Humanos": aluno disparou retenção em 23/06 (tag RET-IA aplicada),
voltou em 25/06 com nova intenção de cancelar. O agente reconhecia a retenção mas, como a
tag já estava no lead, caía no `already=True` → não re-aplicava tag, não re-postava nota e a
automação do DataCrazy (gatilho "tag adicionada") **não re-disparava**. Resultado: "só a tag",
sem atendimento. Não era problema do n8n — era a regra do agente.

**Alternativas descartadas**
- *Remover a tag ao encerrar o atendimento*: o agente não sabe com segurança quando o
  consultor resolveu o caso (acontece fora dele) — frágil.
- *Só janela de tempo, sem toggle*: inviável, pois re-adicionar uma tag já presente não
  gera novo evento "tag adicionada" no DataCrazy → automação não re-dispara.

**Impacto**
- Aluno que volta dias depois com nova retenção volta a acionar a automação + nota.
- Repetições dentro da mesma sessão (≤6h) continuam suprimidas (sem spam).
- Risco residual: janela curta sem a tag entre os dois PATCH; mitigado por retry no re-add
  e log de ALERTA se o re-add falhar após o remove.

---

### [2026-06-25] - Rollout GLOBAL: retenção passa a acionar a automação "Retenção IA" (tag RET-IA) para todos

**Decisão**
Após o teste pelo telefone validar o fluxo, removida a trava por telefone: agora **TODA** retenção
detectada apenas **aciona a automação RET-IA** (tag no lead → n8n) e **silencia o bot** — não
distribui (Wesley/Danúbia), não transfere chat, não fala com o aluno. Controlado pela flag
`RET_IA_ALL = True` (em `agente_ao_vivo_v4.py`). A função foi renomeada de `_is_ret_ia_test_phone`
para `_use_ret_ia_automation`.

Todos os pontos de retenção ficaram silenciosos: fluxo principal, in-hours-rescue, queue-sweep
(short-circuit → `_trigger_retention_tag_only`), sticky in-hours/queue-sweep (já eram silenciosos),
e post-close-rescue / low-conf-D4 (mensagem ao aluno suprimida quando automação ativa).

**Contexto**
O gestor corrigiu o erro de flow no n8n que motivou a reversão de 23/06 e validou o disparo pelo
próprio telefone. Agora a retenção é conduzida pela automação, não pela distribuição.

**Impacto / como reverter**
`RET_IA_ALL = False` volta ao modelo de distribuição (e, se quiser, manter só telefones de teste
em `RET_IA_TEST_PHONES`). A tag RET-IA é idempotente; a automação no DataCrazy consome/remove a tag
após rodar. Cada acionamento silencia a conversa por ~8h (handoff de retenção).

---

### [2026-06-25] - TESTE pontual: fluxo RET-IA (tag/automação) só para telefone de teste

**Decisão**
Reativado o fluxo da automação "Retenção IA" (tag **RET-IA** → n8n) **apenas** para os telefones
em `RET_IA_TEST_PHONES` (hoje: `11970617878`). Para esses números, `trigger_retention` chama
`_trigger_retention_tag_only` (adiciona tag RET-IA, posta nota interna, silencia o bot) **em vez de
distribuir**. Todos os demais alunos seguem a **distribuição normal** (Wesley/Danúbia).

**Contexto**
O modelo de automação foi revertido em 23/06 por erros no flow do n8n. O gestor quer revalidar o
n8n sem impactar a operação, então o teste fica restrito ao próprio telefone.

**Impacto / como desligar**
Esvaziar o set `RET_IA_TEST_PHONES` (ou remover o número) volta 100% à distribuição — sem mexer em
mais nada. Trava por telefone compara os últimos 11 dígitos (`_is_ret_ia_test_phone`).

**Correção (mesmo dia)**
No 1º teste o agente não adicionou a tag e ficou em silêncio: a conversa tinha assinatura
`retention` das últimas 24h (distribuição anterior), e o **dedup de 24h** no fluxo principal
(linha ~13692) suprimia tudo *antes* de chamar `trigger_retention`. Além disso, mesmo sem dedup, o
call site enviaria mensagem ao aluno (não seria silencioso). Solução: desvio do telefone de teste
**no topo de cada ponto de detecção** (fluxo principal, in-hours-rescue, queue-sweep) chamando
`_trigger_retention_tag_only` direto e dando `return`/`continue` — ignora dedup/after-hours,
não distribui e não fala com o aluno. A trava dentro de `trigger_retention` segue como defesa para
os demais call sites.

---

### [2026-06-23] - REVERTIDO: retenção volta a ser DISTRIBUÍDA (removida a regra automação/tag RET-IA)

**Decisão**
Revertido o `agente_ao_vivo_v4.py` para o estado anterior à regra de automação/tag (commit
`e6ae0ca`). O agente **volta a distribuir** retenção para o time (Wesley/Danúbia, rodízio + sticky
por disponibilidade), com nota interna, transferência de chat e mensagem ao aluno — exatamente como
funcionava antes de 22/06.

Ficam **desfeitas** (no código) as duas mudanças abaixo desta entrada:
- "Retenção deixa de ser distribuída… (tag RET-IA)" (commit `0ad02f3`);
- "Detecção negation-aware + sticky consistente (caso Caio)" (commit `45fd901`).

**Contexto**
Decisão do gestor: voltar ao modelo de distribuição direta de retenção. A automação "Retenção IA"
disparada por tag não ficou confiável de validar (a API do DataCrazy não expõe execução/erros das
automações) e optou-se por retomar o fluxo conhecido. As entradas [2026-06-22] e a de
negation-aware/sticky abaixo ficam **apenas como registro histórico** do que foi testado.

**Impacto**
Próximo passo combinado: revisar a fila de "Não iniciados" e redistribuir corretamente (lead + chat)
para os consultores de retenção que estiverem ativos.

---

### [2026-06-23] - Detecção de retenção fica negation-aware + sticky consistente (caso Caio)

**Decisão**
1. **(B) Negação na detecção** — `is_retention_intent` agora ignora a keyword quando há negação
   próxima antes dela (`não/nao/nunca/jamais`, janela ~5 palavras), via helper
   `_kw_present_unnegated`. Ex.: "o estudante **não** tentou trancar", "**não** vou cancelar" deixam
   de ser retenção.
2. **(A) Sticky consistente** — os caminhos `in-hours-rescue` e `queue-sweep` deixaram de usar
   `_is_in_retention` (que varria o histórico por palavra-chave) e passam a usar o novo helper
   `_retention_still_active(conv_id, msg_atual)`: só re-aciona retenção se houver **handoff de
   retenção ativo** OU a **mensagem atual** tiver intenção real de cancelar/trancar. Mesma regra já
   aplicada no `post-close-rescue` em 22/06.

**Contexto**
Após a virada da retenção para automação, um aluno (Caio) enviou só "Sim" e foi marcado como
retenção: o `_is_in_retention` varria o histórico e batia em "não tentou trancar a matrícula"
(match ingênuo de substring, sem entender negação) num caminho sticky que ainda não tinha a
re-avaliação. Gerava falso positivo e acionava RET-IA indevidamente.

**Alternativas descartadas**
- Só corrigir negação (B) sem unificar o sticky (A): manteria os caminhos divergentes e o risco de
  outro falso positivo por palavra antiga no histórico (ex.: "Sim" após menção antiga a "trancar").
- IA/LLM para classificar negação: custo/latência desnecessários para um caso resolvível com regra.

**Impacto**
`_is_in_retention` permanece só no guard de redistribuição (11102, sem `msgs`, checa handoff).
Detecção de retenção mais precisa em todos os pontos; menos acionamentos indevidos da automação.

---

### [2026-06-22] - Retenção deixa de ser distribuída: agente só aciona a automação "Retenção IA" (tag RET-IA)

**Decisão**
Quando o agente detecta retenção (mesmas regras de `is_retention_intent`), ele **não distribui
mais** para Wesley/Danúbia, **não transfere o chat**, **não muda etapa do negócio** e **não envia
nenhuma mensagem ao aluno**. Ele apenas:
1. adiciona a tag **`RET-IA`** (`18a49003-449b-473f-964f-1e0d2935b8e0`) no lead — que dispara a
   automação **"Retenção IA"** no DataCrazy (gatilho "tag adicionada");
2. registra a **nota interna** (para o consultor entender o que o aluno disse);
3. silencia o bot na conversa (`_mark_handoff_active('retention')` + flags) — a automação assume.

`trigger_retention` foi reescrita para esse comportamento e é **idempotente** (se o lead já tem a
tag RET-IA, não re-aplica nem re-posta a nota). Não registra mais no feedback (tema RETENÇÃO).
A mudança vale **dentro e fora do horário** (removido o ramo after-hours de mensagem).

Call sites ajustados (todos deixaram de enviar mensagem ao aluno): fluxo principal, in-hours-rescue,
queue-sweep, post-close-rescue e low-conf-D4. Os dois pontos "sticky" (in-hours e queue-sweep)
continuam chamando `trigger_retention`, agora idempotente.

**Contexto**
Pedido do gestor: a retenção passa a ser conduzida pela automação "Retenção IA" (retenção
executada por IA) no DataCrazy, em vez de distribuir para um consultor humano. O agente só precisa
"acender o gatilho" (a tag) e sair de cena, mantendo a nota interna como contexto. A tag RET-IA já
existia no CRM (criada 2026-06-17, descrição "retenção executada por IA").

**Alternativas descartadas**
- *Executar a automação via API do DataCrazy*: não há endpoint conhecido/confiável no projeto para
  rodar automação manual; a automação estava com gatilho "execução manual". Optou-se por mudar o
  gatilho para "tag adicionada" e o agente apenas adiciona a tag (mecanismo que o agente já domina).
- *Manter registro no feedback (tema RETENÇÃO)*: descartado a pedido do gestor (só nota interna).
- *Manter mensagem/distribuição fora do horário*: descartado — comportamento uniforme (sempre só
  aciona a automação), para "nunca errar".

**Impacto**
Retenção fica 100% a cargo da automação "Retenção IA"; o agente não ocupa consultor nem fala com o
aluno nesses casos. Constantes antigas (`RETENTION_MSG`, `RETENTION_AFTER_HOURS_MSG`,
`RETENTION_WESLEY_CRM_ID`) permanecem definidas mas deixam de ser usadas no caminho de retenção.
Pré-requisito operacional: a automação "Retenção IA" precisa estar com o gatilho configurado para a
tag **RET-IA** e **ligada**. Mudança restrita a `agente_ao_vivo_v4.py`.

---

### [2026-06-22] - Re-avaliação de retenção no retorno pós-encerramento (sticky deixa de ser cego)

**Decisão**
No fluxo de **resgate pós-encerramento** (`process_queue_fast_sweep` / POST-CLOSE-RESCUE),
o sticky de retenção deixa de re-mandar o aluno para a Retenção (Wesley/Danúbia) só porque
houve algum evento de retenção nos últimos 7 dias. Agora a conversa **só é mantida em
retenção** se uma de duas condições for verdadeira:
- **(a)** existe handoff de retenção **ATIVO agora** (`_is_handoff_active(cid)` com
  `motivo == 'retention'` ou `target` contendo `wesley`) — aluno no meio da conversa com a Retenção; ou
- **(b)** a **mensagem atual** do aluno tem intenção de cancelar/trancar
  (`is_retention_intent(user_text)`).

Se nenhuma vale (ex.: retorno só com "Olá" após retenção antiga), o caso cai no **fluxo
normal de atendimento** já existente: volta para o consultor anterior se ele estiver ativo
(`is_attendant_active_now`), senão redistribui para um consultor ativo
(`get_available_consultant`). A nota interna agora registra o motivo (`handoff de retencao
ativo` ou `intencao de cancelamento na msg atual`).

**Contexto**
Caso reportado (Bruna Grazielle, lead #178521): a aluna voltou dizendo apenas "Olá" após o
encerramento e foi transferida automaticamente para Wesley (Retenção) com a nota "manifestou
intenção de cancelamento/trancamento". A causa: `_is_in_retention` retornava `True` por
evidência de retenção dentro da janela de `_RETENTION_RECENT_HOURS = 168` (7 dias), e o
sticky disparava `trigger_retention` independente do conteúdo da mensagem. A verificação de
consultor ativo já existia para o caminho de atendimento; o furo era só a retenção não
re-avaliar "ainda é retenção ou virou atendimento?".

**Alternativas descartadas**
- *LLM classificando todo retorno (ou só o caso ambíguo)*: mais flexível, mas adiciona
  custo/latência num sweep de background e risco de erro num ponto crítico de roteamento.
  O pipeline normal já classifica intenção, então a abordagem determinística entrega o
  objetivo sem o LLM. (Opção A escolhida pelo gestor.)
- *Reduzir `_RETENTION_RECENT_HOURS`*: trataria sintoma (encurtar janela) sem resolver a
  causa (sticky cego). Descartado.
- *Aplicar a mudança também no sticky de "retenção em andamento" (conversa aberta)*:
  descartado a pedido do gestor — escopo restrito a pós-encerramento para **não** correr o
  risco de tirar/redistribuir aluno no meio de um atendimento em andamento.

**Impacto**
Aluno que volta após encerramento sem intenção real de cancelar não é mais jogado na
Retenção por engano; retenção genuína em andamento (handoff ativo) ou novo pedido de
cancelamento continuam indo certo. Mudança restrita a um único call site
(`agente_ao_vivo_v4.py`, bloco POST-CLOSE-RESCUE ~L8189). `_is_in_retention` permanece
inalterada e segue valendo nos demais pontos (não tocados).

---

### [2026-06-18] - Tabulação de disparo pela fonte da verdade (banco `disparos` / activation_*)

**Decisão**
O tipo do retorno de disparo passa a ser determinado lendo o **banco oficial do
disparador** (`disparos`, tabela `activation_dispatch_events`), em vez de adivinhar
pelo texto do template. Esse banco é alimentado pelo disparador externo
(`banco-dcz-crm-sync` / app `disparador_whatsapp`), está no **mesmo servidor
Postgres** que o agente já usa (mesmo host/usuário, só muda o `dbname`) e contém
`datacrazy_lead_id`, `telefone`, `rgm`, `category` e `template_name` de cada envio.

Implementação (`agente_ao_vivo_v4.py`):
- `_lookup_dispatch(phone, lead_id, rgm, max_days=30)` — **somente leitura**; busca o
  disparo mais recente do contato (status `sent`, janela de 30 dias). Prioridade de
  chave: `datacrazy_lead_id` > telefone (sufixo de dígitos, normaliza `55`) > `rgm`.
  Cache em memória de 10 min (hit e miss) + `statement_timeout` 8s p/ não travar o loop.
- `_dispatch_label(category, template_name)` — mapeia para o rótulo do dashboard.
  `financeiro`→`INADIMPLÊNCIA`; `docs-pendentes`→`DOCS PENDENTES`; `processos-caa`→
  `CANCELAMENTO` (template com `cancel`) ou `CAA`; categoria nova→`DISPARO · <CATEGORIA>`.
  (2026-06-18) `ATIVAÇÃO` removida a pedido — `financeiro` agora é sempre `INADIMPLÊNCIA`.
- `_dispatch_tema` nova ordem: 1) `DISPATCH_LABEL` (override manual) → 2) `_lookup_dispatch`
  (fonte da verdade) → 3) fallback `_classify_dispatch_type` (heurística antiga).
- Call site em `process_queue_fast_sweep`: só consulta o banco se a conversa **ainda não
  foi logada** e a mensagem é **substantiva** (evita query repetida por saudação).

**Contexto**
Os disparos saem por um sistema externo e **não aparecem no DataCrazy**, então a
detecção por texto do template era pouco confiável (≈93% caía em "GERAL" no
retroativo). O gestor pediu tabular por tipo de disparo de forma confiável. Investigando
o servidor, achei o banco `disparos` com as tabelas `activation_*` vivas (último evento
17/06, respostas até 18/06) — a fonte oficial. O `lead_id` que o agente já guarda casa
direto com `datacrazy_lead_id`. Dry-run (somente leitura) em 400 retornos recentes: 50%
casaram (limitado pelo histórico anterior a 08/06); no fluxo ao vivo o acerto é maior
porque o `lead_id` está disponível direto no profile.

**Alternativas descartadas**
- Fingerprint do template observando a mensagem OUT no DCZ: inviável — o disparo não
  aparece no DCZ.
- Classificação retroativa por conteúdo dos ~3950 `DISPARO` legados: descartada antes
  por baixa confiabilidade; mantidos como legado.
- Acessar o painel web por login: desnecessário e frágil — a fonte está no banco.

**Impacto**
- Retornos de disparo novos recebem rótulo preciso (`DISPARO · INADIMPLÊNCIA`,
  `· ATIVAÇÃO`, `· DOCS PENDENTES`, `· CANCELAMENTO`, etc.) direto do dado oficial.
- Nenhuma dependência/pasta nova; conexão read-only ao `disparos` (padrão do `dcz_sync`).
- Sem regressão: se não houver match, mantém o comportamento antigo (fallback).
- Não toca no fluxo de `RETENÇÃO` (rota de cancelamento continua via `_log_retention_interaction`).

---

### [2026-06-18] - Aba "Disparador" no cockpit (kb_api + kb_admin)

**Decisão**
Nova aba **Disparador** no cockpit, lendo o banco `disparos` (read-only, mesmo
servidor). Mostra: cards (disparado / responderam / taxa de retorno / nº de
templates), gráfico de barras enviados×responderam por template, tabela de
templates do período (com taxa, clicável p/ filtrar) e tabela paginada de pessoas
disparadas (nome, telefone, RGM, **CPF**, template, tipo, data, **respondeu?**).

Implementação:
- `kb_api.py`: `get_other_db(dbname)` (ctx manager read-only p/ outros bancos do
  mesmo servidor) + `_norm_phone_tail` + `_disp_label` (espelha o do agente) +
  `_cpf_by_rgm` (lote RGM→`mm_matriculados` no `dcz_sync`). Endpoints
  `GET /api/dispatch/summary` (cache 120s) e `GET /api/dispatch/contacts`
  (paginado, filtros template/categoria/respondeu/busca). Atribuição de resposta→
  template pela chave **(category, template)** do disparo mais recente ≤ `received_at`.
- `kb_admin.html`: tab `disparador` (Chart.js já presente), funções `initDisparador`,
  `loadDispSummary`/`dispRenderChart`, `loadDispContacts`, filtros por template.

**Contexto**
O gestor pediu uma visão completa dos disparos (quem recebeu, template, quem
respondeu, gráfico de retorno). Limitações dos dados, decididas com o gestor:
- **Texto do template não fica no banco** (templates aprovados ficam na API da
  Meta; o disparador só guarda o `template_name`). Decisão: exibir só o **nome**
  do template por enquanto. Evolução possível: integrar com a API da Meta
  (`/{WABA_ID}/message_templates`) usando WABA ID + token do app disparador.
- **CPF** não está no disparo → vem por cruzamento RGM→base acadêmica (melhor esforço).
- **Conteúdo da resposta** do aluno não fica no banco de disparos (só quem/quando).

**Alternativas descartadas**
- Cadastro manual do texto do template: deixado como opção; gestor preferiu só o nome.
- Guardar credenciais Meta no cockpit agora: adiado (precisa do token do easypanel).

**Impacto**
- Validado contra dados reais (summary ~0.3s, contacts ~0.1s): ex. `caa_cancelamento`
  77,8% de retorno; inadimplência ~1,5–2%. Sem escrita em nenhum banco.
- Requer rebuild/restart do cockpit para aparecer.

---

### [2026-06-17] - Feedback: tema RETENÇÃO, filtro de saudação e captura da resposta da retenção

**Decisão**
Três ajustes no painel de feedback (`interaction_summary`):

1. **Tema `RETENÇÃO`** — toda conversa encaminhada ao time de Retenção
   (Wesley/Danúbia) passa a ser registrada com `tema='RETENÇÃO'`, **substituindo**
   uma linha `DISPARO` da mesma conversa (quando o retorno de disparo virou
   retenção). Dá ao gestor um filtro próprio para o que vai à retenção.
2. **Filtro de saudação/filler no DISPARO** — `_log_dispatch_reply_once` só
   registra o retorno quando há **pergunta/conteúdo de fato**. Saudação pura
   ("Olá/boa tarde") ou filler ("ok/tudo bem/.") **não** são gravados como
   pergunta e não marcam o dedup (espera o aluno mandar algo real depois).
3. **Coluna RESPOSTA da retenção** — `_capture_retention_responses` (roda a cada
   10 ciclos) busca a **1ª resposta humana substantiva** do time de Retenção e
   preenche a coluna RESPOSTA da linha `RETENÇÃO`, **pulando saudação/abertura
   padrão** do atendente (`_is_retention_opening`).

Implementação:
- Novos helpers: `_is_substantive_student_msg` (+ `_DISPATCH_FILLER_PHRASES`),
  `_log_retention_interaction` (+ `_RETENCAO_LOGGED_CONVS`), `_is_retention_opening`
  (+ `_RETENTION_OPENING_MARKERS`), `_capture_retention_responses`.
- `trigger_retention` (chokepoint único de toda rota de retenção) chama
  `_log_retention_interaction` antes do `return alvo` → cobre todos os call sites
  (handle_message, queue_sweep, rescues) de forma sistêmica.
- Dropdown de TEMA do dashboard (`kb_api.py`) é dinâmico (`by_tema`), então
  `RETENÇÃO` aparece sozinho quando houver linhas — sem mudança no `kb_api`.

**Contexto**
Disparo de cancelamento jogava todos os respondentes para a retenção, mas tudo
ficava misturado em `DISPARO`; e saudações ("Olá/boa tarde/Ok") entravam como
"pergunta" no feedback. O gestor pediu separar a retenção e ver o que a retenção
respondeu ao 1º questionamento.

**Alternativas descartadas**
- Marcar RETENÇÃO **além** de DISPARO (ficar nos dois filtros): descartado — o
  gestor escolheu substituir para separação limpa.
- Registrar a saudação e só esconder no front: descartado — decisão foi não
  registrar nada até haver pergunta real.

**Impacto**
Feedback fica separado por RETENÇÃO; coluna PERGUNTA só traz questionamento real;
coluna RESPOSTA mostra o que a retenção de fato respondeu. Custo: 1 chamada
`identify_student` por novo caso de retenção (baixo volume) e uma varredura leve
de mensagens a cada 10 ciclos só para linhas RETENÇÃO recentes sem resposta.

---

### [2026-06-17] - Feedback: tema DISPARO separado por TIPO de campanha

**Decisão**
O tema genérico `DISPARO` foi substituído por `DISPARO · <TIPO>`, classificado
automaticamente pelo **conteúdo do template** que o aluno respondeu:
`DISPARO · INADIMPLÊNCIA`, `DISPARO · CANCELAMENTO`, `DISPARO · REMATRÍCULA` ou
`DISPARO · GERAL` (fallback/ambíguo). Mantém o prefixo `DISPARO · ` para agrupar.

Implementação:
- `_DISPATCH_TYPE_KEYWORDS`: palavras-âncora por tipo. `_classify_dispatch_type`
  pontua o **texto do template OUT** (padronizado/previsível, não a resposta do
  aluno); vence o tipo com mais ocorrências; **empate → GERAL** (não chuta).
- `_extract_dispatch_template` recupera o corpo do template (msg OUT dispatch-like
  anterior à resposta), espelhando `_is_dispatch_reply` — **sem chamada extra de API**.
- `_dispatch_tema`: aplica override manual `DISPATCH_LABEL` (env) quando setado
  (`DISPARO · <LABEL>`), senão usa a classificação automática.
- `_log_dispatch_reply_once(..., tema=...)`: grava o tema dinâmico; dedup por
  conv_id agora usa `tema LIKE 'DISPARO%'` (cobre todos os subtipos).
- Dropdown de TEMA do dashboard é dinâmico → os subtipos aparecem sozinhos.

**Contexto**
`DISPARO` único misturava campanhas (inadimplência, cancelamento, rematrícula).
O gestor pediu separar por disparo, com preocupação explícita de não classificar
errado.

**Alternativas descartadas**
- Rótulo manual como ÚNICA fonte: descartado — não há passo de config por disparo
  (DISPATCH_REF_DATE é env fixa), exigiria redeploy a cada campanha. Mantido só
  como override opcional.
- Classificar pela resposta do aluno: descartado — resposta é imprevisível; o
  template é padronizado e confiável.

**Impacto**
Cada campanha de disparo fica isolada no feedback (com o filtro de data separando
a rodada). Risco de erro mitigado por âncoras específicas + empate→GERAL + override
manual. Para novos tipos, basta adicionar entradas em `_DISPATCH_TYPE_KEYWORDS`.

---

### [2026-06-09] - Redistribui conversa presa com humano INATIVO ao chegar mensagem nova

**Decisão**
`distribute_to_attendant` deixou de **manter** uma conversa presa com um atendente
que está **Inativo no dashboard** (ex.: Felipe/Débora de folga). Quando chega uma
mensagem nova e a conversa cai numa rota de distribuição, se o humano atual está
Inativo no painel **e a conversa NÃO está em retenção**, o agente **redistribui
para um consultor ATIVO**. Como a distribuição normal força consistência
(`_enforce_assignment_consistency`), isso também corrige o caso de **chat com um
atendente e lead com outro** (ex.: chat Débora / lead Danúbia).

Implementação:
- Novo `_attendant_is_dashboard_inactive(att_name)`: consulta o Supabase e retorna
  True só se o atendente está com `ativo_inativo != 'Ativo'`. Retorna **False**
  (mantém) para: membros do time de Retenção (Wesley/Danúbia — Inativo de
  propósito), atendentes Ativos, nome vazio, ou nome **fora da tabela**
  (conservador: não mexe em desconhecido/supervisor).
- Em `_distribute_to_attendant_locked`, a proteção "já tem humano" (`_dcz_conv_has_human`)
  agora só aborta se o humano está **ativo/retenção**; se está Inativo, libera a
  redistribuição (limpando o handoff antigo p/ os locks não travarem).
- A idempotência externa de `dispatch` também libera quando o alvo está Inativo.

**Contexto**
Felipe e Débora ficam Inativo no painel, mas conversas antigas atribuídas a eles
seguiam presas; o aluno mandava mensagem nova e continuava sem atendimento real.
Regra do usuário: redistribuir **apenas** quem manda mensagem agora — NÃO fazer
varredura em massa de conversas já em filas/atendimento.

**Alternativas descartadas**
- *Varredura em massa redistribuindo todos os presos com inativos*: já causou
  problema antes (mover dezenas de conversas em andamento). Mantém-se o gatilho
  por **mensagem nova do aluno**.
- *Usar `is_attendant_active_now` (que inclui almoço/expediente)*: descartado para
  não roubar conversas de quem está só em pausa de almoço; usa-se o flag **hard**
  `ativo_inativo`.

**Impacto**
Aluno que volta a falar e estava preso com inativo passa a ser atendido por
consultor ativo, com chat e lead consistentes. Retenção (Wesley/Danúbia) nunca é
redistribuída por esse mecanismo.

---

### [2026-06-09] - CAUSA RAIZ do mismatch chat/lead: business exige `attendantId`

**Decisão / correção**
O endpoint de **negócio** (`PATCH /businesses/{id}`) **ignora silenciosamente**
`{'attendant': {'id': ...}}` (responde 200 mas NÃO troca o atendente). Ele exige
**`{'attendantId': <crm_id>}`** (string). O endpoint de **lead** aceita o objeto
`attendant` normalmente — por isso o lead trocava e o negócio não, gerando o
mismatch reportado (chat/lead com uma pessoa, negócio com outra — ex.: lead
Danúbia / chat Débora).

Corrigido em TODOS os pontos que setam atendente de negócio:
- `_dcz_transfer_business` (PATCH principal e POST de criação);
- `_enforce_assignment_consistency` (retry de business);
- auto-fix de auditoria;
- `trigger_retention` (negócio de retenção).
Patches de **lead** seguem com `{'attendant': {'id': ...}}` (correto p/ lead).

Observação: a verificação de conversa (chat) deve ler o campo **`attendants`**
(lista) do objeto de conversa do messaging — não existe `attendant` singular ali.

**Contexto**
Investigação dos casos Ana Julia (preso com Felipe inativo) e Alciene (chat
Débora / negócio Danúbia). Após o fix, ambos ficaram 100% consistentes
(chat+lead+negócio no mesmo consultor ativo) — corrigidos manualmente também.

**Impacto**
A reatribuição de negócio passa a funcionar de fato; some o mismatch crônico
chat/lead/negócio em distribuição, retenção e resgates.

---

### [2026-06-08] - Retenção distribuída ao time (Wesley + Danúbia), SEM dashboard

**Decisão**
A retenção deixou de ser fixa no **Wesley** e passa a ser distribuída para um
**time de Retenção** (`RETENTION_TEAM = ['Wesley', 'Danubia']`), atribuindo
**atendente + lead + negócio + chat**, igual era para o Wesley.

A retenção **NÃO consulta o dashboard de Ativo/Inativo**. Motivo (confirmado pelo
usuário): Wesley e Danúbia ficam de propósito como **"Inativo"** no painel para
**não receberem lead de atendimento normal** — mas devem continuar recebendo
retenção sempre. Ou seja, igual ao Wesley funcionava antes: se a mensagem do
aluno é caso de retenção, distribui direto para um dos dois.

Escolha do membro (`choose_retention_target`):
1. **STICKY**: se a conversa já está com um membro (via `handoff_active`), mantém.
2. Senão, **rodízio determinístico por conversa** (`hash(conv_id) % 2`), que
   divide ~50/50 entre os dois e, por ser determinístico, já é naturalmente
   sticky mesmo se o handoff expirar. **Sempre** retorna um nome (nunca fica sem
   dono).

Implementação:
- `_retention_sticky_target()` / `choose_retention_target(conv_id)`: sticky + rodízio.
- `trigger_retention(..., target_name=None)`: escolhe o alvo, atribui lead/negócio
  (CRM IDs por `CRM_ATTENDANT_MAP`), transfere o chat, marca o `handoff_active`
  (sticky) e **retorna o nome**.
- Todos os call sites (`handle_message` principal e LOW-CONF-D4, `in_hours_rescue`,
  `queue_sweep`, `post_close_rescue`) usam o alvo dinâmico; removidos os
  `_mark_handoff_active(target='Wesley')` hardcoded (que travavam o sticky no Wesley).
- Mensagens ao aluno tornaram-se **genéricas** ("nosso *time de Retenção*"),
  sem citar nome fixo; a apresentação interna usa o nome real do membro escolhido.
- Comportamento **fora do horário** mantido como era com o Wesley: apenas informa
  (mensagem after-hours) e enfileira — não distribui na hora.

**Contexto**
Danúbia passou a integrar o departamento de retenção; a regra é a mesma para
ambos. Ambos ficam Inativo no painel por design (bloqueia atendimento normal),
então a retenção precisa ignorar esse status.

**Alternativas descartadas**
- *Respeitar Ativo/Inativo do dashboard*: descartada porque os dois ficam Inativo
  de propósito — gatear por isso travaria a retenção.
- *Round-robin por contador em memória*: reseta no restart; o rodízio por hash do
  `conv_id` é stateless, balanceado e sticky por natureza.

**Impacto**
Retenção divide carga ~50/50 entre Wesley e Danúbia, sempre distribui (não depende
do painel) e mantém a mesma conversa com o mesmo consultor (sticky).

---

### [2026-06-03] - Início das aulas resolvido pela turma real do aluno (data_matricula + calendário)

**Decisão**
Removido o curto-circuito que respondia **"agosto" fixo** para qualquer pergunta
sobre início das aulas. Agora `handle_inicio_aulas_intent` resolve a **turma de
ingresso de cada aluno** a partir da `data_matricula` (tabela `mm_matriculados`,
banco `dcz_sync`, atualizada diariamente) cruzada com as **janelas de matrícula
do Calendário Acadêmico Graduação EAD 2026**, e responde com a **data oficial de
início das aulas** daquela turma. Quando não dá para determinar com certeza
(Pós, aluno fora da base, `data_matricula` fora das janelas conhecidas), o agente
**transfere para consultor** — nunca inventa.

Implementação:
- `_TURMAS_INGRESSO_2026`: janelas sequenciais sem sobreposição (janela → turma →
  início das aulas), fonte = PDF oficial do calendário.
- `resolve_turma_ingresso(data_matricula)`: mapeia a data na janela e devolve
  turma + data de início (ou None → transferir).
- `data_matricula` adicionada ao `_ACAD_COLS`/`_ACAD_KEYS` em `fetch_academic_data`.
- Regra 12 do `SYSTEM_PROMPT` reescrita: início das aulas depende da turma de
  cada aluno; o LLM nunca cita mês fixo nem inventa; sem dado → transfere.

**Contexto**
Caso real (Ivanice): matriculou-se em abril → turma de **Maio** (aulas 04/05),
mas a regra fixa respondeu "agosto", prejudicando os estudos da aluna. O agente
JÁ possuía o calendário (`academic_calendar_2026`, com as datas de "Início das
aulas do mês de X") e a `data_matricula`, mas o curto-circuito anti-alucinação
cravava "agosto" e nunca consultava esses dados. Regra de turma confirmada pelo
usuário: a turma é a janela de matrícula em que a `data_matricula` se encaixa
(ex.: matrícula 15/08 → Agosto/03/08; 17/08 → Setembro/01/09).

**Alternativas descartadas**
- Manter resposta fixa "agosto": causou o dano relatado; errada para 2026/1.
- Deixar o LLM responder com o calendário injetado: o LLM não sabe a turma do
  aluno sem a lógica de janelas; risco de chute. Optou-se por resposta canônica
  determinística antes do LLM.
- Inventar contato/data para Pós: proibido. Pós não tem dado (tabela só tem
  `tipo='grad'`) → sempre consultor.

**Impacto**
- Graduação encontrada na base: data de início correta por turma (inclui calouros
  mensais e veteranos). Fronteiras de janela testadas (12/04→Abril, 13/04→Maio).
- Pós / fora da base / data fora das janelas → transferência para consultor, sem
  inventar. Vale após rebuild.

---

### [2026-06-03] - Semestre/turma atual respondido pelos dados do aluno

**Decisão**
Adicionado handler canônico `handle_semestre_intent` (gatilho `detect_semestre_intent`)
que responde, **quando o aluno PERGUNTA**, o semestre atual (`serie` da
`mm_matriculados`) e, para calouro (nova matrícula), a turma de ingresso. Múltiplos
cursos → lista o semestre de cada um. Pós / fora da base / sem `serie` → transfere
para consultor (nunca inventa). Função de transferência generalizada em
`_transfer_acad_question_to_consultant` (reutilizada por início-aulas e semestre).

**Contexto**
O `serie` já era injetado no contexto do LLM, mas a regra de privacidade do prompt
("NUNCA diga 'você está no Xº semestre'") tornava a resposta a uma pergunta direta
inconsistente. Mesmo princípio do início das aulas: resposta determinística da base
quando perguntado, transferência quando não há dado.

**Alternativas descartadas**
- Deixar só com o LLM: comportamento inconsistente (desvia ou arrisca).
- Revelar dados proativamente: mantido proibido; só responde quando o aluno pergunta.

**Impacto**
- Pergunta direta sobre semestre/turma respondida com dado real; sem dado → consultor.
  Vale após rebuild.

---

### [2026-06-03] - Anti-alucinação de contato da coordenação + entrada oficial na KB

**Decisão**
Adicionada a **regra crítica nº 16** no `SYSTEM_PROMPT` proibindo o agente de
inventar qualquer e-mail/telefone/ramal/WhatsApp de coordenação, secretaria,
polo ou financeiro. O canal oficial de contato com a coordenação é o
**Blackboard → Organizações**; se o aluno não encontrar, transferir para
consultor. Também foram cadastradas 6 entradas (variações de pergunta) na
`knowledge_base` com tema `COORDENACAO` apontando para esse caminho.

**Contexto**
Print do usuário mostrou o agente respondendo *"geralmente o e-mail é algo como
coordenacao@cruzeirodosul.edu.br"* e *"o telefone da coordenação é (11)
2797-2000"*. Investigação confirmou que NENHUM desses dados existe no código nem
nas 9.847 entradas da KB — eram alucinação do gpt-4o-mini preenchendo uma lacuna
(não havia regra anti-invenção específica para contatos institucionais, só para
URLs/datas/endereço de polo). Pior: contradizia entradas reais da KB.

**Alternativas descartadas**
- Só corrigir o prompt sem cadastrar na KB: o RAG ainda não traria resposta
  confiável e o LLM poderia chutar de novo.
- Cadastrar e-mail/telefone público: não existe canal direto; contato é só pela
  plataforma.

**Impacto**
- Após rebuild, perguntas sobre contato da coordenação caem na entrada oficial
  (RAG top score ~0.83–0.86) e o agente orienta Blackboard → Organizações,
  transferindo para consultor em caso de dificuldade. Nunca mais inventa
  e-mail/telefone "provável".

---

### [2026-06-01] - Ativo/inativo de consultor 100% pelo dashboard (Supabase)

**Decisão**
`_ATTENDANTS_ON_VACATION` esvaziado (`set()`). O controle de quem recebe
leads passa a ser exclusivamente o campo `ativo_inativo` da tabela de
distribuição no Supabase (o dashboard do Cockpit). O set permanece no
código apenas como override manual de emergência (deve ficar vazio).

**Contexto**
A lista fixa duplicava o controle do painel e causava confusão recorrente:
consultores marcados "Ativo" no dashboard (ex: Felipe) continuavam
bloqueados no código e não recebiam leads, enquanto o usuário não entendia
por quê. `get_available_consultant` e `is_attendant_active_now` já
consultavam `ativo_inativo=eq.Ativo`, então a lista fixa era redundante.

**Alternativas descartadas**
- Manter a lista e editá-la a cada mudança: fonte contínua de erro humano e
  dessincronia com o painel.

**Impacto**
- Quem o painel marca como Inativo não recebe leads (Joyce continua fora por
  estar Inativa no Supabase, não mais por lista fixa).
- Para bloquear alguém imediatamente sem mexer no painel, adicionar o
  primeiro nome (lowercase) ao set e rebuildar.
- Requer rebuild para entrar em vigor.

---

### [2026-05-26] - Fix: cegueira a `unstarted`/`opened` + follow-up bot DCZ + guarda D6

**Decisão**

3 mudanças sistêmicas no `agente_ao_vivo_v4.py`:

1. **Helper `_fetch_active_conversations()`** — busca `open` + `unstarted` +
   `opened` em 3 GETs paralelos ao DCZ e funde sem duplicar. Substitui os 4
   sites que faziam `GET /messaging/conversations?status=open` puro:
   - main loop (linha ~12577)
   - `process_in_hours_rescue`
   - `process_after_hours_rescue`
   - `process_post_close_rescue`

2. **Constante global `_FU_TRIGGER_PHRASES`** — frases que disparam o
   monitoramento de inatividade (follow-up + encerramento). Inclui:
   - Frases do agente IA (já existentes): "tudo certo por aí", "ainda está",
     "não tive retorno", "pode mandar", "precisar de mais alguma".
   - **Novas: frases do salesbot/automação DCZ**: "Veja as opções
     disponíveis", "Clique em uma das opções", "Escolha uma opção", "Qual
     plataforma você está", "Seu e-mail de acesso", "Veja o tutorial",
     "Selecione para dar andamento", "Me conta, por favor", "Já um de
     nossos consultores", "Como posso te ajudar".
   - Substituída em 3 locais (PRIO-1 close, follow-up tracker, `_is_fu`
     helper). Antes esses 3 locais tinham listas inline duplicadas.

3. **Camada D6 em `send_and_track`** — bloqueia envio (não-force) se
   `_dcz_conv_has_human(conv_id)` retorna True. Complementa D1 (humano
   FALOU nas últimas 6h): D6 cobre o intervalo entre **atribuição** e
   **primeira fala** do humano (caso Debora: atendente atribuída por 208min
   sem responder, e o sistema ainda mandava follow-up/notas).

**Contexto**

Caso reportado em 2026-05-26 (~17:00 BRT): após disparo em massa, 10
alunos ficaram 2h em conversas `status=unstarted` totalmente invisíveis
ao agente. Diagnóstico via `_find_v4.py` (busca via CRM `/leads?search=`)
encontrou todas — confirmou que o GET `?status=open` não retorna as em
`unstarted` (apenas com status exatamente `open`). Resgate manual via
`_rescue_image.py` processou as 10 (Caio, Jean, Daniela, Demison,
Fernanda, Karem, Larissa, Erick, Gabriela, Beatriz).

Adicionalmente, o usuário reportou que conversas com bot DCZ falando por
último (e.g. menus "Veja as opções disponíveis") não eram encerradas
mesmo sem retorno do aluno. Diagnóstico via `_check_emitter.py` revelou
que as mensagens visualmente atribuídas ao agente IA eram, na verdade,
do salesbot interno do DCZ (status da conv inclui `automation`). A
infraestrutura de follow-up já cobria conversas com bot por último
(`_fu_candidates` em `convs_opened`), mas as frases-gatilho não incluíam
as do bot DCZ — daí o monitoramento nunca entrava em estágio 1/2.

A nota "*Aluno esperando ha 208min — Debora ainda nao respondeu*"
(imagem usuário) **não está no código do projeto** (verificado via grep
exaustivo). Provavelmente vem de outro processo no servidor DCZ ou
config do produto. D6 protege nosso envio agente contra cenário análogo
ainda que a fonte seja externa: nenhum envio sem `force=True` ocorre
enquanto humano está atribuído.

**Alternativas descartadas**

- *Paginação do GET (`offset=N`)*: o DCZ retorna o mesmo lote
  independente do offset — paginação está quebrada do lado deles.
  Triplicar a chamada por status é o workaround viável.
- *Detectar "status automation" e ignorar conversa*: rejeitada pelo
  usuário ("devem agir juntos") — agente IA deve operar follow-up das
  mensagens do bot DCZ normalmente.
- *Wrapper `send_internal_note(conv_id, body)` centralizado*: alto risco
  porque há ~20 sites de chamada direta `requests.post(... isInternal=True)`.
  Adiada — D6 cobre o caso principal (envio ao aluno) sem refatoração
  ampla.

**Impacto**

- 3 chamadas ao DCZ por ciclo no main loop (antes 1). Ciclo de ~10s, ainda
  margem para timeout. Cada GET tem `limit=300`, totalizando até 900
  conversas/ciclo (vs 300 antes).
- Follow-up agora dispara em qualquer mensagem do bot DCZ que contenha
  frases típicas de menu — encerramento de conv ociosa pós-template ou
  pós-menu funciona uniformemente.
- D6 pode ocasionalmente suprimir uma resposta válida do agente em conv
  recém-distribuída para humano que ainda não falou. Mitigação: mensagens
  críticas (transferência, after-hours, distribuição) já usam
  `force=True` e escapam de todas as guardas.

---

### [2026-05-25] - Fix: agente expulsava consultor de retenção (Camadas A+B+C)

**Decisão**

3 camadas defensivas para impedir que o supervisor IA / fila noturna
substituam o atendente humano que já está cuidando da conversa
(caso reportado: Alessandra Prado Franco — Wesley em retenção → Julia →
Camila).

1. **Camada A** (`process_openai_supervisor_loop`, path `tem_humano=True`):
   - Removido `record_pending_escalation(reason='supervisor_block_with_human',
     tier='priority')`.
   - Removido `_mark_handoff_active('supervisor_block', ...)` sobreposto.
   - Removido nudge "Já registrei aqui sua conversa..." nesse path.
   - Substituído por `_record_audit_finding(action_taken='audit_only_human_present')`
     + `continue`. Supervisor só audita, não move conversa.

2. **Camada B** (`process_pending_escalation_auto_dispatch`): antes de
   chamar `distribute_to_attendant`, faz `_dcz_conv_has_human(conv_id)`
   (nova função, GET `/messaging/conversations/{id}` no DCZ). Se já tem
   humano, marca pending como `in_progress` com nota explicativa e pula.

3. **Camada C** (`_mark_handoff_active`): novo parâmetro `protect_human=True`
   (default). Se já existe handoff ATIVO com motivo em
   `{retention, preferred, dispatch, pre_opening_queue}` e `target` preenchido,
   apenas estende TTL — NÃO sobrescreve motivo nem target. Para fazer
   override real (ex: usuário clica "Liberar agente"), o caller passa
   `protect_human=False` ou usa `_clear_handoff_active`.

**Contexto**

Cadeia de bug observada na conversa #155988 (Alessandra):
- 12:29: Wesley em retenção
- 12:34: Aluna responde
- 12:38: Supervisor identifica algo "alta severidade", caminho
  `tem_humano=True` → escreve `pending_escalation(priority)` e
  sobrescreve `handoff_active` com `supervisor_block`.
- 12:40: Fila noturna pega o pending recém-criado → chama
  `distribute_to_attendant` → idempotência só checa `motivo='dispatch'`,
  não bate, distribui pra Julia.
- 12:41: Bot envia "Vou te transferir pra Julia".
- Depois: bug re-disparou e moveu pra Camila.

**Alternativas descartadas**

- **Apenas Camada A**: deixa portas abertas se outras vias chamarem
  `_mark_handoff_active` ou `record_pending_escalation` com humano lá.
- **Apenas Camada B**: bot ainda envia o nudge "Já registrei aqui..."
  desnecessário; e o handoff humano ainda é sobrescrito.
- **Apenas Camada C**: pending_escalation continua sendo criado, e a
  fila ainda tentaria distribuir (mesmo que `distribute_to_attendant`
  agora respeitasse o handoff, traria ruído de log).
- **Triplicação combinada (A+B+C)**: escolhida porque cada camada
  cobre falha das outras (defesa em profundidade).

**Impacto**

- Consultor em retenção/preferred permanece como dono da conversa.
- Supervisor continua auditando: o finding aparece em
  `agent_audit_findings` com `action_taken='audit_only_human_present'`,
  visível na aba Auditoria IA — operadora pode intervir manualmente se
  achar necessário (Liberar agente / Resolver).
- Caso Alessandra: revertida manualmente (Camila → Wesley) com nota
  interna explicativa.

**Arquivos tocados**

- `agente_ao_vivo_v4.py`:
  - `_dcz_conv_has_human` (nova função utilitária).
  - `process_openai_supervisor_loop`: path `tem_humano=True` reescrito.
  - `process_pending_escalation_auto_dispatch`: pré-check `_dcz_conv_has_human`.
  - `_mark_handoff_active`: parâmetro `protect_human=True` + lógica de
    preservação. Constante `_HUMAN_HANDOFF_MOTIVOS`.

---

### [2026-05-25] - Calendário Acadêmico Graduação 2026 integrado ao agente

**Decisão**

Integração estruturada do PDF oficial do calendário acadêmico (Graduação EaD
2026) ao agente, usando:

1. **Tabela `academic_calendar_2026`** (Postgres principal) com 100 eventos
   canônicos (provas A1/AF, liberação de notas, matrículas, transferências,
   retorno ao curso, dispensa, ACs, TCE, ENADE, feriados, etc.). Cria com
   `_ensure_academic_calendar_table()`; seed automático na 1ª subida via
   `_seed_academic_calendar_if_empty()`.
2. **Seed canônico** em `calendar_2026_seed.py` (lista Python imutável,
   commitada). Permite recarregar via `POST /api/calendar/seed`
   (idempotente: `ON CONFLICT DO NOTHING`).
3. **Função de relevância** `_get_relevant_calendar_events(student_profile,
   user_message)` filtra por:
   - data ≥ hoje e ≤ hoje+240 dias;
   - categoria conforme tópicos detectados em `_detect_calendar_topic()`
     (prova, nota, matrícula, início de aulas, transferência, retorno,
     dispensa, AC, estágio, feriado, ENADE, disciplinas especiais);
   - preferência por público (calouro/veterano/concluinte) sem perder
     eventos `publico='todos'`.
4. **Injeção no contexto LLM**: bloco de texto `CALENDÁRIO ACADÊMICO
   GRADUAÇÃO 2026` anexado a `references` antes de `call_llm()`, com
   instrução explícita "use APENAS as datas acima, NUNCA invente".
5. **Regra 14 no `SYSTEM_PROMPT`**: força o LLM a só usar datas do bloco
   ou redirecionar pra consultor humano se a pergunta não estiver coberta.
6. **API/UI**: endpoints `/api/calendar` (GET com filtros, POST, PUT,
   DELETE soft, summary, seed) + aba **Calendário 2026** no `kb_admin.html`
   com modal de criação/edição.

**Contexto**

PDF oficial 2026 (Graduação EaD, ~80 eventos) fornecido pelo usuário em
25/05/2026. Agente alucinava datas (ex: dizia que aulas começam em
fevereiro para quem se matricula agora, quando o correto é agosto).
Necessidade: usar datas oficiais sem deixar o LLM "deduzir".

**Alternativas descartadas**

- **JSON estático embarcado no SYSTEM_PROMPT**: poluiria o prompt (~5KB de
  datas) em TODA chamada, inflando token cost. Descartado.
- **YAML/Markdown como documento RAG**: a similaridade cosseno do RAG não
  filtra por data; um aluno perguntando "quando é a próxima prova"
  pegaria provas passadas. Descartado.
- **Hard-coded no Python**: igual ao JSON, mas pior de manter. Sem
  edição via dashboard. Descartado.
- **Pós-graduação no mesmo schema**: usuário pediu explicitamente apenas
  graduação. Descartado.

**Impacto**

- Agente passa a responder datas oficiais com 100% de fidelidade ao PDF.
- Operadora pode editar eventos pelo Cockpit (aba Calendário 2026) sem
  precisar de deploy — alteração toma efeito no próximo `handle_message`.
- Risco de alucinação reduzido por dupla camada (Regra 14 no prompt +
  filtro programático que descarta eventos passados).
- Eventos descartados (`ativo=FALSE`) também ficam invisíveis pro agente
  sem deletar histórico.

**Arquivos tocados**

- `calendar_2026_seed.py` (novo, 100 eventos).
- `agente_ao_vivo_v4.py`: `_ensure_academic_calendar_table`,
  `_seed_academic_calendar_if_empty`, `_fetch_calendar_events`,
  `_detect_calendar_topic`, `_student_semester_hint`,
  `_get_relevant_calendar_events`, `_format_calendar_block`; chamada do
  seed em `main()`; injeção em `handle_message`; Regra 14 no `SYSTEM_PROMPT`.
- `kb_api.py`: 6 endpoints `/api/calendar` + função local de garantia de
  tabela.
- `kb_admin.html`: nova aba "Calendário 2026" com cards de resumo, tabela
  paginada, filtros (categoria, semestre, busca textual), modal de criação
  e botão de recarga de seed.

---

### [2026-05-21] - Varredura sistêmica: Ações A-F sobre 418 findings da auditoria IA

**Decisão**
6 ações cirúrgicas em pontos específicos do agente baseadas na varredura do
supervisor OpenAI. Commits atômicos por ação, `py_compile` após cada edit.

1. **Ação A — Reset estado pós-escalação CPF** (`20b82aa`): reseta
   `_awaiting_cpf` e `_awaiting_polo_confirm` após `distribute_to_attendant`
   no `is_escalation_trigger`. Corrige colisão "bot distribui + manda 'não
   encontramos você'".
2. **Ação B — Dedup signature no main loop** (`20b82aa`):
   `_signature_recently_sent`/`_register_signature` no follow-up e auto-close
   do `def main`. Supervisor loop já tinha; main loop não. Cobre 191 casos
   `repeticao`.
3. **Ação C — Fortificar handoff_active** (`72b2605`): check em 3 pontos
   - `send_and_track`: motivos expandidos (`supervisor_block`, `retention`,
     `polo_visit`, `pre_opening_queue`, `human_unavailable`, etc).
   - `process_supervisor_loop` close path: novo check.
   - main loop follow-up e close paths: novo check.
   Cobre 106 casos `sobre_resposta`.
4. **Ação D — Retries longos + auto-fix com cutoff temporal** (`af547c2`):
   `_enforce_assignment_consistency` max_retries 2→4, sleeps 3/6/9→5/10/15/20/30s.
   Nova função `_audit_autofix_assignment_findings` faz PATCH em findings
   <60min. `AUDIT_AUTOFIX_CUTOFF_MIN=60` protege histórico DataCrazy contra
   mudanças retroativas. Cobre 86 casos `assignment_mismatch`.
5. **Ação E — Bloquear auto-close com aluno ativo** (`17decaa`): removido
   shortcut `elapsed >= 3600 -> safe_to_close=True` que pulava check
   `recv_ts > sent_ts`. Mesmo check adicionado no supervisor close path.
   Cobre 35 casos `perdido_conversa`.
6. **Ação F — Escalar com confidence baixa** (`83a04c0`): gate antes de
   `send_and_track` da resposta LLM principal. Se `confidence < 0.30` e
   dentro do horário, escala humano. Cobre 53 casos `resposta_generica`.

**Contexto**
Usuária reportou recorrência de erros e pediu varredura completa em vez de
fix caso-a-caso. Audit retornou 418 findings únicos. Caso da imagem (loop de
redistribuição Danubia→Felipe→Camila→Felipe em 22min com nota
`supervisor_block_with_human`) coberto por Ação C e D.

**Alternativas descartadas**
- **Auto** em vez de **Opus**: usuária aprovou Opus pelo menor risco.
- **Dry-run** Ação D: usuária aprovou execução direta com cutoff temporal.
- Refatorar `send_message_crm` para `force=True`: alterava 8 callers; check
  em `send_and_track` (que já tem o parâmetro) é menos invasivo.

**Impacto**
- Mensagens duplicadas em follow-up/close devem cair a zero.
- Bot respondendo após handoff bloqueado em 3 pontos.
- `assignment_mismatch` deve cair com retries longos + auto-fix.
- Conversas com pergunta nova aberta não são mais encerradas.
- Resposta LLM com baixa confiança escala em vez de ser enviada.
- Risco residual: distribute mais lento em DCZ degradado (~75s a mais com
  5 retries). Mitigado por max 30s entre tentativas.

**Recuperação de bug introduzido**
O primeiro commit Ação A (`87db99d`) introduziu 3 erros de indentação
detectados por `py_compile`. Resetado com `git reset --hard 2e036f3` (não
pushed) e refeito. Daqui pra frente, todo edit valida `py_compile` antes
do próximo.

---

### [2026-05-21] - Guards de ação + handoff stale + dispatch race condition

**Decisão**
Três fixes para o caso "aluno foi distribuído duas vezes" e similares:

1. **`_handle_outro_polo` ganha guard de ação** (signature
   `outro_polo_handled` por 24h). Marca ANTES de enviar, evita que duas
   execuções (linha 8741 valida CPF + linha 8984 carrega perfil) gerem
   4 mensagens duplicadas + 2 chamadas de `_move_business_to_perdido` +
   2 chamadas de finish.
2. **`_had_attendant_left_after_handoff`**: nova função que detecta no
   histórico mensagens tipo "Débora finalizou o atendimento" /
   "Atendente Débora removido" criadas DEPOIS do `handoff_active`
   registrado. Se o nome bate com `target_attendant`, **limpa o
   handoff_active stale**. Chamada no início do `process_in_hours_rescue`.
3. **`process_in_hours_rescue` respeita handoff_active** antes de
   distribuir: se há `dispatch` ativo para X e X está ativo agora,
   re-atribui sticky (mesma pessoa) em vez de distribuir para outro
   consultor (evita "Débora vai continuar" + "Vou te conectar com Julia").
   Se X está offline, limpa o handoff e segue fluxo normal.
4. **`send_and_track` recheca handoff dispatch < 90s**: se outro processo
   acabou de distribuir essa conv, suprime a resposta órfã (race
   condition entre LLM gerando resposta + rescue distribuindo). Chamadas
   internas do `distribute_to_attendant` passam `force=True` para escapar
   desse recheck.
5. **`handle_polo_visit_intent` e `handle_polo_address_only` ganham
   guards de ação** (signatures `polo_visit_handled` 4h e
   `polo_address_handled:<nome>` 30min). Antes só `handle_masterclass_intent`,
   `handle_inicio_aulas_intent` e `handle_a1_intent` tinham.

**Contexto**
Usuária reportou caso (10:02-10:03): aluno mandou CPF, agente respondeu
com **OUTRO_POLO_MSG_1 + OUTRO_POLO_MSG_2 duplicadas** (4 mensagens
idênticas). E caso anterior (12:35): bot prometeu Débora às 12:14 ("vai
dar continuidade") mas 21min depois o `process_in_hours_rescue` distribuiu
para Julia, gerando duas promessas conflitantes.

Causa raiz por caso:
- Polo duplicado: 2 caminhos do `handle_message` chamavam `_handle_outro_polo`
  em ciclos sucessivos. `send_and_track` tem dedup mas a função tem
  side effects (envio + pipeline + finish) — guard precisa ser **antes**
  de iniciar a sequência.
- Promessa conflitante: `handoff_active` não é limpo quando atendente
  finaliza e ninguém checava se atendente prometido ainda estava lá
  antes de redistribuir.

**Alternativas descartadas**
- *Só confiar em `send_and_track`*: já provou-se insuficiente para
  funções com side effects (pipeline, finish, distribute).
- *Polling mais espaçado*: tratamento sintomático; não resolve o root
  cause.
- *TTL menor em `handoff_active`*: 4h é razoável para promessas reais;
  reduzir teria efeitos colaterais em handoffs longos legítimos.

**Impacto**
- Funções de ação idempotentes por signature (acompanha o padrão das
  outras intents canônicas).
- `process_in_hours_rescue` agora respeita promessas vigentes (sticky
  re-atribuição) → aluno não recebe 2 promessas diferentes.
- Race condition do dispatch eliminada para mensagens que ainda estão
  no buffer do LLM quando outra thread distribui.

---

### [2026-05-21] - Anti-duplicação por similaridade semântica (paráfrase do LLM)

**Decisão**
- Adicionada coluna `body_norm TEXT` em `agent_sent_signatures` (migration
  leve no startup).
- `_register_body` agora persiste duas normalizações:
  - **Hard** (`<NOME>` em proper nouns) → hash exato (camada 1)
  - **Soft** (preserva nomes, só lowercase/sem-acento) → similaridade (camada 2)
- `_body_recently_sent` ganha 3 camadas em ordem de custo crescente:
  1. **Hash exato** do normalizado hard (`agent_sent_signatures.body_hash`)
  2. **SequenceMatcher char-by-char** ≥ **0.78** sobre o normalizado soft
     (pega mensagens praticamente iguais com pontuação/espaços diferentes)
  3. **Jaccard de palavras únicas** ≥ **0.50** com **≥ 6 palavras em
     comum** (pega paráfrase semântica — mesmo conteúdo, palavras
     diferentes/reordenadas)
- Quaisquer 1, 2 ou 3 já fazem o `send_and_track` suprimir (igual antes).

**Contexto**
Caso reportado pela usuária (Naiara, 12:24): aluna mandou reflexão
religiosa, o agente respondeu **duas mensagens consecutivas** com mesmo
conteúdo semântico mas palavras diferentes. Hash exato não pegou porque
o LLM gerou paráfrases ("essa certeza de que..." vs "essa mensagem traz
uma paz..."). SequenceMatcher também ficou em 0.33 (char-by-char trecho
grande mudou). Jaccard de palavras pegou: 0.526 entre as duas.

**Alternativas descartadas**
- *Apenas baixar threshold do SequenceMatcher*: causaria falsos positivos
  em respostas sobre assuntos diferentes (controle dos testes).
- *Embeddings da OpenAI*: custo extra por mensagem enviada (US$/req), e
  latência. Jaccard de palavras com normalização correta cobre o caso
  real medido.
- *Lock per-conv mais longo abrangendo LLM*: ajudaria contra race
  condition mas não impediria 2 ciclos sequenciais do agente processarem
  a mesma mensagem do aluno em momentos distintos. Dedup é a camada certa.

**Testes determinísticos** (7/8 passam — único miss é paráfrase muito
sutil de mesma resposta com tom diferente):
- CASO REAL imagem: char=0.33 jacc=**0.53** → pega ✅
- Assuntos diferentes: jacc=0.00 → não pega ✅ (zero falsos positivos)
- Mesma canônica com nomes diferentes: pega ✅
- Resposta canônica MasterClass/A1/polo/início-aulas idêntica: pega ✅

**Impacto**
- Eliminado o tipo de duplicação reportado pela usuária ("ainda está
  duplicando as coisas").
- Custo: 1 query extra a `agent_sent_signatures` por mensagem que passou
  da camada 1 (limit 8 rows, indexada por conv_id), + computação O(n²)
  do SequenceMatcher sobre strings de até 400 chars (microssegundos).
- Próximo passo se ainda houver casos: aumentar `LIMIT 8` da camada 2
  ou adicionar normalização que stemming/lematização (mais agressiva).

---

### [2026-05-21] - Silenciamento do supervisor fecha o ciclo (distribui + nudge + pending)

**Decisão**
Quando o supervisor OpenAI detecta severidade ALTA + tipo crítico
(`repeticao_resposta`, `sobre_resposta`, `duplicado_distribuicao`) e
silencia o agente naquela conv, agora o sistema também:
1. Verifica se a conv já tem atendente humano.
2. **Sem humano + dentro do expediente:** distribui imediatamente via
   `distribute_to_attendant` (lock atômico + signature dedup já existentes).
3. **Sem humano + fora do expediente:** registra `pending_escalation`
   com `reason='supervisor_block'`, `tier='priority'` para entrar na
   fila Cockpit e ser distribuído na abertura.
4. **Com humano:** registra `pending_escalation` com
   `reason='supervisor_block_with_human'`, `tier='priority'` apenas para
   destacar na fila (humano já vê a conv).
5. Envia 1 nudge único ao aluno ("já registrei aqui, em pouquinho um(a)
   consultor(a) retoma") com signature `supervisor_block_nudge` TTL 4h.
6. SÓ DEPOIS chama `_mark_handoff_active(supervisor_block, 6h)`. A ordem
   importa: `handoff_active` é PK única por conv, então marcar
   `supervisor_block` por último sobrescreve eventual `dispatch` deixado
   pela distribuição e mantém o bot silenciado.

**Contexto**
Usuária questionou: "Se eu não clicar em Liberar agente e o aluno
enviar mensagem, ele não fica sem resposta nem distribuição?". Análise
do código confirmou o gap: o silenciamento era passivo — só marcava
`handoff_active` e registrava finding, sem garantir caminho para humano.
O `process_in_hours_rescue` cobria o caso depois de ~10min, mas durante
esse intervalo o aluno ficava sem nenhum sinal de atendimento.

**Alternativas descartadas**
- *Não silenciar imediatamente, só registrar finding*: perde a proteção
  contra o bot continuar errando (era o ponto inicial do silenciamento).
- *Encadear distribuição via supervisor_loop interno em vez de
  imediatamente*: aumentaria latência sem ganho real.
- *Inverter ordem (silenciar antes de distribuir)*: `_mark_handoff_active`
  com motivo `dispatch` dentro de `distribute_to_attendant` sobrescreveria
  o `supervisor_block`, anulando o silenciamento.

**Impacto**
- Buraco de até 10min entre silenciamento e resgate eliminado.
- Aluno recebe nudge imediato confirmando que está sendo atendido.
- Cockpit recebe entrada `priority` na fila com motivo claro.
- Bot continua silenciado normalmente (humano libera via dashboard).
- Cobertura assíncrona do `in_hours_rescue` continua funcionando como
  safety net redundante (não atrapalha por causa do lock atômico).

---

### [2026-05-21] - Ligar/Desligar real do agente via flag em `agent_config`

**Decisão**
- Criar flag `agent_runtime_enabled` em `agent_config` (default `true`).
- O agente principal lê a flag a cada iteração do loop (cache 5s). Se `false`,
  pula TODO o processamento (rescue, fila, auto-dispatch, novas convs) e
  registra heartbeat com status `paused`. Reativação é instantânea.
- Endpoints `/api/agent/live/start` e `/api/agent/live/stop` viram set/unset
  dessa flag (NÃO matam mais subprocess). `/api/agent/live/status` agora
  reporta `running = enabled flag AND heartbeat recente`.
- O agente continua sendo iniciado pelo `start.sh` no container — flag não
  controla o ciclo de vida do processo, só se ele processa.

**Contexto**
Dashboard mostrava "Agente Desligado" mas o agente continuava atendendo e
distribuindo. Causa: existiam dois mecanismos em paralelo — (1) agente
principal subido pelo `start.sh` e (2) subprocess de teste com `PHONE_TO_MONITOR`
controlado pelo botão "Ligar/Desligar". O botão controlava só o (2), que
quase nunca estava em uso. Isso impedia o operador de pausar o agente real
durante deploys ou em caso de comportamento errático.

**Alternativas descartadas**
- *Remover agente do `start.sh` e só subir via botão Ligar*: risco operacional
  alto — se container reinicia sozinho (crash, restart automático), agente
  fica parado até alguém notar. Inaceitável fora do expediente.
- *Híbrido (start.sh + flag controla subprocess separado)*: combinaria a
  complexidade das duas abordagens sem ganho real.
- *Matar/reiniciar processo via SIGTERM do cockpit*: depende de IPC entre
  processos dentro do container, frágil em ambientes containerizados.

**Impacto**
- Botão "Ligar/Desligar" do cockpit volta a refletir a realidade
  (`running: true/false` corresponde ao que o operador vê acontecendo).
- Deploy passa a ter procedimento seguro: clicar Desligar → fazer commit →
  rebuild → clicar Ligar.
- Após rebuild, o agente respeita o último estado da flag (não fica
  ligado/desligado por acidente).
- Em caso de bug crítico em produção, operador pode parar o agente
  instantaneamente sem desligar o container inteiro (mantém o dashboard
  operacional, supervisor OpenAI ativo, etc.).

**Telemetria de validação**
- `GET /api/agent/live/status` retorna `{enabled, process_alive, heartbeat_seconds_ago}`.
- Heartbeat do agente passa a registrar status `paused` quando flag=false.

---

### [2026-05-21] - Resgate ignora despedidas ("Obrigado") e fecha conversa

**Decisão**
- `process_in_hours_rescue` passa a buscar a última mensagem do aluno antes
  de distribuir. Se for despedida/agradecimento (`_is_farewell_message`),
  pula o resgate, fecha a conversa via `close_conversation_crm` e marca
  `pending_escalation.status = 'closed_no_engagement'`.

**Contexto**
Caso reportado: Gilflan respondeu apenas "Obrigado" após atendimento já
concluído pela Beatriz. Após 10min sem nova mensagem, `process_in_hours_rescue`
distribuiu a conversa para Danubia desnecessariamente. A função
`_is_farewell_message` já existia e era usada em `process_post_close_rescue`,
mas não em `process_in_hours_rescue`.

**Alternativas descartadas**
- *Apenas pular sem fechar*: deixaria a conversa órfã em "Em aberto" para
  sempre, eventualmente seria recapturada pelo próximo ciclo de rescue.
- *Filtrar antes na listagem*: a info de despedida só é confiável buscando
  histórico, não tem como filtrar via query do DCZ.

**Impacto**
- Não há mais distribuição reflexa de "Obrigado".
- Conversas com despedida real são fechadas no CRM e marcadas como
  `closed_no_engagement` (mesma marcação usada para auto-close sem engajamento).

---

### [2026-05-20] - Auto-correção de findings + upgrade para GPT-5.1

**Decisão**
- Trocar `OPENAI_SUPERVISOR_MODEL` default de `gpt-4o` para `gpt-5.1`
  (reasoning forte, contexto 400K, ~4x mais barato em produção).
- Criar endpoint `POST /api/audit/findings/{id}/fix` que executa correção
  automática sob demanda. Primeiro handler suportado: `assignment_mismatch`
  → reaplica PATCH lead+business+change-attendant até convergir (5 retries
  com backoff). Se sucesso, finding marcado `resolved_by='auto-fix:<tipo>'`.
- Botão "Corrigir agora" na aba Auditoria IA do Cockpit, em verde, separado
  do "Apenas arquivar" (que continua sendo arquivamento sem correção).
- Maps de atendentes (`ATTENDANT_MAP`, `CRM_ATTENDANT_MAP`,
  `STAGE_ATENDIMENTO_ID`) duplicados no `kb_api.py` por enquanto — sem
  refatoração para módulo compartilhado para não tocar o agente em produção.

**Contexto**
Usuário relatou que precisava corrigir manualmente no CRM cada vez que a
verificação determinística (`_enforce_assignment_consistency`) flagrava
divergência. Pediu supervisor "inteligente o suficiente para arrumar
sozinho" e modelo OpenAI mais recente (GPT-4o "muito antigo").

**Alternativas descartadas**
- *Refatorar módulo compartilhado já agora*: maior risco em produção
  funcionando; postergado.
- *Loop autônomo ON por default*: descartado nesta fase. Auto-fix é
  acionado pelo botão — usuário valida antes de virar autônomo.
- *Modelo GPT-5.5* (~US$10/dia): GPT-5.1 atende reasoning necessário com
  custo 4x menor.
- *Função compartilhada via HTTP entre kb_api e agente*: o agente não
  expõe HTTP; complexidade de IPC não compensa para uso pontual.

**Impacto**
- Custo OpenAI: ~US$4/dia (estimado para 4.3k chamadas/dia com gpt-5.1).
- UI: card de finding ganha botão verde "Corrigir agora" para
  `assignment_mismatch`; cinza "Apenas arquivar" para os demais.
- Manutenção: maps de atendentes devem ser atualizados em DOIS lugares
  (agente_ao_vivo_v4.py e kb_api.py) até refatoração futura.
- Reversível: setar env `OPENAI_SUPERVISOR_MODEL=gpt-4o` reverte modelo;
  remover endpoint /fix reverte auto-correção.

---

### [2026-05-20] - Endereços oficiais dos polos + intent de visita presencial

**Decisão**
Adicionar fonte canônica de endereços dos 11 polos (`POLOS_OFICIAIS`) no
código, detectar intenção de visita/dificuldade na comunicação ANTES do LLM
e transferir para consultor humano com mensagem humanizada, eliminando
alucinação de endereço pelo LLM.

**Componentes**
- `POLOS_OFICIAIS`: lista de 11 polos (Barra Funda, Vila Prudente 2, Morumbi,
  Taboão Centro, Taboão Mituizi, Sapopemba, Freguesia do Ó, Ibirapuera,
  Campinas, Capivari, Itapira) com endereço + ponto de referência.
- `_normalize_polo_match(text)`: mapeia texto livre para a entrada certa
  (com aliases tipo "Moema" → Ibirapuera, "Ouro Verde" → Campinas, "Mituzi/Mituzzi" → Taboão Mituizi).
- `detect_polo_intent(text)`: classifica em `visit`, `address_only` ou `none`.
  Triggers de visita: "ir pessoalmente", "ir ao polo", "dificil comunicacao",
  "conversar pessoalmente", "prefiro ir", "qual endereco do polo", etc.
- `handle_polo_visit_intent(conv_id, polo, question)`:
  1. Manda mensagem humanizada com endereço oficial (se polo identificado).
  2. Avisa que vai transferir.
  3. Chama `distribute_to_attendant` se dentro do horário; fora do horário
     registra em `pending_escalation` + marca `handoff_active(motivo='polo_visit')`.
- `handle_polo_address_only(conv_id, polo, question)`: responde só com
  endereço oficial sem transferir. Se polo não identificado, lista os 11.
- Plug em `handle_message` ANTES do LLM e DEPOIS do bloco de retenção.
- `SYSTEM_PROMPT` ganha **REGRA ABSOLUTA #11**: NUNCA inventar endereço,
  rua, número, bairro, referência, horário ou CEP de polo. Se aluno
  perguntar e não houver bloco oficial de endereços nas referências,
  responder: *"Deixa eu confirmar essa informação com a equipe..."*.

**One-shot Vanessa Carmona**
- `_oneshot_fix_vanessa_barra_funda()` executado uma única vez no startup
  do agente (idempotência via `agent_config.oneshot_vanessa_barra_funda_done`).
- Procura conv ativa da Vanessa, manda nota interna + mensagem humanizada
  de desculpas + endereço correto + distribui para consultor humano.

**Contexto**
- Imagem da conversa com Vanessa Carmona mostrou o LLM inventando endereço
  da Barra Funda como "Rua dos Três Irmãos, 100" — alucinação pura. A KB
  não tem essa rua. O endereço correto é "Rua do Bosque, 1621".
- Usuária pediu regra global: sempre que aluno indicar dificuldade ou
  intenção de visitar polo, transferir para humano com mensagem humanizada.

**Alternativas descartadas**
- Inserir os 11 polos como Q&A na `knowledge_base` → mais lento, sujeito
  a embedding decidir pegar ou não; pior controle. Fonte código é mais
  determinística.
- Apenas regra no prompt → LLM ignora "REGRAS ABSOLUTAS" eventualmente
  quando o aluno insiste. Interceptação ANTES do LLM elimina essa janela.
- Endpoint admin temporário para corrigir Vanessa → expõe superfície. O
  one-shot no startup é self-contained, idempotente e some sozinho.

**Impacto**
- Aluno pergunta endereço/local de polo → resposta oficial, sem alucinação.
- Aluno indica intenção de ir presencial / dificuldade online → mensagem
  humanizada + transferência automática (ou fila pré-abertura fora do horário).
- Caso Vanessa será resolvido no primeiro start do agente após este deploy.
- Para atualizar endereço de polo no futuro: editar `POLOS_OFICIAIS` no
  código + redeploy. Fonte única da verdade.

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
