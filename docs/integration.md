# Integração V1 • Mobile, Portal, Bot e Postmortem

Fixe a revisão da branch de Operações indicada no PR. O namespace HTTP é /v1 e a especificação tem versão 1.0.0. Os valores no fio estão em inglês. O consumidor traduz os rótulos na interface.

| Consumidor | Contrato | Uso |
| --- | --- | --- |
| Mobile e Portal operacionais | contracts/v1/openapi.json e schemas.json | Criar o incidente, consultar versão, salas e jornadas, registrar comandos. |
| Postmortem | GET /v1/incidents/{id} e /timeline?after_version=N&limit=100 | Ler eventos na ordem da versão. O cursor é exclusivo. Para reconstruir um corte, guardar a versão do incidente e consumir eventos até ela. |
| Chatbots de gestão e cliente | GET /v1/publications/{audience}/{id} | Usar um token de serviço limitado a read:management ou read:customer. A resposta contém apenas a publicação aprovada, autor e data da aprovação. |
| Bot da sala em simulação | POST /v1/incidents/{id}/simulator/messages | Um campo update estruturado representa resposta oficial explícita. Texto avulso gera evento sem reiniciar o prazo. |
| Integração ITSM | POST /v1/incidents/{id}/references | Registrar resultado externo com fornecedor, identificador, sincronização e estado do chamado. |

O evento tem ID, versão, autor, origem autenticada, fact_at, recorded_at e data. O kind é o comando da rota (por exemplo updates, journeys, severity ou corrections). create inicia a versão 1. reminder e escalation incluem prompt_id e destinatários. communications acrescenta id ao rascunho e approvals referencia esse id. corrections referencia event_id e acrescenta a retificação. A V1 preserva o evento original. Evidências são IDs opacos do catálogo do consumidor, sem URLs assinadas. A resolução dessas referências depende das permissões desse catálogo.

O cliente envia Idempotency-Key em cada escrita e If-Match com a versão entre aspas em cada alteração, inclusive política. A criação dispensa If-Match. Repetir a mesma chave, identidade e comando devolve a resposta original. 409 indica conflito de chave ou regra de negócio. Em 412, releia a versão e reavalie o comando antes de emitir nova chave. 401 exige autenticação válida, 403 indica ausência de permissão e 404 também cobre recursos de outra organização.

O snapshot do incidente inclui a última atualização, o próximo prazo, a previsão com autor e horário e a política específica, quando existir. GET /v1/policy expõe o padrão da organização. O worker registra eventos e avança a versão. Para confirmar encerramento, envie closure-requests, obtenha na timeline o ID do evento da versão retornada e use-o em rooms ou state com o If-Match dessa versão. Uma alteração interveniente exige nova solicitação. A confirmação exige uma identidade de pessoa.

## Propriedade e coordenação

Operações mantém operations/, migrations/, contracts/v1/, dependências, Compose e inicialização. Postmortem implementa seu módulo e testes em postmortem/ e consome a API com token de serviço reader. Coordene qualquer nova migration, dependência ou registro de rota com Principal e Integração antes de editar arquivos compartilhados. Mobile consome o OpenAPI versionado. Nenhuma frente depende da abertura automática de salas da Etapa 2. Merge e deploy cabem à coordenação.
