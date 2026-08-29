# backend/app/core/verticals.py
"""業界プロファイル（VerticalProfile）定義。

`agent_support_example.py` から移設（React マイグレーション）。CLI・API の
双方から参照される。後方互換のため `agent_support_example` が再エクスポートする。
設計: grace/doc/agent_support_verticals.md §1/§6。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from config import get_default_ollama_model

DEFAULT_QUERY = "パスワードを忘れました"

Decision = Literal["answer", "escalate"]
ActionType = Literal["create_ticket", "send_reply", "escalate_to_human"]

# 意図分類（二段判定の第 2 段）:
#   question = 情報・手順・規定を知りたい（FAQ質問） / request = 操作・手続きの実行依頼
#   incident = 障害・被害・トラブルの発生報告
Intent = Literal["question", "request", "incident"]

# 意図分類に使う軽量モデル（CLAUDE.md プロバイダ方針の軽量既定）。
#
# ⚠️ ローカル LLM（Ollama）では既定モデルと同一。クラウドと違い「軽量モデルへ
#    寄せてコストを下げる」動機がなく、別モデルにすると呼び出しのたびに VRAM の
#    ロード/アンロードが起きてかえって遅くなる。grace/config.py の
#    LLMConfig.light_model と揃えてある。既定値は config.py::get_default_ollama_model()
#    の1箇所で管理する。
INTENT_MODEL = get_default_ollama_model()

# 1 語（question / no_info / OK 等）だけを返させる判定系の出力枠。
#
# ⚠️ **10 まで絞ってはいけない。** thinking を出すローカルモデル（qwen3.5 等）は
#    枠を思考で使い切り、本文が空のまま返る。実測ではこれが原因で意図分類・
#    情報なし判定・複雑度推定がすべて「empty response」になり、
#    毎回 LLM を呼んでは丸ごと捨てる（＝90〜250 秒の純粋な無駄）状態だった。
#    llm_compat._strip_think() が思考を剥がす前提で、思考が収まる枠を確保する。
JUDGE_MAX_OUTPUT_TOKENS = 512

# 複数質問の構造解析・再構成（0-(A) 入力・質問分析）の出力枠。
#
# ⚠️ **`JUDGE_MAX_OUTPUT_TOKENS` を流用してはいけない。** あちらは「1 語だけ返す」
#    判定用の枠で、こちらは複数行のクラスタ一覧や 1 文の質問文を返させる。
#    ローカルモデルは思考（<think>）で枠を先に食うため、本文ぶんを上に積む必要が
#    ある。枠が足りないと finish_reason=length の空応答になり、解析器は None を
#    返す（＝単一質問へ倒れて機能が黙って効かなくなる）。
MULTI_QUESTION_MAX_OUTPUT_TOKENS = 1024

# 全プロファイル共通のスコープ方針（reasoning プロンプトへ注入）。
#
# 背景: 検索スコープ（`collections`）が効くのは **内部 RAG だけ** で、
# ⑤ Web フォールバックと executor の動的 web_search にはドメイン制限が無い
# （`grace/config.py::WebSearchConfig` に allowed_domains 相当のフィールドが無く、
# `WebSearchTool.execute` も query/num_results/language しか受け取らない）。
# その結果、gov プロファイルで「明日の東京の天気は？」を投げると天気サイトが
# 引用に載る、という取り違えが実測で確認されている。
#
# 取得側（retrieval）を絞るのは 0 件化 → 情報なし回答 → 誤エスカレの連鎖を
# 招きやすいため、まず生成側（reasoning）で担当範囲を明示する。
#
# ⚠️ 最終文は必須。これが無いと「住民票の取り方は？ ところで明日の天気は？」
#    のような複合質問で、担当範囲内の質問まで丸ごと断られうる。
SCOPE_POLICY = (
    "担当範囲は上記の業務領域に限る。範囲外の話題（天気・ニュース・一般常識・"
    "他業種の手続き等）は、参照情報に含まれていても内容を回答せず、"
    "担当範囲外である旨を明示したうえで適切な窓口を案内すること。"
    "ただし担当範囲内の質問が同時に含まれる場合は、そちらには通常どおり回答する。"
)


@dataclass
class ActionRequest:
    """副作用のある操作の要求（v3・擬似）。"""

    action_type: ActionType
    args: dict = field(default_factory=dict)
    requires_confirmation: bool = True


@dataclass
class VerticalProfile:
    """業界プロファイル（差し替えの共通枠）。設計: agent_support_verticals.md §1/§6。"""

    name: str
    collections: List[str] = field(default_factory=list)   # 検索スコープ（実 Qdrant コレクション名）
    escalate_keywords: List[str] = field(default_factory=list)  # 強制エスカレ語
    action_map: Dict[str, ActionType] = field(default_factory=dict)  # 意図キーワード → action_type
    require_identity: bool = False           # アクション前に本人確認を必須化
    notify_th: Optional[float] = None        # None なら config 既定
    confirm_th: Optional[float] = None
    prompt_addendum: str = ""                # 業界固有の方針（表示・プロンプト注入用）
    # W-1: Web 検索で優先するドメイン（接尾辞一致）。**除外ではなく加点**。
    # 一致した結果のスコアを底上げして上位へ並べ替えるだけで、非一致の結果も残す
    # （絞り込むと 0 件化 → 情報なし回答 → 誤エスカレの連鎖を招くため）。
    preferred_domains: List[str] = field(default_factory=list)
    # 0-(A) のスコープ判定（第 2 段 LLM）へ渡す業務領域の説明。
    # 空なら判定を行わない（＝すべて範囲内とみなす＝従来どおり）。
    scope_description: str = ""
    # 範囲外と判定された質問へ添える案内文。**断るだけで終わらせない。**
    # 「答えません」だけでは利用者は次にどこへ行けばよいか分からず、
    # 窓口へ電話が来るだけになる（SCOPE_POLICY も窓口案内まで求めている）。
    out_of_scope_guidance: str = ""

    def build_prompt_addendum(self) -> str:
        """reasoning へ実際に注入する業務方針を組み立てる。

        業界固有の方針（`prompt_addendum`）に共通の `SCOPE_POLICY` を足したもの。
        `prompt_addendum` 単体は「この業界の方針」を表す値として `/api/verticals`
        がそのまま返すため、スコープ方針はここで合成し、フィールドは汚さない。
        """
        parts = [p for p in (self.prompt_addendum, SCOPE_POLICY) if p]
        return "\n".join(parts)


# 組み込みプロファイル（自治体 / SaaS / EC）
#
# collections は実 Qdrant コレクション名（命名規約 `*_anthropic`。
# docs/vertical_test_data.md 参照）。RAG 検索は config.qdrant.allowed_collections
# 経由でこのスコープに限定される。未登録のコレクションは自動的に無視され、
# 1 つも登録が無い場合は制限なし（既定コレクション横断）で従来どおり動作する。
PROFILES: Dict[str, VerticalProfile] = {
    "gov": VerticalProfile(
        name="自治体",
        # ⚠️ 以前は wikipedia_ja を「専用コレクション登録までの代替」として
        #    許可していたが、gov_faq_anthropic / gov_laws_anthropic が登録済みに
        #    なったため外した（実測 2026-08-29: gov_faq が 0.8011 でヒット）。
        #
        #    許可リストに残しておくと汎用コーパスが自治体の回答の
        #    「社内ナレッジ」として提示されうる。saas / ec は元から専用
        #    コレクションだけなので、それに揃える。
        collections=["gov_faq_anthropic", "gov_laws_anthropic"],
        escalate_keywords=["法的", "訴訟", "減免", "個別", "例外", "不服"],
        action_map={"申請": "send_reply", "手続": "send_reply", "様式": "send_reply"},
        require_identity=False,
        notify_th=0.8, confirm_th=0.5,   # 正確性最優先：厳しめ
        # 公的機関のドメインを優先（加点のみ・除外はしない）
        preferred_domains=["go.jp", "lg.jp"],
        prompt_addendum="条例・公式案内に基づき、断定を避け、該当ページ・担当課を明示。個人情報は尋ねない。",
        scope_description=(
            "自治体（市区町村）の窓口業務。住民票・戸籍・転入転出・マイナンバーカード・"
            "印鑑登録・国民健康保険・税・各種証明書の発行や申請手続きなど。"
        ),
        out_of_scope_guidance=(
            "天気・ニュース・一般常識や他機関の手続きは当窓口では扱っておりません。"
            "各分野の公的機関（例: 気象情報は気象庁）または該当する窓口へお問い合わせください。"
        ),
    ),
    "saas": VerticalProfile(
        name="SaaS",
        collections=["saas_docs_anthropic", "saas_api_anthropic"],
        escalate_keywords=["障害", "ダウン", "落ち", "課金", "請求", "情報漏", "セキュリティ"],
        action_map={"エラー": "create_ticket", "不具合": "create_ticket", "バグ": "create_ticket"},
        require_identity=False,
        preferred_domains=[],   # 自社ドキュメントの公開ドメインが決まったら列挙する
        prompt_addendum="製品バージョンを明示し、再現手順と公式ドキュメント URL を添える。",
        scope_description=(
            "自社 SaaS 製品のサポート。機能の使い方・設定・API・料金プラン・"
            "不具合や障害の報告など、製品に関する事柄。"
        ),
        out_of_scope_guidance=(
            "当サポートは自社製品に関するお問い合わせを承っております。"
            "製品と関係のない話題については、該当する提供元へお問い合わせください。"
        ),
    ),
    "ec": VerticalProfile(
        name="EC",
        collections=["ec_policy_anthropic", "ec_faq_anthropic"],
        escalate_keywords=["決済", "返金", "破損", "クレーム", "不良品"],
        action_map={"返品": "create_ticket", "交換": "create_ticket",
                    "キャンセル": "create_ticket", "解約": "create_ticket"},
        require_identity=True,           # 注文情報の操作は本人確認必須
        preferred_domains=[],   # 自社ストア・規約ページのドメインが決まったら列挙する
        prompt_addendum="注文情報の照会・変更は本人確認必須。返品・交換は規定の版に基づいて回答。",
        scope_description=(
            "EC サイトのカスタマーサポート。注文・配送・返品・交換・キャンセル・"
            "支払い・会員情報など、当ストアでのお買い物に関する事柄。"
        ),
        out_of_scope_guidance=(
            "当ストアでのお買い物に関する事柄以外はお答えできません。"
            "該当する提供元・窓口へお問い合わせください。"
        ),
    ),
}
