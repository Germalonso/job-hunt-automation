# Radar de Vagas — monitoramento automatizado da Gupy

> **Este documento é autossuficiente.** Ele foi escrito para ser lido por uma sessão nova,
> aberta em `D:\job-hunt-automation`, sem acesso à conversa que o originou.
> Destino: salvar como `EXECUCAO.md` na raiz do projeto.

---

## Handoff — o que uma sessão nova precisa saber

**Contexto de execucao:** projeto pessoal de um estudante de Ciencia da Computacao
buscando estagio/junior em TI, em Uberlandia/MG.

**Por que 4 etapas curtas:** cada etapa termina em algo verificavel — vagas na tela,
mensagem no celular, dedup provado, workflow rodando. Plano que exige horas de setup
antes do primeiro resultado visivel tem alta chance de nao ser terminado.

**Ambiente verificado nesta máquina:**

```
⚠ CORRIGIDO EM 24/08/2026. O stack descrito abaixo era o previsto; o que
  realmente roda nesta máquina é outro. Medido, não suposto:

Stack ATIVO: D:\projeto-engajamento  (compose project `projeto-engajamento`)
  projeto-engajamento-postgres-1       porta NAO publicada no host
  projeto-engajamento-evolution-api-1  8080  ·  Evolution v2.3.7
  projeto-engajamento-n8n-1            5678
  projeto-engajamento-redis-1          6379 interno
  projeto-engajamento-ig-checker-1     8000 interno (o radar nao usa)
Postgres: user `engajamento_admin`, db padrao `postgres`. NAO ha porta no
  host — todo acesso por docker exec ou pelo host `postgres` na rede Docker.
  (o plano previa host 5433 e user `evolution`: ambos errados)
Instância Evolution: "teste", state=open  (sem espaço, dispensa o %20)
Evolution v2.3.7 → body {number, text}. A v1 usava textMessage aninhado.
Chave: env AUTHENTICATION_API_KEY do container evolution-api

Stack PARADO, mantido no disco com volumes intactos:
  D:\RECANTO-KARIBE\whatsapp-evolution\docker-compose.yml
psql NÃO está no PATH — todo SQL via docker exec
Python: usar py -3.12. O python do PATH (msys2) falha com SSLCertVerificationError
gh CLI 2.97 instalado
```

**Padrões a reaproveitar (código dele, já funcionando):**

```
D:\RECANTO-KARIBE\n8n\recepcao-hotel-rk.json
  → node Postgres v2.5 executeQuery com $1/queryReplacement e cast $2::timestamptz
  → node HTTP v4.2 para Evolution: POST evolution-api:8080/message/sendText/<INSTANCIA>
    header apikey, body {number, text}
D:\RECANTO-KARIBE\n8n\README.md
  → convenção de pré-requisitos e placeholder de segredo
D:\RECANTO-KARIBE\whatsapp-evolution\init-db\02-hotel-rk-db.sql
  → padrão correto de CREATE DATABASE + \connect
```

**Decisões já tomadas, não reabrir:** pasta `D:\job-hunt-automation`; LLM = Anthropic;
escopo = apenas o radar (CRM, gerador de mensagem e assistente de conteúdo são fases
posteriores); notificação via WhatsApp; nada de automação de envio no LinkedIn.

---

## Contexto

A analise do historico de candidaturas na Gupy mostrou que **o perfil nao e o
gargalo — a velocidade e**:

```
 4h de vaga publicada  →   7 propostas concorrentes
18h                    →  67 propostas concorrentes
```

Sem o radar, a vaga e descoberta abrindo o site, já dentro da janela ruim. O radar reduz isso para
30 minutos.

Objetivo secundário deliberado: o projeto usa a stack de trabalho (n8n, Postgres, API,
WhatsApp) e vira repositório público — atacando a outra lacuna diagnosticada, que é a ausencia
de conteudo tecnico publico no perfil.

**Escopo:** apenas o radar. CRM de networking, gerador de mensagem e assistente de conteúdo
são fases posteriores.

---

## O que a revisão mudou

Dois agentes revisaram a primeira versão e mediram contra a API ao vivo. Cinco correções
estruturais:

| Problema encontrado | Impacto | Correção |
|---|---|---|
| 18 termos de keyword tinham recall catastrófico (6 retornavam zero; `Data Engineer (Python) \|JR (Remote)` era invisível) | Cobertura de 117 vagas em vez de ~1716 | 4 querystrings com filtro **server-side** |
| Sem filtro de idade | Notificaria vagas de **2022** como novas | `publishedDate > now() - 72h` |
| INSERT antes de notificar | Falha do WhatsApp queima a vaga **para sempre** | `notificada_em IS NULL` como fonte da verdade |
| Filtro de texto sem campo definido | `description` gera 47% de falso positivo em "dados" e 30% de falso negativo em "suporte" | Filtrar **só em `name`**, normalizado NFD |
| Zero tratamento de erro | "Nenhuma vaga" e "radar morto" produzem output idêntico | Error Trigger + heartbeat diário |

---

## Arquitetura de coleta (corrigida)

A API da Gupy aceita filtro server-side, o que elimina duas das três camadas de filtro:

```
GET https://employability-portal.gupy.io/api/v1/jobs?<querystring>&offset=0&limit=100
```

**4 perfis de busca substituem os 18 termos:**

```
type=vacancy_type_internship&isRemoteWork=true      →   45 vagas | pág.1 cobre 3,6 anos
type=vacancy_type_internship&city=Uberl%C3%A2ndia   →    8 vagas | pág.1 cobre 81 dias
type=vacancy_type_effective&isRemoteWork=true       → 1370 vagas | pág.1 cobre 74h
type=vacancy_type_effective&city=Uberl%C3%A2ndia    →  293 vagas | pág.1 cobre 329h
```

Cuidados medidos: `city` exige acento (`Uberlandia` → 0 resultados, `Uberlândia` → 392).
`state=MG` → 0; a API quer `state=Minas Gerais`. `limit=101` → HTTP 400.
`pagination.total` **clampa em `limit`** — nunca use para decidir paginação; use
`while len(data) == limit`. Paginar **só no backfill**; no ciclo de 30 min a página 1
cobre 74h, margem de 148x.

**Filtro restante, aplicado só em `name` (nunca em `description`):**

```js
const norm = s => (s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();

nivel    // só exigido quando type = effective; para internship o type já provou
         { estagio, estagiario, estagiaria, junior, jr, trainee }
area     { desenvolvedor, desenvolvimento, software, backend, frontend, fullstack,
           dados, data, python, java, javascript, automacao, cloud, aws, engenheiro }
excluir  { suporte, aprendiz, contabilidade, vendas, comercial, telemarketing }
         // exclusão tem precedência sobre área
idade    publishedDate > now() - 72h
prazo    applicationDeadline IS NULL OR >= current_date
```

`publishedDate` vem em dois formatos (`2026-08-24T15:06:14.924Z` em 596/600 casos e
`2026-08-21` em 4) — o parser precisa forçar UTC quando não há timezone.
Para exibir local use `workplaceType` (sempre preenchido), não `city` (vazio em 141/145
vagas remotas).

---

## Execução em 4 etapas

Ordenadas para entregar resultado visível cedo e testar a dependência mais frágil primeiro.

### Pré-requisitos

```
□ Docker Desktop ABERTO e verde   (verificado: hoje NÃO está rodando)
□ Instância Evolution conectada — o nome esperado pelos workflows e "radar".
  Conferir ANTES:
    curl -H "apikey: <chave>" http://localhost:8080/instance/fetchInstances
    Esperado: state = "open". Se "close", re-parear QR em localhost:8080/manager
□ Python: usar py -3.12 explicitamente. O python do PATH (msys2) falha com
  SSLCertVerificationError na API da Gupy
□ Não há psql no host — todo SQL roda por docker exec
```

### Etapa 0 — Vagas na tela `25 min · sem Docker`

Único objetivo: ver vagas reais no terminal. Se isso funciona, a parte incerta acabou.

```
1. mkdir D:\job-hunt-automation ; git init ; .gitignore (.env, __pycache__, n8n/exports/)
2. Escrever README.md ANTES do código — ele é a spec e é o portfólio
3. scripts/coletar.py --dry-run  → imprime título, empresa, link, idade
4. Repositório: github.com/Germalonso/job-hunt-automation (criado, público, sincronizado)
```

O `git init` + `.gitignore` vêm **antes** de qualquer credencial existir. Segredo commitado
fica permanente no histórico, e o repositório é peça de portfólio — feito para ser visto.

### Etapa 1 — Chega no WhatsApp `20 min`

```
1. Definir WHATSAPP_DESTINO no .env  (55DDDNNNNNNNNN, só dígitos)
2. curl manual para a Evolution mandando "radar online"
3. coletar.py --notify  → manda as vagas encontradas
4. Print da mensagem no celular → docs/
```

Testar o WhatsApp aqui, e não no fim, porque é a dependência que já falhou uma vez neste
ambiente (registrado no README do recanto-karibe).

### Etapa 2 — Persistência e dedup `50 min`

```
1. docker compose up -d postgres redis evolution-api n8n
   ← serviços NOMEADOS. "docker compose up -d" builda backend, frontend e cloudflared,
     e aborta se JWT_SECRET/ADMIN_PASSWORD faltarem no .env
2. docker exec -i postgres psql -U evolution -d postgres < db\00-create-db.sql
   docker exec -i postgres psql -U evolution -d job_hunt_db < db\01-schema.sql
   ← dois arquivos. CREATE DATABASE e CREATE TABLE no mesmo script cria as tabelas
     no database errado. Padrão correto já existe em init-db/02-hotel-rk-db.sql
   ← init-db/ NÃO executa: o volume postgres_data já foi inicializado em abril/2025
3. coletar.py --backfill   → grava tudo com notificada_em = now() e status = 'backfill'
   ← sem isso, o primeiro run do n8n dispara ~80 vagas de uma vez
4. Rodar coletar.py duas vezes: o count não muda
```

Schema:

```sql
-- 00-create-db.sql
CREATE DATABASE job_hunt_db;

-- 01-schema.sql
CREATE TABLE vagas (
  id_externo      TEXT NOT NULL,
  fonte           TEXT NOT NULL DEFAULT 'gupy',
  titulo          TEXT NOT NULL,
  empresa         TEXT,
  url             TEXT,
  tipo            TEXT,
  workplace_type  TEXT,
  cidade          TEXT,
  remoto          BOOLEAN,
  publicada_em    TIMESTAMPTZ,
  prazo           DATE,
  score           INT,
  motivo          TEXT,
  notificada_em   TIMESTAMPTZ,
  status          TEXT DEFAULT 'nova',
  criada_em       TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (fonte, id_externo)   -- evita colisão com LinkedIn na fase 2
);
CREATE INDEX idx_vagas_pendentes ON vagas(notificada_em) WHERE notificada_em IS NULL;
CREATE INDEX idx_vagas_publicada ON vagas(publicada_em DESC);

CREATE TABLE perfis_busca (
  querystring TEXT PRIMARY KEY,
  ativo       BOOLEAN DEFAULT true
);
INSERT INTO perfis_busca (querystring) VALUES
  ('type=vacancy_type_internship&isRemoteWork=true'),
  ('type=vacancy_type_internship&city=Uberl%C3%A2ndia'),
  ('type=vacancy_type_effective&isRemoteWork=true'),
  ('type=vacancy_type_effective&city=Uberl%C3%A2ndia');
```

`score` = 40 remoto + 30 internship + 20 área no título + 10 publicada há menos de 6h.
`motivo` = string dos termos que bateram, ex. `"remoto; internship; dados"`.

### Etapa 3 — n8n rodando sozinho `80 min`

```
[Schedule]  cron 7,37 * * * *   ← jitter; evita bater sempre no minuto cravado
     ▼
[Postgres] SELECT querystring FROM perfis_busca WHERE ativo
     ▼
[Loop Over Items] ← o último node do corpo DEVE reconectar ao próprio Loop,
     │               senão só o primeiro batch roda
     ├─→ [HTTP] GET .../jobs?{{querystring}}&offset=0&limit=100
     │     retryOnFail: true, maxTries: 3, waitBetweenTries: 5000
     │     onError: continueRegularOutput
     │     header User-Agent: radar-vagas/1.0 (github.com/Germalonso/job-hunt-automation)
     ├─→ [Wait] 2s
     └─→ [Code] normaliza NFD, filtra por name, valida contrato, calcula score
     ▼
[Postgres] INSERT ... ON CONFLICT (fonte,id_externo) DO NOTHING   (sem RETURNING)
     ▼
[Postgres] SELECT ... WHERE notificada_em IS NULL
             AND publicada_em > now() - interval '72 hours'
           ORDER BY publicada_em DESC LIMIT 10
     ▼
[HTTP] Evolution sendText  (máx 10 vagas/mensagem, teto 3 mensagens/ciclo)
     ▼
[Postgres] UPDATE vagas SET notificada_em = now()
           WHERE (fonte,id_externo) = ANY($1)     ← o WHERE é obrigatório
```

Workflow auxiliar: **Error Trigger** → Evolution → `"RADAR CAIU: {{error.message}}"`.
Heartbeat diário às 8h: `"radar vivo — X coletadas, Y notificadas em 24h"`. Sem isso,
"silêncio bom" e "silêncio ruim" são indistinguíveis.

Credencial nova no n8n: `Postgres — job_hunt_db`, host `postgres`, porta `5432` (dentro do
Docker), user `evolution`. As credenciais existentes apontam para `hotel_rk_db`.
Casts explícitos no `queryReplacement`: `$1::bigint`, `$::boolean`, `$::timestamptz`,
`$::date` — o padrão já existe no `recepcao-hotel-rk.json`.

Chave da Evolution via credencial **Header Auth** do n8n, nunca header literal: o export do
workflow grava header literal em texto puro, e o repositório é público.

---

## Arquivos

```
D:\job-hunt-automation\
├── README.md              portfólio: problema, dados 4h/18h, arquitetura, decisões, limites
├── EXECUCAO.md            documento de trabalho: pré-requisitos, 4 etapas, erros, DoD
├── .env.example
├── .gitignore             .env, __pycache__/, n8n/exports/
├── db/00-create-db.sql
├── db/01-schema.sql
├── scripts/coletar.py     --dry-run | --notify | --backfill | --test-notify
├── n8n/radar-vagas.json   sanitizado
└── docs/                  print do WhatsApp (etapa 1), print do canvas (etapa 3)
```

O README carrega a parte de justificativa e é escrito na etapa 0 — não é trabalho extra,
é a spec. Repositório de júnior que declara **o que não fez e por quê** é raro e é o que
faz tech lead parar para ler.

---

## Definição de Pronto

```
□ 1  Rodar coletar.py duas vezes: o count da segunda é igual ao da primeira
□ 2  SELECT ... WHERE notificada_em IS NOT NULL GROUP BY (fonte,id_externo)
     HAVING count(*) > 1   → 0 linhas
□ 3  docker exec postgres psql -U evolution -d job_hunt_db -c "\dt" → vagas, perfis_busca
□ 4  Executar só o node Postgres no n8n → retorna os 4 perfis
□ 5  coletar.py --test-notify → mensagem no WhatsApp em menos de 10s
     (insere vaga sintética id_externo='TESTE', reversível)
□ 6  Trocar a URL da Gupy por domínio inválido e executar → workflow termina sem
     erro vermelho, sem notificar, e o Error Trigger avisa no WhatsApp
□ 7  git log -p | grep -iE "apikey|password|@s.whatsapp.net|5534" → nada
□ 8  Amanhã existe ao menos 1 execução automática com status success, e o volume
     de 24h fica entre 3 e 40 vagas novas
     (0 = trigger inativo ou filtro quebrado; >100 = filtro largo demais)
```

---

## Fora de escopo, e as tentações reais

```
✗ Envio automático no LinkedIn      viola o Contrato do Usuário; a conta e um
                                     ativo profissional central
✗ Candidatura automática             a Gupy pontua aderência; volume genérico piora
✗ LinkedIn como segunda fonte        não abrir nesta fase, nem "só para testar"
✗ Painel web                         o WhatsApp é a interface. Não tem tela
✗ Ajustar o filtro no meio           congelado até a etapa 3 terminar. Vaga errada
                                     que chegar, anota — não conserta agora
✗ Coluna status (nova/vista/...)     criada, não usada. Interface é a fase de CRM
```

**Regra de parada:** se uma etapa passar de 2x a estimativa, commitar o que funciona e
seguir para a próxima. Radar com 2 perfis funcionando vale mais que radar com 4 pela metade.

**Botão de pânico:** desativar o workflow no n8n, ou
`UPDATE perfis_busca SET ativo = false;`. Para zerar: `DROP DATABASE job_hunt_db;` — nada
mais é afetado, o radar não toca em `evolution`, `n8n` nem `hotel_rk_db`.

---

## Volume de requests

4 perfis × 48 execuções/dia = **192 requests/dia**. Medido: 20 requests em rajada
retornaram 20× HTTP 200, sem nenhum header de rate limit — é o mesmo endpoint que o portal
público da Gupy chama de qualquer browser. O risco não é volume, é padrão temporal: por
isso `Wait` de 2s no loop e cron com jitter.

Opcional, se quiser reduzir: schedule das 07h às 22h = 120 req/dia. Vaga publicada às 3h
não muda nada.
