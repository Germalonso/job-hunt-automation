# Radar de Vagas

Monitor automatizado da API pública da Gupy que notifica no WhatsApp vagas de
estágio e júnior em TI dentro de 30 minutos da publicação.

## O problema

Candidatura em vaga de tecnologia é um jogo de latência, não só de aderência.
Analisei meu próprio histórico de candidaturas na Gupy procurando o que separava as
que avançaram das que não. O perfil não era o gargalo. A janela era:

```
 4h após a publicação  →   7 propostas concorrentes
18h após a publicação  →  67 propostas concorrentes
```

Descobrir a vaga abrindo o site significa entrar já na faixa ruim da curva. O radar
fecha esse intervalo para 30 minutos.

## Como funciona

```
cron 7,37 * * * *
      │
      ├─ 4 perfis de busca (filtro server-side na própria API da Gupy)
      ├─ filtro de nível/área/exclusão aplicado só no título, normalizado NFD
      ├─ corte de idade: publicada nas últimas 72h
      ├─ dedup em Postgres por (fonte, id_externo)
      └─ notificação via WhatsApp (Evolution API)
```

O estado da notificação vive em `notificada_em IS NULL`, não no INSERT. Uma falha de
entrega no WhatsApp deixa a vaga pendente para o ciclo seguinte em vez de queimá-la.

## O que eu medi antes de escrever código

A primeira versão do plano usava 18 termos de busca por palavra-chave. Medir contra a
API ao vivo derrubou a abordagem inteira:

| Hipótese | Medição | Consequência |
|---|---|---|
| 18 termos de keyword cobrem o mercado | 6 termos retornavam zero; `Data Engineer (Python) \| JR (Remote)` era invisível para todos eles | Trocado por 4 querystrings com filtro server-side: 117 → ~1716 vagas de cobertura |
| `pagination.total` serve para paginar | Clampa no valor de `limit` — um perfil com 1370 vagas responde `total: 100` | Paginar por `while len(data) == limit` |
| Dá para filtrar por `description` | 47% de falso positivo em "dados", 30% de falso negativo em "suporte" | Filtro aplicado só em `name` |
| `city` identifica a localidade | Vazio em 141 de 145 vagas remotas | Exibir `workplaceType`, que nunca vem vazio |
| `state=MG` funciona | Retorna 0. A API quer `state=Minas Gerais` | — |
| Acento é opcional | `Uberlandia` → 0 resultados. `Uberlândia` → 392 | — |
| `publishedDate` tem um formato | Dois: ISO-8601 com `Z` e data pura sem timezone | Parser força UTC quando falta timezone |

O mesmo vale para o ambiente: o stdout do Windows é cp1252 e estoura
`UnicodeEncodeError` em títulos com emoji — 7 em cada 253 vagas. O coletor força UTF-8
no stdout.

## Decisões de engenharia

**Filtro no servidor, não no cliente.** A API da Gupy aceita `type`, `isRemoteWork` e
`city`. Quatro querystrings cobrem o que 18 buscas por palavra-chave cobriam pior.

**Chave primária composta `(fonte, id_externo)`.** O id da Gupy só é único dentro da
Gupy. A composta evita colisão quando entrar uma segunda fonte.

**Notificar antes de marcar.** `INSERT ... ON CONFLICT DO NOTHING`, e o `UPDATE
notificada_em = now()` só depois da entrega confirmada.

**Heartbeat diário.** Sem ele, "nenhuma vaga nova" e "o radar morreu" produzem
exatamente o mesmo silêncio.

**Cron com jitter (`7,37`).** 192 requests/dia é volume irrelevante para um endpoint
que o portal público chama de qualquer browser. O que chama atenção é regularidade de
relógio, não volume — daí os minutos quebrados e o `Wait` de 2s entre perfis.

**Segredo fora do git desde o commit inicial.** O `.gitignore` foi o primeiro arquivo
versionado, antes de qualquer credencial existir. Credencial commitada permanece no
histórico, e este repositório é público por design. A chave da Evolution entra no n8n
como credencial Header Auth, nunca como header literal — o export do workflow grava
header literal em texto puro.

## Fora de escopo, e por quê

| Não fiz | Motivo |
|---|---|
| Envio automático de mensagem no LinkedIn | Viola o Contrato do Usuário da plataforma. A conta é meu principal ativo profissional |
| Candidatura automática | A Gupy pontua aderência. Volume genérico piora o resultado, não melhora |
| LinkedIn como segunda fonte | Fase posterior. Abrir agora dobra a superfície de falha antes da primeira estar estável |
| Painel web | O WhatsApp é a interface. Este sistema não tem tela |
| CRM de networking, gerador de mensagem | Fases posteriores, deliberadamente fora deste escopo |

A coluna `status` existe no schema e não é usada. Ela é o gancho da fase de CRM, e
está documentada como tal em vez de removida.

## Stack

`n8n` · `PostgreSQL 15` · `Evolution API` (WhatsApp) · `Python 3.12` · `Docker`

A mesma stack que uso em produção no [recanto-karibe](https://github.com/Germalonso),
reaproveitando os padrões de nó Postgres com `queryReplacement` e cast explícito.

## Estado

Em construção. Ver [EXECUCAO.md](EXECUCAO.md) para o plano de execução, os
pré-requisitos verificados e a definição de pronto.

```
[x] Etapa 0 — coleta e exibição no terminal
[ ] Etapa 1 — entrega no WhatsApp
[ ] Etapa 2 — persistência e deduplicação
[ ] Etapa 3 — orquestração autônoma no n8n
```

---

Alonso Moura Germano · Ciência da Computação, UFU · Uberlândia/MG
