"""Bounded deterministic operations over validated record and table ports."""
from datetime import datetime
from decimal import Decimal
from operator import eq, ge, le
import re

from .templates import FillError

SIGNATURES = {
    'group_latest': ({'records'}, {'key', 'order', 'distinct', 'compare'}),
    'reduce_coverage': ({'records'}, {'key', 'status', 'evidence', 'positive', 'unknown'}),
    'compare_constraints': ({'records', 'criteria'}, set()),
    'count_duplicates': ({'rows'}, set()),
}


def validate(operation, parameters, inputs):
    ports, keys = SIGNATURES[operation]
    if (set(inputs) != ports or not isinstance(parameters, dict) or set(parameters) != keys
            or any(not isinstance(value, str) or not value or len(value) > 100 for value in parameters.values())):
        raise ValueError('invalid record transform ports or parameters')


def group_latest(p, inputs):
    groups = {}
    for row in inputs['records']:
        key = row[p['key']]
        stamp = datetime.fromisoformat(row[p['order']])
        group = groups.setdefault(key, [stamp, {}, set()])
        if stamp > group[0]:
            group[0], group[1] = stamp, {}
        if stamp == group[0]:
            group[1].setdefault(row[p['compare']], row)
        if row[p['distinct']] is not None:
            group[2].add(row[p['distinct']])
    return [dict(key=key, latest=list(rows.values()), values=sorted(values))
            for key, (_, rows, values) in sorted(groups.items())]


def reduce_coverage(p, inputs):
    rows = inputs['records']
    if len({row[p['key']] for row in rows}) != len(rows):
        raise ValueError('duplicate criterion')
    rows = [{**row, p['status']: row[p['status']] if row[p['evidence']] else p['unknown']} for row in rows]
    return dict(rows=rows, complete=bool(rows) and all(row[p['status']] == p['positive'] for row in rows))


def decimal(value):
    if not re.fullmatch(r'-?[0-9]{1,20}(?:\.[0-9]{1,8})?', value):
        raise ValueError('expected a bounded decimal operand')
    return Decimal(value)


def compare_constraints(p, inputs):
    criteria = inputs['criteria']
    records = inputs['records']
    if any(len({row['id'] for row in values}) != len(values) for values in (records, criteria)):
        raise ValueError('duplicate record key')
    operators = {'lte': le, 'gte': ge, 'eq': eq}
    result = []
    for row in records:
        values = {item['field']: item['value'] for item in row['values']}
        if len(values) != len(row['values']):
            raise ValueError('duplicate operand')
        failed, unknown = [], []
        for criterion in criteria:
            value = values.get(criterion['field'])
            if value is None:
                unknown.append(criterion['id'])
            elif not operators[criterion['operator']](decimal(value), decimal(criterion['threshold'])):
                failed.append(criterion['id'])
        result.append(dict(id=row['id'], status='excluded' if failed else 'unknown' if unknown or not criteria
                           else 'eligible', failed=failed, unknown=unknown, preferences=row['preferences'], quote=row['quote']))
    return result


def count_duplicates(p, inputs):
    rows = inputs['rows']
    return len(rows) - len({tuple(row) for row in rows})


def execute(operation, parameters, inputs):
    operations = {fn.__name__: fn for fn in (group_latest, reduce_coverage, compare_constraints, count_duplicates)}
    try:
        return operations[operation](parameters, inputs)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise FillError('inputs', 'record transform inputs are incompatible') from None
