# backend/app/core/verticals.py
"""業界プロファイル（VerticalProfile）定義。

`agent_support_example.py` から移設（React マイグレーション）。CLI・API の
双方から参照される。後方互換のため `agent_support_example` が再エクスポートする。
設計: grace/doc/agent_support_verticals.md §1/§6。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

DEFAULT_QUERY = "パスワードを忘れました"

Decision = Literal["answer", "escalate"]
ActionType = Literal["create_ticket", "send_reply", "escalate_to_human"]

# 意図分類（二段判定の第 2 段）:
#   question = 情報・手順・規定を知りたい（FAQ質問） / request = 操作・手続きの実行依頼
#   incident = 障害・被害・トラブルの発生報告
Intent = Literal["question", "request", "incident"]

# 意図分類に使う軽量モデル（CLAUDE.md プロバイダ方針の軽量既定）
INTENT_MODEL = "claude-haiku-4-5-20251001"

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
        # wikipedia_ja は専用コレクション（gov_faq/gov_laws）登録までの代替
        collections=["gov_faq_anthropic", "gov_laws_anthropic", "wikipedia_ja"],
        escalate_keywords=["法的", "訴訟", "減免", "個別", "例外", "不服"],
        action_map={"申請": "send_reply", "手続": "send_reply", "様式": "send_reply"},
        require_identity=False,
        notify_th=0.8, confirm_th=0.5,   # 正確性最優先：厳しめ
        # 公的機関のドメインを優先（加点のみ・除外はしない）
        preferred_domains=["go.jp", "lg.jp"],
        prompt_addendum="条例・公式案内に基づき、断定を避け、該当ページ・担当課を明示。個人情報は尋ねない。",
    ),
    "saas": VerticalProfile(
        name="SaaS",
        collections=["saas_docs_anthropic", "saas_api_anthropic"],
        escalate_keywords=["障害", "ダウン", "落ち", "課金", "請求", "情報漏", "セキュリティ"],
        action_map={"エラー": "create_ticket", "不具合": "create_ticket", "バグ": "create_ticket"},
        require_identity=False,
        preferred_domains=[],   # 自社ドキュメントの公開ドメインが決まったら列挙する
        prompt_addendum="製品バージョンを明示し、再現手順と公式ドキュメント URL を添える。",
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
    ),
}
