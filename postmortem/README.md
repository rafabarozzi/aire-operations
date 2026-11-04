# Postmortem v1

Módulo Python isolado, sem dependências obrigatórias de terceiros. Executar da raiz do repositório:

```sh
python -m postmortem.demo          # rascunho Markdown, banco temporário
python -m postmortem.demo --json   # representação consumida pelo portal
python -m unittest postmortem.test_postmortem -v
```

## Entrada de Operations e importação

Referência de desenvolvimento: [OpenAPI v1 no commit 4a49a23](https://github.com/rafabarozzi/aire-operations/blob/4a49a23834e09c834f0cc94bace3be7e3d1a36a0/contracts/v1/openapi.json). A main inspecionada tinha apenas o README. O módulo lê o incidente e pagina a timeline com after_version até a versão do snapshot. Rejeita troca de tenant, incidente divergente e lacunas de versão. As referências ServiceNow/JSM permanecem no snapshot como destinos do INC. A origem é lida de Operations. Não há tabela de incidentes neste módulo.

O worker chama ingest.ingest com identidade verificada, UUID do incidente, get(path, params) autenticado em Operations, prazo absoluto de expiração Unix, coletor opcional e anexos. Cada anexo contém tenant, incident_id, source_id estável e content WebVTT lido do arquivo. started_at e recorded_at são opcionais e exigem fuso. O horário da fala depende do início real da transcrição, que pode diferir do início da reunião. Sem essa âncora, a timeline guarda o deslocamento e deixa o horário absoluto desconhecido. Mensagens seguem fixtures/teams.json; recorded_at é o registro na fonte. Uma edição na fonte exige ID de revisão distinto.

A ingestão reúne todas as fontes antes de persistir. O reenvio idêntico é idempotente. O prazo mais curto entre as fontes rege o incidente inteiro; reimportar não estende o prazo vigente. O host fornece prazos a partir da política e da idade original dos dados. A leitura expira imediatamente. Executar Store.sweep periodicamente para remoção física das linhas. Store.delete remove evidências e todas as revisões do incidente por organização. Em uma exclusão individual de fonte, usar essa exclusão integral conservadora e impedir reingestão da fonte revogada no coletor. Cópias, backups, exportações e políticas dos provedores exigem o mesmo ciclo no ambiente de integração.

## Consulta e revisão pelo portal

Montar portal.application(store, authenticate) em WSGI, por exemplo atrás do backend Operations. O host deve verificar a identidade e derivar tenant, subject, roles e human de credenciais confiáveis. O módulo não expõe servidor público ou mecanismo de login. Acesso de leitura requer reader, operator, coordinator ou admin. O worker gera a primeira revisão com Store.write(actor, id, 0, "generate"). Operações de revisão exigem pessoa autenticada com coordinator/admin.

| Método | Caminho sob /v1/postmortems/{incident_id} | Resultado |
|---|---|---|
| GET | raiz / sem barra final | Última revisão JSON |
| GET | /revisions/{n} | Revisão histórica JSON |
| GET | /markdown | Relatório Markdown da última revisão |
| POST | /review | Revisão humana; sections opcional substitui todas as seções |
| POST | /rectify | Nova revisão em rascunho; reason obrigatório, sections opcional |
| POST | /approve | Aprova versão revisada sem alterar as conclusões |

POST requer If-Match com versão entre aspas. Retorna 201 e a nova revisão. Base obsoleta ou evidência nova na aprovação retorna 409; precondição ausente retorna 428. Um POST cujo resultado se perdeu pode ser reconciliado por GET. Aprovação preserva a versão revisada. Retificação de aprovado exige motivo e novo ciclo de revisão.

A demo JSON é um exemplo executável do contrato postmortem.v1. O retorno inclui tenant, incident_id, version, parent, status, action, reason, author, written_at, sections e timeline. Uma seção vazia representa informação desconhecida. Cada afirmação tem text e refs não vazias apontando para IDs da timeline da **mesma revisão**. Ações corretivas também têm owner e due (data ISO), ambos anuláveis. A timeline preserva source, author, fact_at, recorded_at, text e, quando aplicável, cue. Ordena fatos conhecidos cronologicamente e coloca fatos sem horário absoluto ao final. Campos textuais são dados não confiáveis; renderizar JSON como texto e sanitizar qualquer renderizador Markdown. Não são calculados MTTD/MTTR: a timeline atual não identifica todos os marcos necessários.

## IA e decisões

A demo é extrativa. Opcionalmente, um worker autorizado pode injetar clientes dos SDKs no ai.OpenAI ou ai.Gemini e chamar suggest com evidências de um único incidente após verificar política de transferência e expiração. O provedor recebe dados como texto e não recebe ferramentas. A saída aceita somente hipóteses com referências válidas. O revisor deve verificar se cada referência sustenta semanticamente a afirmação. Inserir sugestões requer revisão explícita; a IA nunca autoriza ações, muda estado ou confirma a causa.

Cada tentativa envia ao sink de auditoria metadados de organização, geração, modelo, horário, resultado e tokens, sem prompts ou conteúdo do incidente. A estimativa de custo fica separada no retorno, depende das tarifas fornecidas por moeda e fica nula sem tarifa/contagem. Não é fatura do provedor.

## Homologação e dependências propostas à frente Operations

- Implementar transporte HTTP autenticado no host, identidade humana verificável, agendamento de expurgo e sinal de revogação de fontes. Homologar paginação e os valores de kind/data com o serviço em execução. O export atual tipa kind como string e data como objeto livre. Proposta: publicar exemplos versionados dos eventos do bot e de retificações, além de referências estáveis de monitores e marcos explícitos para métricas. Nenhuma alteração do contrato compartilhado integra este PR.
- Teams: resolver sala autorizada para chat/reunião e vínculo do tenant, seguir paginação, obter mensagem e transcrição existente com consentimento aplicável. O importador desta versão suporta WebVTT. A política de atribuição pode vedar WebVTT; tratar explicitamente a indisponibilidade ou implementar o formato sem falante antes de habilitar essa coleta. [Mensagens de chat](https://learn.microsoft.com/graph/api/chat-list-messages?view=graph-rest-1.0) · [Conteúdo de transcrição](https://learn.microsoft.com/graph/api/calltranscript-get?view=graph-rest-1.0).
- Slack: validar workspace, associação à conversa e escopos de history para seu tipo; paginar histórico e respostas em threads, respeitar limites e Retry-After. O adaptador recebe o envelope normalizado pelo coletor autorizado. [Histórico](https://docs.slack.dev/reference/methods/conversations.history/) · [Threads](https://docs.slack.dev/reference/methods/conversations.replies/) · [Limites](https://docs.slack.dev/apis/web-api/rate-limits/).
- Validação local com fixtures sintéticas de API, sala e arquivo WebVTT e clientes de IA simulados. Nenhuma chamada autenticada, cobrança real, captura de áudio ou homologação em tenant de provedor. O armazenamento SQLite serve à revisão local; operação distribuída e integração de identidade ficam com a frente principal.
