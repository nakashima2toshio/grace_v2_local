// 指摘カード一覧。severity バッジ・根拠条文・修正案を出し、原文ハイライトと連動する。
import { useEffect, useRef } from 'react';
import type { FindingSummary, ReviewFinding, Severity } from '../types';

const SEVERITY_LABEL: Record<Severity, string> = {
  high: '重大',
  medium: '中',
  low: '軽微',
};

const STATUS_LABEL: Record<string, string> = {
  confirmed: '確定',
  review_required: '要確認',
  suppressed: '抑止',
};

// severity 降順 → 原文の出現順。重大な指摘から読める並びにする。
const SEVERITY_RANK: Record<Severity, number> = { high: 3, medium: 2, low: 1 };

function sortFindings(findings: ReviewFinding[]): ReviewFinding[] {
  return [...findings].sort((a, b) => {
    const rank = SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];
    return rank !== 0 ? rank : a.start - b.start;
  });
}

export function FindingSummaryBar({ summary }: { summary: FindingSummary }) {
  const total = summary.high + summary.medium + summary.low;
  return (
    <section className="finding-summary">
      <span className="sum-total">指摘 {total} 件</span>
      <span className="sum-badge sum-high">重大 {summary.high}</span>
      <span className="sum-badge sum-medium">中 {summary.medium}</span>
      <span className="sum-badge sum-low">軽微 {summary.low}</span>
      <span className="sum-sep">|</span>
      <span className="sum-badge">確定 {summary.confirmed}</span>
      <span className="sum-badge">要確認 {summary.review_required}</span>
      <span className="sum-badge sum-muted" title="根拠不足・実質性なしとして除外した指摘">
        抑止 {summary.suppressed}
      </span>
    </section>
  );
}

interface Props {
  findings: ReviewFinding[];
  selectedFindingId: string | null;
  onSelect: (findingId: string | null) => void;
}

export function FindingList({ findings, selectedFindingId, onSelect }: Props) {
  const selectedRef = useRef<HTMLLIElement | null>(null);

  // 原文のハイライトをクリックされたとき、該当カードまでスクロールする
  useEffect(() => {
    selectedRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [selectedFindingId]);

  if (findings.length === 0) {
    return (
      <section className="finding-list">
        <h2>指摘</h2>
        <p className="finding-empty">
          指摘はありませんでした（ルールに抵触する記述が見つかりませんでした）。
        </p>
      </section>
    );
  }

  return (
    <section className="finding-list">
      <h2>指摘（{findings.length}）</h2>
      <ul>
        {sortFindings(findings).map((finding) => {
          const selected = finding.finding_id === selectedFindingId;
          return (
            <li
              key={finding.finding_id}
              ref={selected ? selectedRef : null}
              className={`finding-card sev-${finding.severity}${selected ? ' selected' : ''}`}
              onClick={() => onSelect(selected ? null : finding.finding_id)}
            >
              <div className="finding-head">
                <span className={`sev-badge sev-${finding.severity}`}>
                  {SEVERITY_LABEL[finding.severity]}
                </span>
                <span className="finding-rule">{finding.rule_title}</span>
                <span className="finding-law">
                  {finding.law} {finding.article}
                </span>
                <span className="finding-status">
                  {STATUS_LABEL[finding.status] ?? finding.status}
                </span>
                {finding.forced && (
                  <span className="badge" title="重大リスク語を検知したため必ず人が確認します">
                    重大リスク語
                  </span>
                )}
                {finding.web_checked && <span className="badge">Web 裏取り済み</span>}
              </div>

              <blockquote className="finding-excerpt">{finding.excerpt}</blockquote>
              <p className="finding-message">{finding.message}</p>
              <p className="finding-suggestion">
                <strong>修正案:</strong> {finding.suggestion}
              </p>

              {finding.citations.length > 0 && (
                <details className="finding-citations">
                  <summary>根拠（{finding.citations.length}）</summary>
                  <ul>
                    {finding.citations.map((citation) => (
                      <li key={citation}>{citation}</li>
                    ))}
                  </ul>
                </details>
              )}

              <div className="finding-meta">
                確信度 {finding.confidence.toFixed(2)} / {finding.category} / {finding.rule_id}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
