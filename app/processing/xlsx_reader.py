"""Minimal stdlib .xlsx sheet reader (no openpyxl): cell values as strings, by row."""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    index = 0
    for char in letters:
        index = index * 26 + ord(char) - 64
    return index - 1


def sheet_names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    return [sheet.get("name") for sheet in root.iter(f"{{{NS['m']}}}sheet")]


def read_sheet(content: bytes, name: str) -> list[list[str | None]]:
    """Rows of the named sheet; shared strings resolved, numbers kept as their stored text."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        sheet = next((s for s in workbook.iter(f"{{{NS['m']}}}sheet") if s.get("name") == name), None)
        if sheet is None:
            raise KeyError(f"Sheet {name!r} not found")
        target = targets[sheet.get(_REL)].lstrip("/")
        path = target if target.startswith("xl/") else f"xl/{target}"
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml")).iter(f"{{{NS['m']}}}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{{{NS['m']}}}t")))
        root = ElementTree.fromstring(archive.read(path))
    rows: list[list[str | None]] = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        cells: dict[int, str | None] = {}
        for cell in row.iter(f"{{{NS['m']}}}c"):
            value = cell.find("m:v", NS)
            if cell.get("t") == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(f"{{{NS['m']}}}t"))
            elif value is None:
                text = None
            elif cell.get("t") == "s":
                text = shared[int(value.text)]
            else:
                text = value.text
            cells[_column_index(cell.get("r"))] = text
        index = int(row.get("r")) - 1
        while len(rows) < index:
            rows.append([])
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows
