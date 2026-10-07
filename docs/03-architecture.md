# Архитектура

> Сверено с кодом и `docs/decisions-index.md` 2026-10-07. Документ начинался как
> черновик до кода: при расхождении правы код и индекс решений.

## Стек

| Слой | Выбор | Причина |
|---|---|---|
| API | FastAPI, SQLAlchemy 2 async, asyncpg, Alembic, Postgres 16 | async-стек под долгие LLM-вызовы, знаком пользователю |
| Фронтенд | React SPA (Vite, TypeScript), общается с API по JSON | кабинет и есть продукт: интерактивный отчёт, прогресс, трекер |
| Аутентификация | email + пароль (bcrypt), сессия JWT; регистрация с подтверждением почты (Resend), сброс пароля; вход через Telegram (OIDC); учётку может завести и админ CLI | см. decisions 09-30, 10-04 |
| Очередь LLM-задач | таблица `jobs` в Postgres + воркер-процесс (polling) | оценка 20–60 с, страница показывает прогресс; Redis на MVP не нужен |
| LLM | DeepSeek (`deepseek-flash`), библиотека `openai` с `base_url=https://api.deepseek.com` | цена, vision, JSON-режим, доступен из РФ |
| Парсинг файлов | pymupdf (PDF), python-docx (DOCX), `core/msdoc.py` и `core/rtf.py` на stdlib (DOC, RTF); структуру извлекает модель | не писать свой парсер; `.doc`/`.rtf` — исключение, см. decisions.md 2026-09-23 |
| PDF резюме | HTML-шаблон → WeasyPrint (или Typst) | ATS-читаемый одноколоночный шаблон |
| Оплата | ЮKassa — когда появится оплата; сейчас тариф выдаёт `app.cli user-plan` | не сделано |
| Хостинг | docker compose на VPS; DeepSeek доступен из РФ → сервер может быть в РФ | упрощает 152-ФЗ |

## Компоненты

```
Browser (React SPA) ──JSON/HTTPS──▶ FastAPI (REST) ──▶ Postgres
        ▲                                │                ▲
        └──── polling статуса job ────────┤                │
                                         └── enqueue ──▶ worker ──▶ DeepSeek API
                                                          │
                                                          └── PDF renderer
```

Долгие операции: SPA создаёт job, получает `job_id`, опрашивает `GET /jobs/{id}`
раз в 2 с и рисует прогресс. WebSocket/SSE в MVP не нужны.

- `app/api` — REST-роутеры: auth, resumes, vacancies, evaluations, artifacts, applications, jobs, health.
- `app/core` — конфиг, логирование, зависимости FastAPI, JWT.
- `app/cli` — административные команды: учётки, тарифы, `calibrate`.
- `core/` — всё, что решает без БД: `llm.py` (тонкая обёртка, модель конфигом),
  `pipeline.py` (шаги модели), `prompts.py`, фильтры, triage, `sources/`, разбор
  файлов (`msdoc.py`, `rtf.py`) и выгрузка (`export.py`).
- `app/services/` — связка ядра с БД: скан и пул, живость, расход, ревизии резюме, почта.
- `app/worker` — цикл выборки `jobs` со статусом `queued`, выполнение, запись результата.
- `frontend/` — отдельное React-приложение (Vite), собирается в статику, раздаётся nginx.

## Модель данных (черновик)

- `users` — email, password_hash, consent_at, plan, limits, is_active, created_at.
- `resumes` — user_id, source_file, raw_text, profile_json (структурированный профиль), is_active.
- `postings` — общий пул вакансий: source, external_id, поля листинга, raw_text, дедуп, живость (с 2026-10-03, см. `docs/decisions.md`).
- `vacancies` — личная строка пользователя: user_id, posting_id, stage, вердикты, избранное, скрытие.
- `evaluations` — vacancy_id, resume_id, report_json, score, verdict, model, tokens_in/out, cost.
- `artifacts` — evaluation_id, kind (tailored_resume/cover_letter/interview_prep), content_json, pdf_path.
- `applications` — vacancy_id, status, status_history_json, next_followup_at.
- `jobs` — kind, payload_json, status, attempts, result_json, error, created/started/finished_at.
- `payments` — provider, external_id, amount, status, plan, period. *(таблица появится вместе с оплатой, не в MVP)*

## LLM-пайплайн

Общие правила:
- все вызовы через JSON-режим с Pydantic-схемой ответа; при невалидном JSON — один ретрай;
- профиль резюме и системный промпт идут первыми (стабильный префикс → кэш DeepSeek, 0.003 $/M);
- температура низкая для скоринга, выше для писем;
- логируем модель, токены, стоимость на каждый вызов;
- фоновые задачи (тейлоринг PDF, интервью-подготовка) можно откладывать на off-peak DeepSeek
  (пик: 01:00–04:00 и 06:00–10:00 UTC, пн–пт), интерактивную оценку — сразу.

### parse_resume
Вход: текст резюме. Выход: `Profile` — контакты, целевая роль, опыт (компания, роль, период,
достижения с цифрами), навыки, образование, языки, пожелания.

### evaluate
Вход: `Profile` + текст вакансии. Выход: `EvaluationReport`:
```
score: 1..5 (шаг 0.5)
verdict: apply | apply_with_caveats | skip
role_summary: str
requirements: [{text, weight: must|nice, match: full|partial|none, evidence, gap_advice}]
red_flags: [str]
salary: {vacancy_range?, market_estimate?, comment}
questions_to_employer: [str]
angle: str   # чем зацепить именно этого работодателя
```
Порог рекомендации: `apply` при score ≥ 4.0; `skip` при < 3.0.

### tailor_resume
Вход: `Profile` + `EvaluationReport`. Выход: `TailoredResume` (те же секции, переупакованные,
+ список внесённых изменений, чтобы пользователь видел, что поменялось и не было выдумок).
Жёсткое правило в промпте: не добавлять опыт и навыки, которых нет в профиле.

### cover_letter
Шаг 1: 3 угла подачи (по одной строке). Шаг 2: текст под выбранный угол, ≤ 1 200 знаков,
без ИИ-штампов, на «вы».

### interview_prep
Выход: 8–12 вероятных вопросов с привязкой к требованиям; для поведенческих — STAR-истории
из достижений профиля; список «что подтянуть за 2 дня».

## Безопасность и ПД

- Резюме — персональные данные. Согласие в онбординге, политика обработки, уведомление
  в Роскомнадзор об обработке; трансграничная передача текста в DeepSeek (КНР) — отдельный пункт
  в согласии и уведомлении.
- Хранить только то, что нужно: исходный файл резюме можно удалять после парсинга.
- Удаление аккаунта и отзыв согласия — **не сделаны** (152-ФЗ, открытый вопрос в decisions 09-22);
  резюме заменяется загрузкой поверх.
- Пароли — bcrypt, никогда не логируются. Сброс по ссылке из письма выходит из всех сессий.
- Секреты в `.env`, никогда не логировать тексты резюме целиком.
