import React, { useMemo, useState } from 'react'

// Portfolio timeline: one row per member project (tasks shifted by the
// frozen offset computed in the backend), plus the drum load curve with the
// pool capacity line overlaid. Pure presentation.
const DAY_W = 22
const ROW_H = 26
const LABEL_W = 170
const LOAD_H = 90

const CC_FILL = '#ef4444'
const NORM_FILL = '#60a5fa'
const PB_FILL = '#f59e0b'
const LOAD_FILL = '#93c5fd'
const CAP_STROKE = '#b91c1c'

export default function PortfolioTimeline({ portfolio }) {
  const [hover, setHover] = useState(null)
  const members = portfolio.members || []
  const profile = portfolio.drum_profile || []
  const capacity = portfolio.drum_capacity

  const maxDay = useMemo(() => {
    const lastTask = Math.max(0, ...members.flatMap((m) => m.tasks.map((t) => t.finish)))
    const lastBuf = Math.max(0, ...members.flatMap((m) => (m.buffers || []).map((b) => b.finish)))
    const lastLoad = Math.max(0, ...profile.map((p) => p.finish))
    return Math.max(1, Math.ceil(Math.max(lastTask, lastBuf, lastLoad)))
  }, [members, profile])

  const width = LABEL_W + maxDay * DAY_W + 20
  const rowsH = members.length * ROW_H
  const loadTop = 30 + rowsH + 24
  const height = loadTop + LOAD_H + 26
  const gridEnd = LABEL_W + maxDay * DAY_W

  const maxLoad = Math.max(capacity || 0, ...profile.map((p) => p.used), 1)
  const yOf = (v) => loadTop + LOAD_H - (v / maxLoad) * (LOAD_H - 8)

  const ticks = []
  for (let d = 0; d <= maxDay; d += Math.max(1, Math.round(maxDay / 24))) ticks.push(d)
  if (ticks[ticks.length - 1] !== maxDay) ticks.push(maxDay)

  // stepped drum load area
  let loadPath = ''
  if (profile.length > 0) {
    loadPath = `M ${LABEL_W + profile[0].start * DAY_W} ${yOf(0)}`
    for (const seg of profile) {
      loadPath += ` L ${LABEL_W + seg.start * DAY_W} ${yOf(seg.used)}`
      loadPath += ` L ${LABEL_W + seg.finish * DAY_W} ${yOf(seg.used)}`
      loadPath += ` L ${LABEL_W + seg.finish * DAY_W} ${yOf(0)}`
    }
  }

  return (
    <div className="gantt-wrap">
      <svg width={width} height={height} className="gantt">
        {ticks.map((d) => (
          <g key={d}>
            <line x1={LABEL_W + d * DAY_W} y1={20} x2={LABEL_W + d * DAY_W} y2={height - 10}
              stroke="#e5e7eb" strokeWidth={1} />
            <text x={LABEL_W + d * DAY_W} y={14} fontSize={10} textAnchor="middle" fill="#6b7280">{d}</text>
          </g>
        ))}

        {/* one row per project, tasks at their staggered positions */}
        {members.map((m, i) => {
          const y = 24 + i * ROW_H
          return (
            <g key={m.project_id}>
              <text x={LABEL_W - 8} y={y + 13} textAnchor="end" fontSize={12} fill="#111827">
                {m.name}（偏移 {m.offset}）
              </text>
              {m.tasks.map((t) => (
                <rect key={t.name} x={LABEL_W + t.start * DAY_W} y={y + 3}
                  width={Math.max(2, (t.finish - t.start) * DAY_W)} height={ROW_H - 8} rx={3}
                  fill={t.is_critical ? CC_FILL : NORM_FILL} opacity={0.9}
                  onMouseEnter={() => setHover({ m, t, y })}
                  onMouseLeave={() => setHover(null)} />
              ))}
              {(m.buffers || []).filter((b) => b.kind === 'project').map((b) => (
                <rect key="pb" x={LABEL_W + b.start * DAY_W} y={y + 3}
                  width={Math.max(2, b.size * DAY_W)} height={ROW_H - 8} rx={3}
                  fill={PB_FILL} opacity={0.55} />
              ))}
            </g>
          )
        })}

        {/* drum load curve with capacity line */}
        <text x={LABEL_W - 8} y={loadTop - 8} textAnchor="end" fontSize={12} fill="#111827">
          鼓负荷{portfolio.drum ? `（${portfolio.drum}）` : ''}
        </text>
        {profile.length > 0 && (
          <path d={loadPath} fill={LOAD_FILL} fillOpacity={0.45} stroke="#2563eb" strokeWidth={1.5} />
        )}
        {capacity !== null && capacity !== undefined && (
          <g>
            <line x1={LABEL_W} y1={yOf(capacity)} x2={gridEnd} y2={yOf(capacity)}
              stroke={CAP_STROKE} strokeWidth={1.5} strokeDasharray="6 3" />
            <text x={gridEnd - 4} y={yOf(capacity) - 4} fontSize={10} fill={CAP_STROKE} textAnchor="end">
              容量 {capacity}
            </text>
          </g>
        )}
        <line x1={LABEL_W} y1={yOf(0)} x2={gridEnd} y2={yOf(0)} stroke="#9ca3af" />
        {[...new Set([0, capacity].filter((v) => v !== null && v !== undefined))].map((v) => (
          <text key={v} x={LABEL_W - 6} y={yOf(v) + 3} fontSize={10} textAnchor="end" fill="#6b7280">{v}</text>
        ))}
        <line x1={LABEL_W} y1={height - 10} x2={gridEnd} y2={height - 10} stroke="#9ca3af" />
      </svg>

      {hover && (
        <div className="gantt-tip">
          <strong>{hover.m.name} · {hover.t.name}</strong>
          <div>组合第 {hover.t.start}–{hover.t.finish} 个工作日（偏移 {hover.m.offset}）</div>
          <div>{hover.t.is_critical ? '关键链任务' : '非关键任务'}</div>
        </div>
      )}

      <div className="legend">
        <span><i style={{ background: CC_FILL }} />关键链</span>
        <span><i style={{ background: NORM_FILL }} />非关键任务</span>
        <span><i style={{ background: PB_FILL }} />项目缓冲</span>
        <span><i style={{ background: LOAD_FILL }} />鼓资源负荷</span>
        <span><i className="dash" style={{ background: 'repeating-linear-gradient(90deg, #b91c1c 0 4px, transparent 4px 7px)' }} />池容量线</span>
      </div>
    </div>
  )
}
