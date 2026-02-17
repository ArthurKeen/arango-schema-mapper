from schema_analyzer.utils import extract_first_json_object


def test_extract_first_json_object_simple():
    txt = '{"a": 1}'
    assert extract_first_json_object(txt) == txt


def test_extract_first_json_object_with_preamble():
    txt = "hello\n\n{ \"a\": {\"b\": 2}, \"c\": 3 }\nbye"
    assert extract_first_json_object(txt).strip().startswith("{")
    assert extract_first_json_object(txt).strip().endswith("}")


def test_extract_first_json_object_handles_strings():
    txt = 'preamble {"a": "x}y", "b": 1} post'
    assert extract_first_json_object(txt) == '{"a": "x}y", "b": 1}'

