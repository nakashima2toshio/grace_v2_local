# backend/tests/test_multi_question.py
"""複数質問クエリの検知・構造解析・再構成（`backend/app/core/gates.py`）。

設計: `docs/multi_question_handling.md` §13。

⚠️ **このテストが最も重視するのは「単一質問の挙動が変わらないこと」である。**
複数質問対応は既存フローの手前に足す前処理であり、単一質問クエリの判定結果が
1 ミリでも変わってはならない（§13.8 の受け入れ基準 #1）。

⚠️ **安全側の向きが他の判定器と逆である。** `_detect_no_info_answer` 等が
「判定できないなら escalate」に倒すのに対し、複数質問検知は
「判定できないなら**単一とみなす**」に倒す。誤って質問を分解する方が害が
大きいため（§13.6）。
"""
from __future__ import annotations

from backend.app.core.gates import (
    MAX_QUESTION_CLUSTERS,
    _parse_cluster_output,
    create_cluster_analyzer,
    deferred_main_questions,
    detect_question_clusters,
    fallback_reconstruct,
    looks_like_multi_question,
    reconstruct_query,
)

# =============================================================================
# 第 1 段: looks_like_multi_question（LLM 呼び出しゼロ）
# =============================================================================

class TestLooksLikeMultiQuestion:
    def test_接続表現があれば候補になる(self):
        assert looks_like_multi_question(
            "住民票の写しの取り方は？ また、他の市町村に住民票を移動する方法は？"
        )

    def test_疑問符が2つ以上なら接続表現が無くても候補になる(self):
        assert looks_like_multi_question("住民票の取り方は？ 手数料は？")

    def test_単一質問は候補にならない(self):
        assert not looks_like_multi_question("住民票の写しの取り方を教えてください")

    def test_疑問符1つの単一質問は候補にならない(self):
        assert not looks_like_multi_question("住民票と戸籍謄本の違いは？")

    def test_空文字は候補にならない(self):
        assert not looks_like_multi_question("")
        assert not looks_like_multi_question("   ")

    def test_半角疑問符も数える(self):
        assert looks_like_multi_question("How about A? And B?")


# =============================================================================
# 単一質問の挙動が変わらないこと（受け入れ基準 #1）
# =============================================================================

class TestSingleQuestionUnchanged:
    def test_第1段で弾かれれば解析器は呼ばれない(self):
        """単一質問では LLM を 1 回も呼ばない（レイテンシ・コストが増えない）。"""
        calls = []

        def analyzer(q):
            calls.append(q)
            return [("A", []), ("B", [])]

        result = detect_question_clusters("住民票の取り方を教えてください", analyzer)

        assert result == []
        assert calls == [], "単一質問で解析器が呼ばれてはいけない"

    def test_解析器が無ければ空リスト(self):
        """`judges.enabled=false` 相当。第 1 段が一致しても単一として扱う。"""
        assert detect_question_clusters("Aは？ また、Bは？", analyzer=None) == []

    def test_解析器がNoneを返せば空リスト(self):
        """判定不能 → **単一とみなす**（安全側の向きが他の判定器と逆）。"""
        assert detect_question_clusters("Aは？ また、Bは？", lambda _q: None) == []


# =============================================================================
# 第 2 段の出力解析: _parse_cluster_output
# =============================================================================

class TestParseClusterOutput:
    def test_複数行が複数クラスタになる(self):
        out = _parse_cluster_output(
            "住民票の写しの取り方は？\n他の市町村に住民票を移動する方法は？", "q"
        )
        assert out == [
            ("住民票の写しの取り方は？", []),
            ("他の市町村に住民票を移動する方法は？", []),
        ]

    def test_パイプ区切りが関連質問になる(self):
        out = _parse_cluster_output("住民票の取り方は？ | その手数料は？", "q")
        assert out == [("住民票の取り方は？", ["その手数料は？"])]

    def test_SINGLEはNone(self):
        assert _parse_cluster_output("SINGLE", "q") is None
        assert _parse_cluster_output("single", "q") is None

    def test_主質問1つ関連質問0はNone(self):
        """単一質問と同義なので None（＝現行フローへ）。"""
        assert _parse_cluster_output("住民票の取り方は？", "q") is None

    def test_主質問1つでも関連質問があればクラスタになる(self):
        """選択は不要だが、再構成の対象になる（§13.3）。"""
        out = _parse_cluster_output("Aは？ | Bは？", "q")
        assert out == [("Aは？", ["Bは？"])]

    def test_空応答はNone(self):
        assert _parse_cluster_output("", "q") is None
        assert _parse_cluster_output("   \n  ", "q") is None

    def test_過剰分解はNoneへ倒す(self):
        """`MAX_QUESTION_CLUSTERS` 超過は信用しない（§13.6）。"""
        text = "\n".join(f"質問{i}は？" for i in range(MAX_QUESTION_CLUSTERS + 1))
        assert _parse_cluster_output(text, "q") is None

    def test_箇条書き記号や番号は除去される(self):
        out = _parse_cluster_output("- Aは？\n2. Bは？", "q")
        assert out == [("Aは？", []), ("Bは？", [])]


# =============================================================================
# 再構成: reconstruct_query / fallback_reconstruct
# =============================================================================

class TestReconstructQuery:
    def test_関連質問が無ければ主質問をそのまま返す(self):
        """LLM を呼ばない（コストゼロ）。"""
        assert reconstruct_query("住民票の取り方は？", [], config=None) == "住民票の取り方は？"

    def test_関連質問があってもconfigが無ければ素朴に連結(self):
        got = reconstruct_query("住民票の取り方は？", ["その手数料は？"], config=None)
        assert got == "住民票の取り方は？ その手数料は？"

    def test_解析器はconfigがNoneならLLMを呼ばない(self):
        """`create_cluster_analyzer(None)` は常に None を返す（単一扱い）。"""
        analyzer = create_cluster_analyzer(None)
        assert analyzer("Aは？ また、Bは？") is None

    def test_フォールバックは単語の羅列にしない(self):
        """`planner.py` が自然言語の文脈維持を求めるため（§13.3）。"""
        got = fallback_reconstruct("住民票の取り方は？", ["その手数料は？"])
        assert "住民票の取り方は？" in got
        assert "その手数料は？" in got

    def test_空の関連質問は無視される(self):
        assert fallback_reconstruct("Aは？", ["", "   "]) == "Aは？"

    def test_前後の空白は落ちる(self):
        assert fallback_reconstruct("  Aは？  ", []) == "Aは？"


# =============================================================================
# 保留質問: deferred_main_questions
# =============================================================================

class TestDeferredMainQuestions:
    def test_採用しなかった主質問だけを返す(self):
        clusters = [("Aは？", ["A関連は？"]), ("Bは？", []), ("Cは？", [])]
        assert deferred_main_questions(clusters, adopted_index=0) == ["Bは？", "Cは？"]

    def test_採用が真ん中でも正しい(self):
        clusters = [("Aは？", []), ("Bは？", []), ("Cは？", [])]
        assert deferred_main_questions(clusters, adopted_index=1) == ["Aは？", "Cは？"]

    def test_クラスタが1つなら保留は無い(self):
        assert deferred_main_questions([("Aは？", ["Bは？"])], adopted_index=0) == []

    def test_関連質問は保留リストに出ない(self):
        """関連質問は主質問に従属するため、主質問ごと保留される。"""
        clusters = [("Aは？", []), ("Bは？", ["B関連は？"])]
        got = deferred_main_questions(clusters, adopted_index=0)
        assert got == ["Bは？"]
        assert "B関連は？" not in got


# =============================================================================
# ここから下は grace_v2_local 固有（Ollama 版）
# =============================================================================
#
# 本リポジトリは `judges.enabled=false` が既定である（ローカル LLM で 1 判定
# 90〜250 秒かかるため）。複数質問の構造解析をそのフラグに繋ぐと、既定構成で
# 機能が丸ごと死ぬ。専用フラグ `judges.multi_question` で独立させてある。

from types import SimpleNamespace  # noqa: E402

from backend.app.core.gates import multi_question_enabled  # noqa: E402
from grace.config import GraceConfig  # noqa: E402


class TestMultiQuestionFlag:
    def test_既定は有効(self):
        """`judges.enabled=false` でも複数質問の解析だけは動く既定にする。"""
        config = GraceConfig()
        assert config.judges.enabled is False, "ローカル既定は補助判定オフのまま"
        assert config.judges.multi_question is True

    def test_judges_enabledがfalseでも無効化されない(self):
        config = SimpleNamespace(
            judges=SimpleNamespace(enabled=False, multi_question=True)
        )
        assert multi_question_enabled(config) is True

    def test_専用フラグがfalseなら無効(self):
        config = SimpleNamespace(
            judges=SimpleNamespace(enabled=True, multi_question=False)
        )
        assert multi_question_enabled(config) is False

    def test_judges属性が無いconfigスタブは有効扱い(self):
        """テスト用スタブは judges を持たないことがある（judges_enabled と同じ扱い）。"""
        assert multi_question_enabled(SimpleNamespace()) is True

    def test_フラグがfalseなら解析器はLLMを呼ばず単一扱い(self):
        config = SimpleNamespace(
            judges=SimpleNamespace(enabled=True, multi_question=False)
        )
        analyzer = create_cluster_analyzer(config)
        assert analyzer("Aは？ また、Bは？") is None

    def test_フラグがfalseなら再構成も素朴な連結へ倒れる(self):
        config = SimpleNamespace(
            judges=SimpleNamespace(enabled=True, multi_question=False)
        )
        got = reconstruct_query("住民票の取り方は？", ["その手数料は？"], config=config)
        assert got == "住民票の取り方は？ その手数料は？"


class TestTokenBudget:
    def test_判定用の枠を流用していない(self):
        """1 語返す判定の枠（512）では、複数行のクラスタ一覧が length で切れる。

        枠が足りないと空応答 → 解析器 None → 単一扱いで、機能が黙って効かなくなる。
        """
        from backend.app.core.verticals import (
            JUDGE_MAX_OUTPUT_TOKENS,
            MULTI_QUESTION_MAX_OUTPUT_TOKENS,
        )

        assert MULTI_QUESTION_MAX_OUTPUT_TOKENS > JUDGE_MAX_OUTPUT_TOKENS


# =============================================================================
# スコープ判定（担当範囲外を選択肢に出さない）
# =============================================================================
#
# ⚠️ 安全側の向きは「判定できないなら**範囲内**」。範囲外と誤判定すると
# 答えられる質問を断ってしまう。答えようとして生成側の SCOPE_POLICY が断る分には
# 二重の防波堤が働くだけで害がない。

from backend.app.core.gates import (  # noqa: E402
    _parse_scope_output,
    create_scope_classifier,
    split_by_scope,
)
from backend.app.core.verticals import PROFILES  # noqa: E402


class TestParseScopeOutput:
    def test_番号つきのIN_OUTを読む(self):
        assert _parse_scope_output("1: IN\n2: OUT", 2) == [True, False]

    def test_番号なしでも読む(self):
        assert _parse_scope_output("IN\nOUT\nIN", 3) == [True, False, True]

    def test_行数が合わなければ判定不能(self):
        """部分的に解釈して一部だけ断る、という中途半端な結果を作らない。"""
        assert _parse_scope_output("1: IN", 2) is None
        assert _parse_scope_output("IN\nOUT\nIN", 2) is None

    def test_IN_OUT以外の行があれば判定不能(self):
        assert _parse_scope_output("1: IN\n2: たぶん範囲外です", 2) is None

    def test_空応答は判定不能(self):
        assert _parse_scope_output("", 1) is None
        assert _parse_scope_output("   ", 1) is None


class TestSplitByScope:
    CLUSTERS = [("住民票の写しの取り方は？", []), ("明日の東京の天気は？", [])]

    def test_範囲外を分離する(self):
        in_idx, out_idx = split_by_scope(self.CLUSTERS, lambda _q: [True, False])
        assert in_idx == [0]
        assert out_idx == [1]

    def test_判定器が無ければ全件範囲内(self):
        assert split_by_scope(self.CLUSTERS, None) == ([0, 1], [])

    def test_判定不能なら全件範囲内(self):
        assert split_by_scope(self.CLUSTERS, lambda _q: None) == ([0, 1], [])

    def test_件数が合わなければ全件範囲内(self):
        assert split_by_scope(self.CLUSTERS, lambda _q: [True]) == ([0, 1], [])

    def test_全件範囲外なら全件範囲内へ倒す(self):
        """分類器が壊れている（全部 OUT）のと本当に全部範囲外なのを区別できない。

        前者だと利用者の質問が丸ごと消える。全部範囲外なら生成側の
        SCOPE_POLICY が従来どおり断るので二重には守られている。
        """
        assert split_by_scope(self.CLUSTERS, lambda _q: [False, False]) == ([0, 1], [])

    def test_判定器へ渡すのは主質問だけ(self):
        seen: list = []

        def classify(questions):
            seen.append(list(questions))
            return [True, False]

        split_by_scope([("Aは？", ["A関連は？"]), ("Bは？", [])], classify)
        assert seen == [["Aは？", "Bは？"]], "関連質問は主質問に従属するので渡さない"


class TestScopeClassifierGuards:
    def test_configがNoneならLLMを呼ばない(self):
        assert create_scope_classifier(None, PROFILES["gov"])(["Aは？"]) is None

    def test_プロファイル未指定ならLLMを呼ばない(self):
        """基本版タブ（vertical なし）には担当範囲という概念が無い。"""
        config = SimpleNamespace(judges=SimpleNamespace(multi_question=True))
        assert create_scope_classifier(config, None)(["Aは？"]) is None

    def test_専用フラグがfalseならLLMを呼ばない(self):
        config = SimpleNamespace(judges=SimpleNamespace(multi_question=False))
        assert create_scope_classifier(config, PROFILES["gov"])(["Aは？"]) is None

    def test_全プロファイルが担当範囲と窓口案内を持つ(self):
        """`scope_description` が空だと判定が無効化され、窓口案内も出せない。"""
        for key, profile in PROFILES.items():
            assert profile.scope_description, f"{key} に scope_description が無い"
            assert profile.out_of_scope_guidance, f"{key} に out_of_scope_guidance が無い"
