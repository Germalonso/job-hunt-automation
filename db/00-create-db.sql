-- Criacao dos databases. Roda no database padrao da conexao.
--
-- Idempotente de proposito: este script tambem e aplicado a mao, por
-- docker exec, no Postgres compartilhado do projeto-engajamento, onde os
-- databases podem ja existir. CREATE DATABASE nao aceita IF NOT EXISTS,
-- entao o padrao e gerar o comando e executa-lo com \gexec.

SELECT 'CREATE DATABASE job_hunt_db'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'job_hunt_db')\gexec

-- Os dois abaixo servem so ao stack proprio deste repositorio (a Evolution e
-- o n8n do docker-compose.yml). No Postgres do projeto-engajamento eles ja
-- existem, e o \gexec simplesmente nao faz nada.

SELECT 'CREATE DATABASE evolution'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'evolution')\gexec

SELECT 'CREATE DATABASE n8n'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'n8n')\gexec
