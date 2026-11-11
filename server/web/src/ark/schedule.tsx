import { useEffect, useState } from 'react'
import { CalendarClock, Check, Clock3, Copy, LoaderCircle, Pause, Play, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AccountAvatar, type GameAccount } from './accounts'

export type Plan = { enabled: boolean; mode: 'hourly' | 'daily'; minute: number; times: string[] }
type Row = GameAccount & { plan: Plan; next_run?: string | null }

const formatNext = (value?: string | null) => {
  if (!value) return '已暂停'
  const at = new Date(value)
  return Number.isNaN(at.getTime()) ? '已暂停'
    : '下次 ' + at.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
const place = (row: GameAccount) => [row.area, row.server].filter(Boolean).join(' · ') || '区服未记录'
const same = (a: Plan, b: Plan) => a.enabled === b.enabled && a.mode === b.mode && a.minute === b.minute && a.times.join() === b.times.join()

// 每个游戏角色一张计划卡：开关、频率、执行时间互不影响。
export function SchedulePanel({ active, onMessage }: {
  active: string; onMessage: (text: string) => void
}) {
  const [rows, setRows] = useState<Row[] | null>(null)
  const [drafts, setDrafts] = useState<Record<string, { plan: Plan; times: string }>>({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let alive = true
    setError('')
    fetch('/api/schedule').then(async r => {
      const j = await r.json()
      if (!r.ok) throw Error(j.error || '读取定时设置失败')
      if (!alive) return
      setRows(j.accounts)
      setDrafts(Object.fromEntries(j.accounts.map((row: Row) => [row.id, { plan: row.plan, times: row.plan.times.join(', ') }])))
    }).catch(() => { if (alive) setError('无法读取定时设置，请重试。') })
    return () => { alive = false }
  }, [reload])

  const edit = (id: string, patch: Partial<Plan>) => setDrafts(old => ({
    ...old, [id]: { ...old[id], plan: { ...old[id].plan, ...patch } },
  }))
  const editTimes = (id: string, times: string) => setDrafts(old => ({ ...old, [id]: { ...old[id], times } }))

  async function save(row: Row) {
    const draft = drafts[row.id]
    if (!draft) return
    const times = draft.plan.mode === 'hourly' ? draft.plan.times : draft.times.split(/[,，\s]+/).filter(Boolean)
    if (!times.length || times.some(t => !/^([01]\d|2[0-3]):[0-5]\d$/.test(t))) {
      setError('请按 HH:MM 填写时间，多个时间用逗号分隔。'); return
    }
    setBusy(row.id); setError('')
    try {
      const r = await fetch('/api/schedule', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Ark-Request': '1' },
        body: JSON.stringify({ account: row.id, ...draft.plan, times }),
      })
      const j = await r.json()
      if (!r.ok) throw Error(j.error || '保存失败')
      const saved: Plan = { enabled: j.enabled, mode: j.mode, minute: j.minute, times: j.times }
      setRows(old => old?.map(item => item.id === row.id ? { ...item, plan: saved, next_run: j.next_run } : item) ?? null)
      setDrafts(old => ({ ...old, [row.id]: { plan: saved, times: saved.times.join(', ') } }))
      onMessage(j.message)
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败，请重试。')
    } finally { setBusy('') }
  }

  // 把当前卡片的设置复制到其他角色，适合多号统一时间；逐个保存并独立报错。
  async function sync(row: Row) {
    const draft = drafts[row.id]
    if (!draft) return
    const times = draft.plan.mode === 'hourly' ? draft.plan.times : draft.times.split(/[,，\s]+/).filter(Boolean)
    if (!times.length || times.some(t => !/^([01]\d|2[0-3]):[0-5]\d$/.test(t))) {
      setError('请按 HH:MM 填写时间，多个时间用逗号分隔。'); return
    }
    setBusy('sync'); setError('')
    const others = (rows || []).filter(item => item.id !== row.id)
    const failed: string[] = []
    for (const other of others) {
      try {
        const r = await fetch('/api/schedule', {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Ark-Request': '1' },
          body: JSON.stringify({ account: other.id, ...draft.plan, times }),
        })
        if (!r.ok) failed.push(other.role)
      } catch { failed.push(other.role) }
    }
    setBusy('')
    if (failed.length) setError('以下角色同步失败，请单独保存：' + failed.join('、'))
    else onMessage(`已把「${row.role}」的定时设置同步到其他 ${others.length} 个角色。`)
    setReload(n => n + 1)
  }

  if (error && !rows) return <section className="panel"><div className="empty" role="alert">{error}
    <button className="text-btn" onClick={() => setReload(n => n + 1)}>重新加载</button></div></section>
  if (!rows) return <section className="panel"><div className="empty" role="status"><LoaderCircle className="spin" size={22}/>正在读取定时设置…</div></section>
  if (!rows.length) return <section className="panel"><div className="empty">
    <CalendarClock size={24}/><b>还没有可定时的角色</b><span>先在账号管理绑定游戏账号，再为每个角色设置执行时间。</span></div></section>

  return <div className="schedule-stack">
    <div className="schedule-banner">
      <Clock3 size={17}/>
      <div><b>每个角色独立调度</b><span>所有时间均为北京时间（UTC+8）。某个角色暂停或失败，不影响其他角色。</span></div>
    </div>
    {error && <div role="alert" className="account-error"><span>{error}</span></div>}
    <div className="schedule-cards">{rows.map(row => {
      const draft = drafts[row.id] || { plan: row.plan, times: row.plan.times.join(', ') }
      const dirty = !same(draft.plan, row.plan) || (draft.plan.mode === 'daily' && draft.times !== row.plan.times.join(', '))
      return <section className={'panel plan-card' + (row.id === active ? ' current' : '')} key={row.id}>
        <header className="plan-head">
          <AccountAvatar id={row.id} role={row.role} size="md"/>
          <div className="plan-title"><h3>{row.role}</h3><p>{place(row)} · ID {row.id}</p></div>
          <span className={'plan-state ' + (draft.plan.enabled ? 'on' : 'off')}>
            {draft.plan.enabled ? <Play size={12}/> : <Pause size={12}/>}{draft.plan.enabled ? '已启用' : '已暂停'}
          </span>
          <label className="switch" title={draft.plan.enabled ? '暂停该角色' : '启用该角色'}>
            <input type="checkbox" checked={draft.plan.enabled} onChange={e => edit(row.id, { enabled: e.target.checked })} aria-label={`${row.role} 启用定时`}/>
            <span/>
          </label>
        </header>
        <div className="plan-body">
          <div className="segmented" role="group" aria-label="执行频率">
            <button type="button" className={draft.plan.mode === 'hourly' ? 'on' : ''} onClick={() => edit(row.id, { mode: 'hourly' })}>每小时</button>
            <button type="button" className={draft.plan.mode === 'daily' ? 'on' : ''} onClick={() => edit(row.id, { mode: 'daily' })}>每天定点</button>
          </div>
          {draft.plan.mode === 'hourly'
            ? <label className="plan-field">每小时的第
                <input type="number" min={0} max={59} value={draft.plan.minute}
                  onChange={e => edit(row.id, { minute: Math.max(0, Math.min(59, Number(e.target.value) || 0)) })}/> 分钟
                <small>0 表示整点执行；建议不同角色错开分钟，减少同时请求。</small>
              </label>
            : <label className="plan-field">每天在
                <input type="text" value={draft.times} placeholder="09:00, 18:30"
                  onChange={e => editTimes(row.id, e.target.value)}/> 执行
                <small>24 小时制，多个时间用逗号分隔，最多 24 个。</small>
              </label>}
        </div>
        <div className="plan-foot">
          <span className="plan-next"><Clock3 size={13}/>{formatNext(row.next_run)}</span>
          <div className="plan-actions">
            {rows.length > 1 && <button className="plan-sync" disabled={!!busy} onClick={() => sync(row)}>
              <Copy size={14}/>{busy === 'sync' ? '同步中…' : '同步到其他角色'}</button>}
            <Button className="save-schedule" disabled={!!busy || !dirty} onClick={() => save(row)}>
              {busy === row.id ? '正在保存…' : dirty ? <><Save size={14}/>保存</> : <><Check size={14}/>已保存</>}
            </Button>
          </div>
        </div>
      </section>
    })}</div>
    <p className="schedule-safety">仅领取已经在游戏内完成的任务奖励，不代做游戏任务。失败会记录原因；下次按计划重新查询后执行，不会在同一次运行中重复提交。</p>
  </div>
}
