from regex_mecab import KeywordExtractor


def test_keyword_extractor_init():
    extractor = KeywordExtractor()
    assert extractor is not None

def test_keyword_extractor_extract():
    extractor = KeywordExtractor()
    text = "AIは人工知能です。機械学習技術が発展しています。"
    keywords = extractor.extract(text, top_n=3)
    assert isinstance(keywords, list)
    # Check that it returns something if text is sufficient
    if keywords:
        assert isinstance(keywords[0], str)
