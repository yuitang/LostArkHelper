"""使用内存假接口检查提交边界；不连接游戏服务器。"""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
import automation
import helper
import store


class FakeAPI:
    SIGN_IN = 'sign'
    CLAIM = 'claim'
    VERIFIED = helper.VERIFIED
    ApiError = helper.ApiError

    def __init__(self, items, signed=True, uncertain=False, reject=False):
        self.items = items
        self.signed = signed
        self.uncertain = uncertain
        self.reject = reject
        self.calls = []

    def sign_status(self, config):
        return self.signed

    def tasks(self, config):
        return [dict(t) for t in self.items]

    def request(self, config, endpoint, extra=None):
        self.calls.append((endpoint, extra))
        if self.uncertain:
            raise helper.ApiError('模拟超时', 'timeout')
        if self.reject:
            raise helper.ApiError('查询任务信息失败！', 'outer_response', endpoint, -1)
        if endpoint == self.SIGN_IN:
            self.signed = True
            for t in self.items:
                if t['id'] == 7:
                    t['status'] = 1
        else:
            for t in self.items:
                if t['id'] == int(extra['task_id']):
                    t['status'] = 2


def task(tid, status):
    return {'id': tid, 'status': status, 'type': 3, 'name': str(tid)}


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # 拒绝计数是进程级的状态，测试之间必须清空，否则会互相屏蔽任务。
        automation.REJECTED.clear()

    def tearDown(self):
        self.temp.cleanup()

    def test_all_supported_and_inferred_claims_use_type_one(self):
        self.assertEqual(set(helper.VERIFIED), set(range(1, 14)))
        self.assertEqual(helper.INFERRED, set(range(7, 13)))
        api = FakeAPI([task(i, 1) for i in range(1, 14)])
        report = {}
        self.assertEqual(automation.run(api, {}, self.root, report=report), 0)
        self.assertEqual([c[1] for c in api.calls],
                         [{'task_id': str(i), 'type': '1'} for i in range(1, 14)])
        self.assertEqual(len(report['claimed']), 13)

    def test_incomplete_claimed_unknown_and_duplicate_skipped(self):
        api = FakeAPI([task(6, 0), task(7, 2), task(99, 1), task(8, 1), task(8, 1)])
        self.assertEqual(automation.run(api, {}, self.root), 0)
        self.assertFalse(api.calls)

    def test_check_only_never_submits(self):
        api = FakeAPI([task(6, 1)], signed=False)
        self.assertEqual(automation.run(api, {}, self.root, check_only=True), 0)
        self.assertFalse(api.calls)

    def test_uncertain_claim_stops_and_blocks_next_run(self):
        api = FakeAPI([task(6, 1), task(7, 1)], uncertain=True)
        report = {}
        self.assertEqual(automation.run(api, {}, self.root, report=report), 1)
        self.assertIn('更新 token', report['message'])
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(json.loads((self.root/'pending-operation.json').read_text())['action'], 6)
        with self.assertRaises(helper.ApiError):
            automation.run(api, {}, self.root)
        self.assertEqual(len(api.calls), 1)

    def test_uncertain_sign_prevents_all_rewards(self):
        api = FakeAPI([task(6, 1)], signed=False, uncertain=True)
        self.assertEqual(automation.run(api, {}, self.root), 1)
        self.assertEqual(api.calls, [('sign', None)])

    def test_recovered_confirmation_clears_pending(self):
        api = FakeAPI([task(6, 2)])
        automation.save_state(self.root/'pending-operation.json', {'action': 6})
        self.assertEqual(automation.run(api, {}, self.root), 0)
        self.assertFalse((self.root/'pending-operation.json').exists())
        self.assertFalse(api.calls)

    def test_new_sign_unlocks_milestone_in_same_run(self):
        api = FakeAPI([task(7, 0)], signed=False)
        self.assertEqual(automation.run(api, {}, self.root), 0)
        self.assertEqual(api.calls, [('sign', None), ('claim', {'task_id': '7', 'type': '1'})])

    def test_definitive_rejection_does_not_lock_the_account(self):
        # 服务端明确拒绝说明提交未生效，不能留下待确认记录，否则该角色会永久无法再执行。
        api = FakeAPI([task(6, 1)], reject=True)
        report = {}
        self.assertEqual(automation.run(api, {}, self.root, report=report), 1)
        self.assertIn('服务端明确拒绝', report['message'])
        self.assertFalse((self.root/'pending-operation.json').exists())
        # 下一次执行不应被「结果未确认」挡住。
        api2 = FakeAPI([task(6, 0)])
        self.assertEqual(automation.run(api2, {}, self.root), 0)

    def test_auto_run_claims_daily_and_weekly_but_not_activity(self):
        # 列表 type：1=每日 2=每周 3=活动累计。自动执行提交前两类，活动任务只提示。
        items = [dict(task(1, 1), type=1), dict(task(3, 1), type=2), dict(task(7, 1), type=3)]
        api = FakeAPI(items)
        report = {}
        self.assertEqual(automation.run(api, {}, self.root, report=report,
                                        auto_types=automation.AUTO_TASK_TYPES), 0)
        self.assertEqual([call[1]['task_id'] for call in api.calls], ['1', '3'])
        self.assertEqual([(s['id'], s['reason']) for s in report['skipped']], [(7, 'manual_only')])

    def test_repeatedly_rejected_task_is_not_resubmitted(self):
        # 服务器明确拒绝到阈值后进入冷却，避免调度器每 30 秒对同一任务反复试错。
        api = FakeAPI([task(3, 1)], reject=True)
        for _ in range(3):
            automation.run(api, {}, self.root)
        self.assertEqual(len(api.calls), automation.REJECT_LIMIT)

    def test_cooldown_expiry_retries_instead_of_giving_up(self):
        # 冷却到期后必须再试一次：任务可能只是「后来才变成可领取」，永久放弃会漏领。
        api = FakeAPI([task(3, 1)], reject=True)
        for _ in range(2):
            automation.run(api, {}, self.root)
        self.assertEqual(len(api.calls), 2)
        for entry in automation.REJECTED.values():
            entry[1] = 0
        automation.run(api, {}, self.root)
        self.assertEqual(len(api.calls), 3)

    def test_cooldown_cleared_when_credentials_are_updated(self):
        # 更新 token 就是为了让它能领到，冷却不能把这次修复挡住。
        api = FakeAPI([task(3, 1)], reject=True)
        for _ in range(2):
            automation.run(api, {}, self.root)
        self.assertTrue(automation.REJECTED)
        automation.clear_cooldowns(self.root)
        self.assertFalse(automation.REJECTED)
        automation.run(api, {}, self.root)
        self.assertEqual(len(api.calls), 3)

    def test_database_commits_rolls_back_and_closes(self):
        with store.connect(self.root) as db:
            db.execute("INSERT INTO settings VALUES (1, '{}')")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')
        with self.assertRaises(RuntimeError):
            with store.connect(self.root) as failed:
                failed.execute("UPDATE settings SET value='bad'")
                raise RuntimeError()
        with self.assertRaises(sqlite3.ProgrammingError):
            failed.execute('SELECT 1')
        with store.connect(self.root) as db:
            self.assertEqual(db.execute('SELECT value FROM settings').fetchone()[0], '{}')

    def test_cross_instance_lock(self):
        a, b = automation.OperationLock(self.root), automation.OperationLock(self.root)
        self.assertTrue(a.acquire())
        try:
            self.assertFalse(b.acquire())
        finally:
            a.release()
        self.assertTrue(b.acquire())
        b.release()


if __name__ == '__main__':
    unittest.main()
