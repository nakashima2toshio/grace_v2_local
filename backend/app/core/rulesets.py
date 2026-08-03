# backend/app/core/rulesets.py
"""文書レビュー（GRACE-Review）のルールセット定義。

設計: backend/docs/review_agent_spec.md §5。

`verticals.py` の `VerticalProfile`（Support 用の業界プロファイル）と役割は似るが、
Review は「1 プロファイル = N 個の検査ルール」を持つため型を分けている。
`VerticalProfile` に 21 個のルール定義を持たせると Support 用の構造が壊れるため。

## 2 つの使われ方

1. **検索スコープ**: `collections` を `config.qdrant.allowed_collections` へ注入し、
   ② Retrieve の `rag_search` を規程コレクションに限定する。
2. **判定基準**: `RuleItem.description` を ③ Detect の LLM プロンプトへ埋め込む。
   規程コレクションが未登録の場合、`description` と `article` が
   ④ Ground の**根拠フォールバック**になるため、条文の要点を自己完結的に書く。

## 二段判定との対応

- **第1段**（キーワード検出・LLM 不要）: `RuleItem.keywords` の部分一致で候補を絞る。
  `always_check=True` のルールは keywords 不問で必ず第2段へ進む（表記漏れの検出は
  キーワードでは拾えないため）。
- **第2段**（LLM 判定）: 候補になったルールのみ「実際に抵触するか」を判定する。

## ⚠️ 法務監修について

**本ルールセットは技術検証用のサンプルであり、法務レビューを受けていない。**
`description` は公開されている条文・ガイドラインの要点を要約したものだが、
実運用には法務部門による監修が必須である。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

# 指摘の重大度。⑤ Severity で確定する。
Severity = Literal["high", "medium", "low"]

# 指摘の確定状態。④' Suppress で確定する。
#   confirmed       : 根拠が十分。自動で指摘として確定
#   review_required : 人間の確認が必要（弱い根拠・重大リスク語・検証不能）
#   suppressed      : 誤検知として除外（件数のみ KPI に残す）
FindingStatus = Literal["confirmed", "review_required", "suppressed"]

# 既定のしきい値。RuleSet 側で上書きできる。
DEFAULT_NOTIFY_TH = 0.85
DEFAULT_CONFIRM_TH = 0.60


@dataclass
class RuleItem:
    """1 個の検査ルール。UI の指摘カードに表示する条文情報の供給源でもある。"""

    rule_id: str                 # "keihyo-01"（RuleSet 内で一意）
    title: str                   # "優良誤認表示"
    category: str                # "優良誤認" — UI のグルーピングに使う
    law: str                     # "景品表示法"
    article: str                 # "第5条第1号"
    description: str             # 判定基準。LLM プロンプトへ埋め込む／根拠フォールバック
    keywords: List[str] = field(default_factory=list)  # 第1段の候補検出語
    severity_default: Severity = "medium"              # ⑤ の基準値
    always_check: bool = False   # True なら keywords 不問で第2段を必ず実行
    web_check: bool = False      # True なら ⑥ Web 裏取りの対象

    def citation(self) -> str:
        """④ Ground へ渡す根拠フォールバック（規程コレクション未登録時に使う）。"""
        return f"[規程] {self.law} {self.article}（{self.title}）: {self.description}"


@dataclass
class RuleSet:
    """業界プロファイル 1 つ分の検査ルール束。"""

    id: str                                   # "ec_ad"
    name: str                                 # "EC広告表示"
    collections: List[str] = field(default_factory=list)   # 規程 Qdrant コレクション
    rules: List[RuleItem] = field(default_factory=list)
    # 強制 high + review_required にする文言。二段判定（語→意図分類）で誤検知を抑止する。
    critical_keywords: List[str] = field(default_factory=list)
    notify_th: float = DEFAULT_NOTIFY_TH      # これ以上は confirmed（自動確定）
    confirm_th: float = DEFAULT_CONFIRM_TH    # これ未満は誤検知抑止の対象
    action_map: Dict[str, str] = field(default_factory=dict)
    prompt_addendum: str = ""                 # ③ Detect のプロンプトへ注入する方針

    def rule_by_id(self, rule_id: str) -> Optional[RuleItem]:
        """`rule_id` から `RuleItem` を引く（見つからなければ None）。"""
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        return None

    @property
    def always_check_rules(self) -> List[RuleItem]:
        """keywords 不問で毎回第2段へ進めるルール（表記漏れ検出用）。"""
        return [r for r in self.rules if r.always_check]

    @property
    def keyword_rules(self) -> List[RuleItem]:
        """第1段のキーワード一致を経てから第2段へ進むルール。"""
        return [r for r in self.rules if not r.always_check]


# =============================================================================
# ec_ad — EC広告表示（景品表示法 / 医薬品医療機器等法 / 特定商取引法）
# =============================================================================

# --- 景品表示法（12件）------------------------------------------------------
_KEIHYO_RULES: List[RuleItem] = [
    RuleItem(
        rule_id="keihyo-01",
        title="優良誤認表示",
        category="優良誤認",
        law="景品表示法",
        article="第5条第1号",
        description=(
            "商品・サービスの品質、規格その他の内容について、実際のものよりも著しく優良であると"
            "一般消費者に誤認させる表示は禁止される。最上級・唯一性を主張する表現は、"
            "客観的な裏付け資料がない限り優良誤認に該当しうる。"
        ),
        keywords=["最高", "最強", "世界初", "日本初", "唯一", "究極", "完璧", "他にはない"],
        severity_default="high",
    ),
    RuleItem(
        rule_id="keihyo-02",
        title="有利誤認表示",
        category="有利誤認",
        law="景品表示法",
        article="第5条第2号",
        description=(
            "価格その他の取引条件について、実際のものまたは競争事業者のものよりも著しく有利で"
            "あると一般消費者に誤認させる表示は禁止される。他社比較で安さを主張する場合は、"
            "比較対象・比較時点・調査方法を明示する必要がある。"
        ),
        keywords=["業界最安", "他社より安", "最安値", "底値", "どこよりも安"],
        severity_default="high",
    ),
    RuleItem(
        rule_id="keihyo-03",
        title="No.1表示の根拠",
        category="優良誤認",
        law="景品表示法",
        article="第5条第1号",
        description=(
            "No.1・第1位等の最上級表示を行う場合、客観的な調査に基づくものであり、かつ"
            "調査結果を正確・適正に引用する必要がある。調査機関名・調査期間・調査対象・"
            "出典の明示がない No.1 表示は不当表示に該当しうる。"
        ),
        keywords=["No.1", "NO.1", "ナンバーワン", "第1位", "シェア1位", "売上1位", "満足度1位"],
        severity_default="high",
        web_check=True,
    ),
    RuleItem(
        rule_id="keihyo-04",
        title="二重価格表示",
        category="有利誤認",
        law="景品表示法",
        article="第5条第2号",
        description=(
            "販売価格に比較対照価格を併記する場合、比較対照価格が最近相当期間にわたって"
            "販売された実績のある価格でなければならない。実売実績のない「通常価格」「定価」"
            "との比較は有利誤認に該当しうる。"
        ),
        keywords=["通常価格", "定価", "メーカー希望小売価格", "元値", "参考価格", "旧価格"],
        severity_default="high",
        web_check=True,
    ),
    RuleItem(
        rule_id="keihyo-05",
        title="打消し表示の明瞭性",
        category="打消し表示",
        law="景品表示法",
        article="第5条",
        description=(
            "強調表示に例外や制約がある場合、その打消し表示は一般消費者が認識できるよう"
            "明瞭に表示する必要がある。文字が小さい、強調表示から離れている、"
            "表示時間が短い等の打消し表示は、実質的に表示していないものと評価されうる。"
        ),
        keywords=["※", "注)", "個人の感想", "効果を保証するものではありません", "一部対象外"],
        severity_default="medium",
        web_check=True,
    ),
    RuleItem(
        rule_id="keihyo-06",
        title="体験談の一般化",
        category="優良誤認",
        law="景品表示法",
        article="第5条第1号",
        description=(
            "体験談を用いる場合、それが一般的に得られる効果であるかのように示すことは"
            "優良誤認に該当しうる。体験者の属性、体験談の件数、平均的な効果との差異を"
            "明示せずに効果を訴求する表示は不当表示となる可能性がある。"
        ),
        keywords=["体験談", "お客様の声", "使ってみたら", "実感しました", "変わりました"],
        severity_default="medium",
    ),
    RuleItem(
        rule_id="keihyo-07",
        title="期間限定表示の常態化",
        category="有利誤認",
        law="景品表示法",
        article="第5条第2号",
        description=(
            "「今だけ」「期間限定」等の表示を行いながら、実際には同一条件での販売を"
            "継続している場合、取引条件が実際よりも有利であると誤認させる表示に該当しうる。"
            "期限を明示し、期限経過後は速やかに表示を変更する必要がある。"
        ),
        keywords=["今だけ", "期間限定", "本日限り", "今なら", "このチャンス"],
        severity_default="medium",
    ),
    RuleItem(
        rule_id="keihyo-08",
        title="無料表示の条件不記載",
        category="有利誤認",
        law="景品表示法",
        article="第5条第2号",
        description=(
            "「無料」「0円」と表示する場合、無料となる条件（購入金額、会員登録、"
            "継続回数等）を明瞭に併記する必要がある。条件を記載せず無料を強調する表示は"
            "有利誤認に該当しうる。"
        ),
        keywords=["無料", "0円", "タダ", "フリー", "実質無料"],
        severity_default="medium",
    ),
    RuleItem(
        rule_id="keihyo-09",
        title="数量限定の根拠",
        category="有利誤認",
        law="景品表示法",
        article="第5条第2号",
        description=(
            "「限定」「先着」「在庫僅少」等の表示は、実際の在庫数量・販売数量の裏付けが"
            "必要である。実態を伴わない数量限定表示は取引条件の有利誤認に該当しうる。"
        ),
        keywords=["限定", "先着", "在庫僅少", "残りわずか", "ラスト"],
        severity_default="low",
    ),
    RuleItem(
        rule_id="keihyo-10",
        title="おとり広告",
        category="おとり広告",
        law="景品表示法",
        article="第5条第3号",
        description=(
            "広告した商品を実際には供給できない、または供給量が著しく限定されているにも"
            "かかわらず、その旨を明示せずに広告することは、おとり広告として不当表示に"
            "該当する。特価品・目玉商品の表示には供給可能数の明示が必要である。"
        ),
        keywords=["特価", "目玉", "売切れ次第", "在庫限り", "赤字覚悟"],
        severity_default="high",
    ),
    RuleItem(
        rule_id="keihyo-11",
        title="原産国の誤認",
        category="原産国",
        law="景品表示法",
        article="第5条第3号",
        description=(
            "商品の原産国について一般消費者に誤認させる表示は禁止される。国内で最終的な"
            "加工のみを行った輸入品を「国産」「日本製」と表示することは、"
            "原産国告示に照らして不当表示に該当しうる。"
        ),
        keywords=["国産", "日本製", "made in", "MADE IN", "産地直送", "純国産"],
        severity_default="medium",
    ),
    RuleItem(
        rule_id="keihyo-12",
        title="景品類の限度額",
        category="景品類",
        law="景品表示法",
        article="第4条",
        description=(
            "懸賞により提供する景品類の最高額は取引価額の20倍かつ10万円以内、"
            "総額は懸賞に係る売上予定総額の2%以内に制限される。総付景品は"
            "取引価額の20%（取引価額1000円未満は200円）以内である。"
        ),
        keywords=["プレゼント", "抽選", "もれなく", "景品", "当たる", "キャンペーン"],
        severity_default="low",
    ),
]

# --- 医薬品医療機器等法（3件）-----------------------------------------------
_YAKKI_RULES: List[RuleItem] = [
    RuleItem(
        rule_id="yakki-01",
        title="食品の医薬品的効能標榜",
        category="効能効果",
        law="医薬品医療機器等法",
        article="第68条",
        description=(
            "医薬品としての承認を受けていない食品・健康食品について、疾病の治療・予防を"
            "目的とする効能効果を標榜することは禁止される。特定の疾病名を挙げて改善を"
            "訴求する表現は、医薬品的効能効果の標榜に該当しうる。"
        ),
        keywords=["治る", "治療", "改善", "予防", "効く", "病気", "症状", "免疫力"],
        severity_default="high",
        web_check=True,
    ),
    RuleItem(
        rule_id="yakki-02",
        title="化粧品の効能範囲逸脱",
        category="効能効果",
        law="医薬品医療機器等法",
        article="第66条",
        description=(
            "化粧品の広告で標榜できる効能効果は、告示で定められた56項目の範囲に限られる。"
            "シワの除去、細胞の再生、恒久的な美白等、範囲を超える効果の標榜は"
            "虚偽・誇大広告に該当しうる。"
        ),
        keywords=["シワが消える", "若返る", "美白", "アンチエイジング", "細胞再生", "永久"],
        severity_default="high",
    ),
    RuleItem(
        rule_id="yakki-03",
        title="医療機器的性能の標榜",
        category="効能効果",
        law="医薬品医療機器等法",
        article="第68条",
        description=(
            "医療機器としての承認・認証を受けていない雑貨について、身体の構造・機能に"
            "影響を及ぼす性能を標榜することは禁止される。血行促進・筋肉増強・痩身等の"
            "効果訴求は医療機器的性能の標榜に該当しうる。"
        ),
        keywords=["血行促進", "筋肉増強", "痩身", "医療用", "治療器", "コリをほぐす"],
        severity_default="medium",
    ),
]

# --- 特定商取引法（6件・すべて always_check）---------------------------------
# 表記漏れの検出はキーワードでは拾えないため、文書全体に対して常時チェックする。
_TOKUSHO_RULES: List[RuleItem] = [
    RuleItem(
        rule_id="tokusho-01",
        title="販売価格・送料の明示",
        category="表記漏れ",
        law="特定商取引法",
        article="第11条",
        description=(
            "通信販売の広告には、商品の販売価格（消費税込み）を表示する必要がある。"
            "送料が別途必要な場合は送料の額も表示しなければならない。"
            "価格または送料の記載が無い、もしくは税込／税別が不明瞭な場合は表示義務違反となる。"
        ),
        severity_default="high",
        always_check=True,
    ),
    RuleItem(
        rule_id="tokusho-02",
        title="代金の支払時期・方法",
        category="表記漏れ",
        law="特定商取引法",
        article="第11条",
        description=(
            "通信販売の広告には、代金の支払時期および支払方法を表示する必要がある。"
            "利用可能な決済手段と、前払い／後払いの別が読み取れない広告は表示義務違反となる。"
        ),
        severity_default="medium",
        always_check=True,
    ),
    RuleItem(
        rule_id="tokusho-03",
        title="商品の引渡時期",
        category="表記漏れ",
        law="特定商取引法",
        article="第11条",
        description=(
            "通信販売の広告には、商品の引渡時期（発送時期・お届け目安）を表示する必要がある。"
            "注文からどの程度で商品が届くかが読み取れない広告は表示義務違反となる。"
        ),
        severity_default="medium",
        always_check=True,
    ),
    RuleItem(
        rule_id="tokusho-04",
        title="返品特約の表示",
        category="表記漏れ",
        law="特定商取引法",
        article="第11条",
        description=(
            "通信販売には法定のクーリング・オフが無いため、返品の可否・条件・期限・"
            "送料負担を広告に表示する必要がある。返品特約の表示が無い場合、"
            "商品到着後8日間は送料購入者負担で返品が可能となる。"
        ),
        severity_default="high",
        always_check=True,
    ),
    RuleItem(
        rule_id="tokusho-05",
        title="事業者名・住所・連絡先",
        category="表記漏れ",
        law="特定商取引法",
        article="第11条",
        description=(
            "通信販売の広告には、事業者の氏名（名称）、住所、電話番号を表示する必要がある。"
            "法人の場合は代表者名または業務責任者名も必要である。"
            "これらが広告本体または明示的なリンク先に無い場合は表示義務違反となる。"
        ),
        severity_default="high",
        always_check=True,
    ),
    RuleItem(
        rule_id="tokusho-06",
        title="定期購入の条件明示",
        category="表記漏れ",
        law="特定商取引法",
        article="第12条の6",
        description=(
            "定期購入契約の場合、契約期間・継続回数・支払総額・解約条件を"
            "申込みの最終確認画面および広告に明示する必要がある。"
            "初回価格のみを強調し、継続回数の縛りや2回目以降の価格・総額を"
            "明示しない表示は、不実表示として禁止される。"
        ),
        severity_default="high",
        always_check=True,
        web_check=True,
    ),
]

EC_AD = RuleSet(
    id="ec_ad",
    name="EC広告表示",
    collections=["ec_ad_rules_anthropic", "ec_policy_anthropic"],
    rules=[*_KEIHYO_RULES, *_YAKKI_RULES, *_TOKUSHO_RULES],
    critical_keywords=[
        # 「必ず人が見るべき」高リスク文言。一致しても意図分類で誤検知は抑止する
        # （例:「当社は No.1 という表現を使いません」は方針表明なので強制しない）。
        "No.1", "NO.1", "ナンバーワン", "日本一", "世界一",
        "最安", "業界最", "完治", "治る", "がん", "医薬品",
        "副作用がない", "絶対", "100%",
    ],
    # 法令チェックは誤指摘のコストが高いため、Support の既定（gov=0.8）より厳しくする。
    notify_th=0.85,
    confirm_th=0.60,
    action_map={"修正": "create_ticket", "差し戻し": "send_reply"},
    prompt_addendum=(
        "景品表示法・特定商取引法・医薬品医療機器等法の条文に基づいて判定し、"
        "該当条項番号を必ず明示すること。断定を避け、根拠のない指摘はしないこと。"
    ),
)


# 組み込みルールセット。API（GET /api/rulesets）と ① S1 の解決に使う。
RULESETS: Dict[str, RuleSet] = {
    EC_AD.id: EC_AD,
}


def get_ruleset(ruleset_id: Optional[str]) -> Optional[RuleSet]:
    """ID から RuleSet を解決する。未指定・未知の ID は None（既定しきい値で動作）。"""
    if not ruleset_id:
        return None
    return RULESETS.get(ruleset_id)
