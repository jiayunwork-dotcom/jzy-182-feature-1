import React, { useEffect, useState } from 'react'
import { api } from '../api'
import PortfolioTimeline from './PortfolioTimeline.jsx'

// Portfolio detail: pool maintenance, joining projects, the staggered
// timeline and shared-resource conflict hints. All computation (drum,
// offsets, profiles, conflicts) is done by the backend.
export default function PortfolioView({ portfolioId, projects }) {
  const [detail, setDetail] = useState(null)
  const [errors, setErrors] = useState([])
  const [joinId, setJoinId] = useState('')
  const [editing, setEditing] = useState(false)
  const [poolDraft, setPoolDraft] = useState([])
  const [drumDraft, setDrumDraft] = useState('')

  const reload = async () => setDetail(await api.getPortfolio(portfolioId))

  useEffect(() => {
    setDetail(null)
    setErrors([])
    setEditing(false)
    reload()
  }, [portfolioId])

  if (!detail) return <div className="empty">加载中…</div>

  const memberIds = new Set(detail.members.map((m) => m.project_id))
  const candidates = projects.filter((p) => !memberIds.has(p.id))

  const showErrors = (e) => {
    if (e.body && e.body.errors) setErrors(e.body.errors)
    else setErrors([{ field: '$', message: String(e) }])
  }

  const join = async () => {
    if (!joinId) return
    setErrors([])
    try {
      await api.joinPortfolio(portfolioId, joinId)
      setJoinId('')
      await reload()
    } catch (e) {
      showErrors(e)
    }
  }

  const startEdit = () => {
    setPoolDraft(detail.pool.map((p) => ({ ...p })))
    setDrumDraft(detail.drum_pinned ? detail.drum : '')
    setEditing(true)
    setErrors([])
  }

  const savePool = async () => {
    setErrors([])
    try {
      await api.updatePortfolio(portfolioId, {
        pool: poolDraft.filter((r) => String(r.name).trim() !== '')
          .map((r) => ({ name: r.name, capacity: Number(r.capacity) })),
        drum: drumDraft === '' ? null : drumDraft,
      })
      setEditing(false)
      await reload()
    } catch (e) {
      showErrors(e)
    }
  }

  const updateRow = (i, key, value) =>
    setPoolDraft(poolDraft.map((r, j) => (j === i ? { ...r, [key]: value } : r)))

  return (
    <section>
      <div className="section-head">
        <h2>组合：{detail.name}</h2>
        <div className="kpis">
          <span>鼓资源 <b>{detail.drum ? `${detail.drum}（${detail.drum_pinned ? '指定' : '自动识别'}）` : '尚未确定（加入项目后按负荷识别）'}</b></span>
          <span>成员项目 <b>{detail.members.length}</b></span>
          <span>组合计划跨度 <b>{detail.makespan}</b> 工作日</span>
        </div>
      </div>

      {errors.length > 0 && (
        <div className="error-box">
          <b>操作被拒收：</b>
          <ul>
            {errors.map((e, i) => (
              <li key={i}><code>{e.field}</code> — {e.message}</li>
            ))}
          </ul>
        </div>
      )}

      <h3>共享资源池 {!editing && <button className="mini" onClick={startEdit}>维护</button>}</h3>
      {!editing ? (
        <table className="task-state-table compact">
          <tbody>
            {detail.pool.map((p) => (
              <tr key={p.name}>
                <td>{p.name}{detail.drum === p.name ? ' 🥁' : ''}</td>
                <td>容量 {p.capacity}</td>
              </tr>
            ))}
            {detail.pool.length === 0 && <tr><td className="muted">池为空</td></tr>}
          </tbody>
        </table>
      ) : (
        <div className="project-form">
          {poolDraft.map((r, i) => (
            <div className="form-row" key={i}>
              <input placeholder="资源名" value={r.name}
                onChange={(e) => updateRow(i, 'name', e.target.value)} />
              <input type="number" placeholder="可用数量" value={r.capacity}
                onChange={(e) => updateRow(i, 'capacity', e.target.value)} />
              <button className="mini" onClick={() => setPoolDraft(poolDraft.filter((_, j) => j !== i))}>×</button>
            </div>
          ))}
          <div className="form-row">
            <button className="mini" onClick={() => setPoolDraft([...poolDraft, { name: '', capacity: 1 }])}>＋资源</button>
            <label>鼓资源
              <select value={drumDraft} onChange={(e) => setDrumDraft(e.target.value)}>
                <option value="">自动（按负荷识别）</option>
                {poolDraft.filter((r) => String(r.name).trim()).map((r, i) => (
                  <option key={i} value={r.name}>{r.name}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="form-row">
            <button className="primary" style={{ width: 'auto' }} onClick={savePool}>保存资源池</button>
            <button onClick={() => setEditing(false)}>取消</button>
          </div>
        </div>
      )}

      <h3>把项目加入组合</h3>
      <div className="form-row">
        <select value={joinId} onChange={(e) => setJoinId(e.target.value)}>
          <option value="">选择项目…</option>
          {candidates.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <button onClick={join} disabled={!joinId}>按鼓错峰排入</button>
        {candidates.length === 0 && <span className="muted">所有项目都已加入</span>}
      </div>
      <p className="hint">
        加入时后端校验项目对池中资源的占用（名字要在池里、单任务需求不超过池子总量），
        然后在组合时间轴上给出起步偏移：项目自身的关键链、链长与缓冲不变，鼓在任何时刻的占用不超过池容量。
      </p>

      <h3>组合时间轴</h3>
      {detail.members.length === 0 ? (
        <div className="empty"><p>还没有成员项目。</p></div>
      ) : (
        <PortfolioTimeline portfolio={detail} />
      )}

      <h3>其它共享资源冲突提示</h3>
      {detail.conflicts.length === 0 ? (
        <p className="hint">池中各共享资源在排布后的占用都不超过容量。</p>
      ) : (
        <div className="error-box">
          <b>以下共享资源在排布后超过池容量（本版本只按鼓错峰，请人工调整）：</b>
          <ul>
            {detail.conflicts.map((c, i) => (
              <li key={i}>
                <code>{c.resource}</code>：组合第 {c.start}–{c.finish} 个工作日占用 {c.used}，容量 {c.capacity}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
