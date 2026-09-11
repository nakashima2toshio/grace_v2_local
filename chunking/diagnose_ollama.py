#!/usr/bin/env python
"""チャンク化が遅い／止まらないときに、**原因を 1 回の実行で切り分ける**。

    uv run python -m chunking.diagnose_ollama                     # 既定モデル
    uv run python -m chunking.diagnose_ollama --model gemma4:26b-mlx

## なぜこれがあるのか

「1 ブロック 543 秒」を 5 回追いかけたが、そのたびに切り分けの手順を
組み立て直していた。543 秒は `CHUNKING_LLM_TIMEOUT`(180) × リトライ 3 回で、
**1 件も成功していない**ことを意味するが、そこから先の

  - モデルが単に遅いのか
  - 思考（reasoning）だけを延々と出して本文に到達していないのか
  - 出力上限（num_predict）まで生成し切っているのか
  - そもそも pull されていないのか

は、タイムアウトのログを何回眺めても区別が付かない。**タイムアウトは
「途中で切った」であって「何が起きたか」を教えてくれない**からである。

そこでタイムアウト無しで 1 回だけ実行し、生成トークン数・速度・
finish_reason・本文の有無を直接見る。

## 既知の失敗（実測済み）

`gemma4:26b-a4b-it-qat` は思考モデルで、**本文を一度も出さないことがある**:

    finish_reason=length, completion_tokens=2766,
    thinking=10007 chars (key=reasoning),
    message_keys=['reasoning', 'role']      ← content が存在しない

`reasoning_effort="none"` を送って思考を抑止しているが、対応は Ollama の
バージョン依存なので、効いていないモデル・環境があり得る。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from config import OllamaConfig, get_default_ollama_model

# チャンク化 Step1 と同じ形の最小プロンプト（長さだけ本番より短くしてある）
SCHEMA_PROMPT = """次のテキストを段落と文に分解し、JSON だけを出力してください。

【入力テキスト】
The Knights have won just seven games out of their last 57. That's just 12 per cent.
Yet in spite of this, they have averaged 15,000 fans to their games over that period.

スキーマ: {"paragraphs": [{"id": 1, "sentences": [{"text": "..."}]}]}"""


# 本番 Step1 と同じ出力上限（chunks_all_async の max_output_tokens）
STEP1_MAX_TOKENS = 8192

# `CHUNKING_LLM_TIMEOUT` 未設定時に効く既定（helper_llm.DEFAULT_OLLAMA_TIMEOUT）
DEFAULT_TIMEOUT = 180.0

# ③ に使う入力ブロック。**実際に 543 秒かけて失敗した block_24 の逐語**
# （2026-09-11 の cc_news_2per.csv / block_size=1000）。作り物の短文だと
# 本番より軽くなり、「手元では通る」を再生産する。
STEP1_BLOCK = (
    "ling sides during a rough period and they are actually in with a chance to win "
    "a game - like the Knights were against the Sea Eagles last Friday - to have it "
    "cruelled by an easily correctable refereeing blunder hurts far more than the "
    "equivalent blunder hurts the supporters of a side entrenched in the top eight "
    "that has won a premiership in the last decade.\n"
    "The Knights have won just seven games out of their last 57.\n"
    "That's just 12 per cent.\n"
    "Yet in spite of this, they have averaged 15,000 fans to their games over that "
    "period.\n"
    "That's hard core.\n"
    "That's true belief.\n"
    "That's the sort of rusted on support that the NRL should be falling over itself "
    "to protect.\n"
    "There will be a large number of those supporters who don't have that much.\n"
    "But when they rock up to the game they are part of something bigger.\n"
    "They are there in their army to urge their side to victory.\n"
    "When their boys get up they can feel great for days and life seems good.\n"
    "When they lose it can turn the world to shit.\n"
    "And if they get "
)


def _post(path: str, payload: dict, timeout: float) -> dict:
    url = f"{OllamaConfig.BASE_URL.rstrip('/').removesuffix('/v1')}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _check_pulled(model: str) -> bool:
    try:
        from services.data_pipeline_service import list_pulled_ollama_models
        pulled = list_pulled_ollama_models()
    except Exception as e:
        print(f"  ⚠️  pull 済み一覧を取得できません: {e}")
        return True

    if not pulled:
        print("  ⚠️  pull 済み一覧が空（Ollama 停止中の可能性）")
        return True
    if model in pulled:
        print(f"  ✅ pull 済み: {model}")
        return True

    print(f"  ❌ '{model}' は pull されていません")
    print(f"     pull 済み: {', '.join(sorted(pulled))}")
    return False


def _bench(model: str, label: str, prompt: str, num_predict: int, timeout: float) -> None:
    """`/api/generate` で 1 回だけ生成し、速度と停止理由を出す。

    ⚠️ **タイムアウトを長く取る。** 180 秒で切ると「遅い」としか分からず、
    生成トークン数も停止理由も残らない。
    """
    print(f"\n--- {label} (num_predict={num_predict}) ---")
    started = time.perf_counter()
    try:
        result = _post(
            "/api/generate",
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": num_predict, "temperature": 0.1},
            },
            timeout=timeout,
        )
    except urllib.error.URLError as e:
        print(f"  ❌ 失敗: {e}")
        return
    except TimeoutError:
        print(f"  ❌ {timeout:.0f} 秒でも終わりませんでした")
        return

    elapsed = time.perf_counter() - started
    eval_count = result.get("eval_count") or 0
    eval_ns = result.get("eval_duration") or 0
    load_ns = result.get("load_duration") or 0
    response = result.get("response") or ""
    thinking = result.get("thinking") or ""

    print(f"  所要:        {elapsed:.1f} 秒")
    print(f"  モデルロード: {load_ns / 1e9:.1f} 秒")
    print(f"  生成トークン: {eval_count}")
    if eval_ns:
        print(f"  生成速度:    {eval_count / (eval_ns / 1e9):.1f} tok/s")
    print(f"  停止理由:    {result.get('done_reason')}")
    print(f"  本文:        {len(response)} 文字")
    if thinking:
        print(f"  ⚠️ 思考:      {len(thinking)} 文字  ← 本文へ到達していない可能性")
    if response:
        print(f"  先頭 120 字:  {response[:120]!r}")

    # --- 判定 ---------------------------------------------------------------
    if result.get("done_reason") == "length":
        print("  ⚠️ **出力上限まで生成し切っている。** モデルが停止トークンを出して")
        print("     いないので、num_predict がそのまま最悪時間になる。")
    if not response and thinking:
        print("  ⚠️ **思考だけで本文が空。** reasoning_effort='none' が効いていない。")
        print("     思考を出さないモデルへ変えるのが確実。")


def _bench_step1(model: str, timeout: float) -> None:
    """**本番の Step1 リクエストそのもの**を、タイムアウトだけ外して 1 回流す。

    ⚠️ **② では代わりにならない。** ② は `/api/generate` に短縮プロンプトを
    投げるだけで、本番とは次の 3 点が違う:

      - 本番は `/v1/chat/completions`（JSON モード・スキーマ添付）
      - 本番のプロンプトは分割ルール＋スキーマ定義で桁違いに長い
      - 本番は入力ブロック（既定 1000 文字）を**逐語で出力し直す**

    実測 2026-09-11 はこの差で判断を誤らせた。② は 74.3 秒で成功したのに、
    本番リクエストは 180 秒で切れ続けた。**軽い方を測って「動く」と読んで
    しまう**のが一番まずい。ここでは重い方を測る。
    """
    print(f"\n--- ③ 本番 Step1 リクエスト (max_tokens={STEP1_MAX_TOKENS}) ---")

    from chunking.models import StructuralResult
    from chunking.prompts import PARAGRAPH_SEPARATION_PROMPT
    from helper.helper_llm import create_llm_client

    prompt = f"{PARAGRAPH_SEPARATION_PROMPT}\n\n【入力テキスト】\n{STEP1_BLOCK}"
    print(f"  入力ブロック: {len(STEP1_BLOCK)} 文字")

    client = create_llm_client("ollama", default_model=model, timeout=timeout)
    started = time.perf_counter()
    try:
        result = client.generate_structured(
            prompt, StructuralResult, model, max_output_tokens=STEP1_MAX_TOKENS
        )
    except Exception as e:
        elapsed = time.perf_counter() - started
        print(f"  ❌ {elapsed:.1f} 秒で失敗: {type(e).__name__}: {e}")
        # ⚠️ **失敗の所要時間で判定しない。** 接続拒否なら 1 秒未満で返るので、
        #    そのまま速さとして読むと「余裕あり」という正反対の結論になる。
        if elapsed >= timeout * 0.9:
            print(f"  ⚠️ 上限 {timeout:.0f} 秒でも終わらない。モデルが重すぎる。")
        else:
            print("  ⚠️ 途中で失敗したので所要時間は速度の目安にならない。")
            print("     上のエラーを先に解消すること（Ollama 起動・モデルの pull）。")
        return

    elapsed = time.perf_counter() - started
    print(f"  ✅ 成功: {elapsed:.1f} 秒 / {len(result.paragraphs)} 段落")
    _verdict(elapsed, getattr(client, "last_usage", {}))


def _verdict(elapsed: float, usage: dict) -> None:
    """**成功した**リクエストの所要時間を既定のタイムアウトと突き合わせる。

    ⚠️ 失敗した呼び出しをここへ渡さないこと。接続拒否は 1 秒未満で返るため
    「余裕あり」と出てしまう（初版が実際にそう表示した）。
    """
    out = usage.get("output_tokens") or 0
    if out:
        print(f"  生成トークン: {out} / 実効 {out / elapsed:.1f} tok/s")

    print(f"  既定のタイムアウト: {DEFAULT_TIMEOUT:.0f} 秒")
    if elapsed <= DEFAULT_TIMEOUT * 0.5:
        print("  → 余裕あり。既定のままで回る。")
    elif elapsed <= DEFAULT_TIMEOUT:
        print("  ⚠️ 既定に収まってはいるが余裕がない。ブロックが長いと落ちる。")
        print(f"     CHUNKING_LLM_TIMEOUT={int(elapsed * 3)} 程度を推奨。")
    else:
        print("  ⚠️ **既定のタイムアウトを超えている。** このままでは全ブロックが")
        print(f"     失敗する（{DEFAULT_TIMEOUT:.0f} 秒 × リトライ 3 回 = "
              f"{DEFAULT_TIMEOUT * 3:.0f} 秒/ブロック）。次のどれかを採る:")
        print(f"     - CHUNKING_LLM_TIMEOUT={int(elapsed * 2)} を設定する")
        print("     - --block-size を下げて 1 リクエストの出力量を減らす")
        print("     - もっと小さいモデルにする（llama3.2:latest 等）")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="診断するモデル（既定: 設定値）")
    parser.add_argument("--timeout", type=float, default=900.0, help="1 回の上限秒数")
    parser.add_argument(
        "--skip-step1", action="store_true", help="③ 本番リクエストを省く（① ② だけ）"
    )
    args = parser.parse_args()

    model = args.model or get_default_ollama_model()

    print("=" * 60)
    print("Ollama 診断")
    print("=" * 60)
    print(f"  接続先: {OllamaConfig.BASE_URL}")
    print(f"  モデル: {model}")
    print()

    if not _check_pulled(model):
        return 1

    # ① ごく短い生成 — 素の速度とロード時間を測る
    _bench(model, "① 短い生成", "Say hello in one short sentence.", 64, args.timeout)

    # ② チャンク化と「同じ形」— ただし短縮版。素の速度を見るための参考値
    _bench(model, "② チャンク化と同じ形（短縮）", SCHEMA_PROMPT, 2048, args.timeout)

    # ③ 本番の Step1 そのもの — ここが 180 秒を超えるかどうかが結論
    if not args.skip_step1:
        _bench_step1(model, args.timeout)

    print("\n" + "=" * 60)
    print("読み方")
    print("=" * 60)
    print("  ⚠️ 結論は ③ で出す。① ② は素の速度を見るための参考値にすぎない")
    print("     （② は短縮プロンプト。本番より軽いので、通っても安心材料にならない）")
    print()
    print("  ③ が 180 秒を超える  → CHUNKING_LLM_TIMEOUT の既定では必ず落ちる")
    print("  生成速度が 1 桁 tok/s → モデルが重すぎる。小さいモデルを試す")
    print("  思考だけで本文が空    → 思考モデル。別モデルにする")
    print("  停止理由が length     → 上限まで生成し切っている")
    return 0


if __name__ == "__main__":
    sys.exit(main())
