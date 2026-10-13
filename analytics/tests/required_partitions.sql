-- A requested available interval must not silently pass as an empty model.
with expected as (
    select d as start_utc from generate_series('{{ var("start_utc") }}'::timestamptz,
        '{{ var("end_utc") }}'::timestamptz-interval '1 day',interval '1 day') d
)
select e.start_utc from expected e
left join {{ ref('stg_partitions') }} p on p.start_utc=e.start_utc
where p.partition_id is null
