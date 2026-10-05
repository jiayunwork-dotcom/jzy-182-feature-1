import React, { useState } from 'react'

const DEMO = {
  name: '四任务示例',
  resources: [{ name: '工程师', capacity: 1 }],
  tasks: [
    { name: 'A', predecessors: [], resources: {}, aggressive: 4, conservative: 8 },
    { name: 'B', predecessors: ['A'], resources: { 工程师: 1 }, aggressive: 6, conservative: 12 },
    { name: 'C', predecessors: ['A'], resources: { 工程师: 1 }, aggressive: 3, conservative: 6 },
    { name: 'D', predecessors: ['B', 'C'], resources: {}, aggressive: 2, conservative: 4 },
  ],
  yellow_line: 50,
  red_line: 80,
}

function emptyProject() {
  return { name: '', resources: [], tasks: [], yellow_line: 50, red_line: 80 }
}

export default function ProjectForm({ onCreate }) {
  const [mode, setMode] = useState('form')
  const [draft, setDraft] = useState(emptyProject())
  const [jsonText, setJsonText] = useState('')
  const [errors, setErrors] = useState([])

  const addResource = () =>
    setDraft({ ...draft, resources: [...draft.resources, { name: '', capacity: 1, shared: false }] })
  const addTask = () =>
    setDraft({
      ...draft,
      tasks: [
        ...draft.tasks,
        { name: '', predecessors: '', resources: '{}', aggressive: 1, conservative: 2 },
      ],
    })

  const updateRes = (i, key, value) => {
    const resources = draft.resources.map((r, j) =>
      j === i ? { ...r, [key]: key === 'capacity' ? Number(value) : value } : r,
    )
    setDraft({ ...draft, resources })
  }
  const updateTask = (i, key, value) => {
    const tasks = draft.tasks.map((t, j) => (j === i ? { ...t, [key]: value } : t))
    setDraft({ ...draft, tasks })
  }

  const normalize = (d) => ({
    name: d.name,
    yellow_line: Number(d.yellow_line),
    red_line: Number(d.red_line),
    resources: d.resources.map((r) => ({
      name: r.name,
      capacity: Number(r.capacity),
      shared: !!r.shared,
    })),
    tasks: d.tasks.map((t) => ({
      name: t.name,
      predecessors: String(t.predecessors || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      resources: typeof t.resources === 'string' ? JSON.parse(t.resources || '{}') : t.resources,
      aggressive: Number(t.aggressive),
      conservative: Number(t.conservative),
    })),
  })

  const submit = async (payload) => {
    setErrors([])
    try {
      await onCreate(payload)
      setDraft(emptyProject())
      setJsonText('')
    } catch (e) {
      if (e.body && e.body.errors) setErrors(e.body.errors)
      else setErrors([{ field: '$', message: String(e) }])
    }
  }

  return (
    <div className="project-form">
      <div className="tabs">
        <button className={mode === 'form' ? 'active' : ''} onClick={() => setMode('form')}>表单录入</button>
        <button className={mode === 'json' ? 'active' : ''} onClick={() => setMode('json')}>JSON 导入</button>
        <button onClick={() => submit(DEMO)}>载入四任务示例</button>
      </div>

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

      {mode === 'form' ? (
        <div>
          <div className="form-row">
            <label>项目名 <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
            <label>黄线 % <input type="number" min="0" max="100" value={draft.yellow_line}
              onChange={(e) => setDraft({ ...draft, yellow_line: e.target.value })} /></label>
            <label>红线 % <input type="number" min="0" max="100" value={draft.red_line}
              onChange={(e) => setDraft({ ...draft, red_line: e.target.value })} /></label>
          </div>

          <h4>资源 <button className="mini" onClick={addResource}>＋</button></h4>
          {draft.resources.map((r, i) => (
            <div className="form-row" key={i}>
              <input placeholder="资源名" value={r.name} onChange={(e) => updateRes(i, 'name', e.target.value)} />
              <input type="number" placeholder="可用数量" value={r.capacity}
                onChange={(e) => updateRes(i, 'capacity', e.target.value)} />
              <label className="check" title="声明该资源取自组合的共享资源池，而不是项目私有">
                <input type="checkbox" checked={!!r.shared}
                  onChange={(e) => updateRes(i, 'shared', e.target.checked)} /> 取自组合池
              </label>
            </div>
          ))}

          <h4>任务 <button className="mini" onClick={addTask}>＋</button></h4>
          <div className="task-rows">
            {draft.tasks.map((t, i) => (
              <div className="task-row" key={i}>
                <input placeholder="名称" value={t.name} onChange={(e) => updateTask(i, 'name', e.target.value)} />
                <input placeholder="紧前（逗号分隔）" value={t.predecessors}
                  onChange={(e) => updateTask(i, 'predecessors', e.target.value)} />
                <input placeholder='资源占用 {"工程师":1}' value={t.resources}
                  onChange={(e) => updateTask(i, 'resources', e.target.value)} />
                <input type="number" placeholder="激进" value={t.aggressive}
                  onChange={(e) => updateTask(i, 'aggressive', e.target.value)} />
                <input type="number" placeholder="保守" value={t.conservative}
                  onChange={(e) => updateTask(i, 'conservative', e.target.value)} />
              </div>
            ))}
          </div>
          <button className="primary" onClick={() => submit(normalize(draft))}>排计划</button>
          {draft.tasks.some((t) => {
            try { JSON.parse(t.resources || '{}'); return false } catch { return true }
          }) && <span className="warn">资源占用字段必须是 JSON，例如 {`{"工程师":1}`}</span>}
        </div>
      ) : (
        <div>
          <textarea rows={14} className="json-input"
            placeholder='{"name":"...","resources":[...],"tasks":[...],"yellow_line":50,"red_line":80}'
            value={jsonText} onChange={(e) => setJsonText(e.target.value)} />
          <div>
            <button className="primary" onClick={() => submit(JSON.parse(jsonText))}>导入并排计划</button>
            <button onClick={() => setJsonText(JSON.stringify(DEMO, null, 2))}>填入示例 JSON</button>
          </div>
        </div>
      )}
    </div>
  )
}
