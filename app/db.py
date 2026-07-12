import psycopg
from pgvector.psycopg import register_vector

from app.config import settings


def get_conn() -> psycopg.Connection:
    """Open a psycopg connection with the pgvector type adapter registered.

    register_vector teaches psycopg to convert Python lists / numpy arrays to
    the Postgres `vector` type (and back), so we can pass embeddings directly
    as query parameters.
    """
    conn = psycopg.connect(settings.database_url)
    register_vector(conn)
    return conn
