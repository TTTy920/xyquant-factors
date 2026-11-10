"""Strict inputs shared by the strategy-research protocol and business layer."""
import re
from datetime import date

TOOL = 'query_strategy_research'
FIELDS = frozenset({'strategy_id', 'sections', 'search', 'category', 'page', 'page_size',
                    'version', 'start', 'end', 'offset', 'limit'})
SECTIONS = frozenset({'info', 'performance', 'nav'})


def validate_query(data):
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError('invalid_request')
    sid, sections, version = data.get('strategy_id'), data.get('sections'), data.get('version')
    if sid is not None and (not isinstance(sid, str) or not re.fullmatch(r's_[a-f0-9]{12}', sid)):
        raise ValueError('invalid_request')
    if version is not None and (not isinstance(version, str) or not re.fullmatch(r'[a-f0-9]{32}', version)):
        raise ValueError('invalid_request')
    for key in ('search', 'category'):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError('invalid_request')
    for key, default, minimum, maximum in [('page', 1, 1, 1000000), ('page_size', 20, 1, 50),
                                            ('offset', 0, 0, 10000000), ('limit', 50, 1, 50)]:
        value = data.get(key, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError('invalid_request')
    for key in ('start', 'end'):
        value = data.get(key)
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
                raise ValueError('invalid_request')
            date.fromisoformat(value)
    if data.get('start') and data.get('end') and data['start'] > data['end']:
        raise ValueError('invalid_request')
    if sid is None:
        if sections is not None:
            raise ValueError('invalid_request')
    else:
        if sections is not None and (not isinstance(sections, list) or not 1 <= len(sections) <= 3
                or any(not isinstance(s, str) or s not in SECTIONS for s in sections)
                or len(set(sections)) != len(sections)):
            raise ValueError('invalid_request')
        if data.get('search', '') or data.get('category', '') or data.get('page', 1) != 1 or data.get('page_size', 20) != 20:
            raise ValueError('invalid_request')
    nav_requested = sid is not None and sections is not None and 'nav' in sections
    if not nav_requested and (data.get('start') is not None or data.get('end') is not None
            or data.get('offset', 0) != 0 or data.get('limit', 50) != 50):
        raise ValueError('invalid_request')
    if nav_requested and data.get('offset', 0) > 0 and version is None:
        raise ValueError('invalid_request')
