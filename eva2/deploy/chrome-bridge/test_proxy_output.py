"""MCP compatibility regression checks; no credentials or browser needed."""
import ast
import json
import pathlib
import unittest

source = pathlib.Path(__file__).with_name('proxy.py').read_text()
namespace = {'json': json}
function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'normalize_tool_output')
exec(compile(ast.Module(body=[function], type_ignores=[]), 'proxy.py', 'exec'), namespace)

class OutputCompatibilityTests(unittest.TestCase):
    def normalize(self, result):
        return json.loads(namespace['normalize_tool_output'](json.dumps({'result': result}).encode()))['result']

    def test_observation_and_page_both_reach_eva(self):
        blocks = [{'type': 'text', 'text': 'Fresh Chrome observation: nonce'}, {'type': 'text', 'text': 'Page title: sample'}]
        result = self.normalize({'content': blocks})
        self.assertEqual(result['content'], blocks)
        self.assertEqual(result['structuredContent']['text'], 'Fresh Chrome observation: nonce\n\nPage title: sample')

    def test_errors_catalog_and_other_content_unchanged(self):
        for result in [{'isError': True, 'content': [{'type': 'text', 'text': 'blocked'}]}, {'content': [{'type': 'image', 'data': 'x'}]}, {'structuredContent': {'value': 1}}, {'tools': []}]:
            with self.subTest(result=result):
                self.assertEqual(self.normalize(result), result)

if __name__ == '__main__':
    unittest.main()