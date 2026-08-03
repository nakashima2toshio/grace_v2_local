# grace/step_trace/_trace.py
"""step_trace 共通ヘルパ（S1〜S9 のトレース用スタブが共有）。

各 `sN_*.py` は `agent_support_example.py` の `run_support_agent()` の 1 ステップだけを
取り出し、**IN → Process → OUT** の 3 段で標準出力に示す
（`grace/doc/agent_support_example_flow.md` §2 の読み方に対応）。

- 実コード（`grace` / `agent_support_example`）をそのまま呼ぶため、環境
  （`ANTHROPIC_API_KEY` / Qdrant）があれば **本物のデータ**でトレースする。
- 環境が無い場合は各スタブが用意する**代表サンプル**（フロー図の gov 例）で
  ステップの構造（IN/Process/OUT の形）だけを示し、鍵が要る箇所はスキップする。

使い方::
    uv run python grace/step_trace/s1_profile.py --vertical gov "住民票の写しの取り方は？"
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# repo root（grace/step_trace/ から 2 つ上）を import パスへ追加し、
# agent_support_example.py / grace / support_actions を解決できるようにする。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# .env から ANTHROPIC_API_KEY / GOOGLE_API_KEY 等を読み込む（未導入でも続行）
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# トレース出力に混ざる実行基盤の INFO ログ（agent_cache / agent_parallel_search /
# qdrant_client_wrapper / grace.config 等）を抑制する対象ロガー。
_NOISY_LOGGERS = (
    "agent_cache",
    "agent_parallel_search",
    "qdrant_client_wrapper",
    "grace",  # grace.config など grace.* を包括
    "httpx",  # 実 API/Qdrant 呼び出し時の "HTTP Request ..." を抑制
    "httpcore",
)


def quiet_logs(level: int = logging.WARNING) -> None:
    """実行基盤の初期化 INFO ログを抑制し、IN/Process/OUT のトレースを見やすくする。

    grace/config.py が root logger を INFO + StreamHandler で構成するため、
    各モジュールの初期化 INFO（例: "CollectionCache initialized" /
    "QdrantClient シングルトン作成" / "Config loaded from ..."）が端末に混ざる。
    ここで対象ロガーを WARNING へ引き上げ、INFO を握りつぶす
    （ロガー側で落とすためハンドラ構成に依存せず確実）。

    `_trace` は各 sN スタブで最初に import されるため、この抑制は後続の重い
    `import agent_support_example`（＝ import 時に出る INFO）にも間に合う。
    デバッグで INFO を見たいときは環境変数 GRACE_TRACE_VERBOSE=1 を設定する。
    """
    if os.getenv("GRACE_TRACE_VERBOSE") == "1":
        return
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(level)


# import 時に自動適用（各 sN は先頭で _trace を import するため、
# 後続の import による INFO も含めて抑制される）。
quiet_logs()


def banner(title: str) -> None:
    """agent_support_example._banner と同じ体裁の見出し。"""
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def ipo(in_: str, process: str, out: str) -> None:
    """IN → Process → OUT を 7 桁ラベル揃えで表示する（フロー図 §2 の体裁）。"""
    for label, body in (("IN", in_), ("Process", process), ("OUT", out)):
        lines = str(body).rstrip("\n").split("\n")
        print(f"{label:<7}: {lines[0]}")
        for extra in lines[1:]:
            print(f"{'':<7}  {extra}")


def have_key() -> bool:
    """LLM 呼び出しに必要な ANTHROPIC_API_KEY があるか。"""
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def note_no_key(step: str) -> None:
    print(f"\n⚠️ ANTHROPIC_API_KEY 未設定のため {step} の実呼び出しはスキップし、"
          "代表サンプルで構造のみ表示します。")
