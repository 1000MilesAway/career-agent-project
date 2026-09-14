"""CLI for the probe.

    python -m probe.run one   --resume data/resumes/r.pdf --vacancy data/vacancies/v.txt
    python -m probe.run batch --resume data/resumes/r.pdf --vacancies data/vacancies/
    python -m probe.run cost                      # summarise everything in out/

Batch mode is what answers "сколько ресурсов на юзера".
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

from . import history, profiles, proposals
from .defaults import starter_profile
from .filters import PreScreen, SearchProfile
from .llm import LLM, USD_RUB, Ledger
from .pipeline import build_search_profile, load_vacancy, parse_resume, read_document, run_all
from .sources import habr, habr_api

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"


def _fmt_money(usd: float) -> str:
    return f"{usd:.4f} $ / {usd * USD_RUB:.2f} ₽"


def _print_ledger(ledger: Ledger, title: str = "Расход") -> None:
    print(f"\n{title}")
    print(f"  {'шаг':<16} {'вход':>8} {'кэш':>7} {'выход':>7} {'сек':>6} {'цена':>18}")
    for c in ledger.calls:
        mark = " !" if c.error else ""
        print(
            f"  {c.step:<16} {c.total_in:>8} {c.cache_hit:>7} {c.output:>7} "
            f"{c.seconds:>6.1f} {_fmt_money(c.usd):>18}{mark}"
        )
    print(f"  {'ИТОГО':<16} {'':>8} {'':>7} {'':>7} {ledger.seconds:>6.1f} "
          f"{_fmt_money(ledger.usd):>18}")


def _report_summary(report: dict) -> None:
    print(f"\n  Балл: {report.get('score')}  Вердикт: {report.get('verdict')}")
    print(f"  {report.get('role_summary', '')}")
    reqs = report.get("requirements", [])
    musts = [r for r in reqs if r.get("weight") == "must"]
    closed = [r for r in musts if r.get("match") == "full"]
    print(f"  Обязательных требований: {len(musts)}, закрыто полностью: {len(closed)}")
    if report.get("red_flags"):
        print("  Красные флаги:")
        for f in report["red_flags"]:
            print(f"    - {f}")


def _save(name: str, payload: dict) -> Path:
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT / f"{stamp}-{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _vacancy_files(folder: Path) -> list[Path]:
    exts = {".txt", ".md", ".pdf", ".docx"}
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in exts)


def cmd_one(args: argparse.Namespace) -> int:
    resume_text = read_document(Path(args.resume))
    vacancy_text = read_document(Path(args.vacancy))
    steps = profiles.get(args.steps).steps

    print(f"Резюме:  {args.resume} ({len(resume_text)} знаков)")
    print(f"Вакансия: {args.vacancy} ({len(vacancy_text)} знаков)")
    print(f"Модель: {args.model or 'из .env'}, шаги: {', '.join(steps)}")

    results, ledger = run_all(resume_text, vacancy_text, steps=steps, model=args.model)

    if "report" in results:
        _report_summary(results["report"])
    _print_ledger(ledger)

    payload = {
        "resume": str(args.resume),
        "vacancy": str(args.vacancy),
        "model": args.model,
        "results": results,
        "usage": ledger.as_dict(),
    }
    print(f"\nПолный отчёт: {_save('one', payload)}")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    resume_path = Path(args.resume)
    resume_text = read_document(resume_path)
    files = _vacancy_files(Path(args.vacancies))
    if not files:
        print(f"В {args.vacancies} нет файлов вакансий (.txt/.md/.pdf/.docx)", file=sys.stderr)
        return 1

    steps = profiles.get(args.steps).steps
    print(f"Резюме: {resume_path.name}")
    print(f"Вакансий: {len(files)}, шаги: {', '.join(steps)}\n")

    # Parse the resume once — in the real product the profile is stored, not re-parsed.
    shared = Ledger()
    profile = parse_resume(LLM(model=args.model, ledger=shared), resume_text)
    print(f"Профиль разобран: {profile.get('target_role')}, "
          f"{profile.get('years_experience')} лет, {profile.get('field')}")
    _print_ledger(shared, "Разбор резюме (один раз на пользователя)")

    rows = []
    all_results = []
    for i, vf in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {vf.name}")
        try:
            vacancy_text = read_document(vf)
            results, ledger = run_all(
                resume_text, vacancy_text, steps=steps, model=args.model, profile=profile
            )
        except Exception as e:
            print(f"  ОШИБКА: {type(e).__name__}: {e}")
            rows.append({"vacancy": vf.name, "error": str(e)[:200]})
            continue

        report = results.get("report", {})
        print(f"  балл {report.get('score')}  {report.get('verdict')}  "
              f"{ledger.seconds:.0f} с  {_fmt_money(ledger.usd)}")
        rows.append({
            "vacancy": vf.name,
            "score": report.get("score"),
            "verdict": report.get("verdict"),
            "usd": round(ledger.usd, 6),
            "rub": round(ledger.rub, 4),
            "seconds": round(ledger.seconds, 1),
            "in_tokens": sum(c.total_in for c in ledger.calls),
            "out_tokens": sum(c.output for c in ledger.calls),
        })
        all_results.append({"vacancy": vf.name, "results": results, "usage": ledger.as_dict()})

    _summary(rows, shared, steps)
    payload = {
        "resume": str(resume_path),
        "steps": list(steps),
        "model": args.model,
        "profile": profile,
        "rows": rows,
        "parse_usage": shared.as_dict(),
        "runs": all_results,
    }
    print(f"\nПолный отчёт: {_save('batch', payload)}")
    return 0


def _summary(rows: list[dict], parse_ledger: Ledger, steps: tuple[str, ...]) -> None:
    ok = [r for r in rows if "error" not in r]
    if not ok:
        print("\nНи одной успешной оценки.")
        return

    print("\n" + "=" * 72)
    print("СВОДКА")
    print("=" * 72)
    print(f"  {'вакансия':<34} {'балл':>5} {'вердикт':>20} {'цена, ₽':>9}")
    for r in sorted(ok, key=lambda x: -(x.get("score") or 0)):
        name = r["vacancy"][:33]
        print(f"  {name:<34} {str(r.get('score')):>5} {str(r.get('verdict')):>20} {r['rub']:>9.2f}")

    scores = [r["score"] for r in ok if isinstance(r.get("score"), (int, float))]
    usd = [r["usd"] for r in ok]
    secs = [r["seconds"] for r in ok]

    print("\n  Честность оценки")
    if scores:
        print(f"    баллы: от {min(scores)} до {max(scores)}, медиана {statistics.median(scores)}")
        spread = max(scores) - min(scores)
        if spread < 1.0:
            print("    ВНИМАНИЕ: разброс меньше 1.0 — модель ставит всем одно и то же,")
            print("    промпт надо чинить до того, как писать продукт.")
        verdicts: dict[str, int] = {}
        for r in ok:
            verdicts[str(r.get("verdict"))] = verdicts.get(str(r.get("verdict")), 0) + 1
        print(f"    вердикты: {verdicts}")

    print("\n  Ресурсы на одну вакансию")
    print(f"    время:  медиана {statistics.median(secs):.0f} с, максимум {max(secs):.0f} с")
    print(f"    цена:   медиана {_fmt_money(statistics.median(usd))}")
    print(f"    токены: вход {statistics.median([r['in_tokens'] for r in ok]):.0f}, "
          f"выход {statistics.median([r['out_tokens'] for r in ok]):.0f} (медиана)")

    per_vac = statistics.median(usd)
    parse_usd = parse_ledger.usd
    print("\n  Прогноз на пользователя (шаги: " + ", ".join(steps) + ")")
    print(f"    разбор резюме, один раз: {_fmt_money(parse_usd)}")
    for n in (10, 30, 100):
        total = parse_usd + per_vac * n
        print(f"    {n:>3} вакансий: {_fmt_money(total)}")
    monthly = parse_usd + per_vac * 30
    print(f"\n    При подписке 1490 ₽/мес и 30 вакансиях себестоимость LLM "
          f"{monthly * USD_RUB:.0f} ₽ — {monthly * USD_RUB / 1490 * 100:.1f}% от чека.")
    print("    Цены off-peak; в пик DeepSeek берёт вдвое дороже.")


def cmd_url(args: argparse.Namespace) -> int:
    """Evaluate a single vacancy given its link."""
    resume_text = read_document(Path(args.resume))
    print(f"Читаю вакансию: {args.url}")
    vacancy_text, meta = load_vacancy(args.url)
    if meta:
        print(f"  {meta['title']} — {meta['company']}")
        print(f"  {meta['location'] or 'локация не указана'} · "
              f"{meta['salary'] or meta['predicted_salary'] or 'зарплата не указана'}")

    steps = profiles.get(args.steps).steps
    results, ledger = run_all(resume_text, vacancy_text, steps=steps, model=args.model)
    if "report" in results:
        _report_summary(results["report"])
    _print_ledger(ledger)

    payload = {"url": args.url, "vacancy": meta, "results": results, "usage": ledger.as_dict()}
    print(f"\nПолный отчёт: {_save('url', payload)}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Search Habr Career, then score every vacancy found against the resume."""
    resume_text = read_document(Path(args.resume))

    print(f"Ищу на Хабр Карьере: запрос «{args.query or 'без запроса'}»"
          f"{', удалёнка' if args.remote else ''}"
          f"{f', от {args.salary:,} ₽'.replace(',', ' ') if args.salary else ''}")
    cards = habr.search(
        query=args.query, pages=args.pages, remote=args.remote, salary=args.salary
    )
    if not cards:
        print("Ничего не найдено.")
        return 1
    print(f"Найдено {len(cards)}, оцениваю первые {min(args.limit, len(cards))}.\n")
    cards = cards[: args.limit]

    shared = Ledger()
    profile = parse_resume(LLM(model=args.model, ledger=shared), resume_text)
    print(f"Профиль: {profile.get('target_role')}, {profile.get('years_experience')} лет\n")

    rows: list[dict] = []
    saved: list[dict] = []
    for i, card in enumerate(cards, 1):
        head = f"[{i}/{len(cards)}] {card['title'][:50]} — {card['company'][:24]}"
        try:
            vac = habr.fetch_vacancy(card["url"])
            results, ledger = run_all(
                resume_text, vac.as_text(), steps=("evaluate",),
                model=args.model, profile=profile,
            )
        except Exception as e:
            print(f"{head}\n    ОШИБКА: {type(e).__name__}: {e}")
            rows.append({"title": card["title"], "url": card["url"], "error": str(e)[:150]})
            continue

        report = results["report"]
        print(f"{head}\n    балл {report.get('score')}  {report.get('verdict')}  "
              f"{_fmt_money(ledger.usd)}")
        rows.append({
            "title": card["title"],
            "company": card["company"],
            "url": card["url"],
            "salary": vac.salary or vac.predicted_salary,
            "score": report.get("score"),
            "verdict": report.get("verdict"),
            "usd": round(ledger.usd, 6),
            "rub": round(ledger.rub, 4),
            "seconds": round(ledger.seconds, 1),
        })
        saved.append({"vacancy": vac.as_dict(), "report": report, "usage": ledger.as_dict()})

    _scan_summary(rows, shared, args.min_score)
    payload = {
        "query": args.query,
        "resume": str(args.resume),
        "profile": profile,
        "rows": rows,
        "parse_usage": shared.as_dict(),
        "runs": saved,
    }
    print(f"\nПолный отчёт: {_save('scan', payload)}")
    return 0


def _scan_summary(rows: list[dict], parse_ledger: Ledger, min_score: float | None) -> None:
    ok = [r for r in rows if "error" not in r]
    if not ok:
        print("\nНи одной успешной оценки.")
        return

    ok.sort(key=lambda r: -(r.get("score") or 0))
    print("\n" + "=" * 78)
    print("ЧТО НАШЛОСЬ — отсортировано по баллу")
    print("=" * 78)
    for r in ok:
        if min_score is not None and (r.get("score") or 0) < min_score:
            continue
        mark = {"apply": "+", "apply_with_caveats": "~", "skip": "-"}.get(str(r.get("verdict")), " ")
        print(f"  {mark} {str(r.get('score')):>4}  {r['title'][:46]:<46} {r['company'][:18]}")
        print(f"         {r['url']}")

    total = parse_ledger.usd + sum(r["usd"] for r in ok)
    worth = [r for r in ok if (r.get("score") or 0) >= 4.0]
    maybe = [r for r in ok if 3.0 <= (r.get("score") or 0) < 4.0]
    print(f"\n  Откликаться: {len(worth)}, с оговорками: {len(maybe)}, "
          f"мимо: {len(ok) - len(worth) - len(maybe)}")
    print(f"  Просмотрено {len(ok)} вакансий за {_fmt_money(total)} "
          f"({sum(r['seconds'] for r in ok):.0f} с)")
    if ok:
        print(f"  Стоимость одной: {_fmt_money(total / len(ok))}")


def _confirm_profile(proposal: proposals.Proposal, assume_yes: bool) -> dict:
    """Show what the model wants to change and write nothing without a yes.

    The rule is from career-ops: the agent proposes, the user confirms. Until
    now the generated profile went straight to disk.
    """
    if proposal.is_noop:
        print("Профиль поиска: модель не предложила изменений.")
        return proposal.accept()

    print("\nМодель предлагает изменить профиль поиска:")
    for line in proposal.describe().splitlines():
        print(f"  {line}")

    if assume_yes:
        print("  --yes: принимаю без вопроса.")
        return proposal.accept()

    try:
        answer = input("\nПринять? [y/N]: ").strip().lower()
    except EOFError:  # non-interactive run without --yes
        answer = ""

    if answer in ("y", "yes", "д", "да"):
        return proposal.accept()
    print("Отклонено — профиль остаётся прежним.")
    return proposal.reject()


def _load_or_build_profile(args, resume_text: str, ledger: Ledger) -> tuple[SearchProfile, dict]:
    """Search profile: from file, from the starter template, or generated from the CV."""
    path = Path(args.profile_file)

    if args.starter:
        merged = starter_profile().as_dict()
        print("Профиль поиска: готовый шаблон (IT).")
    elif path.exists() and not args.regenerate:
        merged = starter_profile().as_dict()
        merged.update(json.loads(path.read_text(encoding="utf-8")))
        print(f"Профиль поиска: из {path}")
    else:
        profile = parse_resume(LLM(model=args.model, ledger=ledger), resume_text)
        answers = {
            "salary_min": args.salary,
            "format": "удалённо" if args.remote else None,
        }
        raw = build_search_profile(LLM(model=args.model, ledger=ledger), profile, answers)
        # Start from the shipped template, then let the CV-derived lists replace it.
        base = starter_profile().as_dict()
        current = dict(base)
        if path.exists():
            current.update(json.loads(path.read_text(encoding="utf-8")))

        wanted = {k: raw[k] for k in
                  ("queries", "positive", "negative", "divisions_negative")
                  if raw.get(k)}
        if raw.get("min_qualification"):
            wanted["min_qualification"] = raw["min_qualification"]

        proposal = proposals.Proposal(
            kind="search_profile", current=current, proposed=wanted,
            rationale=(raw.get("rationale") or "")[:200],
        )
        proposals.save(proposal, path.parent / "search-profile.proposal.json")
        merged = _confirm_profile(proposal, assume_yes=args.yes)

    # CLI flags always win over whatever the file or template said.
    if args.remote:
        merged["remote_only"] = True
    if args.salary is not None:
        merged["salary_min"] = args.salary

    # Derive the API qualification list from the minimum grade, so the free
    # server-side filter matches the local one.
    order = ["intern", "junior", "middle", "senior"]
    low = (merged.get("min_qualification") or "").lower()
    merged["qualifications"] = order[order.index(low):] if low in order else []

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return SearchProfile.from_dict(merged), merged


def cmd_scan2(args: argparse.Namespace) -> int:
    """career-ops-style scan: collect free, filter free, pay only for survivors."""
    resume_text = read_document(Path(args.resume))
    setup = Ledger()
    prof, _ = _load_or_build_profile(args, resume_text, setup)

    print(f"\nЗапросы: {', '.join(prof.queries)}")
    print(f"Фильтры: грейд от {prof.min_qualification or 'любого'}"
          f"{', только удалёнка' if prof.remote_only else ''}"
          f"{f', от {prof.salary_min} ₽' if prof.salary_min else ''}")
    print(f"Стоп-слов: {len(prof.negative)}, ключевых: {len(prof.positive)}\n")

    # 1. Collect — free.
    posts = habr_api.search_many(
        prof.queries, pages=args.pages, remote=prof.remote_only,
        qualifications=prof.qualifications, salary_from=prof.salary_min,
    )
    print(f"Собрано с Хабра: {len(posts)} (без повторов по id)")

    # 2. Pre-screen — free.
    kept, dropped = PreScreen(prof).run(posts)
    print(f"После фильтров: {len(kept)}, отсеяно бесплатно: {len(dropped)}")

    if args.show_dropped and dropped:
        print("\n  Отсеяно:")
        reasons: dict[str, int] = {}
        for _p, why in dropped:
            key = why.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        for why, n in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {n:>3}  {why}")

    # 3. Skip what a previous scan already showed.
    if not args.no_history:
        kept, seen = history.split_seen(kept)
        if seen:
            print(f"Уже показывали раньше: {len(seen)} — пропускаю")

    if not kept:
        print("\nНовых подходящих вакансий нет.")
        return 0

    to_score = kept[: args.limit]
    print(f"\nОцениваю моделью: {len(to_score)} из {len(kept)}\n")

    # 4. Only now does the model get involved.
    profile = parse_resume(LLM(model=args.model, ledger=setup), resume_text)
    rows: list[dict] = []
    saved: list[dict] = []
    for i, p in enumerate(to_score, 1):
        head = f"[{i}/{len(to_score)}] {p.title[:48]} — {p.company[:22]}"
        try:
            vac = habr.fetch_vacancy(p.url)
            results, ledger = run_all(
                resume_text, vac.as_text(), steps=("evaluate",),
                model=args.model, profile=profile,
            )
        except Exception as e:
            print(f"{head}\n    ОШИБКА: {type(e).__name__}: {e}")
            continue
        rep = results["report"]
        print(f"{head}\n    балл {rep.get('score')}  {rep.get('verdict')}  {_fmt_money(ledger.usd)}")
        rows.append({
            "id": p.id, "url": p.url, "title": p.title, "company": p.company,
            "qualification": p.qualification, "salary": p.salary_text,
            "score": rep.get("score"), "verdict": rep.get("verdict"),
            "usd": round(ledger.usd, 6), "rub": round(ledger.rub, 4),
            "seconds": round(ledger.seconds, 1),
        })
        saved.append({"posting": p.as_dict(), "report": rep, "usage": ledger.as_dict()})

    _scan2_summary(rows, setup, len(posts), len(dropped))

    if not args.no_history:
        written = history.append([
            {"id": r["id"], "url": r["url"], "title": r["title"],
             "company": r["company"], "status": "evaluated", "score": str(r["score"])}
            for r in rows
        ])
        if written:
            print(f"  Записано в историю: {written}")

    payload = {
        "profile": prof.as_dict(), "collected": len(posts), "filtered_out": len(dropped),
        "rows": rows, "setup_usage": setup.as_dict(), "runs": saved,
    }
    print(f"\nПолный отчёт: {_save('scan2', payload)}")
    return 0


def _scan2_summary(rows: list[dict], setup: Ledger, collected: int, dropped: int) -> None:
    if not rows:
        print("\nНи одной оценки.")
        return
    rows = sorted(rows, key=lambda r: -(r.get("score") or 0))
    print("\n" + "=" * 78)
    print("РЕЗУЛЬТАТ")
    print("=" * 78)
    for r in rows:
        mark = {"apply": "+", "apply_with_caveats": "~", "skip": "-"}.get(str(r["verdict"]), " ")
        print(f"  {mark} {str(r['score']):>4}  {r['title'][:44]:<44} {r['company'][:16]}")
        print(f"         {r['url']}")

    scored = setup.usd + sum(r["usd"] for r in rows)
    worth = [r for r in rows if (r.get("score") or 0) >= 4.0]
    print(f"\n  Откликаться: {len(worth)} из {len(rows)} оценённых")
    print(f"  Воронка: {collected} собрано → {dropped} отсеяно бесплатно → {len(rows)} оценено")
    print(f"  Потрачено: {_fmt_money(scored)}")
    if dropped:
        saved_money = dropped * (sum(r["usd"] for r in rows) / max(len(rows), 1))
        print(f"  Фильтры сэкономили примерно {_fmt_money(saved_money)}")


def cmd_cost(args: argparse.Namespace) -> int:
    files = sorted(OUT.glob("*.json"))
    if not files:
        print("В out/ пусто — сначала сделайте прогон.")
        return 1
    total_usd = 0.0
    calls = 0
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for key in ("usage", "parse_usage"):
            if key in data:
                total_usd += data[key].get("total_usd", 0)
                calls += len(data[key].get("calls", []))
        for run in data.get("runs", []):
            total_usd += run.get("usage", {}).get("total_usd", 0)
            calls += len(run.get("usage", {}).get("calls", []))
    print(f"Файлов прогона: {len(files)}, вызовов LLM: {calls}")
    print(f"Потрачено всего: {_fmt_money(total_usd)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="probe.run", description="Пробник career-agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("one", help="одно резюме + одна вакансия")
    one.add_argument("--resume", required=True)
    one.add_argument("--vacancy", required=True)
    one.add_argument("--steps", choices=profiles.names(), default="all",
                     help="режим прогона: " + ", ".join(profiles.names()))
    one.add_argument("--model", default=None)
    one.set_defaults(func=cmd_one)

    batch = sub.add_parser("batch", help="одно резюме против папки вакансий")
    batch.add_argument("--resume", required=True)
    batch.add_argument("--vacancies", default="data/vacancies")
    batch.add_argument("--steps", choices=profiles.names(), default="evaluate",
                       help="режим прогона: " + ", ".join(profiles.names()))
    batch.add_argument("--model", default=None)
    batch.set_defaults(func=cmd_batch)

    scan = sub.add_parser("scan", help="найти вакансии на Хабр Карьере и оценить каждую")
    scan.add_argument("--resume", required=True)
    scan.add_argument("--query", default="", help="поисковый запрос, например python")
    scan.add_argument("--pages", type=int, default=1, help="страниц выдачи (25 вакансий на странице)")
    scan.add_argument("--remote", action="store_true", help="только удалёнка")
    scan.add_argument("--salary", type=int, default=None, help="зарплата от, ₽")
    scan.add_argument("--limit", type=int, default=10, help="сколько вакансий оценить")
    scan.add_argument("--min-score", type=float, default=None,
                      help="показать подробности только для вакансий с баллом не ниже")
    scan.add_argument("--model", default=None)
    scan.set_defaults(func=cmd_scan)

    url = sub.add_parser("url", help="оценить одну вакансию по ссылке")
    url.add_argument("--resume", required=True)
    url.add_argument("--url", required=True)
    url.add_argument("--steps", choices=profiles.names(), default="evaluate",
                     help="режим прогона: " + ", ".join(profiles.names()))
    url.add_argument("--model", default=None)
    url.set_defaults(func=cmd_url)

    sc = sub.add_parser("scan2", help="сканер как в career-ops: фильтры до модели")
    sc.add_argument("--resume", required=True)
    sc.add_argument("--pages", type=int, default=2, help="страниц на запрос (25 вакансий/стр.)")
    sc.add_argument("--limit", type=int, default=8, help="сколько выживших оценить моделью")
    sc.add_argument("--remote", action="store_true")
    sc.add_argument("--salary", type=int, default=None, help="зарплата от, ₽")
    sc.add_argument("--starter", action="store_true",
                    help="взять готовый шаблон вместо генерации профиля по резюме")
    sc.add_argument("--profile-file", default="out/search-profile.json",
                    help="куда сохранить/откуда взять профиль поиска")
    sc.add_argument("--regenerate", action="store_true", help="перегенерировать профиль поиска")
    sc.add_argument("--yes", action="store_true",
                    help="принимать предложенный профиль поиска без вопроса")
    sc.add_argument("--show-dropped", action="store_true", help="показать отсеянное")
    sc.add_argument("--no-history", action="store_true", help="не пропускать уже виденные")
    sc.add_argument("--model", default=None)
    sc.set_defaults(func=cmd_scan2)

    cost = sub.add_parser("cost", help="сводка расхода по всем прогонам")
    cost.set_defaults(func=cmd_cost)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
