select * from {{ source('portwatch','weather_values') }}
