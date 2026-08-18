/* 全局数字展示规范：金额统一千位分隔、两位小数；大额金额使用“万”；零值不使用“0万”。 */
(function (root) {
  const numberFormatter = new Intl.NumberFormat('zh-CN', {
    useGrouping: true,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  function numeric(value) {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatNumber(value, options) {
    const parsed = numeric(value);
    if (parsed === null) return '—';
    const decimals = options && Number.isInteger(options.decimals) ? options.decimals : 2;
    return new Intl.NumberFormat('zh-CN', {
      useGrouping: true,
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(parsed);
  }

  function formatMoney(value, options) {
    const parsed = numeric(value);
    if (parsed === null) return '—';
    const compact = !options || options.compact !== false;
    // 零金额始终保留为 ¥0.00，禁止 ¥0万 / 0万 / ¥0.00万。
    if (compact && Math.abs(parsed) >= 10000) {
      return `¥${numberFormatter.format(parsed / 10000)}万`;
    }
    return `¥${numberFormatter.format(parsed)}`;
  }

  root.AppNumberFormat = Object.freeze({ formatNumber, formatMoney });
  root.formatNumber = formatNumber;
  root.formatMoney = formatMoney;
})(window);
