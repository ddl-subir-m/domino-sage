// A working example of a screen with a delayed search, a select and a paged table. Copy its shape,
// not its names: `static/index.html` does not load this file, and its queries are the fixture
// queries declared beside it in `orders.queries.json`, which this app does not have. A real screen
// lives in `static/components/` and asks the queries in this app's own `.sage/queries.json`.
(function () {
  const { createElement: h } = React;
  window.app = window.app || {};

  const PAGE_SIZE = 50;

  // Records keyed the way the store spells the columns (`REGION` on Snowflake), read lower-case.
  const lowerKeys = (record) =>
    Object.fromEntries(Object.entries(record).map(([k, v]) => [k.toLowerCase(), v]));
  const rowsOf = (q) => (q.data ? q.data.records.map(lowerKeys) : []);

  // The screen's state and queries, apart from how they are drawn.
  function useOrdersView() {
    const [draft, setDraft] = React.useState('');
    const search = sage.useDebouncedValue(draft.trim());
    const [region, setRegion] = React.useState('__all__');
    // The page belongs to one filter: a new search or region starts again at the first page.
    const filter = JSON.stringify([search, region]);
    const [paging, setPaging] = React.useState({ filter, page: 0 });
    const page = paging.filter === filter ? paging.page : 0;

    const regions = sage.useQuery('example_order_regions');
    const summary = sage.useQuery('example_orders_summary', { region, search });
    const detail = sage.useQuery('example_orders_page', { region, search, offset: page * PAGE_SIZE });
    const counted = rowsOf(summary)[0];

    return {
      draft, setDraft, region, setRegion, page,
      setPage: (next) => setPaging({ filter, page: next }),
      regions, summary, detail,
      regionChoices: rowsOf(regions).map((r) => r.region),
      rows: rowsOf(detail),
      total: counted ? counted.order_count : null,
      amount: counted ? counted.total_amount : null,
    };
  }

  function OrdersScreen() {
    const v = useOrdersView();
    const columns = ['order_id', 'placed_on', 'customer', 'region', 'amount']
      .map((key) => ({ title: key.replace('_', ' '), dataIndex: key, key }));
    return h('main', { className: 'app' },
      h(antd.Space, { wrap: true },
        h(antd.Input.Search, {
          placeholder: 'Search customers', allowClear: true, value: v.draft,
          onChange: (e) => v.setDraft(e.target.value),
        }),
        h(antd.Select, {
          value: v.region, onChange: v.setRegion, style: { minWidth: 160 },
          disabled: v.regions.status === 'error',
          options: [{ value: '__all__', label: 'All regions' }]
            .concat(v.regionChoices.map((r) => ({ value: r, label: r }))),
        })),
      v.summary.status === 'error'
        ? h(antd.Alert, { type: 'error', message: v.summary.error })
        : h(antd.Space, null,
          h(antd.Statistic, { title: 'Orders', value: v.total, loading: v.total === null }),
          h(antd.Statistic, { title: 'Amount', value: v.amount, precision: 2, loading: v.amount === null })),
      v.detail.status === 'error'
        ? h(antd.Alert, { type: 'error', message: v.detail.error })
        : h(antd.Table, {
          rowKey: 'order_id', columns, dataSource: v.rows, pagination: false,
          loading: v.detail.status === 'loading' || v.detail.refreshing,
          locale: { emptyText: 'No orders match this search.' },
        }),
      h(antd.Pagination, {
        current: v.page + 1, pageSize: PAGE_SIZE, total: v.total || 0, showSizeChanger: false,
        onChange: (next) => v.setPage(next - 1),
      }));
  }

  window.app.useOrdersView = useOrdersView;
  window.app.OrdersScreen = OrdersScreen;
})();
