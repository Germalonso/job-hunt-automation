-- Dossiê de candidatura: cache de currículo, contatos de RH e mensagens.
-- Aplicado depois de 01-schema.sql.
\connect job_hunt_db

-- Cache de currículo personalizado.
--
-- A chave NAO e a vaga. "Estagio Backend Python remoto" na empresa A e na B
-- produzem praticamente o mesmo curriculo — cachear por vaga jogaria fora o
-- reuso exatamente onde ele existe. A chave e um perfil canonico (nivel + area
-- + stack ordenada), combinado com a versao do CV base: editar o CV base muda
-- a versao e invalida o cache inteiro, que e o comportamento desejado.
CREATE TABLE IF NOT EXISTS curriculos_cache (
  fingerprint     TEXT PRIMARY KEY,
  perfil_canonico JSONB NOT NULL,
  cv_markdown     TEXT NOT NULL,
  cv_versao       TEXT NOT NULL,
  modelo          TEXT,
  criado_em       TIMESTAMPTZ DEFAULT now(),
  ultimo_uso      TIMESTAMPTZ DEFAULT now(),
  usos            INT DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_cache_versao ON curriculos_cache (cv_versao);

-- Um dossiê por vaga: o que foi extraído dela e qual currículo a atende.
CREATE TABLE IF NOT EXISTS dossies (
  fonte       TEXT NOT NULL,
  id_externo  TEXT NOT NULL,
  fingerprint TEXT REFERENCES curriculos_cache(fingerprint),
  requisitos  JSONB,
  -- Query pronta para o humano colar no buscador. O sistema NAO acessa o
  -- LinkedIn: fazer isso viola o Contrato do Usuario da plataforma e poe em
  -- risco a conta, que e o ativo profissional que este projeto existe para
  -- valorizar.
  query_busca TEXT,
  status      TEXT DEFAULT 'preparado',
  criado_em   TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (fonte, id_externo),
  FOREIGN KEY (fonte, id_externo) REFERENCES vagas(fonte, id_externo) ON DELETE CASCADE
);

-- Contato de RH, preenchido À MÃO depois que o humano olhou o perfil.
-- Guardar o mínimo: o suficiente para personalizar uma mensagem, nada além.
CREATE TABLE IF NOT EXISTS contatos_rh (
  id           SERIAL PRIMARY KEY,
  empresa      TEXT NOT NULL,
  nome         TEXT,
  cargo        TEXT,
  perfil_url   TEXT,
  -- O que o humano copiou do perfil e considerou relevante. Texto livre:
  -- o sistema nao coleta isso, so recebe.
  notas        TEXT,
  criado_em    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (empresa, nome)
);

CREATE INDEX IF NOT EXISTS idx_contatos_empresa ON contatos_rh (lower(empresa));

-- Rascunhos de mensagem. enviada_em so e preenchido pelo proprio usuario,
-- confirmando que ele enviou com as proprias maos: nada aqui envia nada.
CREATE TABLE IF NOT EXISTS mensagens (
  id          SERIAL PRIMARY KEY,
  fonte       TEXT NOT NULL,
  id_externo  TEXT NOT NULL,
  contato_id  INT REFERENCES contatos_rh(id) ON DELETE SET NULL,
  texto       TEXT NOT NULL,
  modelo      TEXT,
  criado_em   TIMESTAMPTZ DEFAULT now(),
  enviada_em  TIMESTAMPTZ,
  FOREIGN KEY (fonte, id_externo) REFERENCES vagas(fonte, id_externo) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mensagens_vaga ON mensagens (fonte, id_externo);
