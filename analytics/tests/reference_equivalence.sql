-- Compare every field and both directions on exactly the selected frozen publications.
with original as (
    select h.* from {{ source('portwatch','port_activity_hourly') }} h
    join {{ ref('stg_partitions') }} p using(partition_id)
), actual as (select * from {{ ref('port_activity_hourly') }})
select coalesce(a.partition_id,o.partition_id) as partition_id,
    coalesce(a.hour_utc,o.hour_utc) as hour_utc
from actual a full outer join original o using(partition_id,hour_utc)
where to_jsonb(a) is distinct from to_jsonb(o)
