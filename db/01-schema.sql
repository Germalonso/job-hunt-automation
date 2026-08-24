-- Schema do radar. O \connect e obrigatorio: sem ele as tabelas vao para o
-- database padrao da conexao, nao para job_hunt_db.
\connect job_hunt_db

CREATE TABLE IF NOT EXISTS vagas (
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
  -- Fonte da verdade da notificacao. Fica NULL ate a entrega ser confirmada,
  -- para que uma falha no WhatsApp devolva a vaga ao ciclo seguinte em vez
  -- de queima-la para sempre.
  notificada_em   TIMESTAMPTZ,
  -- Criada e nao usada nesta fase. E o gancho do CRM, documentado como tal.
  status          TEXT DEFAULT 'nova',
  criada_em       TIMESTAMPTZ DEFAULT now(),
  -- Composta porque o id da Gupy so e unico dentro da Gupy. Evita colisao
  -- quando entrar uma segunda fonte.
  PRIMARY KEY (fonte, id_externo)
);

-- Indice parcial: o ciclo de 30 min so pergunta por notificada_em IS NULL,
-- que e uma fracao minima da tabela.
CREATE INDEX IF NOT EXISTS idx_vagas_pendentes
    ON vagas (notificada_em) WHERE notificada_em IS NULL;
CREATE INDEX IF NOT EXISTS idx_vagas_publicada
    ON vagas (publicada_em DESC);

CREATE TABLE IF NOT EXISTS perfis_busca (
  querystring TEXT PRIMARY KEY,
  ativo       BOOLEAN DEFAULT true
);

-- Configuracao lida em tempo de execucao pelo workflow do n8n.
--
-- O numero de destino mora AQUI, e nao no JSON do workflow, de proposito: o
-- JSON e versionado em repositorio publico, e telefone pessoal em repo publico
-- nao se rotaciona como uma chave — fica indexado para sempre.
-- Preencher a mao, uma vez, e nunca versionar o valor:
--
--   INSERT INTO config (chave, valor) VALUES ('whatsapp_destino', '55DDDNNNNNNNNN')
--     ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor;
CREATE TABLE IF NOT EXISTS config (
  chave TEXT PRIMARY KEY,
  valor TEXT NOT NULL
);

-- Filtro server-side da Gupy. city exige o acento: sem ele a API devolve zero.
INSERT INTO perfis_busca (querystring) VALUES
  ('type=vacancy_type_internship&isRemoteWork=true'),
  ('type=vacancy_type_internship&city=Uberl%C3%A2ndia'),
  ('type=vacancy_type_effective&isRemoteWork=true'),
  ('type=vacancy_type_effective&city=Uberl%C3%A2ndia')
ON CONFLICT (querystring) DO NOTHING;
