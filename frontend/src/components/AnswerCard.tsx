// 回答カード: decision バッジ（answer=緑 / escalate=赤）、回答本文、出典リスト
// （[社内] と [Web] を区別表示）、groundedness スコア、エスカレ理由、アクション結果。
import { contradictionNotice, escalateReferenceNotice, parseCitation } from '../state/citations';
import type { JobTiming } from '../state/elapsed';
import type { SupportResult } from '../types';
import { JobFinishLine } from './JobClock';
import { Markdown } from './Markdown';

function escalateReason(result: SupportResult): string {
  if (result.forced_escalate) {
    return `エスカレ語を検知（意図分類: ${result.intent ?? '不明'}）による強制エスカレ`;
  }
  if (result.no_info_detected) {
    return '「情報なし回答」を検知（④\' ゲート）';
  }
  return '出典・支持率がしきい値未達（回答ゲート）';
}

function Citation({ text }: { text: string }) {
  const { kind, label, url } = parseCitation(text);
  const isWeb = kind === 'web';
  return (
    <li className={isWeb ? 'citation-web' : 'citation-internal'}>
      <span className="citation-label">{isWeb ? 'Web' : '社内'}</span>
      {url ? (
        // 出典 URL は開けるようにする（新規タブ・リファラを送らない）
        <a href={url} target="_blank" rel="noreferrer noopener">
          {label}
        </a>
      ) : (
        label
      )}
    </li>
  );
}

function CitationList({ citations, title }: { citations: string[]; title: string }) {
  if (citations.length === 0) return null;
  return (
    <div className="citations">
      <h3>{title}</h3>
      <ul>
        {citations.map((citation) => (
          <Citation key={citation} text={citation} />
        ))}
      </ul>
    </div>
  );
}

/**
 * 0-(A) 入力・質問分析の結果。
 *
 * 🔴 **保留した質問は必ず出す。** 出さないと「片方の質問が黙って落ちたのに、
 * 支持率が高いので高信頼として提示される」という、複数質問対応が最も危険とした
 * 事故（docs/multi_question_handling.md §概要）と区別がつかない。
 * 再構成後クエリも併記して、何を質問として解釈したかを検証できるようにする。
 */
function MultiQuestionNotice({ result }: { result: SupportResult }) {
  if (!result.is_multi_question) return null;
  const hasDeferred = result.deferred_questions.length > 0;
  const hasOutOfScope = result.out_of_scope_questions.length > 0;
  if (!hasDeferred && !hasOutOfScope && !result.reconstructed_query) return null;
  return (
    <div className="multi-question-notice">
      {result.reconstructed_query && (
        <p className="notice">
          この問い合わせは複数の質問を含むため、次の 1 問として解釈しました:{' '}
          <em>{result.reconstructed_query}</em>
        </p>
      )}
      {hasDeferred && (
        <>
          <h3>保留した質問（未回答）</h3>
          <ul className="deferred-questions">
            {result.deferred_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
          <p className="notice">
            これらには回答していません。必要であれば個別に問い合わせてください。
          </p>
        </>
      )}
      {/*
        ⚠️ **保留とは別の見出しにする。** 保留は「あとで聞き直せば答えられる」、
        範囲外は「この窓口では答えられない」で、利用者が次に取る行動が違う。
        断るだけで終わらせず、必ず窓口案内を添える（verticals.SCOPE_POLICY）。
      */}
      {hasOutOfScope && (
        <>
          <h3>担当範囲外の質問</h3>
          <ul className="out-of-scope-questions">
            {result.out_of_scope_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
          <p className="notice">
            {result.out_of_scope_guidance ||
              'これらは当窓口の担当範囲外のためお答えできません。該当する窓口へお問い合わせください。'}
          </p>
        </>
      )}
    </div>
  );
}

export function AnswerCard({
  result,
  timing,
}: {
  result: SupportResult;
  /** 実行の開始・完了時刻。カード末尾に「完了 … ／ 所要 …」を出す。 */
  timing?: JobTiming;
}) {
  const isAnswer = result.decision === 'answer';
  return (
    <section className={`answer-card ${isAnswer ? 'answer' : 'escalate'}`}>
      <div className="answer-header">
        <span className={`decision-badge ${result.decision}`}>
          {isAnswer ? 'answer（回答）' : 'escalate（有人対応へ）'}
        </span>
        {result.vertical && <span className="badge">vertical: {result.vertical}</span>}
        {result.used_web && <span className="badge">Web 使用</span>}
        {result.web_reused && <span className="badge">Web 再利用</span>}
      </div>

      <MultiQuestionNotice result={result} />

      {isAnswer ? (
        <>
          {result.answer ? (
            <Markdown source={result.answer} />
          ) : (
            <p className="answer-text">（回答なし）</p>
          )}
          {result.warning && (
            <p className="notice">
              ⚠️ 注意: この回答は出典による裏付けが十分ではありません。内容をご確認ください。
            </p>
          )}
          {result.used_web && result.contradiction && (
            <p className="notice">{contradictionNotice(result.citations)}</p>
          )}
          <CitationList citations={result.citations} title="出典" />
        </>
      ) : (
        <>
          {result.answer && (result.forced_escalate || result.citations.length > 0) ? (
            // 強制エスカレ（エスカレ語）や、出典付きの回答が生成できているのに
            // 方針でエスカレする場合は、生成済みの回答を「参考情報」として提示する
            // （「根拠が見つからなかった」と誤って伝えて有用な回答を捨てない）。
            <>
              {/*
                ⚠️ **文言を固定にしない。** 出典が Web だけでも「社内ナレッジに
                基づく」と表示していた（実測「明日の東京の天気は？」: RAG 0 件・
                出典 9 件すべて Web）。出典の実際の内訳から文言を決める。
              */}
              <p className="notice">{escalateReferenceNotice(result.citations)}</p>
              <Markdown source={result.answer} />
              <CitationList citations={result.citations} title="出典" />
            </>
          ) : (
            // 本当に根拠が得られなかった場合のみ「見つからなかった」と伝える。
            // Web 検索を実行していない（used_web=false）ときは「Web 検索にも」と言わない。
            <p className="answer-text">
              {result.used_web
                ? '社内ナレッジにも Web 検索にも十分な根拠が見つかりませんでした。'
                : '社内ナレッジに十分な根拠が見つかりませんでした。'}
              <br />→ 有人対応へエスカレーションします。
            </p>
          )}
          <p className="notice">理由: {escalateReason(result)}</p>
          {/*
            ⚠️ **回答が空でも、取得できた出典は必ず出す。**

            以前は上の三項演算子が `result.answer && …` で始まっていたため、
            回答が空（＝ローカル LLM が本文を返せなかった）だと分岐ごと落ち、
            出典ブロックへ到達しなかった。実測では Web 検索が 9 件の URL を
            取得できていたのに、画面には「根拠が見つかりませんでした」だけが
            出て**取得済みのリンクが捨てられていた**。

            回答を作れなくても「どこを見れば載っているか」は返せるので、
            候補リンクとして提示する。上の分岐で既に出典を出した場合
            （回答あり）は重複するため出さない。
          */}
          {!result.answer && (
            <CitationList
              citations={result.citations}
              title="参考リンク（検索で見つかった候補）"
            />
          )}
        </>
      )}

      {result.action && (
        <div className="action-result">
          <h3>アクション</h3>
          <p>
            種別 <code>{result.action.action_type}</code>
            {result.identity_checked && '（本人確認ステップあり）'}
          </p>
          <p className="action-message">{result.action_result}</p>
        </div>
      )}

      <dl className="metrics">
        <div>
          <dt>groundedness（支持率）</dt>
          <dd>
            {result.groundedness_decided === 0
              ? '判定不能（判定可能 0 主張）'
              : `${result.groundedness.toFixed(2)}（判定可能 ${result.groundedness_decided} 主張）`}
          </dd>
        </div>
        <div>
          <dt>全体信頼度</dt>
          <dd>{result.overall_confidence.toFixed(2)}</dd>
        </div>
        {result.source_agreement !== null && (
          <div>
            <dt>内部×Web 一致度</dt>
            <dd>{result.source_agreement.toFixed(2)}</dd>
          </div>
        )}
        {result.intent && (
          <div>
            <dt>意図分類</dt>
            <dd>{result.intent}</dd>
          </div>
        )}
        {result.model_used && (
          <div>
            <dt>使用モデル</dt>
            <dd>{result.model_used}</dd>
          </div>
        )}
      </dl>

      {timing && <JobFinishLine timing={timing} />}
    </section>
  );
}
