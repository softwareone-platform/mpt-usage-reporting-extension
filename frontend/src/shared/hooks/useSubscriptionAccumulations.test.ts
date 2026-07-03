import { act, renderHook, waitFor } from '@testing-library/react';

import { http } from '@mpt-extension/sdk';

import { useSubscriptionAccumulations } from './useSubscriptionAccumulations';

jest.mock('@mpt-extension/sdk', () => ({
  http: {
    get: jest.fn(),
  },
}), { virtual: true });

const mockGet = jest.mocked(http.get);

const periods = [
  { subscriptionId: 'SUB-1234-5678', year: 2026, month: 6, ppx1: 12.5, spx1: 15, updatedAt: '2026-07-01T09:00:00Z' },
  { subscriptionId: 'SUB-1234-5678', year: 2026, month: 5, ppx1: 7.25, spx1: 9.1, updatedAt: '2026-06-01T09:00:00Z' },
];

describe('useSubscriptionAccumulations', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('fetches the accumulated months on mount', async () => {
    mockGet.mockResolvedValue({ data: { data: { accumulations: periods } } });

    const { result } = renderHook(() => useSubscriptionAccumulations('SUB-1234-5678'));

    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(mockGet).toHaveBeenCalledWith('/api/v2/subscriptions/SUB-1234-5678/accumulations');
    expect(result.current.accumulations).toEqual(periods);
  });

  it('stays empty without calling the API when the subscription id is missing', async () => {
    const { result } = renderHook(() => useSubscriptionAccumulations(''));

    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(mockGet).not.toHaveBeenCalled();
    expect(result.current.accumulations).toEqual([]);
  });

  it('records fetch failures with an empty list', async () => {
    mockGet.mockRejectedValue(new Error('gateway timeout'));

    const { result } = renderHook(() => useSubscriptionAccumulations('SUB-1234-5678'));

    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.accumulations).toEqual([]);
  });

  it('refreshes the list on demand', async () => {
    mockGet.mockResolvedValue({ data: { data: { accumulations: [] } } });

    const { result } = renderHook(() => useSubscriptionAccumulations('SUB-1234-5678'));

    await waitFor(() => expect(result.current.status).toBe('ready'));
    mockGet.mockResolvedValue({ data: { data: { accumulations: periods } } });
    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.accumulations).toEqual(periods);
  });
});
