// 原文表示＋指摘箇所のハイライト。クリックで該当の指摘カードを選択する。
//
// ⚠️ `dangerouslySetInnerHTML` は使わない。分割結果（highlight.ts）を
// React 要素の配列として組み立てる（XSS 回避）。設計書 §8.2。
import { buildHighlights } from '../state/highlight';
import type { ReviewFinding } from '../types';

interface Props {
  document: string;
  findings: ReviewFinding[];
  selectedFindingId: string | null;
  onSelect: (findingId: string | null) => void;
}

export function DocumentView({
  document,
  findings,
  selectedFindingId,
  onSelect,
}: Props) {
  if (!document) return null;
  const pieces = buildHighlights(document, findings);

  return (
    <section className="document-view">
      <h2>原文（{findings.length} 箇所を指摘）</h2>
      <div className="document-body">
        {pieces.map((piece, index) => {
          if (piece.findingId === null) {
            // 改行を保つのは CSS（white-space: pre-wrap）側の役割
            return <span key={index}>{piece.text}</span>;
          }
          const selected = piece.findingId === selectedFindingId;
          return (
            <mark
              key={index}
              className={`hl hl-${piece.severity}${selected ? ' hl-selected' : ''}`}
              data-finding-id={piece.findingId}
              title="クリックすると該当の指摘へ移動します"
              onClick={() => onSelect(selected ? null : piece.findingId)}
            >
              {piece.text}
            </mark>
          );
        })}
      </div>
    </section>
  );
}
