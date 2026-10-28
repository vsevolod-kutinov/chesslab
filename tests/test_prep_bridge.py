import json
import unittest

from chesslab.prep import bridge


def line(event):
    return json.dumps(event)


class ParseTest(unittest.TestCase):
    def test_tool_use_is_status(self):
        event = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": '"/x/python" -m chesslab.prep games "Muller, Tomas:1" --out .'}}]}}
        self.assertEqual(bridge.parse_event(line(event)), ("status", "games Muller, Tomas:1"))

    def test_links_are_hidden(self):
        event = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": 'py -m chesslab.prep lineup "https://chess-results.com/tnr1.aspx" "Humenne"'}}]}}
        self.assertEqual(bridge.parse_event(line(event)), ("status", "lineup Humenne"))

    def test_webfetch_is_status(self):
        event = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://s2.chess-results.com/tnr1.aspx"}}]}}
        self.assertEqual(bridge.parse_event(line(event)), ("status", "reading s2.chess-results.com"))

    def test_text_is_ignored(self):
        event = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
        self.assertIsNone(bridge.parse_event(line(event)))

    def test_result(self):
        self.assertEqual(bridge.parse_event(line({"type": "result", "is_error": False, "result": "ok"})),
                         ("answer", "ok"))
        self.assertEqual(bridge.parse_event(line({"type": "result", "is_error": True, "result": "boom"})),
                         ("error", "boom"))

    def test_garbage(self):
        self.assertIsNone(bridge.parse_event("not json"))


if __name__ == "__main__":
    unittest.main()
