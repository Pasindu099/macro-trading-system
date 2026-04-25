"""Fetch, cache, and analyze bank research reports from Google Drive."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx

from app.settings import get_settings

BANK_RESEARCH_DIR = Path("data/bank_research")
DOWNLOADS_DIR = BANK_RESEARCH_DIR / "files"
INDEX_PATH = BANK_RESEARCH_DIR / "index.json"

GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

SUPPORTED_MIME_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.google-apps.document": ".txt",
}


@dataclass(frozen=True)
class BankResearchConfig:
    folder_url: str
    google_drive_api_key: str
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    retention_days: int = 7
    output_dir: Path = BANK_RESEARCH_DIR


def load_bank_research_index(index_path: Path = INDEX_PATH) -> dict[str, Any]:
    if not index_path.exists():
        return {
            "generated_at": None,
            "folder_url": None,
            "reports": [],
            "errors": [],
        }
    with index_path.open("r", encoding="utf-8") as file:
        return json.load(file)


async def build_bank_research_cache(config: BankResearchConfig) -> dict[str, Any]:
    """Download Drive reports, summarize them, and write the 7-day cache index."""
    output_dir = config.output_dir
    downloads_dir = output_dir / "files"
    output_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    cleanup_old_bank_research_files(output_dir, config.retention_days)

    folder_id = parse_drive_folder_id(config.folder_url)
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=90) as client:
        drive_files = await fetch_drive_files(client, folder_id, config.google_drive_api_key)
        existing = {
            report.get("drive_file_id"): report
            for report in load_bank_research_index(output_dir / "index.json").get("reports", [])
        }
        reports: list[dict[str, Any]] = []

        for drive_file in drive_files:
            if drive_file["mimeType"] not in SUPPORTED_MIME_TYPES:
                errors.append(f"Skipped unsupported file type: {drive_file['name']}")
                continue

            previous = existing.get(drive_file["id"])
            file_path = downloads_dir / safe_report_filename(drive_file)
            if not file_path.exists():
                try:
                    await download_drive_file(
                        client=client,
                        drive_file=drive_file,
                        destination=file_path,
                        api_key=config.google_drive_api_key,
                    )
                except Exception as exc:
                    errors.append(f"Download failed for {drive_file['name']}: {exc}")
                    continue

            text = extract_report_text(file_path, drive_file["mimeType"])
            if previous and previous.get("source_modified_time") == drive_file.get("modifiedTime"):
                reports.append(previous)
                continue

            try:
                analysis = await analyze_report_text(
                    client=client,
                    text=text,
                    file_name=drive_file["name"],
                    openai_api_key=config.openai_api_key,
                    model=config.openai_model,
                )
            except Exception as exc:
                errors.append(f"Analysis failed for {drive_file['name']}: {exc}")
                analysis = fallback_analysis(drive_file["name"], text)

            reports.append({
                "drive_file_id": drive_file["id"],
                "file_name": drive_file["name"],
                "mime_type": drive_file["mimeType"],
                "source_modified_time": drive_file.get("modifiedTime"),
                "drive_web_link": drive_file.get("webViewLink"),
                "local_path": str(file_path),
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "analysis": analysis,
            })

    reports = sorted(
        reports,
        key=lambda item: item.get("source_modified_time") or "",
        reverse=True,
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "folder_url": config.folder_url,
        "retention_days": config.retention_days,
        "reports": reports,
        "errors": errors,
    }
    write_json(output_dir / "index.json", payload)
    return payload


def config_from_settings(folder_url: str | None = None) -> BankResearchConfig:
    settings = get_settings()
    resolved_folder_url = folder_url or settings.bank_research_drive_folder_url
    if not resolved_folder_url:
        raise ValueError("Provide --folder-url or set BANK_RESEARCH_DRIVE_FOLDER_URL.")
    if not settings.google_drive_api_key:
        raise ValueError("Set GOOGLE_DRIVE_API_KEY before ingesting bank research.")
    return BankResearchConfig(
        folder_url=resolved_folder_url,
        google_drive_api_key=settings.google_drive_api_key,
        openai_api_key=settings.openai_api_key,
        openai_model=settings.openai_model,
        retention_days=settings.bank_research_retention_days,
    )


def parse_drive_folder_id(folder_url: str) -> str:
    patterns = [
        r"/folders/([A-Za-z0-9_-]+)",
        r"[?&]id=([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, folder_url)
        if match:
            return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]+", folder_url.strip()):
        return folder_url.strip()
    raise ValueError("Could not parse Google Drive folder ID from URL.")


async def fetch_drive_files(
    client: httpx.AsyncClient,
    folder_id: str,
    api_key: str,
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params = {
            "key": api_key,
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink,size)",
            "orderBy": "modifiedTime desc",
            "pageSize": 100,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        response = await client.get(GOOGLE_DRIVE_FILES_URL, params=params)
        response.raise_for_status()
        data = response.json()
        files.extend(data.get("files") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            return files


async def download_drive_file(
    client: httpx.AsyncClient,
    drive_file: dict[str, Any],
    destination: Path,
    api_key: str,
) -> None:
    mime_type = drive_file["mimeType"]
    if mime_type == "application/vnd.google-apps.document":
        url = f"{GOOGLE_DRIVE_FILES_URL}/{drive_file['id']}/export"
        params = {"key": api_key, "mimeType": "text/plain"}
    else:
        url = f"{GOOGLE_DRIVE_FILES_URL}/{drive_file['id']}"
        params = {"key": api_key, "alt": "media", "supportsAllDrives": "true"}

    response = await client.get(url, params=params)
    response.raise_for_status()
    destination.write_bytes(response.content)


def safe_report_filename(drive_file: dict[str, Any]) -> str:
    suffix = SUPPORTED_MIME_TYPES.get(drive_file["mimeType"], "")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", drive_file["name"]).strip("._")
    if suffix and not name.lower().endswith(suffix):
        name = f"{name}{suffix}"
    return f"{drive_file['id']}_{name}"


def extract_report_text(file_path: Path, mime_type: str) -> str:
    if mime_type == "application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Install pypdf to extract PDF report text.") from exc

        reader = PdfReader(str(file_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if mime_type in {"text/plain", "application/vnd.google-apps.document"}:
        return file_path.read_text(encoding="utf-8", errors="ignore")

    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_docx_text(file_path)

    return ""


def extract_docx_text(file_path: Path) -> str:
    with zipfile.ZipFile(file_path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        texts = [
            node.text or ""
            for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
        ]
        joined = "".join(texts).strip()
        if joined:
            paragraphs.append(unescape(joined))
    return "\n".join(paragraphs)


async def analyze_report_text(
    client: httpx.AsyncClient,
    text: str,
    file_name: str,
    openai_api_key: str | None,
    model: str,
) -> dict[str, Any]:
    cleaned = " ".join(text.split())
    if not openai_api_key:
        return fallback_analysis(file_name, cleaned, status="missing_openai_api_key")
    if len(cleaned) < 300:
        return fallback_analysis(file_name, cleaned, status="insufficient_text")

    prompt = f"""
Analyze this bank research report for an FX/macro trading dashboard.

Return strict JSON with these keys:
- title: concise report title
- bank: bank or publisher name if identifiable
- report_date: report date if identifiable, otherwise null
- executive_summary: 2-4 sentence summary
- assets: array of objects with asset, asset_class, outlook, confidence, reasons
- geopolitical: object with discussed, summary, bank_view_global_economy, bank_view_financial_markets
- key_risks: array of concise risks

Rules:
- Capture every asset or market discussed, including FX pairs, currencies, rates, commodities, equities, credit, and regions.
- Outlook must be one of Bullish, Bearish, Neutral, Mixed, or Watch.
- Reasons must be paraphrased, not long quotes.
- If geopolitics is not discussed, set geopolitical.discussed=false.

File name: {file_name}
Report text:
{cleaned[:60000]}
""".strip()

    response = await client.post(
        OPENAI_CHAT_URL,
        headers={
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "You extract structured, trader-useful research summaries from bank reports.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        },
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def fallback_analysis(
    file_name: str,
    text: str,
    status: str = "analysis_unavailable",
) -> dict[str, Any]:
    preview = " ".join(text.split())[:600]
    return {
        "title": file_name,
        "bank": infer_bank_from_filename(file_name),
        "report_date": None,
        "executive_summary": (
            "Automated analysis is not available for this report yet. "
            "The file was cached and can be analyzed after credentials/dependencies are configured."
        ),
        "assets": [],
        "geopolitical": {
            "discussed": False,
            "summary": "",
            "bank_view_global_economy": "",
            "bank_view_financial_markets": "",
        },
        "key_risks": [],
        "status": status,
        "text_preview": preview,
    }


def infer_bank_from_filename(file_name: str) -> str:
    upper = file_name.upper()
    for bank in ("MUFG", "JPM", "JPMORGAN", "GOLDMAN", "MORGAN_STANLEY", "UBS", "CITI", "HSBC", "BARCLAYS", "BNP", "NOMURA"):
        if bank.replace("_", " ") in upper or bank in upper:
            return bank.replace("_", " ").title()
    return "Unknown"


def cleanup_old_bank_research_files(
    output_dir: Path = BANK_RESEARCH_DIR,
    retention_days: int = 7,
) -> None:
    output_dir = output_dir.resolve()
    downloads_dir = (output_dir / "files").resolve()
    downloads_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.name != "bank_research" or not str(downloads_dir).startswith(str(output_dir)):
        raise RuntimeError("Refusing to clean up outside data/bank_research.")

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for file_path in downloads_dir.iterdir():
        if not file_path.is_file():
            continue
        modified = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            file_path.unlink()

    index_path = output_dir / "index.json"
    if not index_path.exists():
        return
    data = load_bank_research_index(index_path)
    data["reports"] = [
        report
        for report in data.get("reports", [])
        if not is_report_expired(report, cutoff)
    ]
    write_json(index_path, data)


def is_report_expired(report: dict[str, Any], cutoff: datetime) -> bool:
    cached_at = report.get("cached_at")
    if not cached_at:
        return False
    try:
        return datetime.fromisoformat(cached_at) < cutoff
    except ValueError:
        return False


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, sort_keys=True)
