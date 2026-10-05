import React, { useState } from 'react'

// Create a portfolio: name, shared resource pool, optional pinned drum.
// All validation happens in the backend; errors come back per field.
export default function PortfolioForm({ onCreated }) {
  const [name, setName] = useState('')
  const [pool, setPool] = useState([{ name: '', capacity: 1 }])
  const [drum, setDrum] = useState('')
  const [errors, setErrors] = useState([])

  const updateRow = (i, key, value) =>
    setPool(pool.map((r, j) => (j === i ? { ...r, [key]: key === 'capacity' ? Number(value) : value } : r)))

  const submit = async () => {
    setErrors([])
    try {
      const body = await onCreated({
        name,
        pool: pool.filter((r) => r.name.trim() !== ''),
        drum: drum === '' ? null : drum,
      })
      setName('')
      setPool([{ name: '', capacity: 1 }])
      setDrum('')
    } catch (e) {
      if (e.body && e.body.errors) setErrors(e.body.errors)
      else setErrors([{ field: '$', message: String(e) }])
    }
  }

  return (
    <div className="project-form">
      <h4>新建项目组合</h4>
      {errors.length > 0 && (
        <div className="error-box">
          <b>输入被拒收：</b>
          <ul>
            {errors.map((e, i) => (
              <li key={i}><code>{e.field}</code> — {e.message}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="form-row">
        <label>组合名 <input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>鼓资源
          <select value={drum} onChange={(e) => setDrum(e.target.value)}>
            <option value="">自动（按负荷识别）</option>
            {pool.filter((r) => r.name.trim()).map((r, i) => (
              <option key={i} value={r.name}>{r.name}</option>
            ))}
          </select>
        </label>
      </div>
      <h4>共享资源池 <button className="mini" onClick={() => setPool([...pool, { name: '', capacity: 1 }])}>＋</button></h4>
      {pool.map((r, i) => (
        <div className="form-row" key={i}>
          <input placeholder="资源名" value={r.name} onChange={(e) => updateRow(i, 'name', e.target.value)} />
          <input type="number" placeholder="可用数量" value={r.capacity}
            onChange={(e) => updateRow(i, 'capacity', e.target.value)} />
          <button className="mini" onClick={() => setPool(pool.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button className="primary" onClick={submit}>创建组合</button>
    </div>
  )
}
