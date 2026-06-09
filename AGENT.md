# AGENT.md — registro de decisões estruturais

## Convenções

- Decisões registradas em ordem cronológica decrescente.
- Cada entrada: **Decisão**, **Contexto**, **Alternativas descartadas**, **Impacto**.

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
