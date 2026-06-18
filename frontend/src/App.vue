<script setup>
import { ref, onMounted, computed } from 'vue'
import PortfolioReview from './components/PortfolioReview.vue'

const activeMarket = ref('TW')
const activeTab = ref('screener')
const data = ref(null)
const loading = ref(true)
const error = ref(null)

const availableDates = ref([])
const selectedDate = ref('latest')

const fetchData = async () => {
  loading.value = true
  error.value = null
  const fileName = selectedDate.value === 'latest' 
    ? `latest_${activeMarket.value.toLowerCase()}.json` 
    : `${selectedDate.value}_${activeMarket.value.toLowerCase()}.json`
  const DATA_URL = import.meta.env.DEV 
    ? `/api/${fileName}` 
    : `https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/${fileName}`
  try {
    const response = await fetch(DATA_URL, { cache: 'no-store' })
    if (!response.ok) throw new Error('Failed to fetch data')
    data.value = await response.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

const fetchDates = async () => {
  const fileName = `available_dates_${activeMarket.value.toLowerCase()}.json`
  const URL = import.meta.env.DEV 
    ? `/api/${fileName}` 
    : `https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/${fileName}`
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (res.ok) {
      availableDates.value = await res.json()
    }
  } catch (e) {
    console.error("No dates found")
  }
}

import { watch } from 'vue'

onMounted(() => {
  fetchDates()
  fetchData()
})

watch(selectedDate, () => {
  fetchData()
})

watch(activeMarket, () => {
  selectedDate.value = 'latest'
  fetchDates()
  fetchData()
})

const marketLabel = computed(() => data.value?.market_state?.label || '未知')
const marketClass = computed(() => {
  if (marketLabel.value === '多頭') return 'bullish'
  if (marketLabel.value === '空頭') return 'bearish'
  return 'neutral'
})

const getSignalBadges = (signalString) => {
  if (!signalString) return []
  return signalString.split('、')
}

const searchQuery = ref('')
const sortKey = ref('change_pct')
const sortOrder = ref('desc')

const setSort = (key) => {
  if (sortKey.value === key) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortOrder.value = 'desc'
  }
}

const availableSignals = computed(() => {
  if (!data.value || !data.value.screened) return []
  const signals = new Set()
  data.value.screened.forEach(s => {
    const badges = getSignalBadges(s['訊號'])
    badges.forEach(b => signals.add(b))
  })
  return Array.from(signals).sort()
})

const selectedSignals = ref([])

const toggleSignal = (sig) => {
  if (selectedSignals.value.includes(sig)) {
    selectedSignals.value = selectedSignals.value.filter(s => s !== sig)
  } else {
    selectedSignals.value.push(sig)
  }
}

const filteredAndSortedStocks = computed(() => {
  if (!data.value || !data.value.screened) return []
  
  let result = [...data.value.screened]

  if (selectedSignals.value.length > 0) {
    result = result.filter(s => {
      const badges = getSignalBadges(s['訊號'])
      // 必須包含所有被選取的訊號 (交集)
      return selectedSignals.value.every(sig => badges.includes(sig))
    })
  }

  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    result = result.filter(s => 
      String(s.stock_id).includes(q) || 
      String(s.stock_name).includes(q) || 
      String(s.industry_category || '').toLowerCase().includes(q) ||
      String(s['訊號'] || '').includes(q)
    )
  }

  result.sort((a, b) => {
    let valA = a[sortKey.value]
    let valB = b[sortKey.value]
    
    if (valA === valB) return 0
    if (typeof valA === 'string' && typeof valB === 'string') {
      return sortOrder.value === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA)
    }
    return sortOrder.value === 'asc' ? valA - valB : valB - valA
  })

  return result
})
// 產生 Sparkline SVG polyline points
const getSparklinePoints = (prices) => {
  if (!prices || prices.length === 0) return ''
  const max = Math.max(...prices)
  const min = Math.min(...prices)
  const range = max - min === 0 ? 1 : max - min
  
  // viewBox="0 0 100 30"
  return prices.map((p, i) => {
    const x = (i / (prices.length - 1)) * 100
    const y = 30 - ((p - min) / range) * 30
    return `${x},${y}`
  }).join(' ')
}

const isSparklineUp = (prices) => {
  if (!prices || prices.length < 2) return true
  return prices[prices.length - 1] >= prices[0]
}
</script>

<template>
  <div class="dashboard-container">
    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
      <p>載入最新資料中...</p>
    </div>
    
    <div v-else-if="error" class="error-state">
      <p>⚠️ 發生錯誤：{{ error }}</p>
    </div>

    <div v-else-if="data" class="dashboard-content">
      <header class="glass-panel hero-banner">
        <div class="hero-left">
          <div class="market-toggle">
            <button :class="{ active: activeMarket === 'TW' }" @click="activeMarket = 'TW'">🇹🇼 台股</button>
            <button :class="{ active: activeMarket === 'US' }" @click="activeMarket = 'US'">🇺🇸 美股</button>
          </div>
          <h1>{{ activeMarket === 'TW' ? '台股' : '美股' }}動能與題材掃描</h1>
          <p class="subtitle">自動化多因子選股儀表板</p>
          <p class="logic-desc">💡 <strong>長線保護短線交集邏輯</strong>：結合大盤多空動態調整門檻，嚴格篩出具備「均線多頭」的長線資優生後，再依據「單日強勢漲幅」進行交集排序。</p>
          <p class="logic-desc">⏱️ <strong>更新頻率</strong>：每個交易日自動執行選股與題材分析排程。</p>
        </div>
        <div class="hero-right">
          <div class="market-indicator" :class="marketClass">
            <span class="indicator-dot"></span>
            大盤狀態：{{ marketLabel }} (門檻 {{ data.market_state?.threshold }} 分)
          </div>
          <div class="date-selector" v-if="availableDates.length > 0">
            <label for="date-select" class="text-sm text-gray-400 mr-2">📅 歷史回顧：</label>
            <select id="date-select" v-model="selectedDate" class="date-dropdown">
              <option value="latest">最新 (Latest)</option>
              <option v-for="d in availableDates" :key="d" :value="d">{{ d }}</option>
            </select>
          </div>
          <div class="update-time mt-2">
            更新時間：{{ new Date(data.generated_at).toLocaleString() }}
          </div>
        </div>
      </header>

      <!-- Theme Clusters -->
      <section class="section">
        <h2 class="section-title">熱門題材族群</h2>
        <div class="theme-grid" v-if="data.themes && data.themes.length > 0">
          <div v-for="(theme, idx) in data.themes" :key="idx" class="glass-panel theme-card">
            <h3>{{ theme.name || '未命名題材' }}</h3>
            <p class="theme-reason">{{ theme.reason }}</p>
            <div class="theme-stocks">
              <span v-for="stock in theme.stocks" :key="stock.code" class="stock-pill">
                {{ stock.name }} ({{ stock.code }})
              </span>
            </div>
          </div>
        </div>
        <div v-else class="glass-panel empty-state">
          今日無題材分類資料。
        </div>
      </section>

      <!-- Tab Navigation -->
      <div class="tabs-container">
        <button 
          class="tab-btn" 
          :class="{ active: activeTab === 'screener' }"
          @click="activeTab = 'screener'"
        >
          🚀 強勢股掃描
        </button>
        <button 
          class="tab-btn" 
          :class="{ active: activeTab === 'portfolio' }"
          @click="activeTab = 'portfolio'"
        >
          💼 我的存股體檢
        </button>
      </div>

      <!-- Screener Tab Content -->
      <template v-if="activeTab === 'screener'">
        <!-- Leaderboard -->
        <section class="section">
        <div class="section-header">
          <h2 class="section-title">高分強勢股清單 (共 {{ filteredAndSortedStocks.length }} 檔)</h2>
          <input type="text" v-model="searchQuery" placeholder="搜尋代號、名稱、產業或訊號..." class="search-input" />
        </div>

        <!-- Signal Filter Tags -->
        <div class="signal-filters" v-if="availableSignals.length > 0">
          <span class="filter-label">技術訊號篩選：</span>
          <button 
            v-for="sig in availableSignals" :key="sig"
            class="filter-tag"
            :class="{ active: selectedSignals.includes(sig) }"
            @click="toggleSignal(sig)"
          >
            {{ sig }}
          </button>
          <button v-if="selectedSignals.length > 0" class="clear-filters" @click="selectedSignals = []">
            清除篩選
          </button>
        </div>

        <div class="glass-panel table-container">
          <table class="stock-table" v-if="filteredAndSortedStocks.length > 0">
            <thead>
              <tr>
                <th @click="setSort('stock_id')" class="sortable">代號 <span v-if="sortKey==='stock_id'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span></th>
                <th @click="setSort('stock_name')" class="sortable">名稱 <span v-if="sortKey==='stock_name'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span></th>
                <th>近20日走勢</th>
                <th @click="setSort('industry_category')" class="sortable">產業別 <span v-if="sortKey==='industry_category'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span></th>
                <th @click="setSort('change_pct')" class="sortable">今日漲幅 <span v-if="sortKey==='change_pct'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span></th>
                <th @click="setSort('總分')" class="sortable">綜合評分 <span v-if="sortKey==='總分'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span></th>
                <th>技術訊號</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="stock in filteredAndSortedStocks" :key="stock.stock_id">
                <td class="font-mono">{{ stock.stock_id }}</td>
                <td class="font-bold">{{ stock.stock_name }}</td>
                <td>
                  <svg v-if="stock.sparkline && stock.sparkline.length > 0" class="sparkline" viewBox="0 0 100 30" preserveAspectRatio="none">
                    <polyline 
                      :points="getSparklinePoints(stock.sparkline)" 
                      :stroke="isSparklineUp(stock.sparkline) ? '#4ade80' : '#f87171'"
                      fill="none" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"
                    />
                  </svg>
                </td>
                <td><span class="industry-tag">{{ stock.industry_category }}</span></td>
                <td :class="stock.change_pct > 0 ? 'text-up' : 'text-down'">
                  {{ stock.change_pct > 0 ? '+' : '' }}{{ stock.change_pct }}%
                </td>
                <td class="font-bold score-cell">{{ stock["總分"] }}</td>
                <td>
                  <div class="signal-container">
                    <span v-for="(badge, i) in getSignalBadges(stock['訊號'])" :key="i" class="signal-badge">
                      {{ badge }}
                    </span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-state">
            今日無符合條件之強勢股。
          </div>
        </div>
      </section>
      </template>

      <!-- Portfolio Tab Content -->
      <template v-if="activeTab === 'portfolio'">
        <PortfolioReview :marketState="data?.market_state" :market="activeMarket" />
      </template>
    </div>
  </div>
</template>

<style>
/* Global Styles */
:root {
  --bg-color: #0f172a;
  --panel-bg: rgba(30, 41, 59, 0.7);
  --panel-border: rgba(255, 255, 255, 0.1);
  --text-main: #f8fafc;
  --text-muted: #94a3b8;
  --accent-blue: #3b82f6;
  --up-color: #ef4444; /* 台股漲跌色 (紅漲) */
  --down-color: #22c55e; /* 台股漲跌色 (綠跌) */
  --bullish: #ef4444;
  --bearish: #22c55e;
  --neutral: #eab308;
}

body {
  margin: 0;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  background-color: var(--bg-color);
  background-image: 
    radial-gradient(circle at 15% 50%, rgba(59, 130, 246, 0.15), transparent 25%),
    radial-gradient(circle at 85% 30%, rgba(239, 68, 68, 0.1), transparent 25%);
  color: var(--text-main);
  min-height: 100vh;
}

/* Main Layout */
.dashboard-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
}

/* Tabs */
.tabs-container {
  display: flex;
  gap: 1rem;
  margin-bottom: 2rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  padding-bottom: 0.5rem;
  overflow-x: auto;
  scrollbar-width: none; /* Firefox */
  -ms-overflow-style: none; /* IE/Edge */
}
.tabs-container::-webkit-scrollbar {
  display: none; /* Chrome/Safari */
}

.tab-btn {
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 1.1rem;
  font-weight: bold;
  padding: 0.5rem 1rem;
  cursor: pointer;
  transition: all 0.2s;
  position: relative;
  white-space: nowrap;
}

.tab-btn:hover {
  color: white;
}

.tab-btn.active {
  color: var(--accent-blue);
}

.tab-btn.active::after {
  content: '';
  position: absolute;
  bottom: -0.5rem;
  left: 0;
  width: 100%;
  height: 2px;
  background: var(--accent-blue);
  border-radius: 2px;
}

/* Glassmorphism Utilities */
.glass-panel {
  background: var(--panel-bg);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--panel-border);
  border-radius: 1rem;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
}

/* Typography & Colors */
.text-up { color: var(--up-color); font-weight: 600; }
.text-down { color: var(--down-color); font-weight: 600; }
.font-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.font-bold { font-weight: 600; }

/* Hero Banner */
.hero-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 2rem;
  margin-bottom: 2rem;
  background: linear-gradient(135deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.9));
}

.hero-left h1 {
  margin: 0 0 0.5rem 0;
  font-size: 2.25rem;
  background: linear-gradient(to right, #60a5fa, #a78bfa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero-left .subtitle {
  margin: 0;
  color: var(--text-muted);
  font-size: 1.1rem;
}

.hero-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.75rem;
}

.market-toggle {
  display: inline-flex;
  background: rgba(0, 0, 0, 0.3);
  padding: 0.3rem;
  border-radius: 999px;
  margin-bottom: 1.5rem;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.market-toggle button {
  background: transparent;
  color: var(--text-muted);
  border: none;
  padding: 0.5rem 1.25rem;
  border-radius: 999px;
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.market-toggle button:hover {
  color: white;
}

.market-toggle button.active {
  background: var(--accent-blue);
  color: white;
  box-shadow: 0 2px 10px rgba(59, 130, 246, 0.4);
}

.market-indicator {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  border-radius: 2rem;
  background: rgba(255, 255, 255, 0.05);
  font-weight: 600;
  font-size: 0.95rem;
}

.indicator-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.bullish .indicator-dot { background-color: var(--bullish); box-shadow: 0 0 10px var(--bullish); }
.bearish .indicator-dot { background-color: var(--bearish); box-shadow: 0 0 10px var(--bearish); }
.neutral .indicator-dot { background-color: var(--neutral); box-shadow: 0 0 10px var(--neutral); }

.update-time {
  font-size: 0.85rem;
  color: var(--text-muted);
}

.date-selector {
  display: flex;
  align-items: center;
}

.date-dropdown {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: white;
  padding: 0.4rem 0.8rem;
  border-radius: 0.5rem;
  outline: none;
  font-size: 0.9rem;
  cursor: pointer;
}

.date-dropdown option {
  background: var(--panel-bg);
  color: white;
}

/* Sections */
.section {
  margin-bottom: 3rem;
}

.section-title {
  font-size: 1.5rem;
  margin: 0 0 1.5rem 0;
  color: var(--text-main);
  border-left: 4px solid var(--accent-blue);
  padding-left: 1rem;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
  gap: 1rem;
}

.section-header .section-title {
  margin-bottom: 0; /* Override bottom margin when inside flex header */
}

.search-input {
  background: rgba(30, 41, 59, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: white;
  padding: 0.6rem 1rem;
  border-radius: 0.5rem;
  width: 250px;
  font-size: 0.95rem;
  outline: none;
  transition: border-color 0.2s;
}

.search-input:focus {
  border-color: var(--accent-blue);
}

.sortable {
  cursor: pointer;
  user-select: none;
  transition: color 0.2s;
}

.sortable:hover {
  color: white;
}

.signal-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 1rem;
}

.filter-label {
  font-size: 0.9rem;
  color: var(--text-muted);
  margin-right: 0.5rem;
}

.filter-tag {
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid rgba(167, 139, 250, 0.3);
  color: #c4b5fd;
  padding: 0.3rem 0.8rem;
  border-radius: 999px;
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

.filter-tag:hover {
  background: rgba(167, 139, 250, 0.25);
}

.filter-tag.active {
  background: rgba(167, 139, 250, 0.8);
  color: white;
  border-color: #ddd6fe;
  box-shadow: 0 0 10px rgba(167, 139, 250, 0.4);
}

.clear-filters {
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 0.85rem;
  cursor: pointer;
  text-decoration: underline;
  padding: 0.3rem 0.5rem;
}

.clear-filters:hover {
  color: white;
}

.logic-desc {
  margin-top: 1rem;
  font-size: 0.95rem;
  color: #bae6fd;
  line-height: 1.5;
  max-width: 600px;
}

/* Theme Grid */
.theme-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 1.5rem;
}

.theme-card {
  padding: 1.5rem;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.theme-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3), 0 4px 6px -2px rgba(0, 0, 0, 0.15);
  border-color: rgba(255, 255, 255, 0.2);
}

.theme-card h3 {
  margin: 0 0 0.75rem 0;
  font-size: 1.25rem;
  color: #e2e8f0;
}

.theme-reason {
  color: var(--text-muted);
  font-size: 0.9rem;
  line-height: 1.5;
  margin-bottom: 1.25rem;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.theme-stocks {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.stock-pill {
  font-size: 0.8rem;
  padding: 0.25rem 0.75rem;
  background: rgba(59, 130, 246, 0.15);
  color: #93c5fd;
  border-radius: 999px;
  border: 1px solid rgba(59, 130, 246, 0.3);
}

/* Table */
.table-container {
  overflow-x: auto;
  padding: 1px; /* for border */
}

.stock-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}

.stock-table th, .stock-table td {
  padding: 1rem 1.25rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  white-space: nowrap; /* 防止手機版文字被擠壓成直式 */
}

.stock-table th {
  color: var(--text-muted);
  font-weight: 500;
  font-size: 0.9rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  background: rgba(0, 0, 0, 0.2);
}

.stock-table tr:last-child td {
  border-bottom: none;
}

.stock-table tbody tr {
  transition: background-color 0.15s ease;
}

.stock-table tbody tr:hover {
  background-color: rgba(255, 255, 255, 0.03);
}

.industry-tag {
  font-size: 0.8rem;
  color: #cbd5e1;
  background: rgba(255, 255, 255, 0.1);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
}

.score-cell {
  color: #fbbf24;
  font-size: 1.1rem;
}

.signal-container {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.signal-badge {
  font-size: 0.75rem;
  padding: 0.2rem 0.5rem;
  background: rgba(167, 139, 250, 0.15);
  color: #c4b5fd;
  border: 1px solid rgba(167, 139, 250, 0.3);
  border-radius: 4px;
  white-space: nowrap;
}

/* States */
.loading-state, .error-state, .empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 4rem;
  color: var(--text-muted);
  text-align: center;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid rgba(255, 255, 255, 0.1);
  border-radius: 50%;
  border-top-color: var(--accent-blue);
  animation: spin 1s ease-in-out infinite;
  margin-bottom: 1rem;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Responsive */
@media (max-width: 768px) {
  .hero-banner {
    flex-direction: column;
    align-items: flex-start;
    gap: 1.5rem;
  }
  .hero-right {
    align-items: flex-start;
  }
}

.sparkline {
  width: 80px;
  height: 30px;
  display: block;
}
</style>
