import React, { useState } from 'react'
import { AlertCircle, Link2, LockKeyhole, Trash2, UsersRound } from 'lucide-react'
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

  return <section className="panel account-panel">
    <div className="panel-head">
      <div><h2>游戏账号</h2><p>每个游戏角色单独保存凭据，可同时管理多个账号。</p></div>
      <span className={'badge ' + (accounts.length ? 'yes' : '')}>{accounts.length ? `已绑定 ${accounts.length} 个角色` : '未绑定'}</span>
    </div>
    {accounts.length === 0
      ? <div className="empty"><UsersRound size={23}/><b>还没有绑定游戏账号</b><span>粘贴官方活动分享链接，即可添加第一个角色。</span></div>
      : <div className="account-list">{accounts.map(account => <div className={'account-row' + (account.id === active ? ' current' : '')} key={account.id}>
          <div className="account-icon"><UsersRound size={22}/></div>
          <div className="account-main">
            <h3>{account.role}{account.id === active && <span className="current-tag">当前账号</span>}</h3>
            <p>{[account.area, account.server].filter(Boolean).join(' · ') || '区服未提供'} · 角色 ID {account.id}{formatAdded(account.added) ? ` · 添加于 ${formatAdded(account.added)}` : ''}</p>
          </div>
          <div className="account-actions">
            {account.id !== active && <Button className="refresh" onClick={() => onSelect(account.id)}>切换到此角色</Button>}
            <button className="unbind" disabled={!!busy} onClick={() => { setError(''); setDeleting(account) }} title="解绑该角色">
              <Trash2 size={15}/>解绑
            </button>
          </div>
        </div>)}</div>}
    <div className="account-toolbar">
      <Button className="import-btn" onClick={() => { setError(''); setShowImport(!showImport) }}>
        <Link2 size={16}/>{showImport ? '收起添加表单' : '添加账号'}
      </Button>
      <span className="account-tip">同一角色重复导入会更新 token；不同角色会新增为独立账号。</span>
    </div>
    {error && <div role="alert" className="account-error"><AlertCircle size={16}/><span>{error}</span></div>}
    {showImport && <form className="import-form" onSubmit={submit}>
      <label htmlFor="share">活动分享链接</label>
      <textarea id="share" value={link} onChange={e => setLink(e.target.value)} placeholder="在此粘贴包含账号信息的分享链接" autoComplete="off" required/>
      <p>链接含登录凭据，将加密传输到服务器，请勿公开分享。原始 token 不会回显。</p>
      <label className="token-confirm"><input type="checkbox" checked={replaceToken} onChange={e => setReplaceToken(e.target.checked)}/>如该角色已绑定，允许用新链接更新它的登录凭据</label>
      <div>
        <Button type="submit" disabled={!!busy || !link.trim()}>{busy === 'import' ? '正在验证…' : '验证并保存'}</Button>
        <Button type="button" variant="outline" onClick={() => { setLink(''); setShowImport(false) }}>取消</Button>
      </div>
    </form>}
    <div className="hint"><LockKeyhole size={16}/><span>只接受命运方舟官方域名的 HTTPS 链接；每个用户独立保存凭据，账号之间互不影响。首次添加角色后自动开启每小时随机分钟执行，可在定时设置调整。</span></div>
    {deleting && <div className="modal-backdrop" role="presentation">
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="unbind-title">
        <h2 id="unbind-title">解绑「{deleting.role}」？</h2>
        <p>将删除服务器上该角色的登录凭据和待确认记录，之后不再查询或执行该角色。运行历史会保留，重新导入链接即可再次绑定。</p>
        <div>
          <Button variant="outline" onClick={() => setDeleting(null)}>取消</Button>
          <Button onClick={() => unbind(deleting)} disabled={!!busy}>{busy === 'delete' ? '正在解绑…' : '确认解绑'}</Button>
        </div>
      </div>
    </div>}
  </section>
}
