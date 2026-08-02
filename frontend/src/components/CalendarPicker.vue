<script setup>
/**
 * CalendarPicker — 自訂日曆選擇器
 *
 * Props:
 *   modelValue  — 當前選取日期（YYYY-MM-DD 或空字串）
 *   availableDates — 可選日期陣列（YYYY-MM-DD）；不在此陣列中的日期會 disabled
 *   placeholder — 未選取時的提示文字
 *   allowLatest — 是否顯示「最新 (Latest)」選項（用於歷史回顧）
 *
 * Emits:
 *   update:modelValue — 選取日期變更
 */
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  availableDates: { type: Array, default: () => [] },
  placeholder: { type: String, default: '選擇日期' },
  allowLatest: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const isOpen = ref(false)
const pickerRef = ref(null)
const triggerRef = ref(null)
const dropdownRef = ref(null)
const dropdownStyle = ref({})

// 讓使用者可以手動輸入
const inputText = ref('')

watch(() => props.modelValue, (val) => {
  if (val === 'latest') {
    inputText.value = '最新 (Latest)'
  } else {
    inputText.value = val || ''
  }
}, { immediate: true })

function handleInputConfirm() {
  const val = inputText.value.trim()
  if (val === '最新 (Latest)' || val === 'latest') {
    emit('update:modelValue', 'latest')
    return
  }
  
  if (val === '') {
    emit('update:modelValue', '')
    return
  }

  // 檢查格式 YYYY-MM-DD
  if (/^\d{4}-\d{2}-\d{2}$/.test(val)) {
    if (availableSet.value.has(val)) {
      emit('update:modelValue', val)
      viewYear.value = parseInt(val.substring(0, 4))
      viewMonth.value = parseInt(val.substring(5, 7))
      isOpen.value = false
    } else {
      // 找不到該日期的資料
      alert(`日期 ${val} 沒有資料，請選擇有資料的日期。`)
      // 還原
      inputText.value = props.modelValue === 'latest' ? '最新 (Latest)' : props.modelValue
    }
  } else {
    alert('請輸入正確的日期格式 (YYYY-MM-DD)')
    inputText.value = props.modelValue === 'latest' ? '最新 (Latest)' : props.modelValue
  }
}

// 目前顯示的年月
const viewYear = ref(2026)
const viewMonth = ref(1) // 1-12

// 星期標頭
const weekDays = ['一', '二', '三', '四', '五', '六', '日']

// 可用日期的 Set（加速查詢）
const availableSet = computed(() => new Set(props.availableDates))

// 有資料的年月集合（用於月份導覽 disable）
const availableMonths = computed(() => {
  const months = new Set()
  for (const d of props.availableDates) {
    months.add(d.substring(0, 7)) // "YYYY-MM"
  }
  return months
})

// 初始化顯示月份：以選取日期或最新可用日期為準
function initView() {
  const target = props.modelValue && props.modelValue !== 'latest'
    ? props.modelValue
    : (props.availableDates.length ? props.availableDates[props.availableDates.length - 1] : null)
  if (target) {
    viewYear.value = parseInt(target.substring(0, 4))
    viewMonth.value = parseInt(target.substring(5, 7))
  }
}

onMounted(() => {
  initView()
  document.addEventListener('click', handleClickOutside)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside)
})

watch(() => props.modelValue, () => {
  if (props.modelValue && props.modelValue !== 'latest') {
    viewYear.value = parseInt(props.modelValue.substring(0, 4))
    viewMonth.value = parseInt(props.modelValue.substring(5, 7))
  }
})

function handleClickOutside(e) {
  // 檢查點擊是否在 trigger 或 dropdown 內
  if (pickerRef.value && !pickerRef.value.contains(e.target) &&
      (!dropdownRef.value || !dropdownRef.value.contains(e.target))) {
    isOpen.value = false
  }
}

function updatePosition() {
  if (!triggerRef.value) return
  const rect = triggerRef.value.getBoundingClientRect()
  const dropdownHeight = 380 // 日曆大約高度
  const spaceBelow = window.innerHeight - rect.bottom
  const openAbove = spaceBelow < dropdownHeight && rect.top > dropdownHeight

  dropdownStyle.value = {
    position: 'fixed',
    left: rect.left + 'px',
    width: '300px',
    zIndex: 9999,
    ...(openAbove
      ? { bottom: (window.innerHeight - rect.top + 6) + 'px' }
      : { top: (rect.bottom + 6) + 'px' }),
  }
}

async function toggleOpen() {
  isOpen.value = !isOpen.value
  if (isOpen.value) {
    initView()
    await nextTick()
    updatePosition()
  }
}

// 顯示用的日期文字 (由 inputText 取代)
// const displayText = computed(...) (已移除)

// 月份導覽
function prevMonth() {
  if (viewMonth.value === 1) {
    viewMonth.value = 12
    viewYear.value--
  } else {
    viewMonth.value--
  }
}

function nextMonth() {
  if (viewMonth.value === 12) {
    viewMonth.value = 1
    viewYear.value++
  } else {
    viewMonth.value++
  }
}

// 月份標題
const monthLabel = computed(() => {
  return `${viewYear.value} 年 ${viewMonth.value} 月`
})

// 產生日曆格子
const calendarDays = computed(() => {
  const year = viewYear.value
  const month = viewMonth.value
  const firstDay = new Date(year, month - 1, 1)
  const lastDay = new Date(year, month, 0)
  const daysInMonth = lastDay.getDate()

  // 週一 = 0, 週日 = 6
  let startWeekday = firstDay.getDay() - 1
  if (startWeekday < 0) startWeekday = 6

  const days = []

  // 前月填充
  for (let i = 0; i < startWeekday; i++) {
    days.push({ day: '', dateStr: '', filler: true })
  }

  // 當月日期
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    const available = availableSet.value.has(dateStr)
    const selected = props.modelValue === dateStr
    days.push({ day: d, dateStr, filler: false, available, selected })
  }

  return days
})

function selectDate(dateStr) {
  emit('update:modelValue', dateStr)
  isOpen.value = false
}

function selectLatest() {
  emit('update:modelValue', 'latest')
  isOpen.value = false
}

// 是否有前/後月的資料
const hasPrevMonth = computed(() => {
  const m = viewMonth.value === 1 ? 12 : viewMonth.value - 1
  const y = viewMonth.value === 1 ? viewYear.value - 1 : viewYear.value
  const key = `${y}-${String(m).padStart(2, '0')}`
  // 允許導覽到比最早可用月份更早的一個月（讓使用者看到該月全貌）
  const allKeys = [...availableMonths.value].sort()
  return allKeys.length > 0 && key >= allKeys[0].substring(0, 7)
})

const hasNextMonth = computed(() => {
  const m = viewMonth.value === 12 ? 1 : viewMonth.value + 1
  const y = viewMonth.value === 12 ? viewYear.value + 1 : viewYear.value
  const key = `${y}-${String(m).padStart(2, '0')}`
  const allKeys = [...availableMonths.value].sort()
  return allKeys.length > 0 && key <= allKeys[allKeys.length - 1]
})
</script>

<template>
  <div class="cal-picker" ref="pickerRef">
    <div class="cal-trigger" ref="triggerRef" @click="toggleOpen">
      <span class="cal-icon">📅</span>
      <input 
        type="text" 
        class="cal-input"
        v-model="inputText"
        @keydown.enter="handleInputConfirm"
        @blur="handleInputConfirm"
        :placeholder="placeholder"
        @click.stop="toggleOpen"
      />
      <span class="cal-chevron" :class="{ open: isOpen }">▾</span>
    </div>

    <Teleport to="body">
      <Transition name="cal-fade">
        <div v-if="isOpen" class="cal-dropdown" ref="dropdownRef" :style="dropdownStyle">
          <!-- Latest 選項 -->
          <button v-if="allowLatest" class="cal-latest-btn" @click="selectLatest" type="button"
            :class="{ selected: modelValue === 'latest' }">
            ⚡ 最新 (Latest)
          </button>

          <!-- 月份導覽 -->
          <div class="cal-nav">
            <button @click="prevMonth" :disabled="!hasPrevMonth" class="cal-nav-btn" type="button">‹</button>
            <span class="cal-month-label">{{ monthLabel }}</span>
            <button @click="nextMonth" :disabled="!hasNextMonth" class="cal-nav-btn" type="button">›</button>
          </div>

          <!-- 星期標頭 -->
          <div class="cal-grid cal-header">
            <span v-for="w in weekDays" :key="w" class="cal-cell cal-weekday">{{ w }}</span>
          </div>

          <!-- 日期格子 -->
          <div class="cal-grid">
            <span
              v-for="(d, i) in calendarDays" :key="i"
              class="cal-cell"
              :class="{
                'cal-filler': d.filler,
                'cal-available': d.available,
                'cal-disabled': !d.filler && !d.available,
                'cal-selected': d.selected,
              }"
              @click="d.available ? selectDate(d.dateStr) : null"
            >
              {{ d.day }}
            </span>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style>
.cal-picker {
  position: relative;
  display: inline-block;
}

.cal-trigger {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid var(--panel-border);
  color: var(--text-main);
  padding: 0.5rem 0.85rem;
  border-radius: 8px;
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s ease;
  min-width: 170px;
}

.cal-trigger:hover {
  border-color: rgba(59, 130, 246, 0.5);
}

.cal-trigger:focus {
  outline: none;
  border-color: var(--accent-blue);
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.3);
}

.cal-icon {
  font-size: 1rem;
}

.cal-input {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--text-main);
  font-size: 0.9rem;
  outline: none;
  width: 110px;
  padding: 0;
}

.cal-input::placeholder {
  color: var(--text-muted);
}

.cal-chevron {
  font-size: 0.7rem;
  color: var(--text-muted);
  transition: transform 0.2s ease;
}

.cal-chevron.open {
  transform: rotate(180deg);
}

/* 下拉面板 — Teleport 到 body，用 fixed 定位 */
.cal-dropdown {
  background: rgba(15, 23, 42, 0.97);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 12px;
  padding: 0.75rem;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(12px);
  width: 300px;
}

/* 動畫 */
.cal-fade-enter-active,
.cal-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.cal-fade-enter-from,
.cal-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* Latest 按鈕 */
.cal-latest-btn {
  width: 100%;
  padding: 0.5rem;
  margin-bottom: 0.5rem;
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.25);
  border-radius: 8px;
  color: #93c5fd;
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.15s ease;
}

.cal-latest-btn:hover {
  background: rgba(59, 130, 246, 0.2);
}

.cal-latest-btn.selected {
  background: rgba(59, 130, 246, 0.3);
  border-color: var(--accent-blue);
  color: #fff;
  font-weight: 600;
}

/* 月份導覽 */
.cal-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}

.cal-nav-btn {
  background: none;
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: var(--text-main);
  width: 32px;
  height: 32px;
  border-radius: 8px;
  font-size: 1.2rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}

.cal-nav-btn:hover:not(:disabled) {
  background: rgba(59, 130, 246, 0.15);
  border-color: rgba(59, 130, 246, 0.4);
}

.cal-nav-btn:disabled {
  opacity: 0.25;
  cursor: not-allowed;
}

.cal-month-label {
  font-weight: 600;
  font-size: 0.95rem;
  color: var(--text-main);
}

/* 日曆格子 */
.cal-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 2px;
}

.cal-header {
  margin-bottom: 2px;
}

.cal-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 34px;
  font-size: 0.82rem;
  border-radius: 6px;
  transition: all 0.12s ease;
}

.cal-weekday {
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 600;
  height: 28px;
}

.cal-filler {
  /* 空格，不顯示 */
}

.cal-available {
  color: var(--text-main);
  cursor: pointer;
  background: rgba(255, 255, 255, 0.04);
}

.cal-available:hover {
  background: rgba(59, 130, 246, 0.25);
  color: #fff;
  transform: scale(1.08);
}

.cal-disabled {
  color: rgba(148, 163, 184, 0.25);
  cursor: not-allowed;
}

.cal-selected {
  background: var(--accent-blue) !important;
  color: #fff !important;
  font-weight: 700;
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.4);
}
</style>
