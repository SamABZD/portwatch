-- Archive readiness is interval/processing coverage, never age relative to today's clock.
select m.partition_id,d.dataset_id
from {{ ref('stg_partitions') }} m
cross join lateral unnest(m.context_dataset_ids) d(dataset_id)
left join {{ ref('stg_batches') }} b on b.source_id=m.source_id and b.dataset_id=d.dataset_id
left join {{ ref('stg_clean_batches') }} c on c.raw_batch_id=b.batch_id
    and c.processing_version=m.clean_version
group by m.partition_id,d.dataset_id
having count(c.input_id)=0 or not bool_and(c.status='processed')
    or not bool_and(b.mode='historical')
