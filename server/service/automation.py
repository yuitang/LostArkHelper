"""Hourly checks with a process-shared lock and durable mutation guard."""
import json
import os
import threading
import time
import store
from datetime import datetime, timezone
from pathlib import Path

# Windows 兼容：用 msvcrt.locking 实现与 fcntl.flock 等价的独占文件锁（仅本机单机部署场景）。
if os.name == 'nt':
    import errno
    import msvcrt

    LOCK_EX = 0
    LOCK_NB = 1

    def _flock(fd, flags):
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK if flags & LOCK_NB else msvcrt.LK_LOCK, 1)
        except OSError as exc:
            if flags & LOCK_NB:
                raise BlockingIOError(errno.EAGAIN, 'resource busy') from exc
            raise
else:
    import fcntl

    LOCK_EX = fcntl.LOCK_EX
    LOCK_NB = fcntl.LOCK_NB
    _flock = fcntl.flock


# 服务端明确拒绝的响应类型：这些情况下请求确实没有被受理，可以安全地清除待确认记录。
# 超时、连接中断、返回非 JSON 属于「结果未知」，必须保留待确认记录，避免重复领取。
DEFINITIVE = frozenset({'outer_response', 'business', 'auth_mismatch'})

# 自动执行允许领取的任务类别（列表里的 type 字段）：1=每日，2=每周。
# 领奖接口的 type 参数恒为 1，与任务类别无关——每日任务与每周任务都已验证；
# 3=活动累计（7~12 号）还没有自动领取的实证，继续只提示、不自动提交。
AUTO_TASK_TYPES = frozenset({1, 2})

# 同一任务被服务端明确拒绝到此次数后进入冷却，冷却到期再试一次；成功则计数清零。
# 调度器每 30 秒一轮，对参数不匹配的任务反复试错有触发风控的风险；
# 但永久放弃会让「后来才变成可领取」的任务永远漏领，所以只做冷却、不做拉黑。
REJECT_LIMIT = 2
REJECT_COOLDOWN = 6 * 3600
REJECTED = {}


class OperationLock:
    def __init__(self, root, filename='operations.lock'):
        self.path = Path(root) / filename
        self.thread_lock = threading.Lock()
        self.fd = None

    # 同时取得线程锁和操作系统文件锁；文件存在不等于锁被占用，必须看锁操作是否成功。
    def acquire(self, blocking=False):
        if not self.thread_lock.acquire(blocking=blocking):
            return False
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
            _flock(self.fd, LOCK_EX | (0 if blocking else LOCK_NB))
            return True
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            self.thread_lock.release()
            return False
        except Exception:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            self.thread_lock.release()
            raise

    # 关闭文件描述符即释放系统锁，然后允许同进程中的下一次操作。
    def release(self):
        os.close(self.fd)
        self.fd = None
        self.thread_lock.release()


# 先写临时文件并 fsync，再原子替换目标文件，减少中断留下半份 JSON 的风险。
def save_state(path, state):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temp), str(path))
    # 目录级 fsync 是 POSIX 的持久化手段；Windows 上 os.replace 已保证原子性，跳过该步骤。
    if os.name != 'nt':
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


# 一次完整执行：先查询，筛选可领取白名单任务，每次提交前后重新核对。结果未知立即停止，并用待确认记录阻止下次盲目重试。
# state_dir 指定待确认记录所在目录：多账号时每个角色各自一份，互不影响。
def run(helper, config, root, check_only=False, report=None, allow_sign=True, allow_claim=True,
        state_dir=None, auto_types=None):
    report = report if report is not None else {}
    report.update(sign="not_checked", claimed=[], skipped=[], message="")
    report['errors'] = []
    scope = str(Path(state_dir or root))

    # 领奖只针对状态 1 且在领取白名单内的任务；auto_types 非空时再限定任务类别。
    def claimable(source):
        return [t for t in source if t['status'] == 1 and t['id'] in helper.VERIFIED]

    def blocked(tid):
        entry = REJECTED.get((scope, tid))
        return bool(entry) and time.time() < entry[1]

    def ids(source):
        return sorted(set(t['id'] for t in source
                           if (auto_types is None or t['type'] in auto_types) and not blocked(t['id'])))

    report['stage'] = '查询签到状态'
    signed = helper.sign_status(config)
    report['sign'] = 'already_signed' if signed else 'unsigned'
    report['stage'] = '查询任务列表'
    items = helper.tasks(config)
    picked = claimable(items)
    candidates = ids(picked)
    unknown = sum(t['status'] == 1 and t['id'] not in helper.VERIFIED for t in items)
    print('CHECK signed={} claimable={} skipped_unsupported={}'.format(signed, candidates, unknown), flush=True)
    report['skipped'] = [{'id': t['id'], 'name': str(t.get('name', '未知任务'))[:100], 'reason': 'not_verified'}
                         for t in items if t['status'] == 1 and t['id'] not in helper.VERIFIED]
    report['skipped'] += [{'id': t['id'], 'name': str(t.get('name', '未知任务'))[:100], 'reason': 'manual_only'}
                          for t in picked if auto_types is not None and t['type'] not in auto_types]
    report['skipped'] += [{'id': t['id'], 'name': str(t.get('name', '未知任务'))[:100], 'reason': 'cooling_down'}
                          for t in picked if blocked(t['id'])]
    guard = Path(state_dir or root) / 'pending-operation.json'
    if guard.exists():
        pending = json.loads(guard.read_text(encoding='utf-8'))
        action = pending.get('action')
        matched = [t for t in items if t['id'] == action]
        confirmed = signed if action == 'sign' else len(matched) == 1 and matched[0]['status'] == 2
        if confirmed:
            guard.unlink()
        else:
            raise helper.ApiError('上次提交的结果仍未确认。请前往账号管理更新 token；成功保存后将解除待确认限制，再执行时会重新查询任务状态。', 'unconfirmed')
    if check_only:
        return 0

    # 提交一笔签到或奖励操作，并确认实际状态；结果未知时不能等同于失败后安全重试。
    # 返回 ok / rejected / unknown：只有 rejected（服务端明确拒绝）才能清除待确认记录，
    # 否则一次参数错误就会把该角色永久锁在「结果未确认」上，连签到都无法再执行。
    def mutate(action, send, confirm):
        label = '签到' if action == 'sign' else '领取任务 #{}'.format(action)
        report['stage'] = label + '提交'
        if report.get('run_id'):
            store.update(root, report['run_id'], report)
        save_state(guard, {'action': action, 'started': store.now().isoformat()})
        try:
            send()
            report['stage'] = label + '结果确认'
            if not confirm():
                raise helper.ApiError('接口返回成功，但刷新后的状态未确认成功', 'unconfirmed')
        except Exception as exc:
            info = error_info(exc, report['stage'])
            definitive = getattr(exc, 'kind', None) in DEFINITIVE
            info['summary'] += ('。服务端明确拒绝，本次提交未生效。' if definitive
                                else '。请前往账号管理更新 token 后重试。')
            report['errors'].append(info)
            if action == 'sign':
                report['sign'] = 'failed'
            if definitive:
                guard.unlink(missing_ok=True)
                if action != 'sign':
                    entry = REJECTED.setdefault((scope, action), [0, 0])
                    entry[0] += 1
                    if entry[0] >= REJECT_LIMIT:
                        entry[1] = time.time() + REJECT_COOLDOWN
                        print('COOLDOWN {}'.format(action), flush=True)
                return 'rejected'
            return 'unknown'
        guard.unlink()
        if action != 'sign':
            REJECTED.pop((scope, action), None)
        if action == 'sign':
            report['sign'] = 'signed_now'
        else:
            task = next(t for t in items if t['id'] == action)
            report['claimed'].append({'id': action, 'name': str(task.get('name', '未知任务'))[:100]})
        if report.get('run_id'):
            store.update(root, report['run_id'], report)
        print('CONFIRMED {}'.format(action), flush=True)
        return 'ok'

    if not signed and allow_sign:
        if mutate('sign', lambda: helper.request(config, helper.SIGN_IN),
                  lambda: helper.sign_status(config)) != 'ok':
            report['message'] = '；'.join(e['summary'] for e in report['errors'])
            return 1
        # 本次签到可能刚好解锁累计签到奖励，刷新后再筛选候选。
        items = helper.tasks(config)
        picked = claimable(items)
        candidates = ids(picked)
    for tid in candidates if allow_claim else []:
        report['stage'] = '领取任务 #{} 前查询'.format(tid)
        current = [t for t in helper.tasks(config) if t['id'] == tid]
        if len(current) != 1 or current[0]['status'] != 1:
            continue
        def confirmed():
            after = [t for t in helper.tasks(config) if t['id'] == tid]
            return len(after) == 1 and after[0]['status'] == 2
        outcome = mutate(tid, lambda: helper.request(config, helper.CLAIM,
                         {'task_id': str(tid), 'type': str(helper.VERIFIED[tid])}), confirmed)
        if outcome == 'unknown':
            # 结果未知：继续提交可能重复领取，必须停在原地等人工确认。
            break
        # 服务端明确拒绝某项任务时继续处理下一项，单项不支持不应中断整轮。
    report['stage'] = '执行完成'
    report['message'] = '；'.join(e['summary'] for e in report['errors'])
    return 1 if report['errors'] else 0


# 仅对经过 helper 脱敏边界的 ApiError 输出详情，未知异常只记录类型。
def error_info(exc, stage):
    # Only ApiError messages have passed the helper's redaction boundary.
    from helper import ApiError
    known = isinstance(exc, ApiError)
    kind = getattr(exc, 'kind', 'internal') if known else 'internal'
    message = str(exc) if known else '内部处理异常（{}）'.format(type(exc).__name__)
    code = getattr(exc, 'code', None) if known else None
    endpoint = getattr(exc, 'endpoint', None) if known else None
    summary = '{}：{}{}'.format(stage, message, ' [code={}]'.format(code) if code is not None else '')
    return dict(stage=stage, kind=kind, code=code, endpoint=endpoint, summary=summary)


# 账号级调度入口：持锁、检查到期、按时间槽去重、读取配置，最后持久化脱敏运行结果。
def finish_run(root, run_id, report, outcome, notify=None):
    # 先提交游戏执行结果，再独立尝试邮件；邮件失败不能导致再次提交游戏请求。
    store.update(root, run_id, report, outcome)
    if notify is not None:
        try:
            notify(run_id, report, outcome)
        except Exception:
            print('Email notification unavailable; game execution result retained.', flush=True)


def execute_user(root, helper, check_only=False, run_now=False, notify=None, account=None):
    """账号级调度入口：一次持锁，逐个游戏角色执行。

    每个角色独立保存凭据、待确认记录和运行记录行；某个角色失败不影响其他角色。
    时间槽按「分钟#角色ID」区分，避免同一次计划里多个角色互相去重覆盖。
    """
    import auth
    import accounts
    lock = auth.operation_lock(*account) if account else OperationLock(root)
    if not lock.acquire():
        if account:
            # 可能正被删除，此时不能绕过锁访问或创建账号数据库。
            print('SKIP: another account operation is running.', flush=True)
            return 0
        at = store.now()
        plans = store.account_plans(root, [item['id'] for item in accounts.list_accounts(root)])
        if any(store.due(plan, at) for plan in plans.values()):
            with store.connect(root) as db:
                db.execute("INSERT OR IGNORE INTO runs(slot,started,finished,outcome,details) VALUES (?,?,?,'skipped',?)",
                           (at.strftime('%Y-%m-%dT%H:%M'), at.isoformat(), at.isoformat(), json.dumps({'message':'其他操作正在执行，本次已跳过，避免重复提交。'})))
        print('SKIP: another operation is running.', flush=True)
        return 0
    try:
        if account and (not auth.user_exists(*account) or not root.is_dir()):
            return 0
        at = store.now()
        items = accounts.list_accounts(root)
        if not items:
            if check_only:
                return 0
            # 没有绑定角色时按用户默认计划每小时只记录一次跳过，避免重复刷历史。
            if store.due(store.settings(root), at):
                run_id = store.begin(root, at.strftime('%Y-%m-%dT%H:%M'))
                if run_id is not None:
                    store.update(root, run_id, {'check_only': False, 'trigger': 'scheduled',
                                                'message': '尚未绑定游戏账号，已跳过。'}, 'skipped')
            return 0
        # 每个角色有独立的开关与执行时间，逐个判断是否到期；某个账号暂停不影响其他账号。
        plans = store.account_plans(root, [item['id'] for item in items])
        codes = []
        for item in items:
            plan = plans[item['id']]
            if not check_only and (not plan['enabled'] or (not run_now and not store.due(plan, at))):
                continue
            if check_only and not accounts.exists(root, item['id']):
                continue
            report = {'check_only': check_only, 'trigger': 'scheduled_now' if run_now else 'scheduled',
                      'account': item}
            # 同一分钟内每个角色各占一个时间槽；手动立刻执行不写槽（允许重复手动触发）。
            slot = at.strftime('%Y-%m-%dT%H:%M') + '#' + item['id'] if not check_only and not run_now else None
            run_id = store.begin(root, slot)
            if run_id is None:
                continue
            report['run_id'] = run_id
            try:
                config = accounts.read_config(root, item['id'])
                if not isinstance(config, dict):
                    raise ValueError('账号凭据格式无效')
                # 该角色的待确认记录放在自己的目录里，不同角色之间不会互相阻塞。
                # 自动执行领每日与每周任务；活动任务（3）还没取得自动领取实证，仍只提示。
                code = run(helper, config, root, check_only, report, auto_types=AUTO_TASK_TYPES,
                           state_dir=accounts.account_dir(root, item['id']))
            except Exception as exc:
                error = error_info(exc, report.get('stage', '读取账号配置'))
                report.setdefault('errors', []).append(error)
                report['message'] = '；'.join(e['summary'] for e in report['errors'])
                finish_run(root, run_id, report, 'error', notify)
                print('ERROR: query/write failed for account {}; see private execution history.'.format(item['id']), flush=True)
                codes.append(1)
                continue
            finish_run(root, run_id, report, 'success' if code == 0 else 'error', notify)
            codes.append(code)
        return 1 if any(codes) else 0
    except Exception as exc:
        print('ERROR: scheduled run failed: {}'.format(type(exc).__name__), flush=True)
        return 1
    finally:
        lock.release()


def main():
    import argparse
    import helper
    import auth
    import notifications
    from concurrent.futures import ThreadPoolExecutor
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--run-now', action='store_true', help='Run enabled schedules once now')
    args = parser.parse_args()
    root = Path(os.environ.get('ARK_HELPER_DIR', '/var/lib/ark-dashboard'))
    os.umask(0o077)
    auth.initialize(root)
    users = auth.all_users(root)
    accounts = [(user, auth.user_root(root, user, create=False)) for user in users]
    # 在账号锁内判断到期和账号是否仍存在，避免列表快照中的已删除账号继续执行。
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda pair: execute_user(pair[1], helper, args.check_only, args.run_now,
                            notifications.callback(root, pair[0], pair[1]), account=(root, pair[0])), accounts))
    return 1 if any(code for code in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
