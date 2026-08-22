from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def verify_fixed_income_ui(
    *,
    session_token: str | None,
    email: str | None,
    password: str | None,
    base_url: str,
    chrome_path: str,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    failed_requests: list[str] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            executable_path=chrome_path,
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        if session_token:
            await context.add_cookies([
                {
                    "name": "macro_dashboard_session",
                    "value": session_token,
                    "url": base_url,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
            ])
        page = await context.new_page()
        page.on(
            "console",
            lambda msg: console_errors.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))
        page.on(
            "response",
            lambda response: failed_requests.append(f"{response.status} {response.url}")
            if response.status >= 400 and "favicon" not in response.url
            else None,
        )

        if email and password:
            await page.goto(f"{base_url}/login", wait_until="domcontentloaded")
            await page.fill("input[name='email']", email)
            await page.fill("input[name='password']", password)
            async with page.expect_navigation(wait_until="domcontentloaded"):
                await page.click("button[type='submit']")

        await page.goto(f"{base_url}/fixed-income", wait_until="domcontentloaded")
        await page.locator("[data-fi-state]").wait_for(timeout=30000)
        await page.locator("[data-fi-pairs] tr").first.wait_for(timeout=30000)
        await page.wait_for_function(
            "() => !document.querySelector('[data-fi-state]')?.textContent?.startsWith('Loading')",
            timeout=120000,
        )
        await page.screenshot(path=output_dir / "fixed_income_desktop.png", full_page=True)
        if await page.locator("[data-fi-state]").count() == 0:
            print(f"final_url={page.url}")
            print(f"title={await page.title()}")
            print(f"body_text={(await page.locator('body').inner_text())[:500]}")
            await browser.close()
            raise RuntimeError("fixed-income root not found")
        state = await page.locator("[data-fi-state]").inner_text()
        matrix_rows = await page.locator("[data-fi-pairs] tr").count()
        narrative = await page.locator("[data-fi-pair-narrative]").inner_text()

        await page.set_viewport_size({"width": 390, "height": 900})
        await page.goto(f"{base_url}/fixed-income", wait_until="domcontentloaded")
        await page.locator("[data-fi-state]").wait_for(timeout=30000)
        await page.locator("[data-fi-pairs] tr").first.wait_for(timeout=30000)
        await page.wait_for_function(
            "() => !document.querySelector('[data-fi-state]')?.textContent?.startsWith('Loading')",
            timeout=120000,
        )
        await page.screenshot(path=output_dir / "fixed_income_mobile.png", full_page=True)
        mobile_rows = await page.locator("[data-fi-pairs] tr").count()

        print(f"state={state}")
        print(f"matrix_rows={matrix_rows}")
        print(f"mobile_rows={mobile_rows}")
        print(f"narrative_words={len(narrative.split())}")
        print(f"console_errors={len(console_errors)}")
        for error in console_errors[:10]:
            print(error)
        print(f"failed_requests={len(failed_requests)}")
        for request in failed_requests[:20]:
            print(request)
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-token")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--chrome-path",
        default=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    parser.add_argument("--output-dir", default="data")
    args = parser.parse_args()
    asyncio.run(
        verify_fixed_income_ui(
            session_token=args.session_token,
            email=args.email,
            password=args.password,
            base_url=args.base_url.rstrip("/"),
            chrome_path=args.chrome_path,
            output_dir=Path(args.output_dir),
        )
    )


if __name__ == "__main__":
    main()
