with source as (

    select * from {{ source('tfl', 'raw_line_status') }}

),

renamed as (

    select
        ingested_at,
        line_id,
        line_name,
        status_severity,
        status_severity_description as status_description,
        reason as disruption_reason

    from source

)

select * from renamed