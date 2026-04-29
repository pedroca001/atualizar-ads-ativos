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
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # use service_role pra UPDATE
TABLE = "ofertas"

# regex que captura "~4,700 resultados", "About 12,500 results", "~71 résultats", etc.
COUNT_PATTERNS = [
    r"~?\s*([\d.,\u00a0\s]+)\s+r[eé]sultats?",     # FR
    r"~?\s*([\d.,\u00a0\s]+)\s+resultados?",       # PT/ES
    r"(?:about\s+|~)?\s*([\d.,\u00a0\s]+)\s+results?",  # EN
    r"~?\s*([\d.,\u00a0\s]+)\s+ergebnisse",        # DE
    r"~?\s*([\d.,\u00a0\s]+)\s+risultati",         # IT
]


def parse_count(text: str):
    """Acha o primeiro match de '~N resultados' no texto da página."""
    for pattern in COUNT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1)
            # remove tudo que não for dígito (vírgula, ponto, nbsp, espaço)
            digits = re.sub(r"\D", "", raw)
            if digits:
                return int(digits)
    return None


def scrape_one(page, url: str, retries: int = 2):
    """Abre a URL e tenta extrair o contador. Retorna int ou None."""
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Tenta esperar pelo elemento que tem 'resultado' / 'result' / 'résultat'
            try:
                page.wait_for_function(
                    """() => {
                        const t = document.body.innerText;
                        return /\\d[\\d.,\\u00a0\\s]*\\s+(r[eé]sultats?|resultados?|results?|ergebnisse|risultati)/i.test(t);
                    }""",
                    timeout=15000,
                )
            except PWTimeout:
                # fallback: dorme um pouco e tenta mesmo assim
                page.wait_for_timeout(4000)

            text = page.locator("body").inner_text()
            count = parse_count(text)
            if count is not None:
                return count

            # nenhum match? Pode ser "Nenhum anúncio corresponde"
            if re.search(r"(no\s+ads|aucun|nenhum|ningún)", text, re.IGNORECASE):
                return 0

        except Exception as e:
            print(f"   tentativa {attempt + 1} falhou: {e}", file=sys.stderr)
            if attempt < retries:
                time.sleep(3)
                continue

    return None


def main():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    rows = sb.table(TABLE).select("id, oferta_data").execute().data
    print(f"📦 {len(rows)} ofertas no Supabase")

    # monta lista (id, url) ignorando linhas sem linkBiblioteca
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
        # bloqueia recursos pesados pra acelerar (imagens, fonts, vídeos)
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
                print("   ❌ não consegui extrair o contador")
                fail += 1
                continue

            sb.table(TABLE).update({"anuncios_ativos": count}).eq(
                "id", row_id
            ).execute()
            print(f"   ✅ {count} anúncios ativos")
            ok += 1

        browser.close()

    print(f"\n🏁 Concluído: {ok} ok, {fail} falhas")
    # falha o job se mais de 30% das ofertas falharam
    if targets and fail / len(targets) > 0.3:
        sys.exit(1)


if __name__ == "__main__":
    main()
