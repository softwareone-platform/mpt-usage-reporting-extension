import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

import { http } from '@mpt-extension/sdk';
import { useMPTContext, useMPTModal } from '@mpt-extension/sdk-react';

import App from './App';

jest.mock('@mpt-extension/sdk', () => ({
  http: {
    get: jest.fn(),
    post: jest.fn(),
  },
}), { virtual: true });

jest.mock('@mpt-extension/sdk-react', () => ({
  useMPTContext: jest.fn(),
  useMPTModal: jest.fn(),
}), { virtual: true });

jest.mock('@softwareone-platform/sdk-react-ui-v0/button', () => {
  const React = jest.requireActual<typeof import('react')>('react');

  return {
    Button: ({
      children,
      isBusy,
      isDisabled,
      onClick,
    }: {
      children?: import('react').ReactNode;
      isBusy?: boolean;
      isDisabled?: boolean;
      onClick?: () => void;
    }) => React.createElement('button', { disabled: isDisabled || isBusy, onClick }, children),
  };
});

jest.mock('@softwareone-platform/sdk-react-ui-v0/chip', () => {
  const React = jest.requireActual<typeof import('react')>('react');

  return {
    Chip: ({ label }: { label?: string }) => React.createElement('span', null, label),
  };
});

jest.mock('@softwareone-platform/sdk-react-ui-v0/in-page-highlight', () => {
  const React = jest.requireActual<typeof import('react')>('react');
  const InPageHighlight = ({ children }: { children?: import('react').ReactNode }) =>
    React.createElement('div', null, children);

  InPageHighlight.Item = ({
    children,
    title,
  }: {
    children?: import('react').ReactNode;
    title?: string;
  }) => React.createElement('div', null, title, children);

  return { InPageHighlight };
});

jest.mock('@softwareone-platform/sdk-react-ui-v0/notification', () => {
  const React = jest.requireActual<typeof import('react')>('react');

  return {
    InlineNotification: ({ children }: { children?: import('react').ReactNode }) =>
      React.createElement('div', null, children),
  };
});

jest.mock('@softwareone-platform/sdk-react-ui-v0/text', () => {
  const React = jest.requireActual<typeof import('react')>('react');
  const renderText = ({
    as = 'span',
    children,
  }: {
    as?: string;
    children?: import('react').ReactNode;
  }) => React.createElement(as, null, children);

  return {
    BoldText: renderText,
    RegularText: renderText,
  };
});

const mockGet = jest.mocked(http.get);
const mockPost = jest.mocked(http.post);
const mockUseMPTContext = jest.mocked(useMPTContext);
const mockUseMPTModal = jest.mocked(useMPTModal);
const close = jest.fn();

const runningExecution = {
  id: 11,
  command: 'recalculate',
  status: 'running',
  startedAt: '2026-07-02T10:00:00+00:00',
  completedAt: null,
};

describe('subscription recalculate action modal', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGet.mockResolvedValue({ data: { data: { accumulations: [] } } });
    mockUseMPTModal.mockReturnValue({ open: jest.fn(), close });
  });

  it('launches a recalculation and shows the running status', async () => {
    mockUseMPTContext.mockReturnValue({ data: { subscription: { id: 'SUB-1234-5678' } } });
    mockPost.mockResolvedValue({ data: { data: runningExecution } });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Recalculate' }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/v2/subscriptions/SUB-1234-5678/recalculate');
    });
    expect(await screen.findByText('Running')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Recalculate' })).toBeDisabled();
  });

  it('disables the recalculation when the subscription context is missing', () => {
    mockUseMPTContext.mockReturnValue({});

    render(<App />);

    expect(screen.getByRole('button', { name: 'Recalculate' })).toBeDisabled();
    expect(
      screen.getByText('Subscription context was not provided by Marketplace.'),
    ).toBeInTheDocument();
  });

  it('renders launch errors', async () => {
    mockUseMPTContext.mockReturnValue({ data: { subscription: { id: 'SUB-1234-5678' } } });
    mockPost.mockRejectedValue(
      new Error('A recalculate for subscription SUB-1234-5678 is already running'),
    );

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Recalculate' }));

    expect(
      await screen.findByText('A recalculate for subscription SUB-1234-5678 is already running'),
    ).toBeInTheDocument();
  });

  it('lists the accumulated months with their last update', async () => {
    mockUseMPTContext.mockReturnValue({ data: { subscription: { id: 'SUB-1234-5678' } } });
    mockGet.mockResolvedValue({
      data: {
        data: {
          accumulations: [
            {
              subscriptionId: 'SUB-1234-5678',
              year: 2026,
              month: 6,
              ppx1: 12.5,
              spx1: 15,
              updatedAt: '2026-07-01T09:00:00Z',
            },
            {
              subscriptionId: 'SUB-1234-5678',
              year: 2026,
              month: 5,
              ppx1: 7.25,
              spx1: 9.1,
              updatedAt: '2026-06-01T09:00:00Z',
            },
          ],
        },
      },
    });

    render(<App />);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith('/api/v2/subscriptions/SUB-1234-5678/accumulations');
    });
    expect(await screen.findByText('Jun 2026')).toBeInTheDocument();
    expect(screen.getByText('May 2026')).toBeInTheDocument();
    expect(screen.getByText('12.50')).toBeInTheDocument();
    expect(screen.getByText('9.10')).toBeInTheDocument();
    expect(screen.getByText('1 Jul 2026, 09:00')).toBeInTheDocument();
  });

  it('shows a placeholder when no accumulations are stored', async () => {
    mockUseMPTContext.mockReturnValue({ data: { subscription: { id: 'SUB-1234-5678' } } });

    render(<App />);

    expect(await screen.findByText('No accumulations stored.')).toBeInTheDocument();
  });

  it('closes the modal on Close button click', () => {
    mockUseMPTContext.mockReturnValue({});

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    expect(close).toHaveBeenCalled();
  });
});
