# Instalação em uma máquina nova

Do zero até o radar rodando sozinho. O caminho todo leva cerca de 20 minutos,
e a única etapa que não dá para automatizar é escanear o QR code do WhatsApp.

## Pré-requisitos

| | Por quê |
|---|---|
| Docker Desktop | Sobe Postgres, Redis, Evolution e n8n |
| Python 3.12+ | Só para o coletor por linha de comando. O n8n não precisa dele |
| Um número de WhatsApp | Recebe as notificações. Consome 1 dos 4 slots de dispositivo vinculado |

No Windows, chame o Python por `py -3.12`. Um Python vindo do msys2 falha com
`SSLCertVerificationError` ao chamar a API da Gupy.

## 1. Clonar e configurar

```bash
git clone https://github.com/Germalonso/job-hunt-automation.git
cd job-hunt-automation
cp .env.example .env
```

Abra o `.env` e preencha:

```
RADAR_PGPASSWORD=<uma senha forte qualquer>
EVOLUTION_APIKEY=<uma chave que você inventa; é ela que protege a Evolution>
WHATSAPP_DESTINO=55DDDNNNNNNNNN
```

Para gerar valores decentes:

```bash
py -3.12 -c "import secrets; print('senha:', secrets.token_urlsafe(24)); print('apikey:', secrets.token_hex(32))"
```

O `.env` está no `.gitignore` desde o commit inicial e não deve ser versionado
em nenhuma circunstância.

## 2. Subir o stack

```bash
docker compose up -d
```

Isso cria quatro containers — `radar-postgres`, `radar-redis`, `radar-evolution`
e `radar-n8n` — em portas que não colidem com outros stacks: **5434**, **8081**
e **5679**.

O schema é criado sozinho na primeira subida, pelos scripts de `db/` montados em
`docker-entrypoint-initdb.d`. Confira:

```bash
docker exec radar-postgres psql -U radar -d job_hunt_db -c "\dt"
```

Devem aparecer `vagas`, `perfis_busca` e `config`, e `perfis_busca` já vem com
os 4 perfis de busca semeados.

> Os scripts de init **só rodam quando o volume é criado**. Se você mudar o
> schema depois, aplique à mão com `docker exec -i radar-postgres psql -U radar
> -d postgres < db/01-schema.sql` — os dois arquivos são idempotentes.

## 3. Parear o WhatsApp

Abra `http://localhost:8081/manager`, entre com a `EVOLUTION_APIKEY` do `.env` e
crie uma instância chamada **`radar`**. O nome importa: é o que está na URL dos
workflows.

Escaneie o QR pelo WhatsApp do celular, em *Aparelhos conectados*. Confirme:

```bash
curl -H "apikey: SUA_CHAVE" http://localhost:8081/instance/fetchInstances
```

O estado precisa ser `open`. Se vier `close`, o pareamento caiu e o QR precisa
ser escaneado de novo.

## 4. Gravar o número de destino

O número **não** vive no JSON do workflow, de propósito: esse arquivo é
versionado num repositório público, e telefone pessoal em repo público fica
indexado para sempre. Ele mora numa tabela de configuração:

```bash
docker exec radar-postgres psql -U radar -d job_hunt_db -c "INSERT INTO config (chave, valor) VALUES ('whatsapp_destino', '55DDDNNNNNNNNN') ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor;"
```

## 5. Testar por linha de comando, antes do n8n

```bash
py -3.12 scripts/coletar.py --dry-run       # vê vagas no terminal
py -3.12 scripts/coletar.py --test-notify   # prova que o WhatsApp entrega
py -3.12 scripts/coletar.py --test-pipeline # prova banco -> WhatsApp inteiro
```

Se o `--test-notify` não chegar no celular, pare aqui: não adianta configurar o
n8n com a entrega quebrada.

## 6. Semear o backfill

```bash
py -3.12 scripts/coletar.py --backfill
```

Grava o estoque atual **já marcado como notificado**. Sem esse passo, o primeiro
ciclo do n8n dispara dezenas de vagas velhas de uma vez.

## 7. Importar os workflows

```bash
docker exec radar-n8n mkdir -p /tmp/wf
docker cp n8n/radar-vagas.json radar-n8n:/tmp/wf/radar-vagas.json
docker cp n8n/radar-vagas-erro.json radar-n8n:/tmp/wf/radar-vagas-erro.json
docker cp n8n/radar-vagas-heartbeat.json radar-n8n:/tmp/wf/radar-vagas-heartbeat.json
docker exec radar-n8n n8n import:workflow --separate --input=/tmp/wf
```

> **Windows + Git Bash:** o MSYS traduz `/tmp/wf` para um caminho Windows antes
> do Docker ver, e o `docker cp` falha com *"Could not find the file"*. Rode
> esses comandos no PowerShell, ou prefixe com `MSYS_NO_PATHCONV=1`.

## 8. Criar as duas credenciais no n8n

Abra `http://localhost:5679`. Os workflows chegam **inativos** e com as
credenciais **não preenchidas** — o JSON versionado nunca carrega segredo.

**Postgres — `job_hunt_db`**

| Campo | Valor |
|---|---|
| Host | `postgres` |
| Port | `5432` |
| Database | `job_hunt_db` |
| User | `radar` |
| Password | o `RADAR_PGPASSWORD` do `.env` |

O host é `postgres`, o nome do serviço na rede do compose — não `localhost`, que
dentro do container aponta para o próprio n8n.

**Header Auth — Evolution**

| Campo | Valor |
|---|---|
| Name | `apikey` |
| Value | a `EVOLUTION_APIKEY` do `.env` |

Header Auth em vez de header escrito no nó: o export do workflow grava valor de
header em texto puro, e este repositório é público.

Depois de criar as duas, abra cada workflow e selecione-as nos nós que pedem
credencial.

## 9. Ativar

Ative os três:

| Workflow | Quando dispara |
|---|---|
| `Radar de Vagas` | Minutos 7 e 37 de cada hora |
| `Radar de Vagas - Erro` | Quando qualquer workflow falha |
| `Radar de Vagas - Heartbeat` | Todo dia às 8h |

O heartbeat existe porque, sem ele, "nenhuma vaga nova hoje" e "o radar morreu na
terça" produzem exatamente o mesmo silêncio.

## Verificação final

Depois de 24 horas rodando:

```bash
docker exec radar-postgres psql -U radar -d job_hunt_db -c "SELECT count(*) FILTER (WHERE criada_em > now() - interval '24 hours') AS coletadas, count(*) FILTER (WHERE notificada_em > now() - interval '24 hours') AS notificadas FROM vagas;"
```

Entre 3 e 40 vagas novas é o esperado. **Zero** significa trigger inativo ou
filtro quebrado; **mais de 100** significa filtro largo demais.

## Botão de pânico

```bash
# parar de notificar, sem perder nada
docker exec radar-postgres psql -U radar -d job_hunt_db -c "UPDATE perfis_busca SET ativo = false;"

# parar tudo, preservando os dados
docker compose stop

# zerar o radar por completo
docker compose down -v
```
