// 資料新鮮度判斷
//
// 每日排程若在裸執行步驟失敗（run_daily.sh 的 run_pipeline / etf_alert /
// etf_monthly_review），結果 JSON 不會更新，但頁面照樣顯示舊資料、看不出異狀。
// 這裡直接用資料本身的日期推算落後幾個交易日：不依賴狀態檔或 LINE 推播，
// 連排程完全沒啟動也偵測得到。
//
// 已知限制：不含國定假日行事曆，休市日會誤報落後一天，故呼叫端文案需標明。

export const SCHEDULE_HOUR = 16        // 排程每個交易日 16:00 執行
export const SCHEDULE_BUFFER_MIN = 30  // 給排程跑完的緩衝，未過此刻不算落後

const isWeekend = (d) => d.getDay() === 0 || d.getDay() === 6

/** 一律以台北時間判斷，避免使用者身處其他時區時誤判 */
export const nowInTaipei = () =>
  new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Taipei' }))

export const toYMD = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

/** 依現在時刻推算「最新結果應該是哪一個交易日」；now 可注入以便測試 */
export const expectedTradingDay = (now = nowInTaipei()) => {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const beforeDone = now.getHours() < SCHEDULE_HOUR
    || (now.getHours() === SCHEDULE_HOUR && now.getMinutes() < SCHEDULE_BUFFER_MIN)
  if (!isWeekend(d) && beforeDone) d.setDate(d.getDate() - 1)  // 今天的還沒跑完
  while (isWeekend(d)) d.setDate(d.getDate() - 1)
  return d
}

/** 兩日期間相隔幾個工作日（不含週末） */
export const businessDaysBetween = (from, to) => {
  let n = 0
  const cur = new Date(from)
  while (cur < to) {
    cur.setDate(cur.getDate() + 1)
    if (!isWeekend(cur)) n++
  }
  return n
}

/**
 * 資料是否過期。
 * @returns null 表示資料是新的；否則 { lag, dataDate, expectedDate }
 */
export const getDataStaleness = (dataDateStr, now = nowInTaipei()) => {
  if (!dataDateStr) return null
  const [y, m, d] = dataDateStr.split('-').map(Number)
  const expected = expectedTradingDay(now)
  const lag = businessDaysBetween(new Date(y, m - 1, d), expected)
  if (lag <= 0) return null
  return { lag, dataDate: dataDateStr, expectedDate: toYMD(expected) }
}
