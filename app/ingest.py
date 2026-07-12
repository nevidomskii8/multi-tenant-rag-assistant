"""Ingest knowledge-base documents into pgvector.

Pipeline per file: read -> chunk -> embed (e5 `passage:`) -> insert.
Run from the host against the local DB:

    DATABASE_URL=postgresql://app:<pw>@localhost:5432/app python -m app.ingest
"""

import sys
from pathlib import Path

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


def ingest_file(conn, path: Path) -> int:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return 0
    title = text.splitlines()[0].lstrip("# ").strip()
    # Stable, OS-independent key (forward slashes) that matches eval/golden.json.
    # Repo-root-relative by convention — ingest is run from the project root.
    source = path.as_posix()
    chunks = chunk_text(text, tokenizer(), limit=max_seq_length())
    embeddings = embed_passages(chunks)

    with conn.cursor() as cur:
        # Idempotent re-ingest: drop any prior version of this source first
        # (chunks cascade via the FK), then insert fresh.
        cur.execute("DELETE FROM documents WHERE source = %s", (source,))
        cur.execute(
            "INSERT INTO documents (title, source) VALUES (%s, %s) RETURNING id",
            (title, source),
        )
        doc_id = cur.fetchone()[0]
        for idx, (content, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            cur.execute(
                "INSERT INTO chunks (document_id, chunk_index, content, embedding) "
                "VALUES (%s, %s, %s, %s)",
                (doc_id, idx, content, emb),
            )
    conn.commit()
    return len(chunks)


def main() -> None:
    files = sorted(KB_DIR.glob("*.md"))
    if not files:
        print(f"No .md files found in {KB_DIR}/")
        sys.exit(1)

    with get_conn() as conn:
        total = 0
        for path in files:
            n = ingest_file(conn, path)
            total += n
            print(f"  {path.name}: {n} chunks")
    print(f"Ingested {len(files)} document(s), {total} chunk(s) total.")


if __name__ == "__main__":
    main()
