import contextlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from auth_client import AuthClient, ClientError
from login_tasks import LoginTasks


class AccountRenewalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def client(self, expired=True):
        client=AuthClient.__new__(AuthClient)
        client.root=self.root
        client.file=self.root/'dummy-credential'
        client.file.write_bytes(b'test fixture only')
        client.credentials=SimpleNamespace(clear=Mock())
        client._read=Mock(return_value=dict(client_id='fixture',refresh_token='old-refresh',
            access_token='cached-access',access_expires_at=time.time()+600,
            connection_expires_at=time.time()+(-1 if expired else 86400)))
        client._save=Mock()
        client.json_request=Mock(return_value=dict(access_token='fresh-access',refresh_token='fresh-refresh',
            expires_in=600,connection_expires_at=time.time()+90*86400))
        return client

    def test_stale_connection_date_is_validated_and_updated_by_server(self):
        client=self.client()
        with patch('auth_client.mutex',return_value=contextlib.nullcontext()):
            self.assertEqual(client.access(),'fresh-access')
        client.json_request.assert_called_once()
        self.assertGreater(client._save.call_args.args[0]['connection_expires_at'],time.time()+89*86400)

    def test_valid_cached_access_remains_a_local_operation(self):
        client=self.client(expired=False)
        with patch('auth_client.mutex',return_value=contextlib.nullcontext()):
            self.assertEqual(client.access(),'cached-access')
        client.json_request.assert_not_called()

    def test_network_error_does_not_delete_credentials_or_start_login(self):
        client=self.client()
        client.json_request.side_effect=ClientError('offline','network_error')
        with patch('auth_client.mutex',return_value=contextlib.nullcontext()),self.assertRaises(ClientError) as caught:
            client.access()
        self.assertEqual(caught.exception.code,'network_error')
        self.assertTrue(client.file.exists())
        client.credentials.clear.assert_not_called()
        client._save.assert_not_called()

    def task_auth(self, result='renewed'):
        class Auth:
            def __init__(self):
                self.expiry=time.time()-1
                self.refresh_calls=0
                self.login_calls=0
            def identity(self):
                return dict(customer_id='fixture',username='fixture',connection_id='fixture',connection_expires_at=self.expiry)
            def access(self,force=False):
                self.refresh_calls+=1
                if result=='network':raise ClientError('offline','network_error')
                if result=='denied':raise ClientError('grant expired','login_required')
                self.expiry=time.time()+90*86400
                return 'fixture-access'
            def login(self,**kwargs):
                self.login_calls+=1
                self.expiry=time.time()+90*86400
                return self.identity()
        return Auth()

    def test_login_worker_reuses_renewed_grant_without_browser(self):
        auth=self.task_auth()
        with patch('login_tasks.mutex',return_value=contextlib.nullcontext()):
            tasks=LoginTasks(self.root,auth)
            task=tasks.start(spawn=False)
            self.assertEqual(auth.refresh_calls,0)
            tasks.run(task['login_id'])
        self.assertEqual(tasks.get(task['login_id'])['status'],'connected')
        self.assertEqual(auth.refresh_calls,1)
        self.assertEqual(auth.login_calls,0)

    def test_network_failure_does_not_fall_back_to_browser(self):
        auth=self.task_auth('network')
        with patch('login_tasks.mutex',return_value=contextlib.nullcontext()):
            tasks=LoginTasks(self.root,auth)
            task=tasks.start(spawn=False)
            tasks.run(task['login_id'])
        result=tasks.get(task['login_id'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error_code'],'network_error')
        self.assertEqual(auth.login_calls,0)

    def test_server_expiry_requires_a_fresh_browser_authorization(self):
        auth=self.task_auth('denied')
        with patch('login_tasks.mutex',return_value=contextlib.nullcontext()):
            tasks=LoginTasks(self.root,auth)
            task=tasks.start(spawn=False)
            tasks.run(task['login_id'])
        self.assertEqual(tasks.get(task['login_id'])['status'],'connected')
        self.assertEqual(auth.refresh_calls,1)
        self.assertEqual(auth.login_calls,1)


if __name__=='__main__':unittest.main()
