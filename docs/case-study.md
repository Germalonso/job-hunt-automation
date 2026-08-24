# Radar de Vagas — case study

> Texto pronto para publicação em [alonsogermano.com](https://alonsogermano.com).
> Escrito para ser lido por quem contrata, não por quem já conhece o projeto.

---

## Resumo

Construí um sistema que monitora a API pública da Gupy a cada 30 minutos e me avisa
no WhatsApp quando aparece uma vaga de estágio ou júnior em TI que encaixa no meu
perfil. O objetivo não foi automatizar candidatura — foi atacar a variável que os
dados mostraram ser decisiva: **latência**.

**Stack:** n8n · PostgreSQL · Evolution API · Python 3.12 · Docker
**Código:** [github.com/Germalonso/job-hunt-automation](https://github.com/Germalonso/job-hunt-automation)

---

## O problema, medido

Analisei meu próprio histórico de candidaturas na Gupy, procurando o que separava as
que avançaram das que não.

O padrão não estava no perfil. Estava no relógio:

| Tempo desde a publicação | Propostas concorrentes |
|---|---|
| 4 horas | 7 |
| 18 horas | 67 |

Uma diferença de 14 horas multiplica a concorrência por quase dez. E o meu fluxo até
então — abrir o site quando lembrava — me colocava sistematicamente na segunda linha.

## A solução

Um pipeline agendado que reduz a janela de descoberta de "quando eu lembrar" para
30 minutos:

1. Um cron no n8n dispara nos minutos 7 e 37 de cada hora
2. Quatro perfis de busca consultam a API da Gupy com filtro server-side
3. Um filtro de nível, área e exclusão roda sobre o título normalizado
4. O que passa é gravado no Postgres com deduplicação por chave composta
5. O que ainda não foi entregue vai para o WhatsApp via Evolution API

## Três decisões que definiram o projeto

### Medir antes de construir

A primeira versão do plano usava 18 termos de busca por palavra-chave. Antes de
escrever o coletor, testei os 18 contra a API real: **6 retornavam zero resultados**,
e uma vaga intitulada `Data Engineer (Python) | JR (Remote)` era invisível para todos
eles.

Trocar keywords por quatro querystrings com filtro server-side levou a cobertura de
117 para cerca de 1716 vagas. A medição também revelou que `pagination.total` clampa
no valor de `limit` — um perfil com 1370 vagas responde `total: 100` — o que teria
quebrado a paginação de forma silenciosa.

### Notificar antes de marcar

O desenho ingênuo grava a vaga e depois notifica. Se o WhatsApp falha, a vaga fica
gravada como conhecida e **nunca mais é notificada**: a falha some.

Inverti a fonte da verdade. A vaga entra no banco com `notificada_em NULL`, e esse
campo só é carimbado depois da entrega confirmada. Uma falha de rede devolve a vaga ao
ciclo seguinte automaticamente. O envio também informa *quais* vagas saíram, não
quantas — se a terceira mensagem falha, as duas primeiras não são reenviadas.

### Tratar silêncio como ambíguo

Um sistema que só fala quando encontra algo tem um modo de falha invisível: "nenhuma
vaga hoje" e "o processo morreu na terça" produzem o mesmo silêncio.

Por isso o projeto tem um Error Trigger que avisa quando qualquer workflow quebra, e
um heartbeat diário que reporta o volume das últimas 24 horas mesmo quando esse volume
é zero.

## O que os testes pegaram

Dois bugs só apareceram porque o sistema rodou de verdade, contra dados reais:

**Encoding.** O stdout do Windows usa cp1252 e estoura `UnicodeEncodeError` em títulos
com emoji — 7 em cada 253 vagas coletadas traziam um.

**Formatação vazando.** Títulos da Gupy às vezes terminam com espaço. O WhatsApp só
fecha o marcador de negrito quando o `*` está colado a um caractere não-branco, então
`*título *` chegava com os asteriscos crus na tela. Só ficou visível no print da
primeira notificação real.

O filtro existe em duas linguagens — Python no coletor de linha de comando, JavaScript
no nó do n8n. Escrevi um teste de paridade que roda os dois sobre o mesmo conjunto de
253 vagas reais e compara vaga a vaga, incluindo o score. Sem isso, os dois caminhos
poderiam divergir sem ninguém perceber.

## O que eu deliberadamente não fiz

Um sistema de busca de emprego tem tentações óbvias, e recusar cada uma foi uma
decisão de projeto:

| Não fiz | Motivo |
|---|---|
| Automatizar mensagens no LinkedIn | Viola o Contrato do Usuário da plataforma. Minha conta é meu principal ativo profissional |
| Candidatura automática | A Gupy pontua aderência ao perfil. Volume genérico piora o resultado |
| Painel web | O WhatsApp já é a interface. Uma tela a mais seria trabalho sem usuário |

## Segurança e privacidade

O `.gitignore` foi o primeiro arquivo versionado do repositório, antes de qualquer
credencial existir — segredo commitado permanece no histórico mesmo depois de removido,
e o repositório é público por design.

A chave da Evolution entra no n8n como credencial Header Auth, nunca escrita no nó: o
export do workflow grava valor de header em texto puro. E o número de telefone de
destino mora numa tabela de configuração no banco, não no JSON do workflow. Chave de
API se rotaciona quando vaza; número de telefone, não.

## Resultado

O sistema roda sozinho, com deduplicação verificada, tratamento de falha e
observabilidade. Na primeira execução completa: 253 vagas coletadas, 2 aprovadas pelo
filtro, entregues no WhatsApp em menos de 10 segundos.

O volume baixo não é filtro quebrado — é o corte de idade de 72 horas funcionando. As
vagas de TI existiam no acervo, com 4 a 91 dias de publicação, e o radar as descartou
por serem exatamente o tipo de vaga que já passou da janela útil.

---

*Projeto pessoal, construído em uma tarde, dividido em quatro etapas que entregam algo
verificável cada uma. O código, as decisões e os limites conhecidos estão documentados
no [repositório](https://github.com/Germalonso/job-hunt-automation).*
