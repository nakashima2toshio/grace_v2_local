# tests/test_question_scope_and_deferred.py
"""0-(A) で話題を選んだあとの後始末と、担当範囲外の扱い（`backend/app/core/gates.py`）。

- 採用しなかった話題を保留として返す（`deferred_main_questions`）
- 担当範囲内・範囲外へ分ける（`split_by_scope`。判定できなければ・全部範囲外なら全件を範囲内）
- 範囲外の質問を同じ回答の中で断らせる指示と、断りが抜けたときの追記（`ensure_out_of_scope_notice`）
- 回答本文が出典に触れているか（`answer_cites_sources`）

旧 `test_multi_question.py` から、2026-10-29 以降も残る関数のテストを移した（話題・担当範囲そのものは
`question_refiner.refine_question` が 1 回の LLM 呼び出しで返す。検知・分解・再構成・分類器は削除した）。
"""
from __future__ import annotations

from backend.app.core.gates import (
    answer_cites_sources,
    deferred_main_questions,
    ensure_out_of_scope_notice,
    split_by_scope,
)
from backend.app.core.verticals import PROFILES


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


class TestProfilesHaveScope:
    def test_全プロファイルが担当範囲と窓口案内を持つ(self):
        """`scope_description` が空だと判定が無効化され、窓口案内も出せない。"""
        for key, profile in PROFILES.items():
            assert profile.scope_description, f"{key} に scope_description が無い"
            assert profile.out_of_scope_guidance, f"{key} に out_of_scope_guidance が無い"


class TestOutOfScopeInstruction:
    """担当範囲外の質問を、**同じ回答の中で**断らせる方針注入。

    0-(A) は範囲外の主質問を検索クエリから外す（外さないと検索の重心がボケる。
    実測 2026-08-29: 混在クエリ 0.7225 に対し再構成後 0.8011）。外したままだと
    生成側は範囲外の質問があったことすら知らず、利用者から見て「聞いたはずの
    片方が返答に出てこない」状態になる。検索は絞ったまま、質問文だけを渡す。
    """

    QUESTIONS = ["明日の東京の天気は？"]

    def test_範囲外が無ければ空(self):
        """⚠️ 位置を変えた。断りの指示は業務方針ではなく**構成ルールの後ろ**。

        実測 2026-08-30（03:00 / 04:07）で、業務方針側に置いた指示が
        【回答の構成ルール（最重要）】に負けて 2 回連続で無視された。
        """
        profile = PROFILES["gov"]
        assert profile.build_closing_instruction() == ""
        assert profile.build_closing_instruction([]) == ""

    def test_業務方針には範囲外の質問文を入れない(self):
        """混ぜると構成ルールに埋もれる。分離そのものを固定する。"""
        addendum = PROFILES["gov"].build_prompt_addendum(self.QUESTIONS)
        assert "明日の東京の天気は？" not in addendum

    def test_範囲外の質問文が注入される(self):
        closing = PROFILES["gov"].build_closing_instruction(self.QUESTIONS)
        assert "明日の東京の天気は？" in closing

    def test_窓口案内が注入される(self):
        closing = PROFILES["gov"].build_closing_instruction(self.QUESTIONS)
        assert PROFILES["gov"].out_of_scope_guidance in closing

    def test_同じ回答の中で扱うよう指示する(self):
        """別の問い合わせとして先送りさせない（＝1 回のやり取りで両方に対応）。"""
        addendum = PROFILES["gov"].build_closing_instruction(self.QUESTIONS)
        assert "同じ回答の末尾に" in addendum
        assert "先送りしない" in addendum
        # 構成ルール 1・7（参照情報のみ／捏造禁止）との衝突を明示的に解いていること。
        # ローカル LLM はこの衝突で断りを落としていた（実測 2026-08-29）。
        assert "例外" in addendum

    def test_案内先URLをliteralで渡す(self):
        """⚠️ **URL を記憶から書かせない。**

        構成ルール 4 は「出典行に無い URL を書くのは捏造」としている。
        案内先を出させたいなら、こちらが literal で渡すのが唯一の正しい方法。
        実測 2026-08-29（クラウド版）は、渡していない URL を記憶から補っていた。
        """
        closing = PROFILES["gov"].build_closing_instruction(self.QUESTIONS)
        assert "https://www.jma.go.jp/" in closing
        assert "そのまま書き写す" in closing

    def test_案内先URLは実在するhttpsだけ(self):
        """架空の URL を置くと、こちらが捏造の出どころになる。"""
        for key, profile in PROFILES.items():
            for label, url in profile.out_of_scope_links.items():
                assert url.startswith("https://"), f"{key}: {label}"
                assert label, key

    def test_業界方針とスコープ方針は消えない(self):
        profile = PROFILES["gov"]
        addendum = profile.build_prompt_addendum(self.QUESTIONS)
        assert profile.prompt_addendum in addendum
        assert "担当範囲は上記の業務領域に限る" in addendum

    def test_複数の範囲外質問を列挙する(self):
        closing = PROFILES["gov"].build_closing_instruction(["Aは？", "Bは？"])
        assert "- Aは？" in closing
        assert "- Bは？" in closing


class TestEnsureOutOfScopeNotice:
    """断りが回答本文に無ければ足す（プロバイダに依存させない）。

    実測 2026-08-29（同一の質問・同一の方針注入）:

    | モデル | 回答本文の断り |
    |---|---|
    | claude-sonnet-4-6 | あり |
    | gemma4:26b-a4b-it-qat | **なし**（住民票にだけ答えて終わり） |

    「聞いたはずの片方が返答に出てこない」のは利用者から見て事故なので、
    指示に従わないモデルでも必ず出るようにする。
    """

    QUESTIONS = ["明日の東京の天気は？"]
    GUIDANCE = "気象情報は気象庁へお問い合わせください。"
    ANSWERED = "住民票の写しは窓口・郵送・コンビニ交付で取得できます。"

    def test_断りが無ければ足す(self):
        got = ensure_out_of_scope_notice(self.ANSWERED, self.QUESTIONS, self.GUIDANCE)
        assert "明日の東京の天気は？" in got
        assert self.GUIDANCE in got
        assert got.startswith(self.ANSWERED), "元の回答は先頭に残す"

    def test_モデルが自分で断っていれば足さない(self):
        answer = self.ANSWERED + "\nなお、天気は当窓口の担当範囲外です。"
        assert ensure_out_of_scope_notice(answer, self.QUESTIONS, self.GUIDANCE) is answer

    def test_言い回しが違っても拾う(self):
        for phrase in ("お答えできません", "扱っておりません", "対応範囲外です"):
            answer = f"{self.ANSWERED}\n天気については{phrase}。"
            assert ensure_out_of_scope_notice(
                answer, self.QUESTIONS, self.GUIDANCE
            ) is answer, phrase

    def test_範囲外が無ければ何もしない(self):
        assert ensure_out_of_scope_notice(self.ANSWERED, [], self.GUIDANCE) is self.ANSWERED

    def test_回答が空なら何もしない(self):
        assert ensure_out_of_scope_notice("", self.QUESTIONS, self.GUIDANCE) == ""
        assert ensure_out_of_scope_notice(None, self.QUESTIONS, self.GUIDANCE) is None

    def test_案内先のURLを添える(self):
        """⚠️ **「窓口へどうぞ」で終わらせない。**

        実測 2026-08-30 の指摘「あるけど、URL ぐらい欲しい」。案内だけ出して
        URL が無いと、利用者は結局そこから自分で探すことになる。
        """
        got = ensure_out_of_scope_notice(
            self.ANSWERED, self.QUESTIONS, self.GUIDANCE,
            links={"気象庁": "https://www.jma.go.jp/"},
        )
        assert "気象庁: https://www.jma.go.jp/" in got

    def test_URLが無いプロファイルでも壊れない(self):
        """架空の事業者（saas / ec）には実在 URL を持たせない。"""
        got = ensure_out_of_scope_notice(self.ANSWERED, self.QUESTIONS, self.GUIDANCE)
        assert self.GUIDANCE in got
        assert "http" not in got

    def test_モデルが断っても_URLが無ければ案内先だけ足す(self):
        """⚠️ **断り本文は足さない。足りない URL だけを足す。**

        断りの指示を `prompt_closing` へ移してから、姉妹リポジトリ（grace_v2）
        では**モデル自身が断りを書くのが通常の経路**になった（実測 2026-08-31）。
        つまり「マーカーがあれば何もしない」分岐が主経路になりうるので、
        モデルが断りだけ書いて URL を落とすと案内先が丸ごと消える。
        「あるけど、URL ぐらい欲しい」（実測 2026-08-30）への逆戻りを防ぐ。
        """
        answer = self.ANSWERED + "\n天気は当窓口の担当範囲外です。"
        got = ensure_out_of_scope_notice(
            answer, self.QUESTIONS, self.GUIDANCE,
            links={"気象庁": "https://www.jma.go.jp/"},
        )
        assert "気象庁: https://www.jma.go.jp/" in got
        assert got.startswith(answer), "モデルの断り本文はそのまま残す"
        assert "上記は当窓口の担当範囲外のためお答えできません" not in got, \
            "断り本文を二重に書かない"

    def test_モデルがURLまで書いていれば何も足さない(self):
        answer = (
            self.ANSWERED
            + "\n天気は当窓口の担当範囲外です。"
            + "\n- 気象庁: https://www.jma.go.jp/"
        )
        assert ensure_out_of_scope_notice(
            answer, self.QUESTIONS, self.GUIDANCE,
            links={"気象庁": "https://www.jma.go.jp/"},
        ) is answer

    def test_モデルが断っていてURLの設定が無ければ足さない(self):
        answer = self.ANSWERED + "\n天気は当窓口の担当範囲外です。"
        assert ensure_out_of_scope_notice(
            answer, self.QUESTIONS, self.GUIDANCE,
        ) is answer

    def test_一部のURLだけ書かれていれば残りを足す(self):
        answer = (
            self.ANSWERED
            + "\n天気は当窓口の担当範囲外です。"
            + "\n- 気象庁: https://www.jma.go.jp/"
        )
        got = ensure_out_of_scope_notice(
            answer, self.QUESTIONS, self.GUIDANCE,
            links={
                "気象庁": "https://www.jma.go.jp/",
                "e-Gov": "https://www.e-gov.go.jp/",
            },
        )
        assert got.count("https://www.jma.go.jp/") == 1, "既にある URL は重ねない"
        assert "e-Gov: https://www.e-gov.go.jp/" in got

    def test_案内が空でも既定文を出す(self):
        got = ensure_out_of_scope_notice(self.ANSWERED, self.QUESTIONS, "")
        assert "お問い合わせください" in got

    def test_複数の範囲外質問を列挙する(self):
        got = ensure_out_of_scope_notice(self.ANSWERED, ["Aは？", "Bは？"], self.GUIDANCE)
        assert "- Aは？" in got and "- Bは？" in got


class TestAnswerCitesSources:
    """回答本文が出典に触れているか（**ゲートではなく観測**）。

    構成ルール 4 は「出典行をそのまま書き写す」ことを求めているが、従うかは
    モデル次第である。実測 2026-08-30: 前回まで本文にあった
    「出典: gov_faq.csv」が消えた。出典セクションは別に出るので実害は小さいが、
    揺れが見えないとプロンプトの効き目を評価できない。

    ⚠️ **落ちても回答は止めない。** 止めると、出典を本文に書かないだけの
    正しい回答まで捨てることになる。
    """

    CITATIONS = ["[社内] gov_faq.csv", "[Web] https://www.example.go.jp/a"]

    def test_ファイル名に触れていればTrue(self):
        assert answer_cites_sources("社内ナレッジ (gov_faq.csv) によると…", self.CITATIONS)

    def test_URLに触れていればTrue(self):
        answer = "Web 検索結果（https://www.example.go.jp/a）によると…"
        assert answer_cites_sources(answer, self.CITATIONS)

    def test_触れていなければFalse(self):
        assert answer_cites_sources("住民票は窓口で取得できます。", self.CITATIONS) is False

    def test_出典が無ければTrue(self):
        """書きようが無いものを「守れていない」とは言わない。"""
        assert answer_cites_sources("回答です。", []) is True

    def test_回答が空ならFalse(self):
        assert answer_cites_sources("", self.CITATIONS) is False
        assert answer_cites_sources(None, self.CITATIONS) is False

    def test_ラベルは外して見る(self):
        """`[社内] ` は表示用のラベル。本文に出るのは中身だけ。"""
        assert answer_cites_sources("出典: gov_faq.csv", ["[社内] gov_faq.csv"])
