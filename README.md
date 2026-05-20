# FB Ads Counter - Scraper da Biblioteca de Anuncios

Substitui o actor pago do Apify por um scraper proprio que roda em GitHub Actions.
Pega o numero de anuncios ativos de cada oferta e atualiza o Supabase usado pelo
DR Vault.

## 1. Configurar os secrets

No repo, va em **Settings -> Secrets and variables -> Actions -> New repository secret**
e adicione:

| Nome | Valor |
|---|---|
| `SUPABASE_URL` | URL do projeto Supabase |
| `SUPABASE_SERVICE_KEY` | chave `service_role` |

Nao commitar secrets. A `service_role` bypassa RLS.

## 2. Estrutura esperada no Supabase

Tabela principal: `ofertas`.

Campos principais lidos ou atualizados:

- `id`
- `oferta_data`
- `link_biblioteca`
- `anuncios_ativos`
- `anuncios_ativos_atualizado_em`
- `ads_monitoring_status`
- `ads_zero_since`
- `ads_last_positive_at`
- `ads_inactivated_at`
- `ads_next_check_at`

Tabela de historico: `oferta_ads_leituras`.

Antes de publicar esta alteracao em um projeto existente, rode o SQL em
`supabase_ads_monitoring_cadence.sql` no Supabase SQL editor. Para setups novos,
`supabase_ads_history.sql` ja contem o schema consolidado.

## 3. Schedule

O workflow roda todos os dias as **04:00, 12:00 e 20:00 de Brasilia**
(`07:00, 15:00 e 23:00 UTC`):

```yaml
- cron: "0 7 * * *"
- cron: "0 15 * * *"
- cron: "0 23 * * *"
```

O GitHub Actions pode atrasar uma execucao agendada. O scraper usa o cron que
disparou a rodada para manter a cadencia correta mesmo quando a rodada das 12h
comeca alguns minutos ou horas depois.

O script decide quais ofertas entram no lote de cada horario:

- ofertas ativas: verificadas nas 3 execucoes do dia;
- ofertas com 0 ads por 2 dias: verificadas 1x/dia ao meio-dia;
- ofertas com 0 ads por 7 dias: marcadas como inativas e verificadas 1x a cada 3 dias ao meio-dia;
- se uma oferta volta a ter ads ativos, ela retorna automaticamente para o status ativo.

## 4. Testar manualmente

Em **Actions -> Daily FB Ads Counter -> Run workflow**.
O input `run_local_hour` vem como `12` por padrao para simular a rodada diaria
do meio-dia e reprocessar ofertas em observacao diaria.

## 5. Rodar local

```bash
pip install -r requirements.txt
python -m playwright install chromium --with-deps
export SUPABASE_URL="..."
export SUPABASE_SERVICE_KEY="..."
python scraper.py
```

No PowerShell:

```powershell
$env:SUPABASE_URL="..."
$env:SUPABASE_SERVICE_KEY="..."
python scraper.py
```

## Como funciona

1. Busca ofertas e campos de monitoramento no Supabase.
2. Filtra o lote do horario atual conforme `ads_monitoring_status`, `ads_zero_since` e `ads_next_check_at`.
3. Para cada oferta elegivel com `linkBiblioteca`:
   - abre Chromium headless;
   - navega na URL;
   - espera o JS renderizar o contador;
   - extrai via regex em PT/FR/EN/ES/DE/IT;
   - atualiza `anuncios_ativos`, historico e estado de monitoramento.
4. Reaproveita a mesma aba para todas as ofertas.

## Anti-bot

O Facebook pode pedir captcha em IPs muito agressivos. Se acontecer:

- reduzir a frequencia;
- adicionar espera aleatoria entre ofertas;
- em ultimo caso, usar proxy residencial.
