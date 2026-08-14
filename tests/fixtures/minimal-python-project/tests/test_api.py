import unittest

from app.api import response


class ApiTests(unittest.TestCase):
    def test_response_uses_public_schema(self):
        self.assertEqual(response(), {"message": "hello"})


if __name__ == "__main__":
    unittest.main()
