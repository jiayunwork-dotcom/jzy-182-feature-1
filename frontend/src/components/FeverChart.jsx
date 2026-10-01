import React, { useMemo, useState } from 'react'

// Fever chart: x = critical chain completion %, y = project buffer consumed %.
// Two threshold rays split green / yellow / red. Clicking a trajectory point
// shows every task's remaining duration at that moment.
const W = 560
const H = 420
const PAD = { l: 56, r: 20, t: 20, b: 44 }

const ZONE_COLORS = { green: '#bbf7d0', yellow: '#fde68a', red: '#fecaca' }
const DOT = { green: '#16a34a', yellow: '#d97706', red: '#dc2626' }

function zoneAt(x, y, yLine, rLine) {
  if (y <= (yLine / 100) * x + 1e-9) return 'green'
  if (y >= (rLine / 100) * x - 1e-9) return 'red'
  return 'yellow'
}

export default function FeverChart({ data, onSelectPoint }) {
  const [selected, setSelected] = useState(null)
  const yLine = data.yellow_line
  const rLine = data.red_line
  const points = data.points || []

  const xToPx = (x) => PAD.l + (x / 100) * (W - PAD.l - PAD.r)
  const yToPx = (y) => H - PAD.b - (Math.min(100, y) / 100) * (H - PAD.t - PAD.b)

  // colored polygon bands between the two rays
  const bands = useMemo(() => {
    const x0 = PAD.l
    const x1 = W - PAD.r
    const y0 = H - PAD.b
    const y1 = PAD.t
    const yellowRayAt100 = yToPx(yLine)
    const redRayAt100 = yToPx(rLine)
    return [
      // green: under yellow ray
      { color: ZONE_COLORS.green, poly: `${x0},${y0} ${x1},${y0} ${x1},${yellowRayAt100}` },
      // yellow: between rays
      {
        color: ZONE_COLORS.yellow,
        poly: `${x0},${y0} ${x1},${yellowRayAt100} ${x1},${redRayAt100}`,
      },
      // red: above red ray
      { color: ZONE_COLORS.red, poly: `${x0},${y0} ${x1},${redRayAt100} ${x1},${y1} ${x0},${y1}` },
    ]
  }, [yLine, rLine])

  const selectPoint = (p) => {
    setSelected(p)
    onSelectPoint && onSelectPoint(p)
  }

  const pathD = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xToPx(p.cc_percent)} ${yToPx(p.buffer_percent)}`)
    .join(' ')

  return (
    <div className="fever-wrap">
      <svg width={W} height={H} className="fever">
        {bands.map((b, i) => (
          <polygon key={i} points={b.poly} fill={b.color} opacity={0.55} />
        ))}

        {/* threshold ray lines */}
        <line x1={xToPx(0)} y1={yToPx(0)} x2={xToPx(100)} y2={yToPx(yLine)}
          stroke="#ca8a04" strokeWidth={1.5} strokeDasharray="5 3" />
        <line x1={xToPx(0)} y1={yToPx(0)} x2={xToPx(100)} y2={yToPx(rLine)}
          stroke="#dc2626" strokeWidth={1.5} strokeDasharray="5 3" />

        {/* axes */}
        <line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} stroke="#374151" />
        <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={H - PAD.b} stroke="#374151" />

        {[0, 25, 50, 75, 100].map((v) => (
          <g key={`x${v}`}>
            <text x={xToPx(v)} y={H - PAD.b + 18} fontSize={10} textAnchor="middle" fill="#4b5563">{v}%</text>
          </g>
        ))}
        {[0, 25, 50, 75, 100].map((v) => (
          <text key={`y${v}`} x={PAD.l - 8} y={yToPx(v) + 3} fontSize={10} textAnchor="end" fill="#4b5563">
            {v}%
          </text>
        ))}
        <text x={(W) / 2} y={H - 6} fontSize={12} textAnchor="middle" fill="#111827">
          关键链完成百分比
        </text>
        <text x={14} y={H / 2} fontSize={12} textAnchor="middle" fill="#111827"
          transform={`rotate(-90 14 ${H / 2})`}>
          项目缓冲消耗百分比
        </text>

        {/* trajectory */}
        {points.length > 1 && <path d={pathD} fill="none" stroke="#1f2937" strokeWidth={1.5} />}

        {points.map((p, i) => {
          const cx = xToPx(p.cc_percent)
          const cy = yToPx(p.buffer_percent)
          const active = selected && selected.report_id === p.report_id && selected.timestamp === p.timestamp
          return (
            <g key={`${p.report_id || 'start'}-${i}`} style={{ cursor: 'pointer' }}
              onClick={() => selectPoint(p)}>
              {p.buffer_percent > 100 && (
                <text x={cx} y={PAD.t - 4} fontSize={9} fill="#b91c1c" textAnchor="middle">
                  缓冲已耗尽 ↑
                </text>
              )}
              <circle cx={cx} cy={cy} r={active ? 7 : 4.5}
                fill={DOT[p.zone]} stroke="#111827" strokeWidth={active ? 2 : 0.6} />
            </g>
          )
        })}
      </svg>

      <div className="legend">
        <span><i style={{ background: ZONE_COLORS.green }} />绿区（≤黄线射线）</span>
        <span><i style={{ background: ZONE_COLORS.yellow }} />黄区</span>
        <span><i style={{ background: ZONE_COLORS.red }} />红区（≥红线射线）</span>
        <span>当前：<b className={`zone-text ${points.length ? points[points.length - 1].zone : 'green'}`}>
          {points.length ? zoneLabel(points[points.length - 1].zone) : '绿区'}
        </b></span>
      </div>

      {selected && (
        <div className="point-detail">
          <div className="point-head">
            <span>汇报 {selected.report_id || '（计划起点）'}</span>
            <span>{selected.timestamp ? new Date(selected.timestamp).toLocaleString() : ''}</span>
            <span className={`zone-text ${selected.zone}`}>{zoneLabel(selected.zone)}</span>
            <button onClick={() => setSelected(null)}>×</button>
          </div>
          <div className="point-metrics">
            完成 {selected.cc_percent.toFixed(1)}% ｜ 缓冲消耗 {selected.buffer_percent.toFixed(1)}%
            ｜ slip {selected.slip} 工作日
          </div>
          <table className="task-state-table">
            <thead>
              <tr><th>任务</th><th>关键链</th><th>状态</th><th>剩余工期</th><th>偏差(工作日)</th></tr>
            </thead>
            <tbody>
              {selected.task_states.map((s) => (
                <tr key={s.name} className={s.is_critical ? 'cc-row' : ''}>
                  <td>{s.name}</td>
                  <td>{s.is_critical ? '是' : ''}</td>
                  <td>{statusLabel(s.status)}</td>
                  <td>{s.remaining === null || s.remaining === undefined ? '—' : s.remaining}</td>
                  <td className={s.slip > 0 ? 'late' : s.slip < 0 ? 'early' : ''}>{s.slip}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function zoneLabel(z) {
  return { green: '绿区', yellow: '黄区', red: '红区' }[z]
}
function statusLabel(s) {
  return { pending: '未开始', in_progress: '进行中', completed: '已完成' }[s] || s
}
