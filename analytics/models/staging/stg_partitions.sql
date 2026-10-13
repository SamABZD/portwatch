-- Retain prior versions in public; select one latest publication per comparable daily series.
select distinct on (p.port_id,p.source_id,p.dataset_id,p.clean_version,p.geofence_version,
    p.assignment_version,p.rule_id) p.*, r.parameters,
    case when jsonb_array_length(r.parameters->'context_dataset_ids') > 0
        then array(select jsonb_array_elements_text(r.parameters->'context_dataset_ids'))
        else array[p.dataset_id] end as context_dataset_ids
from {{ source('portwatch','mart_partitions') }} p
join {{ source('portwatch','mart_rule_sets') }} r using(analytical_version)
where p.port_id='{{ var("port_id") }}'
    and p.start_utc >= '{{ var("start_utc") }}'::timestamptz
    and p.end_utc <= '{{ var("end_utc") }}'::timestamptz
    and r.parameters->>'weather_product'='historical_reanalysis'
order by p.port_id,p.source_id,p.dataset_id,p.clean_version,p.geofence_version,
    p.assignment_version,p.rule_id,p.computed_at desc,p.partition_id
