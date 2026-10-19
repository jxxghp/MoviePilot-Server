export interface Column { key: string; label: string; numeric?: boolean; time?: boolean }
/** 声明业务列表列，集中维护中文名称和展示方式。 */
const c = (key: string, label: string, numeric = false, time = false): Column => ({ key, label, numeric, time })
export const columns: Record<string, Column[]> = {
  subscriptions: [c('name', '媒体名称'), c('type', '类型'), c('year', '年份'), c('media_source', '媒体来源'), c('count', '订阅次数', true)],
  'subscribe-shares': [c('share_title', '分享标题'), c('name', '媒体名称'), c('type', '类型'), c('share_user', '分享者'), c('count', '复用次数', true), c('date', '创建时间', false, true)],
  'workflow-shares': [c('share_title', '分享标题'), c('name', '工作流名称'), c('share_user', '分享者'), c('count', '复用次数', true), c('date', '创建时间', false, true)],
  plugins: [c('plugin_id', '插件 ID'), c('count', '安装次数', true), c('average_rating', '平均评分', true), c('rating_count', '评分人数', true), c('date', '最近安装', false, true)],
  ratings: [c('plugin_id', '插件 ID'), c('user_uid', '实例标识'), c('rating', '评分', true), c('updated_at', '更新时间', false, true)],
  installations: [c('user_uid', '实例标识'), c('backend_version', '后端版本'), c('frontend_version', '前端版本'), c('platform', '平台'), c('arch', '架构'), c('last_seen_at', '最近上报', false, true)],
  audit: [c('actor', '操作者'), c('action', '操作'), c('target', '对象'), c('created_at', '时间', false, true)],
}
export const labels: Record<string, string> = {
  id: '记录 ID', name: '名称', share_title: '分享标题', share_comment: '分享介绍', share_user: '分享者', share_uid: '分享实例',
  type: '媒体类型', media_source: '媒体来源', media_id: '媒体 ID', year: '年份', season: '季号', music_type: '音乐类型',
  total_tracks: '总曲目数', total_episode: '总集数', keyword: '搜索关键词', genre_ids: '类别 ID', vote: '媒体评分', description: '介绍',
  include: '包含', exclude: '排除', quality: '质量', resolution: '分辨率', effect: '特效', custom_words: '自定义识别词',
  media_category: '媒体分类', episode_group: '剧集组', count: '次数', date: '创建/更新时间', poster: '海报地址', backdrop: '背景地址',
  timer: '定时规则', actions: '工作流动作', flows: '工作流连接', plugin_id: '插件 ID', repo_url: '仓库地址', user_uid: '实例标识',
  rating: '评分', average_rating: '平均评分', rating_count: '评分人数', created_at: '创建时间', updated_at: '更新时间',
  backend_version: '后端版本', frontend_version: '前端版本', version_flag: '版本分支', platform: '平台', arch: '架构',
  first_seen_at: '首次上报', last_seen_at: '最近上报', report_count: '上报次数', actor: '操作者', action: '操作', target: '对象', detail: '变更内容',
}
