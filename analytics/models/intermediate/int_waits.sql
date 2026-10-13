select m.partition_id as mart_partition_id, w.*
from {{ ref('stg_partitions') }} m
join {{ source('portwatch','visit_inference_partitions') }} p
    on p.port_id=m.port_id and p.source_id=m.source_id and p.dataset_id=m.dataset_id
    and p.clean_version=m.clean_version and p.geofence_version=m.geofence_version
    and p.assignment_version=m.assignment_version and p.rule_id=m.rule_id
join {{ source('portwatch','waiting_episodes') }} w on w.partition_id=p.partition_id
