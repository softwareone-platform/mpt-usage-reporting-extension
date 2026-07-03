import { resolveSubscriptionId } from './model';

describe('subscription model helpers', () => {
  it('resolves the subscription id from the Marketplace context', () => {
    const result = resolveSubscriptionId({ data: { subscription: { id: 'SUB-1234-5678' } } });

    expect(result).toBe('SUB-1234-5678');
  });

  it('trims the subscription id from the Marketplace context', () => {
    const result = resolveSubscriptionId({ data: { subscription: { id: '  SUB-1234-5678  ' } } });

    expect(result).toBe('SUB-1234-5678');
  });

  it('returns an empty subscription id when the context subscription is missing', () => {
    const result = resolveSubscriptionId({});

    expect(result).toBe('');
  });
});
