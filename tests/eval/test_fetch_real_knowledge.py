# tests/eval/test_fetch_real_knowledge.py
"""fetch_real_knowledge（実運用ナレッジ取得・整形）のテスト。

ネットワーク不要: XML/Markdown のパース・行分割・CSV 出力のみを検証する。
実 API（e-Gov / GitHub raw）へのアクセスはユーザー環境でのライブ実行に委ねる。
"""
import csv

from eval.vertical.fetch_real_knowledge import (
    DEFAULT_DOC_URLS,
    DEFAULT_LAWS,
    KnowledgeRow,
    parse_law_xml,
    parse_markdown_sections,
    split_long_rows,
    write_csv,
)

LAW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DataRoot>
  <ApplData>
    <LawFullText>
      <Law><LawBody>
        <LawTitle>行政不服審査法</LawTitle>
        <MainProvision>
          <Article Num="1">
            <ArticleCaption>（目的等）</ArticleCaption>
            <ArticleTitle>第一条</ArticleTitle>
            <Paragraph Num="1">
              <ParagraphSentence><Sentence>この法律は、行政庁の違法又は不当な処分に関し、国民が簡易迅速かつ公正な手続の下で広く行政庁に対する不服申立てをすることができるための制度を定める。</Sentence></ParagraphSentence>
            </Paragraph>
          </Article>
          <Article Num="2">
            <ArticleTitle>第二条</ArticleTitle>
            <Paragraph Num="1">
              <ParagraphSentence><Sentence>行政庁の処分に不服がある者は、審査請求をすることができる。</Sentence></ParagraphSentence>
            </Paragraph>
          </Article>
          <Article Num="3">
            <ArticleTitle>第三条</ArticleTitle>
          </Article>
        </MainProvision>
      </LawBody></Law>
    </LawFullText>
  </ApplData>
</DataRoot>
"""

MARKDOWN = """はじめにの前文です。

# インストール

```bash
# これはコードフェンス内なので見出しではない
pip install fastapi
```

pip でインストールできます。[公式サイト](https://example.com)も参照。

## 認証

**OAuth2** と `APIKey` に対応しています。

## 空セクション

# レート制限

APIのレート制限は1分あたり100リクエストです。
"""


class TestParseLawXml:
    def test_extracts_articles_with_heading(self):
        rows = parse_law_xml(LAW_XML, source_url="https://example.com/law")
        # 本文のない第三条は除外され 2 条
        assert len(rows) == 2
        assert rows[0].title == "行政不服審査法 第一条（目的等）"
        assert rows[0].text.startswith("行政不服審査法 第一条（目的等）: この法律は")
        assert rows[0].source == "https://example.com/law"
        assert rows[1].title == "行政不服審査法 第二条"
        assert "審査請求" in rows[1].text

    def test_empty_law_returns_no_rows(self):
        rows = parse_law_xml("<DataRoot><ApplData/></DataRoot>")
        assert rows == []


class TestParseMarkdownSections:
    def test_splits_by_heading_and_skips_code_fences(self):
        rows = parse_markdown_sections(
            MARKDOWN, source_url="https://raw.example.com/docs/guide.md")
        titles = [r.title for r in rows]
        # 前文はドキュメント名見出し・空セクションは除外・フェンス内 # は見出し扱いしない
        assert titles == ["guide", "インストール", "認証", "レート制限"]
        assert "これはコードフェンス内" not in " ".join(r.text for r in rows)

    def test_markdown_syntax_is_flattened(self):
        rows = parse_markdown_sections(MARKDOWN, source_url="x/guide.md")
        auth = next(r for r in rows if r.title == "認証")
        # 強調・インラインコード記号は落ち、平文が残る
        assert "OAuth2" in auth.text and "**" not in auth.text and "`" not in auth.text
        install = next(r for r in rows if r.title == "インストール")
        # リンクはラベルのみ残る
        assert "公式サイト" in install.text and "https://example.com" not in install.text


class TestSplitLongRows:
    def test_short_rows_pass_through(self):
        rows = [KnowledgeRow(text="短い。", title="t", source="s")]
        assert split_long_rows(rows, max_chars=100) == rows

    def test_long_row_is_split_at_sentence_boundary(self):
        text = "あ" * 60 + "。" + "い" * 60 + "。"
        rows = split_long_rows(
            [KnowledgeRow(text=text, title="法 第一条", source="s")], max_chars=80)
        assert len(rows) == 2
        assert rows[0].text.endswith("。") and rows[0].title == "法 第一条（1）"
        assert rows[1].title == "法 第一条（2）"
        # 分割しても本文は失われない
        assert "".join(r.text for r in rows) == text


class TestWriteCsv:
    def test_writes_text_column_csv(self, tmp_path):
        out = tmp_path / "sub" / "knowledge.csv"
        write_csv([KnowledgeRow(text="本文です。", title="見出し", source="url")], out)
        with out.open(encoding="utf-8") as f:
            records = list(csv.DictReader(f))
        # text カラムは chunking / register_to_qdrant が自動検出するキー
        assert records == [{"text": "本文です。", "title": "見出し", "source": "url"}]


class TestDefaults:
    def test_default_sources_are_pinned_and_nonempty(self):
        assert len(DEFAULT_LAWS) >= 3
        # OSS docs はタグ固定 URL（master/main 追従による再現性崩れを防ぐ）
        assert DEFAULT_DOC_URLS
        assert all("/master/" not in u and "/main/" not in u for u in DEFAULT_DOC_URLS)
