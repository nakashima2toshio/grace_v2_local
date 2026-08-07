// 実行中ジョブの job_id を、コンポーネントの寿命より長く覚えておくストア。
//
// ## 何のためにあるか
//
// タブ（およびサブタブ）は**アンマウントで切り替える**設計なので、離れると
// パネルの reducer 状態と SSE 購読が破棄される。ジョブ自体はバックエンドで
// 走り続けるが、`job_id` を失うため戻っても進捗を追えなかった。
//
// ここに `job_id` を残しておけば、再マウント時に購読し直せる。
// `Job.stream_events()` は**常にイベントを先頭からリプレイする**
// （`backend/app/core/jobs.py`）ので、再購読するだけでタイムラインごと復元される。
//
// ## なぜ React の状態でないのか
//
// 覚えておきたいのは「アンマウントされても消えない」情報なので、
// state に置くと目的を果たせない。`App` まで持ち上げる案もあるが、
// 進捗の所有者はパネル側という設計を崩したくないため、
// モジュールスコープの小さなストアにしてある。
//
// ローカル開発用のシングルページなので、タブを閉じれば消えて構わない
// （`sessionStorage` にはしない — 復元先のジョブがサーバ再起動で消えている
// 可能性があり、かえって不整合を招く）。
import type { DataJobKind } from '../types';

const activeJobs = new Map<DataJobKind, string>();

/** ジョブを起動したら覚える。 */
export function rememberJob(kind: DataJobKind, jobId: string): void {
  activeJobs.set(kind, jobId);
}

/** 再マウント時に引く。無ければ undefined。 */
export function recallJob(kind: DataJobKind): string | undefined {
  return activeJobs.get(kind);
}

/** ジョブが消えていた（サーバ再起動・GC）ときに捨てる。 */
export function forgetJob(kind: DataJobKind): void {
  activeJobs.delete(kind);
}

/** テスト用。全消去。 */
export function clearAllJobs(): void {
  activeJobs.clear();
}
