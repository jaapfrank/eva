import base64
import io
import unittest

from fastapi import HTTPException
from PIL import Image

from eva_brain.service import BytesReq, _open_b64, faces_b64


class ImagePayloadValidationTest(unittest.TestCase):
    def assert_payload_error(self, value, code):
        with self.assertRaises(HTTPException) as caught:
            faces_b64(BytesReq(image_b64=value))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail['code'], code)

    def test_empty_payload_is_controlled_422(self):
        self.assert_payload_error('', 'empty_image')

    def test_invalid_base64_is_controlled_422(self):
        self.assert_payload_error('geen geldige base64!', 'invalid_base64')

    def test_non_image_bytes_are_controlled_422(self):
        value = base64.b64encode(b'dit is geen afbeelding').decode('ascii')
        self.assert_payload_error(value, 'invalid_image')

    def test_valid_image_is_fully_decoded(self):
        stream = io.BytesIO()
        Image.new('RGB', (2, 3), color=(10, 20, 30)).save(stream, format='PNG')
        image = _open_b64(base64.b64encode(stream.getvalue()).decode('ascii'))
        self.assertEqual(image.mode, 'RGB')
        self.assertEqual(image.size, (2, 3))


if __name__ == '__main__':
    unittest.main()
