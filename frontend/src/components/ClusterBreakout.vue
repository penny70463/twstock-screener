<script setup>
import { ref, onMounted, computed } from 'vue'

const data = ref(null)
const loading = ref(true)
const error = ref(null)

// 根據開發環境決定 API 網址
const URL = import.meta.env.DEV
  ? '/api/cluster_tw.json'
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/cluster_tw.json'

onMounted(async () => {
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('尚無族群突破資料')
    data.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const themes = computed(() => data.value?.themes || [])
const totalStocks = computed(() =>
  themes.value.reduce((sum, t) => sum + t.count, 0))
</script>

<template>
  <section class="section">
    <div v-if="loading" class="empty-state">載入族群突破資料中...</div>
    <div v-else-if="error" class="glass-panel empty-state">⚠️ {{ error }}</div>
    <template v-else-if="data">
      <div class="section-header">
        <h2 class="section-title">族群出量突破 (近 {{ data.params?.window_days || 10 }} 日共 {{ totalStocks }} 檔)</h2>
        <span class="cluster-date">📅 基準日：{{ data.date }}</span>
      </div>

      <p class="logic-desc cluster-desc">
        💡 <strong>雁行擴散偵測</strong>：出量突破 = 收盤創 60 日新高 + 成交量達 20 日均量
        2 倍以上 + 當日大漲。族群行情從來不是全員同天漲——領頭羊先突破，同題材個股在數日內
        接連跟進。此頁追蹤近 10 個交易日觸發的個股並以 AI 分群，「🔥 今日點火」越多的族群，
        代表擴散越活躍。<strong>股名旁 * 為基準日當天觸發。</strong>
      </p>

      <div class="theme-grid" v-if="themes.length > 0">
        <div v-for="(theme, idx) in themes" :key="idx" class="glass-panel theme-card">
          <h3>{{ theme.name }}</h3>
          <div class="cluster-stats">
            <span class="stat-fired" v-if="theme.fired_today_count > 0">🔥 今日點火 {{ theme.fired_today_count }} 檔</span>
            <span class="stat-total">累計 {{ theme.count }} 檔</span>
          </div>
          <p class="theme-reason">{{ theme.reason }}</p>
          <div class="theme-stocks">
            <span
              v-for="stock in theme.stocks" :key="stock.stock_id"
              class="stock-pill"
              :class="{ 'pill-fired': stock.fired_today }"
              :title="`收盤 ${stock.close}｜當日 ${stock.chg_pct > 0 ? '+' : ''}${stock.chg_pct}%｜量 ${stock.vol_x}x｜觸發日 ${stock.fired_dates.join(' ')}`"
            >
              {{ stock.stock_name }} ({{ stock.stock_id }}){{ stock.fired_today ? ' *' : '' }}
            </span>
          </div>
        </div>
      </div>
      <div v-else class="glass-panel empty-state">
        近期無出量突破事件。
      </div>
    </template>
  </section>
</template>

<style scoped>
.cluster-date {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.cluster-desc {
  margin-bottom: 1.5rem;
  max-width: none;
}

.cluster-stats {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
}

.stat-fired {
  color: #fb923c;
  font-weight: 600;
}

.stat-total {
  color: var(--text-muted);
}

.pill-fired {
  background: rgba(251, 146, 60, 0.18);
  color: #fdba74;
  border-color: rgba(251, 146, 60, 0.45);
  font-weight: 600;
}
</style>
