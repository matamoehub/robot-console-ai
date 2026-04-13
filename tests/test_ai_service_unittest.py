import unittest

from app import APP


class AiServiceAppTest(unittest.TestCase):
    def setUp(self):
        self.client = APP.test_client()

    def test_version_endpoint(self):
        resp = self.client.get('/api/version')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['app'], 'robot-console-ai')

    def test_admin_redirects_without_login(self):
        resp = self.client.get('/admin')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers.get('Location', ''))


if __name__ == '__main__':
    unittest.main()
