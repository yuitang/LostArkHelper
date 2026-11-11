import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CalendarCheck, CheckCircle2, FileText, Filter, LoaderCircle, RefreshCw } from 'lucide-react'
import { AccountAvatar, type GameAccount } from './accounts'

type Run = {
  id: number; started: string; finished?: string; outcome: string
  email_status?: string; email_reason?: string
  details: {
    sign?: string; claimed?: { id: number; name: string }[]; skipped?: { id: number; name: string; reason?: string }[]
    message?: string; pending?: number | string; check_only?: boolean; trigger?: string
    account?: { id: string; role?: string }
  }
}

const outcomeLabels: Record<string, string> = { success: '执行成功', error: '执行失败', blocked: '需人工核对', skipped: '已跳过', running: '执行中', interrupted: '执行中断' }
const signLabels: Record<string, string> = { already_signed: '今日已签到，本次跳过', signed_now: '本次签到成功', unsigned: '未签到', not_checked: '未获取签到状态', failed: '本次签到失败，详见错误原因' }
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
const filters = [{ key: 'all', label: '全部' }, { key: 'success', label: '成功' }, { key: 'error', label: '失败' }, { key: 'skipped', label: '已跳过' }]

export function HistoryPanel({ accounts, active }: { accounts: GameAccount[]; active: string }) {
  // 默认显示全部角色的记录，便于多账号时一眼看到整体；切换顶栏角色后自动跟随该角色。
  const [account, setAccount] = useState('')
  const [outcome, setOutcome] = useState('all')
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [cursor, setCursor] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const request = useRef(0)

  const load = useCallback(async (append = false) => {
    const id = ++request.current
    setBusy(true); setError('')
    try {
      const query = new URLSearchParams()
      if (account) query.set('account', account)
      if (append && cursor) query.set('before', String(cursor))
      const r = await fetch('/api/history' + (query.toString() ? '?' + query : ''))
      const j = await r.json()
      if (!r.ok) throw Error(j.error || '读取失败')
      if (id !== request.current) return
      setRuns(old => append && old ? [...old, ...j.items] : j.items)
      setCursor(j.next)
    } catch (e) {
      if (id === request.current) setError(e instanceof Error ? e.message : '无法读取运行记录，请重试。')
    } finally { if (id === request.current) setBusy(false) }
  }, [account, cursor])

  // 切换筛选时重新从第一页读取，避免不同角色的分页游标混用。
  useEffect(() => { setCursor(null); setRuns(null); void load(false) }, [account]) // eslint-disable-line react-hooks/exhaustive-deps
  // 顶栏切换角色后，记录页跟随显示该角色，保持全局上下文一致（首次进入仍显示全部）。
  const firstActive = useRef(true)
  useEffect(() => {
    if (firstActive.current) { firstActive.current = false; return }
    if (active) setAccount(active)
  }, [active])

  const shown = (runs || []).filter(run => outcome === 'all'
    || (outcome === 'error' ? ['error', 'blocked', 'interrupted'].includes(run.outcome) : run.outcome === outcome))
  const stats = shown.reduce((acc, run) => {
    acc.total += 1
    if (run.outcome === 'success') acc.success += 1
    else if (run.outcome === 'skipped') acc.skipped += 1
    else acc.failed += 1
    return acc
  }, { total: 0, success: 0, failed: 0, skipped: 0 })

  return <section className="panel history">
    <div className="panel-head">
      <div><h2>运行记录</h2><p>北京时间 · 每页 30 条 · 含签到结果与奖励明细</p></div>
      <button className="refresh" onClick={() => load(false)} disabled={busy}><RefreshCw size={15} className={busy ? 'spin' : ''}/>刷新</button>
    </div>
    <div className="history-filters">
      <div className="filter-row" role="tablist" aria-label="按角色筛选">
        <span className="filter-label"><Filter size={13}/>角色</span>
        <button role="tab" aria-selected={account === ''} className={'filter-chip' + (account === '' ? ' on' : '')} onClick={() => setAccount('')}>全部角色</button>
        {accounts.map(item => <button key={item.id} role="tab" aria-selected={account === item.id}
          className={'filter-chip' + (account === item.id ? ' on' : '')} onClick={() => setAccount(item.id)}>
          <AccountAvatar id={item.id} role={item.role} size="sm"/>{item.role}
        </button>)}
      </div>
      <div className="filter-row">
        <span className="filter-label">结果</span>
        {filters.map(item => <button key={item.key} className={'filter-chip plain' + (outcome === item.key ? ' on' : '')}
          onClick={() => setOutcome(item.key)}>{item.label}</button>)}
      </div>
      <div className="history-stats">
        <span><b>{stats.total}</b> 条记录</span>
        <span className="ok"><CheckCircle2 size={13}/>{stats.success} 成功</span>
        <span className="bad"><AlertTriangle size={13}/>{stats.failed} 异常</span>
        <span className="muted">{stats.skipped} 跳过</span>
      </div>
    </div>
    {error ? <div className="empty" role="alert">{error}<button className="text-btn" onClick={() => load(false)}>重新加载</button></div>
      : runs === null ? <div className="empty" role="status"><LoaderCircle className="spin" size={22}/>正在读取执行记录…</div>
      : shown.length === 0 ? <div className="empty"><FileText size={24}/><b>还没有符合条件的记录</b>
          <span>{runs.length ? '换一个角色或结果筛选试试。' : '首次执行后，结果会保存在这里。'}</span></div>
      : <div className="run-list">{shown.map(run => <article className={'run-entry ' + run.outcome} key={run.id}>
          <div className="run-header">
            <div className="run-who">
              {run.details.account?.id && <AccountAvatar id={run.details.account.id} role={run.details.account.role || '角色'} size="sm"/>}
              <div>
                <b>{run.details.account?.role || '未标记角色'}</b>
                <time dateTime={run.started}>{formatTime(run.started)}</time>
                <small>{run.details.check_only ? '部署只查询验证' : run.details.trigger === 'manual' ? '手动操作' : '定时任务'} · #{run.id}</small>
              </div>
            </div>
            <span className={'run-outcome ' + run.outcome}>{outcomeLabels[run.outcome] || '未知状态'}</span>
          </div>
          <div className="run-sign"><CalendarCheck size={16}/><span>{signLabels[run.details.sign || 'not_checked'] || '签到状态未确认'}</span></div>
          {run.details.pending !== undefined && <p className="run-warning">待确认：{run.details.pending === 'sign' ? '签到操作' : `任务 #${run.details.pending} 的奖励领取`}，请在账号管理更新该角色的 token；保存后可重新执行。</p>}
          <div className="run-rewards"><b>本次领取奖励</b>
            {run.details.claimed?.length ? <ul>{run.details.claimed.map(task => <li key={task.id}><CheckCircle2 size={14}/>{task.name}<span>#{task.id}</span></li>)}</ul>
              : <p>{run.details.check_only ? '只查询验证，未执行奖励领取。' : run.outcome === 'success' ? '无，本次没有需要领取的已收录奖励。' : '暂无已确认领取的奖励。'}</p>}
          </div>
          {!!run.details.skipped?.filter(t => t.reason !== 'manual_only').length &&
            <p className="run-muted">未收录而跳过：{run.details.skipped.filter(t => t.reason !== 'manual_only').map(t => `${t.name}（#${t.id}）`).join('、')}</p>}
          {!!run.details.skipped?.filter(t => t.reason === 'manual_only').length &&
            <p className="run-muted">自动执行只领每日任务，以下需在游戏内领取：{run.details.skipped.filter(t => t.reason === 'manual_only').map(t => `${t.name}（#${t.id}）`).join('、')}</p>}
          {run.details.message && <p className="run-warning">{run.details.message}</p>}
          {run.email_status && <p className="run-muted">邮件提醒：{run.email_status === 'sent' ? '已发送' : run.email_status === 'sending' ? '发送中' : run.email_reason || '发送未确认'}</p>}
        </article>)}</div>}
    {runs !== null && cursor !== null && <div className="history-more"><button className="refresh" onClick={() => load(true)} disabled={busy}>
      {busy ? <LoaderCircle className="spin" size={15}/> : null}加载更多记录</button></div>}
  </section>
}
