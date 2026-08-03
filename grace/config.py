"""
GRACE Config - 設定管理

YAMLファイルと環境変数からの設定読み込み
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, Field

# =============================================================================
# Logging Configuration
# =============================================================================

def init_grace_logging():
    """GRACEパッケージ用のロギングを初期化"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / "grace_run.log"
    
    # 既存のハンドラがあるかチェックして重複を防ぐ
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
    else:
        # graceパッケージの出力を確実にする
        grace_logger = logging.getLogger("grace")
        if not any(isinstance(h, logging.FileHandler) for h in grace_logger.handlers):
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            grace_logger.addHandler(fh)
            grace_logger.setLevel(logging.INFO)

# モジュール読み込み時に初期化
init_grace_logging()

logger = logging.getLogger(__name__)


# =============================================================================
# 設定モデル定義
# =============================================================================

class LLMConfig(BaseModel):
    """LLM設定（本プロジェクトは Anthropic を使用）"""
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    # ステップ毎の確信度評価（evaluate_with_factors）などテレメトリ級の
    # 定型評価タスクに使う軽量モデル。回答生成・根拠検証は model を使う。
    light_model: str = "claude-haiku-4-5-20251001"
    # M-1: 論理層（計画生成・推論・根拠検証）に使う上位モデル。
    # ""（空）= model と同じ。上位モデルへ寄せたいときだけ設定する
    # （例: "claude-opus-5"）。
    #
    # ⚠️ 切り替え前に必ず確認すること:
    #   1. claude-opus-5 は拡張思考が既定 ON で、max_tokens は「思考+本文」の
    #      合計上限になる。llm_compat が既定で thinking を明示 disabled にして
    #      いるためそのままでも動くが、思考を活かすなら
    #      heavy_thinking_budget_tokens を設定する
    #   2. 入出力単価が上がる（sonnet の 1.7 倍程度）。cost.* の上限も見直す
    #   3. 判定系（複雑度推定・意図分類・情報なし判定・RAG 適合性）は
    #      light_model 側であり、ここでは切り替わらない
    heavy_model: str = ""
    # 論理層で拡張思考に使うトークン予算（0=無効）。有効化すると温度指定は無視される。
    heavy_thinking_budget_tokens: int = 0
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 30
    # reasoning プロンプトのシステム指示へ追記する業務方針（空=追記なし）。
    # 業界プロファイル（VerticalProfile.prompt_addendum）の注入口として使い、
    # executor 経由・Web フォールバック経由の両方の reasoning に効く。
    prompt_addendum: str = ""


class EmbeddingConfig(BaseModel):
    """Embedding設定"""
    provider: str = "gemini"
    model: str = "gemini-embedding-001"
    dimensions: int = 3072


class ConfidenceWeights(BaseModel):
    """Confidence重み設定"""
    search_quality: float = 0.25
    source_agreement: float = 0.20
    llm_self_eval: float = 0.25
    tool_success: float = 0.15
    query_coverage: float = 0.15


class ConfidenceThresholds(BaseModel):
    """Confidence閾値設定"""
    silent: float = 0.9
    notify: float = 0.7
    confirm: float = 0.4


class ConfidenceConfig(BaseModel):
    """Confidence計算設定"""
    weights: ConfidenceWeights = Field(default_factory=ConfidenceWeights)
    thresholds: ConfidenceThresholds = Field(default_factory=ConfidenceThresholds)
    # S1: 根拠妥当性（groundedness）を最終 confidence の主成分にする設定
    groundedness_enabled: bool = True
    groundedness_weight: float = 0.6   # 支持率（主成分）の重み
    self_eval_weight: float = 0.25     # 自己評価（従）
    coverage_weight: float = 0.15      # 網羅度（従）
    search_aux_weight: float = 0.2     # 検索ベース集約値（補助）の重み
    # M-6: 判定できた claim の割合（decided / total）による支持率の減衰。
    #
    # support_rate は supported / (supported + contradicted) で、neutral（情報源に
    # 関連記述が無く判断できない）を分母から除く。このため「11 claim 中 7 しか
    # 判定できず、その 7 が全部 supported」でも支持率 1.0 になり、根拠の薄さが
    # スコアに出ない。判定率が低いほど支持率を割り引く。
    #
    #   damping    = min(1.0, (decided / total) / coverage_target)
    #   effective  = support_rate * (1 - strength + strength * damping)
    #
    # neutral には「お問い合わせください」等の定型句も含まれ、これは原理的に
    # どの情報源でも支持されない。全損させないよう strength は控えめにする。
    # strength=0.0 で従来どおり（減衰なし）。
    groundedness_coverage_strength: float = 0.3
    groundedness_coverage_target: float = 0.8
    # 曖昧クエリ等の明確化（ask_user）計画＝最終回答なしのときに用いる低信頼値。
    # 0.4 未満で ESCALATE、0.4〜0.7 で CONFIRM 介入になる（既定は ESCALATE 寄り）。
    clarification_confidence: float = 0.3
    # S1: 較正（temperature scaling）パラメータの保存先
    calibration_path: str = "config/calibration.json"


class InterventionConfig(BaseModel):
    """介入設定"""
    default_timeout: int = 300  # 5分
    auto_proceed_on_timeout: bool = False
    max_clarification_rounds: int = 3
    # 対話モード。True なら CONFIRM で一時停止して確認を求める（UI/streaming 想定）。
    # ブロッキング実行（execute_plan）は非対話のため CONFIRM では停止せず自動進行する
    # （ESCALATE は常に停止）。
    interactive: bool = True


class ReplanConfig(BaseModel):
    """リプラン設定"""
    max_replans: int = 3
    confidence_threshold: float = 0.4
    partial_replan_threshold: float = 0.6
    cooldown_seconds: int = 5


class CostConfig(BaseModel):
    """コスト管理設定"""
    daily_limit_usd: float = 10.0
    hourly_limit_usd: float = 2.0
    per_query_limit_usd: float = 0.50
    warning_threshold: float = 0.8


class ErrorConfig(BaseModel):
    """エラーハンドリング設定"""
    max_retries: int = 3
    retry_delay_base: float = 1.0
    retry_delay_max: float = 30.0
    exponential_backoff: bool = True


class LoggingConfig(BaseModel):
    """ログ設定"""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: str = "logs/grace.log"
    max_size_mb: int = 100
    backup_count: int = 5


class QdrantConfig(BaseModel):
    """Qdrant設定"""
    url: str = "http://localhost:6333"
    collection_name: str = "customer_support_faq"
    search_limit: int = 5
    score_threshold: float = 0.35
    rag_sufficient_score: float = 0.7  # RAG結果が十分と判断するスコア閾値（これ未満ならweb_searchを動的実行）
    # True の場合、RAG検索を collection_name（または明示指定コレクション）の
    # 1コレクションのみに限定し、全コレクション横断のフォールバックを行わない。
    # ベンチマーク等でアクセス回数を最小化したい場合に使用する。
    restrict_to_collection: bool = False
    search_priority: list = Field(default_factory=lambda: ["wikipedia_ja", "livedoor", "cc_news", "japanese_text"])
    # 検索を許可するコレクションの許可リスト（空=制限なし）。業界プロファイル等で
    # 検索範囲（フォールバック連鎖を含む）をスコープするために使う。一致判定は
    # search_priority と同じ部分一致（例: "wikipedia_ja" は "wikipedia_ja_5per" に一致）。
    # 有効コレクション（次元一致・実体あり）との一致が 1 つも無い場合は制限を適用せず
    # 従来どおり検索する（コレクション未登録の段階でもデモが動くようにするため。警告ログを出す）。
    allowed_collections: list = Field(default_factory=list)


class WebSearchConfig(BaseModel):
    """Web検索設定"""
    backend: str = "serpapi"                  # "duckduckgo" or "google_cse" or "serpapi"
    num_results: int = 5
    language: str = "ja"
    timeout: int = 30
    # タイムアウト・一時エラー時のリトライ（試行総数）。タイムアウト起因の
    # 「検索 0 件 → 情報なし回答 → 誤エスカレ」連鎖（saas 500エラー報告で顕在化）
    # を抑えるため、リトライとフォールバックは設定で調整可能にする。
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0        # 待機 = backoff × 試行回数（線形）
    # 主バックエンドが失敗/0件のとき 1 度だけ試す代替バックエンド（""=無効）。
    # duckduckgo は API キー不要のため既定のフォールバックに適する。
    fallback_backend: str = "duckduckgo"
    # Google CSE用（backendが"google_cse"の場合のみ使用）※新規受付停止
    google_cse_api_key: str = ""
    google_cse_engine_id: str = ""
    # SerpAPI用（backendが"serpapi"の場合に使用）
    serpapi_api_key: str = ""
    # W-1: 優先ドメイン（業界プロファイルがリクエストごとに注入する）。
    #
    # ⚠️ これは「除外リスト」ではなく「加点リスト」。取得側を絞り込むと
    # 0 件化 → 情報なし回答 → ④' の誤エスカレ、という連鎖を招くことが
    # 実測で分かっている（saas の 500 エラー報告で顕在化）。そのため
    # 一致したドメインのスコアを底上げして順位を上げるだけに留め、
    # 非一致の結果も従来どおり残す。
    # 一致判定は取得 URL のホスト名に対する接尾辞一致
    #   （"go.jp" は "www.city.example.go.jp" に一致する）。
    preferred_domains: list = Field(default_factory=list)
    preferred_domain_boost: float = 0.15


class ToolsConfig(BaseModel):
    """ツール設定"""
    enabled: list = Field(default_factory=lambda: ["rag_search", "web_search", "reasoning", "ask_user"])
    disabled: list = Field(default_factory=list, description="プロジェクト全体で恒久的に禁止するツールのリスト")


class CodeExecuteConfig(BaseModel):
    """code_execute（サンドボックス Python 実行）設定。

    セキュリティ上、既定では tools.enabled に含めず opt-in とする。
    実体はサブプロセス分離＋resource 制限＋isolated mode による best-effort サンドボックス。
    真の隔離が必要な場合はコンテナ/gVisor 等の外部境界を併用すること。
    """
    timeout_seconds: int = 5          # CPU/実時間のタイムアウト
    max_memory_mb: int = 256          # アドレス空間上限（RLIMIT_AS）
    max_output_chars: int = 10000     # 標準出力の最大文字数（超過分は切り詰め）
    # AST レベルで import を禁止するモジュール（防御の多層化）
    denied_imports: list = Field(default_factory=lambda: [
        "subprocess", "socket", "ctypes", "multiprocessing",
        "urllib", "requests", "http", "ftplib", "shutil", "asyncio",
    ])


class MemoryConfig(BaseModel):
    """実行メモリ層（P4）設定。

    実行ログから (質問キーワード, コレクション, 成否, confidence) を蓄積し、
    Planner のコレクション優先順位に反映する。
    """
    enabled: bool = True
    path: str = "logs/grace_memory.jsonl"
    # best_collection の採用条件（実績が薄いコレクションへ早まって固定しない）
    min_count: int = 3        # この件数以上の実績が必要
    min_score: float = 0.6    # success_rate(平滑化) × mean_confidence の下限


class PlannerConfig(BaseModel):
    """Planner設定（二層計画生成）"""
    # この複雑度（ヒューリスティック推定）未満の質問は
    # ルールベース計画（LLM呼び出しなし）で即時に計画を生成する
    llm_plan_complexity_threshold: float = 0.7
    # True の場合、複雑度に関わらず常に LLM 計画生成を使用する
    force_llm_plan: bool = False
    # 生成する PlanStep のステップ実行タイムアウト（秒）
    step_timeout_seconds: int = 30
    # LLM 計画生成のリトライ回数（空レスポンス・不完全JSON時に再試行）
    llm_plan_max_attempts: int = 2
    # LLM 計画生成の最大出力トークン数（計画JSONが途中で切れないよう大きめ）
    plan_max_output_tokens: int = 8192
    # LLM 複雑度推定の温度・最大出力トークン数（数値のみを返すため小さく）
    complexity_temperature: float = 0.1
    complexity_max_output_tokens: int = 10


class ExecutorConfig(BaseModel):
    """Executor設定"""
    # 検索結果が不十分な場合に動的挿入するフォールバックアクションの連鎖
    # （PlanStep.fallback が指定されていない場合のデフォルト）
    fallback_chain: Dict[str, str] = Field(default_factory=lambda: {
        "rag_search": "web_search",
        "web_search": "ask_user",
    })
    # 依存関係のない検索ステップを並列実行する
    parallel_search: bool = True
    max_parallel_steps: int = 4
    # S3: ハイブリッド ReAct（観測駆動ループ）
    react_enabled: bool = True              # 複雑質問を ReAct ループで実行する
    react_complexity_threshold: float = 0.7  # この複雑度以上のみ ReAct（未満は静的パス温存）
    react_max_iterations: int = 8           # ReAct ループの最大反復回数
    # RAG 検索結果の意味的適合性チェック（_evaluate_rag_relevance）に使うモデル。
    # 出力は YES / NO の 2 値だけなので、既定では軽量モデル（llm.light_model）を使う。
    # ""（空）= llm.light_model にフォールバック。A/B したいときはモデル名を直接書く
    #   （例: "claude-sonnet-4-6" で従来どおり主モデル判定に戻せる）。
    # ⚠️ この判定は「RAG 経路を捨てて Web 検索へ落ちるか」を左右する影響の大きい
    #    分岐なので、モデルを変えたら誤判定率を実測で確認すること。
    relevance_check_model: str = ""


class GraceConfig(BaseModel):
    """GRACE Agent 統合設定"""
    version: str = "1.0"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    confidence: ConfidenceConfig = Field(default_factory=ConfidenceConfig)
    intervention: InterventionConfig = Field(default_factory=InterventionConfig)
    replan: ReplanConfig = Field(default_factory=ReplanConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    error: ErrorConfig = Field(default_factory=ErrorConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    code_execute: CodeExecuteConfig = Field(default_factory=CodeExecuteConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)


# =============================================================================
# 設定ローダー
# =============================================================================

class ConfigLoader:
    """設定ローダー"""

    DEFAULT_CONFIG_PATH = "config/grace_config.yml"
    ENV_PREFIX = "GRACE_"

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._config: Optional[GraceConfig] = None

    def load(self) -> GraceConfig:
        """設定を読み込み"""
        if self._config is not None:
            return self._config

        # 1. デフォルト設定
        config_dict: Dict[str, Any] = {}

        # 2. YAMLファイルから読み込み
        config_file = Path(self.config_path)
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    loaded = yaml.safe_load(f)
                    if loaded:
                        config_dict = loaded
                logger.info(f"Config loaded from {self.config_path}")
            except Exception as e:
                logger.warning(f"Failed to load config from {self.config_path}: {e}")
        else:
            logger.info(f"Config file not found: {self.config_path}, using defaults")

        # 3. 環境変数で上書き
        config_dict = self._apply_env_overrides(config_dict)

        # 4. Pydanticモデルで検証
        self._config = GraceConfig(**config_dict)

        return self._config

    def _apply_env_overrides(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """環境変数による上書き"""
        for key, value in os.environ.items():
            if not key.startswith(self.ENV_PREFIX):
                continue

            # GRACE_LLM_MODEL -> llm.model
            parts = key[len(self.ENV_PREFIX):].lower().split('_')

            if len(parts) >= 2:
                section = parts[0]
                subkey = '_'.join(parts[1:])

                if section not in config_dict:
                    config_dict[section] = {}

                # 型変換
                config_dict[section][subkey] = self._convert_value(value)
                logger.debug(f"Config override: {section}.{subkey} = {value}")

        return config_dict

    def _convert_value(self, value: str) -> Any:
        """文字列から適切な型に変換"""
        # bool
        if value.lower() in ('true', 'false'):
            return value.lower() == 'true'

        # int
        try:
            return int(value)
        except ValueError:
            pass

        # float
        try:
            return float(value)
        except ValueError:
            pass

        # リスト（カンマ区切り）
        if ',' in value:
            return [v.strip() for v in value.split(',')]

        return value

    def reload(self) -> GraceConfig:
        """設定を再読み込み"""
        self._config = None
        return self.load()


# =============================================================================
# シングルトンインスタンス
# =============================================================================

_config_loader: Optional[ConfigLoader] = None


def get_config(config_path: Optional[str] = None) -> GraceConfig:
    """設定を取得（シングルトン）"""
    global _config_loader

    if _config_loader is None:
        _config_loader = ConfigLoader(config_path)

    return _config_loader.load()


def reload_config() -> GraceConfig:
    """設定を再読み込み"""
    global _config_loader

    if _config_loader is not None:
        return _config_loader.reload()

    return get_config()


def reset_config():
    """設定をリセット（テスト用）"""
    global _config_loader
    _config_loader = None


# =============================================================================
# モデル層の解決（M-1）
# =============================================================================
#
# GRACE は用途をおおまかに 3 層で使い分ける。
#
#   論理層 (heavy): 計画生成 / 推論（最終回答）/ 根拠検証 / ReAct ループ
#   標準層 (model): 自己評価・網羅度など、スコアを出す定型評価
#   軽量層 (light): 複雑度推定・意図分類・情報なし判定・RAG 適合性チェック
#                   （いずれも 1 語〜数値しか返さない）
#
# `heavy_model` が空のあいだは標準層と同じモデルに解決されるため、
# 設定しない限り挙動は変わらない。

def resolve_heavy_model(config: Any) -> str:
    """論理層に使うモデル名を解決する（未設定なら `llm.model`）。"""
    llm = getattr(config, "llm", None)
    if llm is None:
        return ""
    heavy = (getattr(llm, "heavy_model", "") or "").strip()
    return heavy or (getattr(llm, "model", "") or "")


def heavy_thinking_budget(config: Any) -> int:
    """論理層の拡張思考トークン予算を返す（0=無効）。

    `heavy_model` を設定していない（＝標準層と同じモデルを使っている）間は
    思考を有効にしない。モデルを上げていないのに思考コストだけ増えるのを防ぐ。
    """
    llm = getattr(config, "llm", None)
    if llm is None or not (getattr(llm, "heavy_model", "") or "").strip():
        return 0
    try:
        return max(0, int(getattr(llm, "heavy_thinking_budget_tokens", 0) or 0))
    except (TypeError, ValueError):
        return 0


# =============================================================================
# エクスポート
# =============================================================================

__all__ = [
    # Config models
    "LLMConfig",
    "EmbeddingConfig",
    "ConfidenceWeights",
    "ConfidenceThresholds",
    "ConfidenceConfig",
    "InterventionConfig",
    "ReplanConfig",
    "CostConfig",
    "ErrorConfig",
    "LoggingConfig",
    "QdrantConfig",
    "WebSearchConfig",
    "ToolsConfig",
    "CodeExecuteConfig",
    "MemoryConfig",
    "GraceConfig",

    # Loader
    "ConfigLoader",

    # Functions
    "get_config",
    "reload_config",
    "reset_config",
]
