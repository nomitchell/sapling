from sapling.retrieval import SemanticRetriever


class Embeddings:
    def __init__(self, **kwargs):
        self.calls = []

    def passage_embed(self, texts):
        self.calls.append(("passage", texts))
        for text in texts:
            yield [1.0, 0.0] + [0.0] * 382 if "adversarial" in text else [0.0, 1.0] + [0.0] * 382

    def query_embed(self, texts):
        self.calls.append(("query", texts))
        return [[1.0] + [0.0] * 383 for _ in texts]


def test_semantic_ranking_cache_and_invalid_cache_recovery(tmp_path):
    retriever = SemanticRetriever(tmp_path, model_factory=Embeddings)
    rows = [{"id": "irrelevant", "goal": "Training time for neural models"},
            {"id": "relevant", "goal": "Measuring adversarial accuracy under bounded perturbations"}]
    query = "Resistance to deliberately corrupted images"
    result = retriever.rank(query, rows)
    assert result[0]["id"] == "relevant"
    assert result[0]["retrieval"] == "semantic-local"
    assert len(retriever._model.calls) == 2
    assert retriever.rank(query, rows) == result
    assert len(retriever._model.calls) == 2
    # A fresh process can reuse vectors without invoking inference again.
    other = SemanticRetriever(tmp_path, model_factory=Embeddings)
    assert other.rank(query, rows) == result
    assert other._model.calls == []
    for path in (tmp_path / "vectors").glob("*.json"):
        path.write_text("[0]", encoding="utf-8")
    assert other.rank(query, rows) == result
    assert len(other._model.calls) == 2


def test_missing_semantic_model_falls_back_without_repeated_load(tmp_path):
    calls = []
    def unavailable(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("model unavailable")
    retriever = SemanticRetriever(tmp_path, model_factory=unavailable)
    rows = [{"id": "a", "goal": "diffusion robustness"}, {"id": "b", "goal": "protein folding"}]
    assert retriever.rank("diffusion", rows)[0]["id"] == "a"
    assert retriever.rank("diffusion", rows)[0]["retrieval"] == "lexical"
    assert len(calls) == 1
