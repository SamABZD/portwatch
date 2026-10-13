select p.partition_id from {{ ref('stg_partitions') }} p
left join {{ ref('port_activity_hourly') }} h using(partition_id)
group by p.partition_id,p.start_utc,p.end_utc
having count(h.hour_utc) <> extract(epoch from p.end_utc-p.start_utc)/3600
    or min(h.hour_utc)<>p.start_utc or max(h.hour_end_utc)<>p.end_utc
