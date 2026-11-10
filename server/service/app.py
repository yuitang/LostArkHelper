#!/usr/bin/env python3
"""Multi-user dashboard with private server-side game credentials."""
import json
import os
import secrets
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit, parse_qs
from automation import OperationLock, run, error_info, finish_run
import store
import auth
import accounts
import helper
import import_link as importer
import admin
import notifications

ROOT = Path(os.environ.get('ARK_HELPER_DIR', '/var/lib/ark-dashboard'))
STATIC = Path(os.environ.get('ARK_STATIC_DIR', '/opt/lostark-dashboard/dist'))
PORT = int(os.environ.get('ARK_PORT', '18745'))
PUBLIC_ORIGIN = os.environ.get('ARK_PUBLIC_ORIGIN', 'https://lostarkhelper.dpdns.org').rstrip('/')
COOKIE = 'ark_session'

# 读取指定角色的游戏凭据；只供后端内部调用，不能把返回值直接输出到网页或日志。
def read_config(root, account_id):
    return accounts.read_config(root, account_id)

# 向官方接口查询签到和任务，向网页仅返回展示所需的字段；此函数不提交签到或领奖。
# account_id 为空且只有一个账号时自动选中该账号；多账号必须显式指定。
def status(root, account_id=None):
    items = accounts.list_accounts(root)
    if not items:
        return {'configured': False, 'source': None, 'accounts': []}
    try:
        # 状态查询是只读的：多账号未指定时默认第一个角色，页面按返回值显示当前账号。
        account_id = accounts.resolve(root, account_id, default_first=True)
    except accounts.AccountError as exc:
        return {'configured': False, 'source': None, 'accounts': items, 'error': str(exc)}
    current = next(item for item in items if item['id'] == account_id)
    base = {'configured': True, 'source': current['role'], 'account': current, 'accounts': items}
    try:
        config = read_config(root, account_id)
        signed = helper.sign_status(config)
        tasks = helper.tasks(config)
        cleaned = [{'id': t['id'], 'name': str(t.get('name', '未知任务'))[:100],
                    'type': t['type'], 'status': t['status'], 'exp': t.get('exp')} for t in tasks]
        return dict(base, signed=signed, tasks=cleaned, time=datetime.now(store.TZ).strftime('%H:%M'))
    except helper.ApiError as exc:
        return dict(base, error=str(exc), needs_token_update=exc.kind in ('auth_invalid', 'auth_mismatch'))
    except Exception:
        return dict(base, error='查询失败，请更新登录凭据或检查活动是否有效。')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    # 统一添加缓存限制、CSP 和禁止嵌入等响应头，减少凭据页面被其他站点利用的机会。
    def common(self, content_type, length):
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()

    def reply(self, code, data, cookie=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.common('application/json; charset=utf-8', len(body))
        self.wfile.write(body)

    # 仅接受本机地址，防止浏览器通过其他 Host 名称绕过本机访问边界。
    def valid_host(self):
        return self.headers.get('Host', '') in (f'localhost:{PORT}', f'127.0.0.1:{PORT}')

    def session_token(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            return cookies[COOKIE].value if COOKIE in cookies else ''
        except Exception:
            return ''

    # 生成会话 Cookie；HttpOnly 阻止页面脚本读取，SameSite=Strict 限制跨站携带。
    def cookie(self, value, expires=False):
        secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
        return '{}={}; Path=/; HttpOnly; SameSite=Strict; Max-Age={}{}'.format(
            COOKIE, value, 0 if expires else auth.SESSION_SECONDS, secure)

    # 根据 HttpOnly 会话 Cookie 确定当前访问身份；服务器版与本地版的身份来源不同。
    def current_user(self):
        return auth.session_user(ROOT, self.session_token())

    def do_GET(self):
        try:
            self.get()
        except Exception:
            self.reply(500, {'error': '服务暂时不可用，请稍后重试。'})

    # GET 路由：处理状态、计划、历史等查询，以及构建后的前端静态文件。
    def get(self):
        if not self.valid_host():
            return self.reply(403, {'error': '访问地址不允许'})
        path = urlsplit(self.path).path
        if path.startswith('/api/'):
            user = self.current_user()
            if path == '/api/auth/me':
                return self.reply(200, {'user': auth.public_user(user)})
            if not user:
                return self.reply(401, {'error': '请先登录。'})
            if path.startswith('/api/admin/'):
                if not user.get('is_admin'):
                    return self.reply(403, {'error': '仅管理员可访问此功能。'})
                if path == '/api/admin/users':
                    try:
                        query = parse_qs(urlsplit(self.path).query)
                        limit, offset = int(query.get('limit', ['30'])[0]), int(query.get('offset', ['0'])[0])
                        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
                            raise ValueError()
                    except ValueError:
                        return self.reply(400, {'error': '分页参数无效。'})
                    return self.reply(200, admin.users(ROOT, limit, offset, actor=user))
                if path == '/api/admin/mail':
                    return self.reply(200, notifications.public_mail(ROOT))
                return self.reply(404, {'error': 'not found'})
            lock = auth.operation_lock(ROOT, user)
            if not lock.acquire():
                return self.reply(409, {'error': '你的账号已有操作正在进行，请稍后再试。'})
            try:
                if not auth.user_exists(ROOT, user):
                    return self.reply(401, {'error': '账号已删除，请重新登录。'})
                root = auth.user_root(ROOT, user)
                if path == '/api/notifications':
                    return self.reply(200, dict(auth.notification_preferences(ROOT, user),
                                              mail_ready=notifications.public_mail(ROOT)['ready']))
                if path == '/api/schedule':
                    items = accounts.list_accounts(root)
                    plans = store.account_plans(root, [item['id'] for item in items])
                    return self.reply(200, {
                        'timezone': 'Asia/Shanghai',
                        'default_plan': store.settings(root),
                        'accounts': [dict(item, plan=plans[item['id']], next_run=store.next_run(plans[item['id']]))
                                     for item in items]})
                if path == '/api/history':
                    query = parse_qs(urlsplit(self.path).query)
                    try:
                        before = int(query.get('before', ['0'])[0])
                        if before < 0: raise ValueError()
                    except ValueError:
                        return self.reply(400, {'error': '分页参数无效'})
                    account_id = (query.get('account', [''])[0] or '').strip() or None
                    if account_id and not accounts.valid_id(account_id):
                        return self.reply(400, {'error': '账号参数无效。'})
                    return self.reply(200, store.history(root, before, account_id))
                if path == '/api/accounts':
                    return self.reply(200, {'accounts': accounts.list_accounts(root)})
                if path == '/api/status':
                    query = parse_qs(urlsplit(self.path).query)
                    account_id = (query.get('account', [''])[0] or '').strip() or None
                    return self.reply(200, status(root, account_id))
                return self.reply(404, {'error': 'not found'})
            finally:
                lock.release()
        if path in ('/', '/login', '/register'):
            path = '/index.html'
        if '..' in path or not (path == '/index.html' or path.startswith('/assets/')):
            return self.reply(404, {'error': 'not found'})
        file = (STATIC / path.lstrip('/')).resolve()
        if not file.is_relative_to(STATIC.resolve()) or not file.is_file():
            return self.reply(404, {'error': 'not found'})
        typ = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
               '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.ico': 'image/x-icon', '.svg': 'image/svg+xml', '.woff2': 'font/woff2'}.get(file.suffix, 'application/octet-stream')
        body = file.read_bytes()
        self.send_response(200)
        self.common(typ, len(body))
        self.wfile.write(body)

    def do_POST(self):
        try:
            self.post()
        except Exception:
            self.reply(500, {'error': '操作未能确认，请检查运行记录；不要盲目重试。'})

    # POST 路由：先验证 Host、Origin、自定义头及 JSON 大小，再处理配置和写操作。
    def post(self):
        origin = self.headers.get('Origin', '')
        local = origin == 'http://' + self.headers.get('Host', '')
        public = origin == PUBLIC_ORIGIN and self.headers.get('X-Forwarded-Proto') == 'https'
        if not self.valid_host() or not (local or public) or self.headers.get('X-Ark-Request') != '1' or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(403, {'error': '请求来源无效'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 1 <= size <= 16384: raise ValueError()
            obj = json.loads(self.rfile.read(size))
            if not isinstance(obj, dict): raise ValueError()
        except Exception:
            return self.reply(400, {'error': '请求格式无效'})
        path = urlsplit(self.path).path
        if path in ('/api/auth/login', '/api/auth/register'):
            username, password = obj.get('username'), obj.get('password')
            identity = self.headers.get('CF-Connecting-IP', self.client_address[0])
            if not auth.rate_limit(ROOT, identity, username, path.endswith('register')):
                return self.reply(429, {'error': '尝试过于频繁，请在 15 分钟后重试。'})
            if path.endswith('register'):
                try:
                    user = auth.create_user(ROOT, username, password)
                    setup_lock = auth.operation_lock(ROOT, user)
                    if not setup_lock.acquire():
                        return self.reply(409, {'error': '账号正在处理其他操作，请稍后重新登录。'})
                    try:
                        if not auth.user_exists(ROOT, user):
                            return self.reply(409, {'error': '注册期间账号已被管理员删除。'})
                        root = auth.user_root(ROOT, user)
                        store.settings(root, dict(store.DEFAULT))
                    finally:
                        setup_lock.release()
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
                # Registration does not create a logged-in session.
                return self.reply(201, {'message': '注册成功，请使用新账号登录。'})
            user = auth.authenticate(ROOT, username, password)
            if not user:
                return self.reply(401, {'error': '用户名或密码错误。'})
            try:
                token = auth.new_session(ROOT, user)
            except ValueError:
                return self.reply(401, {'error': '账号已删除，请重新登录。'})
            return self.reply(200, {'user': auth.public_user(user)}, self.cookie(token))
        user = self.current_user()
        if not user:
            return self.reply(401, {'error': '请先登录。'})
        if path == '/api/auth/logout':
            auth.logout(ROOT, self.session_token())
            return self.reply(200, {'message': '已退出登录。'}, self.cookie('', expires=True))
        if path.startswith('/api/admin/') and not user.get('is_admin'):
            return self.reply(403, {'error': '仅管理员可访问此功能。'})
        lock = auth.operation_lock(ROOT, user)
        if not lock.acquire():
            return self.reply(409, {'error': '你的账号已有操作正在进行，请稍后再试。'})
        try:
            if not auth.user_exists(ROOT, user):
                return self.reply(401, {'error': '账号已删除，请重新登录。'})
            if path == '/api/admin/users/delete':
                try:
                    value = admin.delete_user(ROOT, user, obj)
                except admin.DeleteError as exc:
                    return self.reply(exc.status, {'error': str(exc)})
                return self.reply(200, value)
            if path.startswith('/api/admin/'):
                if path != '/api/admin/mail':
                    return self.reply(404, {'error': 'not found'})
                mail_lock = OperationLock(ROOT)
                if not mail_lock.acquire():
                    return self.reply(409, {'error': '管理员设置正在被使用，请稍后重试。'})
                try:
                    try:
                        value = notifications.save_mail(ROOT, obj)
                    except ValueError as exc:
                        return self.reply(400, {'error': str(exc)})
                    return self.reply(200, dict(value, message='QQ 发信设置已保存；授权码不会回显。'))
                finally:
                    mail_lock.release()
            root = auth.user_root(ROOT, user)
            if path == '/api/notifications':
                try:
                    value = notifications.validate_preferences(obj)
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
                auth.notification_preferences(ROOT, user, value)
                return self.reply(200, dict(value, message='异常邮件提醒设置已保存。'))
            if path == '/api/schedule':
                account_id = obj.pop('account', None) if isinstance(obj, dict) else None
                try:
                    account_id = accounts.resolve(root, account_id.strip() if isinstance(account_id, str) and account_id.strip() else None)
                    plan = store.account_settings(root, account_id, obj)
                except accounts.AccountError as exc:
                    return self.reply(400, {'error': str(exc)})
                except ValueError:
                    return self.reply(400, {'error': '时间设置无效，请检查分钟及 HH:MM 格式。'})
                current = next((item for item in accounts.list_accounts(root) if item['id'] == account_id), {'id': account_id})
                return self.reply(200, dict(plan, timezone='Asia/Shanghai', account=current,
                                            next_run=store.next_run(plan),
                                            message='「{}」的定时设置已保存。'.format(current.get('role') or '当前角色')))
            if path in ('/api/import', '/api/accounts/import'):
                if not isinstance(obj.get('url'), str):
                    return self.reply(400, {'error': '请填写分享链接。'})
                try:
                    config, info = importer.parse_link(obj['url'])
                    account_id = accounts.config_id(config)
                except (ValueError, accounts.AccountError):
                    return self.reply(400, {'error': '分享链接无效，请从官方 App 复制支持的活动链接。'})
                existing = accounts.exists(root, account_id)
                if existing and obj.get('replace') is not True:
                    return self.reply(409, {'error': '该角色已绑定，请勾选确认更新后再提交；如需换角色请新增账号。'})
                if existing:
                    old = read_config(root, account_id)
                    if any(str(old['form'].get(k)) != str(config['form'].get(k)) for k in ('user_id', 'openid', 'sRoleld')):
                        return self.reply(409, {'error': '仅支持更新同一游戏账号和角色的 token，不能在此切换账号。'})
                # 不能把过期凭据保存成“更新成功”，也不能据此清除待确认记录。
                try:
                    helper.validate_login(config, force=True)
                except helper.ApiError as exc:
                    return self.reply(400 if exc.kind in ('auth_invalid', 'auth_mismatch') else 503,
                                      {'error': str(exc), 'needs_token_update': exc.kind in ('auth_invalid', 'auth_mismatch')})
                # 首个账号才初始化定时计划；已有其他账号时只新增，不改动已有计划。
                first = not accounts.list_accounts(root)
                saved_id, created = accounts.save(root, config, info,
                                                  added=datetime.now(store.TZ).isoformat(timespec='seconds'))
                try:
                    initialized = store.token_imported(root, saved_id, first_import=first)
                except Exception:
                    # 首次计划落盘失败时撤回本次新增，重试仍可完整初始化。
                    if created:
                        accounts.delete(root, saved_id)
                    raise
                # 已持有该账号的操作锁，且凭据保存成功；允许用新凭据重新查询后执行。
                # 校验失败、身份不符或写入失败时不会执行到这里，原待确认记录仍保留。
                pending = accounts.pending_file(root, saved_id)
                recovered = pending.exists()
                pending.unlink(missing_ok=True)
                names = ' / '.join(str(info.get(k) or '未提供')[:100] for k in ('area', 'server', 'role'))
                current_plan = store.account_settings(root, saved_id)
                plan_note = '。该角色已启用每小时第 {} 分钟执行，可在定时设置单独调整。'.format(current_plan['minute']) if initialized else ''
                verb = '角色已添加' if created else '角色 token 已更新'
                return self.reply(200, {'message': verb + '：' + names + plan_note + ('待确认限制已解除，下次执行将重新查询签到和任务状态。' if recovered else ''),
                                        'account': next(item for item in accounts.list_accounts(root) if item['id'] == saved_id),
                                        'created': created, 'pending_cleared': recovered,
                                        'schedule_initialized': initialized})
            if path == '/api/accounts/delete':
                account_id = obj.get('id')
                if not isinstance(account_id, str) or not account_id.strip():
                    return self.reply(400, {'error': '请选择要解绑的账号。'})
                try:
                    removed = accounts.delete(root, account_id.strip())
                except accounts.AccountError as exc:
                    return self.reply(404, {'error': str(exc)})
                # 该角色的独立计划一并清理，运行历史保留用于审计。
                store.clear_account_settings(root, removed)
                remaining = accounts.list_accounts(root)
                return self.reply(200, {'message': '已解绑该角色：服务器上的登录凭据和待确认记录已删除，运行历史保留。',
                                        'id': removed, 'accounts': remaining})
            if path not in ('/api/sign', '/api/claim', '/api/run'):
                return self.reply(404, {'error': 'not found'})
            requested = obj.get('account')
            try:
                account_id = accounts.resolve(root, requested.strip() if isinstance(requested, str) and requested.strip() else None)
                config = read_config(root, account_id)
            except accounts.AccountError as exc:
                return self.reply(400, {'error': str(exc)})
            current = next((item for item in accounts.list_accounts(root) if item['id'] == account_id), {'id': account_id})
            report = {'trigger': 'manual', 'account': current}
            run_id = store.begin(root, None)
            report['run_id'] = run_id
            try:
                code = run(helper, config, root, report=report, state_dir=accounts.account_dir(root, account_id),
                           allow_sign=path in ('/api/sign', '/api/run'), allow_claim=path in ('/api/claim', '/api/run'))
            except Exception as exc:
                report.setdefault('errors', []).append(error_info(exc, report.get('stage', '手动操作')))
                report['message'] = '；'.join(e['summary'] for e in report['errors'])
                finish_run(root, run_id, report, 'error', notifications.callback(ROOT, user, root))
                return self.reply(500, {'error': report['message']})
            finish_run(root, run_id, report, 'success' if code == 0 else 'error', notifications.callback(ROOT, user, root))
            # 响应写回失败不应把已确认的游戏成功改记为失败、再触发异常邮件。
            return self.reply(200 if code == 0 else 409, {'message': ('执行成功：' + ('本次签到成功' if report.get('sign') == 'signed_now' else '今日已签到，跳过签到') + '；领取奖励 {} 项。'.format(len(report.get('claimed', [])))) if code == 0 else report.get('message', '执行失败，请查看运行记录。')})
        finally:
            lock.release()

if __name__ == '__main__':
    os.umask(0o077)
    auth.initialize(ROOT)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.timeout = 30
    server.serve_forever()
