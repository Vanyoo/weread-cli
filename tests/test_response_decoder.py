import json
import unittest
from pathlib import Path

from weread_cli.browser import CDPBrowserManager
from weread_cli.response_decoder import decode_response, html_to_text


class ResponseDecoderTest(unittest.TestCase):
    def test_decodes_known_weread_response_framing(self):
        capture = json.loads(Path('/tmp/weread-capture.json').read_text())
        decoded = decode_response(capture[0]['response'])
        self.assertIn('见了狼嗥', decoded.text)
        self.assertIn(decoded.payload_start, range(24, 33))
        self.assertGreater(decoded.decoded_bytes, 3000)

    def test_headless_browser_is_default(self):
        manager = CDPBrowserManager({})
        self.assertEqual(manager.mode, "headless")

    def test_html_to_text_keeps_chinese_paragraphs(self):
        text = html_to_text('<p class="content">山上的小屋</p><p>测试</p>')
        self.assertEqual(text, '山上的小屋 测试')


if __name__ == '__main__':
    unittest.main()
