import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    """
    Estabelece e retorna a conexão com o banco de dados PostgreSQL.
    Utiliza a variável de ambiente 'DATABASE_URL' definida no arquivo .env.
    O 'RealDictCursor' permite que as linhas retornadas atuem como dicionários.
    """
    return psycopg2.connect(
        os.environ.get("DATABASE_URL"),
        cursor_factory=psycopg2.extras.RealDictCursor
    )
