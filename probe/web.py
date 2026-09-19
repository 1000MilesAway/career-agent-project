"""One-screen web probe: scan Habr, see the report and what it cost.

    .venv/bin/python -m probe.web
    open http://127.0.0.1:8010

Single-user by design: this is a probe, not the product. What it does keep is
the resume and the found vacancies, on disk — reopening the page must not ask
the user to pay for the same parsing and scoring twice.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from core import profiles, proposals, tg_scan, triage
from core.defaults import starter_profile
from core.filters import PreScreen, SearchProfile
from core.llm import LLM, USD_RUB, Ledger
from core.pipeline import (
    build_search_profile,
    cover_letter,
    interview_prep,
    load_vacancy,
    parse_resume,
    read_document,
    run_all,
    tailor_resume,
)
from core.sources import habr_api, telegram

from . import found, history, session

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="career-agent probe")

# Cached resume profile, so pasting a second vacancy does not re-parse the CV.
# Seeded from disk at import, so a server restart keeps the resume.
STATE: dict = {
    "profile": None, "resume_text": None, "resume_name": None, "search_profile": None,
    # A generated search profile waits here until the user accepts it.
    "pending_profile": None,
    # Target roles, derived from the CV once and reused by every triage run.
    "archetypes": None,
}

_stored = session.load()
if _stored:
    STATE.update(
        profile=_stored.get("profile"),
        resume_text=_stored.get("resume_text"),
        resume_name=_stored.get("resume_name"),
        search_profile=_stored.get("search_profile"),
        archetypes=_stored.get("archetypes"),
    )

INDEX_PATH = Path(__file__).parent / "index.html"

# The apply steps, by the name run profiles use for them. Each takes the same
# four arguments and each is bought separately, so a user who wants only the
# letter is not charged for the interview prep.
APPLY_STEPS = {
    "tailor": tailor_resume,
    "letter": cover_letter,
    "interview": interview_prep,
}
STEP_ARTIFACT = {
    "tailor": "tailored_resume",
    "letter": "cover_letter",
    "interview": "interview_prep",
}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Read the page per request: editing index.html must not need a restart.

    Cheap enough (one small file) and it removes a whole class of "I changed it
    but the browser shows the old thing". Python changes still need a restart —
    see --reload in the README.
    """
    return HTMLResponse(
        INDEX_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/state")
async def state() -> JSONResponse:
    """Everything the page needs to redraw itself after a reload."""
    return JSONResponse({
        "has_resume": STATE["profile"] is not None,
        "resume_name": STATE["resume_name"],
        "profile": STATE["profile"],
        "run_profiles": profiles.as_list(),
        "apply_steps": profiles.steps_as_list(),
        "statuses": found.STATUSES,
        "found": found.load(),
        "spend": found.spend(),
    })


@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)) -> JSONResponse:
    suffix = Path(file.filename).suffix
    tmp = ROOT / "out" / f"_upload-{os.getpid()}{suffix}"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(await file.read())
    try:
        text = read_document(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    ledger = Ledger()
    profile = await run_in_threadpool(parse_resume, LLM(ledger=ledger), text)
    STATE.update(profile=profile, resume_text=text, resume_name=file.filename)
    session.save(profile, text, file.filename)
    return JSONResponse({"profile": profile, "usage": ledger.as_dict()})


@app.post("/api/evaluate")
async def evaluate_vacancy(vacancy: str = Form(...), steps: str = Form("evaluate")) -> JSONResponse:
    if not STATE["profile"]:
        return JSONResponse({"error": "Сначала загрузите резюме"}, status_code=400)

    # The field takes either a Habr link or pasted text.
    try:
        vacancy_text, meta = await run_in_threadpool(load_vacancy, vacancy)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Не удалось прочитать вакансию: {e}"}, status_code=400)

    run_profile = profiles.get(steps)
    results, ledger = await run_in_threadpool(
        run_all, STATE["resume_text"], vacancy_text, run_profile.steps, None, STATE["profile"]
    )
    return JSONResponse({
        "results": results, "vacancy": meta, "run_profile": run_profile.as_dict(),
        "usage": ledger.as_dict(), "usd_rub": USD_RUB,
    })


@app.post("/api/scan")
async def scan(
    remote: str = Form(""), salary: str = Form(""), pages: str = Form("5"),
    any_salary: str = Form(""),
) -> JSONResponse:
    """Collect + pre-screen only. Free: no model is called for the listing."""
    if not STATE["profile"]:
        return JSONResponse({"error": "Сначала загрузите резюме"}, status_code=400)

    sp = STATE.get("search_profile")
    if sp is None:
        raw = await run_in_threadpool(
            build_search_profile, LLM(ledger=Ledger()), STATE["profile"],
            {"format": "удалённо" if remote else None},
        )
        base = starter_profile().as_dict()
        wanted = {k: raw[k] for k in
                  ("queries", "positive", "negative", "divisions_negative") if raw.get(k)}
        if raw.get("min_qualification"):
            wanted["min_qualification"] = raw["min_qualification"]

        # The agent proposes; the scan runs on the starter template until the
        # user accepts. Nothing generated is stored without that yes.
        proposal = proposals.Proposal(
            kind="search_profile", current=base, proposed=wanted,
            rationale=raw.get("rationale", ""),
        )
        STATE["pending_profile"] = proposal
        merged = base
        merged["qualifications"] = _grades_from(merged.get("min_qualification"))
        merged["rationale"] = raw.get("rationale", "")
        sp = merged

    # Habr's `salary` parameter drops every posting without a stated range, so a
    # threshold silently hides vacancies that just don't publish numbers.
    # "any_salary" keeps those in: we ask the API for everything and let the
    # free filters decide.
    wants_any = bool(any_salary)
    sp = {**sp, "remote_only": bool(remote),
          "salary_min": None if wants_any else (int(salary) if salary.strip() else None)}
    prof = SearchProfile.from_dict(sp)

    posts = await run_in_threadpool(
        habr_api.search_many, prof.queries, int(pages or 5), prof.remote_only,
        prof.qualifications, prof.salary_min,
    )
    kept, dropped = PreScreen(prof).run(posts)
    fresh, seen = history.split_seen(kept)

    reasons: dict[str, int] = {}
    for _p, why in dropped:
        key = why.split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1

    # Remember what survived, so a reload still shows it. Scores already paid
    # for are kept by the merge.
    rows = found.merge([p.as_dict() for p in fresh])
    history.append([
        {"id": p.id, "url": p.url, "title": p.title, "company": p.company, "status": "found"}
        for p in fresh
    ])

    pending = STATE.get("pending_profile")
    return JSONResponse({
        "profile": sp,
        "collected": len(posts),
        "dropped": len(dropped),
        "drop_reasons": reasons,
        "seen_before": len(seen),
        "new_count": len(fresh),
        "rows": rows,
        "pending_profile": pending.as_dict() if pending else None,
    })


@app.post("/api/scan_tg")
async def scan_telegram(pages: str = Form("1"), groups: str = Form("core")) -> JSONResponse:
    """The second source. Collect + pre-screen only — no model is called.

    Telegram states no fields, so everything the free filters can act on comes
    from the post text itself. The survivors join the same found list as Habr's,
    because from here on the pipeline does not care where a vacancy came from.
    """
    if not STATE["profile"]:
        return JSONResponse({"error": "Сначала загрузите резюме"}, status_code=400)

    result = await run_in_threadpool(
        tg_scan.collect,
        None, tuple(g.strip() for g in groups.split(",") if g.strip()),
        int(pages or 1), telegram.MAX_SILENCE_DAYS, False, STATE.get("search_profile"),
    )

    rows_new = tg_scan.as_rows(result["kept"])
    fresh, seen = history.split_seen_rows(rows_new)
    rows = found.merge(fresh)
    history.append([
        {"id": r["id"], "url": r["url"], "title": r["title"],
         "company": r["company"], "status": "found", "source": "telegram"}
        for r in fresh
    ])

    return JSONResponse({
        "channels": result["channels"],
        "collected": result["collected"],
        "problems": result["problems"],
        "filtered": result.get("filtered", False),
        "dropped": len(result["dropped"]),
        "drop_reasons": tg_scan.drop_reasons(result["dropped"]),
        "seen_before": len(seen),
        "new_count": len(fresh),
        "rows": rows,
    })


@app.post("/api/triage")
async def triage_found(only_new: str = Form("1")) -> JSONResponse:
    """Second screen: one cheap call sorts the whole found list.

    Between the free filters and the 0.17 RUB-per-vacancy evaluation. Judges
    listing data only — no page is fetched — and marks rather than deletes, so
    a wrong verdict stays visible instead of silently hiding a job.
    """
    if not STATE["profile"]:
        return JSONResponse({"error": "Сначала загрузите резюме"}, status_code=400)

    rows = found.load()
    # "0" is a non-empty string and therefore truthy — a bare `if only_new`
    # would make "judge everything again" impossible to ask for.
    fresh_only = only_new.strip().lower() not in ("0", "false", "no", "")
    pending = [r for r in rows if not r.get("triage")] if fresh_only else rows
    if not pending:
        return JSONResponse({
            "rows": rows, "counts": {}, "judged": 0,
            "note": "Все вакансии уже прошли отсев.",
        })

    ledger = Ledger()
    llm = LLM(ledger=ledger)

    archetypes = STATE.get("archetypes")
    if not archetypes:
        archetypes = await run_in_threadpool(triage.build_archetypes, llm, STATE["profile"])
        STATE["archetypes"] = archetypes
        session.save_archetypes(archetypes)

    result = await run_in_threadpool(triage.run_triage, llm, archetypes, pending)
    rows = triage.apply_verdicts(rows, result)
    found.save(rows)

    return JSONResponse({
        "rows": rows,
        "counts": result.counts(),
        "judged": len(result.verdicts),
        "missing": result.missing,
        "archetypes": archetypes,
        "usage": ledger.as_dict(),
        "usd_rub": USD_RUB,
    })


@app.post("/api/artifacts")
async def artifacts(url: str = Form(...), vacancy_id: str = Form(""),
                    step: str = Form(...)) -> JSONResponse:
    """One apply document: the cover letter, the tailored resume or the prep.

    Bought one at a time, from its own button in the row. A bundle would charge
    for documents the user did not ask for, and the report every one of them
    reads is already paid for — so it is never re-bought here.
    """
    if not STATE["profile"]:
        return JSONResponse({"error": "Сначала загрузите резюме"}, status_code=400)

    step = (step or "").strip().lower()
    if step not in APPLY_STEPS:
        return JSONResponse({"error": f"неизвестный шаг: {step}"}, status_code=400)

    stored = found.find(vacancy_id) if vacancy_id else None
    if not stored or not stored.get("report"):
        return JSONResponse(
            {"error": "Сначала оцените вакансию — письмо и резюме пишутся по отчёту"},
            status_code=400,
        )

    key = STEP_ARTIFACT[step]
    have = stored.get("artifacts") or {}
    if have.get(key):
        return JSONResponse({
            "step": step, "artifacts": have, "usage": None,
            "cached": True, "usd_rub": USD_RUB,
        })

    # Same rule as scoring: a Telegram post carries its own text.
    if stored.get("text"):
        vacancy_text = stored["text"]
    else:
        try:
            vacancy_text, _meta = await run_in_threadpool(load_vacancy, url)
        except Exception as e:
            return JSONResponse({"error": f"Не удалось прочитать вакансию: {e}"}, status_code=400)

    ledger = Ledger()
    llm = LLM(ledger=ledger)
    produced = {key: await run_in_threadpool(
        APPLY_STEPS[step], llm, STATE["profile"], stored["report"], vacancy_text
    )}

    usage = ledger.as_dict()
    rows = found.set_artifacts(vacancy_id, produced, usage)
    merged = (found.find(vacancy_id) or {}).get("artifacts", produced)
    return JSONResponse({
        "step": step, "artifacts": merged, "usage": usage,
        "cached": False, "rows": rows, "usd_rub": USD_RUB,
    })


@app.post("/api/save")
async def save_vacancy(vacancy_id: str = Form(...), saved: str = Form("1")) -> JSONResponse:
    """Put a vacancy on the shortlist, or take it off. The user's call alone."""
    on = saved.strip().lower() not in ("0", "false", "no", "")
    rows = found.set_saved(vacancy_id, on)
    return JSONResponse({"rows": rows, "saved": found.saved()})


@app.get("/api/saved")
async def saved_list() -> JSONResponse:
    """The shortlist page: what the user decided to act on."""
    return JSONResponse({
        "rows": found.saved(),
        "statuses": found.STATUSES,
        "apply_steps": profiles.steps_as_list(),
    })


@app.get("/api/profile")
async def profile_page() -> JSONResponse:
    """The resume page: the parsed profile and the search lists behind it."""
    return JSONResponse({
        "has_resume": STATE["profile"] is not None,
        "resume_name": STATE["resume_name"],
        "profile": STATE["profile"],
        # Shown so the user can see what the scan actually searches for, rather
        # than having to trust an invisible list.
        "search_profile": STATE.get("search_profile") or starter_profile().as_dict(),
        "search_profile_accepted": STATE.get("search_profile") is not None,
        "archetypes": STATE.get("archetypes"),
    })


@app.post("/api/status")
async def set_status(vacancy_id: str = Form(...), status: str = Form(...),
                     note: str = Form("")) -> JSONResponse:
    """Move a vacancy along the tracker. The user decides; nothing moves it alone."""
    try:
        rows = found.set_status(vacancy_id, status, note)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"rows": rows, "statuses": found.STATUSES})


@app.post("/api/forget")
async def forget() -> JSONResponse:
    """Drop the found list. Only the user clears it — no scan does.

    History goes with it. Keeping it would make the next scan call every
    survivor "already shown" and drop it into an empty list — the vacancies
    become permanently invisible, which is exactly what the button looks like
    it should undo.
    """
    keep = [r["id"] for r in found.saved()]
    found.clear()
    history.clear(keep_ids=keep)
    return JSONResponse({"rows": found.load(), "kept": len(keep)})


def _grades_from(minimum: str | None) -> list[str]:
    """Grades at or above the minimum, for the free server-side filter."""
    order = ["intern", "junior", "middle", "senior"]
    low = (minimum or "").lower()
    return order[order.index(low):] if low in order else []


@app.post("/api/profile_decision")
async def profile_decision(accept: str = Form(...)) -> JSONResponse:
    """Accept or reject the profile the model proposed. Nothing is stored until this."""
    proposal = STATE.get("pending_profile")
    if proposal is None:
        return JSONResponse({"error": "Нечего подтверждать"}, status_code=400)

    if accept.strip().lower() in ("1", "true", "yes", "y"):
        merged = proposal.accept()
        merged["qualifications"] = _grades_from(merged.get("min_qualification"))
        STATE["search_profile"] = merged
        session.save_search_profile(merged)
    else:
        proposal.reject()

    STATE["pending_profile"] = None
    return JSONResponse({
        "status": proposal.status,
        "search_profile": STATE.get("search_profile"),
    })


@app.post("/api/score_one")
async def score_one(url: str = Form(...), vacancy_id: str = Form("")) -> JSONResponse:
    """Score a single vacancy from the found list."""
    if not STATE["profile"]:
        return JSONResponse({"error": "Сначала загрузите резюме"}, status_code=400)

    stored = found.find(vacancy_id) if vacancy_id else None

    # Already paid for: hand back the stored report instead of buying it again.
    if stored and stored.get("report"):
        return JSONResponse({
            "url": url, "vacancy": None, "report": stored["report"],
            "usage": None, "cached": True, "usd_rub": USD_RUB,
        })

    # A Telegram post IS the vacancy: the text was collected at scan time and
    # t.me has no page to parse. Fetching the link would fail and, worse, would
    # make the second source unusable from the screen.
    if stored and stored.get("text"):
        vacancy_text, meta = stored["text"], None
    else:
        try:
            vacancy_text, meta = await run_in_threadpool(load_vacancy, url)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
    results, ledger = await run_in_threadpool(
        run_all, STATE["resume_text"], vacancy_text, ("evaluate",), None, STATE["profile"]
    )
    usage = ledger.as_dict()
    if vacancy_id:
        found.set_score(vacancy_id, results["report"], usage)
    return JSONResponse({
        "url": url, "vacancy": meta, "report": results["report"],
        "usage": usage, "cached": False, "usd_rub": USD_RUB,
    })


def main() -> None:
    import uvicorn

    port = int(os.getenv("PROBE_PORT", "8010"))
    # PROBE_RELOAD=1 restarts the server on any .py change. Off by default so a
    # demo run is not restarted mid-scan; on while editing.
    reload = os.getenv("PROBE_RELOAD", "").strip() not in ("", "0", "false")
    print(f"Пробник: http://127.0.0.1:{port}" + (" (авторестарт)" if reload else ""))
    uvicorn.run(
        "probe.web:app" if reload else app,
        host="127.0.0.1", port=port, log_level="warning",
        reload=reload, reload_dirs=[str(Path(__file__).parent)],
    )


if __name__ == "__main__":
    main()
