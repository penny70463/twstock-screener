<script setup>
import { ref, onMounted } from 'vue'

const etfData = ref(null)
const error = ref(null)

// 根據開發環境決定 API 網址
const URL = import.meta.env.DEV
  ? '/api/latest_etf.json'
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/latest_etf.json'

onMounted(async () => {
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('no data')
    etfData.value = await res.json()
  } catch (e) {
    error.value = e.message
  }
})

const getSignalClass = (signal) => {
  switch (signal) {
    case 'green': return 'signal-green'
    case 'yellow': return 'signal-yellow'
    case 'red': return 'signal-red'
    default: return ''
  }
}
</script>

<template>
  <div class="etf-traffic-light" v-if="etfData && etfData.etfs && etfData.etfs.length > 0">
    <div class="section-title">🚦 核心 ETF 存股紅綠燈</div>
    <div class="etf-cards">
      <div 
        v-for="etf in etfData.etfs" 
        :key="etf.code" 
        class="etf-card glass-panel"
        :class="getSignalClass(etf.signal)"
      >
        <div class="card-header">
          <div class="etf-name">{{ etf.name }}</div>
          <div class="etf-code">{{ etf.code }}</div>
        </div>
        
        <div class="price-info">
          <div class="current-price">${{ etf.price }}</div>
          <div class="signal-badge">{{ etf.desc }}</div>
        </div>

        <div class="strategy-details">
          <div class="detail-row">
            <span class="label">🎯 入手參考 (50MA)</span>
            <span class="value">${{ etf.entry_price }}</span>
          </div>
          <div class="detail-row">
            <span class="label">🛡️ 出場底線 (200MA)</span>
            <span class="value">${{ etf.exit_price }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.etf-traffic-light {
  margin-bottom: 2rem;
}

.section-title {
  font-size: 1.25rem;
  margin: 0 0 1rem 0;
  color: var(--text-main);
}

.etf-cards {
  display: flex;
  gap: 1rem;
  overflow-x: auto;
  padding-bottom: 0.5rem;
}

.etf-card {
  flex: 0 0 250px;
  padding: 1rem;
  border-radius: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  border: 1px solid rgba(255, 255, 255, 0.1);
  transition: transform 0.2s, box-shadow 0.2s;
}

.etf-card:hover {
  transform: translateY(-2px);
}

/* 狀態顏色樣式 */
.signal-green {
  border-top: 4px solid var(--bullish);
  background: linear-gradient(to bottom, rgba(16, 185, 129, 0.05), transparent);
}

.signal-yellow {
  border-top: 4px solid var(--neutral);
  background: linear-gradient(to bottom, rgba(245, 158, 11, 0.1), transparent);
  box-shadow: 0 0 15px rgba(245, 158, 11, 0.15); /* 強調黃燈區間 */
}

.signal-red {
  border-top: 4px solid var(--bearish);
  background: linear-gradient(to bottom, rgba(239, 68, 68, 0.05), transparent);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.etf-name {
  font-weight: 600;
  font-size: 1.1rem;
  color: var(--text-main);
}

.etf-code {
  font-size: 0.85rem;
  color: var(--text-muted);
}

.price-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.current-price {
  font-size: 1.5rem;
  font-weight: 700;
  color: white;
}

.signal-badge {
  font-size: 0.8rem;
  padding: 0.2rem 0.6rem;
  border-radius: 1rem;
  font-weight: 600;
}

.signal-green .signal-badge { background: rgba(16, 185, 129, 0.2); color: var(--bullish); }
.signal-yellow .signal-badge { background: rgba(245, 158, 11, 0.2); color: var(--neutral); }
.signal-red .signal-badge { background: rgba(239, 68, 68, 0.2); color: var(--bearish); }

.strategy-details {
  border-top: 1px dashed rgba(255, 255, 255, 0.1);
  padding-top: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  font-size: 0.85rem;
}

.detail-row .label {
  color: var(--text-muted);
}

.detail-row .value {
  color: var(--text-main);
  font-weight: 500;
}
</style>
