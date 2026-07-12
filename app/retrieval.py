"""Retrieve the most relevant KB chunks for a query.

Embeds the query (e5 `query:` prefix) and ranks chunks by cosine distance in
pgvector. Try it from the host:

    DATABASE_URL=postgresql://app:<pw>@localhost:5432/app \\
        python -m app.retrieval "how do refunds work?"
"""

import sys

from app.db import get_conn
from app.embeddings import embed_query


def retrieve(query: str, k: int = 4) -> list[dict]:
    emb = embed_query(query)
    # `<=>` is pgvector's cosine-distance operator; it uses the HNSW index built
    # with vector_cosine_ops. Distance 0 = identical, so 1 - distance reads as a
    # familiar 0..1 similarity score.
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.content, d.title, d.source,
                   1 - (c.embedding <=> %s) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            ORDER BY c.embedding <=> %s
            LIMIT %s
            """,
            (emb, emb, k),
        )
        rows = cur.fetchall()
    return [
        {"content": content, "title": title, "source": source, "score": float(score)}
        for content, title, source, score in rows
    ]


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m app.retrieval "your question"')
        sys.exit(1)
    query = " ".join(sys.argv[1:])
    for i, hit in enumerate(retrieve(query), 1):
        preview = hit["content"][:200] + ("…" if len(hit["content"]) > 200 else "")
        print(f"\n[{i}] {hit['title']}  (score {hit['score']:.3f})")
        print(preview)


if __name__ == "__main__":
    main()