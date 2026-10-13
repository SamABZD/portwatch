select * from {{ source('portwatch','port_zones') }}
