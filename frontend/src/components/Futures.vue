<script setup>
import { ref, onMounted, computed } from 'vue'

const data = ref(null)
const loading = ref(true)
const error = ref(null)

const URL = import.meta.env.DEV
  ? '/api/futures_tw.json'
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/futures_tw.json'

onMounted(async () => {
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('尚無台指期資料')
    data.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const regime = computed(() => data.value?.regime || 'unknown')
const twiiClose = computed(() => data.value?.twii_close)
const usSignal = computed(() => data.value?.us_signal || {})
const taifexSignal = computed(() => data.value?.taifex_signal || {})
const signals = computed(() => data.value?.signals || {})
const entryExit = computed(() => data.value?.entry_exit)

const signalColor = computed(() => {
  if (signals.value.buy_signal) return '#10b981'
  if (signals.value.sell_signal) return '#ef4444'
  return '#f59e0b'
})

const signalText = computed(() => {
  if (signals.value.buy_signal) return '🟢 買進'
  if (signals.value.sell_signal) return '🔴 賣出'
  return '🟡 觀望'
})
</script>

<template>
  <section class="section">
    <div v-if="loading" class="empty-state">載入台指期資料中...</div>
    <div v-else-if="error" class="glass-panel empty-state">⚠️ {{ error }}</div>
    <template v-else-if="data">
      <div class="section-header">
        <h2 class="section-title">台指期波段策略 (TXF)</h2>
        <span class="fut-date">📅 基準日：{{ data.date }}</span>
      </div>

      <p class="logic-desc fut-desc">
        💡 <strong>策略邏輯</strong>：綜合美股隔夜走勢（AAPL/QQQ/SP500）與期交所法人籌碼（投信/自營/外資），
        判斷台指期短期多空方向。信號為「買進」、「賣出」、「觀望」三檔。
      </p>

      <div class="signal-box" :style="{ backgroundColor: signalColor }">
        <div class="signal-main">{{ signalText }}</div>
        <div class="signal-sub">{{ regime }} Regime</div>
      </div>

      <!-- US Market Signal -->
      <div class="glass-panel signal-card">
        <h3>🇺🇸 美股隔夜信號</h3>
        <table class="compact-table">
          <tbody>
          <tr>
            <td>信號</td>
            <td class="font-bold">{{ usSignal.us_signal }}</td>
          </tr>
          <tr>
            <td>AAPL (5D)</td>
            <td :class="usSignal.aapl_change_pct > 0 ? 'text-up' : 'text-down'">
              {{ usSignal.aapl_change_pct > 0 ? '+' : '' }}{{ usSignal.aapl_change_pct }}%
            </td>
          </tr>
          <tr>
            <td>QQQ (5D)</td>
            <td :class="usSignal.qqq_change_pct > 0 ? 'text-up' : 'text-down'">
              {{ usSignal.qqq_change_pct > 0 ? '+' : '' }}{{ usSignal.qqq_change_pct }}%
            </td>
          </tr>
          <tr>
            <td>S&P 500 (5D)</td>
            <td :class="usSignal.sp500_change_pct > 0 ? 'text-up' : 'text-down'">
              {{ usSignal.sp500_change_pct > 0 ? '+' : '' }}{{ usSignal.sp500_change_pct }}%
            </td>
          </tr>
          </tbody>
        </table>
      </div>

      <!-- TAIFEX Institutional Signal -->
      <div class="glass-panel signal-card">
        <h3>📊 期交所籌碼信號 (TXF)</h3>
        <table class="compact-table">
          <tbody>
          <tr>
            <td>信號</td>
            <td class="font-bold">{{ taifexSignal.taifex_signal }}</td>
          </tr>
          <tr>
            <td>投信淨單</td>
            <td :class="taifexSignal.trust_net > 0 ? 'text-up' : 'text-down'">
              {{ taifexSignal.trust_net > 0 ? '+' : '' }}{{ taifexSignal.trust_net }}
            </td>
          </tr>
          <tr>
            <td>自營淨單</td>
            <td :class="taifexSignal.dealer_net > 0 ? 'text-up' : 'text-down'">
              {{ taifexSignal.dealer_net > 0 ? '+' : '' }}{{ taifexSignal.dealer_net }}
            </td>
          </tr>
          <tr>
            <td>外資淨單</td>
            <td :class="taifexSignal.foreign_net > 0 ? 'text-up' : 'text-down'">
              {{ taifexSignal.foreign_net > 0 ? '+' : '' }}{{ taifexSignal.foreign_net }}
            </td>
          </tr>
          <tr>
            <td>加權分數</td>
            <td class="font-bold">{{ taifexSignal.weighted_score }}</td>
          </tr>
          </tbody>
        </table>
      </div>

      <!-- Entry / Exit Plan -->
      <div v-if="entryExit" class="glass-panel entry-card">
        <h3>📍 進出場計畫</h3>
        <div v-if="entryExit.note" class="empty-state">
          {{ entryExit.note }}
        </div>
        <div v-else class="entry-details">
          <div class="twii-ref">加權指數：{{ twiiClose }}</div>
          <table class="entry-table">
            <tbody>
            <tr>
              <td>進場點位</td>
              <td class="entry-value">{{ entryExit.entry }}</td>
            </tr>
            <tr>
              <td>目標點位</td>
              <td class="entry-value">{{ entryExit.target }}</td>
            </tr>
            <tr>
              <td>停損點位</td>
              <td class="entry-value">{{ entryExit.stop_loss }}</td>
            </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="strategy-note">
        ⚠️ <strong>策略說明</strong>：此為 1.0 版（簡化邏輯）。回測驗證後會調整進出場點數、持倉周期、成本考量等。
      </div>
    </template>
  </section>
</template>

<style scoped>
.fut-date {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.fut-desc {
  margin-bottom: 1.5rem;
  max-width: none;
}

.signal-box {
  padding: 2rem;
  border-radius: 12px;
  color: white;
  margin-bottom: 2rem;
  text-align: center;
}

.signal-main {
  font-size: 2rem;
  font-weight: bold;
  margin-bottom: 0.5rem;
}

.signal-sub {
  font-size: 0.9rem;
  opacity: 0.9;
}

.signal-card {
  margin-bottom: 1.5rem;
  padding: 1rem;
}

.signal-card h3 {
  margin: 0 0 1rem 0;
  font-size: 1.05rem;
}

.compact-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

.compact-table tr {
  border-bottom: 1px solid var(--border-color);
}

.compact-table td {
  padding: 0.75rem;
}

.compact-table td:first-child {
  color: var(--text-muted);
  width: 40%;
}

.text-up {
  color: #10b981;
  font-weight: 600;
}

.text-down {
  color: #ef4444;
  font-weight: 600;
}

.entry-card {
  margin-bottom: 1.5rem;
  padding: 1rem;
  border: 2px solid var(--accent-blue);
}

.entry-card h3 {
  margin: 0 0 1rem 0;
}

.twii-ref {
  font-size: 0.95rem;
  color: var(--text-muted);
  margin-bottom: 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border-color);
}

.entry-table {
  width: 100%;
  border-collapse: collapse;
}

.entry-table tr {
  border-bottom: 1px solid var(--border-color);
}

.entry-table td {
  padding: 0.75rem;
}

.entry-table td:first-child {
  color: var(--text-muted);
  width: 40%;
}

.entry-value {
  font-weight: bold;
  font-size: 1.1rem;
  color: var(--accent-blue);
}

.strategy-note {
  margin-top: 1.5rem;
  padding: 1rem;
  background: rgba(59, 130, 246, 0.1);
  border-left: 4px solid var(--accent-blue);
  border-radius: 8px;
  color: var(--accent-blue);
  font-size: 0.9rem;
}
</style>
