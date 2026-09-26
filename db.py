import os
from functools import lru_cache
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


class SupabaseConfigurationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    supabase_url = os.environ.get("SUPABASE_URL")
    secret_key = os.environ.get("SUPABASE_SECRET_KEY")
    if not supabase_url or not secret_key:
        raise SupabaseConfigurationError(
            "Configure SUPABASE_URL e SUPABASE_SECRET_KEY no arquivo .env."
        )
    if secret_key.startswith("sb_publishable_"):
        raise SupabaseConfigurationError(
            "SUPABASE_SECRET_KEY precisa ser uma chave secreta server-side; sb_publishable não ignora RLS."
        )
    return create_client(supabase_url, secret_key)
