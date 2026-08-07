// FastAPI（backend/app/main.py）の API クライアント。
// 通信方式: ジョブ起動と HITL 応答は POST、ステップ進捗は SSE（EventSource）。
//
// Support（/api/support/*）と Review（/api/review/*）は同じ形をしており、
// SSE のイベント形式も同一。そのため購読関数は 1 本を共用する。
import type {
  ChunkingParams,
  DataJobStatusResponse,
  CollectionDetail,
  CollectionInfo,
  CollectionPoints,
  InputFileListResponse,
  QdrantHealth,
  QueryParams,
  RegisterParams,
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
 * `kind` で Support / Review / データ準備のどのストリームかを選ぶ。イベント形式は
 * 3 者同一なので、パースと終了判定は共通。
 */
export function subscribeStream(
  jobId: string,
  onEvent: (event: SupportEvent) => void,
  onError: (message: string) => void,
  kind: 'support' | 'review' | 'data' = 'support',
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


// ===========================================================================
// データ準備パイプライン（チャンキング → Q/A 生成 → Qdrant 登録 → コレクション管理）
//
// 参照系は素の GET、ジョブ系は Support / Review と同じ
// 「POST で起動 → SSE で購読 → 必要なら confirm」の流れ。
// SSE と confirm は 3 種のジョブで共通（/api/data/*）。
// ===========================================================================

/** Qdrant の稼働確認。**落ちていても 200 が返る**ので available で判定する。 */
export async function fetchQdrantHealth(): Promise<QdrantHealth> {
  const response = await requireOk(await fetch('/api/qdrant/health'));
  return response.json();
}

/** コレクション一覧。 */
export async function fetchCollections(): Promise<CollectionInfo[]> {
  const response = await requireOk(await fetch('/api/qdrant/collections'));
  return response.json();
}

/** コレクション詳細（ベクトル設定＋データ元の集計）。 */
export async function fetchCollectionDetail(name: string): Promise<CollectionDetail> {
  const response = await requireOk(
    await fetch(`/api/qdrant/collections/${encodeURIComponent(name)}`),
  );
  return response.json();
}

/** コレクションのポイントをプレビューする。列はコレクションごとに違う。 */
export async function fetchCollectionPoints(
  name: string,
  limit = 50,
): Promise<CollectionPoints> {
  const response = await requireOk(
    await fetch(`/api/qdrant/collections/${encodeURIComponent(name)}/points?limit=${limit}`),
  );
  return response.json();
}

/** 入力ファイルの候補一覧（許可ディレクトリ内）。 */
export async function fetchInputFiles(dir: string): Promise<InputFileListResponse> {
  const response = await requireOk(
    await fetch(`/api/files?dir=${encodeURIComponent(dir)}`),
  );
  return response.json();
}

/** チャンク化ジョブを起動する（非破壊なので承認なし）。 */
export async function startChunking(
  params: ChunkingParams,
): Promise<{ job_id: string; stream_url: string }> {
  const response = await requireOk(
    await fetch('/api/chunking/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }),
  );
  return response.json();
}

/**
 * Qdrant 登録ジョブを起動する。
 * ⚠️ `recreate: true` のときだけ intervention（承認）が発生する。
 */
export async function startRegister(
  params: RegisterParams,
): Promise<{ job_id: string; stream_url: string }> {
  const response = await requireOk(
    await fetch('/api/qdrant/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }),
  );
  return response.json();
}

/**
 * コレクション削除ジョブを起動する。**必ず承認が必要。**
 * HTTP DELETE ではなく POST なのは、承認を経ずに消える経路を作らないため。
 */
export async function startDelete(
  collections: string[],
): Promise<{ job_id: string; stream_url: string }> {
  const response = await requireOk(
    await fetch('/api/qdrant/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ collections }),
    }),
  );
  return response.json();
}

/** データ準備ジョブの HITL CONFIRM 応答（3 種共通）。 */
export async function confirmDataIntervention(
  jobId: string,
  interventionId: string,
  approve: boolean,
): Promise<{ status: string }> {
  const response = await requireOk(
    await fetch(`/api/data/confirm/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intervention_id: interventionId, approve }),
    }),
  );
  return response.json();
}

/**
 * データ準備ジョブの状態を問い合わせる。
 *
 * **再購読の前に「ジョブがまだ存在するか」を確かめる**用途で使う。
 * `JobManager` は完了ジョブを 50 件までしか保持しないため（`MAX_FINISHED_JOBS`）、
 * 古い job_id は 404 になる。SSE で直接つなぐと 404 が `onerror` として届き、
 * 「切断されました」という誤ったエラーになってしまう。
 */
export async function fetchDataJobStatus(jobId: string): Promise<DataJobStatusResponse> {
  const response = await requireOk(await fetch(`/api/data/result/${jobId}`));
  return response.json();
}
