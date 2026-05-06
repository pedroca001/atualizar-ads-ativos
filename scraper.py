"""
FB Ad Library counter scraper - estratégia robusta:
- aguarda networkidle (FB para de fazer requests AJAX = página estabilizou)
- só aceita "0 anúncios" depois desse estabilizar
"""

import os
import re
import json
import time
import sys
from datetime import datetime, timedelta, timezone
from supabase import create_client
from playwright.sync_api import sync_playwright

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TABLE = "ofertas"
HISTORY_TABLE = "oferta_ads_leituras"
HISTORY_RETENTION_DAYS = 7

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


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def record_reading(sb, row_id, url, count, read_at, status="success", error=None):
    sb.table(HISTORY_TABLE).insert(
        {
            "oferta_id": row_id,
            "anuncios_ativos": count,
            "lido_em": read_at,
            "fonte": "github_actions",
            "link_biblioteca": url,
            "status": status,
            "erro": error,
        }
    ).execute()


def cleanup_old_history(sb):
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)
    sb.table(HISTORY_TABLE).delete().lt("lido_em", cutoff.isoformat()).execute()


def scrape_one(page, url: str, retries: int = 2):
    """
    Estratégia:
    1. Carrega a página
    2. Espera networkidle (FB para de fazer fetches = conteúdo real carregou)
    3. Só DEPOIS lê o texto e extrai o número
    """
    last_error = None
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # 1) garante body
            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass

            # 2) ESPERA O FACEBOOK PARAR DE FAZER REQUESTS.
            # networkidle = nenhum request AJAX há 500ms.
            # Isso é o sinal de que a busca de anúncios terminou.
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                # se não chegar em networkidle em 20s, segue mesmo assim
                pass

            # 3) tempo extra de segurança
            page.wait_for_timeout(2000)

            # 4) tenta extrair o número
            text = page.locator("body").inner_text()
            count = parse_count(text)
            if count is not None:
                return count

            # 5) número não apareceu? Tenta esperar mais 5s e relê
            # (FB pode estar lento)
            page.wait_for_timeout(5000)
            text = page.locator("body").inner_text()
            count = parse_count(text)
            if count is not None:
                return count

            # 6) só agora aceita "sem anúncios" — depois do networkidle e dois retries
            if EMPTY_PATTERNS.search(text):
                return 0

            last_error = "número não apareceu mesmo após networkidle"

        except Exception as e:
            last_error = str(e)[:200]

        if attempt < retries:
            print(f"   tentativa {attempt + 1} falhou: {last_error}", file=sys.stderr)
            time.sleep(3)

    print(f"   ❌ desistindo: {last_error}", file=sys.stderr)
    return None


def main():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    rows = sb.table(TABLE).select("id, oferta_data, link_biblioteca").execute().data
    print(f"📦 {len(rows)} ofertas no Supabase")

    targets = []
    for row in rows:
        data = row.get("oferta_data")
        if isinstance(data, str):
            data = json.loads(data)
        link = row.get("link_biblioteca") or (data or {}).get("linkBiblioteca")
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
        # IMPORTANTE: NÃO bloqueia mais image/media/font porque queremos
        # que o networkidle inclua todos os requests. Bloquear iria criar
        # falsos networkidle.
        # (Removido o context.route)

        page = context.new_page()

        for idx, (row_id, url) in enumerate(targets, 1):
            print(f"[{idx}/{len(targets)}] {row_id}")
            read_at = utc_now_iso()
            count = scrape_one(page, url)
            if count is None:
                try:
                    record_reading(
                        sb,
                        row_id,
                        url,
                        None,
                        read_at,
                        status="failed",
                        error="contador nao encontrado",
                    )
                except Exception as e:
                    print(f"   falha ao registrar leitura: {str(e)[:200]}", file=sys.stderr)
                fail += 1
                continue

            sb.table(TABLE).update(
                {
                    "anuncios_ativos": count,
                    "anuncios_ativos_atualizado_em": read_at,
                }
            ).eq("id", row_id).execute()
            record_reading(sb, row_id, url, count, read_at)
            print(f"   ✅ {count} anúncios ativos")
            ok += 1

        browser.close()

    try:
        cleanup_old_history(sb)
        print(f"🧹 histórico com mais de {HISTORY_RETENTION_DAYS} dias apagado")
    except Exception as e:
        print(f"⚠️ falha ao limpar histórico antigo: {str(e)[:200]}", file=sys.stderr)

    print(f"\n🏁 Concluído: {ok} ok, {fail} falhas")
    if targets and fail / len(targets) > 0.3:
        sys.exit(1)


if __name__ == "__main__":
    main()
