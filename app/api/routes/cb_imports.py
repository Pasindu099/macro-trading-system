"""/admin/cb-imports: manual imports for blocked / PDF-only central-bank sources (Step 12 decision)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_role
from app.db.models import User
from app.services import cb_manual_imports as imports
from app.services.cb_summary import bank_config

router = APIRouter(tags=["cb-imports"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
AdminDep = Depends(require_role("admin"))
MAX_UPLOAD = 20 * 1024 * 1024
FILE_BANKS = {"projections": ("RBNZ", "BOE", "BOC"), "statements": tuple(bank_config())}


def _back(message: str) -> RedirectResponse:
    return RedirectResponse(f"/admin/cb-imports?msg={quote(message)}", status_code=303)


def _who(user) -> str | None:
    return getattr(user, "email", None)


@router.get("/admin/cb-imports", response_class=HTMLResponse)
async def cb_imports_page(request: Request, _: User = AdminDep) -> HTMLResponse:
    sources = []
    for bank in bank_config():
        for kind, reason in imports.manual_sources(bank).items():
            sources.append({"bank": bank, "kind": kind, "reason": reason,
                            "last_import": await imports.last_import(bank, kind)})
    return templates.TemplateResponse(request, "cb_imports.html", {
        "request": request, "page_title": "CB imports | ForexCompass", "sources": sources,
        "banks": list(bank_config()), "file_banks": FILE_BANKS, "variables": sorted(imports.parsers.BOUNDS),
        "recent": await imports.import_summary(), "message": request.query_params.get("msg", "")})


@router.post("/admin/cb-imports/file")
async def cb_import_file(bank: str = Form(...), kind: str = Form(...), round_date: date = Form(...),
                         upload: UploadFile = File(...), user: User = AdminDep) -> RedirectResponse:
    bank = bank.upper()
    if kind not in FILE_BANKS or bank not in FILE_BANKS[kind]:
        return _back(f"No file import for {bank} {kind}")
    content = await upload.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        return _back("File larger than 20 MB")
    try:
        if kind == "projections":
            count = await imports.import_projection_file(bank, upload.filename or "upload", content, round_date, _who(user))
        else:
            count = await imports.import_statement(bank, round_date, upload.filename or "upload.txt", content, _who(user))
    except (ValueError, KeyError) as exc:
        return _back(f"Rejected: {exc}")
    return _back(f"Imported {count} {kind} rows for {bank} {round_date}")


@router.post("/admin/cb-imports/values")
async def cb_import_values(bank: str = Form(...), round_date: date = Form(...), values: str = Form(...),
                           user: User = AdminDep) -> RedirectResponse:
    bank = bank.upper()
    if bank not in bank_config():
        return _back(f"Unknown bank {bank}")
    try:
        count = await imports.import_projection_values(bank, round_date, values, _who(user))
    except ValueError as exc:
        return _back(f"Rejected: {exc}")
    return _back(f"Saved {count} projection values for {bank} {round_date}")
