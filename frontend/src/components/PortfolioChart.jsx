// Portfolio timeline: every member is drawn at the backend-computed offset;
// the drum resource's load step curve and its capacity line are overlaid.
// Pure presentation — offsets, profiles and warnings all come from the server.
import React, { useMemo, useState } from 'react'

const DAY_W = 22
const LABEL_W = 170
const ROW_H = 28
const CC_FILL = '#ef4444'
const NORM_FILL = '#60a5fa'
const PB_FILL = '#f59e0b'
const FB_FILL = '#34d399'
const LOAD_OK = '#10b981'
const LOAD_OVER = '#ef4444'

function niceMax(x) {
  return Math.max(1, Math.ceil(x))
}

export default function PortfolioChart({ portfolio }) {
  const [hover, setHover] = useState(null)

  const { members, maxDay } = useMemo(() => {
    const members = [...(portfolio.members || [])].sort((a, b) => a.position - b.position)
    const lastTask = Math.max(
      ...members.flatMap((m) => m.tasks.map((t) => t.finish)),
      0,
    )
    const lastBuf = Math.max(
      ...members.flatMap((m) => m.buffers.map((b) => b.finish)),
      0,
    )
    const horizon = portfolio.timeline?.horizon ?? 0
    return { members, maxDay: niceMax(Math.max(lastTask, lastBuf, horizon)) }
  }, [portfolio])

  const drum = portfolio.drum_profile
  const width = LABEL_W + maxDay * DAY_W + 20
  const memberHeight = members.length * ROW_H
  const drumHeight = 64
  const height = memberHeight + drumHeight + 40

  const ticks = []
  for (let d = 0; d <= maxDay; d += Math.max(1, Math.round(maxDay / 20))) ticks.push(d)
  if (ticks[ticks.length - 1] !== maxDay) ticks.push(maxDay)
  const xAt = (day) => LABEL_W + day * DAY_W

  // scale the drum load curve so capacity sits at 3/4 of the strip height
  const cap = drum ? drum.capacity : 1
  const yScale = cap > 0 ? (drumHeight - 18) / Math.max(cap, 1) : 1
  const loadY = (load) => drumHeight - 8 - load * yScale
  const loadPath = drum
    ? drum.pieces.map((p, i) => {
        const x0 = xAt(p.start)
        const x1 = xAt(p.finish)
        const y = loadY(p.load)
        const start = i === 0 ? `M ${x0} ${loadY(0)} L ${x0} ${y}` : `L ${x0} ${y}`
        return `${start} L ${x1} ${y}`
      }).join(' ') + ` L ${xAt(drum.pieces.at(-1)?.finish ?? 0)} ${loadY(0)}`
    : ''

  return (
    <div className="gantt-wrap">
      <svg width={width} height={height} className="gantt">
        {ticks.map((d) => (
          <g key={d}>
            <line x1={xAt(d)} y1={18} x2={xAt(d)} y2={height - 6}
              stroke="#e5e7eb" strokeWidth={1} />
            <text x={xAt(d)} y={12} fontSize={10} textAnchor="middle" fill="#6b7280">{d}</text>
          </g>
        ))}

        {/* member lanes */}
        {members.map((m, i) => {
          const y = 22 + i * ROW_H
          return (
            <g key={m.project_id}>
              <text x={LABEL_W - 8} y={y + 15} textAnchor="end" fontSize={11} fill="#111827">
                #{m.position + 1} {m.project_name}
              </text>
              {m.tasks.map((t) => {
                const x = xAt(t.start)
                const w = Math.max(2, (t.finish - t.start) * DAY_W)
                return (
                  <rect
                    key={t.name}
                    x={x} y={y + 4} width={w} height={ROW_H - 10} rx={3}
                    fill={t.is_critical ? CC_FILL : NORM_FILL} opacity={0.9}
                    onMouseEnter={() => setHover({ kind: 'task', member: m, data: t, y })}
                    onMouseLeave={() => setHover(null)}
                  >
                    <title>{`${m.project_name} · ${t.name}：第 ${t.start}–${t.finish} 工作日${t.is_critical ? '（关键链）' : ''}`}</title>
                  </rect>
                )
              })}
              {m.buffers.filter((b) => b.kind === 'project').map((b) => (
                <rect
                  key="pb"
                  x={xAt(b.start)} y={y + 4}
                  width={Math.max(2, b.size * DAY_W)} height={ROW_H - 10} rx={3}
                  fill={PB_FILL} opacity={0.55}
                >
                  <title>{`${m.project_name} · 项目缓冲 ${b.size} 工作日`}</title>
                </rect>
              ))}
            </g>
          )
        })}

        {/* offset markers as vertical guides through the lanes */}
        {members.map((m) => (
          <line
            key={`off-${m.project_id}`}
            x1={xAt(m.offset)} y1={18} x2={xAt(m.offset)} y2={22 + memberHeight}
            stroke="#7c3aed" strokeWidth={1.2} strokeDasharray="5 3"
          />
        ))}

        {/* drum load strip */}
        {drum && (
          <g transform={`translate(0, ${22 + memberHeight + 6})`}>
            <text x={LABEL_W - 8} y={14} textAnchor="end" fontSize={11} fill="#374151">
              鼓：{drum.resource}
            </text>
            <line x1={LABEL_W} y1={drumHeight - 8} x2={xAt(maxDay)} y2={drumHeight - 8}
              stroke="#9ca3af" strokeWidth={1} />
            <path d={loadPath} fill={LOAD_OK} opacity={0.25} stroke="none" />
            {drum.pieces.map((p) => (
              <rect
                key={`${p.start}-${p.finish}`}
                x={xAt(p.start)}
                y={loadY(p.load)}
                width={Math.max(0, (p.finish - p.start) * DAY_W)}
                height={Math.max(0, p.load * yScale)}
                fill={p.load > cap + 1e-9 ? LOAD_OVER : LOAD_OK}
                opacity={0.35}
              />
            ))}
            {/* capacity line */}
            <line
              x1={LABEL_W} y1={loadY(cap)} x2={xAt(maxDay)} y2={loadY(cap)}
              stroke="#b91c1c" strokeWidth={2}
            />
            <text x={xAt(maxDay) + 4} y={loadY(cap) + 3} fontSize={10} fill="#b91c1c">
              容量 {cap}
            </text>
            <text x={LABEL_W + 4} y={12} fontSize={10} fill="#065f46">
              峰值 {drum.peak} / {cap}{drum.overloaded ? '（超额）' : ''}
            </text>
          </g>
        )}
      </svg>

      {hover && hover.kind === 'task' && (
        <div className="gantt-tip">
          <strong>{hover.member.project_name} · {hover.data.name}</strong>
          <div>组合第 {hover.data.start}–{hover.data.finish} 个工作日（项目内偏移 {hover.member.offset}）</div>
          <div>{hover.data.is_critical ? '关键链任务' : '非关键任务'}</div>
        </div>
      )}

      <div className="legend">
        <span><i style={{ background: CC_FILL }} />关键链</span>
        <span><i style={{ background: NORM_FILL }} />非关键任务</span>
        <span><i style={{ background: PB_FILL, opacity: 0.6 }} />项目缓冲（随项目平移）</span>
        <span><i style={{ background: LOAD_OK, opacity: 0.5 }} />鼓负荷</span>
        <span><i className="dash-red" />鼓容量线</span>
        <span><i className="dash-purple" />项目起步偏移</span>
      </div>
    </div>
  )
}
