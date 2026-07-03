export interface Reference {
  id?: string;
  name?: string;
}

export interface ApiResponse<T> {
  data: T;
}

export type ExecutionStatus = 'running' | 'success' | 'completed_with_errors' | 'failed';

export interface CommandExecution {
  id: number;
  command: string;
  status: ExecutionStatus;
  parameters?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  startedAt: string;
  completedAt: string | null;
}

export interface AccumulationPeriod {
  subscriptionId: string;
  year: number;
  month: number;
  ppx1: number;
  spx1: number;
  updatedAt: string;
}

export interface SubscriptionContext {
  data?: {
    subscription?: Reference;
  };
}

export function resolveSubscriptionId(context?: SubscriptionContext): string {
  return context?.data?.subscription?.id?.trim() ?? '';
}

