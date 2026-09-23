# Архитектура

## Стек

| Слой | Выбор | Причина |
|---|---|---|
| API | FastAPI, SQLAlchemy 2 async, asyncpg, Alembic, Postgres 16 | async-стек под долгие LLM-вызовы, знаком пользователю |
| Фронтенд | React SPA (Vite, TypeScript), общается с API по JSON | кабинет и есть продукт: интерактивный отчёт, прогресс, трекер |
| Аутентификация | email + пароль (bcrypt), сессия JWT; пользователей заводит админ CLI | в MVP нет регистрации и почты |
| Очередь LLM-задач | таблица `jobs` в Postgres + воркер-процесс (polling) | оценка 20–60 с, страница показывает прогресс; Redis на MVP не нужен |
| LLM | DeepSeek V4.1 Flash, библиотека `openai` с `base_url=https://api.deepseek.com` | цена, vision, JSON-режим, доступен из РФ |
| Парсинг файлов | pymupdf (PDF), python-docx (DOCX), `core/msdoc.py` и `core/rtf.py` на stdlib (DOC, RTF); структуру извлекает модель | не писать свой парсер; `.doc`/`.rtf` — исключение, см. decisions.md 2026-09-23 |
| PDF резюме | HTML-шаблон → WeasyPrint (или Typst) | ATS-читаемый одноколоночный шаблон |
| Оплата | ЮKassa, вместе с публичной регистрацией | не в MVP |
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
- `app/cli` — административные команды, в MVP главная — создание пользователя.
- `app/services/llm` — тонкая обёртка: `complete(prompt, schema) -> pydantic`, провайдер конфигом.
- `app/services/pipeline` — evaluate, tailor_resume, cover_letter, interview_prep, parse_resume.
- `app/services/files` — PDF/DOC/DOCX/RTF → text, HTML → PDF.
- `app/worker` — цикл выборки `jobs` со статусом `queued`, выполнение, запись результата.
- `frontend/` — отдельное React-приложение (Vite), собирается в статику, раздаётся nginx.

## Модель данных (черновик)

- `users` — email, password_hash, consent_at, plan, limits, is_active, created_at.
- `resumes` — user_id, source_file, raw_text, profile_json (структурированный профиль), is_active.
- `vacancies` — user_id, source (text/screenshot/url/hh_api), raw_text, url, title, company, parsed_json.
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
- Удаление аккаунта — кнопка в настройках кабинета, удаляет всё; плюс CLI-команда у админа.
- Пароли — bcrypt, никогда не логируются. Пока учётки заводятся вручную,
  пароль передаётся пользователю вне сервиса и его стоит сменить при первом входе.
- Секреты в `.env`, никогда не логировать тексты резюме целиком.
