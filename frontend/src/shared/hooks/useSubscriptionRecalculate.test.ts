import { act, renderHook, waitFor } from '@testing-library/react';

import { http } from '@mpt-extension/sdk';

import {
  MAX_POLL_FAILURES,
  POLL_INTERVAL_MS,
  useSubscriptionRecalculate,
} from './useSubscriptionRecalculate';

jest.mock('@mpt-extension/sdk', () => ({
  http: {
    get: jest.fn(),
    post: jest.fn(),
  },
}), { virtual: true });

const mockGet = jest.mocked(http.get);
const mockPost = jest.mocked(http.post);

const runningExecution = {
  id: 11,
  command: 'recalculate',
  status: 'running',
  startedAt: '2026-07-02T10:00:00+00:00',
  completedAt: null,
};

describe('useSubscriptionRecalculate', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.useRealTimers();
  });

  it('starts idle without calling the API', () => {
    const { result } = renderHook(() => useSubscriptionRecalculate('SUB-1234-5678'));

    expect(mockGet).not.toHaveBeenCalled();
    expect(mockPost).not.toHaveBeenCalled();
    expect(result.current.status).toBe('idle');
  });

  it('does not post when the subscription id is missing', async () => {
    const { result } = renderHook(() => useSubscriptionRecalculate(''));

    await act(async () => {
      await result.current.recalculate();
    });

    expect(mockPost).not.toHaveBeenCalled();
    expect(result.current.status).toBe('idle');
  });

  it('posts the recalculate and marks the run as running', async () => {
    mockPost.mockResolvedValue({ data: { data: runningExecution } });

    const { result } = renderHook(() => useSubscriptionRecalculate('SUB-1234-5678'));

    await act(async () => {
      await result.current.recalculate();
    });

    expect(mockPost).toHaveBeenCalledWith('/api/v2/subscriptions/SUB-1234-5678/recalculate');
    expect(result.current.status).toBe('running');
    expect(result.current.execution).toEqual(runningExecution);
  });

  it('records launch failures', async () => {
    mockPost.mockRejectedValue(new Error('already running'));

    const { result } = renderHook(() => useSubscriptionRecalculate('SUB-1234-5678'));

    await act(async () => {
      await result.current.recalculate();
    });

    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe('already running');
  });

  it('polls the execution by id until the run finishes', async () => {
    jest.useFakeTimers();
    mockPost.mockResolvedValue({ data: { data: runningExecution } });

    const { result } = renderHook(() => useSubscriptionRecalculate('SUB-1234-5678'));

    await act(async () => {
      await result.current.recalculate();
    });
    mockGet.mockResolvedValue({
      data: {
        data: { ...runningExecution, status: 'success', completedAt: '2026-07-02T10:05:00+00:00' },
      },
    });
    await act(async () => {
      jest.advanceTimersByTime(POLL_INTERVAL_MS);
    });

    expect(mockGet).toHaveBeenLastCalledWith('/api/v2/executions/11');
    await waitFor(() => expect(result.current.status).toBe('success'));
    expect(result.current.execution?.completedAt).toBe('2026-07-02T10:05:00+00:00');
  });

  it('keeps the run visible when a poll tick fails', async () => {
    jest.useFakeTimers();
    mockPost.mockResolvedValue({ data: { data: runningExecution } });
    mockGet.mockRejectedValue(new Error('gateway timeout'));

    const { result } = renderHook(() => useSubscriptionRecalculate('SUB-1234-5678'));

    await act(async () => {
      await result.current.recalculate();
    });
    await act(async () => {
      jest.advanceTimersByTime(POLL_INTERVAL_MS);
    });

    expect(mockGet).toHaveBeenCalledWith('/api/v2/executions/11');
    expect(result.current.status).toBe('running');
  });

  it('surfaces an error after repeated poll failures', async () => {
    jest.useFakeTimers();
    mockPost.mockResolvedValue({ data: { data: runningExecution } });
    mockGet.mockRejectedValue(new Error('gateway timeout'));

    const { result } = renderHook(() => useSubscriptionRecalculate('SUB-1234-5678'));

    await act(async () => {
      await result.current.recalculate();
    });
    for (let tick = 0; tick < MAX_POLL_FAILURES; tick += 1) {
      await act(async () => {
        jest.advanceTimersByTime(POLL_INTERVAL_MS);
      });
    }

    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe(
      'The recalculation status could not be refreshed. Reopen to retry.',
    );
  });
});
