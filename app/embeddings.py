from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import settings

# e5 models are trained with asymmetric prefixes: documents are embedded as
# "passage: ..." and search queries as "query: ...". Using the wrong prefix
# (or none) measurably degrades retrieval quality.
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    """Load the embedding model once per process (it is ~450MB — expensive)."""
    return SentenceTransformer(settings.embedding_model)


def tokenizer():
    """The model's own tokenizer — used by chunking to measure real token counts."""
    return _model().tokenizer


def max_seq_length() -> int:
    """Max input tokens the model accepts before it silently truncates the rest."""
    return _model().max_seq_length


def embed_passages(texts: list[str]) -> np.ndarray:
    prefixed = [f"{PASSAGE_PREFIX}{t}" for t in texts]
    return _model().encode(prefixed, normalize_embeddings=True)


def embed_query(text: str) -> np.ndarray:
    return _model().encode(f"{QUERY_PREFIX}{text}", normalize_embeddings=True)
