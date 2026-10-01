import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import ProjectForm from './components/ProjectForm.jsx'
import GanttChart from './components/GanttChart.jsx'
import FeverChart from './components/FeverChart.jsx'
import ReportPanel from './components/ReportPanel.jsx'

export default function App() {
  const [projects, setProjects] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [project, setProject] = useState(null)
  const [trajectory, setTrajectory] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [rule, setRule] = useState('')
  const [replayAt, setReplayAt] = useState('')
  const [replayPoint, setReplayPoint] = useState(null)

  const refreshList = useCallback(async () => {
    const body = await api.listProjects()
    setProjects(body.projects)
  }, [])

  useEffect(() => {
    refreshList()
    api.rule().then((b) => setRule(b.rule)).catch(() => {})
  }, [refreshList])

  const openProject = useCallback(async (id) => {
    setSelectedId(id)
    setShowForm(false)
    const [detail, traj] = await Promise.all([api.getProject(id), api.trajectory(id)])
    setProject(detail)
    setTrajectory(traj)
    setReplayPoint(null)
    setReplayAt('')
  }, [])

  const refreshTrajectory = useCallback(async () => {
    if (!selectedId) return
    const traj = await api.trajectory(selectedId)
    setTrajectory(traj)
    const detail = await api.getProject(selectedId)
    setProject(detail)
  }, [selectedId])

  const createProject = async (payload) => {
    const body = await api.createProject(payload)
    await refreshList()
    await openProject(body.id)
  }

  const doReplay = async () => {
    if (!replayAt || !selectedId) return
    const iso = new Date(replayAt).toISOString()
    const point = await api.state(selectedId, iso)
    setReplayPoint(point)
  }

  return (
    <div className="app">
      <header>
        <h1>关键链项目管理平台</h1>
        <p className="subtitle">Critical Chain · 资源冲突消解 · 缓冲管理 · 发烧图（计算全在后端）</p>
      </header>

      <aside className="sidebar">
        <button className="primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? '收起录入' : '＋ 新建 / 导入项目'}
        </button>
        <h3>项目列表</h3>
        <ul className="project-list">
          {projects.map((p) => (
            <li key={p.id} className={p.id === selectedId ? 'selected' : ''}>
              <button onClick={() => openProject(p.id)} title={p.name}>{p.name}</button>
            </li>
          ))}
          {projects.length === 0 && <li className="muted">还没有项目</li>}
        </ul>
        <details className="rule-details">
          <summary>排程规则（后端固定）</summary>
          <p>{rule}</p>
        </details>
      </aside>

      <main className="content">
        {showForm && <ProjectForm onCreate={createProject} />}

        {!project && !showForm && (
          <div className="empty">
            <p>从左侧新建项目，或点“载入四任务示例”快速体验：A(4)→B(6)/C(3)→D(2)，B、C 共用一名工程师。</p>
            <p>计划阶段按激进工期在考虑逻辑依赖与资源容量后排出关键链，并放置项目缓冲与汇入缓冲；执行阶段通过追加式汇报流投影出发烧图。</p>
          </div>
        )}

        {project && (
          <section>
            <div className="section-head">
              <h2>{project.name}</h2>
              <div className="kpis">
                <span>计划工期 <b>{project.plan.makespan}</b> 工作日</span>
                <span>关键链 <b>{project.plan.critical_chain.join(' → ')}</b></span>
                <span>仅依赖关键路径 <b>{project.plan.critical_path_length}</b> 天</span>
                <span>黄线 <b>{project.yellow_line}%</b> / 红线 <b>{project.red_line}%</b></span>
              </div>
            </div>

            <h3>计划甘特图</h3>
            <GanttChart plan={project.plan} />

            <h3>发烧图轨迹</h3>
            <p className="hint">点击轨迹上的点查看该时刻各任务剩余工期。关键链在执行期冻结，不随汇报重排。</p>
            {trajectory && (
              <FeverChart data={trajectory} onSelectPoint={() => {}} />
            )}

            <div className="replay-bar">
              <label>历史回放：选择时刻
                <input type="datetime-local" value={replayAt}
                  onChange={(e) => setReplayAt(e.target.value)} />
              </label>
              <button onClick={doReplay}>回放到该时刻</button>
              {replayPoint && (
                <div className="replay-result">
                  完成 {replayPoint.cc_percent.toFixed(1)}% ｜ 缓冲消耗 {replayPoint.buffer_percent.toFixed(1)}%
                  ｜ <b className={`zone-text ${replayPoint.zone}`}>
                    {({ green: '绿区', yellow: '黄区', red: '红区' })[replayPoint.zone]}
                  </b>
                  <table className="task-state-table compact">
                    <tbody>
                      {replayPoint.task_states.map((s) => (
                        <tr key={s.name}>
                          <td>{s.name}{s.is_critical ? ' ★' : ''}</td>
                          <td>{({ pending: '未开始', in_progress: '进行中', completed: '已完成' })[s.status]}</td>
                          <td>剩余 {s.remaining === null ? '—' : s.remaining}</td>
                          <td>slip {s.slip}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <h3>进度汇报</h3>
            <ReportPanel projectId={selectedId} tasks={project.plan.tasks} onSubmitted={refreshTrajectory} />
          </section>
        )}
      </main>
    </div>
  )
}
