import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  AlertCircle, Anchor, ArrowRight, CalendarCheck, Check, CheckCircle2, ChevronRight, Clock3,
  Compass, Gift, LayoutDashboard, LoaderCircle, LogOut, RefreshCw, ScrollText,
  Settings2, ShieldCheck, Sparkles, UsersRound, X, Zap,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { TooltipProvider } from '@/components/ui/tooltip'
import './style.css'
import { AuthScreen } from './auth'
import LightRays from '../components/LightRays'
import './dashboard-rays.css'
import './workbench.css'
import { AdminPanel, NotificationsPanel } from './management'
import { AccountsPanel, AccountRail, AccountSwitcher, type GameAccount } from './accounts'
import { SchedulePanel } from './schedule'
import { HistoryPanel } from './history'

type Task = { id: number; name: string; type: number; status: number; exp?: number }
type State = { configured: boolean; signed?: boolean; tasks?: Task[]; error?: string; source?: string; time?: string }
const groups = ['', '每日任务', '每周任务', '活动任务']
const icons = { admin: ShieldCheck, overview: LayoutDashboard, accounts: UsersRound, activities: Compass, history: ScrollText, settings: Settings2 }
type Page = keyof typeof icons

const pageCopy: Record<Page, { title: string; lead: string }> = {
  overview: { title: '欢迎回来，冒险家', lead: '所有角色的签到与任务进度，一屏掌握。' },
  accounts: { title: '账号管理', lead: '为每个游戏角色单独保存凭据与定时计划。' },
  activities: { title: '活动中心', lead: '查看已接入的活动任务与当前状态。' },
  history: { title: '运行记录', lead: '按角色查看每次签到与领奖的结果。' },
  settings: { title: '定时设置', lead: '为每个角色单独设定自动执行时间。' },
  admin: { title: '用户管理', lead: '查看注册用户、绑定角色与执行概览。' },
}
const apiFetch = window.fetch.bind(window)

function StatCard({ icon, tone: color, label, value, hint }: {
  icon: React.ReactNode; tone: string; label: string; value: string; hint: string
}) {
  return <div className="stat-card">
    <span className={'stat-icon ' + color}>{icon}</span>
    <div className="stat-copy"><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>
  </div>
}

// 工作台主组件：page 控制导航，data 是脱敏状态；不把 Cookie/token 渲染到页面。
function Dashboard({ user, onLogout }: { user: { username: string; is_admin?: boolean }; onLogout: () => void }) {
  const [page, setPage] = useState<Page>('overview')
  const [data, setData] = useState<State>({ configured: false })
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [confirm, setConfirm] = useState<'sign' | 'claim' | 'run' | null>(null)
  const [accounts, setAccounts] = useState<GameAccount[]>([])
  const [active, setActive] = useState('')
  const [taskFilter, setTaskFilter] = useState('')

  // 刷新只查询当前角色的状态；账号列表随响应一起更新，避免额外请求。
  async function refresh(target?: string) {
    const account = target ?? active
    setBusy('refresh')
    try {
      const r = await fetch('/api/status' + (account ? '?account=' + encodeURIComponent(account) : ''))
      const j = await r.json()
      if (Array.isArray(j.accounts)) setAccounts(j.accounts)
      if (j.account) setActive(j.account.id)
      setData(j)
      if (!r.ok) setMessage(j.error || '无法查询状态')
    } catch { setMessage('无法连接后台服务，请检查网络连接。') } finally { setBusy('') }
  }
  // 切换当前操作的角色，并按该角色的凭据重新查询。
  function select(id: string) { setActive(id); void refresh(id) }
  useEffect(() => { void refresh() }, [])

  // 写操作交给后端持锁、提交与确认；请求体带上当前角色，避免误操作其他账号。
  async function action(path: string) {
    setConfirm(null); setBusy(path); setMessage('')
    try {
      const r = await fetch('/api/' + path, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Ark-Request': '1' },
        body: JSON.stringify({ account: active }),
      })
      const j = await r.json()
      setMessage(j.message || j.error || '操作结束')
      await refresh()
    } catch { setMessage('操作结果未知。请到 App 检查状态，不要盲目重试。') } finally { setBusy('') }
  }

  const tasks = data.tasks || []
  const ready = tasks.filter(t => t.status === 1)
  const claimed = tasks.filter(t => t.status === 2)
  const current = accounts.find(item => item.id === active)
  const pages: { key: Page; label: string }[] = [
    { key: 'overview', label: '总览' }, { key: 'accounts', label: '账号管理' }, { key: 'activities', label: '活动中心' },
    { key: 'history', label: '运行记录' }, { key: 'settings', label: '定时设置' },
    ...(user.is_admin ? [{ key: 'admin' as Page, label: '用户管理' }] : []),
  ]
  const visible = taskFilter ? tasks.filter(t => String(t.type) === taskFilter) : tasks
  const ordered = visible.filter(t => t.status === 1).concat(visible.filter(t => t.status !== 1))

  return <div className="app dashboard-rays">
    <div className="dashboard-rays-background" aria-hidden="true">
      {!window.matchMedia('(prefers-reduced-motion: reduce)').matches &&
        <LightRays raysOrigin="top-right" raysColor="#e4c17e" raysSpeed={0.4} lightSpread={0.8} rayLength={2.5}
          intensity={1.4} followMouse={false} intro={false} maxDevicePixelRatio={1.5}/>}
    </div>
    <aside className="sidebar">
      <div className="brand official-brand"><img src="/assets/ark-helper-wordmark.png" alt="方舟助手" width="1425" height="574"/></div>
      <div className="sidebar-label">工作台</div>
      <nav>{pages.map(p => {
        const Icon = icons[p.key]
        return <button key={p.key} aria-label={p.label} title={p.label} className={page === p.key ? 'nav active' : 'nav'}
          onClick={() => setPage(p.key)}>
          <Icon size={18}/><span>{p.label}</span>
          {p.key === 'accounts' && accounts.length > 0 && page !== 'accounts' && <em className="nav-badge">{accounts.length}</em>}
          {page === p.key && <ChevronRight size={15} className="nav-end"/>}
        </button>
      })}</nav>
      <div className="side-bottom">
        <div className="connection"><ShieldCheck size={17}/><div><b>私密连接</b><small>HTTPS · 身份验证</small></div></div>
        <p>个人活动管理工作台<br/>不接入第三方统计</p>
      </div>
    </aside>
    <main>
      <header className="topbar">
        <div className="crumb"><span>方舟助手</span><ChevronRight size={14}/><b>{pageCopy[page].title.replace('欢迎回来，冒险家', '总览')}</b></div>
        <div className="top-right">
          <AccountSwitcher accounts={accounts} active={active} onSelect={select} onManage={() => setPage('accounts')}/>
          <span className="online-dot"/>
          <span className="session-name">{user.username}</span>
          <button className="logout-button" onClick={onLogout} aria-label="退出登录"><LogOut size={16}/><span>退出</span></button>
        </div>
      </header>
      <div className="content">
        <div className="headline">
          <div>
            <div className="headline-title"><h1>{pageCopy[page].title}</h1><Sparkles size={19}/></div>
            <p>{pageCopy[page].lead}</p>
          </div>
          {page !== 'admin' && <Button className="refresh" onClick={() => refresh()} disabled={!!busy}>
            <RefreshCw size={15} className={busy === 'refresh' ? 'spin' : ''}/>刷新状态</Button>}
        </div>
        {message && <div role="status" className="notice"><AlertCircle size={17}/><span>{message}</span>
          <button aria-label="关闭提示" onClick={() => setMessage('')}><X size={16}/></button></div>}

        {(page === 'overview' || page === 'activities') && <>
          {accounts.length > 1 && <AccountRail accounts={accounts} active={active} onSelect={select}/>}
          {page === 'overview' ? <>
            <section className="hero-card">
              <div className="hero-copy">
                <div className="feature-tag"><span/>{current ? '当前角色' : '活动追踪'}</div>
                <h2>{current ? current.role : '方舟最佳搭档'}</h2>
                <p>{current ? [current.area, current.server].filter(Boolean).join(' · ') || '区服未记录' : '签到、任务与奖励进度，尽在掌握。'}</p>
                <div className="hero-badges">
                  <span><ShieldCheck size={14}/>凭据仅保存在你的服务器</span>
                  <span><Clock3 size={14}/>{data.time ? '更新于 ' + data.time : '等待查询'}</span>
                </div>
              </div>
              <div className="hero-side">
                <div className={'hero-signal ' + (!data.configured ? 'idle' : data.signed ? 'ok' : 'wait')}>
                  {!data.configured ? <UsersRound size={22}/> : data.signed ? <CheckCircle2 size={22}/> : <CalendarCheck size={22}/>}
                  <div>
                    <small>今日签到</small>
                    <b>{!data.configured ? '未绑定账号' : data.signed === undefined ? '未确认' : data.signed ? '已完成' : '未签到'}</b>
                  </div>
                </div>
                <div className="hero-actions">
                  <Button className="hero-primary" disabled={!data.configured || !!busy} onClick={() => setConfirm('run')}>
                    <Zap size={15}/>{busy === 'run' ? '正在执行…' : '立即执行一次'}</Button>
                  {accounts.length > 1 && <button className="text-btn" onClick={() => setPage('settings')}>为每个角色设定时间 <ArrowRight size={14}/></button>}
                </div>
              </div>
              <div className="feature-art" aria-hidden="true">
                <div className="orb outer"><div className="orb inner"><Anchor size={54} strokeWidth={1.15}/></div></div>
                <i className="star s1"/><i className="star s2"/><i className="star s3"/>
              </div>
            </section>

            <div className="section-heading"><div><h2>今日进度</h2>
              <p>{current ? `「${current.role}」的实时查询结果` : '基于当前账号的实时查询结果'}</p></div>
              {data.time && <span className="updated">更新于 {data.time}</span>}</div>
            <section className="status-grid">
              <StatCard icon={<CalendarCheck size={21}/>} tone="gold" label="每日签到"
                value={!data.configured ? '未绑定' : data.signed === undefined ? '未确认' : data.signed ? '今日已签到' : '今日未签到'}
                hint={data.signed ? '今天的签到已完成' : '以游戏内活动状态为准'}/>
              <StatCard icon={<Gift size={21}/>} tone="blue" label="可领取任务"
                value={data.tasks ? `${ready.length} 项` : '—'} hint="仅领取白名单中的任务"/>
              <StatCard icon={<CheckCircle2 size={21}/>} tone="green" label="已完成任务"
                value={data.tasks ? `${claimed.length} 项` : '—'} hint="按当前接口状态推测"/>
            </section>

            <div className="main-grid">
              <section className="panel task-panel">
                <div className="panel-head"><div><h2>任务进度</h2><p>查看每日、每周及活动任务</p></div>
                  <button className="text-btn" onClick={() => setPage('activities')}>查看活动 <ArrowRight size={15}/></button></div>
                {data.error ? <div className="empty"><AlertCircle size={22}/><b>暂时无法获取任务</b><span>{data.error}</span>
                    <button className="text-btn" onClick={() => setPage('accounts')}>检查账号配置 <ArrowRight size={14}/></button></div>
                  : !data.configured ? <div className="empty"><UsersRound size={23}/><b>先连接你的账号</b><span>导入活动分享链接后，即可查询任务。</span>
                    <button className="text-btn" onClick={() => setPage('accounts')}>前往账号管理 <ArrowRight size={14}/></button></div>
                  : !data.tasks ? <div className="empty"><LoaderCircle className="spin" size={23}/><span>正在获取活动信息…</span></div>
                  : <div className="task-list">{ordered.slice(0, 5).map(task => <div className="task" key={task.id}>
                      <div className={'task-icon ' + (task.status === 1 ? 'ready' : '')}><Check size={16}/></div>
                      <div className="task-name"><b>{task.name}</b><span>{groups[task.type] || '其他任务'} · 活跃值 +{task.exp ?? '—'}</span></div>
                      <span className={'task-status ' + (task.status === 1 ? 'available' : '')}>
                        {task.status === 1 ? '可领取' : task.status === 2 ? '已完成' : task.status === 3 ? '已结束' : '未完成'}</span>
                    </div>)}{tasks.length === 0 && <div className="empty">暂无任务数据</div>}</div>}
              </section>
              <aside className="right-column">
                <section className="panel action-panel">
                  <div className="panel-head"><div><h2>快捷操作</h2><p>{current ? `作用于「${current.role}」` : '手动执行，结果可追踪'}</p></div></div>
                  <button className="action" disabled={!data.configured || !!busy} onClick={() => setConfirm('run')}>
                    <span className="action-ico gold"><RefreshCw size={18} className={busy === 'run' ? 'spin' : ''}/></span>
                    <span><b>{busy === 'run' ? '正在执行…' : '立即执行一次'}</b><small>检查签到并领取已完成奖励</small></span><ChevronRight size={17}/></button>
                  <button className="action" disabled={!data.configured || !!data.error || !!busy} onClick={() => setConfirm('sign')}>
                    <span className="action-ico gold"><CalendarCheck size={18}/></span>
                    <span><b>每日签到</b><small>已签到时将自动跳过</small></span><ChevronRight size={17}/></button>
                  <button className="action" disabled={!data.configured || !!data.error || !!busy} onClick={() => setConfirm('claim')}>
                    <span className="action-ico blue"><Gift size={18}/></span>
                    <span><b>领取任务奖励</b><small>包含推断任务 6–11</small></span><ChevronRight size={17}/></button>
                </section>
                <section className="security-note"><ShieldCheck size={19}/><div><b>安全提示</b>
                  <p>登录凭据仅保存在你的服务器。不要公开分享活动链接。</p></div></section>
                <div className="account-line"><span>当前操作角色</span><b>{current?.role || data.source || '未绑定'}</b></div>
              </aside>
            </div>
          </> : <>
            <div className="filter-row task-filters" role="tablist" aria-label="任务类型">
              <span className="filter-label"><Compass size={13}/>类型</span>
              {[{ key: '', label: '全部' }, { key: '1', label: '每日任务' }, { key: '2', label: '每周任务' }, { key: '3', label: '活动任务' }]
                .map(item => <button key={item.key} role="tab" aria-selected={taskFilter === item.key}
                  className={'filter-chip plain' + (taskFilter === item.key ? ' on' : '')} onClick={() => setTaskFilter(item.key)}>{item.label}</button>)}
            </div>
            <section className="panel task-panel activity-panel">
              <div className="panel-head"><div><h2>{current ? current.role + ' · 任务' : '任务列表'}</h2>
                <p>{data.tasks ? `共 ${visible.length} 项，其中 ${visible.filter(t => t.status === 1).length} 项可领取` : '等待查询当前角色'}</p></div></div>
              {data.error ? <div className="empty"><AlertCircle size={22}/><b>暂时无法获取任务</b><span>{data.error}</span></div>
                : !data.configured ? <div className="empty"><UsersRound size={23}/><b>还没有绑定账号</b><span>导入活动分享链接后即可查看任务。</span>
                  <button className="text-btn" onClick={() => setPage('accounts')}>前往账号管理 <ArrowRight size={14}/></button></div>
                : !data.tasks ? <div className="empty"><LoaderCircle className="spin" size={23}/><span>正在获取活动信息…</span></div>
                : <div className="task-list wide">{ordered.map(task => <div className="task" key={task.id}>
                    <div className={'task-icon ' + (task.status === 1 ? 'ready' : '')}><Check size={16}/></div>
                    <div className="task-name"><b>{task.name}</b><span>{groups[task.type] || '其他任务'} · 活跃值 +{task.exp ?? '—'} · #{task.id}</span></div>
                    <span className={'task-status ' + (task.status === 1 ? 'available' : '')}>
                      {task.status === 1 ? '可领取' : task.status === 2 ? '已完成' : task.status === 3 ? '已结束' : '未完成'}</span>
                  </div>)}{visible.length === 0 && <div className="empty">该类型下暂无任务</div>}</div>}
            </section>
          </>}
        </>}

        {page === 'accounts' && <>
          <AccountsPanel accounts={accounts} active={active} onSelect={select}
            onChanged={(text, selectId) => { setMessage(text); if (selectId !== undefined) setActive(selectId); void refresh(selectId) }}/>
          <NotificationsPanel/>
        </>}
        {page === 'history' && <HistoryPanel accounts={accounts} active={active}/>}
        {page === 'settings' && <SchedulePanel active={active} onMessage={setMessage}/>}
        {page === 'admin' && user.is_admin && <AdminPanel/>}
        <footer>ARK COMPANION <span>·</span> 个人活动管理工作台 <span>·</span> 与游戏官方无关联</footer>
      </div>
    </main>
    {confirm && <div className="modal-backdrop" role="presentation">
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dialog-title">
        <div className="dialog-head">
          <span className="dialog-icon"><Zap size={18}/></span>
          <h2 id="dialog-title">{confirm === 'run' ? '立即执行签到与领奖？' : confirm === 'sign' ? '确认执行每日签到？' : '确认领取任务奖励？'}</h2>
        </div>
        {current && <p className="dialog-target">目标角色：<b>{current.role}</b>{current.server ? `（${current.server}）` : ''}</p>}
        <p>{confirm === 'run' ? '不受定时时间限制，立即查询状态，未签到则签到，并领取已完成的白名单任务奖励；不会修改定时计划。'
          : confirm === 'sign' ? '将先查询签到状态，仅在未签到时提交；不会使用签到卡。'
          : '仅提交当前可领取且已收录的任务，含推断参数的任务 6–11；其他任务将跳过。'}</p>
        <div><Button variant="outline" onClick={() => setConfirm(null)}>取消</Button>
          <Button onClick={() => action(confirm)} disabled={!!busy}>确认执行</Button></div>
      </div>
    </div>}
  </div>
}

// 根组件先检查会话。服务器使用账号登录，本地版使用启动器签发的随机会话。
function App() {
  const [user, setUser] = useState<{ username: string; is_admin?: boolean } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  async function check() {
    setLoading(true); setError('')
    try {
      const r = await apiFetch('/api/auth/me')
      if (!r.ok) throw Error()
      const j = await r.json()
      setUser(j.user)
    } catch { setError('暂时无法连接服务器，请重试。') } finally { setLoading(false) }
  }
  useEffect(() => {
    void check()
    const expired = () => { setUser(null); window.history.replaceState(null, '', '/login') }
    window.addEventListener('ark-session-expired', expired)
    return () => window.removeEventListener('ark-session-expired', expired)
  }, [])
  async function logout() {
    try {
      const r = await apiFetch('/api/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Ark-Request': '1' }, body: '{}' })
      if (!r.ok) throw Error()
      setUser(null); window.history.replaceState(null, '', '/login')
    } catch { window.alert('退出失败，请检查网络后重试。') }
  }
  if (loading) return <div className="auth-loading" role="status">正在连接你的工作台…</div>
  if (error) return <div className="auth-loading" role="alert">{error}<button onClick={check}>重新连接</button></div>
  return user ? <Dashboard key={user.username} user={user} onLogout={logout}/> : <AuthScreen onLogin={setUser}/>
}
// 后端返回 401 时统一通知根组件处理过期会话。
window.fetch = async (...args: Parameters<typeof fetch>) => {
  const r = await apiFetch(...args)
  if (r.status === 401 && !String(args[0]).startsWith('/api/auth/')) window.dispatchEvent(new Event('ark-session-expired'))
  return r
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><TooltipProvider><App/></TooltipProvider></React.StrictMode>)
