"""Tests for semantic retrieval (fake session + fake LLM, no pgvector)."""

from __future__ import annotations

from trading_intel.memory.retrieval import ChunkHit, format_kb, retrieve_chunks


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.captured = None

    def execute(self, stmt, params):
        self.captured = (str(stmt), params)
        return FakeResult(self._rows)


class FakeLLM:
    def embed(self, text, *, model=None):
        return [[0.1, 0.2, 0.3]]


_ROWS = [
    {
        "chunk_id": 1,
        "document_id": 10,
        "path": "/r/Managing Smile Risk.pdf",
        "text": "skew is the demand for downside protection",
        "distance": 0.12,
    },
    {
        "chunk_id": 2,
        "document_id": 11,
        "path": "/r/trading-volatility.pdf",
        "text": "term structure sets the carry",
        "distance": 0.20,
    },
]


def test_retrieve_maps_rows_and_embeds_query():
    sess = FakeSession(_ROWS)
    hits = retrieve_chunks(sess, FakeLLM(), "what is skew", k=3)
    assert [h.chunk_id for h in hits] == [1, 2]
    assert hits[0].title == "Managing Smile Risk"
    assert hits[0].distance == 0.12
    stmt, params = sess.captured
    assert params["qvec"] == "[0.1,0.2,0.3]"
    assert params["kind"] == "methodology"
    assert params["k"] == 3
    assert "<=> CAST(:qvec AS vector)" in stmt


def test_empty_query_short_circuits():
    sess = FakeSession(_ROWS)
    assert retrieve_chunks(sess, FakeLLM(), "   ") == []
    assert sess.captured is None  # no DB call


def test_symbols_filter_adds_clause_and_param():
    sess = FakeSession(_ROWS)
    retrieve_chunks(sess, FakeLLM(), "nvda thesis", kind="research", symbols=["nvda"])
    stmt, params = sess.captured
    assert "c.symbols &&" in stmt
    assert params["symbols"] == ["NVDA"]
    assert params["kind"] == "research"


def test_format_kb_groups_titles_and_bounds_length():
    assert format_kb([]) == ""
    hits = [
        ChunkHit(1, 10, "Doc A", "first chunk", 0.1),
        ChunkHit(2, 10, "Doc A", "second chunk", 0.2),
        ChunkHit(3, 11, "Doc B", "third chunk", 0.3),
    ]
    kb = format_kb(hits)
    assert kb.count("### Doc A") == 1  # repeated title collapses to one heading
    assert "### Doc B" in kb
    assert "second chunk" in kb


def test_format_kb_truncates_to_max_chars():
    hits = [ChunkHit(i, 1, f"T{i}", "x" * 100, 0.1 * i) for i in range(10)]
    kb = format_kb(hits, max_chars=150)
    assert len(kb) <= 300  # stops early, keeps at least the first block
    assert "T0" in kb
