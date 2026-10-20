# CLAUDE.md

<!--
Maintainer notes (HTML comments are stripped before loading, cost no context).
Keep this file short: it loads into every session. For each line ask
"would Claude make a mistake without it?" — if not, cut it.
Where things go instead:
  - server, CI, deploy, auth, mail, quotas      → docs/ops.md
  - pipeline invariant, sources                 → .claude/rules/pipeline.md (path-scoped)
  - test rules                                  → .claude/rules/tests.md (path-scoped)
  - rules for frontend/ only                    → .claude/rules/frontend.md (path-scoped)
  - a repeated agent workflow                   → a skill in .claude/skills/
  - why a decision was made                     → docs/decisions.md (append) +
                                                  one line in docs/decisions-index.md
  - reply format                                → ~/.claude/output-styles/concise-ru.md
No volatile numbers here (test counts etc.) — they go stale silently.
Prohibitions phrased as "never" make agents over-cautious: state the rule
where it applies (a path-scoped rule, a hook) instead of a global ban list.
-->

**career-agent** — коммерческий SaaS для поиска работы на рынке РФ: веб-сервис
по подписке, любые сферы; отклик отправляет сам пользователь. Пайплайн взят из
open-source [career-ops](https://github.com/career-ops-hq/career-ops) (далее
**CO**); его локальная копия `/home/vpn/career-ops` — эталон поведения, а не
библиотека. Расходиться с CO можно, но с записью в `docs/decisions.md`.

**Перед работой читай `docs/decisions-index.md`** — что решено и какие развилки
уже закрыты, по строке на решение; переоткрывать их без запроса пользователя
нельзя. Журнал `docs/decisions.md` целиком не читать: нужную запись найти грепом
по заголовку из индекса. Новое решение — скилл `decision`.

## Правило подтверждений

Агент **предлагает**, пользователь **принимает**. Ничего сгенерированного не
пишется в состояние без явного «да»: ни профиль поиска, ни стоп-списки, ни
резюме. Механизм — `core/proposals.py` (`current` / `proposed` / `rationale`,
дифф, статус); молчание = отказ, поля вне предложения не затираются.

## Архитектура

| Путь | Что |
|---|---|
| `core/` | ядро пайплайна: **решает, но не хранит**. Промпты, фильтры, triage, шаги модели, источники, экспорт резюме |
| `app/` | продукт: FastAPI + Postgres. Роутеры `app/api/`, связка ядра с БД `app/services/`, воркер очереди `app/worker.py`, админ-CLI `app/cli.py` |
| `frontend/` | кабинет: React SPA, свой образ с nginx |
| `migrations/` | Alembic |
| `docs/` | спецификация, архитектура, роадмап, `decisions.md`, `ops.md` |
| `data/` | персональные данные и списки-данные; в `.gitignore` |

1. **Ничто в `core/` не импортирует `app/`** — ядро тестируется без Postgres
   (`tests/test_layering.py`).
2. **`app/` отдаёт только JSON**, HTML — только `frontend/`.

Неочевидное:

- `core/sources/apply_link.py` — где пользователь **откликается**;
  `core/sources/description_link.py` — где вакансия **описана**.
- `core/liveness.py` закрывает вакансию только по явному признаку, иначе
  «неизвестно».
- Вакансии — **общий пул**: `postings` одна на всех, у пользователя — личная
  строка `vacancies`. Листинг, прочитанный недавно, из сети не запрашивается;
  отсеянное не хранится, причина вычисляется (`GET /api/vacancies/screened`).
- Деньги показывает только `app/api/admin.py` (`is_admin`); пользователь видит
  лишь проценты лимитов и время сброса.
- Списки каналов, стоп-слов, запросов, работодателей — **данные**
  (`data/sources/`), а не код.
- LLM — DeepSeek через OpenAI-совместимый API, модель задаётся конфигом;
  отдельные шаги (сейчас письма → Haiku) идут через OpenRouter по `LLM_STEP_MODELS`.

## Команды

Окружение — `uv` (системного `pip` нет).

```bash
uv sync --group dev
uv run pytest                        # ключ API и Postgres не нужны
uv run ruff check .
uv run python -m app.cli calibrate --source golden   # скоринг без модели и БД
docker compose up -d && uv run alembic upgrade head  # Postgres 16 на :5433
uv run uvicorn app.main:app --reload # API на :8000; после правки .py перезапускать
uv run python -m app.worker          # воркер очереди
```

## Git и выкатка

- **Работаем прямо в `main`**, без веток и PR, если не попросили. Worktree —
  только по просьбе; его ветка сносится в той же сессии.
- **До работы — `git fetch origin`**: дерево может отставать от `origin/main`, а
  снапшот git-статуса этого не покажет.
- **Закончил задачу — скилл `ship`, не спрашивая**: проверка, коммит, пуш,
  подтверждение по `revision`. Не пушить, только если пользователь сказал не
  пушить, работа не доделана или проверки красные — тогда так и написать.
- Боевой сервер — `ssh max@82.25.161.103`; остальное про сервер, CI, почту и
  лимиты — `docs/ops.md`. **Платные ручки** (сканы, оценки) против боевой базы —
  только с согласия пользователя.

## Конвенции

- Python 3.12, ruff (`line-length = 100`), pytest с `asyncio_mode = "auto"`.
- **Тексты для пользователя — по-русски; код, комментарии, коммиты — по-английски.**
- Комментарий объясняет **почему**; проверенная развилка стоит комментария и
  записи в `docs/decisions.md`.
- Секреты только в `.env`, в репозитории — `.env.example`.
- **При сжатии контекста сохранять** список изменённых файлов, какие проверки
  запускались и с каким итогом, запушено ли (с ревизией).
- **Адрес, пользователь, путь — проверять, а не вспоминать**: память могла
  относиться к другому проекту. Признак промаха — «не найдено» или «не тот ключ».
