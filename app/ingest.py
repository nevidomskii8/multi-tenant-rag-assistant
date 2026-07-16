"""Ingest knowledge-base documents into a room.

Pipeline per document: text -> chunk -> embed (e5 `passage:`) -> insert, with
`room_id` stamped on both the document and every chunk (denormalized so RLS can
filter chunks without a join). Two entry points, split by trust:

- **Operator seed** — the CLI below, run on the admin connection (`app`, bypasses
  RLS). For synthetic KB / fixtures / the eval room:

      DATABASE_URL=postgresql://app:<pw>@localhost:5432/app \\
          python -m app.ingest --room <room_id> [files...]

- **User writer** — `ingest_text(conn, ...)` called on the request path through
  `session_for_user` (`app_rt`), so RLS's WITH CHECK gates the write to the
  member's own room. See `POST /rooms/{id}/documents`.

Helpers here do NOT commit — the caller owns the transaction (the CLI commits
per file; the request path commits when `session_for_user` exits, keeping the
`app.user_id` identity set across the inserts).
"""

import argparse
from pathlib import Path

import psycopg

from app.db import get_conn
from app.embeddings import (
    PASSAGE_PREFIX,
    embed_passages,
    max_seq_length,
    tokenizer,
)

KB_DIR = Path("data/kb")


def chunk_text(
    text: str,
    tok,
    *,
    size: int = 256,
    overlap: int = 32,
    limit: int = 512,
    prefix: str = PASSAGE_PREFIX,
) -> list[str]:
    """Split text into overlapping windows measured in *tokens*, not words.

    Word counts are an unreliable proxy for token counts on multilingual text:
    the e5 tokenizer fragments Cyrillic into far more subword tokens per word
    than Latin, so a fixed word window can silently overflow the model's input
    limit on ru/uk text. Measuring with the model's own tokenizer keeps every
    chunk within budget regardless of language.

    `size`/`overlap` are token counts. Overlap keeps a sentence straddling a
    boundary retrievable from either side. The final chunk is embedded as
    `prefix + chunk` (+ 2 special tokens); we assert that total stays within
    `limit` so truncation fails loudly instead of quietly dropping the tail.
    """
    ids = tok.encode(text, add_special_tokens=False)
    if not ids:
        return []
    # prefix + [CLS]/[SEP] overhead that rides along with every embedded chunk.
    overhead = len(tok.encode(prefix, add_special_tokens=True))
    step = size - overlap
    chunks: list[str] = []
    for start in range(0, len(ids), step):
        window = ids[start : start + size]
        chunk = tok.decode(window, skip_special_tokens=True).strip()
        total = overhead + len(tok.encode(chunk, add_special_tokens=False))
        if total > limit:
            raise ValueError(f"chunk of {total} tokens exceeds model limit {limit}; lower `size`")
        chunks.append(chunk)
        if start + size >= len(ids):
            break
    return chunks


def _persist(conn: psycopg.Connection, title: str, source: str, text: str, room_id: int) -> int:
    """Chunk + embed + upsert one document into a room. Does not commit."""
    chunks = chunk_text(text, tokenizer(), limit=max_seq_length())
    if not chunks:
        return 0
    embeddings = embed_passages(chunks)
    with conn.cursor() as cur:
        # Idempotent per (source, room): drop any prior version in THIS room
        # (chunks cascade via the composite FK), then insert fresh.
        cur.execute("DELETE FROM documents WHERE source = %s AND room_id = %s", (source, room_id))
        cur.execute(
            "INSERT INTO documents (title, source, room_id) VALUES (%s, %s, %s) RETURNING id",
            (title, source, room_id),
        )
        doc_id = cur.fetchone()[0]
        for idx, (content, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            cur.execute(
                "INSERT INTO chunks (document_id, chunk_index, content, embedding, room_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (doc_id, idx, content, emb, room_id),
            )
    return len(chunks)


def ingest_text(
    conn: psycopg.Connection,
    title: str,
    text: str,
    room_id: int,
    *,
    source: str | None = None,
) -> int:
    """Ingest a raw text document (e.g. an upload). Does not commit."""
    text = text.strip()
    if not text:
        return 0
    return _persist(conn, title, source or title, text, room_id)


def ingest_file(conn: psycopg.Connection, path: Path, room_id: int) -> int:
    """Ingest a KB file. Title = first line; source = repo-relative posix path.
    Does not commit."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return 0
    title = text.splitlines()[0].lstrip("# ").strip()
    # Stable, OS-independent key (forward slashes) that matches eval/golden.json.
    source = path.as_posix()
    return _persist(conn, title, source, text, room_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest KB documents into a room (operator seed).")
    parser.add_argument("--room", type=int, required=True, help="target room id")
    parser.add_argument("files", nargs="*", help="markdown files (default: data/kb/*.md)")
    args = parser.parse_args()

    files = [Path(f) for f in args.files] if args.files else sorted(KB_DIR.glob("*.md"))
    if not files:
        parser.error(f"no files given and none found in {KB_DIR}/")

    with get_conn() as conn:  # admin: seeding is a privileged operation
        total = 0
        for path in files:
            n = ingest_file(conn, path, args.room)
            conn.commit()  # per-file: a later failure keeps earlier files
            total += n
            print(f"  {path.name}: {n} chunks")
    print(f"Ingested {len(files)} document(s), {total} chunk(s) into room {args.room}.")


if __name__ == "__main__":
    main()
