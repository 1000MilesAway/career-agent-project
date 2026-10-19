---
paths:
  - "tests/**"
  - "conftest.py"
---

# Тесты

- Без Postgres набор проходит, DB-тесты скипаются сами (`tests/dbmark.py`).
  Падение «потому что нет базы» — баг в тесте.
- `data/golden/` нет в git, поэтому в worktree и в CI тесты золотого набора
  скипаются — это нормально.
- Набор обязан проходить при пустом `DEEPSEEK_API_KEY`: секреты тестам задаёт
  `tests/conftest.py`, а не машина разработчика.
- Внешние сервисы мокаются. **Живые вызовы LLM запрещены** — промпты
  проверяются снапшотами на записанных ответах (`tests/fixtures/`).

Postgres для DB-тестов и локального API:

```bash
docker compose up -d                 # Postgres 16 на порту 5433
uv run alembic upgrade head
```
