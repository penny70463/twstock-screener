<script setup>
import { ref, onMounted, computed } from 'vue'

const data = ref(null)
const loading = ref(true)
const error = ref(null)

const URL = import.meta.env.DEV
  ? '/api/quarter_tw.json'
  : 'https://raw.githubusercontent.com/penny70463/twstock-screener/master/data/results/quarter_tw.json'

onMounted(async () => {
  try {
    const res = await fetch(URL, { cache: 'no-store' })
    if (!res.ok) throw new Error('尚無季底法人資料')
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
  preview: '👀 預備期（名單 5 日前定案）',
  active: '🟢 進行中',
}[phase.value] || ''))
</script>

<template>
  <section class="section">
    <div v-if="loading" class="empty-state">載入季底法人資料中...</div>
    <div v-else-if="error" class="glass-panel empty-state">⚠️ {{ error }}</div>
    <template v-else-if="data">
      <div class="section-header">
        <h2 class="section-title">季底作帳（法人行情）{{ phaseLabel }}</h2>
        <span class="qe-date">📅 基準日：{{ data.date }}</span>
      </div>

      <p class="logic-desc qe-desc">
        💡 <strong>12 季回測結論</strong>：季底法人（尤其是投信）的買超訊號可信度高。
        有效規則：投信季底前 5～3 天內「淨買超 ≥ 7 日」的前 10 檔股票，
        <strong>季底前 5 日隔日開盤進場、T+5 收盤前出場</strong>（期望 +1.45%）。
        不要搶早進或搶快出；若已持有，季底時拖到 T+10 反而平均多賺 +6%
        （但需嚴守停損 -10%）。進場太早（季底前 12 天）或季底當日進場皆無效。
      </p>
      <p class="logic-desc qe-desc qe-warn">
        ⚠️ 期望值低（無超額報酬 α），優勢在穩定性（12 季中 7 季為正）。
        小額參與、嚴守紀律。
      </p>

      <div v-if="phase === 'off'" class="glass-panel empty-state">
        現在非季底窗口。頁籤僅在 <strong>3、6、9、12 月最後 20 日</strong>
        啟用（季底前 5 日預備、季底最後 5 日進行）。
      </div>

      <div v-else class="glass-panel table-container">
        <table class="stock-table" v-if="stocks.length > 0">
          <thead>
            <tr>
              <th>代號</th>
              <th>名稱</th>
              <th>投信買超天數</th>
              <th>買超佔比</th>
              <th>進場參考</th>
              <th>停損線</th>
              <th>預期出場</th>
              <th>當前價</th>
              <th>進場後損益</th>
              <th>狀態</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in stocks" :key="s.stock_id" :class="{ 'row-stopped': s.stop_hit }">
              <td class="font-mono">{{ s.stock_id }}</td>
              <td class="font-bold">{{ s.stock_name }}</td>
              <td class="text-center">{{ s.buy_days }}/10 日</td>
              <td>{{ (s.buy_ratio * 100).toFixed(2) }}%</td>
              <td>{{ s.entry_ref }}</td>
              <td class="font-bold">{{ s.stop_line }}</td>
              <td>{{ s.exit_plan }}</td>
              <td>{{ s.current }}</td>
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
        <div v-else class="empty-state">本季無符合投信買超條件（≥7天）的個股。</div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.qe-date {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.qe-desc {
  margin-bottom: 1rem;
  max-width: none;
}

.qe-warn {
  color: #fbbf24;
}

.row-stopped {
  opacity: 0.55;
}

.text-center {
  text-align: center;
}

.status-stop { color: #f87171; font-weight: 600; }
.status-hold { color: #4ade80; }
.status-wait { color: var(--text-muted); }
</style>
