import os
import unittest

import requests
from dotenv import load_dotenv


load_dotenv()


class TestSurfConnection(unittest.TestCase):
    def setUp(self):
        self.api_key = os.getenv("SURF_API_KEY")
        self.base_url = os.getenv("SURF_API_BASE", "https://willma.surf.nl/api/v0")

        if not self.api_key:
            self.skipTest("SURF_API_KEY is not set")

        self.headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

    def test_can_list_text_sequences(self):
        response = requests.get(
            f"{self.base_url}/sequences",
            headers=self.headers,
            timeout=10,
        )

        self.assertEqual(response.status_code, 200, response.text)

        sequences = response.json()
        self.assertIsInstance(sequences, list)

        text_sequences = [
            sequence
            for sequence in sequences
            if sequence.get("sequence_type") == "text"
        ]

        self.assertGreater(len(text_sequences), 0)
        self.assertTrue(
            all("name" in sequence and "description" in sequence for sequence in text_sequences)
        )


if __name__ == "__main__":
    unittest.main()
