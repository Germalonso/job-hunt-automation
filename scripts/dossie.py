#!/usr/bin/env python3
"""Dossiê de candidatura: currículo personalizado com cache, e mensagem para o RH.

O sistema NUNCA acessa o LinkedIn. Ele monta a query de busca, e quem abre o
perfil, avalia e cola o contexto é a pessoa. Acesso automatizado ao LinkedIn
viola o Contrato do Usuário da plataforma e poe em risco justamente a conta que
este projeto existe para valorizar. Enviar mensagem tambem e sempre manual.

Requer py -3.12, o pacote `anthropic` e ANTHROPIC_API_KEY no .env.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coletar import carregar_env, psql  # noqa: E402  (reusa a camada de banco)

MODELO = "claude-opus-5"
RAIZ = Path(__file__).resolve().parent.parent

# Fallback server-side: se um classificador recusar o pedido, a Anthropic
# roteia para outro modelo em vez de devolver erro.
BETAS = ["server-side-fallback-2026-07-01"]

INSTRUCOES_CV = """Você adapta currículos para vagas específicas.

Regras invioláveis:
- NUNCA invente experiência, tecnologia, número ou data que não esteja no
  currículo base. Currículo que não se sustenta na entrevista é pior que
  currículo genérico.
- Você reordena, reescreve e corta. Você não acrescenta fatos.
- Se a vaga pede algo que a pessoa não tem, simplesmente não mencione. Não
  invente proximidade ("familiaridade com...") para tapar buraco.
- Priorize o que a vaga pede: o que é relevante sobe, o que não é desce ou sai.
- Uma página. Português do Brasil. Markdown limpo, sem preâmbulo e sem
  comentário seu — devolva apenas o currículo.
- Resultado medido vale mais que responsabilidade descrita. Se o currículo base
  traz um número, preserve o número."""

INSTRUCOES_MSG = """Você escreve mensagens curtas de primeiro contato com
recrutadores, para um candidato a estágio ou vaga júnior.

Regras:
- No máximo 6 linhas. Recrutador lê no celular, entre reuniões.
- Português do Brasil, cordial e direto. Sem "espero que esteja bem",
  sem "venho por meio desta", sem bajulação.
- Cite UMA conexão concreta entre o candidato e a vaga ou o contexto da pessoa.
  Uma só, a mais forte. Mensagem que lista tudo não conecta nada.
- Só use informação que foi fornecida. Não invente nada sobre a pessoa nem
  sobre o candidato.
- Termine com uma pergunta simples de responder, não com um pedido vago.
- Devolva apenas a mensagem, sem assunto, sem assinatura e sem comentário seu."""


def cliente():
    carregar_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY nao definida. Adicione ao .env "
                           "(veja .env.example)")
    import anthropic
    return anthropic.Anthropic()


def cv_base():
    """Devolve (texto, versao). A versao e o hash do conteudo: editar o CV base
    invalida o cache de curriculos sozinho, sem ninguem precisar lembrar."""
    caminho = RAIZ / "cv-base.md"
    if not caminho.exists():
        raise RuntimeError("cv-base.md nao existe. Rode: "
                           "cp cv-base.exemplo.md cv-base.md  e preencha")
    texto = caminho.read_text(encoding="utf-8").strip()
    if len(texto) < 200:
        raise RuntimeError("cv-base.md parece vazio (%d chars). Preencha antes "
                           "de gerar curriculo." % len(texto))
    return texto, hashlib.sha256(texto.encode("utf-8")).hexdigest()[:12]


def texto_da_resposta(resp):
    """Extrai o texto, tratando recusa antes de ler o conteudo."""
    if getattr(resp, "stop_reason", None) == "refusal":
        detalhe = getattr(resp, "stop_details", None)
        raise RuntimeError("modelo recusou o pedido: "
                           + str(getattr(detalhe, "category", "sem categoria")))
    partes = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    if not partes:
        raise RuntimeError("resposta sem bloco de texto")
    return "\n".join(partes).strip()


ESQUEMA_REQUISITOS = {
    "type": "object",
    "properties": {
        "nivel": {"type": "string",
                  "enum": ["estagio", "junior", "trainee", "pleno", "indefinido"]},
        "area": {"type": "string",
                 "enum": ["backend", "frontend", "fullstack", "dados", "cloud",
                          "qa", "mobile", "seguranca", "outra"]},
        "stack": {"type": "array", "items": {"type": "string"},
                  "description": "tecnologias exigidas, minusculas, sem versao"},
        "diferenciais": {"type": "array", "items": {"type": "string"}},
        "resumo": {"type": "string", "description": "uma frase sobre a vaga"},
    },
    "required": ["nivel", "area", "stack", "diferenciais", "resumo"],
    "additionalProperties": False,
}


def extrair_requisitos(client, vaga):
    """Extrai o perfil da vaga em JSON validado pelo esquema."""
    descricao = re.sub(r"<[^>]+>", " ", vaga.get("descricao") or "")
    descricao = re.sub(r"\s+", " ", descricao)[:6000]
    conteudo = ("Vaga: " + (vaga.get("titulo") or "")
                + "\nEmpresa: " + (vaga.get("empresa") or "")
                + "\nDescricao: " + descricao)
    resp = client.beta.messages.create(
        model=MODELO,
        max_tokens=2000,
        betas=BETAS,
        fallbacks="default",
        output_config={"effort": "low",
                       "format": {"type": "json_schema",
                                  "schema": ESQUEMA_REQUISITOS}},
        system="Extraia o perfil tecnico da vaga. Liste em `stack` apenas o que "
               "a vaga realmente exige, nao o que seria bom saber.",
        messages=[{"role": "user", "content": conteudo}],
    )
    return json.loads(texto_da_resposta(resp))


def fingerprint(requisitos, cv_versao):
    """Chave do cache. NAO e o id da vaga: 'estagio backend python' em duas
    empresas gera o mesmo curriculo, e cachear por vaga jogaria fora o reuso
    justamente onde ele existe. sort_keys garante determinismo."""
    canonico = json.dumps({
        "nivel": (requisitos.get("nivel") or "").lower(),
        "area": (requisitos.get("area") or "").lower(),
        "stack": sorted({str(s).lower().strip() for s in requisitos.get("stack", [])}),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((canonico + "|" + cv_versao).encode("utf-8")).hexdigest()[:16]


def gerar_cv(client, texto_cv, vaga, requisitos):
    """O prefixo estavel (instrucoes + CV base) vai no system com cache_control;
    a vaga, que muda a cada chamada, vem depois do breakpoint."""
    conteudo = ("Adapte o curriculo para esta vaga.\n\n"
                "Titulo: " + (vaga.get("titulo") or "")
                + "\nEmpresa: " + (vaga.get("empresa") or "")
                + "\nNivel: " + str(requisitos.get("nivel"))
                + "\nArea: " + str(requisitos.get("area"))
                + "\nStack exigida: " + ", ".join(requisitos.get("stack", []))
                + "\nDiferenciais: " + ", ".join(requisitos.get("diferenciais", [])))
    resp = client.beta.messages.create(
        model=MODELO,
        max_tokens=16000,
        betas=BETAS,
        fallbacks="default",
        thinking={"type": "adaptive"},
        system=[
            {"type": "text", "text": INSTRUCOES_CV},
            {"type": "text", "text": "CURRICULO BASE:\n\n" + texto_cv,
             "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": conteudo}],
    )
    return texto_da_resposta(resp), resp.usage


def query_rh(empresa):
    """Query para o humano colar no buscador. O sistema nao busca por conta
    propria e nao acessa o LinkedIn."""
    limpa = re.sub(r"\s+", " ", (empresa or "").strip())
    return ('site:linkedin.com/in "' + limpa + '" '
            '(recrutador OR recrutadora OR "talent acquisition" OR '
            '"people" OR "gente e gestao" OR RH)')


def esc(valor):
    """Escapa literal para SQL. Os valores aqui vem de API publica e do proprio
    usuario, entao passam por aspas duplicadas."""
    if valor is None:
        return "NULL"
    return "'" + str(valor).replace("'", "''") + "'"


def buscar_vaga(id_externo):
    saida = psql("SELECT titulo, empresa, url, tipo, workplace_type, cidade "
                 "FROM vagas WHERE fonte = 'gupy' AND id_externo = "
                 + esc(id_externo) + ";")
    linha = [l for l in saida.splitlines() if l.strip()]
    if not linha:
        raise RuntimeError("vaga " + str(id_externo) + " nao esta no banco. "
                           "Rode coletar.py --backfill antes.")
    c = linha[0].split("\t")
    return {"id_externo": str(id_externo), "titulo": c[0], "empresa": c[1],
            "url": c[2], "tipo": c[3], "workplace_type": c[4],
            "cidade": c[5] if len(c) > 5 else "", "descricao": ""}


def cmd_preparar(args):
    client = cliente()
    texto_cv, versao = cv_base()
    vaga = buscar_vaga(args.vaga)
    print("vaga: " + vaga["titulo"] + "  ·  " + vaga["empresa"])

    requisitos = extrair_requisitos(client, vaga)
    print("perfil: %s / %s · stack: %s" % (
        requisitos["nivel"], requisitos["area"],
        ", ".join(requisitos["stack"]) or "(nenhuma explicita)"))

    fp = fingerprint(requisitos, versao)
    existente = psql("SELECT cv_markdown FROM curriculos_cache WHERE fingerprint = "
                     + esc(fp) + ";").strip()

    if existente and not args.forcar:
        psql("UPDATE curriculos_cache SET usos = usos + 1, ultimo_uso = now() "
             "WHERE fingerprint = " + esc(fp) + ";")
        print("curriculo: CACHE HIT (" + fp + ") — nenhuma chamada de geracao")
    else:
        cv, uso = gerar_cv(client, texto_cv, vaga, requisitos)
        psql("INSERT INTO curriculos_cache (fingerprint, perfil_canonico,"
             " cv_markdown, cv_versao, modelo) VALUES ("
             + esc(fp) + ", " + esc(json.dumps(requisitos, ensure_ascii=False))
             + "::jsonb, " + esc(cv) + ", " + esc(versao) + ", " + esc(MODELO)
             + ") ON CONFLICT (fingerprint) DO UPDATE SET cv_markdown = EXCLUDED.cv_markdown,"
             " ultimo_uso = now();")
        print("curriculo: GERADO (" + fp + ")  ·  cache_read=%s cache_write=%s" % (
            getattr(uso, "cache_read_input_tokens", 0),
            getattr(uso, "cache_creation_input_tokens", 0)))
        destino = RAIZ / "saida" / ("cv-" + fp + ".md")
        destino.parent.mkdir(exist_ok=True)
        destino.write_text(cv, encoding="utf-8")
        print("           gravado em " + str(destino.relative_to(RAIZ)))

    q = query_rh(vaga["empresa"])
    psql("INSERT INTO dossies (fonte, id_externo, fingerprint, requisitos, query_busca)"
         " VALUES ('gupy', " + esc(args.vaga) + ", " + esc(fp) + ", "
         + esc(json.dumps(requisitos, ensure_ascii=False)) + "::jsonb, " + esc(q)
         + ") ON CONFLICT (fonte, id_externo) DO UPDATE SET fingerprint = EXCLUDED.fingerprint,"
         " requisitos = EXCLUDED.requisitos, query_busca = EXCLUDED.query_busca;")

    print()
    print("Procure o RH voce mesmo, com esta query:")
    print("  " + q)
    print()
    print("Depois registre quem achou:")
    print("  py -3.12 scripts/dossie.py --contato " + str(args.vaga)
          + " --nome \"Nome\" --cargo \"Cargo\" --notas \"o que viu no perfil\"")
    return 0


def cmd_contato(args):
    vaga = buscar_vaga(args.vaga)
    psql("INSERT INTO contatos_rh (empresa, nome, cargo, perfil_url, notas) VALUES ("
         + esc(vaga["empresa"]) + ", " + esc(args.nome) + ", " + esc(args.cargo)
         + ", " + esc(args.url) + ", " + esc(args.notas) + ") "
         "ON CONFLICT (empresa, nome) DO UPDATE SET cargo = EXCLUDED.cargo,"
         " perfil_url = EXCLUDED.perfil_url, notas = EXCLUDED.notas;")
    print("contato registrado: " + str(args.nome) + " · " + vaga["empresa"])
    print("agora gere a mensagem:")
    print("  py -3.12 scripts/dossie.py --mensagem " + str(args.vaga))
    return 0


def cmd_mensagem(args):
    client = cliente()
    texto_cv, _ = cv_base()
    vaga = buscar_vaga(args.vaga)

    saida = psql("SELECT id, nome, cargo, notas FROM contatos_rh WHERE lower(empresa) = lower("
                 + esc(vaga["empresa"]) + ") ORDER BY criado_em DESC LIMIT 1;")
    linhas = [l for l in saida.splitlines() if l.strip()]
    if not linhas:
        print("nenhum contato registrado para " + vaga["empresa"] + ".",
              file=sys.stderr)
        print("registre com --contato antes de gerar a mensagem.", file=sys.stderr)
        return 1
    c = linhas[0].split("\t")
    contato = {"id": c[0], "nome": c[1], "cargo": c[2],
               "notas": c[3] if len(c) > 3 else ""}

    conteudo = ("Vaga: " + vaga["titulo"] + " na " + vaga["empresa"]
                + "\nModalidade: " + (vaga.get("workplace_type") or "")
                + "\n\nPessoa: " + contato["nome"] + " — " + (contato["cargo"] or "")
                + "\nContexto observado no perfil dela: "
                + (contato["notas"] or "(nada registrado)")
                + "\n\nCandidato (curriculo base):\n" + texto_cv)
    resp = client.beta.messages.create(
        model=MODELO, max_tokens=4000, betas=BETAS, fallbacks="default",
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": INSTRUCOES_MSG,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": conteudo}],
    )
    texto = texto_da_resposta(resp)
    psql("INSERT INTO mensagens (fonte, id_externo, contato_id, texto, modelo) VALUES "
         "('gupy', " + esc(args.vaga) + ", " + str(int(contato["id"])) + ", "
         + esc(texto) + ", " + esc(MODELO) + ");")
    print("-" * 62)
    print(texto)
    print("-" * 62)
    print()
    print("Revise, ajuste e envie VOCE MESMO. O sistema nao envia nada.")
    print("Depois de enviar, marque:")
    print("  py -3.12 scripts/dossie.py --enviada " + str(args.vaga))
    return 0


def cmd_enviada(args):
    psql("UPDATE mensagens SET enviada_em = now() WHERE fonte = 'gupy'"
         " AND id_externo = " + esc(args.vaga) + " AND enviada_em IS NULL;")
    print("marcada como enviada.")
    return 0


def cmd_cache(args):
    print(psql(
        "SELECT fingerprint, perfil_canonico->>'nivel', perfil_canonico->>'area',"
        " usos, left(cv_versao, 8), to_char(ultimo_uso, 'DD/MM HH24:MI')"
        " FROM curriculos_cache ORDER BY usos DESC, ultimo_uso DESC;").strip()
        or "cache vazio.")
    total = psql("SELECT coalesce(sum(usos), 0), count(*) FROM curriculos_cache;").strip()
    if total:
        usos, distintos = total.split("\t")
        economia = int(usos) - int(distintos)
        print()
        print("%s usos sobre %s curriculos distintos — %d geracao(oes) evitada(s)."
              % (usos, distintos, economia))
    return 0


def main():
    p = argparse.ArgumentParser(description="Dossie de candidatura")
    p.add_argument("--preparar", dest="vaga_preparar", metavar="ID",
                   help="extrai requisitos, resolve o curriculo (com cache) e "
                        "monta a query de busca do RH")
    p.add_argument("--contato", dest="vaga_contato", metavar="ID",
                   help="registra o RH que VOCE encontrou")
    p.add_argument("--mensagem", dest="vaga_mensagem", metavar="ID",
                   help="gera o rascunho de mensagem para o contato da vaga")
    p.add_argument("--enviada", dest="vaga_enviada", metavar="ID",
                   help="marca que VOCE enviou a mensagem")
    p.add_argument("--cache", action="store_true", help="mostra o cache de curriculos")
    p.add_argument("--forcar", action="store_true",
                   help="regera o curriculo mesmo com cache hit")
    p.add_argument("--nome"), p.add_argument("--cargo")
    p.add_argument("--url"), p.add_argument("--notas")
    args = p.parse_args()

    try:
        if args.cache:
            return cmd_cache(args)
        if args.vaga_preparar:
            args.vaga = args.vaga_preparar
            return cmd_preparar(args)
        if args.vaga_contato:
            args.vaga = args.vaga_contato
            return cmd_contato(args)
        if args.vaga_mensagem:
            args.vaga = args.vaga_mensagem
            return cmd_mensagem(args)
        if args.vaga_enviada:
            args.vaga = args.vaga_enviada
            return cmd_enviada(args)
    except RuntimeError as erro:
        print("FALHOU: " + str(erro), file=sys.stderr)
        return 1

    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
