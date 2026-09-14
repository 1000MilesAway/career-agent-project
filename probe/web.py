"""One-screen web probe: paste a vacancy, see the report and what it cost.

    .venv/bin/python -m probe.web
    open http://127.0.0.1:8010

Deliberately stateless and single-user: this is a probe, not the product.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from . import history, profiles, proposals
from .defaults import starter_profile
from .filters import PreScreen, SearchProfile
from .llm import LLM, USD_RUB, Ledger
from .pipeline import build_search_profile, load_vacancy, parse_resume, read_document, run_all
from .sources import habr, habr_api

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="career-agent probe")

# Cached resume profile, so pasting a second vacancy does not re-parse the CV.
STATE: dict = {
    "profile": None, "resume_text": None, "resume_name": None, "search_profile": None,
    # A generated search profile waits here until the user accepts it.
    "pending_profile": None,
}

INDEX = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX


@app.get("/api/state")
async def state() -> JSONResponse:
    return JSONResponse({
        "has_resume": STATE["profile"] is not None,
        "resume_name": STATE["resume_name"],
        "profile": STATE["profile"],
        "run_profiles": profiles.as_list(),
    })


@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)) -> JSONResponse:
    tmp = ROOT / "out" / f"_upload{Path(file.filename).suffix}"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(await file.read())
    try:
        text = read_document(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    ledger = Ledger()
    profile = parse_resume(LLM(ledger=ledger), text)
    STATE.update(profile=profile, resume_text=text, resume_name=file.filename)
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
    results, ledger = run_all(
        STATE["resume_text"], vacancy_text, steps=run_profile.steps, profile=STATE["profile"]
    )
    return JSONResponse({
        "results": results, "vacancy": meta, "run_profile": run_profile.as_dict(),
        "usage": ledger.as_dict(), "usd_rub": USD_RUB,
    })


@app.post("/api/search")
async def search_vacancies(
    query: str = Form(""), remote: str = Form(""), salary: str = Form(""),
) -> JSONResponse:
    """Habr search only — no scoring, so the page can show results instantly."""
    try:
        rows = await run_in_threadpool(
            habr.search, query, 1, bool(remote), int(salary) if salary.strip() else None
        )
    except Exception as e:
        return JSONResponse({"error": f"Поиск не удался: {e}"}, status_code=400)
    return JSONResponse({"rows": rows})


@app.post("/api/scan")
async def scan(
    remote: str = Form(""), salary: str = Form(""), pages: str = Form("1"),
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
        order = ["intern", "junior", "middle", "senior"]
        low = (merged.get("min_qualification") or "").lower()
        merged["qualifications"] = order[order.index(low):] if low in order else []
        merged["rationale"] = raw.get("rationale", "")
        sp = merged

    sp = {**sp, "remote_only": bool(remote),
          "salary_min": int(salary) if salary.strip() else None}
    prof = SearchProfile.from_dict(sp)

    posts = await run_in_threadpool(
        habr_api.search_many, prof.queries, int(pages or 1), prof.remote_only,
        prof.qualifications, prof.salary_min,
    )
    kept, dropped = PreScreen(prof).run(posts)
    fresh, seen = history.split_seen(kept)

    reasons: dict[str, int] = {}
    for _p, why in dropped:
        key = why.split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1

    pending = STATE.get("pending_profile")
    return JSONResponse({
        "profile": sp,
        "collected": len(posts),
        "dropped": len(dropped),
        "drop_reasons": reasons,
        "seen_before": len(seen),
        "rows": [p.as_dict() for p in fresh],
        "pending_profile": pending.as_dict() if pending else None,
    })


@app.post("/api/profile_decision")
async def profile_decision(accept: str = Form(...)) -> JSONResponse:
    """Accept or reject the profile the model proposed. Nothing is stored until this."""
    proposal = STATE.get("pending_profile")
    if proposal is None:
        return JSONResponse({"error": "Нечего подтверждать"}, status_code=400)

    if accept.strip().lower() in ("1", "true", "yes", "y"):
        merged = proposal.accept()
        order = ["intern", "junior", "middle", "senior"]
        low = (merged.get("min_qualification") or "").lower()
        merged["qualifications"] = order[order.index(low):] if low in order else []
        STATE["search_profile"] = merged
    else:
        proposal.reject()

    STATE["pending_profile"] = None
    return JSONResponse({
        "status": proposal.status,
        "search_profile": STATE.get("search_profile"),
    })


@app.post("/api/score_one")
async def score_one(url: str = Form(...)) -> JSONResponse:
    """Score a single vacancy from the search list."""
    if not STATE["profile"]:
        return JSONResponse({"error": "Сначала загрузите резюме"}, status_code=400)
    try:
        vacancy_text, meta = await run_in_threadpool(load_vacancy, url)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    results, ledger = run_all(
        STATE["resume_text"], vacancy_text, steps=("evaluate",), profile=STATE["profile"]
    )
    return JSONResponse({
        "url": url, "vacancy": meta, "report": results["report"],
        "usage": ledger.as_dict(), "usd_rub": USD_RUB,
    })


def main() -> None:
    import uvicorn

    port = int(os.getenv("PROBE_PORT", "8010"))
    print(f"Пробник: http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
