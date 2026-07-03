import { useEffect } from 'react';

import { useMPTModal } from '@mpt-extension/sdk-react';
import { Button } from '@softwareone-platform/sdk-react-ui-v0/button';
import { Chip } from '@softwareone-platform/sdk-react-ui-v0/chip';
import { InPageHighlight } from '@softwareone-platform/sdk-react-ui-v0/in-page-highlight';
import { InlineNotification } from '@softwareone-platform/sdk-react-ui-v0/notification';
import { BoldText, RegularText } from '@softwareone-platform/sdk-react-ui-v0/text';

import { useSubscriptionAccumulations } from '../../shared/hooks/useSubscriptionAccumulations';
import { useSubscriptionId } from '../../shared/hooks/useSubscriptionId';
import {
  RecalculateStatus,
  useSubscriptionRecalculate,
} from '../../shared/hooks/useSubscriptionRecalculate';
import '../../shared/components/ActionModal.scss';

const STATUS_LABEL: Record<RecalculateStatus, string> = {
  idle: 'Idle',
  loading: 'Starting',
  running: 'Running',
  success: 'Success',
  error: 'Error',
};

const STATUS_COLOR: Record<RecalculateStatus, 'gray' | 'primary' | 'success' | 'danger'> = {
  idle: 'gray',
  loading: 'primary',
  running: 'primary',
  success: 'success',
  error: 'danger',
};

function monthLabel(year: number, month: number): string {
  return new Date(year, month - 1, 1).toLocaleDateString('en-GB', {
    month: 'short',
    year: 'numeric',
  });
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return '—';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  });
}

export default function App() {
  const subscriptionId = useSubscriptionId();
  const { error, execution, recalculate, status } = useSubscriptionRecalculate(subscriptionId);
  const { accumulations, refresh, status: accumulationsStatus } =
    useSubscriptionAccumulations(subscriptionId);
  const { close } = useMPTModal();
  const isBusy = status === 'loading' || status === 'running';

  useEffect(() => {
    // A finished run has rebuilt the buckets; reload the accumulated months.
    if (status === 'success' || status === 'error') {
      void refresh();
    }
  }, [status, refresh]);

  return (
    <div className="action-modal">
      <div className="action-modal__content">
        <BoldText as="h2" size={4}>
          Recalculate usage
        </BoldText>
        <RegularText as="p" size={2} color="grey-5">
          Reset and re-accumulate this subscription&apos;s usage for the last 13 months.
        </RegularText>

        {!subscriptionId && (
          <InlineNotification status="warning" isStandalone>
            Subscription context was not provided by Marketplace.
          </InlineNotification>
        )}

        <InPageHighlight style="inline" mode="sparse" direction="vertical">
          <InPageHighlight.Item title="Current status">
            <Chip color={STATUS_COLOR[status]} label={STATUS_LABEL[status]} />
          </InPageHighlight.Item>
          <InPageHighlight.Item title="Last run started">
            {formatTimestamp(execution?.startedAt)}
          </InPageHighlight.Item>
          <InPageHighlight.Item title="Last run completed">
            {formatTimestamp(execution?.completedAt)}
          </InPageHighlight.Item>
        </InPageHighlight>

        <BoldText as="h3" size={3}>
          Monthly accumulations
        </BoldText>
        {accumulations.length > 0 ? (
          <table className="action-modal__table">
            <thead>
              <tr>
                <th>Month</th>
                <th>Purchase (PPx1)</th>
                <th>Sales (SPx1)</th>
                <th>Last updated</th>
              </tr>
            </thead>
            <tbody>
              {accumulations.map((period) => (
                <tr key={`${period.year}-${period.month}`}>
                  <td>{monthLabel(period.year, period.month)}</td>
                  <td>{period.ppx1.toFixed(2)}</td>
                  <td>{period.spx1.toFixed(2)}</td>
                  <td>{formatTimestamp(period.updatedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <RegularText as="p" size={2} color="grey-5">
            {accumulationsStatus === 'error'
              ? 'The stored accumulations could not be loaded.'
              : 'No accumulations stored.'}
          </RegularText>
        )}

        {status === 'error' && (
          <InlineNotification status="error" isStandalone>
            {error || `The last recalculation finished as "${execution?.status ?? 'failed'}".`}
          </InlineNotification>
        )}
      </div>
      <div className="action-modal__actions">
        <Button
          isBusy={isBusy}
          isDisabled={!subscriptionId || isBusy}
          onClick={recalculate}
          color="primary"
          type="outline"
        >
          Recalculate
        </Button>
        <Button color="primary" type="solid" onClick={() => close()}>
          Close
        </Button>
      </div>
    </div>
  );
}
