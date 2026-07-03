import { useCallback, useEffect, useState } from 'react';

import { http } from '@mpt-extension/sdk';

import { AccumulationPeriod, ApiResponse } from '../model';

export type AccumulationsStatus = 'loading' | 'ready' | 'error';

interface AccumulationsPayload {
  accumulations: AccumulationPeriod[];
}

export function useSubscriptionAccumulations(subscriptionId: string) {
  const [accumulations, setAccumulations] = useState<AccumulationPeriod[]>([]);
  const [status, setStatus] = useState<AccumulationsStatus>('loading');

  const refresh = useCallback(async () => {
    if (!subscriptionId) {
      setStatus('ready');
      return;
    }

    try {
      const encodedSubscriptionId = encodeURIComponent(subscriptionId);
      const response = await http.get<ApiResponse<AccumulationsPayload>>(
        `/api/v2/subscriptions/${encodedSubscriptionId}/accumulations`,
      );
      setAccumulations(response.data.data.accumulations);
      setStatus('ready');
    } catch {
      setAccumulations([]);
      setStatus('error');
    }
  }, [subscriptionId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { accumulations, refresh, status };
}
