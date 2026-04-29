# FB Ads Counter — Scraper gratuito da Biblioteca de Anúncios

Substitui o actor pago do Apify por um scraper próprio que roda em GitHub Actions.
Pega só o **número de anúncios ativos** (`~4,700 resultados`) de cada oferta e
atualiza a tabela `ofertas` do Supabase.

**Custo:** R$ 0,00. Roda em ~3-5 min/dia. Free tier do GitHub cobre tranquilo.

---

## 1. Subir o repositório

```bash
git init
git add .
git commit -m "scraper inicial"
gh repo create fb-ads-scraper --private --source=. --push
```

(Se preferir, crie o repo manualmente em github.com e faça push.)

## 2. Configurar os secrets

No repo, vá em **Settings → Secrets and variables → Actions → New repository secret** e adicione:

| Nome | Valor |
|---|---|
| `SUPABASE_URL` | `https://SEU-PROJETO.supabase.co` |
| `SUPABASE_SERVICE_KEY` | sua chave **service_role** (não a anon!) |

⚠️ A `service_role` está em **Project Settings → API → service_role**. Ela bypassa RLS, por isso precisa ficar como secret e nunca commitada.

## 3. Estrutura esperada da tabela `ofertas`

O script lê:
- `id` (PK, qualquer tipo)
- `oferta_data` (jsonb) — espera `{"linkBiblioteca": "https://..."}` dentro
- `anuncios_ativos` (int) — coluna que será atualizada

Se o nome do campo dentro do JSON for outro, edite a linha no `scraper.py`:
```python
link = (data or {}).get("linkBiblioteca")
```

## 4. Testar manualmente

Em **Actions → Daily FB Ads Counter → Run workflow**. Vai rodar agora e você
acompanha os logs em tempo real.

## 5. Schedule

Roda automaticamente todo dia às **09:00 Brasília** (12:00 UTC).
Pra mudar, edite o cron em `.github/workflows/scrape.yml`:

```yaml
- cron: "0 12 * * *"   # min hora dia mês dia-da-semana (UTC)
```

## Rodar local (debug)

```bash
pip install -r requirements.txt
python -m playwright install chromium --with-deps
export SUPABASE_URL="..."
export SUPABASE_SERVICE_KEY="..."
python scraper.py
```

## Como funciona

1. `SELECT id, oferta_data FROM ofertas` no Supabase
2. Para cada linha com `linkBiblioteca`:
   - Abre Chromium headless
   - Navega na URL e espera o JS renderizar o contador
   - Extrai via regex (`~4,700 resultados` em PT/FR/EN/ES/DE/IT)
   - `UPDATE ofertas SET anuncios_ativos = X WHERE id = Y`
3. Bloqueia imagens/fontes/vídeos pra carregar mais rápido
4. Reaproveita a mesma aba pra todas as ofertas (mais rápido que abrir uma por vez)

## Quando o Facebook quebra o seletor

A Meta muda o DOM com frequência. O script usa **regex no texto da página**, não
seletor CSS, justamente pra ser resiliente. Mesmo que mudem a estrutura HTML, o
texto "X resultados" continua aparecendo. Se um dia mudarem a palavra, é só
adicionar mais um padrão em `COUNT_PATTERNS`.

## Anti-bot

O FB às vezes pede captcha em IPs muito agressivos. Se isso acontecer:
- Diminuir a frequência (1x/dia já é bem conservador)
- Adicionar `page.wait_for_timeout(random.randint(2000, 5000))` entre ofertas
- Em último caso, usar proxy residencial barato (BrightData, etc)

Pra volume baixo (até ~100 ofertas/dia) você nunca vai ver bloqueio.
