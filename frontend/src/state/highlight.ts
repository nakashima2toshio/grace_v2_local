// 原文を「非該当テキスト」と「指摘スパン」へ分割する純関数。
//
// 設計: backend/docs/review_agent_spec.md §8.2。
// `ReviewFinding.start` / `.end` は**原文の文字オフセット**なので、原文をそのまま
// 切り出せばハイライトになる（バックエンドは分割時に正規化を一切していない）。
//
// ⚠️ `dangerouslySetInnerHTML` を使わずに済むよう、ここでは**データだけ**を作る。
// React 要素の組み立ては DocumentView 側で行う（XSS 回避）。
import type { ReviewFinding, Severity } from '../types';

export interface HighlightPiece {
  /** 原文の該当部分。 */
  text: string;
  /** ハイライト対象なら指摘 ID、通常テキストなら null。 */
  findingId: string | null;
  severity: Severity | null;
}

// 重なったときにどちらを優先するか（大きいほど優先）。
const SEVERITY_RANK: Record<Severity, number> = { high: 3, medium: 2, low: 1 };

/**
 * 指摘を start 昇順に整列し、重なりを解消する。
 *
 * 重なりは「同じ文言が複数のルールに触れる」ときに普通に起きる
 * （例: 「業界No.1」は景表法の優良誤認と、打消し表示の両方で拾われうる）。
 * その場合は severity の高い方を残す。同値なら先に来た方（先勝ち）。
 */
export function resolveOverlaps(findings: ReviewFinding[]): ReviewFinding[] {
  const sorted = [...findings]
    .filter((f) => f.end > f.start)
    .sort((a, b) => {
      if (a.start !== b.start) return a.start - b.start;
      // 同じ開始位置なら severity 高 → 長い方の順で先に置く
      const rank = SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];
      if (rank !== 0) return rank;
      return b.end - a.end;
    });

  const kept: ReviewFinding[] = [];
  for (const finding of sorted) {
    const previous = kept[kept.length - 1];
    if (previous && finding.start < previous.end) {
      // 重なっている → severity が高い方だけを残す
      if (SEVERITY_RANK[finding.severity] > SEVERITY_RANK[previous.severity]) {
        kept[kept.length - 1] = finding;
      }
      continue;
    }
    kept.push(finding);
  }
  return kept;
}

/**
 * 原文を描画用の断片列へ分割する。
 *
 * オフセットが原文の範囲外を指していた場合は、その指摘を無視して
 * 本文を欠落させない（バックエンドの不整合で画面が壊れるのを防ぐ）。
 */
export function buildHighlights(
  document: string,
  findings: ReviewFinding[],
): HighlightPiece[] {
  const pieces: HighlightPiece[] = [];
  let cursor = 0;

  for (const finding of resolveOverlaps(findings)) {
    if (finding.start < cursor || finding.end > document.length) continue;
    if (finding.start > cursor) {
      pieces.push({
        text: document.slice(cursor, finding.start),
        findingId: null,
        severity: null,
      });
    }
    pieces.push({
      text: document.slice(finding.start, finding.end),
      findingId: finding.finding_id,
      severity: finding.severity,
    });
    cursor = finding.end;
  }

  if (cursor < document.length) {
    pieces.push({ text: document.slice(cursor), findingId: null, severity: null });
  }
  return pieces;
}
