import React, { useEffect, useState } from 'react'

// Report submission. The report log is append-only on the server; the panel
// shows the deterministic stream order so users can see out-of-order arrivals
// being inserted correctly.
export default function ReportPanel({ projectId, tasks, onSubmitted }) {
  const [form, setForm] = useState({
    report_id: '',
    task: '',
    timestamp: nowLocal(),
    remaining: '',
    completed: false,
  })
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [history, setHistory] = useState([])

  async function refresh() {
    const res = await fetch(`/api/projects/${projectId}/reports`)
    const body = await res.json()
    setHistory(body.reports || [])
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  async function submit(e) {
    e.preventDefault()
    setError(null)
    setNotice(null)
    const payload = {
      report_id: form.report_id.trim(),
      task: form.task,
      timestamp: new Date(form.timestamp).toISOString(),
    }
    if (form.completed) payload.completed = true
    else payload.remaining = Number(form.remaining)
    try {
      const res = await fetch(`/api/projects/${projectId}/reports`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const body = await res.json()
      if (!res.ok) {
        setError(body.errors?.map((x) => `${x.field}: ${x.message}`).join('；') || '提交失败')
        return
      }
      setNotice(body.duplicate ? '该汇报编号已存在，按幂等处理，状态未改变' : '已追加汇报并刷新状态')
      setForm({ report_id: '', task: form.task, timestamp: nowLocal(), remaining: '', completed: false })
      await refresh()
      onSubmitted && onSubmitted(body)
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="report-panel">
      <form onSubmit={submit} className="report-form">
        <label>汇报编号
          <input value={form.report_id} required
            onChange={(e) => setForm({ ...form, report_id: e.target.value })} />
        </label>
        <label>任务
          <select value={form.task} required onChange={(e) => setForm({ ...form, task: e.target.value })}>
            <option value="">选择任务…</option>
            {tasks.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}{t.is_critical ? '（关键链）' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>汇报时刻
          <input type="datetime-local" value={form.timestamp} required
            onChange={(e) => setForm({ ...form, timestamp: e.target.value })} />
        </label>
        <label className="check">
          <input type="checkbox" checked={form.completed}
            onChange={(e) => setForm({ ...form, completed: e.target.checked })} />
          已完成
        </label>
        {!form.completed && (
          <label>剩余工期(工作日)
            <input type="number" min="0" step="0.5" value={form.remaining} required
              onChange={(e) => setForm({ ...form, remaining: e.target.value })} />
          </label>
        )}
        <button className="primary" type="submit">提交汇报</button>
      </form>

      {error && <div className="error-box">{error}</div>}
      {notice && <div className="notice-box">{notice}</div>}

      <h4>汇报流（按时刻、编号确定排序，追加不改写）</h4>
      <table className="history-table">
        <thead>
          <tr><th>#</th><th>汇报编号</th><th>任务</th><th>时刻(UTC)</th><th>剩余</th><th>完成</th></tr>
        </thead>
        <tbody>
          {history.map((r, i) => (
            <tr key={r.report_id}>
              <td>{i + 1}</td>
              <td>{r.report_id}</td>
              <td>{r.task}</td>
              <td>{r.timestamp}</td>
              <td>{r.remaining === null ? '—' : r.remaining}</td>
              <td>{r.completed ? '✓' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function nowLocal() {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}
