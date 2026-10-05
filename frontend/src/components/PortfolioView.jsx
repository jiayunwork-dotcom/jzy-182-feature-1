import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import PortfolioChart from './PortfolioChart.jsx'

function emptyDraft() {
  return { name: '', resources: [{ name: '', capacity: 1 }], drum: '' }
}

export default function PortfolioView() {
  const [portfolios, setPortfolios] = useState([])
  const [projects, setProjects] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [draft, setDraft] = useState(emptyDraft())
  const [formErrors, setFormErrors] = useState([])
  const [joinErrors, setJoinErrors] = useState([])
  const [joinProjectId, setJoinProjectId] = useState('')
  const [mappingRows, setMappingRows] = useState([])
  const [editingPool, setEditingPool] = useState(false)

  const refresh = useCallback(async () => {
    const [pf, pj] = await Promise.all([api.listPortfolios(), api.listProjects()])
    setPortfolios(pf.portfolios)
    setProjects(pj.projects)
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const openPortfolio = useCallback(async (id) => {
    setSelectedId(id)
    setJoinErrors([])
    const d = await api.getPortfolio(id)
    setDetail(d)
    setDraft({
      name: d.name,
      resources: d.resources.map((r) => ({ ...r })),
      drum: d.drum_mode === 'manual' ? d.drum : '',
    })
  }, [])

  useEffect(() => {
    if (selectedId) openPortfolio(selectedId).catch(() => {})
  }, [selectedId, openPortfolio])

  const submitCreate = async () => {
    setFormErrors([])
    try {
      const payload = {
        name: draft.name,
        resources: draft.resources.map((r) => ({ name: r.name, capacity: Number(r.capacity) })),
      }
      if (draft.drum) payload.drum = draft.drum
      const body = await api.createPortfolio(payload)
      await refresh()
      await openPortfolio(body.id)
    } catch (e) {
      setFormErrors(e.body?.errors || [{ field: '$', message: String(e) }])
    }
  }

  const submitPoolUpdate = async () => {
    setFormErrors([])
    try {
      const payload = {
        name: draft.name,
        resources: draft.resources.map((r) => ({ name: r.name, capacity: Number(r.capacity) })),
      }
      if (draft.drum) payload.drum = draft.drum
      await api.updatePortfolio(selectedId, payload)
      setEditingPool(false)
      await openPortfolio(selectedId)
    } catch (e) {
      setFormErrors(e.body?.errors || [{ field: '$', message: String(e) }])
    }
  }

  // ----- join form: when a project is picked, load its input and pre-fill
  // one mapping row per project resource (auto-suggested when names match) --
  const [projectInputs, setProjectInputs] = useState({})
  const selectedJoinInput = projectInputs[joinProjectId] || null

  const onPickProject = async (id) => {
    setJoinProjectId(id)
    setJoinErrors([])
    setMappingRows([])
    if (!id) return
    let input = projectInputs[id]
    if (!input) {
      try {
        const detail = await api.getProject(id)
        input = detail.input
        setProjectInputs((m) => ({ ...m, [id]: input }))
      } catch {
        return
      }
    }
    setMappingRows(
      (input.resources || []).map((r) => ({ from: r.name, to: poolHas(r.name) ? r.name : '' })),
    )
  }

  const poolHas = (name) => (detail?.resources || []).some((r) => r.name === name)

  const submitJoin = async () => {
    setJoinErrors([])
    const shared_resources = {}
    for (const row of mappingRows) {
      if (row.to) shared_resources[row.from] = row.to
    }
    try {
      await api.joinPortfolio(selectedId, { project_id: joinProjectId, shared_resources })
      setJoinProjectId('')
      setMappingRows([])
      await Promise.all([refresh(), openPortfolio(selectedId)])
    } catch (e) {
      setJoinErrors(e.body?.errors || [{ field: '$', message: String(e) }])
    }
  }

  const addResRow = () => setDraft({ ...draft, resources: [...draft.resources, { name: '', capacity: 1 }] })
  const updateRes = (i, key, value) =>
    setDraft({
      ...draft,
      resources: draft.resources.map((r, j) => (j === i ? { ...r, [key]: value } : r)),
    })

  const drumLocked = (detail?.members || []).length > 0
  const poolNames = (detail?.resources || []).map((r) => r.name)

  return (
    <div className="portfolio-view">
      <div className="portfolio-layout">
        <aside className="portfolio-list">
          <h3>项目组合</h3>
          <ul className="project-list">
            {portfolios.map((p) => (
              <li key={p.id} className={p.id === selectedId ? 'selected' : ''}>
                <button onClick={() => openPortfolio(p.id)} title={p.name}>
                  {p.name}
                  <span className="muted small">（{p.member_count} 个项目{p.drum ? ` · 鼓 ${p.drum}` : ''}）</span>
                </button>
              </li>
            ))}
            {portfolios.length === 0 && <li className="muted">还没有组合</li>}
          </ul>
        </aside>

        <section className="portfolio-main">
          {!selectedId && (
            <div>
              <h2>新建组合</h2>
              <p className="hint">
                组合拥有自己的共享资源池。项目加入时声明哪些资源取自池子，后端按鼓（最紧的共享资源）
                把各项目整体错开排入；鼓由管理员指定，或留空按负荷自动识别（第一个项目加入时冻结）。
              </p>
              <PortfolioForm
                draft={draft} setDraft={setDraft} addResRow={addResRow} updateRes={updateRes}
                drumLocked={false} poolNames={[]}
                onSubmit={submitCreate} errors={formErrors} submitText="建立组合"
              />
            </div>
          )}

          {selectedId && detail && (
            <>
              <div className="section-head">
                <h2>{detail.name}</h2>
                <div className="kpis">
                  <span>鼓 <b>{detail.drum || '未定'}</b>（{detail.drum_mode === 'manual' ? '管理员指定' : '按负荷自动'}）</span>
                  <span>成员 <b>{detail.members.length}</b></span>
                  <span>组合跨度 <b>{detail.timeline.horizon}</b> 工作日</span>
                </div>
              </div>

              <div className="pool-box">
                <div className="pool-head">
                  <h3>共享资源池</h3>
                  {!editingPool ? (
                    <button className="mini" onClick={() => setEditingPool(true)}>维护资源池</button>
                  ) : (
                    <span>
                      <button className="mini primary" onClick={submitPoolUpdate}>保存</button>{' '}
                      <button className="mini" onClick={() => { setEditingPool(false); openPortfolio(selectedId) }}>取消</button>
                    </span>
                  )}
                </div>
                {!editingPool ? (
                  <ul className="pool-list">
                    {detail.resources.map((r) => (
                      <li key={r.name}>
                        {r.name} <span className="muted">× {r.capacity}</span>
                        {detail.drum === r.name && <em className="drum-tag">鼓</em>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <PortfolioForm
                    draft={draft} setDraft={setDraft} addResRow={addResRow} updateRes={updateRes}
                    drumLocked={drumLocked} poolNames={poolNames} embedded
                    onSubmit={submitPoolUpdate} errors={formErrors} submitText="保存资源池"
                  />
                )}
              </div>

              <h3>加入项目（按鼓错峰排入）</h3>
              <div className="join-box">
                <select value={joinProjectId} onChange={(e) => onPickProject(e.target.value)}>
                  <option value="">选择已录入的项目…</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
                {joinProjectId && (
                  <table className="mapping-table">
                    <thead>
                      <tr><th>项目资源</th><th>取自池中资源（留空＝项目私有）</th></tr>
                    </thead>
                    <tbody>
                      {!selectedJoinInput && (
                        <tr><td colSpan={2} className="muted">读取项目资源中…</td></tr>
                      )}
                      {selectedJoinInput && (selectedJoinInput.resources || []).length === 0 && (
                        <tr><td colSpan={2} className="muted">该项目没有声明资源。</td></tr>
                      )}
                      {mappingRows.map((row, i) => (
                        <tr key={row.from}>
                          <td>{row.from}</td>
                          <td>
                            <input
                              list="pool-names"
                              value={row.to}
                              onChange={(e) =>
                                setMappingRows((rows) =>
                                  rows.map((r, j) => (j === i ? { ...r, to: e.target.value } : r)),
                                )
                              }
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <datalist id="pool-names">
                  {poolNames.map((n) => <option key={n} value={n} />)}
                </datalist>
                {joinProjectId && (
                  <button className="primary" onClick={submitJoin}>错峰排入</button>
                )}
                {joinErrors.length > 0 && <ErrorList errors={joinErrors} />}
              </div>

              {detail.warnings.length > 0 && (
                <div className="warn-box">
                  <b>其它共享资源冲突（本版只提示，不据此推延项目）：</b>
                  <ul>
                    {detail.warnings.map((w) => (
                      <li key={w.resource}>
                        {w.message}
                        {' '}
                        {w.windows.map((win, i) => (
                          <code key={i}>
                            第 {win.start}–{win.finish} 工作日占用 {win.load}/{w.capacity}
                          </code>
                        ))}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {detail.members.length > 0 ? (
                <>
                  <h3>组合时间轴</h3>
                  <PortfolioChart portfolio={detail} />
                  <table className="member-table compact">
                    <thead>
                      <tr>
                        <th>#</th><th>项目</th><th>起步偏移</th><th>链长</th>
                        <th>计划完成</th><th>关键链</th><th>共享资源映射</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...detail.members].sort((a, b) => a.position - b.position).map((m) => (
                        <tr key={m.project_id}>
                          <td>{m.position + 1}</td>
                          <td>{m.project_name}</td>
                          <td><b>{m.offset}</b></td>
                          <td>{m.makespan}</td>
                          <td>{m.offset + m.makespan}</td>
                          <td>{m.critical_chain.join(' → ')}</td>
                          <td className="muted">
                            {Object.entries(m.shared_resources).map(([k, v]) => `${k}→${v}`).join('，') || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              ) : (
                <p className="muted">还没有项目加入。</p>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}

function ErrorList({ errors }) {
  return (
    <div className="error-box">
      <b>输入被拒收：</b>
      <ul>
        {errors.map((e, i) => (
          <li key={i}><code>{e.field}</code> — {e.message}</li>
        ))}
      </ul>
    </div>
  )
}

function PortfolioForm({ draft, setDraft, addResRow, updateRes, drumLocked, poolNames, onSubmit, errors, submitText, embedded }) {
  return (
    <div className="portfolio-form">
      <div className="form-row">
        <label>组合名
          <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </label>
        <label>鼓资源（留空＝按负荷自动识别）
          <input
            list={embedded ? 'pool-names-form' : undefined}
            placeholder="留空自动"
            value={draft.drum}
            disabled={drumLocked}
            onChange={(e) => setDraft({ ...draft, drum: e.target.value })}
          />
        </label>
      </div>
      {embedded && (
        <datalist id="pool-names-form">
          {poolNames.map((n) => <option key={n} value={n} />)}
        </datalist>
      )}
      {drumLocked && <p className="hint">已有项目加入，鼓已冻结，不能再改。</p>}

      <h4>资源池 <button className="mini" onClick={addResRow}>＋</button></h4>
      {draft.resources.map((r, i) => (
        <div className="form-row" key={i}>
          <input placeholder="资源名" value={r.name} onChange={(e) => updateRes(i, 'name', e.target.value)} />
          <input type="number" min={0} placeholder="可用数量" value={r.capacity}
            onChange={(e) => updateRes(i, 'capacity', e.target.value)} />
        </div>
      ))}
      <button className="primary" onClick={onSubmit}>{submitText}</button>
      {errors.length > 0 && <ErrorList errors={errors} />}
    </div>
  )
}
