export interface SourceMonitorState {
  status?: string;
  last_completed_at?: string | null;
  counts?: Record<string, number>;
}
export interface CreatorMonitor {
  subscribed: boolean;
  enabled?: boolean;
  next_run_at?: string | null;
  last_scan_at?: string | null;
  monitor?: SourceMonitorState;
}
export const monitorStatus: Record<string, string> = {
  running: '正在检查更新',
  partial: '等待继续检查',
  complete: '完整检查完成',
  window_complete: '增量检查完成',
  unstable: '列表变化，等待复查',
  blocked: '检查暂时受阻',
};
