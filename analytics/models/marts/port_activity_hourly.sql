{{ config(contract={'enforced':true},
    indexes=[{'columns':['partition_id','hour_utc'],'unique':true}]) }}
select m.partition_id,m.port_id,m.source_id,m.dataset_id,m.analytical_version,h.*
from {{ ref('stg_partitions') }} m
cross join lateral ({{ hourly_metrics() }}) h
