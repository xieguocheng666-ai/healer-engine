from agent.analyzer import Analyzer, _extract_json_object


def test_extract_json_object_plain():
    assert _extract_json_object('{"a":1}') == {"a": 1}


def test_extract_json_object_with_code_block():
    assert _extract_json_object('```json\n{"a":1}\n```') == {"a": 1}


def test_fuse_selector_issue_consistent_high_confidence():
    a = Analyzer(client=None)
    text = {"category": "选择器问题"}
    vision = {"rendered": True, "target_visible": True}
    category, conf, review = a._fuse(text, vision)
    assert category == "选择器问题"
    assert conf == "high"
    assert review is False


def test_fuse_product_bug_consistent_high_confidence():
    a = Analyzer(client=None)
    text = {"category": "产品 bug"}
    vision = {"rendered": True, "target_visible": False}
    _, conf, review = a._fuse(text, vision)
    assert conf == "high"
    assert review is False


def test_fuse_conflict_needs_review():
    a = Analyzer(client=None)
    text = {"category": "选择器问题"}
    vision = {"rendered": True, "target_visible": False}
    _, conf, review = a._fuse(text, vision)
    assert conf == "low"
    assert review is True


def test_fuse_no_vision_needs_review():
    a = Analyzer(client=None)
    text = {"category": "选择器问题"}
    _, conf, review = a._fuse(text, None)
    assert conf == "medium"
    assert review is True
