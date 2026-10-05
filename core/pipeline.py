"""The five pipeline steps, plus file reading. No web, no DB — just the idea."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from . import prompts
from .llm import LLM, Ledger
from .sources import geekjob, hh
from .sources.habr import fetch_vacancy, is_habr_url


def _with_links(text: str, uris: list[str]) -> str:
    """Append the link targets the text layer does not show.

    A CV writes "@handle" or "GitHub" and puts the address in the link; the
    extracted text keeps only the visible words, and the contact is lost —
    to the model and to `core.contacts` alike. Listed once each, in order.
    """
    seen: list[str] = []
    for uri in uris:
        uri = (uri or "").strip()
        if uri.lower().startswith(("http://", "https://", "mailto:", "tel:")) and uri not in seen:
            seen.append(uri)
    if not seen:
        return text
    return text + "\n\nСсылки:\n" + "\n".join(seen)


def read_document(path: Path) -> str:
    """PDF/DOCX/DOC/RTF/TXT -> plain text."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            text = "\n".join(page.get_text() for page in doc).strip()
            uris = [link.get("uri", "") for page in doc for link in page.get_links()]
        return _with_links(text, uris)
    if suffix == ".docx":
        import docx
        from docx.opc.constants import RELATIONSHIP_TYPE as RT

        document = docx.Document(str(path))
        text = "\n".join(p.text for p in document.paragraphs).strip()
        uris = [
            rel.target_ref
            for rel in document.part.rels.values()
            if rel.reltype == RT.HYPERLINK and rel.is_external
        ]
        return _with_links(text, uris)
    if suffix == ".doc":
        from .msdoc import read_doc

        return read_doc(path)
    if suffix == ".rtf":
        from .rtf import rtf_to_text

        return rtf_to_text(path.read_bytes())
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8").strip()
    raise ValueError(f"неподдерживаемый формат: {path.suffix}")


def image_message(path: Path, instruction: str) -> list[dict]:
    """Vacancy as a screenshot: hand the image to the vision model."""
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    return [
        {"type": "text", "text": instruction},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]


def load_vacancy(source: str) -> tuple[str, dict | None]:
    """Accept a URL or raw text. Returns (text for the model, source metadata)."""
    src = source.strip()
    if src.startswith(("http://", "https://")):
        if is_habr_url(src):
            vac = fetch_vacancy(src)
            return vac.as_text(), vac.as_dict()
        if geekjob.is_geekjob_url(src):
            posting = geekjob.fetch_vacancy(src)
            return posting.as_text(), {"url": src, "title": posting.title}
        if hh.is_hh_url(src):
            return hh.fetch_vacancy(src)
        raise ValueError(
            "Пока поддерживаются только ссылки на career.habr.com, hh.ru и geekjob.ru. "
            "Для других сайтов вставьте текст вакансии."
        )
    return src, None


def build_search_profile(llm: LLM, profile: dict, answers: dict | None = None) -> dict:
    """Propose search queries and stop-words from the CV. The user confirms them."""
    answers = answers or {}
    return llm.complete_json(
        step="search_profile",
        system=prompts.SEARCH_PROFILE,
        user=(
            f"Профиль кандидата:\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
            f"Ответы о желаемой работе:\n{json.dumps(answers, ensure_ascii=False, indent=2)}"
        ),
        temperature=0.2,
    )


def parse_resume(llm: LLM, resume_text: str) -> dict:
    return llm.complete_json(
        step="parse_resume",
        system=prompts.PARSE_RESUME,
        user=f"Резюме:\n\n{resume_text}",
        temperature=0.0,
    )


def evaluate(llm: LLM, profile: dict, vacancy_text: str) -> dict:
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)
    return llm.complete_json(
        step="evaluate",
        system=prompts.EVALUATE,
        user=f"Профиль кандидата:\n{profile_json}\n\nВакансия:\n{vacancy_text}",
        temperature=0.1,
        # The step most often run and the slowest: with reasoning it took ~50 s
        # and ~0.9 ₽ against 13 s and 0.17 ₽ measured before thinking was on.
        thinking=False,
    )


def tailor_resume(llm: LLM, profile: dict, report: dict, vacancy_text: str) -> dict:
    return llm.complete_json(
        step="tailor_resume",
        system=prompts.TAILOR_RESUME,
        user=(
            f"Профиль:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"Оценка:\n{json.dumps(report, ensure_ascii=False)}\n\n"
            f"Вакансия:\n{vacancy_text}"
        ),
        temperature=0.3,
    )


def cover_letter(llm: LLM, profile: dict, report: dict, vacancy_text: str) -> dict:
    return llm.complete_json(
        step="cover_letter",
        system=prompts.COVER_LETTER,
        user=(
            f"Профиль:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"Оценка:\n{json.dumps(report, ensure_ascii=False)}\n\n"
            f"Вакансия:\n{vacancy_text}"
        ),
        temperature=0.6,
    )


def interview_prep(llm: LLM, profile: dict, report: dict, vacancy_text: str) -> dict:
    return llm.complete_json(
        step="interview_prep",
        system=prompts.INTERVIEW_PREP,
        user=(
            f"Профиль:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"Оценка:\n{json.dumps(report, ensure_ascii=False)}\n\n"
            f"Вакансия:\n{vacancy_text}"
        ),
        temperature=0.3,
    )


def run_all(
    resume_text: str,
    vacancy_text: str,
    steps: tuple[str, ...] = ("evaluate", "tailor", "letter", "interview"),
    model: str | None = None,
    profile: dict | None = None,
) -> tuple[dict, Ledger]:
    """Full flow for one resume + one vacancy. Returns results and the cost ledger."""
    ledger = Ledger()
    llm = LLM(model=model, ledger=ledger)

    out: dict = {}
    if profile is None:
        profile = parse_resume(llm, resume_text)
    out["profile"] = profile

    if "evaluate" in steps:
        report = evaluate(llm, profile, vacancy_text)
        out["report"] = report
    else:
        return out, ledger

    if "tailor" in steps:
        out["tailored_resume"] = tailor_resume(llm, profile, report, vacancy_text)
    if "letter" in steps:
        out["cover_letter"] = cover_letter(llm, profile, report, vacancy_text)
    if "interview" in steps:
        out["interview_prep"] = interview_prep(llm, profile, report, vacancy_text)

    return out, ledger
