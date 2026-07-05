<script setup>
import { ref, onMounted, computed } from 'vue'

const data = ref(null)
const loading = ref(true)
const error = ref(null)

const URL = import.meta.env.DEV
  ? '/api/event_driven_tw.json'
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/event_driven_tw.json'

onMounted(async () => {
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('尚無事件驅動資料')
    data.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const regime = computed(() => data.value?.regime || 'unknown')
const events = computed(() => data.value?.events || [])

const regimeLabel = computed(() => ({
  bullish: '🟢 多頭',
  mixed: '🟡 混合',
  bearish: '🔴 空頭',
  unknown: '⚫ 未知',
}[regime.value]))

const regimeColor = computed(() => ({
  bullish: '#10b981',
  mixed: '#f59e0b',
  bearish: '#ef4444',
  unknown: '#6b7280',
}[regime.value]))
</script>

<template>
  <section class="section">
    <div v-if="loading" class="empty-state">載入事件驅動資料中...</div>
    <div v-else-if="error" class="glass-panel empty-state">⚠️ {{ error }}</div>
    <template v-else-if="data">
      <div class="section-header">
        <h2 class="section-title">展覽會供應鏈 {{ regimeLabel }}</h2>
        <span class="ed-date">📅 基準日：{{ data.date }}</span>
      </div>

      <p class="logic-desc ed-desc">
        💡 <strong>策略邏輯</strong>：監控即將到來的產業展覽會。
        展覽會前 7～15 日，龍頭公司及相關供應鏈股往往因預期訂單增加而提前上漲。
        <strong>僅在多頭或混合 Regime 下執行</strong>。
      </p>

      <div class="regime-indicator" :style="{ backgroundColor: regimeColor }">
        {{ regime === 'bullish' ? '✓ 可執行多頭選股'
           : regime === 'mixed' ? '⚠ 多空並行'
           : '✗ 空頭時禁用' }}
      </div>

      <div v-if="events.length === 0" class="glass-panel empty-state">
        近期無主要展覽會。
      </div>

      <div v-else class="glass-panel table-container">
        <div v-for="evt in events" :key="evt.name" class="event-card">
          <div class="event-header">
            <h3>{{ evt.name }}</h3>
            <span class="event-meta">{{ evt.lookback_days }} 日前進場 | {{ evt.count }} 檔</span>
          </div>

          <table class="stock-table" v-if="evt.stocks && evt.stocks.length > 0">
            <thead>
              <tr>
                <th>代號</th>
                <th>名稱</th>
                <th>類型</th>
                <th>現價</th>
                <th>季線</th>
                <th>距季線</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in evt.stocks" :key="s.stock_id">
                <td class="font-mono">{{ s.stock_id }}</td>
                <td class="font-bold">{{ s.stock_name }}</td>
                <td>
                  <span v-if="s.type === '龍頭'" class="badge-leader">龍頭</span>
                  <span v-else class="badge-supply">供應鏈</span>
                </td>
                <td>{{ s.current_price }}</td>
                <td>{{ s.ma60 }}</td>
                <td :class="s.dist_ma60_pct > 0 ? 'text-up' : 'text-down'">
                  {{ s.dist_ma60_pct > 0 ? '+' : '' }}{{ s.dist_ma60_pct }}%
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.ed-date {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.ed-desc {
  margin-bottom: 1.5rem;
  max-width: none;
}

.regime-indicator {
  padding: 0.75rem 1rem;
  border-radius: 8px;
  color: white;
  font-weight: 600;
  text-align: center;
  margin-bottom: 1.5rem;
  font-size: 0.95rem;
}

.event-card {
  margin-bottom: 2rem;
  padding: 1rem;
  background: var(--bg-secondary);
  border-radius: 8px;
}

.event-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 1rem;
  border-bottom: 2px solid var(--border-color);
  padding-bottom: 0.5rem;
}

.event-header h3 {
  margin: 0;
  font-size: 1.1rem;
  color: var(--text-primary);
}

.event-meta {
  font-size: 0.85rem;
  color: var(--text-muted);
}

.badge-leader {
  display: inline-block;
  background: #3b82f6;
  color: white;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 600;
}

.badge-supply {
  display: inline-block;
  background: #06b6d4;
  color: white;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 600;
}

.text-up {
  color: #10b981;
}

.text-down {
  color: #ef4444;
}
</style>
