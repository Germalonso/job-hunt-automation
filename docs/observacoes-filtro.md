# Observações sobre o filtro

Registro de vaga que o filtro classificou de forma discutível. **Não corrigir até a
Etapa 3 terminar** (regra 3 do `CLAUDE.md`): mexer no filtro no meio da construção
impede saber se uma mudança de volume veio do filtro ou da orquestração.

## Primeira execução — 2026-08-24, `--dry-run`

253 coletadas · 2 aprovadas.

O volume baixo **não** é filtro quebrado. As vagas de estágio em TI existiam, mas
todas caíram no corte de 72h — o radar estava olhando um estoque acumulado, não o
fluxo de publicação. Em regime permanente esse cenário não se repete.

### Falso negativo — termo de área ausente

| Título | Termo que faltou |
|---|---|
| `Rocket Lab - Perfis v(dev) - REC \| 2026.2` | `dev` |
| `ESTAGIÁRIO SISTEMA DE MONITORAÇÃO` | `sistema` |
| `Digital Analytics Assistant (Estagio em análise digital)` | `analytics`, `analise` |
| `Estagiário(a) de UX User Experience - Trabalho Remoto` | `ux` — decidir se UX é escopo |

Candidatos a entrar em `AREA` na revisão pós-Etapa 3: `dev`, `sistema`, `analytics`,
`ia`, `inteligencia artificial`. Atenção ao adicionar `dev`: com fronteira de palavra
ele não casa em `desenvolvedor`, mas casa em `dev` isolado — que é o caso desejado.

### Exclusão funcionando como projetado

| Título | Descartado por | Correto? |
|---|---|---|
| `Estágio em Contabilidade: Dados e Inteligência Artificial` | `contabilidade` | Sim — precedência sobre área é o comportamento desejado |
| `PESSOA ESTAGIÁRIA DE DADOS PARA SUPORTE E ATENDIMENTO AO CLIENTE` | `suporte` | Sim |
| `Estágio em Suporte Comercial (Vaga Remota - BH e SP)` | `suporte` | Sim |

### Distribuição dos descartes

```
sem nivel no titulo      174   (dos 200 effective — esperado)
fora da area de TI        43
excluido: vendas          10
excluido: suporte          6
excluido: comercial        4
excluido: contabilidade    2
excluido: aprendiz         1
antiga (3d a 91d)         11
```

`sem nivel no titulo: 174` é o filtro trabalhando: a maioria das vagas `effective`
é pleno ou sênior, e o termo de nível é a única defesa contra elas.
