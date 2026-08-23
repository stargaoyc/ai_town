"""导出 OpenAPI spec 到 openapi.json（供前端 pnpm gen:api 生成 TypeScript 类型）

用法（在 packages/backend 下）：
    .venv/Scripts/python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Settings() 在导入 src.main 时即实例化，提供占位值避免环境缺失时失败
for key, value in {
    "DATABASE_URL": "postgresql+asyncpg://placeholder:placeholder@localhost/placeholder",
    "REDIS_URL": "redis://localhost:6379/0",
    "OPENAI_API_KEY": "placeholder",
    "JWT_SECRET": "export-openapi-placeholder-secret-not-used",
}.items():
    os.environ.setdefault(key, value)

from src.main import app  # noqa: E402


def main() -> None:
    spec = app.openapi()
    out_path = BACKEND_DIR / "openapi.json"
    out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"openapi.json written: {len(spec['paths'])} paths -> {out_path}")


if __name__ == "__main__":
    main()
