import { createI18n } from 'vue-i18n'

export const SUPPORTED_LOCALES = ['vi', 'en', 'zh-CN'] as const
export type AppLocale = (typeof SUPPORTED_LOCALES)[number]
export const DEFAULT_LOCALE: AppLocale = 'vi'

export const messages = {
  vi: {
    app: { name: 'Gpt_reg', tagline: 'Vận hành tài khoản', connected: 'Đã kết nối', reconnecting: 'Đang kết nối lại' },
    nav: { primary: 'Điều hướng chính', registration: 'Đăng ký', checks: 'Kiểm tra', settings: 'Cài đặt', collapse: 'Thu gọn', expand: 'Mở rộng' },
    common: {
      search: 'Tìm kiếm', filter: 'Bộ lọc', all: 'Tất cả', run: 'Chạy', stop: 'Dừng', retry: 'Thử lại',
      export: 'Xuất', copy: 'Sao chép', clear: 'Xoá', save: 'Lưu', refresh: 'Làm mới', cancel: 'Huỷ', confirm: 'Xác nhận',
      total: 'Tổng', success: 'Thành công', running: 'Đang chạy', errors: 'Lỗi', queued: 'Chờ', details: 'Chi tiết',
      language: 'Ngôn ngữ', theme: 'Giao diện', light: 'Sáng', dark: 'Tối', show: 'Hiện', hide: 'Ẩn', close: 'Đóng', noData: 'Chưa có dữ liệu', actions: 'Thao tác',
    },
    registration: {
      title: 'Đăng ký', subtitle: 'Đăng ký tài khoản theo lô', batch: 'Cấu hình lô', input: 'Combo đầu vào',
      inputPlaceholder: 'email|password|refresh_token|client_id', source: 'Nguồn', mode: 'Chế độ', browser: 'Browser', http: 'HTTP',
      headless: 'Headless', twofa: '2FA', engineFallback: 'Fallback engine', concurrency: 'Số luồng', run: 'Chạy đăng ký', stopAll: 'Dừng tất cả',
      clearInput: 'Xoá dữ liệu nhập', jobs: 'Tác vụ', activity: 'Hoạt động', results: 'Kết quả', successful: 'Thành công', failed: 'Lỗi',
      retryFailed: 'Thử lại lỗi', retryOne: 'Thử lại', deleteOne: 'Xoá account', clearDone: 'Xoá đã xong', clearAll: 'Xoá tất cả', viewAll: 'Tất cả tác vụ', comboCount: '{count} combo',
      gmailUnavailable: 'Gmail chưa hỗ trợ đăng ký tự động', noJobs: 'Dán combo và chạy batch để bắt đầu.',
      noActivity: 'Log realtime sẽ xuất hiện tại đây.', exportFormat: 'Định dạng xuất', selectedJob: 'Job đang chọn',
    },
    checks: {
      title: 'Kiểm tra tài khoản', subtitle: 'Đăng nhập HTTP và đọc gói tài khoản', batch: 'Danh sách kiểm tra', input: 'Tài khoản đầu vào',
      inputPlaceholder: 'mail|pass|2fa\nmail|pass|2fa|email|mailpass|refresh|client_id', concurrency: 'Số luồng',
      run: 'Check plan', stop: 'Dừng', retry: 'Retry lỗi', export: 'Xuất Live', clearDone: 'Xoá kết quả',
      results: 'Kết quả', live: 'Live', invalid: 'Die/Lỗi', email: 'Email', plan: 'Plan', status: 'Trạng thái', mfa: '2FA',
      searchPlaceholder: 'Tìm email...', allPlans: 'Mọi plan', allStatuses: 'Mọi trạng thái', noResults: 'Chưa có kết quả check.',
      lineCount: '{count} dòng', subscription: 'Đăng ký', expires: 'Hết hạn', deactivated: 'Đã vô hiệu hoá',
    },
    settings: {
      title: 'Cài đặt', subtitle: 'Proxy, SMS và tuỳ chọn giao diện', proxy: 'Proxy', proxyPool: 'Proxy pool',
      proxyHint: 'Mỗi dòng một proxy', rotation: 'Rotation mode', roundRobin: 'Round robin', random: 'Random',
      sms: 'SMSBower', apiKey: 'API key', country: 'Quốc gia', notConfigured: 'Chưa cấu hình', appearance: 'Giao diện',
      saved: 'Đã lưu', saveFailed: 'Lưu thất bại', balance: 'Số dư', inventory: 'Tồn kho', affordable: 'Có thể mua',
    },
    status: {
      idle: 'Idle', queued: 'Chờ', running: 'Đang chạy', success: 'Thành công', error: 'Lỗi', cancelled: 'Đã huỷ',
      live: 'LIVE', die: 'DIE', onboarding: 'Chưa hoàn tất', offline: 'Mất kết nối',
    },
    toast: {
      copied: 'Đã copy {count} dòng', nothingToCopy: 'Không có dữ liệu để copy', stopped: 'Đã yêu cầu dừng',
      removed: 'Đã xoá {count} mục', retrying: 'Đang retry {count} mục', started: 'Đã bắt đầu {count} mục', saved: 'Đã lưu cấu hình',
      requestFailed: 'Thao tác thất bại', connected: 'Đã kết nối lại server', disconnected: 'Mất kết nối, đang thử lại',
    },
    errors: { noCombos: 'Chưa có combo nào', invalidCombo: 'Combo không hợp lệ', gmailUnavailable: 'Nguồn Gmail chưa hỗ trợ đăng ký tự động', jobNotFound: 'Không tìm thấy tác vụ' },
    confirm: {
      clearTitle: 'Xác nhận xoá dữ liệu', clearDone: 'Xoá các mục đã hoàn tất?', clearAll: 'Xoá toàn bộ mục đã kết thúc? Thao tác này không thể hoàn tác.',
      clearChecks: 'Xoá các kết quả check đã hoàn tất?', deleteOne: 'Xoá {email}? Thao tác này không thể hoàn tác.',
    },
  },
  en: {
    app: { name: 'Gpt_reg', tagline: 'Account operations', connected: 'Connected', reconnecting: 'Reconnecting' },
    nav: { primary: 'Primary navigation', registration: 'Registration', checks: 'Account check', settings: 'Settings', collapse: 'Collapse', expand: 'Expand' },
    common: {
      search: 'Search', filter: 'Filter', all: 'All', run: 'Run', stop: 'Stop', retry: 'Retry', export: 'Export', copy: 'Copy',
      clear: 'Clear', save: 'Save', refresh: 'Refresh', cancel: 'Cancel', confirm: 'Confirm', total: 'Total', success: 'Success',
      running: 'Running', errors: 'Errors', queued: 'Queued', details: 'Details', language: 'Language', theme: 'Theme', light: 'Light',
      dark: 'Dark', show: 'Show', hide: 'Hide', close: 'Close', noData: 'No data', actions: 'Actions',
    },
    registration: {
      title: 'Registration', subtitle: 'Batch account registration', batch: 'Batch setup', input: 'Input combos',
      inputPlaceholder: 'email|password|refresh_token|client_id', source: 'Source', mode: 'Mode', browser: 'Browser', http: 'HTTP',
      headless: 'Headless', twofa: '2FA', engineFallback: 'Engine fallback', concurrency: 'Concurrency', run: 'Run registration', stopAll: 'Stop all',
      clearInput: 'Clear input', jobs: 'Jobs', activity: 'Activity', results: 'Results', successful: 'Success', failed: 'Error',
      retryFailed: 'Retry failed', retryOne: 'Retry', deleteOne: 'Delete account', clearDone: 'Clear completed', clearAll: 'Clear all', viewAll: 'All jobs', comboCount: '{count} combos',
      gmailUnavailable: 'Automatic Gmail registration is not available', noJobs: 'Paste combos and run a batch to begin.',
      noActivity: 'Realtime logs will appear here.', exportFormat: 'Export format', selectedJob: 'Selected job',
    },
    checks: {
      title: 'Account check', subtitle: 'HTTP login and account plan lookup', batch: 'Check list', input: 'Account input',
      inputPlaceholder: 'mail|pass|2fa\nmail|pass|2fa|email|mailpass|refresh|client_id', concurrency: 'Concurrency',
      run: 'Check plans', stop: 'Stop', retry: 'Retry failed', export: 'Export live', clearDone: 'Clear results', results: 'Results',
      live: 'Live', invalid: 'Die/Error', email: 'Email', plan: 'Plan', status: 'Status', mfa: '2FA', searchPlaceholder: 'Search email...',
      allPlans: 'All plans', allStatuses: 'All statuses', noResults: 'No account checks yet.', lineCount: '{count} lines',
      subscription: 'Subscription', expires: 'Expires', deactivated: 'Deactivated',
    },
    settings: {
      title: 'Settings', subtitle: 'Proxy, SMS, and appearance', proxy: 'Proxy', proxyPool: 'Proxy pool', proxyHint: 'One proxy per line',
      rotation: 'Rotation mode', roundRobin: 'Round robin', random: 'Random', sms: 'SMSBower', apiKey: 'API key', country: 'Country',
      notConfigured: 'Not configured', appearance: 'Appearance', saved: 'Saved', saveFailed: 'Save failed', balance: 'Balance',
      inventory: 'Inventory', affordable: 'Affordable',
    },
    status: {
      idle: 'Idle', queued: 'Queued', running: 'Running', success: 'Success', error: 'Error', cancelled: 'Cancelled', live: 'LIVE',
      die: 'DIE', onboarding: 'Incomplete', offline: 'Disconnected',
    },
    toast: {
      copied: 'Copied {count} lines', nothingToCopy: 'Nothing to copy', stopped: 'Stop requested', removed: 'Removed {count} items',
      retrying: 'Retrying {count} items', started: 'Started {count} items', saved: 'Settings saved', requestFailed: 'Request failed',
      connected: 'Reconnected to server', disconnected: 'Connection lost, retrying',
    },
    errors: { noCombos: 'No combos provided', invalidCombo: 'Invalid combo', gmailUnavailable: 'Automatic Gmail registration is unavailable', jobNotFound: 'Job not found' },
    confirm: {
      clearTitle: 'Confirm data removal', clearDone: 'Remove completed items?',
      clearAll: 'Remove all finished items? This action cannot be undone.', clearChecks: 'Remove completed check results?', deleteOne: 'Delete {email}? This action cannot be undone.',
    },
  },
  'zh-CN': {
    app: { name: 'Gpt_reg', tagline: '账户运营中心', connected: '已连接', reconnecting: '正在重新连接' },
    nav: { primary: '主导航', registration: '批量注册', checks: '账号检测', settings: '设置', collapse: '收起', expand: '展开' },
    common: {
      search: '搜索', filter: '筛选', all: '全部', run: '运行', stop: '停止', retry: '重试', export: '导出', copy: '复制', clear: '清除',
      save: '保存', refresh: '刷新', cancel: '取消', confirm: '确认', total: '总数', success: '成功', running: '运行中', errors: '错误',
      queued: '排队中', details: '详情', language: '语言', theme: '主题', light: '浅色', dark: '深色', show: '显示', hide: '隐藏', close: '关闭', noData: '暂无数据', actions: '操作',
    },
    registration: {
      title: '批量注册', subtitle: '批量创建账户', batch: '批次设置', input: '账号组合',
      inputPlaceholder: 'email|password|refresh_token|client_id', source: '来源', mode: '模式', browser: '浏览器', http: 'HTTP',
      headless: '无头模式', twofa: '2FA', engineFallback: '引擎回退', concurrency: '并发数', run: '开始注册', stopAll: '全部停止', clearInput: '清空输入',
      jobs: '任务', activity: '实时日志', results: '结果', successful: '成功', failed: '错误', retryFailed: '重试失败', retryOne: '重试', deleteOne: '删除账号',
      clearDone: '清除已完成', clearAll: '全部清除', viewAll: '全部任务', comboCount: '{count} 个组合',
      gmailUnavailable: '暂不支持自动注册 Gmail', noJobs: '粘贴账号组合并运行批次。', noActivity: '实时日志将显示在这里。',
      exportFormat: '导出格式', selectedJob: '当前任务',
    },
    checks: {
      title: '账号检测', subtitle: '通过 HTTP 登录并读取套餐', batch: '检测列表', input: '账号输入',
      inputPlaceholder: 'mail|pass|2fa\nmail|pass|2fa|email|mailpass|refresh|client_id', concurrency: '并发数', run: '检测套餐',
      stop: '停止', retry: '重试失败', export: '导出有效账号', clearDone: '清除结果', results: '检测结果', live: '有效', invalid: '无效/错误',
      email: '邮箱', plan: '套餐', status: '状态', mfa: '2FA', searchPlaceholder: '搜索邮箱...', allPlans: '全部套餐',
      allStatuses: '全部状态', noResults: '暂无检测结果。', lineCount: '{count} 行', subscription: '订阅', expires: '到期时间', deactivated: '已停用',
    },
    settings: {
      title: '设置', subtitle: '代理、短信与外观', proxy: '代理', proxyPool: '代理池', proxyHint: '每行一个代理', rotation: '轮换模式',
      roundRobin: '轮询', random: '随机', sms: 'SMSBower', apiKey: 'API 密钥', country: '国家', notConfigured: '未配置',
      appearance: '外观', saved: '已保存', saveFailed: '保存失败', balance: '余额', inventory: '库存', affordable: '可购买',
    },
    status: {
      idle: '空闲', queued: '排队中', running: '运行中', success: '成功', error: '错误', cancelled: '已取消', live: '有效',
      die: '无效', onboarding: '未完成', offline: '连接断开',
    },
    toast: {
      copied: '已复制 {count} 行', nothingToCopy: '没有可复制的数据', stopped: '已请求停止', removed: '已删除 {count} 项',
      retrying: '正在重试 {count} 项', started: '已启动 {count} 项', saved: '设置已保存', requestFailed: '请求失败',
      connected: '已重新连接服务器', disconnected: '连接断开，正在重试',
    },
    errors: { noCombos: '尚未提供账号组合', invalidCombo: '账号组合格式无效', gmailUnavailable: '暂不支持自动注册 Gmail', jobNotFound: '未找到任务' },
    confirm: {
      clearTitle: '确认删除数据', clearDone: '删除已完成的项目？', clearAll: '删除所有已结束项目？此操作无法撤销。',
      clearChecks: '删除已完成的检测结果？', deleteOne: '删除 {email}？此操作无法撤销。',
    },
  },
} as const

export function resolveLocale(saved: string | null, browserLocale: string): AppLocale {
  if (SUPPORTED_LOCALES.includes(saved as AppLocale)) return saved as AppLocale
  const normalized = browserLocale.toLowerCase()
  if (normalized.startsWith('zh')) return 'zh-CN'
  if (normalized.startsWith('en')) return 'en'
  if (normalized.startsWith('vi')) return 'vi'
  return DEFAULT_LOCALE
}

export function createAppI18n(locale: AppLocale) {
  return createI18n({ legacy: false, locale, fallbackLocale: DEFAULT_LOCALE, messages })
}
