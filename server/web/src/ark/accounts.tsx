import React, { useEffect, useRef, useState } from 'react'
import { AlertCircle, Check, ChevronDown, CreditCard, Link2, LockKeyhole, Plus, Settings2, Trash2, UsersRound } from 'lucide-react'
import { Button } from '@/components/ui/button'

export type GameAccount = { id: string; role: string; server?: string; area?: string; added?: string }

type Props = {
  accounts: GameAccount[]
  active: string
  onSelect: (id: string) => void
  onChanged: (message: string, selectId?: string) => void
}

const formatAdded = (value?: string) => {
  if (!value) return ''
  const at = new Date(value)
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })
}

// 用角色 ID 派生固定的色相，同一角色在切换器、列表和记录里颜色一致。
const tone = (id: string) => {
  let hash = 0
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) % 360
  return hash
}

export function AccountAvatar({ id, role, size = 'md' }: { id: string; role: string; size?: 'sm' | 'md' | 'lg' }) {
  return <span className={'acct-avatar ' + size} style={{ '--hue': tone(id) } as React.CSSProperties} aria-hidden="true">
    {role.trim().slice(0, 1) || '角'}
  </span>
}

const place = (account: GameAccount) => [account.area, account.server].filter(Boolean).join(' · ') || '区服未记录'

// 顶栏账号切换器：多账号时展开下拉，单账号时只显示当前角色。
export function AccountSwitcher({ accounts, active, onSelect, onManage }: {
  accounts: GameAccount[]; active: string; onSelect: (id: string) => void; onManage: () => void
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false) }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', escape) }
  }, [open])
  const current = accounts.find(item => item.id === active) || accounts[0]
  if (!current) return null
  const single = accounts.length <= 1
  return <div className="acct-switch" ref={box}>
    <button className={'acct-switch-btn' + (open ? ' open' : '')} onClick={() => (single ? onManage() : setOpen(!open))}
      aria-haspopup={single ? undefined : 'listbox'} aria-expanded={single ? undefined : open}
      title={single ? '管理游戏账号' : '切换游戏账号'}>
      <AccountAvatar id={current.id} role={current.role} size="sm"/>
      <span className="acct-switch-copy"><b>{current.role}</b><small>{place(current)}</small></span>
      {single ? <Settings2 size={14}/> : <ChevronDown size={15} className={open ? 'flip' : ''}/>}
    </button>
    {open && !single && <div className="acct-menu" role="listbox" aria-label="游戏账号">
      <div className="acct-menu-head"><span>已绑定 {accounts.length} 个角色</span><small>切换后所有页面跟随该角色</small></div>
      {accounts.map(account => <button key={account.id} role="option" aria-selected={account.id === active}
        className={'acct-menu-item' + (account.id === active ? ' current' : '')}
        onClick={() => { onSelect(account.id); setOpen(false) }}>
        <AccountAvatar id={account.id} role={account.role} size="sm"/>
        <span><b>{account.role}</b><small>{place(account)} · {account.id}</small></span>
        {account.id === active && <Check size={15} className="acct-check"/>}
      </button>)}
      <button className="acct-menu-manage" onClick={() => { setOpen(false); onManage() }}><UsersRound size={15}/>管理游戏账号</button>
    </div>}
  </div>
}

// 总览与活动页的账号横条：一眼看到所有角色，点击即切换。
export function AccountRail({ accounts, active, onSelect }: {
  accounts: GameAccount[]; active: string; onSelect: (id: string) => void
}) {
  if (accounts.length === 0) return null
  return <div className="acct-rail" role="tablist" aria-label="游戏角色">
    {accounts.map(account => <button key={account.id} role="tab" aria-selected={account.id === active}
      className={'acct-chip' + (account.id === active ? ' current' : '')} onClick={() => onSelect(account.id)}>
      <AccountAvatar id={account.id} role={account.role} size="sm"/>
      <span><b>{account.role}</b><small>{account.server || '区服未记录'}</small></span>
      {account.id === active && <i className="acct-chip-dot"/>}
    </button>)}
  </div>
}

// 账号管理：以游戏角色 ID（解析自官方分享链接）区分账号，支持添加多个角色、切换与解绑。
export function AccountsPanel({ accounts, active, onSelect, onChanged }: Props) {
  const [link, setLink] = useState('')
  const [replaceToken, setReplaceToken] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [showImport, setShowImport] = useState(accounts.length === 0)
  const [deleting, setDeleting] = useState<GameAccount | null>(null)

  // 链接含凭据，只在内存中交给本端后端解析；成功后立即清空输入框。
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy('import'); setError('')
    try {
      const r = await fetch('/api/accounts/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Ark-Request': '1' },
        body: JSON.stringify({ url: link, replace: replaceToken }),
      })
      const j = await r.json()
      if (!r.ok) throw Error(j.error || '导入失败')
      setLink(''); setReplaceToken(false); setShowImport(false)
      onChanged(j.message, j.account?.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '导入失败，请重试。')
    } finally {
      setBusy('')
    }
  }

  // 解绑会删除服务器上该角色的登录凭据和待确认记录，需二次确认。
  async function unbind(account: GameAccount) {
    setBusy('delete'); setError('')
    try {
      const r = await fetch('/api/accounts/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Ark-Request': '1' },
        body: JSON.stringify({ id: account.id }),
      })
      const j = await r.json()
      if (!r.ok) throw Error(j.error || '解绑失败')
      setDeleting(null)
      onChanged(j.message, j.accounts?.[0]?.id || '')
    } catch (e) {
      setError(e instanceof Error ? e.message : '解绑失败，请重试。')
    } finally {
      setBusy('')
    }
  }

  return <>
    <section className="panel account-panel">
      <div className="panel-head">
        <div><h2>游戏账号</h2><p>每个游戏角色单独保存凭据、待确认记录与定时计划。</p></div>
        <span className={'badge ' + (accounts.length ? 'yes' : '')}>{accounts.length ? `${accounts.length} 个角色` : '未绑定'}</span>
      </div>
      {accounts.length === 0
        ? <div className="empty"><UsersRound size={24}/><b>还没有绑定游戏账号</b><span>粘贴官方活动分享链接，即可添加第一个角色。</span></div>
        : <div className="acct-cards">{accounts.map(account => <article key={account.id}
            className={'acct-card' + (account.id === active ? ' current' : '')}>
            <div className="acct-card-top">
              <AccountAvatar id={account.id} role={account.role} size="lg"/>
              <div className="acct-card-name">
                <h3>{account.role}</h3>
                <p>{place(account)}</p>
              </div>
              {account.id === active && <span className="acct-tag">当前</span>}
            </div>
            <dl className="acct-meta">
              <div><dt><CreditCard size={13}/>角色 ID</dt><dd>{account.id}</dd></div>
              <div><dt>添加时间</dt><dd>{formatAdded(account.added) || '未记录'}</dd></div>
            </dl>
            <div className="acct-card-actions">
              {account.id === active
                ? <span className="acct-using"><span className="online-dot"/>正在使用</span>
                : <Button className="refresh acct-switch-action" onClick={() => onSelect(account.id)}>切换到此角色</Button>}
              <button className="unbind" disabled={!!busy} onClick={() => { setError(''); setDeleting(account) }} title="解绑该角色">
                <Trash2 size={15}/>解绑
              </button>
            </div>
          </article>)}
          <button className={'acct-add' + (showImport ? ' open' : '')} onClick={() => { setError(''); setShowImport(!showImport) }}>
            <span><Plus size={18}/></span>
            <b>{showImport ? '收起添加表单' : '添加账号'}</b>
            <small>支持同时绑定多个角色</small>
          </button>
        </div>}
      {error && <div role="alert" className="account-error"><AlertCircle size={16}/><span>{error}</span></div>}
      {showImport && <form className="import-form" onSubmit={submit}>
        <div className="import-head"><Link2 size={16}/><b>粘贴活动分享链接</b><span>同一角色重复导入会更新 token，不同角色会新增为独立账号。</span></div>
        <label htmlFor="share" className="sr-only">活动分享链接</label>
        <textarea id="share" value={link} onChange={e => setLink(e.target.value)} placeholder="在此粘贴从官方 App 复制的活动分享链接" autoComplete="off" required/>
        <p>链接含登录凭据，将加密传输到你自己的服务器，请勿公开分享。原始 token 不会回显。</p>
        <label className="token-confirm"><input type="checkbox" checked={replaceToken} onChange={e => setReplaceToken(e.target.checked)}/>如该角色已绑定，允许用新链接更新它的登录凭据</label>
        <div>
          <Button type="submit" disabled={!!busy || !link.trim()}>{busy === 'import' ? '正在验证…' : '验证并保存'}</Button>
          <Button type="button" variant="outline" onClick={() => { setLink(''); setShowImport(false) }}>取消</Button>
        </div>
      </form>}
      <div className="hint"><LockKeyhole size={16}/><span>只接受命运方舟官方域名的 HTTPS 链接；凭据按角色分别保存，互不影响。首次添加的角色会自动开启每小时执行，可在定时设置为每个角色单独调整。</span></div>
      {deleting && <div className="modal-backdrop" role="presentation">
        <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="unbind-title">
          <div className="dialog-head"><span className="dialog-icon danger"><Trash2 size={18}/></span><h2 id="unbind-title">解绑「{deleting.role}」？</h2></div>
          <p>将删除服务器上该角色的登录凭据、待确认记录和独立定时计划，之后不再查询或执行该角色。运行历史会保留，重新导入链接即可再次绑定。</p>
          <div>
            <Button variant="outline" onClick={() => setDeleting(null)}>取消</Button>
            <Button onClick={() => unbind(deleting)} disabled={!!busy}>{busy === 'delete' ? '正在解绑…' : '确认解绑'}</Button>
          </div>
        </div>
      </div>}
    </section>
  </>
}
