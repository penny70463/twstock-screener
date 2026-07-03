<script setup>
import { ref, onMounted, computed } from 'vue'

const data = ref(null)
const loading = ref(true)
const error = ref(null)

// 根據開發環境決定 API 網址
const URL = import.meta.env.DEV
  ? '/api/pullback_tw.json'
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/pullback_tw.json'

onMounted(async () => {
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('尚無回檔轉強資料')
    data.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const stocks = computed(() => data.value?.screened || [])

// "2026-05" -> "5月"
const monthLabel = (ym) => `${parseInt(ym.split('-')[1], 10)}月`

// 營收月份欄位標籤（新月份在前）
const revMonths = computed(() => {
  const months = data.value?.params?.revenue_months || []
  return [...months].reverse()
})

const revOf = (stock, ym) => stock.revenue.find(r => r.month === ym) || {}

// 產生 Sparkline SVG polyline points（與 App.vue 相同邏輯）
const getSparklinePoints = (prices) => {
  if (!prices || prices.length === 0) return ''
  const max = Math.max(...prices)
  const min = Math.min(...prices)
  const range = max - min === 0 ? 1 : max - min
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
  <section class="section">
    <div v-if="loading" class="empty-state">載入回檔轉強資料中...</div>
    <div v-else-if="error" class="glass-panel empty-state">⚠️ {{ error }}</div>
    <template v-else-if="data">
      <div class="section-header">
        <h2 class="section-title">回檔轉強清單 (共 {{ stocks.length }} 檔)</h2>
        <span class="pullback-date">📅 基準日：{{ data.date }}</span>
      </div>

      <p class="logic-desc pullback-desc">
        💡 <strong>回檔轉強四條件</strong>：① 站上季線、半年線、年線
        ② 最近兩個月營收「年增且月增」
        ③ 曾自近一個月高點回檔 7% 以上
        ④ 回檔期間收盤守住季線，當日站回 5 日線並突破前一日高點。
        鎖定「基本面成長、趨勢未壞、洗完籌碼剛翻多」的個股；入選日即為型態完成的訊號日。
      </p>

      <div class="glass-panel table-container">
        <table class="stock-table" v-if="stocks.length > 0">
          <thead>
            <tr>
              <th>代號</th>
              <th>名稱</th>
              <th>近20日走勢</th>
              <th>產業別</th>
              <th>收盤價</th>
              <th>自月高回檔</th>
              <th>距季線</th>
              <th v-for="ym in revMonths" :key="ym + 'yoy'">{{ monthLabel(ym) }}營收年增</th>
              <th v-for="ym in revMonths" :key="ym + 'mom'">{{ monthLabel(ym) }}月增</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="stock in stocks" :key="stock.stock_id">
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
              <td class="font-bold">{{ stock.close }}</td>
              <td class="text-down">-{{ stock.pullback_pct }}%</td>
              <td class="text-up">+{{ stock.dist_ma60_pct }}%</td>
              <td v-for="ym in revMonths" :key="stock.stock_id + ym + 'yoy'"
                  :class="revOf(stock, ym).yoy > 0 ? 'text-up' : 'text-down'">
                {{ revOf(stock, ym).yoy > 0 ? '+' : '' }}{{ revOf(stock, ym).yoy }}%
              </td>
              <td v-for="ym in revMonths" :key="stock.stock_id + ym + 'mom'"
                  :class="revOf(stock, ym).mom > 0 ? 'text-up' : 'text-down'">
                {{ revOf(stock, ym).mom > 0 ? '+' : '' }}{{ revOf(stock, ym).mom }}%
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state">
          本日無符合回檔轉強四條件之個股。
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.pullback-date {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.pullback-desc {
  margin-bottom: 1.5rem;
  max-width: none;
}
</style>
