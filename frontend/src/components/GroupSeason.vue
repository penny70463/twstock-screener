<script setup>
import { ref, onMounted, computed } from 'vue'

const data = ref(null)
const loading = ref(true)
const error = ref(null)

// 根據開發環境決定 API 網址
const URL = import.meta.env.DEV
  ? '/api/group_tw.json'
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/group_tw.json'

onMounted(async () => {
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('尚無集團作帳資料')
    data.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const phase = computed(() => data.value?.phase || 'off')
const stocks = computed(() => data.value?.stocks || [])
const phaseLabel = computed(() => ({
  off: '⏸ 非季節',
  preview: '👀 預備期（名單 11/14 收盤定案）',
  active: '🟢 進行中',
}[phase.value] || ''))
</script>

<template>
  <section class="section">
    <div v-if="loading" class="empty-state">載入集團作帳資料中...</div>
    <div v-else-if="error" class="glass-panel empty-state">⚠️ {{ error }}</div>
    <template v-else-if="data">
      <div class="section-header">
        <h2 class="section-title">集團作帳（年底行情）{{ phaseLabel }}</h2>
        <span class="gs-date">📅 基準日：{{ data.date }}</span>
      </div>

      <p class="logic-desc gs-desc">
        💡 <strong>十年回測的真相</strong>：傳統說的「12 月集團作帳」已被市場搶跑——超額報酬
        集中在 <strong>11/15～11 月底</strong>（十年有八年為正），12 月才進場歷史上是輸家
        （12月初～12/20 十年僅一年為正）。有效規則：<strong>每集團取今年以來最強 2 檔、
        且站上 60 日線</strong>（落後補漲選法無效），11/15 後首個交易日開盤進場，
        12 月最後交易日收盤前出場（保守者 11 月底先出一半），停損 = 進場價 -8%（收盤確認）。
      </p>
      <p class="logic-desc gs-desc gs-warn">
        ⚠️ 2024、2025 連兩年超額報酬為負，此季節性效應可能正在衰減——參與時部位宜小，
        並嚴守停損與出場日。
      </p>

      <div v-if="phase === 'off'" class="glass-panel empty-state">
        現在是非作帳季節。每年 <strong>11/1</strong> 起顯示預備名單、
        <strong>11/15</strong> 起顯示正式名單與停損線，<strong>12 月最後交易日</strong>收盤前出場。
      </div>

      <div v-else class="glass-panel table-container">
        <table class="stock-table" v-if="stocks.length > 0">
          <thead>
            <tr>
              <th>集團</th>
              <th>代號</th>
              <th>名稱</th>
              <th>今年以來</th>
              <th>收盤價</th>
              <th>進場參考</th>
              <th>停損線</th>
              <th>進場後損益</th>
              <th>狀態</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in stocks" :key="s.stock_id" :class="{ 'row-stopped': s.stop_hit }">
              <td><span class="industry-tag">{{ s.group }}</span></td>
              <td class="font-mono">{{ s.stock_id }}</td>
              <td class="font-bold">{{ s.stock_name }}</td>
              <td :class="s.ytd_pct > 0 ? 'text-up' : 'text-down'">{{ s.ytd_pct > 0 ? '+' : '' }}{{ s.ytd_pct }}%</td>
              <td>{{ s.close }}</td>
              <td>{{ s.entry_ref ?? '11/15後首日開盤' }}</td>
              <td class="font-bold">{{ s.stop_line }}</td>
              <td v-if="s.pnl_pct !== null" :class="s.pnl_pct > 0 ? 'text-up' : 'text-down'">
                {{ s.pnl_pct > 0 ? '+' : '' }}{{ s.pnl_pct }}%
              </td>
              <td v-else>—</td>
              <td>
                <span v-if="s.stop_hit" class="status-stop">🛑 破線出場</span>
                <span v-else-if="phase === 'preview'" class="status-wait">預備</span>
                <span v-else class="status-hold">持有中</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state">本季無符合條件（站上 60 日線）的集團強勢股。</div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.gs-date {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.gs-desc {
  margin-bottom: 1rem;
  max-width: none;
}

.gs-warn {
  color: #fbbf24;
}

.row-stopped {
  opacity: 0.55;
}

.status-stop { color: #f87171; font-weight: 600; }
.status-hold { color: #4ade80; }
.status-wait { color: var(--text-muted); }
</style>
