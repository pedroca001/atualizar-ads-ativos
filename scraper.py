"""
FB Ad Library counter scraper - versão com debug.
"""

import os
import re
import json
import time
import sys
from supabase import create_client
from playwright.sync_api import sync_playwright

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TABLE = "ofertas"
DEBUG = os.environ.get("DEBUG", "1") == "1"
MAX_DEBUG = 5  # limita quantas ofertas vão imprimir debug detalhado

COUNT_PATTERNS = [
    r"~?\s*([\d.,\u00a0\s]+)\s+r[eé]sultats?",
    r"~?\s*([\d.,\u00a0\s]+)\s+resultados?",
    r"(?:about\s+|~)?\s*([\d.,\u00a0\s]+)\s+results?",
    r"~?\s*([\d.,\u00a0\s]+)\s+ergebnisse",
    r"~?\s*([\d.,\u00a0\s]+)\s+risultati",
]

EMPTY_PATTERNS = re.compile(
    r"(no\s+ads\s+match|aucune?\s+annonce|nenhum\s+anúncio|"
    r"ningún\s+anuncio|nessun\s+annuncio|keine\s+anzeigen)",
    re.IGNORECASE,
)


def parse_count(text: str):
    for pattern in COUNT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if digits:
                return int(digits)
    return None


debug_count = 0


def scrape_one(page, url: str, idx: int, retries: int = 2):
    global debug_count
    last_error = None
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass

            try:
                page.wait_for_function(
                    """() => {
                        const body = document && document.body;
                        if (!body) return false;
                        const t = body.innerText || '';
                        if (/\\d[\\d.,\\u00a0\\s]*\\s+(r[eé]sultats?|resultados?|results?|ergebnisse|risultati)/i.test(t)) return true;
                        if (/(no\\s+ads\\s+match|aucune?\\s+annonce|nenhum\\s+anúncio|ningún\\s+anuncio|nessun\\s+annuncio|keine\\s+anzeigen)/i.test(t)) return true;
                        return false;
                    }""",
                    timeout=20000,
                )
            except Exception:
                page.wait_for_timeout(3000)

            text = page.locator("body").inner_text()
            count = parse_count(text)
            if count is not None:
                return count

            # DEBUG: imprime amostra do texto pra entender o que tá vindo
            if DEBUG and debug_count < MAX_DEBUG:
                debug_count += 1
                snippet = text[:600].replace("\n", " | ")
                print(f"   🔍 DEBUG idx={idx}: count=None | text[:600]={snippet!r}", file=sys.stderr)
                # também imprime a URL final (pra detectar redirect pra login)
                print(f"   🔍 DEBUG url_final={page.url}", file=sys.stderr)

            if EMPTY_PATTERNS.search(text):
                return 0

            last_error = "padrão de contagem não encontrado"

        except Exception as e:
            last_error = str(e)[:200]

        if attempt < retries:
            print(f"   tentativa {attempt + 1} falhou: {last_error}", file=sys.stderr)
            time.sleep(3)

    print(f"   ❌ desistindo: {last_error}", file=sys.stderr)
    return None


def main():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    rows = sb.table(TABLE).select("id, oferta_data").execute().data
    print(f"📦 {len(rows)} ofertas no Supabase")

    targets = []
    for row in rows:
        data = row["oferta_data"]
        if isinstance(data, str):
            data = json.loads(data)
        link = (data or {}).get("linkBiblioteca")
        if link:
            targets.append((row["id"], link))

    print(f"🎯 {len(targets)} ofertas com linkBiblioteca\n")

    ok, fail = 0, 0
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 800},
            locale="pt-BR",
        )
        context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )

        page = context.new_page()

        # DEBUG: pra primeira oferta, imprime mais informação ainda
        for idx, (row_id, url) in enumerate(targets, 1):
            print(f"[{idx}/{len(targets)}] {row_id}")
            if idx == 1 and DEBUG:
                print(f"   🔍 URL: {url}", file=sys.stderr)

            count = scrape_one(page, url, idx)
            if count is None:
                fail += 1
                continue

            sb.table(TABLE).update({"anuncios_ativos": count}).eq(
                "id", row_id
            ).execute()
            print(f"   ✅ {count} anúncios ativos")
            ok += 1

            # PARA NA 5a OFERTA pra economizar tempo durante debug
            if DEBUG and idx >= 5:
                print("\n🛑 DEBUG MODE: parando em 5 ofertas pra você analisar o output")
                break

        browser.close()

    print(f"\n🏁 Concluído: {ok} ok, {fail} falhas")


if __name__ == "__main__":
    main()
