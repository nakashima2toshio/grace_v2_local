# backend/app/main.py
"""GRACE Web API（FastAPI）。

CLI と同じコアサービスを Web から呼ぶための API。エージェントは 2 つあり、
ジョブ基盤（`core/jobs.py`）・SSE・HITL ブリッジを共有する。

| エージェント | コア | ルータ |
|---|---|---|
| GRACE-Support（問い合わせ → 回答） | `core/support_agent.py` | `/api/support/*` |
| GRACE-Review（文書 → 指摘） | `core/review_agent.py` | `/api/review/*` |

ローカル開発専用（認証なし）。フロントエンドは frontend/（Vite + React + TS）。

起動（リポジトリルートで）::

    uvicorn backend.app.main:app --reload --port 8000

前提: `.env` に ANTHROPIC_API_KEY / GOOGLE_API_KEY、Qdrant 起動済み
（docker-compose -f docker-compose/docker-compose.yml up -d）。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import meta, review, support

# .env から ANTHROPIC_API_KEY / GOOGLE_API_KEY 等を読み込む（未導入でも続行）
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

app = FastAPI(
    title="GRACE API",
    description=(
        "業界特化・自律型エージェント（内部RAG＋Web裏取り＋HITL アクション）。"
        "Support（問い合わせ→回答）と Review（文書→指摘）を提供する。"
    ),
    version="1.1.0",
)

# ローカル開発: Vite dev サーバ（既定 5173）からのアクセスを許可
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(support.router)
app.include_router(review.router)
app.include_router(meta.router)
