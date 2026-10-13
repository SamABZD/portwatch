select w.episode_id from {{ ref('int_waits') }} w
left join {{ ref('int_visits') }} v on v.mart_partition_id=w.mart_partition_id
    and v.partition_id=w.partition_id and v.visit_id=w.associated_visit_id
where w.associated_visit_id is not null and v.visit_id is null
