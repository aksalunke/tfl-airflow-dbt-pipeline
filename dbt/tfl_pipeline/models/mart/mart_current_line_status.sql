with ranked as (

    select
        line_id,
        line_name,
        status_severity,
        status_description,
        disruption_reason,
        ingested_at,
        dense_rank() over (
            partition by line_id
            order by ingested_at desc
        ) as recency_rank

    from {{ ref('stg_line_status') }}

)

select
    line_id,
    line_name,
    status_severity,
    status_description,
    disruption_reason,
    ingested_at

from ranked
where recency_rank = 1