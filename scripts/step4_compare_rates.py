"""Compare the fixed live snapshot with stored observations on common dates."""

import asyncio
import json
from datetime import date
from pathlib import Path

from app.services.rates import _stored_histories


async def main() -> None:
    snapshot = json.loads(Path("data/step4_rates_live_snapshot.json").read_text(encoding="utf-8"))
    symbols = list(snapshot["series"])
    start, end = date.fromisoformat(snapshot["from"]), date.fromisoformat(snapshot["to"])
    yield_symbols = [s for s in symbols if s.endswith(".GBOND")]
    fx_symbols = [s for s in symbols if s.endswith(".FOREX")]
    yields = await _stored_histories(yield_symbols, start, end)
    fx = await _stored_histories(fx_symbols, start, end, fx=True)
    stored = dict(zip(yield_symbols + fx_symbols, yields + fx, strict=True))
    for symbol in symbols:
        live = snapshot["series"][symbol]
        db = {row["date"]: row["close"] for row in stored[symbol]}
        overlap = set(live) & set(db)
        differences = {day: abs(live[day] - db[day]) for day in overlap}
        worst = max(differences, key=differences.get) if differences else None
        relative_pct = (
            differences[worst] / abs(live[worst]) * 100
            if worst and symbol.endswith(".FOREX") and live[worst] else None
        )
        print(symbol, "overlap", len(overlap), "max_abs", differences.get(worst),
              "max_pct", relative_pct, "at", worst)


if __name__ == "__main__":
    asyncio.run(main())
