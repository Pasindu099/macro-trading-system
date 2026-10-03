"""Report desks.yaml indicator series whose canonical name is not in the DB.

Run: docker compose exec app python -m scripts.check_desk_indicators
"""

import asyncio

from app.services.desks import known_canonicals, load_desks, validate_indicator_mapping


async def main() -> None:
    for code, desk in load_desks().items():
        if not desk.get("enabled"):
            continue
        report = validate_indicator_mapping(desk, await known_canonicals(desk["country"]))
        print(f"{code}: {report['checked']} series checked, {len(report['missing'])} not in DB")
        for item in report["missing"]:
            print(f"  - {item['chart']}: {item['series']} ({item['canonical']})")
        for member in desk.get("members", []):
            known = await known_canonicals(member["country"])
            for item in desk.get("key_data", []):
                canonical = item["series"].get(member["code"])
                if not canonical or canonical not in known:
                    print(f"  - {member['code']}: {item['id']} ({canonical}) unavailable")


if __name__ == "__main__":
    asyncio.run(main())
