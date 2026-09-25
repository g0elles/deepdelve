import sys
sys.path.insert(0, "src")
from pipeline.run import quote_in_source, chunks

SRC = "Japan's FIT Act was enacted in 2011.\n  It took effect  in July 2012, after the disaster."


def test_quote_match():
    assert quote_in_source("It took effect in July 2012, after the disaster.", SRC)  # whitespace-insensitive
    assert not quote_in_source("It took effect in July 2013, after the disaster.", SRC)  # altered fact
    assert not quote_in_source("too short", SRC)  # below min length


def test_chunks_capped():
    assert len(chunks("x" * 100000)) == 4


def test_markdown_markup_ignored():
    src = 'system for most [technologies](https://x.org/t "tech") which finished with EEG 2017.[[3]](#cite_note-4) The EEG first'
    assert quote_in_source("system for most technologies which finished with EEG 2017.", src)
    assert not quote_in_source("system for most technologies which finished with EEG 2018.", src)


def test_bm25_sentences_are_literal():
    from pipeline.run import sentences, bm25_top
    src = ("Intro line.\n\nThe [Renewable Energy Sources Act](https://x/eeg \"EEG\") introduced feed-in tariffs in 2000.[[1]](#c1) "
           "Cats are small domestic animals that enjoy sleeping in the afternoon sun.\n")
    top = bm25_top(sentences(src), "feed-in tariffs Renewable Energy Sources Act", 1)
    assert top and "feed-in tariffs in 2000" in top[0][1]
    assert quote_in_source(top[0][1], src)  # candidate is always a literal quote


def test_cited_lines_parse():
    from pipeline.dataset import cited_lines
    assert cited_lines("- fact (lines 6‑7)") == {6, 7}
    assert cited_lines("- fact (line 4) and more (lines 10-11)") == {4, 10, 11}
    assert cited_lines("- fact with no citation") == set()
