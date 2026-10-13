select m.partition_id,p.id from {{ ref('stg_partitions') }} m
join {{ ref('stg_positions') }} p on p.source_id=m.source_id
    and p.dataset_id=any(m.context_dataset_ids) and p.processing_version=m.clean_version
left join {{ ref('stg_assignments') }} a on a.position_id=p.id and a.port_id=m.port_id
    and a.geofence_version=m.geofence_version and a.assignment_version=m.assignment_version
where a.position_id is null
