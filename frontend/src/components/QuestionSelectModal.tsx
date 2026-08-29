// 0-(A) 入力・質問分析: 複数の主質問が見つかったときに、先に答える 1 つを選ばせる。
//
// ⚠️ **自動では選ばない。** 勝手に 1 つ選ぶと、選ばれなかった質問が黙って落ちた
// のか、そもそも検知されなかったのかを利用者が区別できない
// （docs/multi_question_handling.md §13.1）。選ばれなかった主質問は
// 「保留」として結果カードにも必ず出す。
//
// 「後で選ぶ（このまま実行）」は拒否に相当し、バックエンドは**原文のまま
// 単一質問として**処理する（escalate には倒さない。§13.8-7）。
import { useState } from 'react';

import type { InterventionInfo } from '../types';

interface Props {
  intervention: InterventionInfo;
  submitting: boolean;
  /** approve=true なら selectedOption を採用、false なら原文のまま実行。 */
  onRespond: (approve: boolean, selectedOption: string | null) => void;
}

export function QuestionSelectModal({ intervention, submitting, onRespond }: Props) {
  const options = intervention.options ?? [];
  const [selected, setSelected] = useState<string>(options[0] ?? '');

  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-label="回答する質問の選択">
        <h2>複数の質問が含まれています</h2>
        <p className="modal-message">{intervention.message}</p>

        <fieldset className="question-options">
          <legend>先に回答する質問を選んでください</legend>
          {options.map((option) => (
            <label key={option} className="question-option">
              <input
                type="radio"
                name="main-question"
                value={option}
                checked={selected === option}
                onChange={() => setSelected(option)}
                disabled={submitting}
              />
              <span>{option}</span>
            </label>
          ))}
        </fieldset>

        <p className="modal-note">
          選ばなかった質問は「保留」として結果に表示されます（黙って落としません）。
        </p>
        {typeof intervention.timeout_seconds === 'number' && (
          <p className="modal-note">
            {intervention.timeout_seconds} 秒で応答が無い場合は、原文のまま 1 回だけ実行します。
          </p>
        )}

        <div className="modal-actions">
          <button
            className="approve"
            disabled={submitting || !selected}
            onClick={() => onRespond(true, selected)}
          >
            この質問に回答する
          </button>
          <button
            className="reject"
            disabled={submitting}
            onClick={() => onRespond(false, null)}
          >
            選ばずに原文のまま実行
          </button>
        </div>
      </div>
    </div>
  );
}
