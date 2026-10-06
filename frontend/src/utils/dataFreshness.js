// 資料新鮮度判斷
//
// 每日排程若在裸執行步驟失敗（run_daily.sh 的 run_pipeline / etf_alert /
// etf_monthly_review），結果 JSON 不會更新，但頁面照樣顯示舊資料、看不出異狀。
// 這裡直接用資料本身的日期推算落後幾個交易日：不依賴狀態檔或 LINE 推播，
// 連排程完全沒啟動也偵測得到。
//
// 已知限制：不含國定假日行事曆，休市日會誤報落後一天，故呼叫端文案需標明。
//
// 當天 16:00 開跑的結果常到晚上、甚至隔天才 push 上 GitHub。
// 若 16:30 就把「今天」當成應已發布，分頁留到下午就會每天再亮一次警告。
// 因此當天一律還看前一個交易日；隔日才要求昨天的檔。排程真的沒跑，隔天仍會亮。

const isWeekend = (d) => d.getDay() === 0 || d.getDay() === 6

/** 一律以台北時間判斷，避免使用者身處其他時區時誤判 */
export const nowInTaipei = () =>
  new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Taipei' }))

export const toYMD = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

/** 依現在時刻推算「最新結果應該是哪一個交易日」；now 可注入以便測試 */
export const expectedTradingDay = (now = nowInTaipei(), market = 'TW') => {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  // 當天這次排程尚未要求完成，先看前一個交易日
  d.setDate(d.getDate() - 1)
  while (isWeekend(d)) d.setDate(d.getDate() - 1)

  // 美股資料因時差，排程執行時只拿得到前一個交易日的資料
  if (market === 'US') {
    d.setDate(d.getDate() - 1)
    while (isWeekend(d)) d.setDate(d.getDate() - 1)
  }

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
export const getDataStaleness = (dataDateStr, market = 'TW', now = nowInTaipei()) => {
  if (!dataDateStr) return null
  const [y, m, d] = dataDateStr.split('-').map(Number)
  const expected = expectedTradingDay(now, market)
  const lag = businessDaysBetween(new Date(y, m - 1, d), expected)
  if (lag <= 0) return null
  return { lag, dataDate: dataDateStr, expectedDate: toYMD(expected) }
}
