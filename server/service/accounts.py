#!/usr/bin/env python3
"""多游戏账号存储：每个用户目录下按游戏角色 ID（roleId）分目录保存。

目录结构（均在用户私有目录内，绝不进入发行包）：
  accounts.json                    账号索引（仅展示信息，不含凭据）
  accounts/<roleId>/config-dashboard.json   该角色的登录凭据
  accounts/<roleId>/pending-operation.json  该角色的待确认记录

账号唯一标识使用官方分享链接解析出的 sRoleld（游戏角色 ID），只接受数字，
因此可以直接作为目录名，不需要转义。旧版单账号文件由 migrate() 原地迁移。
"""
import json
import os
import re
import shutil
from pathlib import Path

CONFIG = 'config-dashboard.json'
PENDING = 'pending-operation.json'
INDEX = 'accounts.json'
FOLDER = 'accounts'


class AccountError(ValueError):
    """账号不存在、标识非法或凭据结构无效。"""


def valid_id(value):
    """角色 ID 只接受数字，避免路径穿越；长度上限防止异常输入。"""
    return isinstance(value, str) and re.fullmatch(r'[0-9]{1,20}', value or '') is not None


def _accounts_dir(root):
    return Path(root) / FOLDER


def account_dir(root, account_id):
    if not valid_id(account_id):
        raise AccountError('账号标识无效。')
    return _accounts_dir(root) / account_id


def _index_file(root):
    return Path(root) / INDEX


def _read_index(root):
    file = _index_file(root)
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if valid_id(key) and isinstance(value, dict)}


def _write_index(root, data):
    """原子写入索引；索引只是展示用元数据，丢了可以重建，不含凭据。"""
    from automation import save_state
    save_state(_index_file(root), data)


def _clean_meta(value):
    """只保留可展示字段并截断长度，索引里不能出现 cookie 或 token。"""
    source = value if isinstance(value, dict) else {}
    text = lambda key: str(source.get(key) or '')[:100]
    return {'role': text('role'), 'server': text('server'), 'area': text('area'),
            'added': text('added')}


def migrate(root):
    """把旧版单账号文件迁移到 accounts/<roleId>/，可重复调用。

    旧版把凭据直接放在用户目录根下，迁移后旧路径不再存在，账号仍以同一角色 ID 出现。
    """
    root = Path(root)
    legacy = root / CONFIG
    if not legacy.exists():
        return
    try:
        config = json.loads(legacy.read_text(encoding='utf-8'))
        account_id = str(config['form']['sRoleld'])
        if not valid_id(account_id):
            return
    except (OSError, ValueError, KeyError, TypeError):
        # 旧文件损坏时保持原样，交给上层按“未配置”处理，避免误删用户数据。
        return
    target = account_dir(root, account_id)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_file = target / CONFIG
    if config_file.exists():
        # 新路径已有凭据时保留新的，旧文件是重复副本，直接移除以免两处不一致。
        legacy.unlink(missing_ok=True)
    else:
        os.replace(str(legacy), str(config_file))
    guard = root / PENDING
    if guard.exists() and not (target / PENDING).exists():
        os.replace(str(guard), str(target / PENDING))
    elif guard.exists():
        guard.unlink(missing_ok=True)
    index = _read_index(root)
    if account_id not in index:
        index[account_id] = {'role': '', 'server': '', 'area': '', 'added': ''}
        _write_index(root, index)


def list_accounts(root):
    """列出已绑定账号，按角色 ID 排序；索引缺失时用角色 ID 兜底显示。"""
    root = Path(root)
    migrate(root)
    folder = _accounts_dir(root)
    if not folder.is_dir():
        return []
    index = _read_index(root)
    items = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or not valid_id(entry.name) or not (entry / CONFIG).is_file():
            continue
        meta = _clean_meta(index.get(entry.name, {}))
        items.append({'id': entry.name, 'role': meta['role'] or '角色 ' + entry.name,
                      'server': meta['server'], 'area': meta['area'], 'added': meta['added']})
    return items


def exists(root, account_id):
    if not valid_id(account_id):
        return False
    return (account_dir(root, account_id) / CONFIG).is_file()


def resolve(root, account_id=None, default_first=False):
    """确定要操作的账号：显式指定 > 唯一账号 > 报错，避免误操作其他角色。

    default_first 仅供只读查询使用（如状态查询），写操作必须显式指定账号。
    """
    items = list_accounts(root)
    if account_id:
        if not any(item['id'] == account_id for item in items):
            raise AccountError('账号不存在或已解绑。')
        return account_id
    if not items:
        raise AccountError('请先导入自己的游戏账号。')
    if len(items) > 1 and not default_first:
        raise AccountError('你有多个游戏账号，请先选择一个账号再操作。')
    return items[0]['id']


def read_config(root, account_id):
    """读取凭据；只供后端内部使用，返回值不得直接输出到网页或日志。"""
    file = account_dir(root, account_id) / CONFIG
    try:
        obj = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise AccountError('账号凭据无法读取，请重新导入。') from None
    if not isinstance(obj, dict) or not isinstance(obj.get('form'), dict) or not isinstance(obj.get('cookie'), str):
        raise AccountError('账号凭据格式无效，请重新导入。')
    return obj


def config_id(config):
    """从解析后的凭据中取出角色 ID，作为账号唯一标识。"""
    try:
        account_id = str(config['form']['sRoleld'])
    except (KeyError, TypeError):
        raise AccountError('分享链接未包含游戏角色 ID。') from None
    if not valid_id(account_id):
        raise AccountError('分享链接中的游戏角色 ID 无效。')
    return account_id


def save(root, config, info=None, added=None):
    """保存/更新一个账号；返回 (account_id, 是否新建)。调用方必须持有用户操作锁。"""
    from automation import save_state
    account_id = config_id(config)
    folder = account_dir(root, account_id)
    created = not (folder / CONFIG).is_file()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    save_state(folder / CONFIG, config)
    index = _read_index(root)
    meta = _clean_meta(info or {})
    previous = index.get(account_id, {})
    # 更新 token 时不覆盖已有的首次添加时间。
    meta['added'] = previous.get('added') or meta['added'] or str(added or '')
    index[account_id] = meta
    _write_index(root, index)
    return account_id, created


def delete(root, account_id):
    """解绑账号：删除凭据、待确认记录和目录，保留运行历史供审计。"""
    if not exists(root, account_id):
        raise AccountError('账号不存在或已解绑。')
    folder = account_dir(root, account_id)
    shutil.rmtree(folder)
    index = _read_index(root)
    if index.pop(account_id, None) is not None:
        _write_index(root, index)
    return account_id


def pending_file(root, account_id):
    return account_dir(root, account_id) / PENDING
