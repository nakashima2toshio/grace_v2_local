// FastAPI（backend/app/main.py）の API クライアント。
// 通信方式: ジョブ起動と HITL 応答は POST、ステップ進捗は SSE（EventSource）。
//
// Support（/api/support/*）と Review（/api/review/*）は同じ形をしており、
// SSE のイベント形式も同一。そのため購読関数は 1 本を共用する。
import type {
  QueryParams,
  ReviewParams,
  RuleSetInfo,
  SupportEvent,
  VerticalInfo,
} from '../types';

async function requireOk(response: Response): Promise<Response> {
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(`API エラー (${response.status}): ${body || response.statusText}`);
  }
  return response;
}

/** 問い合わせジョブを起動し job_id を得る。 */
export async function startQuery(
  params: QueryParams,
): Promise<{ job_id: string; stream_url: string }> {
  const response = await requireOk(
    await fetch('/api/support/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }),
  );
  return response.json();
}

/** HITL CONFIRM への応答（承認 / 拒否）。 */
export async function confirmIntervention(
  jobId: string,
  interventionId: string,
  approve: boolean,
): Promise<{ status: string }> {
  const response = await requireOk(
    await fetch(`/api/support/confirm/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intervention_id: interventionId, approve }),
    }),
  );
  return response.json();
}

/** 業界プロファイル一覧（セレクタ用）。 */
export async function fetchVerticals(): Promise<VerticalInfo[]> {
  const response = await requireOk(await fetch('/api/verticals'));
  return response.json();
}

/** 文書レビュージョブを起動し job_id を得る。 */
export async function startReview(
  params: ReviewParams,
): Promise<{ job_id: string; stream_url: string }> {
  const response = await requireOk(
    await fetch('/api/review/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }),
  );
  return response.json();
}

/** レビューの HITL CONFIRM への応答（承認 / 拒否）。 */
export async function confirmReviewIntervention(
  jobId: string,
  interventionId: string,
  approve: boolean,
): Promise<{ status: string }> {
  const response = await requireOk(
    await fetch(`/api/review/confirm/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intervention_id: interventionId, approve }),
    }),
  );
  return response.json();
}

/** ルールセット一覧（セレクタ用）。 */
export async function fetchRuleSets(): Promise<RuleSetInfo[]> {
  const response = await requireOk(await fetch('/api/rulesets'));
  return response.json();
}

/**
 * SSE でステップ進捗を購読する。`done` イベントで自動クローズ。
 * 戻り値は購読解除関数（アンマウント時に呼ぶ）。
 *
 * `kind` で Support / Review のどちらのストリームかを選ぶ。イベント形式は
 * 両者同一なので、パースと終了判定は共通。
 */
export function subscribeStream(
  jobId: string,
  onEvent: (event: SupportEvent) => void,
  onError: (message: string) => void,
  kind: 'support' | 'review' = 'support',
): () => void {
  const source = new EventSource(`/api/${kind}/stream/${jobId}`);
  source.onmessage = (message) => {
    let event: SupportEvent;
    try {
      event = JSON.parse(message.data) as SupportEvent;
    } catch {
      return;
    }
    onEvent(event);
    if (event.type === 'done') {
      source.close();
    }
  };
  source.onerror = () => {
    // done 前の切断のみエラー扱い（close 済みなら no-op）
    if (source.readyState === EventSource.CLOSED) return;
    source.close();
    onError('進捗ストリームが切断されました。バックエンドの起動を確認してください。');
  };
  return () => source.close();
}
