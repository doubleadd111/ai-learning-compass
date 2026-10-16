import json

import pytest

from desktop.gateway import GatewayError, ModelGateway
from desktop.models import ModelConnection
from desktop.planning import PlanningTurn
from tests.test_desktop_planning import draft, ready
from tests.test_desktop_schema import sdk


def long_outline():
    data = ready().model_dump()
    for point, length in zip(data['outline']['points'], (139, 114, 117)):
        point['summary'] = ('学习与实践并检查结果，逐步完成一个可运行的小作品。' * 8)[:length]
    return data


@pytest.mark.parametrize('wire', [False, True])
def test_screenshot_lengths_get_short_excerpts_without_losing_originals(wire):
    from desktop.planning import prepare_outline_excerpt
    data = long_outline()
    original = json.loads(json.dumps(data))
    if wire:
        data['outlines'] = [data.pop('outline')]
    text, count = prepare_outline_excerpt(json.dumps(data, ensure_ascii=False))
    value = json.loads(text)
    outline = value['outlines'][0] if wire else value['outline']
    assert count == 3 and sum(len(p['summary']) for p in outline['points']) == 100
    for old, new in zip(original['outline']['points'], outline['points']):
        assert 4 <= len(new['summary']) <= 40 and new['summary'].endswith('…')
        assert old['summary'] in new['content'] and old['content'] in new['content']
        assert len(new['content']) <= 500
        assert all(old[key] == new[key] for key in old if key not in {'summary', 'content'})
    if wire:
        from desktop.planning import ClarificationPayload
        assert ClarificationPayload.model_validate(value).to_turn().outline
    else:
        assert PlanningTurn.model_validate(value).outline


@pytest.mark.parametrize('case', ['valid', 'not_json', 'missing', 'short', 'too_many', 'overflow', 'ask', 'bad_content'])
def test_no_silent_bypass_when_text_cannot_be_preserved(case):
    from desktop.planning import prepare_outline_excerpt
    value = long_outline()
    if case == 'valid':
        value = ready().model_dump()
    elif case == 'not_json':
        value = 'invalid-json'
    elif case == 'missing':
        del value['outline']['points'][0]['summary']
    elif case == 'short':
        for point in value['outline']['points']:
            point['summary'] = '简短内容'
    elif case == 'too_many':
        value['outline']['points'] *= 2
    elif case == 'overflow':
        value['outline']['points'][1]['content'] = '保留原有内容' * 70
    elif case == 'ask':
        value['decision'] = 'ask'
    else:
        value['outline']['points'][0]['content'] = ''
    text = value if isinstance(value, str) else json.dumps(value)
    assert prepare_outline_excerpt(text) == (text, 0)


@pytest.mark.parametrize('protocol', ['responses', 'chat'])
def test_long_summary_acceptance_uses_one_mock_request_not_paid_repair(protocol):
    config = ModelConnection(name='mock', provider='DeepSeek', base_url='https://example.invalid', model='mock',
                             api_type=protocol, output_format='json_schema')
    data = long_outline()
    data['outlines'] = [data.pop('outline')]
    calls = []
    with sdk(config, [json.dumps(data)], calls, reject_unions=True) as client:
        result, event = ModelGateway(config, 'placeholder', client=client).clarify(draft())
    assert result.outline and len(calls) == 1 and event['metrics']['attempts'] == 1
    assert event['metrics']['summary_excerpted_count'] == 3


def test_excerpt_does_not_fix_other_invalid_fields():
    config = ModelConnection(name='mock', base_url='https://example.invalid', model='mock', api_type='responses')
    data = long_outline()
    data['outline']['points'][0]['rationale'] = ''
    calls = []
    with sdk(config, [json.dumps(data)] * 2, calls) as client:
        with pytest.raises(GatewayError, match='rationale'):
            ModelGateway(config, 'placeholder', client=client).clarify(draft())
    assert len(calls) == 2


@pytest.mark.parametrize('count', [3, 4, 5])
def test_excerpt_keeps_three_to_five_points_and_originals(count):
    from desktop.planning import prepare_outline_excerpt
    value = long_outline()
    points = value['outline']['points']
    while len(points) < count:
        points.append(dict(points[0], id=f'extra-{len(points)}'))
    text, changed = prepare_outline_excerpt(json.dumps(value))
    result = PlanningTurn.model_validate_json(text)
    assert changed == count and sum(len(p.summary) for p in result.outline.points) == 100
    assert all(old['summary'] in new.content and old['content'] in new.content
               for old, new in zip(points, result.outline.points))


def test_excerpt_and_safe_audit_survive_draft_restart(tmp_path):
    from desktop.planning import prepare_outline_excerpt
    from desktop.store import Store
    text, count = prepare_outline_excerpt(json.dumps(long_outline()))
    d = draft()
    d.receive_turn(PlanningTurn.model_validate_json(text))
    store = Store(tmp_path)
    d.events.append(store.planning_event(d.id, 'clarify', dict(metrics={
        'summary_excerpted_count': count, 'raw_output': 'must-not-save'})))
    store.save_draft(d)
    restored = Store(tmp_path).drafts()[0]
    assert restored.outline == d.outline
    assert restored.events[0]['metrics'] == {'summary_excerpted_count': 3}
    assert store.list_plans() == []
