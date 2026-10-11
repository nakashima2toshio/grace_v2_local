#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qa_qdrant - Q/A 生成 → Qdrant 登録の CLI パッケージ。

データ準備 3 工程の ③ にあたる。各 CLI はこのパッケージ配下のモジュールが持つ。

    make_qa.py                  Q/A 生成のみ
    make_qa_register_qdrant.py  Q/A 生成 → Qdrant 登録の統合
    register_to_qdrant.py       既存 CSV → Qdrant 登録

各 CLI の使い方は `qa_qdrant/docs/README_qa_qdrant.md`、環境構築は `docs/data_prep_install.md`。

⚠️ **本ファイルには処理を書かないこと。**

2026-09-21 まで、ここには `make_qa.py` の v3.0 以前の写し（236 行）が入っていた。
`backend/app/core/data_jobs.py` の Qdrant 登録ジョブが
`from qa_qdrant.register_to_qdrant import ...` を実行するたび、パッケージ import の
副作用として `config` と `qa_generation.pipeline`（＝ Celery 一式）が読み込まれ、
**登録ジョブが使わないモジュールを 116 件・約 0.2 秒**余計に引き込んでいた。

`__init__.py` に import や実行コードを置くと、パッケージ内の**どのモジュールを
読むときでも**その代償を払うことになる。調査の記録は
`qa_qdrant/docs/README_qa_qdrant.md` §3（全文は git 履歴の旧 `qa_qdrant/docs/README.md` §4）。
"""
