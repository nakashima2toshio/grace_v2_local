#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
smart_qa_generator.py - コンテンツを考慮したインテリジェントQ/A生成システム v3.0

改修内容（v3.0）:
- 分析(analyze_chunk)＋生成(generate_qa_pairs)の2段階方式を削除し、
  analyze_and_generate() による構造化出力1回（response_schema=SmartQAResult）に統合
  （Markdownフェンス手剥がし＋json.loads の脆弱なパースを排除）

特徴:
- LLMによるチャンク分析で適切なQ/A数を動的決定（0〜5個）
- 重要トピック・重要度・複雑さを構造化スキーマ(SmartQAResult)で取得
- チャンク1件 = LLM呼び出し1回（コスト最小）
"""

import logging
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from helper.helper_llm import create_llm_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# 構造化出力スキーマ（分析 + Q/A生成の統合用）
# ============================================================

class SmartQAPair(BaseModel):
    """Q/Aペア1件"""
    question: str = Field(..., description="自然な日本語の質問文")
    answer: str = Field(..., description="チャンクの情報のみに基づく簡潔な回答（50-150文字程度）")
    topic: str = Field("その他", description="Q/Aの主題（1-3単語）")


class SmartQAResult(BaseModel):
    """チャンク分析とQ/A生成の統合結果"""
    qa_count: int = Field(..., ge=0, le=5, description="このチャンクから生成すべきQ/A数（0-5）")
    key_topics: List[str] = Field(default_factory=list, description="主要トピックのリスト")
    importance_score: float = Field(0.5, ge=0.0, le=1.0, description="情報の重要度（0.0-1.0）")
    complexity: str = Field("medium", description="複雑さ（low/medium/high）")
    reasoning: str = Field("", description="qa_count の判断理由（1-2文）")
    qa_pairs: List[SmartQAPair] = Field(
        default_factory=list,
        description="生成したQ/Aペア（qa_count 個。qa_count=0 の場合は空リスト）"
    )


class SmartQAGenerator:
    """
    コンテンツを考慮したインテリジェントQ/A生成クラス（構造化出力1回方式）
    """

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: Optional[str] = None):
        """
        初期化

        Args:
            model: 使用するClaudeモデル（デフォルト: claude-sonnet-4-6）
            api_key: 未使用（統一クライアントが環境変数からキーを解決する）
        """
        self.model = model

        # 統一 LLM クライアント（Anthropic Claude）を使用する。
        self.client = create_llm_client(provider="anthropic", default_model=model)
        # 直近の analyze_and_generate 呼び出しのトークン使用量。
        # process_chunk → Celery worker → collect_results(usage_out) へ伝播し、
        # トークン集計サマリーを実値化する（#67 の usage 配管）。
        self.last_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        logger.info(f"統一LLMクライアント(Anthropic)を使用 (model={self.model})")

    COMBINED_PROMPT = """
以下のテキストチャンクを分析し、適切な数のQ/Aペアを生成してください。
分析（Q/A数の決定）と生成を1回で行います。

# Step 1: 分析（qa_count の決定基準）
- 0個: 補足情報のみ・意味のない繰り返し・メタ情報のみ（ページ番号、参照リンク等）
- 1個: 単純な事実の記述（1つの情報のみ）
- 2個: 関連する2つの事実
- 3個（標準）: 複数の関連情報を含む標準的な説明パラグラフ
- 4-5個: 高密度な技術情報・複数の独立したポイント・重要な警告や注意事項を含む

# Step 2: Q/A生成のガイドライン
1. 質問: 自然な日本語で、ユーザーが実際に尋ねそうな形式（「〜は何ですか？」等）
2. 回答: 簡潔かつ正確に、チャンクの情報のみを使用（推測しない）、50-150文字程度
3. 優先順位: 重要な情報から順にQ/A化、警告・注意事項は必ず含める
4. topic: 各Q/Aの主題を1-3単語で表現
5. qa_pairs の件数は必ず qa_count と一致させること（qa_count=0 なら空リスト）

# 重要な注意
- 質より量を優先しない（無駄なQ/Aは作らない）
- 重複した情報は1つのQ/Aにまとめる

# チャンク
```
{chunk_text}
```
"""

    def analyze_and_generate(self, chunk_text: str) -> SmartQAResult:
        """チャンク分析とQ/A生成を1回の構造化出力呼び出しで実行する。

        旧実装は analyze_chunk()（LLM 1回目）+ generate_qa_pairs()（LLM 2回目）の
        2段階だったが、1回の構造化出力（response_schema=SmartQAResult）に統合して
        コストを半減し、Markdownフェンス除去によるJSONパースの脆弱性も排除した。
        """
        # generate_structured は解析済みの SmartQAResult インスタンスを直接返す。
        result = self.client.generate_structured(
            prompt=self.COMBINED_PROMPT.format(chunk_text=chunk_text),
            response_schema=SmartQAResult,
            model=self.model,
            max_output_tokens=4096,
            temperature=0.2,
        )
        if result is None:
            raise ValueError("analyze_and_generate returned empty response")
        # per-call トークン使用量を取り込む（AnthropicClient.last_usage 由来）。
        # process_chunk → Celery worker → collect_results(usage_out) へ伝播する。
        client_usage = getattr(self.client, "last_usage", None)
        if isinstance(client_usage, dict):
            self.last_usage = {
                "input_tokens": int(client_usage.get("input_tokens", 0) or 0),
                "output_tokens": int(client_usage.get("output_tokens", 0) or 0),
            }
        return result

    def process_chunk(self, chunk_text: str) -> Dict:
        """
        チャンクの分析とQ/A生成を一括実行（構造化出力1回）

        旧実装にあった「分析→生成」の2段階方式（Markdownフェンス手剥がし＋
        json.loads の脆弱なパース）は削除済み。構造化出力が失敗した場合は
        success=False を返し、呼び出し側でそのチャンクをスキップする。

        Returns:
            dict: {
                'analysis': Dict,        # 分析結果
                'qa_pairs': List[Dict],  # 生成されたQ/A
                'usage': Dict[str, int], # トークン使用量 {input_tokens, output_tokens}
                'success': bool          # 成功フラグ
            }
        """
        try:
            result = self.analyze_and_generate(chunk_text)

            analysis = {
                'qa_count'        : result.qa_count,
                'key_topics'      : result.key_topics,
                'importance_score': result.importance_score,
                'complexity'      : result.complexity,
                'reasoning'       : result.reasoning,
            }
            qa_pairs = [
                {'question': qa.question, 'answer': qa.answer, 'topic': qa.topic or 'その他'}
                for qa in result.qa_pairs
            ]
            logger.info(
                f"Q/A生成完了（統合1回呼び出し）: qa_count={result.qa_count}, "
                f"生成={len(qa_pairs)}個, 重要度={result.importance_score:.2f}"
            )
            return {
                'analysis': analysis,
                'qa_pairs': qa_pairs,
                'usage'   : dict(self.last_usage),
                'success' : True
            }

        except Exception as e:
            logger.error(f"チャンク処理エラー（構造化出力に失敗）: {e}")
            return {
                'analysis': {},
                'qa_pairs': [],
                'usage'   : {"input_tokens": 0, "output_tokens": 0},
                'success' : False
            }


# ============================================================
# 統計分析ユーティリティ
# ============================================================

def analyze_qa_statistics(results: List[Dict]) -> Dict:
    """
    Q/A生成結果の統計分析

    Args:
        results: process_chunk()の結果リスト

    Returns:
        dict: 統計情報
    """
    total_chunks = len(results)
    total_qa = sum(len(r['qa_pairs']) for r in results)

    qa_distribution = {}
    for r in results:
        count = len(r['qa_pairs'])
        qa_distribution[count] = qa_distribution.get(count, 0) + 1

    avg_qa_per_chunk = total_qa / total_chunks if total_chunks > 0 else 0

    importance_scores = [
        r['analysis'].get('importance_score', 0)
        for r in results
        if r['analysis']
    ]
    avg_importance = sum(importance_scores) / len(importance_scores) if importance_scores else 0

    return {
        'total_chunks'        : total_chunks,
        'total_qa_pairs'      : total_qa,
        'avg_qa_per_chunk'    : avg_qa_per_chunk,
        'avg_importance_score': avg_importance,
        'qa_distribution'     : qa_distribution
    }


# ============================================================
# 使用例
# ============================================================

if __name__ == "__main__":
    import os

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("エラー: ANTHROPIC_API_KEY が設定されていません")
        exit(1)

    generator = SmartQAGenerator(api_key=api_key)

    test_chunks = [
        "この製品は赤色です。",
        """
        この製品は赤色で、サイズはMサイズです。
        価格は3,000円で、送料無料です。
        """,
        """
        AES-256暗号化アルゴリズムは、対称鍵暗号方式の一種で、
        256ビットの鍵長を持ちます。NIST（米国国立標準技術研究所）
        により承認されており、機密情報の保護に広く使用されています。
        ブロック暗号として動作し、128ビットのブロックサイズで
        データを処理します。CBC、GCM、CTRなど複数のモードが利用可能で、
        用途に応じて選択できます。
        """,
        "詳細については付録Aを参照してください。"
    ]

    results = []
    print("=" * 60)
    print("スマートQ/A生成システム - デモ v3.0（構造化出力1回方式）")
    print("=" * 60)

    for i, chunk in enumerate(test_chunks, 1):
        print(f"\n{'=' * 60}")
        print(f"チャンク {i}")
        print(f"{'=' * 60}")
        print(f"内容:\n{chunk.strip()}\n")

        result = generator.process_chunk(chunk)
        results.append(result)

        if result['success']:
            analysis = result['analysis']
            qa_pairs = result['qa_pairs']

            print("【分析結果】")
            print(f"  Q/A数      : {analysis['qa_count']}")
            print(f"  重要度     : {analysis['importance_score']:.2f}")
            print(f"  複雑さ     : {analysis['complexity']}")
            print(f"  主要トピック: {', '.join(analysis['key_topics']) if analysis['key_topics'] else 'なし'}")
            print(f"  理由       : {analysis['reasoning']}")

            if qa_pairs:
                print("\n【生成されたQ/A】")
                for j, qa in enumerate(qa_pairs, 1):
                    print(f"\n  Q{j} ({qa.get('topic', 'N/A')}): {qa['question']}")
                    print(f"  A{j}: {qa['answer']}")
        else:
            print("❌ 処理失敗")

    print(f"\n{'=' * 60}")
    print("統計情報")
    print(f"{'=' * 60}")

    stats = analyze_qa_statistics(results)
    print(f"総チャンク数        : {stats['total_chunks']}")
    print(f"総Q/A数            : {stats['total_qa_pairs']}")
    print(f"平均Q/A数/チャンク : {stats['avg_qa_per_chunk']:.2f}")
    print(f"平均重要度         : {stats['avg_importance_score']:.2f}")
    print("\nQ/A数分布:")
    for count, freq in sorted(stats['qa_distribution'].items()):
        print(f"  {count}個: {freq}チャンク")

    print(f"\n{'=' * 60}")
    print("✅ デモ完了")
    print(f"{'=' * 60}")
