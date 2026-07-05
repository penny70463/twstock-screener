<script setup>
import { ref, onMounted, computed } from 'vue'

const data = ref(null)
const loading = ref(true)
const error = ref(null)

const URL = import.meta.env.DEV
  ? '/api/short_tw.json'
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/short_tw.json'

onMounted(async () => {
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('尚無做空資料')
    data.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const regime = computed(() => data.value?.regime || 'unknown')
const stocks = computed(() => data.value?.stocks || [])

const regimeLabel = computed(() => ({
  bullish: '🟢 多頭（禁用放空）',
  mixed: '🟡 混合（多空並行）',
  bearish: '🔴 空頭（僅做空）',
  unknown: '⚫ 未知',
}[regime.value]))

const shortAllowed = computed(() => regime.value === 'mixed' || regime.value === 'bearish')
</script>

<template>
  <section class="section">
    <div v-if="loading" class="empty-state">載入做空資料中...</div>
    <div v-else-if="error" class="glass-panel empty-state">⚠️ {{ error }}</div>
    <template v-else-if="data">
      <div class="section-header">
        <h2 class="section-title">做空機會 {{ regimeLabel }}</h2>
        <span class="ss-date">📅 基準日：{{ data.date }}</span>
      </div>

      <p class="logic-desc ss-desc">
        💡 <strong>策略邏輯</strong>：在空頭或混合 Regime 下，篩選符合 4 條做空條件的股票進場放空。
        條件：技術面（跌破季線）、籌碼面（融券增加）、基本面（營收下滑）、動能（近月新低）。
        <strong>風險極高，務必嚴守停損</strong>（+8%～+10%）。
      </p>

      <div class="regime-indicator" :class="shortAllowed ? 'short-active' : 'short-disabled'">
        {{ shortAllowed ? '✓ 做空條件開啟' : '✗ 多頭時禁用做空' }}
      </div>

      <div v-if="!shortAllowed" class="glass-panel empty-state">
        當前 Regime 為 {{ regime }}，不執行做空選股。
      </div>

      <div v-else-if="stocks.length === 0" class="glass-panel empty-state">
        {{ regime === 'mixed' ? '混合環境：暫無做空機會' : '空頭環境：暫無符合條件的放空標的' }}
      </div>

      <div v-else class="glass-panel table-container">
        <table class="stock-table">
          <thead>
            <tr>
              <th>代號</th>
              <th>名稱</th>
              <th>現價</th>
              <th>季線</th>
              <th>距季線</th>
              <th>融券餘額</th>
              <th>進場參考</th>
              <th>停利線</th>
              <th>停損線</th>
              <th>當前 PnL</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in stocks" :key="s.stock_id" class="row-short">
              <td class="font-mono">{{ s.stock_id }}</td>
              <td class="font-bold">{{ s.stock_name }}</td>
              <td class="text-down">{{ s.current_price }}</td>
              <td>{{ s.ma60 }}</td>
              <td class="text-down font-bold">{{ s.dist_ma60_pct }}%</td>
              <td class="text-center">{{ s.short_balance }}</td>
              <td class="text-muted">{{ s.entry_ref }}</td>
              <td class="text-up">{{ s.stop_profit }}</td>
              <td class="text-down">{{ s.stop_loss }}</td>
              <td v-if="s.current_pnl_pct !== null" :class="s.current_pnl_pct < 0 ? 'text-up' : 'text-down'">
                {{ s.current_pnl_pct > 0 ? '+' : '' }}{{ s.current_pnl_pct }}%
              </td>
              <td v-else>—</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="risk-warning">
        ⚠️ <strong>風險提示</strong>：做空為高風險操作，理論上虧損無上限。
        融券利息成本年化 0.6%～1.2%，須納入成本考量。融券額度有限，實務受限於可融券數量。
      </div>
    </template>
  </section>
</template>

<style scoped>
.ss-date {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.ss-desc {
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

.short-active {
  background: #ef4444;
}

.short-disabled {
  background: #10b981;
}

.row-short {
  opacity: 0.85;
  border-left: 3px solid #ef4444;
}

.text-up {
  color: #10b981;
}

.text-down {
  color: #ef4444;
}

.text-muted {
  color: var(--text-muted);
}

.text-center {
  text-align: center;
}

.risk-warning {
  margin-top: 1.5rem;
  padding: 1rem;
  background: rgba(239, 68, 68, 0.1);
  border-left: 4px solid #ef4444;
  border-radius: 8px;
  color: #ef4444;
  font-size: 0.9rem;
}
</style>
