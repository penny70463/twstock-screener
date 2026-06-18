<script setup>
import { ref, onMounted, computed, watch } from 'vue'

const props = defineProps({
  marketState: {
    type: Object,
    default: () => ({})
  }
})

const universe = ref([])
const loading = ref(true)
const error = ref(null)

const UNIVERSE_URL = import.meta.env.DEV 
  ? '/api/universe.json' 
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/universe.json'

// 使用者存股狀態
const portfolio = ref({
  cash: 0,
  positions: []
})

// 新增表單狀態
const newStock = ref({
  id: '',
  shares: '',
  price: ''
})

// 載入 localStorage
onMounted(async () => {
  const saved = localStorage.getItem('twstock_portfolio')
  if (saved) {
    try {
      portfolio.value = JSON.parse(saved)
    } catch (e) {
      console.error('Failed to parse portfolio', e)
    }
  }

  try {
    const res = await fetch(UNIVERSE_URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('Failed to fetch universe data')
    const data = await res.json()
    universe.value = data.stocks || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

// 儲存到 localStorage
watch(portfolio, (newVal) => {
  localStorage.setItem('twstock_portfolio', JSON.stringify(newVal))
}, { deep: true })

const addPosition = () => {
  if (!newStock.value.id || !newStock.value.shares || !newStock.value.price) return
  
  const existing = portfolio.value.positions.find(p => p.id === newStock.value.id)
  if (existing) {
    // 加碼計算平均成本
    const oldTotal = existing.shares * existing.price
    const newTotal = Number(newStock.value.shares) * Number(newStock.value.price)
    existing.shares += Number(newStock.value.shares)
    existing.price = (oldTotal + newTotal) / existing.shares
  } else {
    portfolio.value.positions.push({
      id: newStock.value.id,
      shares: Number(newStock.value.shares),
      price: Number(newStock.value.price)
    })
  }
  
  newStock.value = { id: '', shares: '', price: '' }
}

const removePosition = (id) => {
  portfolio.value.positions = portfolio.value.positions.filter(p => p.id !== id)
}

// 結合大盤與個股資料的持倉清單
const enrichedPositions = computed(() => {
  return portfolio.value.positions.map(pos => {
    const stockData = universe.value.find(s => String(s.stock_id) === String(pos.id))
    
    const currentPrice = stockData ? stockData.close : pos.price
    const costValue = pos.price * pos.shares
    const currentValue = currentPrice * pos.shares
    const pl = currentValue - costValue
    const plPct = (pl / costValue) * 100

    let score = stockData ? stockData['總分'] : 0
    let signals = stockData ? (stockData['訊號'] || '') : ''
    let trendScore = stockData ? stockData['趨勢'] : 0
    let momScore = stockData ? stockData['動能'] : 0

    const defaultAdvice = { suggestion: '未知', class: 'text-gray-400', reason: '', stop: 0 }
    let shortTerm = { ...defaultAdvice }
    let swing = { ...defaultAdvice }
    let longTerm = { ...defaultAdvice }

    if (!stockData) {
      const msg = { suggestion: '無資料', class: 'text-gray-400', reason: '非上市前600大或無歷史資料', stop: 0 }
      shortTerm = { ...msg }; swing = { ...msg }; longTerm = { ...msg };
    } else if (signals.includes('未通過')) {
      const msg = { suggestion: '⚠️ 建議出清', class: 'text-red-400', reason: signals, stop: 0 }
      shortTerm = { ...msg }; swing = { ...msg }; longTerm = { ...msg };
    } else {
      const threshold = props.marketState?.threshold || 70
      
      // 短線：重動能與短期訊號
      if (momScore >= 15 || signals.includes('強勢') || signals.includes('金叉') || signals.includes('齊揚')) {
        shortTerm = { suggestion: '✅ 續抱', class: 'text-green-400', reason: `動能強勢`, stop: stockData['短線停損'] }
      } else {
        shortTerm = { suggestion: '📉 建議減碼', class: 'text-yellow-400', reason: `動能轉弱`, stop: stockData['短線停損'] }
      }
      
      // 波段：重總分
      if (score >= threshold) {
        swing = { suggestion: '✅ 續抱', class: 'text-green-400', reason: `總分達標`, stop: stockData['波段停損'] }
      } else {
        swing = { suggestion: '📉 建議減碼', class: 'text-yellow-400', reason: `總分偏低`, stop: stockData['波段停損'] }
      }
      
      // 長線：重趨勢 (例如趨勢分數有拿到 12分以上)
      if (trendScore >= 12) {
        longTerm = { suggestion: '✅ 續抱', class: 'text-green-400', reason: `長線多頭`, stop: stockData['長線停損'] }
      } else {
        longTerm = { suggestion: '⚠️ 破線出清', class: 'text-red-400', reason: `長線翻空`, stop: stockData['長線停損'] }
      }
    }

    return {
      ...pos,
      name: stockData ? stockData.stock_name : '未知',
      currentPrice,
      costValue,
      currentValue,
      pl,
      plPct,
      score,
      shortTerm,
      swing,
      longTerm,
      signals
    }
  }).sort((a, b) => b.plPct - a.plPct)
})

const totalPortfolioValue = computed(() => {
  const stocksValue = enrichedPositions.value.reduce((sum, p) => sum + p.currentValue, 0)
  return stocksValue + Number(portfolio.value.cash)
})

const totalPL = computed(() => {
  return enrichedPositions.value.reduce((sum, p) => sum + p.pl, 0)
})

const formatCurrency = (val) => {
  return new Intl.NumberFormat('zh-TW', { style: 'currency', currency: 'TWD', maximumFractionDigits: 0 }).format(val)
}

// 曝險控管建議
const exposureAdvice = computed(() => {
  if (totalPortfolioValue.value <= 0) return null

  const regime = props.marketState?.label || '未知'
  let targetRatio = 1.0 // 預設多頭 100%
  if (regime === '盤整') targetRatio = 0.5
  if (regime === '空頭') targetRatio = 0.0

  const stocksValue = enrichedPositions.value.reduce((sum, p) => sum + p.currentValue, 0)
  const targetStockValue = totalPortfolioValue.value * targetRatio
  const excess = stocksValue - targetStockValue

  if (excess > 0) {
    return {
      status: 'warning',
      icon: '⚠️',
      title: '曝險過高，建議減碼',
      message: `目前大盤為【${regime}】，建議總持股水位應降至 ${targetRatio * 100}%。您的持股比例偏高，建議收回 ${formatCurrency(excess)} 的現金。請優先考慮從下方「建議減碼/出清」的標的開始賣出。`,
      cssClass: 'bg-yellow-900/40 border-yellow-500/50 text-yellow-100'
    }
  } else if (excess < 0) {
    // excess is negative, so -excess is the amount we can buy
    return {
      status: 'success',
      icon: '✅',
      title: '資金充裕，可適度建倉',
      message: `目前大盤為【${regime}】，建議總持股水位為 ${targetRatio * 100}%。您目前資金水位安全，仍有 ${formatCurrency(-excess)} 的額度可以考慮買進「強勢股掃描」中的高分標的。`,
      cssClass: 'bg-green-900/40 border-green-500/50 text-green-100'
    }
  } else {
    return {
      status: 'safe',
      icon: '🛡️',
      title: '水位安全',
      message: `目前大盤為【${regime}】，您的持股水位剛好符合大盤目標比例 (${targetRatio * 100}%)，部位控管非常良好！`,
      cssClass: 'bg-blue-900/40 border-blue-500/50 text-blue-100'
    }
  }
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
  <div class="portfolio-container">
    <div v-if="loading" class="text-center py-8">
      <p class="text-gray-400">載入全市場資料庫中...</p>
    </div>
    
    <div v-else-if="error" class="text-center py-8 text-red-400">
      <p>載入失敗: {{ error }}</p>
    </div>

    <template v-else>
      <!-- 大盤部位與曝險建議 -->
      <div v-if="exposureAdvice" class="exposure-alert" :class="exposureAdvice.cssClass">
        <div class="exposure-icon">{{ exposureAdvice.icon }}</div>
        <div class="exposure-content">
          <h4 class="exposure-title">{{ exposureAdvice.title }}</h4>
          <p class="exposure-message">{{ exposureAdvice.message }}</p>
        </div>
      </div>

      <!-- 總覽卡片 -->
      <div class="glass-panel summary-card">
        <div class="summary-item">
          <span class="summary-label">總資產價值</span>
          <span class="summary-value">{{ formatCurrency(totalPortfolioValue) }}</span>
        </div>
        <div class="summary-item">
          <span class="summary-label">現金部位</span>
          <div class="cash-input-group">
            <span class="currency-symbol">$</span>
            <input type="number" v-model="portfolio.cash" class="cash-input" placeholder="輸入現金餘額" />
          </div>
        </div>
        <div class="summary-item">
          <span class="summary-label">未實現損益</span>
          <span class="summary-value" :class="totalPL >= 0 ? 'text-green-400' : 'text-red-400'">
            {{ totalPL > 0 ? '+' : '' }}{{ formatCurrency(totalPL) }}
          </span>
        </div>
      </div>

      <!-- 新增持股 -->
      <div class="add-stock-panel glass-panel">
        <h2 class="section-title">新增持股</h2>
        <form @submit.prevent="addPosition" class="add-form">
          <input type="text" v-model="newStock.id" placeholder="股票代號 (如: 2330)" required class="form-input" />
          <input type="number" v-model="newStock.shares" placeholder="持股數 (股)" required class="form-input" min="1" />
          <input type="number" step="0.01" v-model="newStock.price" placeholder="平均成本" required class="form-input" min="0" />
          <button type="submit" class="submit-btn">新增 / 加碼</button>
        </form>
      </div>

      <!-- 持股清單 -->
      <div class="glass-panel table-container mt-6">
        <h2 class="section-title mb-4">持股體檢報告</h2>
        <table class="stock-table" v-if="enrichedPositions.length > 0">
          <thead>
            <tr>
              <th>代號名稱</th>
              <th>股數</th>
              <th>均價/現價</th>
              <th>近20日走勢</th>
              <th>未實現損益</th>
              <th>診斷建議 (短/波/長)</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="pos in enrichedPositions" :key="pos.id">
              <td>
                <div class="font-bold">{{ pos.name }}</div>
                <div class="text-sm font-mono text-gray-400">{{ pos.id }}</div>
              </td>
              <td>{{ pos.shares.toLocaleString() }}</td>
              <td>
                <div class="text-gray-400">${{ pos.price.toFixed(2) }}</div>
                <div class="font-bold">${{ pos.currentPrice.toFixed(2) }}</div>
              </td>
              <td>
                <svg v-if="pos.score && universe.find(s => String(s.stock_id) === String(pos.id))?.sparkline?.length > 0" class="sparkline" viewBox="0 0 100 30" preserveAspectRatio="none">
                  <polyline 
                    :points="getSparklinePoints(universe.find(s => String(s.stock_id) === String(pos.id)).sparkline)" 
                    :stroke="isSparklineUp(universe.find(s => String(s.stock_id) === String(pos.id)).sparkline) ? '#4ade80' : '#f87171'"
                    fill="none" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"
                  />
                </svg>
              </td>
              <td>
                <div :class="pos.pl >= 0 ? 'text-green-400' : 'text-red-400'" class="font-bold">
                  {{ pos.pl > 0 ? '+' : '' }}{{ formatCurrency(pos.pl) }}
                </div>
                <div :class="pos.pl >= 0 ? 'text-green-400' : 'text-red-400'" class="text-sm">
                  {{ pos.pl > 0 ? '+' : '' }}{{ pos.plPct.toFixed(2) }}%
                </div>
              </td>
              <td class="advice-cell">
                <div class="advice-block">
                  <div class="advice-row" :title="pos.shortTerm.reason">
                    <span class="advice-label">短:</span>
                    <span class="font-bold text-sm" :class="pos.shortTerm.class">{{ pos.shortTerm.suggestion }}</span>
                    <span v-if="pos.shortTerm.stop" class="text-xs text-gray-400 ml-1">(防守 ${{ pos.shortTerm.stop }})</span>
                  </div>
                  <div class="advice-row" :title="pos.swing.reason">
                    <span class="advice-label">波:</span>
                    <span class="font-bold text-sm" :class="pos.swing.class">{{ pos.swing.suggestion }}</span>
                    <span v-if="pos.swing.stop" class="text-xs text-gray-400 ml-1">(防守 ${{ pos.swing.stop }})</span>
                  </div>
                  <div class="advice-row" :title="pos.longTerm.reason">
                    <span class="advice-label">長:</span>
                    <span class="font-bold text-sm" :class="pos.longTerm.class">{{ pos.longTerm.suggestion }}</span>
                    <span v-if="pos.longTerm.stop" class="text-xs text-gray-400 ml-1">(防守 ${{ pos.longTerm.stop }})</span>
                  </div>
                </div>
              </td>
              <td>
                <button @click="removePosition(pos.id)" class="delete-btn">移除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="text-center py-8 text-gray-400">
          目前無持股紀錄，請從上方新增。
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.portfolio-container {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.summary-card {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1.5rem;
  padding: 1.5rem;
  background: linear-gradient(135deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.9));
  border-left: 4px solid var(--accent-purple);
}

.table-container {
  padding: 1.5rem;
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.summary-label {
  font-size: 0.9rem;
  color: var(--text-muted);
}

.summary-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: white;
}

.cash-input-group {
  display: flex;
  align-items: center;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 0.5rem;
  padding: 0.3rem 0.8rem;
}

.currency-symbol {
  color: var(--text-muted);
  margin-right: 0.5rem;
}

.cash-input {
  background: transparent;
  border: none;
  color: white;
  font-size: 1.2rem;
  font-weight: bold;
  width: 100%;
  outline: none;
}

.add-stock-panel {
  padding: 1.5rem;
}

.section-title {
  font-size: 1.5rem;
  margin: 0 0 1.5rem 0;
  color: var(--text-main);
  border-left: 4px solid var(--accent-blue);
  padding-left: 1rem;
}

.add-form {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}

.form-input {
  flex: 1;
  min-width: 120px;
  background: rgba(30, 41, 59, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: white;
  padding: 0.6rem 1rem;
  border-radius: 0.5rem;
  outline: none;
  transition: border-color 0.2s;
}

.form-input:focus {
  border-color: var(--accent-purple);
}

.submit-btn {
  background: var(--accent-purple);
  color: white;
  border: none;
  padding: 0.6rem 1.5rem;
  border-radius: 0.5rem;
  font-weight: bold;
  cursor: pointer;
  transition: opacity 0.2s;
  white-space: nowrap;
}

.submit-btn:hover {
  opacity: 0.9;
}

.delete-btn {
  background: transparent;
  color: #f87171;
  border: 1px solid #f87171;
  padding: 0.3rem 0.8rem;
  border-radius: 0.25rem;
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.2s;
}

.delete-btn:hover {
  background: rgba(248, 113, 113, 0.1);
}

.text-green-400 { color: #4ade80; }
.text-red-400 { color: #f87171; }
.text-yellow-400 { color: #facc15; }

.exposure-alert {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
  padding: 1.2rem 1.5rem;
  border-radius: 0.75rem;
  border: 1px solid;
}

.exposure-icon {
  font-size: 1.8rem;
  line-height: 1;
}

.exposure-title {
  font-size: 1.1rem;
  font-weight: 700;
  margin: 0 0 0.4rem 0;
}

.exposure-message {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.5;
  opacity: 0.9;
}

.sparkline {
  width: 80px;
  height: 30px;
  display: block;
}

.advice-cell {
  vertical-align: middle;
}
.advice-block {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.advice-row {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}
.advice-label {
  font-size: 0.8rem;
  color: var(--text-muted);
  width: 1.5rem;
}
</style>
