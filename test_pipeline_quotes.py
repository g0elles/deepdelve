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


def test_link_and_crossref_residue_dropped():
    from pipeline.run import sentences
    src = "Results are summarized in Table 1 \u2023 Lethe \u2023 Lethe: Layer-Adaptive KV Cache Pruning for LLM Serving. Real sentence about caches that is long enough to pass the filter here. See the rules...](/en/geo/1) for details on the rules of export."
    assert sentences(src) == ["Real sentence about caches that is long enough to pass the filter here."]


def test_bibliography_lines_dropped():
    from pipeline.run import sentences
    src = "Topham (1987), 'Benefit-Cost Rules for Urban Transit Subsidies', Journal of Transport Economics and Policy, 21(1), pp. 15-30. The 2010 decree created the federal data protection law for private parties."
    assert sentences(src) == ["The 2010 decree created the federal data protection law for private parties."]


def test_entity_and_specificity():
    from pipeline.run import facet_entities, entity_ok, specificity
    fs = [{"id": "f1", "name": "Safety Adherence Events", "questions": ["average Lima commute"]},
          {"id": "f2", "name": "Safety Adherence Events", "questions": ["average Bogota commute"]}]
    ents = facet_entities(fs)
    assert ents["f1"] == {"lima"} and ents["f2"] == {"bogot"}
    assert entity_ok("f1", "Commutes in Lima average 90 minutes.", "", ents)
    assert not entity_ok("f1", "Commutes in Bogota average 60 minutes.", "Lima " * 9, ents)  # rival named
    assert entity_ok("f1", "Commutes average 90 minutes.", "", ents)  # names no entity: kept
    assert specificity("The fine is 320 days of pay for firms that break the data law under this rule.") > specificity("Modal share is an important part of transport.")


def test_jurisdiction_and_recency():
    from pipeline.run import countries, jurisdiction_ok, year_score
    q = countries("¿Cómo se regula la protección de datos en México?")
    assert q == {"mx"}
    assert not jurisdiction_ok("En Colombia la multa diaria es de 5.000 salarios mínimos.", q)
    assert jurisdiction_ok("La multa es de 100 a 320,000 días de salario mínimo.", q)  # names none: kept
    assert jurisdiction_ok("A diferencia de Colombia, en México la multa es mayor.", q)  # names query country too
    assert countries("UK, Denmark and Taiwan") == {"uk", "dk", "tw"}
    assert year_score("Inflation was 15.9% in June 2026.", 2026) > 0 > year_score("Inflation hit 17% in July 2022.", 2026)
    assert year_score("Inflation was high.", 2026) == 0


def test_source_off_jurisdiction():
    from pipeline.run import source_off_jurisdiction
    assert source_off_jurisdiction("Chile " * 8 + "Mexico", {"mx"})
    assert not source_off_jurisdiction("Mexico " * 8 + "Chile " * 8, {"mx"})
    assert not source_off_jurisdiction("Chile " * 8, set())
