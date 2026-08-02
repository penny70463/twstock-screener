<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import CalendarPicker from './CalendarPicker.vue'

const data = ref(null)
const loading = ref(true)
const error = ref(null)

const startDate = ref('')
const endDate = ref('')
const searchQuery = ref('')
const activeList = ref('gainers') // 'gainers' or 'losers'
const topN = ref(50)
const sortKey = ref('change_pct')
const sortOrder = ref('desc')

// 快捷區間定義
const presets = [
  { label: '近 1 週', days: 7 },
  { label: '近 1 月', days: 30 },
  { label: '近 3 月', days: 90 },
  { label: '近半年', days: 180 },
  { label: '今年 (YTD)', ytd: true },
  { label: '近 1 年', days: 365 },
]

function getUrl() {
  const base = import.meta.env.DEV ? '/api' : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results'
  return `${base}/price_history_tw.json`
}

async function fetchData() {
  loading.value = true
  error.value = null
  try {
    const res = await fetch(getUrl(), { cache: 'no-store' })
    if (!res.ok) throw new Error('尚無歷史收盤資料，請稍後再試')
    data.value = await res.json()
    // 預設選「今年」
    applyPreset(presets.find(p => p.ytd))
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(fetchData)

const availableDates = computed(() => data.value?.dates || [])

function applyPreset(preset) {
  if (!availableDates.value.length) return
  const dates = availableDates.value
  const lastDate = dates[dates.length - 1]
  endDate.value = lastDate

  if (preset.ytd) {
    const year = lastDate.substring(0, 4)
    const firstOfYear = dates.find(d => d.startsWith(year))
    startDate.value = firstOfYear || dates[0]
  } else {
    const target = new Date(lastDate)
    target.setDate(target.getDate() - preset.days)
    const targetStr = target.toISOString().substring(0, 10)
    const found = dates.find(d => d >= targetStr)
    startDate.value = found || dates[0]
  }
}

function isPresetActive(preset) {
  if (!availableDates.value.length || !startDate.value || !endDate.value) return false
  const dates = availableDates.value
  const lastDate = dates[dates.length - 1]
  if (endDate.value !== lastDate) return false

  if (preset.ytd) {
    const year = lastDate.substring(0, 4)
    const firstOfYear = dates.find(d => d.startsWith(year))
    return startDate.value === firstOfYear
  }

  const target = new Date(lastDate)
  target.setDate(target.getDate() - preset.days)
  const targetStr = target.toISOString().substring(0, 10)
  const found = dates.find(d => d >= targetStr)
  return startDate.value === (found || dates[0])
}

// 計算排行
const rankings = computed(() => {
  if (!data.value || !startDate.value || !endDate.value) return []
  const dates = data.value.dates
  const startIdx = dates.indexOf(startDate.value)
  const endIdx = dates.indexOf(endDate.value)
  if (startIdx < 0 || endIdx < 0 || startIdx >= endIdx) return []

  const results = []
  for (const [code, info] of Object.entries(data.value.stocks)) {
    const closeStart = info.closes[startIdx]
    const closeEnd = info.closes[endIdx]
    if (closeStart == null || closeEnd == null || closeStart <= 0) continue
    const changePct = ((closeEnd - closeStart) / closeStart * 100)
    results.push({
      code,
      name: info.name,
      industry: info.industry,
      market: info.market,
      close_start: closeStart,
      close_end: closeEnd,
      change_pct: Math.round(changePct * 100) / 100,
    })
  }
  return results
})

const setSort = (key) => {
  if (sortKey.value === key) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortOrder.value = key === 'change_pct'
      ? (activeList.value === 'gainers' ? 'desc' : 'asc')
      : 'asc'
  }
}

const displayList = computed(() => {
  let list = [...rankings.value]

  // 漲幅榜 / 跌幅榜先取 topN 候選
  if (activeList.value === 'gainers') {
    list.sort((a, b) => b.change_pct - a.change_pct)
  } else {
    list.sort((a, b) => a.change_pct - b.change_pct)
  }

  // 搜尋時顯示全部符合，不搜尋時只取前 N
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    list = list.filter(s =>
      s.code.toLowerCase().includes(q) ||
      s.name.toLowerCase().includes(q) ||
      (s.industry || '').toLowerCase().includes(q)
    )
  } else {
    list = list.slice(0, topN.value)
  }

  // 再依使用者選的欄位排序
  list.sort((a, b) => {
    let valA = a[sortKey.value]
    let valB = b[sortKey.value]
    if (valA === valB) return 0
    if (typeof valA === 'string' && typeof valB === 'string') {
      return sortOrder.value === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA)
    }
    return sortOrder.value === 'asc' ? valA - valB : valB - valA
  })

  return list
})

const totalStocks = computed(() => rankings.value.length)
const avgChange = computed(() => {
  if (!rankings.value.length) return 0
  const sum = rankings.value.reduce((s, r) => s + r.change_pct, 0)
  return Math.round(sum / rankings.value.length * 100) / 100
})
const medianChange = computed(() => {
  if (!rankings.value.length) return 0
  const sorted = [...rankings.value].map(r => r.change_pct).sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2 * 100) / 100
})
const upCount = computed(() => rankings.value.filter(r => r.change_pct > 0).length)
const downCount = computed(() => rankings.value.filter(r => r.change_pct < 0).length)

// 切換漲跌榜時重置排序
watch(activeList, (val) => {
  sortKey.value = 'change_pct'
  sortOrder.value = val === 'gainers' ? 'desc' : 'asc'
})
</script>

<template>
  <section class="section">
    <div v-if="loading" class="empty-state">載入歷史收盤資料中...</div>
    <div v-else-if="error" class="glass-panel empty-state">⚠️ {{ error }}</div>
    <template v-else-if="data">
      <div class="section-header">
        <h2 class="section-title">區間漲跌幅排行</h2>
        <span class="range-date-info">
          📅 資料涵蓋：{{ data.date_range[0] }} ~ {{ data.date_range[1] }}（{{ availableDates.length }} 個交易日）
        </span>
      </div>

      <p class="logic-desc">
        💡 選擇起訖日期（或使用快捷按鈕），即時計算全台股池 {{ Object.keys(data.stocks).length }} 檔個股的區間漲跌幅，
        依序列出漲幅榜與跌幅榜。計算方式：<strong>（訖日收盤 − 起日收盤）÷ 起日收盤 × 100%</strong>。
      </p>

      <!-- 日期選擇與快捷按鈕 -->
      <div class="glass-panel date-controls">
        <div class="date-pickers">
          <div class="date-field">
            <label>起始日</label>
            <CalendarPicker v-model="startDate" :availableDates="availableDates" placeholder="選擇起始日" />
          </div>
          <span class="date-arrow">→</span>
          <div class="date-field">
            <label>結束日</label>
            <CalendarPicker v-model="endDate" :availableDates="availableDates" placeholder="選擇結束日" />
          </div>
        </div>
        <div class="preset-buttons">
          <button
            v-for="p in presets" :key="p.label"
            class="preset-btn"
            :class="{ active: isPresetActive(p) }"
            @click="applyPreset(p)"
          >{{ p.label }}</button>
        </div>
      </div>

      <!-- 統計摘要 -->
      <div class="stats-row" v-if="rankings.length > 0">
        <div class="glass-panel stat-card">
          <span class="stat-label">涵蓋股數</span>
          <span class="stat-value">{{ totalStocks }}</span>
        </div>
        <div class="glass-panel stat-card">
          <span class="stat-label">平均漲跌</span>
          <span class="stat-value" :class="avgChange >= 0 ? 'text-up' : 'text-down'">
            {{ avgChange >= 0 ? '+' : '' }}{{ avgChange }}%
          </span>
        </div>
        <div class="glass-panel stat-card">
          <span class="stat-label">中位數</span>
          <span class="stat-value" :class="medianChange >= 0 ? 'text-up' : 'text-down'">
            {{ medianChange >= 0 ? '+' : '' }}{{ medianChange }}%
          </span>
        </div>
        <div class="glass-panel stat-card">
          <span class="stat-label">上漲 / 下跌</span>
          <span class="stat-value">
            <span class="text-up">{{ upCount }}</span> / <span class="text-down">{{ downCount }}</span>
          </span>
        </div>
      </div>

      <!-- 漲幅榜 / 跌幅榜 切換 + 搜尋 -->
      <div class="list-controls">
        <div class="list-toggle">
          <button
            class="toggle-btn"
            :class="{ active: activeList === 'gainers' }"
            @click="activeList = 'gainers'"
          >🔺 漲幅榜 Top {{ topN }}</button>
          <button
            class="toggle-btn"
            :class="{ active: activeList === 'losers' }"
            @click="activeList = 'losers'"
          >🔻 跌幅榜 Top {{ topN }}</button>
        </div>
        <input
          type="text"
          v-model="searchQuery"
          placeholder="搜尋代號、名稱或產業..."
          class="search-input"
        />
      </div>

      <!-- 排行表格 -->
      <div class="glass-panel table-container">
        <table class="stock-table" v-if="displayList.length > 0">
          <thead>
            <tr>
              <th class="rank-col">#</th>
              <th @click="setSort('code')" class="sortable">
                代號 <span v-if="sortKey==='code'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
              </th>
              <th @click="setSort('name')" class="sortable">
                名稱 <span v-if="sortKey==='name'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
              </th>
              <th @click="setSort('industry')" class="sortable">
                產業別 <span v-if="sortKey==='industry'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
              </th>
              <th @click="setSort('close_start')" class="sortable num-col">
                起日收盤 <span v-if="sortKey==='close_start'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
              </th>
              <th @click="setSort('close_end')" class="sortable num-col">
                訖日收盤 <span v-if="sortKey==='close_end'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
              </th>
              <th @click="setSort('change_pct')" class="sortable num-col">
                漲跌幅% <span v-if="sortKey==='change_pct'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(stock, idx) in displayList" :key="stock.code">
              <td class="rank-col font-mono">{{ idx + 1 }}</td>
              <td class="font-mono">{{ stock.code }}</td>
              <td class="font-bold">{{ stock.name }}</td>
              <td><span class="industry-tag">{{ stock.industry }}</span></td>
              <td class="num-col">{{ stock.close_start }}</td>
              <td class="num-col">{{ stock.close_end }}</td>
              <td class="num-col font-bold" :class="stock.change_pct >= 0 ? 'text-up' : 'text-down'">
                {{ stock.change_pct >= 0 ? '+' : '' }}{{ stock.change_pct }}%
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state">
          {{ startDate && endDate ? '所選區間無有效資料（可能起訖日相同或順序錯誤）。' : '請選擇起訖日期。' }}
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.range-date-info {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.date-controls {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.25rem;
  margin-bottom: 1.5rem;
}

.date-pickers {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.date-field {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.date-field label {
  font-size: 0.8rem;
  color: var(--text-muted);
  font-weight: 500;
}

.date-dropdown {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid var(--panel-border);
  color: var(--text-main);
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  font-size: 0.9rem;
  cursor: pointer;
  min-width: 160px;
}

.date-dropdown:focus {
  outline: none;
  border-color: var(--accent-blue);
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.3);
}

.date-arrow {
  font-size: 1.2rem;
  color: var(--text-muted);
  padding-bottom: 0.4rem;
}

.preset-buttons {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.preset-btn {
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.3);
  color: #93c5fd;
  padding: 0.4rem 0.85rem;
  border-radius: 20px;
  font-size: 0.82rem;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
}

.preset-btn:hover {
  background: rgba(59, 130, 246, 0.25);
  border-color: rgba(59, 130, 246, 0.6);
  transform: translateY(-1px);
}

.preset-btn.active {
  background: rgba(59, 130, 246, 0.35);
  border-color: var(--accent-blue);
  color: #fff;
  font-weight: 600;
}

/* 統計摘要卡片 */
.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}

.stat-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0.85rem;
  text-align: center;
}

.stat-label {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin-bottom: 0.3rem;
}

.stat-value {
  font-size: 1.15rem;
  font-weight: 700;
}

/* 漲跌榜切換 */
.list-controls {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  gap: 1rem;
  flex-wrap: wrap;
}

.list-toggle {
  display: flex;
  gap: 0.5rem;
}

.toggle-btn {
  background: rgba(30, 41, 59, 0.7);
  border: 1px solid var(--panel-border);
  color: var(--text-muted);
  padding: 0.5rem 1rem;
  border-radius: 8px;
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

.toggle-btn:hover {
  border-color: rgba(255, 255, 255, 0.25);
}

.toggle-btn.active {
  background: rgba(59, 130, 246, 0.2);
  border-color: var(--accent-blue);
  color: var(--text-main);
  font-weight: 600;
}

.rank-col {
  width: 40px;
  text-align: center;
  color: var(--text-muted);
}

.num-col {
  text-align: right;
}

/* 響應式 */
@media (max-width: 640px) {
  .date-pickers {
    flex-direction: column;
    align-items: stretch;
  }
  .date-arrow {
    text-align: center;
    padding: 0;
  }
  .list-controls {
    flex-direction: column;
    align-items: stretch;
  }
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
