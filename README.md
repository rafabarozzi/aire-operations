# AIRE Operations

API e worker Python para incidentes, comunicações e War Rooms. A demonstração começa com um incidente manual e uma sala Teams ou Slack existente. Os únicos envios implementados são simulados e persistidos no PostgreSQL.

## Executar localmente

Requer Python 3.12 e Docker Compose v2. No diretório do clone:

```bash
python -m venv .local-venv
. .local-venv/bin/activate
python -m pip install -r requirements-test.txt
python -m operations.dev keys
docker compose up --build -d
TOKEN=$(python -m operations.dev token)
curl -fsS http://127.0.0.1:8080/v1/incidents \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: checkout-demo-1" \
  -H 'Content-Type: application/json' --data @contracts/v1/manual.example.json
```

Swagger está em http://127.0.0.1:8080/docs. O token sintético vale uma hora. A aplicação recebe somente a chave pública. A chave privada .local/issuer.pem pertence ao emissor local. O Compose usa credenciais sintéticas e publica a API apenas em loopback.

### Experimentar a War Room

1. Guarde id e version da resposta. Em cada POST seguinte use Idempotency-Key nova e If-Match com a versão entre aspas, por exemplo `"1"`.
2. POST /v1/incidents/{id}/policy com `{"interval_minutes":1,"escalation_minutes":1,"additional_teams":["ERT"]}`.
3. POST /v1/incidents/{id}/rooms com `{"provider":"teams","room_id":"checkout-demo","state":"open"}`. O worker grava a primeira cobrança ao vencer o prazo. Consulte /v1/incidents/{id}/timeline para ver reminder e, depois do atraso, escalation com o mesmo prompt_id.
4. Releia o incidente. Envie a simulator/messages `{"provider":"teams","room_id":"checkout-demo","message":"verificando"}`. A cobrança segue pendente. Para oficializar “sem mudança”, envie o mesmo provedor e sala com um objeto update contendo no_change=true, fact_at em RFC3339 e recovery_at=null. A nova atualização encerra a cobrança e inicia o próximo prazo.
5. POST em communications cria um rascunho por público. POST em approvals, com publication_id obtido da timeline, autoriza e publica. Teste a leitura do cliente com um token próprio, por exemplo `python -m operations.dev token --client bot --roles read:customer`.

A migração roda uma vez antes da API e do worker e aceita reexecução. Transações PostgreSQL, bloqueios por linha e SKIP LOCKED coordenam workers. Incidente e agenda têm chave de organização. O simulador grava envio e avanço da agenda na mesma transação. O intervalo vigente é aplicado na próxima atualização. Pausa suprime envios até o prazo e exige motivo. Uma cobrança pendente pode ter entregas em várias salas abertas.

## Testar e conferir o contrato

Com PostgreSQL de teste próprio, configure DATABASE_URL e execute:

```bash
python -m unittest discover -s tests -v
python -m contracts.export
git diff --exit-code contracts/
```

A suíte usa organizações sintéticas exclusivas e simula o avanço do relógio do worker. Use um banco dedicado porque o worker seleciona todos os incidentes vencidos. O workflow operations-v1 usa PostgreSQL 16. O Compose pode executar a suíte em um ambiente local descartável com `docker compose run --rm api python -m unittest discover -s tests -v`.

## Identidade e permissões

Portal e Mobile enviam access token Bearer destinado a aire-operations-v1. O serviço valida RS256 com a chave pública configurada, emissor, audiência, expiração, emissão e azp na allowlist AIRE_CLIENTS. O emissor confiável deve vincular sub, tenant, actor_type (person ou service), azp e roles à identidade e às permissões verificadas. AIRE não lê tenant de headers nem do corpo. Uma sessão de outra organização requer novo token emitido para ela. Portal/Mobile usam identidade delegada. Postmortem e chatbots usam identidade de serviço. O emissor local é exclusivo de desenvolvimento.

| Permissão | Operação |
| --- | --- |
| reader, operator, coordinator, admin | Leitura operacional do tenant, inclusive timeline interna |
| operator, coordinator, admin | Comandos operacionais. Resposta oficial exige o responsável configurado ou coordinator/admin |
| admin | Alterar política por organização e por incidente |
| Papéis definidos em severity_roles, recovery_roles, closure_roles | Autorizar severidade, recuperação/reabertura ou confirmação de encerramento |
| draft:PÚBLICO, approve:PÚBLICO | Criar rascunho ou aprovar/publicar, além de um papel operacional |
| read:PÚBLICO | Ler publicações aprovadas exclusivamente para aquele público |
| simulator | Operar a entrada simulada de uma sala aberta, além de papel operacional |

O principal de chatbot público recebe somente read do seu público. Uma publicação é imutável após aprovação. Uma sugestão de IA pode entrar pelo mesmo fluxo de rascunho. O encerramento do incidente requer estado resolved, solicitação e confirmação humana autorizada. Encerrar sala e registrar resolução ITSM usam comandos próprios.

## Dependências externas e homologação

O piloto local simula instalação, identificação da sala e entrega. Produção requer onboarding do IdP, mapeamento validado de organizações/papéis, rotação de chaves, verificação dos callbacks dos provedores, credenciais e homologação por tenant. Chamadas reais de Teams/Slack exigem uma fila de saída com retentativas e reconciliação de duplicatas. A aplicação registra referências ITSM e resultados fornecidos por integradores. Ela não envia chamados a ServiceNow/JSM nesta versão.

Referências oficiais consultadas para delimitar os adaptadores:

- [Microsoft identity platform • validação de access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens)
- [Teams • instalação e mensagens proativas](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages)
- [Slack • verificação de requests](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
- [Slack • chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)

[Limites e contratos para as frentes paralelas](docs/integration.md)
