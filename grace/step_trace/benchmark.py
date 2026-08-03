"""
GRACE Benchmark Logger

GRACEエージェントの各フェーズ（Plan / Execute / Confidence /
Intervention / Replan）の性能指標を計測・記録・CSV出力するモジュール。

使用例::

    from grace.benchmark import BenchmarkRunner, BENCHMARK_QUERIES

    runner = BenchmarkRunner()          # config.llm.model / provider を自動取得
    session = runner.run(
        query_id="Q01",
        query_text="cc_newsから最近のAIニュースを3件教えて",
        level="Easy",
        category="事実検索",
    )

    # Qdrantコレクションを明示指定して全クエリセットを３回ずつ実行
    runner = BenchmarkRunner(qdrant_collection="cc_news_2per_anthropic")
    sessions = runner.run_query_set(runs_per_query=3)
"""

from __future__ import annotations

import csv
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

BENCHMARK_LOG_DIR = Path("logs")
BENCHMARK_CSV_PATH = BENCHMARK_LOG_DIR / "benchmark_results.csv"

CSV_HEADERS: List[str] = [
    "timestamp", "session_id", "query_id", "query_text_short",
    "level", "category", "agent_path", "expected_case", "agent_mode",
    "model", "provider", "run_number",
    # Plan フェーズ
    "plan_time_sec", "plan_complexity", "plan_steps", "requires_confirmation",
    # Execute フェーズ
    "execute_time_sec", "total_time_sec",
    "tool_calls", "rag_step_count", "sources_total",
    # Confidence
    "overall_confidence", "min_step_confidence", "max_step_confidence",
    # Intervention
    "intervention_level",
    # Replan
    "replan_count", "overall_status",
    # 検索結果ハンドリング評価（3-3: ドメイン網羅 → 検索ハンドリング）
    "rag_top_score", "rag_hit_in_target", "web_fallback_fired",
    "route_correct", "replan_converged",
    # Cost / Tokens
    "input_tokens", "output_tokens", "cost_usd",
    # 品質スコア（後付け: LLM-Judge または手動）
    "accuracy_score", "completeness_score",
]

# ---------------------------------------------------------------------------
# 標準クエリセット
# ---------------------------------------------------------------------------

# 標準クエリセットは Qdrant コレクション ``cc_news_2per_anthropic`` の実データ
# （海外一般ニュースの日本語Q&A: NFL・Amazon在宅勤務・PC製品・裁判・Google製品・
# ハリケーン等。代表100件は docs/data_100.txt 参照）に対応させ、
# GRACE の各処理経路（"path"）を満遍なく通過するよう設計している。
#
# path で狙う GRACE の分岐:
#   - rule_plan   : 複雑度 < 0.7 → ルールベース計画（LLM呼び出しなし）
#   - llm_plan    : 複雑度 >= 0.7 または Web検索マーカーで LLM 計画生成
#   - rag_hit     : RAG 検索ヒット → 高信頼 → SILENT
#   - multi_rag   : 複数ステップ RAG → 推論・統合
#   - web_fallback: RAG 不十分 → web_search を動的挿入（fallback_chain）
#   - intervention: 曖昧クエリ → 低信頼 → CONFIRM / ESCALATE
#   - ask_user    : 一致なし → ask_user フォールバック
#   - replan      : ステップ失敗 → PARTIAL / FULL リプラン
# 各クエリには ``case`` (A〜E) と ``expected`` (期待挙動) を付与する。
# これにより「ドメイン網羅」ではなく「検索結果スコアに応じた分岐ハンドリング」を
# 自動採点できる（3-1 / 3-4）。
#
# case の定義:
#   A: 高スコア命中  — コレクション内の確実な実問。RAGのみ完結・web/replanを起こさない
#   B: 中スコア境界  — 言い換え/複数統合。rag_sufficient_score判定が働き必要時のみweb
#   C: 低スコア不一致 — コレクション外トピック。RAG不十分検知 → web_searchへ切替
#   D: 要リプラン    — 1段目失敗構造。replanが発火し かつ収束する（無限ループしない）
#   E: 曖昧          — ask_user / CONFIRM・ESCALATE を正しく選ぶ
#
# expected のキー:
#   intervention   : 許容する介入レベル集合（None=不問）
#   web            : web_fallback を期待するか（True/False/None=不問）
#   replan         : リプラン発火＋収束を期待するか（True/False/None=不問）
#   min_rag_score  : 期待する RAG 最高スコア下限（None=不問）
BENCHMARK_QUERIES: List[Dict[str, Any]] = [
    # ── case A: 高スコア命中（RAGのみ完結・SILENT/NOTIFY・web/replanなし）──
    {"id": "Q01", "level": "Easy",   "category": "事実検索",  "path": "rule_plan+rag_hit", "case": "A",
     "text": "Amazonが在宅勤務向けに今後募集する新規職は何件ですか？",
     "expected": {"intervention": ["SILENT", "NOTIFY"], "web": False, "replan": False, "min_rag_score": 0.6}},
    {"id": "Q02", "level": "Easy",   "category": "事実検索",  "path": "rule_plan+rag_hit", "case": "A",
     "text": "Razer Blade Stealthの13.3インチモデルの価格と画面解像度を教えてください",
     "expected": {"intervention": ["SILENT", "NOTIFY"], "web": False, "replan": False, "min_rag_score": 0.6}},
    {"id": "Q07", "level": "Easy",   "category": "手順説明",   "path": "rule_plan+rag_hit", "case": "A",
     "text": "ネバダ州のNHP警察官の身体カメラについて、契約金額・対象人数・施行時期を教えてください",
     "expected": {"intervention": ["SILENT", "NOTIFY"], "web": False, "replan": False, "min_rag_score": 0.6}},
    # ── case B: 中スコア境界（複数ソース統合・必要時のみweb・介入は妥当範囲）──
    {"id": "Q03", "level": "Medium", "category": "推論・比較", "path": "llm_plan+multi_rag", "case": "B",
     "text": "サンフランシスコ49ersのGMとヘッドコーチの人事異動について、複数の記事を比較して経緯を説明してください",
     "expected": {"intervention": ["SILENT", "NOTIFY", "CONFIRM"], "web": None, "replan": None, "min_rag_score": None}},
    {"id": "Q04", "level": "Medium", "category": "推論・比較", "path": "llm_plan+multi_rag", "case": "B",
     "text": "スポーツ選手の給与をめぐる不満の事例を複数挙げ、それぞれの主張の違いを比較してください",
     "expected": {"intervention": ["SILENT", "NOTIFY", "CONFIRM"], "web": None, "replan": None, "min_rag_score": None}},
    {"id": "Q05", "level": "Hard",   "category": "推論・比較", "path": "llm_plan+multi_rag", "case": "B",
     "text": "Googleがカメラ・写真・プライバシー・スマートホーム分野で進めている取り組みを、複数の記事から根拠を挙げて統合的に説明してください",
     "expected": {"intervention": ["SILENT", "NOTIFY", "CONFIRM"], "web": None, "replan": None, "min_rag_score": None}},
    {"id": "Q06", "level": "Hard",   "category": "手順説明",   "path": "llm_plan+multi_rag", "case": "B",
     "text": "ハーヴェイ・ワインスタインが起訴された経緯と罪状の違いを詳しく整理して説明してください",
     "expected": {"intervention": ["SILENT", "NOTIFY", "CONFIRM"], "web": None, "replan": None, "min_rag_score": None}},
    {"id": "Q08", "level": "Medium", "category": "推論・比較", "path": "llm_plan+multi_rag", "case": "B",
     "text": "テクノロジーと金融セクターが今後好調と予想される理由を、複数の記事の根拠を挙げて説明してください",
     "expected": {"intervention": ["SILENT", "NOTIFY", "CONFIRM"], "web": None, "replan": None, "min_rag_score": None}},
    # ── case E: 曖昧（低信頼 → CONFIRM / ESCALATE / ask_user）──
    {"id": "Q09", "level": "Easy",   "category": "曖昧",       "path": "intervention", "case": "E",
     "text": "最近の重要なニュースを教えて",
     "expected": {"intervention": ["CONFIRM", "ESCALATE"], "web": None, "replan": None, "min_rag_score": None}},
    {"id": "Q10", "level": "Easy",   "category": "曖昧",       "path": "ask_user+intervention", "case": "E",
     "text": "あの件について詳しく教えて",
     "expected": {"intervention": ["CONFIRM", "ESCALATE"], "web": None, "replan": None, "min_rag_score": None}},
    # ── case C: 低スコア不一致（コレクション外 → web_searchへ切替）──
    {"id": "Q11", "level": "Hard",   "category": "Web・回復",  "path": "llm_plan+web_fallback", "case": "C",
     "text": "2025年の暗号資産（ビットコイン）市場の最新ニュースを検索して、価格動向をまとめてください",
     "expected": {"intervention": None, "web": True, "replan": None, "min_rag_score": None}},
    # ── case D: 検索ミス → 回復 ────────────────────────────────────────────────
    # Q12: RAG が低スコア（非空）で命中せず、動的 web フォールバックで回復するケース。
    #      この経路は replan_count を増やさない（成功扱いの低スコア → web 動的挿入）ため、
    #      期待は「web で回復」（replan は不問）とする。
    {"id": "Q12", "level": "Hard",   "category": "Web・回復",  "path": "rag_miss+web_fallback", "case": "D",
     "text": "コレクションに存在しないトピック（日本の量子コンピュータ政策）を検索し、情報が不足した場合のフォールバックの過程を示してください",
     "expected": {"intervention": None, "web": True, "replan": None, "min_rag_score": None}},
    # Q13: 真に replan を強制するケース。
    #      ``force_collection`` で初回 RAG を存在しないコレクションへ向け、結果ゼロ →
    #      ステップ failed → _should_trigger_replan が必ず発火 → 回復プランへ差し替え。
    #      期待は「replan が発火し かつ 上限内で収束」。web は回復手段に依存するため不問。
    {"id": "Q13", "level": "Hard",   "category": "Web・回復",  "path": "forced_replan+recovery", "case": "D",
     "text": "ナレッジベースを検索して、2027年のG7サミット開催地に関する公式発表をまとめてください。該当情報が見つからない場合は代替手段で補完してください",
     "expected": {"intervention": None, "web": None, "replan": True, "min_rag_score": None},
     "force_collection": "__grace_bench_missing_collection__"},
]

# 高速完了モード（--fast）で実行する代表クエリ ID。
# 検索ハンドリングの 5 ケース（A:高スコア命中 / B:中スコア境界 / C:低スコア不一致 /
# D:要リプラン / E:曖昧）を 1 本ずつ最小構成で通過させる。
FAST_QUERY_IDS: List[str] = ["Q01", "Q03", "Q11", "Q13", "Q10"]


def select_queries(
    query_ids: Optional[List[str]] = None,
    limit: Optional[int] = None,
    fast: bool = False,
    queries: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """
    実行対象クエリを絞り込むヘルパー。

    Args:
        query_ids: 実行する ID のリスト（例: ["Q01", "Q03"]）。指定時はこれを最優先。
        limit:     先頭から実行する件数。
        fast:      True の場合 ``FAST_QUERY_IDS`` の代表クエリのみを実行。
        queries:   元のクエリリスト（省略時は ``BENCHMARK_QUERIES``）。

    Returns:
        絞り込み後のクエリリスト（元の定義順を保持）。
    """
    base = queries or BENCHMARK_QUERIES
    if query_ids:
        wanted = set(query_ids)
        selected = [q for q in base if q["id"] in wanted]
    elif fast:
        wanted = set(FAST_QUERY_IDS)
        selected = [q for q in base if q["id"] in wanted]
    else:
        selected = list(base)
    if limit is not None:
        selected = selected[:limit]
    return selected


# ---------------------------------------------------------------------------
# BenchmarkSession
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSession:
    """
    1回の実行セッションのベンチマークデータを保持するデータクラス。

    タイミングは ``time.monotonic()`` で計測し、plan_time_sec /
    execute_time_sec / total_time_sec は property で計算する。
    """

    # ── Identity ────────────────────────────────────────────────────────────────────────
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    query_id: str = ""
    query_text: str = ""
    level: str = ""         # Easy / Medium / Hard
    category: str = ""      # 事実検索 / 推論・比較 / 手順説明 / 曖昧 / Web・回復
    agent_path: str = ""    # 狙った GRACE 処理経路（BENCHMARK_QUERIES の "path"）
    expected_case: str = "" # 検索ハンドリングケース A〜E（BENCHMARK_QUERIES の "case"）
    agent_mode: str = "grace_dynamic"  # grace_dynamic / react
    expected: Dict[str, Any] = field(default_factory=dict)  # 期待挙動（CSV非出力）
    model: str = ""
    provider: str = ""
    run_number: int = 1
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # ── Phase タイミング（monotonic秒） ───────────────────────────────────────────────
    plan_start: float = 0.0
    plan_end: float = 0.0
    execute_start: float = 0.0
    execute_end: float = 0.0

    # ── Plan フェーズ指標 ──────────────────────────────────────────────────────────────────────
    plan_complexity: float = 0.0
    plan_steps: int = 0
    requires_confirmation: bool = False
    plan_id: str = ""

    # ── Execute フェーズ指標 ───────────────────────────────────────────────────────────────────
    tool_calls: int = 0         # 実行された全ステップ数
    rag_step_count: int = 0     # rag_search アクションのステップ数
    sources_total: int = 0      # 全ステップのソース数合計

    # ── Confidence 指標 ─────────────────────────────────────────────────────────────────────
    step_confidences: List[float] = field(default_factory=list)
    overall_confidence: float = 0.0

    # ── Intervention ──────────────────────────────────────────────────────────────────────────
    intervention_level: str = ""   # SILENT / NOTIFY / CONFIRM / ESCALATE

    # ── Replan ──────────────────────────────────────────────────────────────────────────────
    replan_count: int = 0
    overall_status: str = ""

    # ── 検索結果ハンドリング評価（3-3） ─────────────────────────────────────────────────
    rag_top_score: float = 0.0          # RAG 最高スコア（命中度の生値）
    rag_hit_in_target: Optional[bool] = None   # 狙ったコレクションから十分なスコアで取れたか
    web_fallback_fired: Optional[bool] = None  # web_search へ切替が起きたか
    route_correct: Optional[bool] = None       # 期待経路と実経路の一致（Agent正解率）
    replan_converged: Optional[bool] = None     # リプランが上限内で収束したか

    # ── Cost / Tokens ──────────────────────────────────────────────────────────────────────
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    # ── 品質スコア（後付け） ───────────────────────────────────────────────────────────────────
    accuracy_score: Optional[float] = None
    completeness_score: Optional[float] = None

    # ── Properties ────────────────────────────────────────────────────────────────────────

    @property
    def plan_time_sec(self) -> float:
        """計画生成フェーズの所要時間（秒）"""
        if self.plan_end > 0 and self.plan_start > 0:
            return round(self.plan_end - self.plan_start, 3)
        return 0.0

    @property
    def execute_time_sec(self) -> float:
        """実行フェーズの所要時間（秒）"""
        if self.execute_end > 0 and self.execute_start > 0:
            return round(self.execute_end - self.execute_start, 3)
        return 0.0

    @property
    def total_time_sec(self) -> float:
        """Plan + Execute の合計所要時間（秒）"""
        start = self.plan_start if self.plan_start > 0 else self.execute_start
        end   = self.execute_end if self.execute_end > 0 else self.plan_end
        if start > 0 and end > 0:
            return round(end - start, 3)
        return round(self.plan_time_sec + self.execute_time_sec, 3)

    @property
    def min_step_confidence(self) -> float:
        """ステップ信頼度の最小値"""
        return round(min(self.step_confidences), 3) if self.step_confidences else 0.0

    @property
    def max_step_confidence(self) -> float:
        """ステップ信頼度の最大値"""
        return round(max(self.step_confidences), 3) if self.step_confidences else 0.0

    def to_csv_row(self) -> Dict[str, Any]:
        """CSV 1行分の辞書を返す"""
        return {
            "timestamp":           self.timestamp,
            "session_id":          self.session_id,
            "query_id":            self.query_id,
            "query_text_short":    self.query_text[:50].replace("\n", " "),
            "level":               self.level,
            "category":            self.category,
            "agent_path":          self.agent_path,
            "expected_case":       self.expected_case,
            "agent_mode":          self.agent_mode,
            "model":               self.model,
            "provider":            self.provider,
            "run_number":          self.run_number,
            "plan_time_sec":       self.plan_time_sec,
            "plan_complexity":     round(self.plan_complexity, 3),
            "plan_steps":          self.plan_steps,
            "requires_confirmation": self.requires_confirmation,
            "execute_time_sec":    self.execute_time_sec,
            "total_time_sec":      self.total_time_sec,
            "tool_calls":          self.tool_calls,
            "rag_step_count":      self.rag_step_count,
            "sources_total":       self.sources_total,
            "overall_confidence":  round(self.overall_confidence, 3),
            "min_step_confidence": self.min_step_confidence,
            "max_step_confidence": self.max_step_confidence,
            "intervention_level":  self.intervention_level,
            "replan_count":        self.replan_count,
            "overall_status":      self.overall_status,
            "rag_top_score":       round(self.rag_top_score, 4),
            "rag_hit_in_target":   self.rag_hit_in_target,
            "web_fallback_fired":  self.web_fallback_fired,
            "route_correct":       self.route_correct,
            "replan_converged":    self.replan_converged,
            "input_tokens":        self.input_tokens,
            "output_tokens":       self.output_tokens,
            "cost_usd":            round(self.cost_usd, 6),
            "accuracy_score":      self.accuracy_score,
            "completeness_score":  self.completeness_score,
        }


# ---------------------------------------------------------------------------
# BenchmarkLogger
# ---------------------------------------------------------------------------

class BenchmarkLogger:
    """
    BenchmarkSession の内容を
    - ``[BENCHMARK]`` プレフィックス付きのフォーマットログ
    - ``logs/benchmark_results.csv`` への CSV 追記
    の両形式で出力する。
    """

    # Confidence → InterventionLevel の閾値（grace/config.py の ConfidenceThresholds と一致）
    _THRESH_SILENT:  float = 0.9
    _THRESH_NOTIFY:  float = 0.7
    _THRESH_CONFIRM: float = 0.4

    def __init__(self, csv_path: Optional[Path] = None, config: Any = None) -> None:
        self.csv_path = csv_path or BENCHMARK_CSV_PATH
        # route メトリクス算出に使う config（BenchmarkRunner から注入）。
        self._config_ref = config
        BENCHMARK_LOG_DIR.mkdir(exist_ok=True)
        self._ensure_csv_headers()

    def _ensure_csv_headers(self) -> None:
        """CSV ヘッダーを保証する。

        - ファイルが無ければヘッダーを書く。
        - 既存ファイルのヘッダーが現行 ``CSV_HEADERS`` と不一致なら、旧ファイルを
          タイムスタンプ付きでバックアップ退避してから新規にヘッダーを書き直す
          （スキーマ変更時の列ズレ追記を防止する）。
        """
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=CSV_HEADERS).writeheader()
            return

        # 既存ヘッダーを読み取り、現行スキーマと一致するか検査
        try:
            with open(self.csv_path, "r", newline="", encoding="utf-8") as fh:
                existing_header = next(csv.reader(fh), [])
        except Exception:
            existing_header = []

        if existing_header == CSV_HEADERS:
            return

        # スキーマ不一致 → 旧ファイルを退避して作り直す
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = self.csv_path.with_name(
            f"{self.csv_path.stem}.{ts}.bak{self.csv_path.suffix}"
        )
        try:
            self.csv_path.rename(backup)
            logger.warning(
                "[BENCHMARK] CSV schema mismatch detected. "
                "Rotated old file -> %s (new schema: %d cols)",
                backup, len(CSV_HEADERS),
            )
        except Exception as e:
            logger.error("[BENCHMARK] CSV rotation failed: %s", e)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=CSV_HEADERS).writeheader()

    # ── record helpers ──────────────────────────────────────────────────────────────────────

    def record_plan_result(self, session: BenchmarkSession, plan: Any) -> None:
        """
        Planner.create_plan() が返した ExecutionPlan の指標を session に記録する。

        Args:
            session: 記録対象のセッション
            plan: grace.schemas.ExecutionPlan インスタンス
        """
        session.plan_complexity       = getattr(plan, "complexity", 0.0)
        session.plan_steps            = len(getattr(plan, "steps", []))
        session.requires_confirmation = getattr(plan, "requires_confirmation", False)
        session.plan_id               = getattr(plan, "plan_id", "") or ""

        logger.debug(
            "[BENCHMARK] plan recorded: steps=%d complexity=%.2f requires_conf=%s",
            session.plan_steps, session.plan_complexity, session.requires_confirmation,
        )

    def record_execution_result(self, session: BenchmarkSession, result: Any) -> None:
        """
        Executor.execute() が返した ExecutionResult の指標を session に記録する。

        Args:
            session: 記録対象のセッション
            result: grace.schemas.ExecutionResult インスタンス
        """
        session.overall_confidence = getattr(result, "overall_confidence", 0.0)
        session.replan_count       = getattr(result, "replan_count", 0)
        session.overall_status     = getattr(result, "overall_status", "")

        # total_execution_time_ms が付いている場合はそちらも参照
        exec_ms = getattr(result, "total_execution_time_ms", None)
        if exec_ms and session.execute_time_sec == 0.0:
            session.execute_end = session.execute_start + exec_ms / 1000.0

        # Token usage（プロバイダーによってキー名が異なる）
        tu = getattr(result, "total_token_usage", None) or {}
        if isinstance(tu, dict):
            session.input_tokens  = (
                tu.get("input_tokens")  or tu.get("prompt_tokens")    or 0
            )
            session.output_tokens = (
                tu.get("output_tokens") or tu.get("completion_tokens") or 0
            )

        # Cost
        cost = getattr(result, "total_cost_usd", None)
        if cost is not None:
            session.cost_usd = float(cost)

        # ステップ別指標
        # RAG 最高スコアは executor が集約した result.rag_max_score を最優先で使う
        # （StepResult.output は表示用の整形済み文字列のため、ここから score は読めない）。
        # 数値でない値（None / 不正な型）は None に丸めて後段の比較を保護する。
        _rms = getattr(result, "rag_max_score", None)
        try:
            rag_top_score: Optional[float] = float(_rms) if _rms is not None else None
        except (TypeError, ValueError):
            rag_top_score = None
        # web 実行の有無は executor の web_search_used を最優先（replan とは別物）。
        web_fired = bool(getattr(result, "web_search_used", False))
        for step_result in getattr(result, "step_results", []):
            session.tool_calls += 1
            conf = getattr(step_result, "confidence", 0.0)
            session.step_confidences.append(conf)
            sources = getattr(step_result, "sources", []) or []
            if sources:
                session.sources_total += len(sources)
            # フォールバック: 万一 output が生の検索結果リスト（dict に "score"）の場合は抽出。
            # 初期値 None / 比較で負スコアも正しく扱う（0.0 への丸め込みを避ける）。
            out = getattr(step_result, "output", None)
            if isinstance(out, list):
                for item in out:
                    if isinstance(item, dict) and "score" in item:
                        try:
                            s_val = float(item["score"])
                        except (TypeError, ValueError):
                            continue
                        rag_top_score = s_val if rag_top_score is None else max(rag_top_score, s_val)
            # web_search 切替のフォールバック検出: ソースに URL を含む
            for s in sources:
                if isinstance(s, str) and s.startswith(("http://", "https://")):
                    web_fired = True

        # RAG ステップ数は executor の集計値を採用（web ステップを混同しない）。
        rsc = getattr(result, "rag_search_count", None)
        if isinstance(rsc, (int, float)) and not isinstance(rsc, bool):
            session.rag_step_count = int(rsc)
        else:
            # フォールバック: ソースを持つステップ数（旧挙動）
            session.rag_step_count = sum(
                1 for sr in getattr(result, "step_results", [])
                if getattr(sr, "sources", None)
            )

        session.rag_top_score = rag_top_score if rag_top_score is not None else 0.0

        # Confidence → Intervention Level
        session.intervention_level = self._score_to_intervention(
            session.overall_confidence
        )

        # 検索結果ハンドリング評価（3-3 / 3-4）
        self._compute_route_metrics(session, web_fired)

        logger.debug(
            "[BENCHMARK] execution recorded: confidence=%.3f replan=%d status=%s "
            "rag_top=%.3f route_correct=%s",
            session.overall_confidence, session.replan_count, session.overall_status,
            session.rag_top_score, session.route_correct,
        )

    def _compute_route_metrics(
        self, session: BenchmarkSession, web_fired: bool,
    ) -> None:
        """期待挙動（session.expected）と実行結果を突き合わせ、
        rag_hit_in_target / web_fallback_fired / route_correct / replan_converged を算出する。

        単一コレクション固定（restrict_to_collection）運用を前提とするため、
        十分なスコアで取れたヒットは「狙ったコレクションでの命中」と見なす。
        """
        from .config import get_config as _get_config

        cfg = getattr(self, "_config_ref", None) or _get_config()
        sufficient = getattr(cfg.qdrant, "rag_sufficient_score", 0.7)
        max_replans = getattr(getattr(cfg, "replan", None), "max_replans", 3)

        failed = session.overall_status == "failed"

        # rag_hit_in_target: 十分なスコアで RAG ヒットしたか
        session.rag_hit_in_target = session.rag_top_score >= sufficient

        # web_fallback_fired: 実際に web_search が実行されたかのみで判定する。
        # （リプラン発生＝web実行 ではないため、replan_count とは切り離す）
        session.web_fallback_fired = bool(web_fired)

        # replan_converged: 上限内で破綻せず収束したか
        converged = (session.replan_count <= max_replans) and not failed

        expected = session.expected or {}
        exp_replan = expected.get("replan")
        if exp_replan is True:
            # リプランが発火し かつ 収束していること
            session.replan_converged = converged and session.replan_count >= 1
        else:
            session.replan_converged = converged

        # route_correct: 期待した経路（介入 / web / スコア）に一致したか
        if not expected:
            session.route_correct = None
            return

        ok = not failed
        exp_intervention = expected.get("intervention")
        if exp_intervention:
            ok = ok and session.intervention_level in exp_intervention
        exp_web = expected.get("web")
        if exp_web is not None:
            ok = ok and (bool(session.web_fallback_fired) == bool(exp_web))
        exp_min = expected.get("min_rag_score")
        if exp_min is not None:
            ok = ok and session.rag_top_score >= exp_min
        if exp_replan is not None:
            ok = ok and bool(session.replan_converged)
        session.route_correct = ok

    def _score_to_intervention(self, score: float) -> str:
        """信頼度スコアを InterventionLevel 文字列に変換"""
        if score >= self._THRESH_SILENT:
            return "SILENT"
        if score >= self._THRESH_NOTIFY:
            return "NOTIFY"
        if score >= self._THRESH_CONFIRM:
            return "CONFIRM"
        return "ESCALATE"

    # ── output ─────────────────────────────────────────────────────────────────────────────

    def finalize_and_log(self, session: BenchmarkSession) -> None:
        """ベンチマーク結果をフォーマットして logger.info + print に出力"""
        sep  = "=" * 60
        dash = "-" * 58
        lines = [
            f"\n[BENCHMARK] {sep}",
            f"[BENCHMARK] Query    : {session.query_id} | {session.level} | {session.category}",
            f"[BENCHMARK] Model    : {session.model} ({session.provider}) | Run: {session.run_number}",
            f"[BENCHMARK] {dash}",
            "[BENCHMARK] [Plan]",
            f"[BENCHMARK]   生成時間       : {session.plan_time_sec:.2f} 秒",
            f"[BENCHMARK]   複雑度スコア   : {session.plan_complexity:.2f}",
            f"[BENCHMARK]   計画ステップ数 : {session.plan_steps}",
            f"[BENCHMARK]   要確認フラグ   : {session.requires_confirmation}",
            "[BENCHMARK] [Execute]",
            f"[BENCHMARK]   実行時間       : {session.execute_time_sec:.2f} 秒",
            f"[BENCHMARK]   合計時間       : {session.total_time_sec:.2f} 秒",
            f"[BENCHMARK]   ツール呼出回数 : {session.tool_calls}",
            f"[BENCHMARK]   RAGステップ数  : {session.rag_step_count}",
            f"[BENCHMARK]   ソース数合計   : {session.sources_total}",
            "[BENCHMARK] [Confidence]",
            f"[BENCHMARK]   全体信頼度     : {session.overall_confidence:.3f}",
            f"[BENCHMARK]   ステップ最小   : {session.min_step_confidence:.3f}",
            f"[BENCHMARK]   ステップ最大   : {session.max_step_confidence:.3f}",
            "[BENCHMARK] [Intervention]",
            f"[BENCHMARK]   Level          : {session.intervention_level}",
            "[BENCHMARK] [Replan]",
            f"[BENCHMARK]   リプラン回数   : {session.replan_count}",
            f"[BENCHMARK]   ステータス     : {session.overall_status}",
            "[BENCHMARK] [Routing / 検索ハンドリング]",
            f"[BENCHMARK]   Case           : {session.expected_case} | mode: {session.agent_mode}",
            f"[BENCHMARK]   RAG最高スコア  : {session.rag_top_score:.4f}",
            f"[BENCHMARK]   命中(target)   : {session.rag_hit_in_target}",
            f"[BENCHMARK]   web切替        : {session.web_fallback_fired}",
            f"[BENCHMARK]   リプラン収束   : {session.replan_converged}",
            f"[BENCHMARK]   経路一致(正解) : {session.route_correct}",
            "[BENCHMARK] [Cost / Tokens]",
            f"[BENCHMARK]   Input tokens   : {session.input_tokens:,}",
            f"[BENCHMARK]   Output tokens  : {session.output_tokens:,}",
            f"[BENCHMARK]   推定コスト     : ${session.cost_usd:.6f} USD",
            f"[BENCHMARK] {sep}\n",
        ]
        log_text = "\n".join(lines)
        # コンソールへは print のみで出力（logger ハンドラ経由の二重表示を防ぐ）。
        # ログファイル用には1行サマリのみ残す。
        print(log_text)
        logger.info(
            "[BENCHMARK] %s recorded: case=%s mode=%s route_correct=%s "
            "rag_top=%.4f replan=%d status=%s",
            session.query_id, session.expected_case, session.agent_mode,
            session.route_correct, session.rag_top_score,
            session.replan_count, session.overall_status,
        )

    def save_to_csv(self, session: BenchmarkSession) -> None:
        """ベンチマーク結果を CSV ファイルに追記"""
        with open(self.csv_path, "a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=CSV_HEADERS).writerow(session.to_csv_row())
        logger.info("[BENCHMARK] CSV appended: %s", self.csv_path)


# ---------------------------------------------------------------------------
# BenchmarkRunner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    """
    GRACEパイプライン全体（Plan → Execute → Confidence → Intervention → Replan）
    をラップし、1クエリまたはクエリセット全体のベンチマークを実行する。

    モデル名・プロバイダーは ``config.llm`` から自動取得するため、
    各リポジトリ（anthropic / openai / gemini / ollama）でコードを変更せずに使用できる。
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        provider: Optional[str]   = None,
        config: Any               = None,
        csv_path: Optional[Path]  = None,
        qdrant_collection: Optional[str] = None,
    ) -> None:
        from .config import get_config as _get_config

        self.config     = config or _get_config()
        self.model_name = model_name or self.config.llm.model
        self.provider   = provider   or self.config.llm.provider
        self.bm_logger  = BenchmarkLogger(csv_path=csv_path, config=self.config)
        if qdrant_collection:
            self.config.qdrant.collection_name = qdrant_collection

    # ── 単一クエリ実行 ──────────────────────────────────────────────────────────────────────

    def run(
        self,
        query_id:   str,
        query_text: str,
        run_number: int = 1,
        level:      str = "",
        category:   str = "",
        agent_path: str = "",
        expected_case: str = "",
        expected:   Optional[Dict[str, Any]] = None,
        agent_mode: str = "grace_dynamic",
        force_collection: Optional[str] = None,
    ) -> BenchmarkSession:
        """
        1クエリをフルパイプラインで実行し、各指標を計測して返す。

        Args:
            query_id:   クエリ ID（例: "Q01"）
            query_text: クエリ本文
            run_number: 同一クエリ内の試行番号（1〜3 を推奨）
            level:      難易度ラベル（"Easy" / "Medium" / "Hard"）
            category:   カテゴリラベル
            agent_path: 狙った GRACE 処理経路ラベル（BENCHMARK_QUERIES の "path"）
            expected_case: 検索ハンドリングケース A〜E
            expected:   期待挙動 dict（intervention/web/replan/min_rag_score）
            agent_mode: "grace_dynamic"（Plan+Executor）または "react"（ReAct+Reflection）
            force_collection: 指定時、初回プランの全 rag_search ステップの検索先を
                            この（通常は存在しない）コレクションへ固定する。結果ゼロ →
                            ステップ failed → リプランを確定的に発火させる Case D 用。

        Returns:
            BenchmarkSession: 計測結果
        """
        from .executor import Executor
        from .planner import Planner

        session = BenchmarkSession(
            query_id   = query_id,
            query_text = query_text,
            model      = self.model_name,
            provider   = self.provider,
            run_number = run_number,
            level      = level,
            category   = category,
            agent_path = agent_path,
            expected_case = expected_case,
            expected   = expected or {},
            agent_mode = agent_mode,
        )

        # ReAct + Reflection モードは別パイプライン（services/agent_service.py）
        if agent_mode == "react":
            return self._run_react(session)

        try:
            # ── Phase 1: Plan ────────────────────────────────────────────────────────
            try:
                planner = Planner(config=self.config, model_name=self.model_name)
            except TypeError:
                planner = Planner(config=self.config)
            session.plan_start = time.monotonic()
            plan = planner.create_plan(query_text)
            session.plan_end   = time.monotonic()
            self.bm_logger.record_plan_result(session, plan)

            # Case D: 初回 RAG を存在しないコレクションへ向けて確定的に空振りさせ、
            # ステップ failed 経由でリプランを強制する（回復プランは executor 内で
            # 新規生成されるため、ここでは初回プランの rag_search のみ書き換える）。
            if force_collection:
                forced = 0
                for _step in getattr(plan, "steps", None) or []:
                    if getattr(_step, "action", None) == "rag_search":
                        _step.collection = force_collection
                        forced += 1
                logger.info(
                    "[BENCHMARK] %s: force_collection=%s を %d 個の rag_search に適用"
                    "（初回検索を空振りさせ replan を強制）",
                    query_id, force_collection, forced,
                )

            # ── Phase 2-5: Execute / Confidence / Intervention / Replan ──
            try:
                executor = Executor(config=self.config, model_name=self.model_name)
            except TypeError:
                executor = Executor(config=self.config)
            session.execute_start = time.monotonic()
            result = self._call_execute(executor, plan)
            session.execute_end   = time.monotonic()
            self.bm_logger.record_execution_result(session, result)

        except Exception as exc:
            logger.error(
                "[BENCHMARK] %s run%d failed: %s",
                query_id, run_number, exc, exc_info=True,
            )
            session.overall_status = "failed"
            now = time.monotonic()
            if session.plan_end    == 0.0:
                session.plan_end    = now
            if session.execute_end == 0.0:
                session.execute_end = now

        finally:
            self.bm_logger.finalize_and_log(session)
            self.bm_logger.save_to_csv(session)

        return session

    # ── ReAct + Reflection モード ──────────────────────────────────────────────────
    def _run_react(self, session: BenchmarkSession) -> BenchmarkSession:
        """同一クエリを ReAct+Reflection エージェント（services/agent_service.py の
        ReActAgent）で実行し、観測可能な指標を計測する。

        ReAct は GRACE のような plan_complexity / overall_confidence /
        intervention_level / replan_count を持たないため、それらは記録しない。
        tool_calls / rag_step_count / web_fallback_fired / rag_top_score /
        final_answer の有無を計測し、route_correct は web 期待との一致で採点する。
        """
        try:
            from services.agent_service import ReActAgent
        except Exception as exc:  # 依存未導入など
            logger.error("[BENCHMARK] ReActAgent import 失敗: %s", exc, exc_info=True)
            session.overall_status = "failed"
            now = time.monotonic()
            session.plan_start = session.plan_end = now
            session.execute_start = session.execute_end = now
            self.bm_logger.finalize_and_log(session)
            self.bm_logger.save_to_csv(session)
            return session

        collection = self.config.qdrant.collection_name
        web_fired = False
        rag_top_score = 0.0
        final_text = ""

        session.execute_start = time.monotonic()
        try:
            agent = ReActAgent(
                selected_collections=[collection],
                model_name=self.model_name,
            )
            run_turn = getattr(agent, "run_turn", None) or getattr(agent, "execute_turn", None)
            if run_turn is None:
                raise AttributeError("ReActAgent に run_turn/execute_turn がありません")

            for event in run_turn(session.query_text):
                if not isinstance(event, dict):
                    continue
                etype = event.get("type")
                if etype == "tool_call":
                    session.tool_calls += 1
                    name = (event.get("name") or "").lower()
                    if "rag" in name or "knowledge" in name:
                        session.rag_step_count += 1
                    if "web" in name or "search_web" in name:
                        web_fired = True
                elif etype == "tool_result":
                    content = str(event.get("content", ""))
                    if "http://" in content or "https://" in content:
                        web_fired = True
                    # スコア表記 "score": 0.87 を素朴に抽出
                    import re as _re
                    for m in _re.findall(r'"?score"?\s*[:=]\s*([0-9]*\.?[0-9]+)', content):
                        try:
                            rag_top_score = max(rag_top_score, float(m))
                        except ValueError:
                            pass
                elif etype == "final_text":
                    final_text = str(event.get("content", "") or "")

            session.overall_status = "success" if final_text.strip() else "partial"
        except Exception as exc:
            logger.error("[BENCHMARK] ReAct 実行失敗: %s", exc, exc_info=True)
            session.overall_status = "failed"
        finally:
            session.execute_end = time.monotonic()

        session.rag_top_score = rag_top_score
        # ReAct には plan フェーズが無いため plan_time は 0
        session.plan_start = session.plan_end = session.execute_start

        # route 採点（web 期待・スコア下限・破綻有無のみ。介入/リプランは対象外）
        expected = session.expected or {}
        sufficient = getattr(self.config.qdrant, "rag_sufficient_score", 0.7)
        session.rag_hit_in_target = rag_top_score >= sufficient
        session.web_fallback_fired = web_fired
        if expected:
            ok = session.overall_status != "failed"
            exp_web = expected.get("web")
            if exp_web is not None:
                ok = ok and (bool(web_fired) == bool(exp_web))
            exp_min = expected.get("min_rag_score")
            if exp_min is not None:
                ok = ok and rag_top_score >= exp_min
            session.route_correct = ok

        self.bm_logger.finalize_and_log(session)
        self.bm_logger.save_to_csv(session)
        return session

    @staticmethod
    def _call_execute(executor: Any, plan: Any) -> Any:
        """
        Executor.execute() を呼び出す。

        ストリーミング（Generator）とバッチ（直接 ExecutionResult を返す）の
        両インターフェースに対応する。
        """
        import types
        if hasattr(executor, 'execute'):
            result = executor.execute(plan)
        elif hasattr(executor, 'execute_plan_generator'):
            result = executor.execute_plan_generator(plan)
        else:
            result = executor.execute_plan(plan)

        # Generator: return 値 (StopIteration.value) を優先して捕捉する
        if isinstance(result, types.GeneratorType):
            last, ret = None, None
            try:
                while True:
                    last = next(result)
            except StopIteration as e:
                ret = e.value
            return ret if ret is not None else last

        return result

    # ── クエリセット一括実行 ──────────────────────────────────────────────────────────────────

    def run_query_set(
        self,
        queries: Optional[List[Dict[str, str]]] = None,
        runs_per_query: int = 3,
        fast: bool = False,
        query_ids: Optional[List[str]] = None,
        limit: Optional[int] = None,
        max_replans: Optional[int] = None,
        restrict_collection: Optional[bool] = None,
        mode: str = "grace",
    ) -> List[BenchmarkSession]:
        """
        複数クエリを ``runs_per_query`` 回ずつ実行する。

        Args:
            queries:        クエリリスト（省略時は ``BENCHMARK_QUERIES`` を使用）
            runs_per_query: 各クエリの試行回数（統計的信頼性のため 3 を推奨）
            fast:           高速完了モード。``FAST_QUERY_IDS`` の代表クエリのみを
                            ``runs_per_query=1`` で実行する（明示指定があれば優先）。
                            未指定なら max_replans=1 / 単一コレクション固定を既定適用し、
                            アクセス回数を最小化する。
            query_ids:      実行する ID を明示指定（例: ["Q01", "Q03"]）。
            limit:          先頭から実行する件数。
            max_replans:    リプラン上限の上書き（config.replan.max_replans）。
            restrict_collection: True で RAG 検索を単一コレクションに限定
                            （config.qdrant.restrict_to_collection）。
            mode:           "grace"（Plan+Executor）/ "react"（ReAct+Reflection）/
                            "both"（同一クエリを両方式で実行し横並び比較）。

        Returns:
            List[BenchmarkSession]: 全セッション結果
        """
        mode_map = {
            "grace": ["grace_dynamic"],
            "react": ["react"],
            "both":  ["grace_dynamic", "react"],
        }
        agent_modes = mode_map.get(mode, ["grace_dynamic"])
        if fast and runs_per_query == 3:
            # 高速モードでは既定の試行回数を 1 に落とす
            # （呼び出し側が明示的に runs_per_query を変えた場合はそれを尊重）
            runs_per_query = 1

        # アクセス回数を抑制する設定（fast 時は既定で有効化）
        effective_max_replans = max_replans
        if effective_max_replans is None and fast:
            effective_max_replans = 1
        if effective_max_replans is not None and hasattr(self.config, "replan"):
            self.config.replan.max_replans = effective_max_replans

        effective_restrict = restrict_collection
        if effective_restrict is None and fast:
            effective_restrict = True
        if effective_restrict is not None and hasattr(self.config, "qdrant"):
            self.config.qdrant.restrict_to_collection = effective_restrict

        queries = select_queries(
            query_ids=query_ids, limit=limit, fast=fast, queries=queries,
        )
        sessions: List[BenchmarkSession] = []
        total = len(queries) * runs_per_query * len(agent_modes)
        done  = 0

        speed = "FAST" if fast else "FULL"
        logger.info(
            "[BENCHMARK] speed=%s mode=%s(%s) queries=%d runs_per_query=%d total=%d",
            speed, mode, "/".join(agent_modes), len(queries), runs_per_query, total,
        )

        for query in queries:
            for agent_mode in agent_modes:
                for run in range(1, runs_per_query + 1):
                    done += 1
                    logger.info(
                        "[BENCHMARK] Progress %d/%d | %s [%s|%s] Run %d/%d",
                        done, total, query["id"], query.get("case", ""),
                        agent_mode, run, runs_per_query,
                    )
                    session = self.run(
                        query_id   = query["id"],
                        query_text = query["text"],
                        run_number = run,
                        level      = query.get("level", ""),
                        category   = query.get("category", ""),
                        agent_path = query.get("path", ""),
                        expected_case = query.get("case", ""),
                        expected   = query.get("expected"),
                        agent_mode = agent_mode,
                        force_collection = query.get("force_collection"),
                    )
                    sessions.append(session)

        logger.info(
            "[BENCHMARK] All done: %d sessions. CSV => %s",
            done, self.bm_logger.csv_path,
        )
        return sessions


# ---------------------------------------------------------------------------
# エクスポート
# ---------------------------------------------------------------------------

__all__ = [
    "BENCHMARK_QUERIES",
    "FAST_QUERY_IDS",
    "select_queries",
    "CSV_HEADERS",
    "BENCHMARK_CSV_PATH",
    "BenchmarkSession",
    "BenchmarkLogger",
    "BenchmarkRunner",
]
