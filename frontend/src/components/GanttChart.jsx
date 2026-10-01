import React, { useMemo, useState } from 'react'

// Gantt-style plan rendering. Pure presentation: the plan (start/finish,
// critical chain, buffers) is computed entirely by the backend.
const DAY_W = 22
const ROW_H = 30
const LABEL_W = 150

const CC_FILL = '#ef4444'
const NORM_FILL = '#60a5fa'
const PB_FILL = '#f59e0b'
const FB_FILL = '#34d399'
const FB_OVER = '#fca5a5'

function niceMax(x) {
  return Math.max(1, Math.ceil(x))
}

export default function GanttChart({ plan }) {
  const [hover, setHover] = useState(null)

  const { tasks, buffers, maxDay } = useMemo(() => {
    const bufs = plan.buffers || []
    const tasks = [...plan.tasks].sort((a, b) => a.start - b.start || a.name.localeCompare(b.name))
    const lastTask = Math.max(...tasks.map((t) => t.finish), 0)
    const lastBuf = Math.max(...bufs.map((b) => b.finish), 0)
    return { tasks, buffers: bufs, maxDay: niceMax(Math.max(lastTask, lastBuf)) }
  }, [plan])

  const width = LABEL_W + maxDay * DAY_W + 20
  const taskRows = tasks.length
  const height = (taskRows + buffers.length + 1) * ROW_H + 30

  const ticks = []
  for (let d = 0; d <= maxDay; d += Math.max(1, Math.round(maxDay / 20))) ticks.push(d)
  if (ticks[ticks.length - 1] !== maxDay) ticks.push(maxDay)

  const gridEnd = LABEL_W + maxDay * DAY_W

  return (
    <div className="gantt-wrap">
      <svg width={width} height={height} className="gantt">
        {/* header */}
        {ticks.map((d) => (
          <g key={d}>
            <line x1={LABEL_W + d * DAY_W} y1={20} x2={LABEL_W + d * DAY_W} y2={height - 10}
              stroke="#e5e7eb" strokeWidth={1} />
            <text x={LABEL_W + d * DAY_W} y={14} fontSize={10} textAnchor="middle" fill="#6b7280">{d}</text>
          </g>
        ))}

        {tasks.map((t, i) => {
          const y = 24 + i * ROW_H
          const x = LABEL_W + t.start * DAY_W
          const w = Math.max(2, (t.finish - t.start) * DAY_W)
          return (
            <g key={t.name}
              onMouseEnter={() => setHover({ kind: 'task', data: t, y })}
              onMouseLeave={() => setHover(null)}>
              <text x={LABEL_W - 8} y={y + 15} textAnchor="end" fontSize={12}
                fill={t.is_critical ? CC_FILL : '#111827'} fontWeight={t.is_critical ? 700 : 400}>
                {t.name}
              </text>
              <rect x={x} y={y + 4} width={w} height={ROW_H - 10} rx={3}
                fill={t.is_critical ? CC_FILL : NORM_FILL} opacity={0.9} />
              <text x={x + 4} y={y + 17} fontSize={10} fill="#0f172a">
                {t.aggressive}d
              </text>
            </g>
          )
        })}

        {buffers.map((b, i) => {
          const y = 24 + (taskRows + i) * ROW_H
          const label =
            b.kind === 'project' ? '项目缓冲 PB' : `汇入缓冲 → ${b.feeding_into}`
          const x = LABEL_W + Math.max(0, b.start) * DAY_W
          const w = Math.max(2, b.size * DAY_W)
          const fill = b.kind === 'project' ? PB_FILL : b.overflow ? FB_OVER : FB_FILL
          return (
            <g key={`${b.kind}-${b.feeding_into || ''}`}
              onMouseEnter={() => setHover({ kind: 'buffer', data: b, y })}
              onMouseLeave={() => setHover(null)}>
              <text x={LABEL_W - 8} y={y + 15} textAnchor="end" fontSize={11} fill="#374151">
                {label}
              </text>
              <rect x={x} y={y + 4} width={w} height={ROW_H - 10} rx={3}
                fill={fill} stroke={b.overflow ? '#b91c1c' : 'none'}
                strokeDasharray={b.overflow ? '4 2' : undefined} opacity={0.85} />
              <text x={x + 4} y={y + 17} fontSize={10} fill="#1f2937">{b.size}d</text>
            </g>
          )
        })}

        {/* makespan line */}
        <line x1={LABEL_W + plan.makespan * DAY_W} y1={20}
          x2={LABEL_W + plan.makespan * DAY_W} y2={height - 10}
          stroke="#111827" strokeWidth={1.5} strokeDasharray="6 3" />
        <line x1={LABEL_W} y1={height - 10} x2={gridEnd} y2={height - 10} stroke="#9ca3af" />
      </svg>

      {hover && (
        <div className="gantt-tip">
          {hover.kind === 'task' ? (
            <>
              <strong>{hover.data.name}</strong>
              <div>第 {hover.data.start}–{hover.data.finish} 个工作日（激进 {hover.data.aggressive} / 保守 {hover.data.conservative}）</div>
              <div>{hover.data.is_critical ? '关键链任务' : '非关键任务'}</div>
              {hover.data.resource_predecessors.length > 0 && (
                <div>资源等待：{hover.data.resource_predecessors.join(', ')}</div>
              )}
            </>
          ) : (
            <>
              <strong>{hover.data.kind === 'project' ? '项目缓冲' : `汇入缓冲（汇入 ${hover.data.feeding_into}）`}</strong>
              <div>大小 {hover.data.size} 个工作日</div>
              <div>区间 {Math.round(hover.data.start * 100) / 100} – {hover.data.finish}</div>
              {hover.data.overflow && <div className="warn">缓冲大于汇入点前的可用松弛，已标红</div>}
            </>
          )}
        </div>
      )}

      <div className="legend">
        <span><i style={{ background: CC_FILL }} />关键链</span>
        <span><i style={{ background: NORM_FILL }} />非关键任务</span>
        <span><i style={{ background: PB_FILL }} />项目缓冲</span>
        <span><i style={{ background: FB_FILL }} />汇入缓冲</span>
        <span><i className="dash" />虚线为计划完工点（makespan）</span>
      </div>
    </div>
  )
}
