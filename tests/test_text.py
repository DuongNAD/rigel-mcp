from rigel_mcp.ingest import split_text
from rigel_mcp.text import fold, fts_match, query_terms


def test_fold_removes_diacritics_and_d_bar():
    assert fold("Đa hình ƠỨ") == "da hinh ou"


def test_query_terms_drop_function_words_but_keep_technical_ones():
    assert query_terms("Giải thích đa hình trong Java") == ["giai", "da", "hinh", "trong", "java"]
    assert query_terms("What is the polymorphism of Java?") == ["polymorphism", "java"]
    assert query_terms("abstract class vs interface") == ["abstract", "class", "interface"]
    assert query_terms("the of và") == []


def test_fts_match_builds_or_query_with_exact_phrase_and_no_prefixes():
    assert fts_match(["da", "hinh"]) == '"da" OR "hinh" OR "da hinh"'
    assert fts_match(["java"]) == '"java"'
    assert fts_match([]) == ""


def test_split_text_respects_limit_and_keeps_every_word():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(10))
    parts = split_text(text, limit=500)
    assert len(parts) > 1 and all(len(p) <= 500 for p in parts)
    assert " ".join(" ".join(parts).split()) == " ".join(text.split())


def test_split_text_handles_one_very_long_line():
    parts = split_text("x" * 1200, limit=500)
    assert [len(p) for p in parts] == [500, 500, 200]
    assert split_text("   \n\n  ") == []
