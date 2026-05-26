-- Monitoring cadence for ads active checks.
-- Run this in the Supabase SQL editor before deploying the scraper cadence update.

alter table public.ofertas
add column if not exists ads_monitoring_status text not null default 'active',
add column if not exists ads_zero_since timestamptz,
add column if not exists ads_last_positive_at timestamptz,
add column if not exists ads_inactivated_at timestamptz,
add column if not exists ads_next_check_at timestamptz;

alter table public.ofertas
alter column ads_monitoring_status set default 'active';

update public.ofertas
set ads_monitoring_status = 'active'
where ads_monitoring_status is null;

alter table public.ofertas
alter column ads_monitoring_status set not null;

alter table public.ofertas
drop constraint if exists ofertas_ads_monitoring_status_check;

alter table public.ofertas
add constraint ofertas_ads_monitoring_status_check
check (ads_monitoring_status in ('active', 'cooldown', 'inactive'));

create index if not exists idx_ofertas_ads_monitoring_status
on public.ofertas (ads_monitoring_status, ads_next_check_at);

with latest_positive as (
    select
        oferta_id,
        max(lido_em) as last_positive_at
    from public.oferta_ads_leituras
    where status = 'success'
      and anuncios_ativos > 0
    group by oferta_id
),
zero_windows as (
    select
        l.oferta_id,
        min(l.lido_em) as zero_since
    from public.oferta_ads_leituras l
    left join latest_positive p on p.oferta_id = l.oferta_id
    where l.status = 'success'
      and l.anuncios_ativos = 0
      and (p.last_positive_at is null or l.lido_em > p.last_positive_at)
    group by l.oferta_id
),
zero_candidates as (
    select
        o.id,
        coalesce(z.zero_since, o.anuncios_ativos_atualizado_em) as zero_since
    from public.ofertas o
    left join zero_windows z on z.oferta_id = o.id
    where coalesce(o.anuncios_ativos, 0) = 0
      and coalesce(z.zero_since, o.anuncios_ativos_atualizado_em) is not null
)
update public.ofertas o
set
    ads_zero_since = coalesce(o.ads_zero_since, z.zero_since),
    ads_monitoring_status = case
        when now() - coalesce(o.ads_zero_since, z.zero_since) >= interval '3 days' then 'inactive'
        when now() - coalesce(o.ads_zero_since, z.zero_since) >= interval '2 days' then 'cooldown'
        else 'active'
    end,
    ads_inactivated_at = case
        when now() - coalesce(o.ads_zero_since, z.zero_since) >= interval '3 days'
            then coalesce(o.ads_inactivated_at, now())
        else null
    end
from zero_candidates z
where o.id = z.id;

update public.ofertas
set
    ads_monitoring_status = 'active',
    ads_zero_since = null,
    ads_last_positive_at = coalesce(anuncios_ativos_atualizado_em, ads_last_positive_at),
    ads_inactivated_at = null,
    ads_next_check_at = null
where coalesce(anuncios_ativos, 0) > 0;
