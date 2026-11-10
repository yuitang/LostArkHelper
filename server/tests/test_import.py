"""导入与待确认恢复回归；使用虚构凭据和假接口，不访问游戏服务。"""
from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
import accounts
import app
import auth
import automation
import helper
import import_link



def sample_link(**updates):
    values = dict(token='ExampleToken', userId='123', appOpenid='ExampleOpenid',
                  appid='102037747', roleId='456', serverId='4', areaId='50',
                  uin='123456', accType='qc', accessToken='ExampleAccess',
                  access_token='ExampleAccess', serverName='卡丹', areaName='卢佩恩', roleName='测试角色')
    values.update(updates)
    return 'https://lostark.qq.com/cp/a20260818stjb/h5.html?' + urlencode(values)


def account_dir(root, role_id='456'):
    return Path(root) / 'accounts' / role_id


def write_account(root, link=None, config=None, role_id='456'):
    """按多账号结构写入凭据：accounts/<角色ID>/config-dashboard.json。"""
    folder = account_dir(root, role_id)
    folder.mkdir(parents=True, exist_ok=True)
    value = config if config is not None else import_link.parse_link(link or sample_link())[0]
    text = value if isinstance(value, str) else json.dumps(value)
    (folder / 'config-dashboard.json').write_text(text, encoding='utf-8')
    return folder


def account_guard(root, role_id='456'):
    return account_dir(root, role_id) / 'pending-operation.json'


class ImportTests(unittest.TestCase):
    def test_parameter_names_case_insensitive_values_unchanged(self):
        for transform in (lambda k:k, str.lower, str.upper, str.swapcase):
            from urllib.parse import parse_qsl
            link = 'https://lostark.qq.com/cp/test/h5.html?' + urlencode([
                (transform(k), v) for k, v in parse_qsl(sample_link().split('?', 1)[1])])
            config, info = import_link.parse_link(link)
            self.assertEqual(config['form']['token'], 'ExampleToken')
            self.assertEqual(config['form']['access_token'], 'ExampleAccess')
            self.assertEqual(config['form']['login_type'], 'qc')
            self.assertEqual(info, dict(server='卡丹', area='卢佩恩', role='测试角色'))

    def test_identical_duplicates_accepted_without_changing_values(self):
        expected = import_link.parse_link(sample_link())
        for suffix in ('&acctype=qc', '&TOKEN=ExampleToken', '&ACCESSTOKEN=ExampleAccess', '&accType=qc&ACCTYPE=qc', '&token=%45xampleToken'):
            with self.subTest(suffix=suffix):
                self.assertEqual(import_link.parse_link(sample_link() + suffix), expected)

    def test_case_alias_conflicts_and_conflicting_access_rejected(self):
        for suffix in ('&acctype=wx', '&TOKEN=exampletoken', '&USERID=999', '&ACCESSTOKEN=DifferentAccess', '&acctype='):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                import_link.parse_link(sample_link() + suffix)
        for updates in (dict(accType='wx'), dict(access_token='OtherAccess'), dict(token='invalid value')):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                import_link.parse_link(sample_link(**updates))

    def test_pending_error_invites_token_update(self):
        class Fake:
            VERIFIED = helper.VERIFIED
            ApiError = helper.ApiError
            @staticmethod
            def sign_status(config): return True
            @staticmethod
            def tasks(config): return [dict(id=2, status=1)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            automation.save_state(root/'pending-operation.json', dict(action=2))
            with self.assertRaisesRegex(helper.ApiError, '更新 token'):
                automation.run(Fake, {}, root)
            self.assertTrue((root/'pending-operation.json').exists())

    def test_multi_account_add_update_pending_recovery_and_isolation(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            stack.enter_context(patch.object(app, 'ROOT', root))
            login = stack.enter_context(patch.object(helper, 'validate_login', return_value=None))
            user = auth.create_user(root, 'test', 'PrivateTestPassword123', legacy=True)
            stack.enter_context(patch.object(app.Handler, 'current_user', return_value=user))
            # 已绑定角色 456；另一个角色 777 的待确认记录不能被 456 的导入清除。
            write_account(root)
            guard = account_guard(root)
            other_guard = account_guard(root, '777')
            other_guard.parent.mkdir(parents=True)
            other_guard.write_text('{"action": 7}')
            server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            stack.enter_context(patch.object(app, 'PORT', server.server_address[1]))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            def call(body):
                c = http.client.HTTPConnection('127.0.0.1', app.PORT)
                try:
                    c.request('POST', '/api/import', json.dumps(body), {
                        'Origin': 'http://127.0.0.1:' + str(app.PORT),
                        'Content-Type': 'application/json', 'X-Ark-Request': '1'})
                    r = c.getresponse()
                    return r.status, json.loads(r.read())
                finally:
                    c.close()
            def saved_config(role='456'):
                return json.loads((account_dir(root, role)/'config-dashboard.json').read_text(encoding='utf-8'))
            try:
                # 同一角色未勾选确认时拒绝更新，也不清除待确认记录。
                guard.write_text('{"action": 2}')
                self.assertEqual(call(dict(url=sample_link()))[0], 409)
                self.assertTrue(guard.exists())
                # 勾选确认后更新同一角色：清除自己的记录，不影响其他角色。
                status, data = call(dict(url=sample_link(token='NewToken'), replace=True))
                self.assertEqual(status, 200)
                self.assertTrue(data['pending_cleared'])
                self.assertIn('限制已解除', data['message'])
                self.assertFalse(guard.exists())
                self.assertTrue(other_guard.exists())
                self.assertEqual(saved_config()['form']['token'], 'NewToken')
                # 新增不同角色作为新账号，不覆盖已有账号，也不清除它的待确认记录。
                guard.write_text('{"action": 2}')
                status, data = call(dict(url=sample_link(roleId='888', roleName='第二角色')))
                self.assertEqual(status, 200)
                self.assertTrue(data['created'])
                self.assertEqual([a['id'] for a in accounts.list_accounts(root)], ['456', '888'])
                self.assertTrue(guard.exists())
                self.assertEqual(saved_config('888')['form']['sRoleld'], '888')
                # 同一角色但换了 QQ 身份：拒绝更新，不能偷偷换号。
                for body in (dict(url=sample_link(userId='999', replace=True)),
                             dict(url=sample_link(appOpenid='OtherOpenid', replace=True)),
                             dict(url='https://invalid.example/', replace=True)):
                    before = (account_dir(root)/'config-dashboard.json').read_text(encoding='utf-8')
                    self.assertEqual(call(body)[0], 400 if body['url'].startswith('https://invalid') else 409)
                    self.assertEqual((account_dir(root)/'config-dashboard.json').read_text(encoding='utf-8'), before)
                    self.assertTrue(guard.exists())
                # 过期或网络校验失败不能写入新凭据，也不能清除待确认记录。
                for kind, expected in (('auth_invalid', 400), ('auth_mismatch', 400), ('auth_check', 503)):
                    guard.write_text('{"action": 2}')
                    login.side_effect = helper.ApiError('请更新 token', kind)
                    status, data = call(dict(url=sample_link(), replace=True))
                    self.assertEqual(status, expected)
                    self.assertIn('更新 token', data['error'])
                    self.assertTrue(guard.exists())
                    self.assertEqual(saved_config()['form']['token'], 'NewToken')
                login.side_effect = None
                # 磁盘写入失败时保留保护，不产生半个账号。
                guard.write_text('{"action": 2}')
                with patch.object(automation, 'save_state', side_effect=OSError('simulated write failure')):
                    self.assertEqual(call(dict(url=sample_link(roleId='999', roleName='第三角色')))[0], 500)
                self.assertFalse(accounts.exists(root, '999'))
                self.assertTrue(guard.exists())
                # 解除全部角色后首次导入仍会初始化计划。
                shutil.rmtree(account_dir(root, '456'))
                shutil.rmtree(account_dir(root, '888'))
                self.assertEqual(call(dict(url=sample_link()))[0], 200)
                self.assertEqual([a['id'] for a in accounts.list_accounts(root)], ['456'])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

if __name__ == '__main__':
    unittest.main()
