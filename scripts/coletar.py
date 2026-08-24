#!/usr/bin/env python3
"""Radar de Vagas — coletor da API publica da Gupy.

Etapa 0: --dry-run imprime no terminal as vagas que passam no filtro.
Persistencia e notificacao entram nas etapas seguintes.

Requer py -3.12. O python do PATH (msys2) falha com SSLCertVerificationError.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# O stdout do Windows e cp1252 e estoura UnicodeEncodeError em titulo com emoji
# (7 em cada 253 vagas medidas). Precisa vir antes de qualquer print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://employability-portal.gupy.io/api/v1/jobs"
USER_AGENT = "radar-vagas/1.0 (github.com/Germalonso/job-hunt-automation)"
LIMITE_PAGINA = 100  # limit=101 devolve HTTP 400

# Filtro server-side. Quatro perfis substituem 18 buscas por palavra-chave.
# O parametro city exige acento: sem circunflexo a API devolve 0 resultados.
PERFIS = (
    "type=vacancy_type_internship&isRemoteWork=true",
    "type=vacancy_type_internship&city=Uberl%C3%A2ndia",
    "type=vacancy_type_effective&isRemoteWork=true",
    "type=vacancy_type_effective&city=Uberl%C3%A2ndia",
)

# Filtro de texto, aplicado SO no campo name. Em description a medicao deu
# 47% de falso positivo em "dados" e 30% de falso negativo em "suporte".
NIVEL = ("estagio", "estagiario", "estagiaria", "junior", "jr", "trainee")
AREA = ("desenvolvedor", "desenvolvimento", "software", "backend", "frontend",
        "fullstack", "dados", "data", "python", "java", "javascript",
        "automacao", "cloud", "aws", "engenheiro")
EXCLUIR = ("suporte", "aprendiz", "contabilidade", "vendas", "comercial",
           "telemarketing")

IDADE_MAXIMA = timedelta(hours=72)
INTERNSHIP = "vacancy_type_internship"

# Teto de notificacao por ciclo. Mensagem gigante no WhatsApp e ilegivel, e
# 30 vagas de uma vez significa que algo quebrou no filtro, nao que o dia foi bom.
VAGAS_POR_MENSAGEM = 10
MAX_MENSAGENS = 3


def carregar_env(caminho=None):
    """Le o .env sem dependencia externa. Nao sobrescreve variavel ja no ambiente."""
    arquivo = Path(caminho) if caminho else Path(__file__).resolve().parent.parent / ".env"
    if not arquivo.exists():
        return {}
    valores = {}
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valores[chave.strip()] = valor.strip().strip('"').strip("'")
    for chave, valor in valores.items():
        os.environ.setdefault(chave, valor)
    return valores


def config_evolution():
    """Devolve (url, instancia, apikey, destino) ou levanta RuntimeError explicando."""
    carregar_env()
    url = os.environ.get("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
    instancia = os.environ.get("EVOLUTION_INSTANCIA", "")
    apikey = os.environ.get("EVOLUTION_APIKEY", "")
    destino = re.sub(r"\D", "", os.environ.get("WHATSAPP_DESTINO", ""))

    faltando = [nome for nome, valor in
                (("EVOLUTION_INSTANCIA", instancia), ("EVOLUTION_APIKEY", apikey),
                 ("WHATSAPP_DESTINO", destino)) if not valor]
    if faltando:
        raise RuntimeError("faltando no .env: " + ", ".join(faltando)
                           + "  (copie de .env.example)")
    if not (12 <= len(destino) <= 13):
        raise RuntimeError("WHATSAPP_DESTINO deve ter 12 ou 13 digitos no formato "
                           "55DDDNNNNNNNNN; veio com " + str(len(destino)))
    return url, instancia, apikey, destino


def enviar_whatsapp(texto, tentativas=3):
    """POST na Evolution v2: body {number, text}. A v1 usava textMessage aninhado."""
    url, instancia, apikey, destino = config_evolution()
    # A instancia vai na URL e pode conter espaco — precisa de escape.
    endpoint = url + "/message/sendText/" + urllib.parse.quote(instancia, safe="")
    corpo = json.dumps({"number": destino, "text": texto}).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=corpo, method="POST",
        headers={"Content-Type": "application/json", "apikey": apikey})

    for tentativa in range(1, tentativas + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", "replace")[:300]
            # 4xx nao melhora com retry: chave errada ou instancia desconectada.
            if 400 <= erro.code < 500:
                raise RuntimeError("Evolution HTTP " + str(erro.code) + ": " + detalhe)
            if tentativa == tentativas:
                raise RuntimeError("Evolution HTTP " + str(erro.code) + ": " + detalhe)
        except (urllib.error.URLError, TimeoutError) as erro:
            if tentativa == tentativas:
                raise RuntimeError("Evolution inacessivel: " + str(erro))
        time.sleep(2 * tentativa)


def formatar_mensagem(vagas):
    """Monta o texto de uma mensagem. Sem markdown pesado: o WhatsApp so faz *negrito*."""
    linhas = ["*" + str(len(vagas)) + " vaga(s) nova(s)*", ""]
    for vaga in vagas:
        local = vaga["workplace_type"] or "?"
        if vaga["cidade"]:
            local += " · " + vaga["cidade"]
        linhas.append("*" + str(vaga["titulo"]) + "*")
        linhas.append(str(vaga["empresa"]) + " · " + local
                      + " · ha " + idade_legivel(vaga["idade"]))
        if vaga["prazo"]:
            linhas.append("prazo: " + str(vaga["prazo"]))
        linhas.append(str(vaga["url"]))
        linhas.append("")
    return "\n".join(linhas).strip()


def notificar(vagas):
    """Fatia em mensagens e envia. Devolve quantas vagas foram efetivamente enviadas."""
    if not vagas:
        return 0
    lotes = [vagas[i:i + VAGAS_POR_MENSAGEM]
             for i in range(0, len(vagas), VAGAS_POR_MENSAGEM)][:MAX_MENSAGENS]
    enviadas = 0
    for indice, lote in enumerate(lotes, start=1):
        enviar_whatsapp(formatar_mensagem(lote))
        enviadas += len(lote)
        print("  enviada mensagem " + str(indice) + "/" + str(len(lotes))
              + " com " + str(len(lote)) + " vaga(s)")
        if indice < len(lotes):
            time.sleep(2)
    return enviadas


def normalizar(s):
    """NFD + remocao de diacritico + minuscula, para 'Estagio' casar com 'estagio'."""
    txt = unicodedata.normalize("NFD", s or "")
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return txt.lower()


def termos_presentes(titulo_norm, termos):
    """Casa com fronteira de palavra, para 'jr' nao casar dentro de outra palavra."""
    return [t for t in termos if re.search(r"\b" + re.escape(t) + r"\b", titulo_norm)]


def parse_publicada(valor):
    """publishedDate vem em dois formatos: ISO com Z (252/253) e data pura (1/253).
    Sem timezone o Python assume horario local, o que erra em 3h. Forcar UTC."""
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def buscar(querystring, offset=0, tentativas=3):
    url = BASE_URL + "?" + querystring + "&offset=" + str(offset) + "&limit=" + str(LIMITE_PAGINA)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for tentativa in range(1, tentativas + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8")).get("data", [])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as erro:
            if tentativa == tentativas:
                print("  ! perfil falhou apos " + str(tentativas) + " tentativas: " + str(erro),
                      file=sys.stderr)
                return []
            time.sleep(2 * tentativa)
    return []


def avaliar(vaga, agora):
    """Devolve (aprovada, motivo_da_rejeicao, score, termos_que_bateram)."""
    titulo = normalizar(vaga.get("name"))

    # Exclusao tem precedencia sobre area.
    bloqueio = termos_presentes(titulo, EXCLUIR)
    if bloqueio:
        return False, "excluido: " + bloqueio[0], 0, []

    e_estagio = vaga.get("type") == INTERNSHIP
    # Para internship o proprio type ja provou o nivel; so effective exige o termo.
    niveis = termos_presentes(titulo, NIVEL)
    if not e_estagio and not niveis:
        return False, "sem nivel no titulo", 0, []

    areas = termos_presentes(titulo, AREA)
    if not areas:
        return False, "fora da area de TI", 0, []

    publicada = parse_publicada(vaga.get("publishedDate"))
    if publicada is None:
        return False, "sem data de publicacao", 0, []
    idade = agora - publicada
    if idade > IDADE_MAXIMA:
        return False, "antiga (" + str(idade.days) + "d)", 0, []

    prazo = vaga.get("applicationDeadline")
    if prazo and prazo < agora.date().isoformat():
        return False, "prazo encerrado", 0, []

    remoto = bool(vaga.get("isRemoteWork")) or vaga.get("workplaceType") == "remote"
    score = 0
    motivos = []
    if remoto:
        score += 40
        motivos.append("remoto")
    if e_estagio:
        score += 30
        motivos.append("internship")
    if areas:
        score += 20
        motivos.append(areas[0])
    if idade < timedelta(hours=6):
        score += 10
        motivos.append("recente")
    return True, "", score, motivos


def idade_legivel(delta):
    horas = int(delta.total_seconds() // 3600)
    return str(horas) + "h" if horas < 48 else str(horas // 24) + "d"


def coletar(verboso=False):
    agora = datetime.now(timezone.utc)
    vistas = set()
    aprovadas = []
    rejeicoes = {}
    total = 0

    for perfil in PERFIS:
        brutas = buscar(perfil)
        total += len(brutas)
        if verboso:
            print("  " + str(len(brutas)).rjust(3) + " vagas  ·  " + perfil)
        for vaga in brutas:
            chave = ("gupy", str(vaga.get("id")))
            if chave in vistas:  # os perfis se sobrepoem
                continue
            vistas.add(chave)

            ok, rejeicao, score, motivos = avaliar(vaga, agora)
            if not ok:
                rejeicoes[rejeicao] = rejeicoes.get(rejeicao, 0) + 1
                continue

            publicada = parse_publicada(vaga.get("publishedDate"))
            aprovadas.append({
                "id_externo": str(vaga.get("id")),
                "titulo": vaga.get("name"),
                "empresa": vaga.get("careerPageName"),
                "url": vaga.get("jobUrl"),
                "tipo": vaga.get("type"),
                "workplace_type": vaga.get("workplaceType"),
                "cidade": vaga.get("city") or None,
                "publicada_em": publicada,
                "prazo": vaga.get("applicationDeadline"),
                "score": score,
                "motivo": "; ".join(motivos),
                "idade": agora - publicada,
            })
        time.sleep(2)  # o risco nao e volume, e padrao temporal

    aprovadas.sort(key=lambda v: (-v["score"], -v["publicada_em"].timestamp()))
    return aprovadas, total, rejeicoes


def imprimir(vagas, total, rejeicoes):
    print()
    if not vagas:
        print("Nenhuma vaga nova no filtro.")
    for vaga in vagas:
        local = vaga["workplace_type"] or "?"
        if vaga["cidade"]:
            local += " · " + vaga["cidade"]
        prazo = " · prazo " + str(vaga["prazo"]) if vaga["prazo"] else ""
        print("[" + str(vaga["score"]).rjust(3) + "] " + str(vaga["titulo"]))
        print("      " + str(vaga["empresa"]) + " · " + local
              + " · ha " + idade_legivel(vaga["idade"]) + prazo)
        print("      " + str(vaga["url"]))
        print("      motivo: " + vaga["motivo"])
        print()

    print("-" * 62)
    print(str(total) + " coletadas · " + str(len(vagas)) + " aprovadas")
    if rejeicoes:
        pares = sorted(rejeicoes.items(), key=lambda kv: -kv[1])
        detalhe = " · ".join(motivo + ": " + str(n) for motivo, n in pares)
        print("descartadas — " + detalhe)


def main():
    parser = argparse.ArgumentParser(description="Radar de Vagas — coletor da Gupy")
    parser.add_argument("--dry-run", action="store_true",
                        help="coleta e imprime no terminal, sem gravar nem notificar")
    parser.add_argument("--notify", action="store_true",
                        help="coleta e envia as vagas aprovadas no WhatsApp")
    parser.add_argument("--test-notify", action="store_true",
                        help="envia uma mensagem sintetica para validar a entrega")
    args = parser.parse_args()

    if args.test_notify:
        carimbo = datetime.now().strftime("%d/%m %H:%M")
        try:
            enviar_whatsapp("*radar online* · teste de entrega " + carimbo
                            + "\nSe voce esta lendo isto, a Evolution esta entregando.")
        except RuntimeError as erro:
            print("FALHOU: " + str(erro), file=sys.stderr)
            return 1
        print("mensagem de teste enviada.")
        return 0

    if not (args.dry_run or args.notify):
        parser.print_help()
        print("\nEscolha um modo: --dry-run, --notify ou --test-notify.",
              file=sys.stderr)
        return 2

    modo = "notify" if args.notify else "dry-run"
    print("Radar de Vagas — coleta (" + modo + ")\n")

    # Validar credencial ANTES de gastar 4 requests na Gupy.
    if args.notify:
        try:
            config_evolution()
        except RuntimeError as erro:
            print("FALHOU: " + str(erro), file=sys.stderr)
            return 1

    vagas, total, rejeicoes = coletar(verboso=True)
    imprimir(vagas, total, rejeicoes)

    if args.notify:
        if not vagas:
            print("nada a notificar.")
            return 0
        print()
        try:
            enviadas = notificar(vagas)
        except RuntimeError as erro:
            print("FALHOU no envio: " + str(erro), file=sys.stderr)
            return 1
        print(str(enviadas) + " vaga(s) notificada(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
