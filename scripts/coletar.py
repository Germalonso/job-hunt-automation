#!/usr/bin/env python3
"""Radar de Vagas — coletor da API publica da Gupy.

Etapa 0: --dry-run imprime no terminal as vagas que passam no filtro.
Persistencia e notificacao entram nas etapas seguintes.

Requer py -3.12. O python do PATH (msys2) falha com SSLCertVerificationError.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
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


COLUNAS = ("fonte", "id_externo", "titulo", "empresa", "url", "tipo",
           "workplace_type", "cidade", "remoto", "publicada_em", "prazo",
           "score", "motivo", "notificada_em", "status")


def config_postgres():
    carregar_env()
    return (os.environ.get("PGCONTAINER", "radar-postgres"),
            os.environ.get("PGUSER", "radar"),
            os.environ.get("PGDATABASE", "job_hunt_db"))


def psql(script, entrada_extra=""):
    """Roda psql dentro do container: nao ha psql no host, e o Postgres do
    projeto-engajamento nao publica porta. O container vem do .env, entao o
    mesmo codigo serve ao stack proprio (radar-postgres)."""
    container, usuario, database = config_postgres()
    comando = ["docker", "exec", "-i", container, "psql", "-U", usuario,
               "-d", database, "-v", "ON_ERROR_STOP=1", "-q", "--no-align",
               "--tuples-only", "--field-separator=\t"]
    processo = subprocess.run(comando, input=(script + entrada_extra),
                              capture_output=True, text=True, encoding="utf-8")
    if processo.returncode != 0:
        raise RuntimeError("psql falhou: " + (processo.stderr or "").strip()[:400])
    return processo.stdout


def _csv_das_vagas(vagas, notificada_agora):
    """CSV e o caminho seguro: titulo com aspas ou virgula nao vira SQL."""
    buffer = io.StringIO()
    escritor = csv.writer(buffer, lineterminator="\n")
    carimbo = datetime.now(timezone.utc).isoformat() if notificada_agora else ""
    for vaga in vagas:
        remoto = vaga["workplace_type"] == "remote" or bool(vaga.get("remoto"))
        escritor.writerow([
            "gupy",
            vaga["id_externo"],
            (vaga["titulo"] or "").strip(),
            vaga["empresa"] or "",
            vaga["url"] or "",
            vaga["tipo"] or "",
            vaga["workplace_type"] or "",
            vaga["cidade"] or "",
            "true" if remoto else "false",
            vaga["publicada_em"].isoformat(),
            vaga["prazo"] or "",
            vaga["score"],
            vaga["motivo"],
            carimbo,
            "backfill" if notificada_agora else "nova",
        ])
    return buffer.getvalue()


def gravar(vagas, notificada_agora=False):
    """INSERT ... ON CONFLICT DO NOTHING. Devolve quantas linhas entraram.

    Sem RETURNING de proposito: quem decide o que notificar e o SELECT por
    notificada_em IS NULL, nao o resultado do INSERT. Assim uma falha de
    entrega no WhatsApp devolve a vaga ao ciclo seguinte."""
    if not vagas:
        return 0
    lista = ", ".join(COLUNAS)
    script = (
        "BEGIN;\n"
        "CREATE TEMP TABLE _entrada (LIKE vagas INCLUDING DEFAULTS)"
        " ON COMMIT DROP;\n"
        "\\copy _entrada (" + lista + ") FROM STDIN WITH (FORMAT csv, NULL '')\n"
    )
    fim = ("\\.\n"
           "INSERT INTO vagas (" + lista + ")\n"
           "SELECT " + lista + " FROM _entrada\n"
           "ON CONFLICT (fonte, id_externo) DO NOTHING;\n"
           "COMMIT;\n")
    antes = contar()
    psql(script + _csv_das_vagas(vagas, notificada_agora) + fim)
    return contar() - antes


def contar():
    return int((psql("SELECT count(*) FROM vagas;") or "0").strip() or 0)


def pendentes(limite=VAGAS_POR_MENSAGEM * MAX_MENSAGENS):
    """O que ainda nao foi entregue, dentro da janela de 72h."""
    saida = psql(
        "SELECT fonte, id_externo, titulo, empresa, url, workplace_type, cidade,"
        " publicada_em, prazo, score, motivo FROM vagas"
        " WHERE notificada_em IS NULL"
        "   AND publicada_em > now() - interval '72 hours'"
        " ORDER BY score DESC, publicada_em DESC LIMIT " + str(int(limite)) + ";")
    agora = datetime.now(timezone.utc)
    linhas = []
    for linha in saida.splitlines():
        if not linha.strip():
            continue
        campo = linha.split("\t")
        if len(campo) < 11:
            continue
        publicada = parse_publicada(campo[7].replace(" ", "T", 1))
        linhas.append({
            "fonte": campo[0], "id_externo": campo[1], "titulo": campo[2],
            "empresa": campo[3], "url": campo[4], "workplace_type": campo[5],
            "cidade": campo[6] or None, "publicada_em": publicada,
            "prazo": campo[8] or None, "score": int(campo[9] or 0),
            "motivo": campo[10],
            "idade": (agora - publicada) if publicada else timedelta(0),
        })
    return linhas


def marcar_notificadas(vagas):
    """UPDATE com WHERE obrigatorio: sem ele, marca a tabela inteira."""
    if not vagas:
        return 0
    valores = ", ".join(
        "('" + v["fonte"].replace("'", "''") + "','"
        + re.sub(r"[^0-9A-Za-z_-]", "", str(v["id_externo"])) + "')"
        for v in vagas)
    psql("UPDATE vagas SET notificada_em = now(), status = 'notificada'"
         " WHERE (fonte, id_externo) IN (" + valores + ");")
    return len(vagas)


def test_pipeline():
    """Prova o caminho inteiro — banco, filtro de pendentes, entrega e carimbo —
    sem depender de a Gupy ter publicado vaga nova. Reversivel: a vaga sintetica
    usa fonte='teste' e e removida no fim, inclusive se o envio falhar."""
    psql("DELETE FROM vagas WHERE fonte = 'teste';")
    psql("INSERT INTO vagas (fonte, id_externo, titulo, empresa, url, tipo,"
         " workplace_type, remoto, publicada_em, score, motivo, status)"
         " VALUES ('teste', 'TESTE', 'Vaga sintetica do teste de pipeline',"
         " 'Radar de Vagas', 'https://example.invalid/teste',"
         " 'vacancy_type_internship', 'remote', true, now(), 100,"
         " 'teste automatizado', 'nova');")
    try:
        fila = [v for v in pendentes() if v["fonte"] == "teste"]
        if not fila:
            print("FALHOU: a vaga sintetica nao apareceu em pendentes()",
                  file=sys.stderr)
            return 1

        entregues = notificar(fila)
        if not entregues:
            print("FALHOU: nada foi entregue", file=sys.stderr)
            return 1

        marcar_notificadas(entregues)
        restante = psql("SELECT count(*) FROM vagas WHERE fonte = 'teste'"
                        " AND notificada_em IS NULL;").strip()
        if restante != "0":
            print("FALHOU: a vaga sintetica seguiu pendente apos o envio",
                  file=sys.stderr)
            return 1
        print("pipeline ok: gravou, listou como pendente, entregou e carimbou.")
        return 0
    finally:
        psql("DELETE FROM vagas WHERE fonte = 'teste';")
        print("vaga sintetica removida.")


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
        # O WhatsApp so fecha o negrito com o * colado a caractere nao-branco.
        # Titulo da Gupy as vezes vem com espaco no fim ("... DEFICIENCIA "),
        # e sem o strip os dois asteriscos vazam como texto literal.
        titulo = str(vaga["titulo"] or "").strip()
        linhas.append(("*" + titulo + "*") if titulo else "(sem titulo)")
        linhas.append(str(vaga["empresa"]) + " · " + local
                      + " · há " + idade_legivel(vaga["idade"]))
        if vaga["prazo"]:
            linhas.append("prazo: " + str(vaga["prazo"]))
        linhas.append(str(vaga["url"]))
        linhas.append("")
    return "\n".join(linhas).strip()


def notificar(vagas):
    """Fatia em mensagens e envia. Devolve a lista das vagas efetivamente
    entregues — so essas podem ser marcadas como notificadas. Se a terceira
    mensagem falha, as duas primeiras continuam marcadas e a falha nao
    reenvia o que ja chegou."""
    if not vagas:
        return []
    lotes = [vagas[i:i + VAGAS_POR_MENSAGEM]
             for i in range(0, len(vagas), VAGAS_POR_MENSAGEM)][:MAX_MENSAGENS]
    entregues = []
    for indice, lote in enumerate(lotes, start=1):
        try:
            enviar_whatsapp(formatar_mensagem(lote))
        except RuntimeError as erro:
            print("  ! mensagem " + str(indice) + " falhou: " + str(erro),
                  file=sys.stderr)
            break
        entregues.extend(lote)
        print("  enviada mensagem " + str(indice) + "/" + str(len(lotes))
              + " com " + str(len(lote)) + " vaga(s)")
        if indice < len(lotes):
            time.sleep(2)
    return entregues


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
    parser.add_argument("--backfill", action="store_true",
                        help="grava o estoque atual como ja notificado, para o "
                             "primeiro ciclo do n8n nao disparar tudo de uma vez")
    parser.add_argument("--test-notify", action="store_true",
                        help="envia uma mensagem sintetica para validar a entrega")
    parser.add_argument("--test-pipeline", action="store_true",
                        help="insere vaga sintetica, notifica e limpa; prova o "
                             "caminho banco -> WhatsApp de ponta a ponta")
    args = parser.parse_args()

    if args.test_pipeline:
        try:
            return test_pipeline()
        except RuntimeError as erro:
            print("FALHOU: " + str(erro), file=sys.stderr)
            return 1

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

    if not (args.dry_run or args.notify or args.backfill):
        parser.print_help()
        print("\nEscolha um modo: --dry-run, --notify, --backfill ou --test-notify.",
              file=sys.stderr)
        return 2

    modo = "backfill" if args.backfill else ("notify" if args.notify else "dry-run")
    print("Radar de Vagas — coleta (" + modo + ")\n")

    # Validar credencial e banco ANTES de gastar 4 requests na Gupy.
    try:
        if args.notify:
            config_evolution()
        if args.notify or args.backfill:
            contar()
    except RuntimeError as erro:
        print("FALHOU: " + str(erro), file=sys.stderr)
        return 1

    vagas, total, rejeicoes = coletar(verboso=True)
    imprimir(vagas, total, rejeicoes)

    if args.dry_run:
        return 0

    if args.backfill:
        # Marca tudo como ja notificado: sem isto, o primeiro ciclo do n8n
        # dispara o estoque inteiro de uma vez.
        novas = gravar(vagas, notificada_agora=True)
        print()
        print(str(novas) + " vaga(s) gravada(s) como backfill · "
              + str(contar()) + " no banco.")
        return 0

    novas = gravar(vagas)
    print()
    print(str(novas) + " vaga(s) nova(s) gravada(s) · " + str(contar()) + " no banco.")

    fila = pendentes()
    if not fila:
        print("nada pendente para notificar.")
        return 0

    entregues = notificar(fila)
    marcar_notificadas(entregues)
    print(str(len(entregues)) + " de " + str(len(fila)) + " pendente(s) notificada(s).")
    return 0 if len(entregues) == len(fila) else 1


if __name__ == "__main__":
    raise SystemExit(main())
