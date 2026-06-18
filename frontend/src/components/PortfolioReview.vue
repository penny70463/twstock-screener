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

    let suggestion = '未知'
    let suggestionClass = 'text-gray-400'
    let score = stockData ? stockData['總分'] : 0
    let reason = ''

    if (!stockData) {
      suggestion = '無資料'
      reason = '非上市前600大或無歷史資料'
    } else if (stockData['訊號'] && stockData['訊號'].includes('未通過')) {
      suggestion = '⚠️ 建議出清'
      suggestionClass = 'text-red-400'
      reason = stockData['訊號']
    } else {
      const threshold = props.marketState?.threshold || 70
      if (score >= threshold) {
        suggestion = '✅ 續抱'
        suggestionClass = 'text-green-400'
        reason = `總分 ${score} 達標 (${threshold})`
      } else {
        suggestion = '📉 建議減碼'
        suggestionClass = 'text-yellow-400'
        reason = `總分 ${score} 未達標 (${threshold})`
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
      suggestion,
      suggestionClass,
      reason,
      signals: stockData ? stockData['訊號'] : ''
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
        <h3 class="panel-title">新增持股</h3>
        <form @submit.prevent="addPosition" class="add-form">
          <input type="text" v-model="newStock.id" placeholder="股票代號 (如: 2330)" required class="form-input" />
          <input type="number" v-model="newStock.shares" placeholder="持股數 (股)" required class="form-input" min="1" />
          <input type="number" step="0.01" v-model="newStock.price" placeholder="平均成本" required class="form-input" min="0" />
          <button type="submit" class="submit-btn">新增 / 加碼</button>
        </form>
      </div>

      <!-- 持股清單 -->
      <div class="glass-panel table-container mt-6">
        <h3 class="panel-title mb-4">持股體檢報告</h3>
        <table class="stock-table" v-if="enrichedPositions.length > 0">
          <thead>
            <tr>
              <th>代號名稱</th>
              <th>股數</th>
              <th>均價/現價</th>
              <th>未實現損益</th>
              <th>AI 診斷建議</th>
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
                <div :class="pos.pl >= 0 ? 'text-green-400' : 'text-red-400'" class="font-bold">
                  {{ pos.pl > 0 ? '+' : '' }}{{ formatCurrency(pos.pl) }}
                </div>
                <div :class="pos.pl >= 0 ? 'text-green-400' : 'text-red-400'" class="text-sm">
                  {{ pos.pl > 0 ? '+' : '' }}{{ pos.plPct.toFixed(2) }}%
                </div>
              </td>
              <td>
                <div class="font-bold" :class="pos.suggestionClass">{{ pos.suggestion }}</div>
                <div class="text-xs text-gray-400 mt-1 max-w-xs whitespace-normal">{{ pos.reason }}</div>
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
  background: linear-gradient(135deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.9));
  border-left: 4px solid var(--accent-purple);
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

.panel-title {
  font-size: 1.2rem;
  margin-bottom: 1rem;
  color: white;
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
</style>
