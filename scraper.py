"""
FB Ad Library counter scraper.
Lê todas as ofertas do Supabase, abre cada linkBiblioteca num Chromium headless,
extrai o número de anúncios ativos e atualiza a coluna anuncios_ativos.
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


def scrape_one(page, url: str, retries: int = 2):
    """Abre a URL e tenta extrair o contador. Retorna int ou None."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # garante que o body existe
            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass

            # espera o JS renderizar UM dos dois: contador OU mensagem de "sem resultados".
            # O que vier primeiro libera. Isso evita esperar 25s em ofertas com 0 anúncios.
            try:
                page.wait_for_function(
                    """() => {
                        const body = document && document.body;
                        if (!body) return false;
                        const t = body.innerText || '';
                        // achou um número de resultados?
                        if (/\\d[\\d.,\\u00a0\\s]*\\s+(r[eé]sultats?|resultados?|results?|ergebnisse|risultati)/i.test(t)) return true;
                        // achou mensagem de "sem anúncios"?
                        if (/(no\\s+ads\\s+match|aucune?\\s+annonce|nenhum\\s+anúncio|ningún\\s+anuncio|nessun\\s+annuncio|keine\\s+anzeigen)/i.test(t)) return true;
                        return false;
                    }""",
                    timeout=15000,
                )
            except Exception:
                page.wait_for_timeout(2000)

            text = page.locator("body").inner_text()
            count = parse_count(text)
            if count is not None:
                return count

            # nenhum match? Verifica se a página diz "sem anúncios"
            if EMPTY_PATTERNS.search(text):
                return 0

            last_error = "padrão de contagem não encontrado no texto da página"

        except Exception as e:
            last_error = str(e)[:200]

        if attempt < retries:
            print(f"   tentativa {attempt + 1} falhou: {last_error}", file=sys.stderr)
            time.sleep(3)

    print(f"   ❌ desistindo após {retries + 1} tentativas: {last_error}", file=sys.stderr)
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
        # bloqueia recursos pesados pra acelerar
        context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )

        page = context.new_page()

        for idx, (row_id, url) in enumerate(targets, 1):
            print(f"[{idx}/{len(targets)}] {row_id}")
            count = scrape_one(page, url)
            if count is None:
                fail += 1
                continue

            sb.table(TABLE).update({"anuncios_ativos": count}).eq(
                "id", row_id
            ).execute()
            print(f"   ✅ {count} anúncios ativos")
            ok += 1

        browser.close()

    print(f"\n🏁 Concluído: {ok} ok, {fail} falhas")
    if targets and fail / len(targets) > 0.3:
        sys.exit(1)


if __name__ == "__main__":
    main()
