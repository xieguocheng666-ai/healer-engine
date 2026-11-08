def test_business_failure_is_not_counted_as_wrong_target():
    from demo_healing import summarize
    rows = [{'candidate': '#right', 'identity_correct': True, 'correct': False}]
    result = summarize(rows)
    assert result['false_heal_count'] == 0
    assert result['passed'] == 0


def test_wrong_target_is_counted_as_false_heal():
    from demo_healing import summarize
    rows = [{'candidate': '#wrong', 'identity_correct': False, 'correct': False}]
    assert summarize(rows)['false_heal_count'] == 1
