from app.agent.executor_node import extract_text_content


class TestExtractTextContent:
    def test_string_passthrough(self):
        assert extract_text_content("hello") == "hello"

    def test_none_to_empty(self):
        assert extract_text_content(None) == ""

    def test_anthropic_block_list(self):
        content = [{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}]
        assert extract_text_content(content) == "第一段\n第二段"

    def test_block_list_with_empty_and_other_types(self):
        content = [{"type": "text", "text": "x"}, {"type": "tool_use", "id": "t1"}, "raw"]
        assert extract_text_content(content) == "x\nraw"

    def test_non_text_object_fallback(self):
        assert extract_text_content({"foo": 1}) == "{'foo': 1}"
