<script setup>
import { ref, onMounted, computed } from 'vue'

const data = ref(null)
const error = ref(null)

const URL = import.meta.env.DEV
  ? '/api/strategy_vs_benchmark_us.json'
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/strategy_vs_benchmark_us.json'

onMounted(async () => {
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('no data')
    data.value = await res.json()
  } catch (e) {
    error.value = e.message
  }
})

const W = 720, H = 280, PAD = 36

// 三條曲線：strategy / qqq / spy
const lines = computed(() => {
  if (!data.value) return []
  const s = data.value.series
  const all = s.flatMap(p => [p.strategy, p.qqq, p.spy].filter(v => v != null))
  const min = Math.min(...all), max = Math.max(...all)
  const range = max - min === 0 ? 1 : max - min
  const x = i => PAD + (i / (s.length - 1)) * (W - 2 * PAD)
  const y = v => H - PAD - ((v - min) / range) * (H - 2 * PAD)
  const mk = key => s.map((p, i) => `${x(i)},${y(p[key])}`).join(' ')
  return [
    { key: 'strategy', label: '動能策略', color: '#34d399', pts: mk('strategy') },
    { key: 'qqq', label: 'QQQ 買進持有', color: '#60a5fa', pts: mk('qqq') },
    { key: 'spy', label: 'SPY 買進持有', color: '#9ca3af', pts: mk('spy') },
  ]
})

const rows = computed(() => {
  if (!data.value) return []
  const m = data.value.metrics
  return [
    { name: '動能策略', cls: 'strat', ...m.strategy },
    { name: 'QQQ', cls: 'qqq', ...m.QQQ },
    { name: 'SPY', cls: 'spy', ...m.SPY },
  ]
})

const fmt = v => (v > 0 ? '+' : '') + v + '%'
</script>

<template>
  <section class="section" v-if="data">
    <div class="section-header">
      <h2 class="section-title">📊 主動 vs 被動：動能策略 vs 買進持有</h2>
      <span class="period-badge">{{ data.start }} ~ {{ data.end }}</span>
    </div>

    <div class="glass-panel compare-panel">
      <svg :viewBox="`0 0 ${W} ${H}`" class="compare-chart" preserveAspectRatio="xMidYMid meet">
        <polyline v-for="ln in lines" :key="ln.key" :points="ln.pts"
                  fill="none" :stroke="ln.color" stroke-width="2"
                  vector-effect="non-scaling-stroke" stroke-linejoin="round" />
      </svg>

      <div class="legend">
        <span v-for="ln in lines" :key="ln.key" class="legend-item">
          <span class="swatch" :style="{ background: ln.color }"></span>{{ ln.label }}
        </span>
      </div>

      <table class="compare-table">
        <thead>
          <tr><th>策略</th><th>年化</th><th>總報酬</th><th>最大回撤</th><th>夏普</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.name" :class="r.cls">
            <td class="name">{{ r.name }}</td>
            <td>{{ fmt(r.cagr) }}</td>
            <td>{{ fmt(r.total) }}</td>
            <td class="dd">{{ fmt(r.mdd) }}</td>
            <td class="sharpe">{{ r.sharpe }}</td>
          </tr>
        </tbody>
      </table>

      <p class="caveat">⚠️ {{ data.caveat }}</p>
      <p class="subnote">回測：{{ data.universe }}；{{ data.strategy_label }}。</p>
    </div>
  </section>
</template>

<style scoped>
.compare-panel { padding: 1.2rem 1.4rem; }
.period-badge { font-size: .8rem; color: #9ca3af; }
.compare-chart { width: 100%; height: auto; display: block; }
.legend { display: flex; gap: 1.2rem; flex-wrap: wrap; margin: .4rem 0 1rem; font-size: .85rem; color: #cbd5e1; }
.legend-item { display: inline-flex; align-items: center; gap: .4rem; }
.swatch { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }
.compare-table { width: 100%; border-collapse: collapse; font-size: .9rem; }
.compare-table th, .compare-table td { padding: .5rem .6rem; text-align: right; border-bottom: 1px solid rgba(255,255,255,.08); }
.compare-table th:first-child, .compare-table td.name { text-align: left; }
.compare-table .name { font-weight: 600; }
.compare-table tr.strat .name { color: #34d399; }
.compare-table tr.qqq .name { color: #60a5fa; }
.compare-table tr.spy .name { color: #9ca3af; }
.compare-table .dd { color: #f87171; }
.compare-table .sharpe { font-weight: 600; }
.caveat { margin-top: 1rem; font-size: .82rem; line-height: 1.5; color: #fbbf24; background: rgba(251,191,36,.08); padding: .7rem .9rem; border-radius: 8px; }
.subnote { margin-top: .5rem; font-size: .76rem; color: #94a3b8; }
</style>
