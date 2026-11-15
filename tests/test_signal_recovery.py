from app.content import chunk_document, prioritize_chunks
from app.tools import discover_links


def test_company_rows_first_and_full_text_retained():
    text = "Index methodology and background.\n" * 30 + "Example Holdings Metals 123,456 3.20\n" * 8
    doc = {"document_id": "doc", "text": text}
    chunks = chunk_document(doc)
    ordered = prioritize_chunks(chunks, [doc])
    first = ordered[0]
    assert "Example Holdings" in text[first["start"]:first["end"]]
    assert len(ordered) == len(chunks)
    assert "".join(text[c["start"]:c["end"]] for c in sorted(ordered, key=lambda c:c["start"])) == text


def test_haze_discovery_is_bounded_and_excludes_privacy():
    links = [{"url":"https://www.fmm.org.my/article/newsroom/haze-health", "label":"Haze health impact"},
             {"url":"https://www.fmm.org.my/privacy-haze", "label":"Privacy haze"}]
    assert discover_links(links, ["safety inspection"], 1) == [links[0]["url"]]
