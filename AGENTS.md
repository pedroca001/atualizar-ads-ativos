# AGENTS.md - atualizar-ads-ativos

## Purpose

- Papel do repo: fonte canonica do scraper Python/Playwright que alimenta o DR Vault com contagem de anuncios ativos.
- Stack / operacao: Python, Playwright, Supabase, GitHub Actions.
- Raiz de codigo: `<CODIGO_ROOT>\atualizar-ads-ativos`.

## Ownership

- Ficha canonica no Brain: `<BRAIN_ROOT>\Projetos\Webapps_Infoapps\atualizar_ads_ativos.md`.
- Doc relacionado: `<BRAIN_ROOT>\Projetos\Webapps_Infoapps\dr_vault_swipe.md`.
- Operacao/deploy: workflow GitHub Actions `Daily FB Ads Counter`.

## Local Contracts

- Comandos principais: `pip install -r requirements.txt`, `python -m playwright install chromium`, `python scraper.py`.
- Segredos proibidos: valores de `.env`, tokens, API keys, service keys, cookies, credenciais, exports privados, dados de cliente/membro e qualquer segredo de producao. Documente apenas nomes.
- Existe tambem `<CODIGO_ROOT>\atualizar_ads_ativos` apontando para o mesmo remote, mas ele e clone legado. Mudancas novas pertencem somente a esta pasta com hifen.
- O scraper mantem 14 dias em `oferta_ads_leituras`. O backfill por logs vive em `scripts/backfill_ads_history.py`, roda em dry-run por padrao e nunca altera a contagem atual da oferta.
- Preserve trabalho do usuario: nao use reset, checkout, delete ou moves em massa sem aprovacao explicita.
- Use caminhos portateis em docs: `<BRAIN_ROOT>` e `<CODIGO_ROOT>`, nunca drive/letra/usuario fixo.

## Work Guidance

- Antes de editar, leia `<BRAIN_ROOT>\AGENTS.md`, `<CODIGO_ROOT>\AGENTS.md`, este arquivo e a ficha canonica em Ownership.
- Siga padroes, scripts e estrutura ja existentes neste repo antes de criar abstracoes novas.
- Antes de deploy, rota, checkout, Worker, DNS, banco, auth ou automacao, confirme o alvo operacional na ficha Brain.
- Mantenha outputs, midias, caches, `node_modules`, builds e stores locais de credenciais fora do Git salvo regra explicita do repo.

## Verification

- Verificacao base: `python -m unittest discover -s tests -v`; depois rode uma checagem local controlada com env configurado. Nao acione automacao de producao sem conferir a ficha Brain.
- Para backfill, rode primeiro `python scripts/backfill_ads_history.py --days 14`; use `--apply` somente depois de validar a quantidade recuperavel e os slots sem log.
- No GitHub, valide os workflows `CI`, `Daily FB Ads Counter` e `Keep scheduled scraper active`. O keepalive deve pular o commit quando a branch principal tiver menos de 45 dias.
- Rode tambem qualquer teste, lint, build, typecheck, dry-run ou checagem manual mais estreita que combine com os arquivos tocados.
- Se a verificacao nao puder rodar, registre o bloqueio e o risco residual.

## Child DOX Index

- Nenhum `AGENTS.md` filho existe hoje. Este arquivo cobre o repo inteiro ate uma subpasta virar fronteira duravel propria.
