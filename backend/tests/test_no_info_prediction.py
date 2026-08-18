# backend/tests/test_no_info_prediction.py
"""④' が**中身のある予測回答を「予測だから」で落とさない**ことを固定するテスト。

## 背景（実測 2026-08-17 16:17）

「明日の東京の天気は？」に対し、Web 出典 9 件から

    くもり / 最高 30℃ / 最低 22℃ / 降水確率 20%・10%・20% / 時間帯別気温

まで具体的に答え、groundedness 1.00（17/17 supported）・全体信頼度 0.90 だった
回答が、④' で `no_info` と判定されエスカレされた。

    [no-info] 実質回答判定（claude-haiku-4-5-20251001）: no_info
    [gate] 情報なし回答を検知（出典が Web のみ）→ 有人対応へエスカレーション

判定が得られていた（`None` ではない）ので #74 の修正は正しく効いておらず、
**判定基準そのものが誤発火していた**。判定プロンプトの no_info 条件に

    質問が将来の予測・見通しを求めており、回答が確定情報ではなく要望・検討段階の
    情報の紹介に留まる（「確定した内容ではない」等の注記つき）場合も no_info

があり、**天気予報は定義上どうやってもこれに当たる**。

  - 「明日の天気」は将来の予測そのもの
  - 天気予報は原理的に確定情報ではない
  - 「最新の予報は各天気予報サービスにて直接ご確認ください」は
    「確定した内容ではない」等の注記に読める

つまり「予測を問う質問 × 標準的な注記 ⇒ 内容によらず no_info」になっていた。

## なぜ「仕様」ではなく「欠陥」なのか

- no_info の定義本文は **「実質的な情報がゼロで」** から始まる。予測の条件は
  「また、…も no_info」と**同じ原則の追加ケース**として並んでおり、
  「予測質問は全部有人」という独立ルールとして置かれたものではない。
- ④' は名前どおり**情報なし回答検知ゲート**。不確実な話題そのものを有人へ
  回したいなら、それは ④ の強制エスカレ（キーワード／intent）の仕事であって、
  「中身のある回答を捨てる」のはこのゲートの職掌外。
- groundedness を安全弁には使えない。「見つかりませんでした」型の回答も
  （正確に報告している以上）groundedness は高く出る。それはまさに ④' が
  拾うべきケースなので、スコアでは切り分けられない。**判定基準の側を直す。**

ここで固定すること:
  1. 「予測の中身を示していれば answered」が判定基準に明示されること
  2. 「注記の有無で判定しない」が明示されること
  3. 予測質問の answered 例が判定例に入ること
  4. 既存の no_info 条件（実質情報ゼロ／案内のみ／事例紹介のみ）は残ること
  5. 判定器の入出力契約（answered/no_info の解釈）を変えていないこと

⚠️ LLM には接続しない。プロンプトは**実際に組み立てたもの**を検査する
（定数を読むのではなく `judge()` を呼んで client に渡った文字列を見る）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.core.gates import _detect_no_info_answer, create_no_info_judge

QUERY = "明日の東京の天気は？"

# 実測 16:17 の回答（要点のみ）。具体値と注記の両方を含む点が重要。
FORECAST_ANSWER = (
    "明日（8月18日）の東京の天気はくもりの予報です。"
    "最高気温は30℃、最低気温は22℃の見込みです。"
    "降水確率は朝20%、日中10%、夜20%となっています。\n"
    "上記の情報は取得時点のものであり、最新の予報は各天気予報サービスにて"
    "直接ご確認ください。"
)


@pytest.fixture()
def prompt():
    """`judge()` が実際に LLM へ渡したプロンプト文字列。"""
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(text="answered")
    import grace.llm_compat as compat
    original = compat.create_chat_client
    compat.create_chat_client = lambda _c: client
    try:
        judge = create_no_info_judge(
            SimpleNamespace(llm=SimpleNamespace(light_model="claude-haiku-4-5-20251001"))
        )
    finally:
        compat.create_chat_client = original

    judge(QUERY, FORECAST_ANSWER)
    [call] = client.models.generate_content.call_args_list
    return call.kwargs["contents"]


# =============================================================================
# ① 予測の中身を示していれば answered
# =============================================================================

class TestPredictionWithSubstanceIsAnswered:

    def test_carve_out_is_stated(self, prompt):
        """「予測の中身を示していれば answered」が判定基準に書かれていること。"""
        assert "具体的な予測の内容" in prompt
        assert "answered" in prompt

    def test_the_caveat_is_explicitly_not_a_signal(self, prompt):
        """注記の有無で no_info にしない、と明示されていること。

        実測の回答が持っていた「最新情報は各提供元で確認を」という注記は、
        予測に付くのが当然の断り書きであって情報量の欠如ではない。
        """
        assert "注記の有無で no_info にしてはならない" in prompt

    def test_prediction_example_is_answered(self, prompt):
        """判定例に「予測に具体値 → answered」が入っていること。

        ⚠️ 質問文（`QUERY`）はプロンプト末尾にも載るので、`"明日の東京の天気は？"`
        の有無では例の存在を確かめられない。例だけが持つ語で見る。
        """
        assert "降水確率を具体的に示し" in prompt, "予測質問の answered 例が無い"
        assert "予測であること・注記があることは減点しない" in prompt

    def test_old_trigger_wording_is_gone(self, prompt):
        """「確定情報ではなく…注記つき」という発火条件を残していないこと。

        この言い回しが残っていると、予測質問 × 標準的な注記で再発する。
        """
        assert "確定情報ではなく要望・検討段階の" not in prompt
        assert "「確定した内容ではない」等の注記つき）場合も no_info" not in prompt


# =============================================================================
# ② 既存の no_info 条件は落とさない
# =============================================================================

class TestExistingCriteriaSurvive:

    def test_zero_substance_is_still_no_info(self, prompt):
        assert "実質的な情報が" in prompt
        assert "ゼロ" in prompt

    def test_guidance_only_is_still_no_info(self, prompt):
        assert "それをどこで確認できるかの案内" in prompt
        assert "案内が丁寧でも no_info" in prompt

    def test_unresolved_prediction_is_still_no_info(self, prompt):
        """予測を問われて中身を示せない回答は従来どおり no_info。"""
        assert "要望・検討段階の情報の紹介に留まる場合も no_info" in prompt

    def test_existing_answered_examples_survive(self, prompt):
        for example in ("返品規定", "送料はいくら", "どんな制度ですか"):
            assert example in prompt

    def test_output_contract_is_unchanged(self, prompt):
        assert "出力（answered / no_info のいずれか 1 語のみ）:" in prompt
        assert prompt.rstrip().endswith("1 語のみ）:")


# =============================================================================
# ③ 判定器の入出力契約は変えていない
# =============================================================================

class TestJudgeContractUnchanged:

    def _judge_returning(self, text):
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text=text)
        import grace.llm_compat as compat
        original = compat.create_chat_client
        compat.create_chat_client = lambda _c: client
        try:
            return create_no_info_judge(
                SimpleNamespace(llm=SimpleNamespace(light_model="m"))
            )
        finally:
            compat.create_chat_client = original

    def test_answered_keeps_the_answer(self):
        judge = self._judge_returning("answered")

        assert judge(QUERY, FORECAST_ANSWER) is False
        assert _detect_no_info_answer(
            QUERY, FORECAST_ANSWER, judge, force_judge=True,
        ) == (False, None)

    def test_no_info_still_escalates(self):
        """判定器が no_info と言えば従来どおりエスカレする（安全弁は残す）。"""
        judge = self._judge_returning("no_info")

        assert _detect_no_info_answer(
            QUERY, FORECAST_ANSWER, judge, force_judge=True,
        ) == (True, None)

    def test_the_answer_reaches_the_prompt(self, prompt):
        """判定対象の回答本文と質問がプロンプトに載ること。"""
        assert QUERY in prompt
        assert "最高気温は30℃" in prompt
