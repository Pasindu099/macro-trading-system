"""Currency whiteboard CRUD and HTMX library endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.settings import SUPPORTED_CURRENCIES
from app.whiteboard.categories import CATEGORY_ORDER
from app.whiteboard.registry import WIDGET_REGISTRY, indicator_library, search_library, widget_data

router = APIRouter(tags=["whiteboard"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
SessionDep = Depends(get_session)


class BoardCreate(BaseModel):
    currency: str
    name: str = Field(min_length=1, max_length=120)


class ItemCreate(BaseModel):
    kind: Literal["widget", "note", "thesis"]
    widget_type: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    x: int
    y: int
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class ItemPatch(BaseModel):
    x: int | None = None
    y: int | None = None
    w: int | None = Field(default=None, gt=0)
    h: int | None = Field(default=None, gt=0)
    z: int | None = None
    config: dict[str, Any] | None = None


class LinkCreate(BaseModel):
    from_item_id: int
    to_item_id: int
    label: str | None = None


class ViewportPatch(BaseModel):
    x: float
    y: float
    zoom: float = Field(ge=0.25, le=2)


def _currency(value: str) -> str:
    currency = value.upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise HTTPException(422, "Unsupported currency")
    return currency


@router.get("/desks/{currency}/whiteboard", response_class=HTMLResponse)
async def whiteboard_page(request: Request, currency: str,
                          session: AsyncSession = SessionDep) -> HTMLResponse:
    currency = _currency(currency)
    await list_boards(currency, session)
    return templates.TemplateResponse(request, "whiteboard/page.html", {
        "page_title": f"{currency} Whiteboard | ForexCompass", "currency": currency,
    })


def _dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


async def _board(session: AsyncSession, board_id: int) -> dict[str, Any]:
    row = (await session.execute(text("SELECT * FROM whiteboards WHERE id=:id"), {"id": board_id})).first()
    if row is None:
        raise HTTPException(404, "Board not found")
    return _dict(row)


@router.get("/whiteboard/{currency}/boards")
@router.get("/api/boards")
async def list_boards(currency: str, session: AsyncSession = SessionDep) -> list[dict[str, Any]]:
    currency = _currency(currency)
    rows = (await session.execute(text("SELECT * FROM whiteboards WHERE currency=:currency ORDER BY id"),
                                  {"currency": currency})).all()
    if not rows:
        await session.execute(text("""
            INSERT INTO whiteboards (currency, name) VALUES (:currency, 'Main')
            ON CONFLICT (currency, name) DO NOTHING
        """), {"currency": currency})
        await session.commit()
        rows = (await session.execute(text("SELECT * FROM whiteboards WHERE currency=:currency ORDER BY id"),
                                      {"currency": currency})).all()
    return [_dict(row) for row in rows]


@router.post("/api/boards", status_code=201)
async def create_board(body: BoardCreate, session: AsyncSession = SessionDep) -> dict[str, Any]:
    currency = _currency(body.currency)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Board name is required")
    row = (await session.execute(text("""
        INSERT INTO whiteboards (currency, name) VALUES (:currency, :name)
        ON CONFLICT (currency, name) DO NOTHING RETURNING *
    """), {"currency": currency, "name": name})).first()
    if row is None:
        raise HTTPException(409, "Board name already exists")
    await session.commit()
    return _dict(row)


@router.get("/api/boards/{board_id}")
async def get_board(board_id: int, session: AsyncSession = SessionDep) -> dict[str, Any]:
    board = await _board(session, board_id)
    items = (await session.execute(text("SELECT * FROM whiteboard_items WHERE board_id=:id ORDER BY z,id"),
                                   {"id": board_id})).all()
    links = (await session.execute(text("SELECT * FROM whiteboard_links WHERE board_id=:id ORDER BY id"),
                                   {"id": board_id})).all()
    return {**board, "items": [_dict(row) for row in items], "links": [_dict(row) for row in links]}


@router.post("/api/boards/{board_id}/items", status_code=201)
async def create_item(board_id: int, body: ItemCreate, session: AsyncSession = SessionDep) -> dict[str, Any]:
    await _board(session, board_id)
    if body.kind == "widget" and body.widget_type not in WIDGET_REGISTRY:
        raise HTTPException(422, "Unknown widget type")
    if body.kind != "widget" and body.widget_type is not None:
        raise HTTPException(422, "Only widgets have a widget type")
    row = (await session.execute(text("""
        INSERT INTO whiteboard_items (board_id, kind, widget_type, config, x, y, w, h)
        VALUES (:board_id, :kind, :widget_type, CAST(:config AS jsonb), :x, :y, :w, :h)
        RETURNING *
    """), {"board_id": board_id, **body.model_dump(exclude={"config"}),
           "config": json.dumps(body.config)})).first()
    await session.commit()
    return _dict(row)


@router.patch("/api/boards/{board_id}/items/{item_id}")
async def patch_item(board_id: int, item_id: int, body: ItemPatch,
                     session: AsyncSession = SessionDep) -> dict[str, Any]:
    await _board(session, board_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(422, "No changes supplied")
    if any(value is None for value in fields.values()):
        raise HTTPException(422, "Null values are not supported")
    if "config" in fields:
        fields["config"] = json.dumps(fields["config"])
    assignments = ", ".join(f"{key} = {'CAST(:config AS jsonb)' if key == 'config' else ':' + key}"
                            for key in fields)
    row = (await session.execute(text(f"""
        UPDATE whiteboard_items SET {assignments}, updated_at=now()
        WHERE id=:item_id AND board_id=:board_id RETURNING *
    """), {**fields, "item_id": item_id, "board_id": board_id})).first()
    if row is None:
        raise HTTPException(404, "Item not found")
    await session.commit()
    return _dict(row)


@router.delete("/api/boards/{board_id}/items/{item_id}", status_code=204)
async def delete_item(board_id: int, item_id: int, session: AsyncSession = SessionDep) -> None:
    await _board(session, board_id)
    result = await session.execute(text("DELETE FROM whiteboard_items WHERE id=:id AND board_id=:board_id"),
                                   {"id": item_id, "board_id": board_id})
    if result.rowcount == 0:
        raise HTTPException(404, "Item not found")
    await session.commit()


@router.post("/api/boards/{board_id}/links", status_code=201)
async def create_link(board_id: int, body: LinkCreate, session: AsyncSession = SessionDep) -> dict[str, Any]:
    await _board(session, board_id)
    if body.from_item_id == body.to_item_id:
        raise HTTPException(422, "A link needs two items")
    row = (await session.execute(text("""
        INSERT INTO whiteboard_links (board_id, from_item_id, to_item_id, label)
        SELECT :board_id, :from_id, :to_id, :label
        WHERE (SELECT board_id FROM whiteboard_items WHERE id=:from_id) = :board_id
          AND (SELECT board_id FROM whiteboard_items WHERE id=:to_id) = :board_id
        RETURNING *
    """), {"board_id": board_id, "from_id": body.from_item_id,
           "to_id": body.to_item_id, "label": body.label})).first()
    if row is None:
        raise HTTPException(422, "Both items must belong to this board")
    await session.commit()
    return _dict(row)


@router.delete("/api/boards/{board_id}/links/{link_id}", status_code=204)
async def delete_link(board_id: int, link_id: int, session: AsyncSession = SessionDep) -> None:
    await _board(session, board_id)
    result = await session.execute(text("DELETE FROM whiteboard_links WHERE id=:id AND board_id=:board_id"),
                                   {"id": link_id, "board_id": board_id})
    if result.rowcount == 0:
        raise HTTPException(404, "Link not found")
    await session.commit()


@router.patch("/api/boards/{board_id}/viewport")
async def patch_viewport(board_id: int, body: ViewportPatch,
                         session: AsyncSession = SessionDep) -> dict[str, Any]:
    await _board(session, board_id)
    row = (await session.execute(text("""
        UPDATE whiteboards SET viewport=CAST(:viewport AS jsonb), updated_at=now()
        WHERE id=:id RETURNING *
    """), {"id": board_id, "viewport": json.dumps(body.model_dump())})).first()
    await session.commit()
    return _dict(row)


@router.get("/api/whiteboard/library", response_class=HTMLResponse)
async def library(currency: str, session: AsyncSession = SessionDep) -> HTMLResponse:
    currency = _currency(currency)
    items = await indicator_library(currency)
    counts = {category: sum(item["category"] == category for item in items)
              + sum(spec.category == category for spec in WIDGET_REGISTRY.values()
                    if spec.widget_type != "indicator") for category in CATEGORY_ORDER}
    html = templates.get_template("whiteboard/library.html").render(
        currency=currency, categories=CATEGORY_ORDER, counts=counts)
    return HTMLResponse(html)


@router.get("/api/whiteboard/library/{category}", response_class=HTMLResponse)
async def library_category_items(category: str, currency: str, q: str = "",
                                 session: AsyncSession = SessionDep) -> HTMLResponse:
    currency = _currency(currency)
    if category not in CATEGORY_ORDER:
        raise HTTPException(404, "Unknown category")
    indicators = search_library([item for item in await indicator_library(currency)
                                 if item["category"] == category], q)
    fixed = [{"widget_type": spec.widget_type, "title": spec.title, "config": {},
              "cb_preferred": False} for spec in WIDGET_REGISTRY.values()
             if spec.category == category and spec.widget_type != "indicator"
             and q.casefold() in spec.title.casefold()]
    html = templates.get_template("whiteboard/category.html").render(
        currency=currency, category=category, q=q, items=fixed + indicators)
    return HTMLResponse(html)


@router.get("/api/whiteboard/widget-data/{widget_type}")
async def get_widget_data(widget_type: str, currency: str,
                          indicator_code: str | None = None, country_code: str | None = None,
                          title: str | None = None) -> dict[str, Any]:
    currency = _currency(currency)
    if widget_type not in WIDGET_REGISTRY:
        raise HTTPException(404, "Unknown widget type")
    return await widget_data(widget_type, currency, {"indicator_code": indicator_code,
                                                   "country_code": country_code, "title": title})
