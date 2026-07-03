import { useCallback, useEffect, useRef, useState } from 'react';

import { http } from '@mpt-extension/sdk';

import { ApiResponse, CommandExecution, ExecutionStatus } from '../model';

export type RecalculateStatus = 'idle' | 'loading' | 'running' | 'success' | 'error';

export const POLL_INTERVAL_MS = 5000;
export const MAX_POLL_FAILURES = 3;

const STATUS_FROM_EXECUTION: Record<ExecutionStatus, RecalculateStatus> = {
  running: 'running',
  success: 'success',
  completed_with_errors: 'error',
  failed: 'error',
};

export function useSubscriptionRecalculate(subscriptionId: string) {
  const [error, setError] = useState('');
  const [execution, setExecution] = useState<CommandExecution | null>(null);
  const [status, setStatus] = useState<RecalculateStatus>('idle');
  const pollFailures = useRef(0);

  const fetchExecution = useCallback(async (executionId: number) => {
    try {
      const response = await http.get<ApiResponse<CommandExecution>>(
        `/api/v2/executions/${executionId}`,
      );
      const latest = response.data.data;
      pollFailures.current = 0;
      setExecution(latest);
      setStatus(STATUS_FROM_EXECUTION[latest.status] ?? 'idle');
    } catch {
      // A transient poll failure keeps the in-flight run visible and retries on the
      // next tick; repeated failures stop the polling and surface the problem.
      pollFailures.current += 1;
      if (pollFailures.current >= MAX_POLL_FAILURES) {
        setError('The recalculation status could not be refreshed. Reopen to retry.');
        setStatus('error');
      }
    }
  }, []);

  const recalculate = useCallback(async () => {
    if (!subscriptionId) {
      return;
    }

    setError('');
    setStatus('loading');
    pollFailures.current = 0;

    try {
      const encodedSubscriptionId = encodeURIComponent(subscriptionId);
      const response = await http.post<ApiResponse<CommandExecution>>(
        `/api/v2/subscriptions/${encodedSubscriptionId}/recalculate`,
      );
      setExecution(response.data.data);
      setStatus('running');
    } catch (recalculateError) {
      setError(
        recalculateError instanceof Error
          ? recalculateError.message
          : 'Subscription recalculation failed.',
      );
      setStatus('error');
    }
  }, [subscriptionId]);

  const executionId = execution?.id;

  useEffect(() => {
    if (status !== 'running' || executionId === undefined) {
      return undefined;
    }

    const interval = setInterval(() => {
      void fetchExecution(executionId);
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [status, executionId, fetchExecution]);

  return { error, execution, recalculate, status };
}
