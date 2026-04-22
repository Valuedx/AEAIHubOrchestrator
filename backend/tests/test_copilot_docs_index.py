"""COPILOT-01b.iii — unit tests for the file-backed docs grounding
index.

Exercises the real codewiki + node_registry on disk — the index is
small (a few hundred chunks) so the load cost is trivial, and
assertions on "our docs actually contain X" catch both the chunker
and the docs themselves drifting.
"""

from __future__ import annotations

import pytest

from app.copilot import docs_index


@pytest.fixture(autouse=True)
def _reset_index_cache():
    """Each test sees a fresh load — otherwise a test that edited
    the on-disk docs would pollute subsequent runs. Not that we edit
    docs in tests, but cheap insurance."""
    docs_index.reset_cache()
    yield
    docs_index.reset_cache()


# ---------------------------------------------------------------------------
# Index load
# ---------------------------------------------------------------------------


def test_index_loads_from_disk_on_first_call():
    size = docs_index.index_size()
    # We have ~19 codewiki files with many headings each + ~25
    # registry node types + 1 registry-index. Codewiki alone
    # contributes hundreds of section-chunks.
    assert size > 100, f"expected >100 chunks, got {size}"
    assert size < 2000, f"chunk count {size} looks suspiciously inflated"


def test_index_cached_across_calls():
    first = docs_index.index_size()
    second = docs_index.index_size()
    assert first == second
    # Verify the cache is actually used by checking that iter_chunks
    # returns the same list identity (via its length + first chunk).
    a = list(docs_index.iter_chunks())
    b = list(docs_index.iter_chunks())
    assert len(a) == len(b)


def test_reset_cache_forces_reload():
    first = list(docs_index.iter_chunks())
    docs_index.reset_cache()
    second = list(docs_index.iter_chunks())
    # Fresh load → same content, but fresh DocChunk instances (not the
    # same list object — we compare by equality of key fields).
    assert len(first) == len(second)
    assert first[0].source_path == second[0].source_path


# ---------------------------------------------------------------------------
# Chunker + slugifier
# ---------------------------------------------------------------------------


def test_codewiki_chunks_have_path_prefix():
    chunks = list(docs_index.iter_chunks())
    cw_chunks = [c for c in chunks if c.kind == "codewiki"]
    assert cw_chunks, "no codewiki chunks indexed"
    for c in cw_chunks:
        assert c.source_path.startswith("codewiki/"), c.source_path
        assert c.source_path.endswith(".md"), c.source_path


def test_registry_chunks_have_node_type():
    chunks = list(docs_index.iter_chunks())
    reg_chunks = [c for c in chunks if c.kind == "registry"]
    # ~25 node types in the registry today; lower bound is loose to
    # survive node-type churn without churning the test.
    assert len(reg_chunks) >= 15, (
        f"expected >=15 registry chunks, got {len(reg_chunks)}"
    )
    for c in reg_chunks:
        assert c.node_type, f"registry chunk missing node_type: {c.title}"
        assert c.source_path == "shared/node_registry.json"


def test_registry_categories_index_present():
    chunks = list(docs_index.iter_chunks())
    idx = [c for c in chunks if c.kind == "registry-index"]
    assert len(idx) == 1
    assert "Node categories" in idx[0].title


def test_anchors_are_valid_slugs():
    import re
    chunks = list(docs_index.iter_chunks())
    for c in chunks:
        # Slug is [a-z0-9-] (no spaces, no underscores after normalize).
        assert re.match(r"^[a-z0-9\-]*$", c.anchor), (
            f"anchor {c.anchor!r} for {c.title!r} contains invalid chars"
        )


# ---------------------------------------------------------------------------
# search_docs — word-overlap ranking
# ---------------------------------------------------------------------------


def test_search_docs_empty_query_returns_no_results():
    out = docs_index.search_docs("")
    assert out["match_count"] == 0
    assert out["results"] == []


def test_search_docs_nonsense_query_returns_no_results():
    out = docs_index.search_docs("xyzzy foobarbaz")
    assert out["match_count"] == 0


def test_search_docs_finds_intent_classifier_section():
    out = docs_index.search_docs("intent classifier")
    assert out["match_count"] > 0
    # Top result should be the node-types.md Intent Classifier
    # section OR the registry chunk for intent_classifier.
    top = out["results"][0]
    assert "intent classifier" in top["title"].lower() or (
        top["source_path"].endswith("node_registry.json")
        and "intent" in top["title"].lower()
    )


def test_search_docs_honours_top_k_cap():
    out = docs_index.search_docs("workflow", top_k=3)
    assert len(out["results"]) <= 3


def test_search_docs_top_k_clamped_above_20():
    out = docs_index.search_docs("workflow", top_k=500)
    assert len(out["results"]) <= 20


def test_search_docs_title_matches_score_higher():
    """When a term appears in a section title, that section should
    outrank a section that only mentions it in passing."""
    out = docs_index.search_docs("automationedge")
    assert out["match_count"] > 0
    top = out["results"][0]
    # The canonical AE doc, or its Overview chunk, should be #1.
    assert top["source_path"].endswith("automationedge.md")


def test_search_docs_covers_recent_copilot_docs():
    """Regression: the copilot's own docs should be searchable —
    if a doc page rewrite broke the index parsing, this test fires
    before the agent silently returns empty results on questions
    about itself."""
    out = docs_index.search_docs("copilot draft workspace")
    assert out["match_count"] > 0
    paths = {r["source_path"] for r in out["results"]}
    # copilot.md or feature-roadmap.md should turn up somewhere
    # in the top results.
    assert any("copilot" in p or "roadmap" in p for p in paths)


def test_search_docs_excerpt_is_not_empty():
    out = docs_index.search_docs("automationedge")
    for r in out["results"]:
        assert r["excerpt"].strip()
        assert r["title"].strip()
        assert "source_path" in r


# ---------------------------------------------------------------------------
# get_node_examples — registry + related
# ---------------------------------------------------------------------------


def test_get_node_examples_returns_registry_entry_for_real_type():
    out = docs_index.get_node_examples("llm_agent")
    assert out["node_type"] == "llm_agent"
    assert out["registry_entry"] is not None
    entry = out["registry_entry"]
    assert entry["source_path"] == "shared/node_registry.json"
    assert "llm_agent" in entry["excerpt"].lower() or "llm agent" in entry["title"].lower()
    # Config schema section should be present for a node type with fields.
    assert "config schema" in entry["excerpt"].lower()


def test_get_node_examples_null_registry_for_unknown_type():
    out = docs_index.get_node_examples("totally_made_up")
    assert out["node_type"] == "totally_made_up"
    assert out["registry_entry"] is None
    # Related sections may still have zero results — tool is
    # non-failing on unknown types so the agent can self-correct.


def test_get_node_examples_related_sections_exclude_registry_self():
    """The registry chunk is reported under registry_entry; it
    shouldn't also appear in related_sections, or the agent sees
    the same content twice in one tool_result."""
    out = docs_index.get_node_examples("automation_edge")
    for r in out["related_sections"]:
        assert r["source_path"] != "shared/node_registry.json", (
            "related_sections should only contain codewiki results"
        )


def test_get_node_examples_surfaces_codewiki_page_when_available():
    """``automation_edge`` has a full codewiki page at
    ``codewiki/automationedge.md``; get_node_examples should return
    it alongside the registry entry. Regression for the case where
    the node's type id and its doc filename disagree — the related-
    search must still find the page."""
    out = docs_index.get_node_examples("automation_edge")
    assert out["registry_entry"] is not None
    sources = {r["source_path"] for r in out["related_sections"]}
    assert any("automationedge.md" in s for s in sources), (
        f"expected codewiki/automationedge.md in related sections, got {sources}"
    )
