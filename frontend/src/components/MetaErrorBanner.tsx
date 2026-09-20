// メタ情報（業界プロファイル / ルールセット）の取得に失敗したことを伝えるバナー。
//
// セレクタが空になる原因はほぼ「バックエンドが起動していない」だが、
// 以前は無言で空配列に倒していたため、ユーザーには「機能が壊れている」としか見えなかった。
// ここで理由と復旧手順を出し、**再読み込みボタンで復帰できる**ようにする
// （バックエンドを起動し直したあと、ページ全体をリロードしなくて済む）。

interface Props {
  message: string;
  onRetry: () => void;
  /** 再取得中はボタンを無効化して二重送信を防ぐ。 */
  retrying?: boolean;
}

export function MetaErrorBanner({ message, onRetry, retrying = false }: Props) {
  return (
    <div className="warn-banner meta-error" role="alert">
      <span>⚠️ {message}</span>
      <button type="button" onClick={onRetry} disabled={retrying}>
        {retrying ? '再取得中…' : '再取得'}
      </button>
    </div>
  );
}
