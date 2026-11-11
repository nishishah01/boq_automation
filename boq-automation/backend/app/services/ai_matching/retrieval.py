"""Stage 3 retrieval: lexical + embedding search over FAISS, per Cost Sheet Type.

The default embedder is LSA (TF-IDF + truncated SVD). It runs offline and needs no model
download. Set EMBEDDING_BACKEND=sentence-transformers to use a neural embedder instead;
nothing else changes.
"""
import numpy as np
import faiss
from scipy.sparse import hstack
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from app.core.config import get_settings
from app.services.item_master.normalize import canon, extract_models, norm_model, search_terms
from app.services.item_master.processor import Component


class LsaEmbedder:
    def __init__(self, dims: int = 256):
        self.dims = dims
        self.svd: TruncatedSVD | None = None

    def fit(self, matrix) -> np.ndarray:
        n = min(self.dims, min(matrix.shape) - 1)
        if n < 2:
            return normalize(matrix.toarray().astype("float32"))
        self.svd = TruncatedSVD(n_components=n, random_state=7)
        return normalize(self.svd.fit_transform(matrix)).astype("float32")

    def encode(self, matrix) -> np.ndarray:
        if self.svd is None:
            return normalize(matrix.toarray().astype("float32"))
        return normalize(self.svd.transform(matrix)).astype("float32")


class ComponentIndex:
    def __init__(self, components: list[Component]):
        self.components = components
        self.model_map: dict[str, list[int]] = {}
        for i, c in enumerate(components):
            for m in c.attrs.models:
                self.model_map.setdefault(m, []).append(i)
        docs = [c.search_text for c in components] or [""]
        self.word = TfidfVectorizer(analyzer=search_terms, sublinear_tf=True)
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), lowercase=False, sublinear_tf=True)
        self.word_m = self.word.fit_transform(docs)
        self.char_m = self.char.fit_transform(docs)
        self.neural = None
        if get_settings().embedding_backend == "sentence-transformers":
            from sentence_transformers import SentenceTransformer
            self.neural = SentenceTransformer(get_settings().embedding_model)
            vectors = self.neural.encode(docs, normalize_embeddings=True).astype("float32")
        else:
            self.lsa = LsaEmbedder()
            vectors = self.lsa.fit(hstack([self.word_m, self.char_m]).tocsr())
        self.faiss = faiss.IndexFlatIP(vectors.shape[1])
        self.faiss.add(vectors)

    def search(self, query: str, k: int = 40) -> list[tuple[Component, float]]:
        """Top candidates with a similarity in 0..1. Exact model hits always make the list."""
        if not self.components:
            return []
        text = canon(query)
        qw, qc = self.word.transform([text]), self.char.transform([text])
        lexical = 0.65 * (self.word_m @ qw.T).toarray().ravel() + 0.35 * (self.char_m @ qc.T).toarray().ravel()
        if self.neural is not None:
            qv = self.neural.encode([text], normalize_embeddings=True).astype("float32")
        else:
            qv = self.lsa.encode(hstack([qw, qc]).tocsr())
        n = len(self.components)
        sims, ids = self.faiss.search(qv, min(n, max(k * 3, 100)))
        semantic = np.zeros(n)
        semantic[ids[0]] = np.clip(sims[0], 0, 1)
        blended = 0.6 * lexical + 0.4 * semantic
        order = list(np.argsort(-blended)[:k])
        for m in extract_models(text):
            for i in self.model_map.get(norm_model(m), []):
                if i not in order:
                    order.append(i)
        return [(self.components[i], float(blended[i])) for i in order]

    def by_models(self, models: set[str]) -> list[Component]:
        """Every record whose brief, description or Model column states one of these models."""
        seen, out = set(), []
        for m in models:
            for i in self.model_map.get(norm_model(m), []):
                if i not in seen:
                    seen.add(i)
                    out.append(self.components[i])
        return out

    def by_item_nos(self, item_nos: set[str]) -> list[Component]:
        return [c for c in self.components if c.item_no in item_nos]


class MasterIndexes:
    """One FAISS index per Cost Sheet Type, so filtering happens before any search."""

    def __init__(self, components: list[Component]):
        self.components = components
        self.by_no = {c.item_no: c for c in components}
        self.by_type: dict[str, list[Component]] = {}
        for c in components:
            self.by_type.setdefault(c.cost_sheet_type, []).append(c)
        self._indexes: dict[str, ComponentIndex] = {}
        # Every model the master states anywhere, for recognising models written with spaces.
        self.models: set[str] = {m for c in components for m in c.attrs.models}

    def index(self, cost_sheet_type: str) -> ComponentIndex:
        if cost_sheet_type not in self._indexes:
            self._indexes[cost_sheet_type] = ComponentIndex(self.by_type.get(cost_sheet_type, []))
        return self._indexes[cost_sheet_type]
