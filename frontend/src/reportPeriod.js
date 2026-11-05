const presets = {last3:3,last6:6}

function currentMonth(now) {
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit'}).formatToParts(now).map(p=>[p.type,p.value]))
  return `${parts.year}-${parts.month}`
}

function shift(month,delta) {
  const [year,number]=month.split('-').map(Number)
  const date=new Date(Date.UTC(year,number-1+delta,1))
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth()+1).padStart(2,'0')}`
}

export function reportPeriod(preset,now=new Date()) {
  const month=currentMonth(now)
  if(preset==='year')return [month.slice(0,4)+'-01',month]
  const count=presets[preset]||1
  return [shift(month,-count),shift(month,-1)]
}

export function monthLabel(month) {
  if(!month)return '未选择'
  const [year,number]=month.split('-')
  return `${year}年${Number(number)}月`
}

export function monthRangeError(range) {
  if(!Array.isArray(range)||range.length!==2||range.some(m=>typeof m!=='string'||!/^\d{4}-(0[1-9]|1[0-2])$/.test(m)))return '请选择完整的起止月份。'
  if(range[0]>range[1])return '起始月份不能晚于结束月份。'
  const [ay,am]=range[0].split('-').map(Number),[by,bm]=range[1].split('-').map(Number)
  if((by-ay)*12+bm-am>=36)return '一次最多统计36个月，请缩小月份范围。'
  return ''
}
