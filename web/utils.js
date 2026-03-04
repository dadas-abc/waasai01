// 全局工具：将时间统一格式化为中国北京时间 YYYY-MM-DD HH:mm:ss
function formatCST(ts) {
  try {
    const d = (ts instanceof Date) ? ts : new Date(ts);
    const utcMs = d.getTime() + d.getTimezoneOffset() * 60000;
    const cst = new Date(utcMs + 8 * 3600 * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    const y = cst.getUTCFullYear();
    const m = pad(cst.getUTCMonth() + 1);
    const da = pad(cst.getUTCDate());
    const h = pad(cst.getUTCHours());
    const mi = pad(cst.getUTCMinutes());
    const s = pad(cst.getUTCSeconds());
    return y + '-' + m + '-' + da + ' ' + h + ':' + mi + ':' + s;
  } catch (e) {
    return (typeof ts === 'string') ? ts : '';
  }
}
window.formatCST = formatCST;
