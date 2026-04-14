import re
from typing import Dict, Any, Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def extract_price_from_html(html: str) -> Optional[str]:
    patterns = [
        r'"priceAmount":"(\d+\.\d{2})"',
        r'"displayPrice":"\$?(\d+\.\d{2})"',
        r'\$(\d+\.\d{2})',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return f"${m.group(1)}"
    return None


def normalize_space(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(value.split())


async def check_amazon_product(asin: str) -> Dict[str, Any]:
    url = f"https://www.amazon.com/dp/{asin}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1400, "height": 1400},
            locale="en-US",
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass

        html = await page.content()

        title = ""
        sold_by = ""
        ships_from = ""

        title_locators = [
            page.locator("#productTitle"),
            page.get_by_text(re.compile(r".+"), exact=False).locator("xpath=ancestor-or-self::*[1]"),
        ]

        for locator in title_locators[:1]:
            try:
                title = normalize_space(await locator.first.text_content(timeout=3000))
                if title:
                    break
            except Exception:
                pass

        offer_text_candidates = []

        # Main offer box text
        for selector in [
            "#merchantInfo",
            "#tabular-buybox",
            "#desktop_qualifiedBuyBox",
            "#buybox",
            "#exports_desktop_qualifiedBuybox_buybox_tabular_feature_div",
            "#fulfillerInfoFeature_feature_div",
            "#merchantInfoFeature_feature_div",
        ]:
            try:
                text = normalize_space(await page.locator(selector).inner_text(timeout=2000))
                if text:
                    offer_text_candidates.append(text)
            except Exception:
                pass

        combined = " | ".join(offer_text_candidates)

        sold_by_amazon = (
            "Sold by Amazon.com" in combined
            or "sold by Amazon.com" in combined
        )
        ships_from_amazon = (
            "Ships from Amazon.com" in combined
            or "ships from Amazon.com" in combined
        )

        sold_match = re.search(r"Sold by\s+([^|]+?)(?:Ships from|$)", combined, re.IGNORECASE)
        if sold_match:
            sold_by = normalize_space(sold_match.group(1))

        ships_match = re.search(r"Ships from\s+([^|]+?)(?:Sold by|$)", combined, re.IGNORECASE)
        if ships_match:
            ships_from = normalize_space(ships_match.group(1))

        in_stock = False

        stock_checks = [
            "Add to Cart",
            "Buy Now",
            "In Stock",
        ]
        for needle in stock_checks:
            if needle in html:
                in_stock = True
                break

        unavailable_markers = [
            "Currently unavailable",
            "Temporarily out of stock",
            "Out of Stock",
        ]
        for needle in unavailable_markers:
            if needle in html:
                in_stock = False

        price_text = extract_price_from_html(html)

        await context.close()
        await browser.close()

        return {
            "asin": asin,
            "url": url,
            "title": title or f"Amazon item {asin}",
            "price_text": price_text or "Unknown",
            "in_stock": in_stock,
            "sold_by_amazon": sold_by_amazon,
            "ships_from_amazon": ships_from_amazon,
            "sold_by": sold_by or ("Amazon.com" if sold_by_amazon else "Unknown"),
            "ships_from": ships_from or ("Amazon.com" if ships_from_amazon else "Unknown"),
        }
