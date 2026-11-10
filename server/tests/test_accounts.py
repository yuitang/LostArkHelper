"""多游戏账号：迁移、按角色 ID 区分、添加/解绑接口与调度隔离。"""
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
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
import accounts
import app
import auth
import automation
import helper
import store
from test_import import account_dir, account_guard, sample_link, write_account


class FakeGame:
    """假接口：只返回状态，不访问游戏服务。"""
    VERIFIED = helper.VERIFIED
    ApiError = helper.ApiError

    @staticmethod
    def sign_status(config):
        return True

    @staticmethod
    def tasks(config):
        return [dict(id=2, type=1, name='测试任务', status=2)]


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / 'panel.key').write_text('PrivateOwnerPassword123')
        auth.initialize(self.base)
        self.user = auth.create_user(self.base, 'tester', 'PrivateUserPassword123')
        self.root = auth.user_root(self.base, self.user)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(app, 'ROOT', self.base))
        self.stack.enter_context(patch.object(app.Handler, 'current_user', return_value=self.user))
        self.login = self.stack.enter_context(patch.object(helper, 'validate_login', return_value=None))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.stack.enter_context(patch.object(app, 'PORT', self.server.server_address[1]))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1])
        try:
            connection.request('GET' if body is None else 'POST', path,
                               None if body is None else json.dumps(body),
                               {'Origin': 'http://127.0.0.1:' + str(app.PORT),
                                'Content-Type': 'application/json', 'X-Ark-Request': '1'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_legacy_single_account_is_migrated_to_role_directory(self):
        # 旧版把凭据和待确认记录放在用户目录根下，升级后必须原地迁移而不是丢弃。
        config = app.importer.parse_link(sample_link())[0]
        (self.root / 'config-dashboard.json').write_text(json.dumps(config), encoding='utf-8')
        (self.root / 'pending-operation.json').write_text('{"action": 2}', encoding='utf-8')
        items = accounts.list_accounts(self.root)
        self.assertEqual([item['id'] for item in items], ['456'])
        self.assertFalse((self.root / 'config-dashboard.json').exists())
        self.assertFalse((self.root / 'pending-operation.json').exists())
        self.assertTrue((account_dir(self.root) / 'config-dashboard.json').is_file())
        self.assertTrue(account_guard(self.root).exists())
        # 重复调用不会再次移动或覆盖。
        self.assertEqual(accounts.list_accounts(self.root), items)

    def test_accounts_are_keyed_by_role_id_and_index_has_no_credentials(self):
        first = app.importer.parse_link(sample_link())[0]
        second = app.importer.parse_link(sample_link(roleId='888', roleName='第二角色'))[0]
        accounts.save(self.root, second, dict(role='第二角色', server='卡丹', area='卢佩恩'))
        accounts.save(self.root, first, dict(role='测试角色', server='卡丹', area='卢佩恩', added='2026-01-01T00:00:00'))
        items = accounts.list_accounts(self.root)
        # 按角色 ID 排序，展示信息来自索引，更新不覆盖首次添加时间。
        self.assertEqual([item['id'] for item in items], ['456', '888'])
        self.assertEqual(items[0]['role'], '测试角色')
        self.assertEqual(items[0]['added'], '2026-01-01T00:00:00')
        index = (self.root / accounts.INDEX).read_text(encoding='utf-8')
        for secret in ('ExampleToken', 'ExampleAccess', 'cookie', 'openid'):
            self.assertNotIn(secret, index)

    def test_api_add_list_status_and_unbind_multiple_accounts(self):
        self.assertEqual(self.request('/api/accounts')[1]['accounts'], [])
        self.assertEqual(self.request('/api/status')[1]['configured'], False)

        status, data = self.request('/api/accounts/import', dict(url=sample_link()))
        self.assertEqual(status, 200)
        self.assertTrue(data['created'])
        self.assertEqual(data['account']['id'], '456')

        # 第二个角色可以直接新增；同名角色再次提交需要确认更新。
        status, data = self.request('/api/accounts/import', dict(url=sample_link(roleId='888', roleName='第二角色')))
        self.assertEqual(status, 200)
        self.assertTrue(data['created'])
        self.assertEqual(self.request('/api/accounts/import', dict(url=sample_link()))[0], 409)
        self.assertEqual(self.request('/api/accounts/import', dict(url=sample_link(), replace=True))[0], 200)

        listed = self.request('/api/accounts')[1]['accounts']
        self.assertEqual([item['id'] for item in listed], ['456', '888'])
        self.assertEqual(len(self.request('/api/accounts')[1]['accounts']), 2)

        # 多账号时状态查询必须显式指定，避免误读其他角色。
        status = self.request('/api/status')[1]
        self.assertFalse(status['configured'])
        self.assertIn('选择', status['error'])
        with patch.object(helper, 'sign_status', return_value=True), \
             patch.object(helper, 'tasks', return_value=[dict(id=1, type=1, name='任务', status=1)]):
            status = self.request('/api/status?account=888')[1]
        self.assertTrue(status['configured'])
        self.assertEqual(status['account']['id'], '888')
        self.assertEqual(status['source'], '第二角色')

        # 解绑只删除该角色的凭据目录，其他角色和运行历史不受影响。
        run_id = store.begin(self.root, None)
        store.update(self.root, run_id, dict(trigger='manual'), 'success')
        status, data = self.request('/api/accounts/delete', dict(id='456'))
        self.assertEqual(status, 200)
        self.assertEqual([item['id'] for item in data['accounts']], ['888'])
        self.assertFalse(account_dir(self.root, '456').exists())
        self.assertTrue((account_dir(self.root, '888') / 'config-dashboard.json').is_file())
        self.assertEqual(len(store.history(self.root)['items']), 1)
        self.assertEqual(self.request('/api/accounts/delete', dict(id='456'))[0], 404)

    def test_unbind_rejects_unknown_or_invalid_ids(self):
        write_account(self.root)
        # 少了 id、空串和非字符串直接拒绝；格式合法但不存在的角色返回 404。
        for value in ('', 888, None):
            self.assertEqual(self.request('/api/accounts/delete', dict(id=value))[0], 400)
        for value in ('999', '../888', '45 6'):
            self.assertEqual(self.request('/api/accounts/delete', dict(id=value))[0], 404)
        self.assertTrue((account_dir(self.root) / 'config-dashboard.json').is_file())
        with self.assertRaises(accounts.AccountError):
            accounts.account_dir(self.root, '../etc')
        with self.assertRaises(accounts.AccountError):
            accounts.read_config(self.root, '999')

    def test_scheduler_runs_each_account_independently(self):
        write_account(self.root)
        write_account(self.root, link=sample_link(roleId='888', roleName='第二角色'), role_id='888')
        account_guard(self.root, '888').write_text('{"action": 5}')
        store.settings(self.root, dict(store.DEFAULT, enabled=True, minute=store.now().minute))
        # 角色 888 有待确认记录 → 该角色报错；角色 456 正常执行，互不阻塞。
        self.assertEqual(automation.execute_user(self.root, FakeGame, run_now=True), 1)
        runs = store.history(self.root)['items']
        self.assertEqual(sorted(item['details']['account']['id'] for item in runs), ['456', '888'])
        outcomes = {item['details']['account']['id']: item['outcome'] for item in runs}
        self.assertEqual(outcomes['456'], 'success')
        self.assertEqual(outcomes['888'], 'error')
        # 每个角色各自的时间槽，同一次计划不会互相覆盖。
        with store.connect(self.root) as db:
            slots = [row['slot'] for row in db.execute('SELECT slot FROM runs ORDER BY id')]
        self.assertEqual(len(slots), 2)
        self.assertIsNone(slots[0])
        self.assertIsNone(slots[1])

    def test_scheduled_slots_include_role_id_to_avoid_deduplication(self):
        write_account(self.root)
        write_account(self.root, link=sample_link(roleId='888', roleName='第二角色'), role_id='888')
        store.settings(self.root, dict(store.DEFAULT, enabled=True, minute=store.now().minute))
        self.assertEqual(automation.execute_user(self.root, FakeGame), 0)
        with store.connect(self.root) as db:
            slots = sorted(row['slot'] for row in db.execute('SELECT slot FROM runs'))
        self.assertEqual(len(slots), 2)
        self.assertTrue(all(slot.endswith('#456') or slot.endswith('#888') for slot in slots))
        # 同一分钟重复调度不会重复提交。
        self.assertEqual(automation.execute_user(self.root, FakeGame), 0)
        with store.connect(self.root) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
