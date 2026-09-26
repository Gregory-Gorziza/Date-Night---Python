# Date Night

Bem-vindo ao repositório do **Date Night**!

## 🗄️ Banco de Dados (Supabase)
Para acessar e gerenciar o banco de dados deste projeto, clique no link abaixo:
👉 [**Acessar Dashboard do Supabase**](https://supabase.com/dashboard/project/uyvaoyopnyzbtainfvte)

*(Lembre-se de verificar se o projeto está ativo caso ocorra erro de conexão no código).*

## 🚀 Como executar o projeto localmente
1. Crie `.env` a partir de `.env.example` e configure `SUPABASE_URL`, `SUPABASE_SECRET_KEY` e `SECRET_KEY`. O Flask lê `.env`, não `.env.local`.
2. Execute `schema_postgresql.sql` no SQL Editor do Supabase. Faça backup antes se o banco já tiver dados; a migração cria campos ausentes e um índice único de e-mail.
3. Dê dois cliques em `run.bat`. O script cria o ambiente virtual e instala as dependências.
4. Acesse [http://localhost:5000](http://localhost:5000).

O projeto Java original usa JDBC/MySQL; esta versão Flask usa a API REST do Supabase. `schema_postgresql.sql` continua sendo a migração do schema no Supabase.

No painel Supabase, crie uma chave secreta server-side para `SUPABASE_SECRET_KEY`. Não use a chave `sb_publishable` como chave do servidor: ela respeita RLS e não dá ao app Flask, que tem login próprio, acesso administrativo. Nunca envie nem versione a chave secreta.

O cadastro aceita até cinco fotos (5 MB cada). As imagens são convertidas para JPEG e salvas em `instance/uploads`, fora da pasta pública; faça backup dessa pasta junto ao banco. Em hospedagem, use um volume persistente ou migre para armazenamento de objetos.

Para testes locais, coloque uma foto JPG, PNG ou WebP em `teste/`. O login mostra um botão local para gerar 100 perfis demonstrativos; ao gerar outro lote, o lote de teste anterior é substituído.

## 🛠️ Tecnologias Utilizadas
- **Python** (Flask)
- **PostgreSQL** (Supabase)
- **HTML/CSS** (Interface)



Itens gerados com ia IA:
Melhorar frases da descricao
verificar erros
criacao dos templates
auxilio transicao de c# para python