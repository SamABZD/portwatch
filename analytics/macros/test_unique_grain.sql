{% test unique_grain(model, columns) %}
select {{ columns | join(', ') }},count(*)
from {{ model }}
group by {{ columns | join(', ') }}
having count(*)>1
{% endtest %}
