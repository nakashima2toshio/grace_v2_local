---
name: grace-agent-ci
description: >-
  Work with CI, branching, auto-merge, and PR workflow in grace_v2 (and the
  sibling *_grace_agent repos). Use when editing .github/workflows/ci.yml, when
  ruff/lint blocks a PR, when configuring or relying on the claude/* auto-merge,
  or when creating branches/PRs via the GitHub MCP tools. Encodes the CI gate
  design, ruff config gotchas, and the remote-environment/PR conventions.
---

# grace_agent CI・自動マージ・PR運用スキル

## CI 構成（`.github/workflows/ci.yml`）— grace_v2

**必須ゲートは 4 つ。すべて blocking で、1 つでも赤ならマージされない。**

| ジョブ名 | 表示名 | 内容 |
|---|---|---|
| `build` | `compile (syntax gate)` | `python -m compileall -q -x '\.venv\|/\.git/\|/logs/' .`（依存不要の構文チェック） |
| `lint` | `ruff` | `ruff check .`（`ruff==0.12.11` 固定。`pyproject.toml` の `[tool.ruff]` に従う） |
| `backend-tests` | `pytest (backend)` | `pytest backend/tests -q -rs`（`PYTHONPATH=.`）。スタブベースで実 API キー・Qdrant 不要 |
| `frontend` | `frontend (tsc + vitest + build)` | `frontend/` で `npm ci` → `npm run lint`(tsc --noEmit) → `npm test`(vitest) → `npm run build` |

- **`auto-merge`** = `needs: [build, lint, backend-tests, frontend]`。4 ゲート成功後、`head_ref` が
  `claude/*` の PR を master へマージ（`gh pr ready` → `gh pr merge --merge`）。
  `hold` ラベルが付いた PR は対象外。
- トリガ: `pull_request`（master 宛、types に `ready_for_review`/`labeled`/`unlabeled` を含む）と
  `push`（master）。`auto-merge` は `github.event_name == 'pull_request'` のみ発火。
- `concurrency: ci-${{ PR番号 || ref }}` + `cancel-in-progress: true` で多重実行を抑制。

> ⚠️ **frontend ゲートを忘れない。** Python 側が全部緑でも `frontend/` の型エラー 1 個で
> マージは止まる。バックエンドの API スキーマを変えたら `frontend/src` の型も追随させ、
> ローカルで `cd frontend && npm run lint && npm test` を通してから push する。

## ruff 設定の要点（環境差バグ回避）
- `pyproject.toml`: `[tool.ruff] extend-exclude = [".venv", "logs"]`、
  `[tool.ruff.lint] select = ["E","F","I"]` / `ignore = ["E501"]`。
- **`[tool.ruff.lint.isort] known-first-party` を明示必須。** 未設定だと
  「CI（未インストール）＝first-party」「ローカル（導入済）＝third-party」で isort 分類が割れ、
  **I001 がローカル緑／CI 赤**になる。トップレベル module/package を列挙しておく
  （grace_v2 は `agent_cache`〜`support_actions` を列挙済み。**新規トップレベル
  モジュールを足したらここにも追記する**）。
- ローカル検証は `uv run ruff check . --no-cache`。負債の一括解消は安全 fix
  （F401/I001/F541）を `ruff check . --fix`、残り（E402/E701/E722/E741/F841/F811）は手動。
  E402 は `sys.path` 操作後の意図的 import なら `# noqa: E402`。

## ブランチ・PR 運用
- 開発は `claude/<topic>` ブランチ。**ドラフト PR で作成**（auto-merge が Ready 化してマージ）。
  bootstrap 用に自己マージさせたくない変更（`ci.yml` 自体など）は `ci/*` 等 `claude/` 以外の名前にする。
- master への直 push は許可（ブランチ保護なし）。マージを止めるのは上記 4 ゲートのみ。
- **指定ブランチの PR が既にマージ済みなら、そのブランチは使い回さない。**
  `git fetch origin master && git checkout -B <branch> origin/master` で master から作り直し、
  新しい PR を立てる（マージ済み履歴の上に積まない）。
- GitHub 操作は **`mcp__github__*` MCP ツール**（`gh` CLI はこの環境に無い）。ToolSearch で都度ロード。
  スコープ外 repo は `mcp__claude-code-remote__list_repos` / `add_repo`。
- **リモート Git プロキシは ref 削除を 403 で拒否する**（`git push origin --delete` 不可。
  MCP にも delete-branch / delete-ref ツールが無い）→ **ブランチ削除は GitHub UI か
  ユーザのローカルでしか行えない。** 実行できない旨を正直に報告し、コマンドを提示する。
- `--force-with-lease` が "stale info" で拒否されたら、remote-tracking ref が無いのが原因。
  一時 ref へ fetch して期待 SHA を明示する（`--force-with-lease=<branch>:<SHA>`）。
  その前に `git merge-base --is-ancestor` で上書き対象が master に入っているか必ず確認する。
- commit メッセージ末尾・PR 本文末尾に session リンクを付与（ハーネス規約）。
  **モデル識別子はコミット・PR 本文・コードコメントに書かない**（チャット返信のみ）。

## リモート実行環境
- コンテナは ephemeral・起動時に fresh clone。**コミット＆プッシュしないと消える。**
- `uv run` で依存解決可能（pytest 実走に利用）。`docker-compose/docker-compose.yml` が Qdrant。
  開発サーバ一括起動は `run_dev.sh`（uvicorn + Vite）。
- バックエンド単体起動: `uvicorn backend.app.main:app --reload --port 8000`。
  CLI 版は `uv run python agent_support_example.py --vertical gov -v "<質問>"`
  （両者は `backend/app/core/support_agent.py::run_support_agent_core` を共有する）。

## PRアクティビティ購読
- `subscribe_pr_activity` で CI 失敗・レビューコメントを受信。
  CI 成功・新規 push・コンフリクト遷移は webhook で来ないため、必要なら `send_later` で
  約 1 時間後の自己チェックインを再アーム（このサンドボックスでは `send_later` 不在のことが多い）。
