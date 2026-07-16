"""Retrieve the most relevant KB chunks for a query, within one room.

Embeds the query (e5 `query:` prefix) and ranks chunks by cosine distance in
pgvector. Runs on a caller-supplied connection so retrieval inherits that
connection's RLS scope: on an `app_rt` request connection the `WHERE room_id`
filter is belt, and RLS membership is suspenders (defense in depth). Debug from
the host against a room (admin connection bypasses RLS):

    DATABASE_URL=postgresql://app:<pw>@localhost:5432/app \\
        python -m app.retrieval <room_id> "how do refunds work?"
"""

import sys

import psycopg

from app.embeddings import embed_query


def retrieve(conn: psycopg.Connection, query: str, k: int = 4, *, room_id: int) -> list[dict]:
    emb = embed_query(query)
    # `<=>` is pgvector's cosine-distance operator; it uses the HNSW index built
    # with vector_cosine_ops. Distance 0 = identical, so 1 - distance reads as a
    # familiar 0..1 similarity score.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.content, d.title, d.source,
                   1 - (c.embedding <=> %s) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.room_id = %s
            ORDER BY c.embedding <=> %s
            LIMIT %s
            """,
            (emb, room_id, emb, k),
        )
        rows = cur.fetchall()
    return [
        {"content": content, "title": title, "source": source, "score": float(score)}
        for content, title, source, score in rows
    ]


def main() -> None:
    if len(sys.argv) < 3:
        print('Usage: python -m app.retrieval <room_id> "your question"')
        sys.exit(1)
    from app.db import get_conn

    room_id = int(sys.argv[1])
    query = " ".join(sys.argv[2:])
    with get_conn() as conn:  # admin: debug helper, bypasses RLS
        for i, hit in enumerate(retrieve(conn, query, room_id=room_id), 1):
            preview = hit["content"][:200] + ("…" if len(hit["content"]) > 200 else "")
            print(f"\n[{i}] {hit['title']}  (score {hit['score']:.3f})")
            print(preview)


if __name__ == "__main__":
    main()
