window.SW = window.SW || {};

(function () {
  const { createElement: h, useEffect, useRef } = React;

  SW.Chart = function Chart({ options, height = 280 }) {
    const ref = useRef(null);
    const chart = useRef(null);

    useEffect(() => {
      // Highcharts comes off a CDN. When it is unreachable the chart is the
      // thing that should be missing, not the whole workspace.
      if (!ref.current || typeof Highcharts === 'undefined') return undefined;
      chart.current = Highcharts.chart(ref.current, {
        ...options,
        chart: { ...(options.chart || {}), height },
      });
      return () => chart.current && chart.current.destroy();
    }, [JSON.stringify(options), height]);

    if (typeof Highcharts === 'undefined') {
      return h(
        'div',
        { className: 'sw-chart is-unavailable', style: { height } },
        'Charts need the Highcharts CDN, which is not reachable right now.'
      );
    }

    return h('div', { className: 'sw-chart', ref });
  };

  SW.ChartCard = function ChartCard({ title, subtitle, options, height, extra }) {
    return h(
      'div',
      { className: 'sw-chart-card' },
      h(
        'div',
        { className: 'sw-chart-card-head' },
        h(
          'div',
          { style: { display: 'flex', alignItems: 'baseline', gap: 8 } },
          h('div', { className: 'sw-chart-card-title' }, title),
          extra
        ),
        subtitle && h('div', { className: 'sw-chart-card-sub' }, subtitle)
      ),
      h(SW.Chart, { options, height })
    );
  };

  SW.chartPresets = {
    stackedArea: (series, categories, unit) => ({
      chart: { type: 'areaspline' },
      xAxis: {
        categories: categories.map((d) => SW.util.shortDate(d)),
        tickInterval: Math.max(1, Math.ceil(categories.length / 7)),
      },
      yAxis: { labels: { format: unit === 'cost' ? '${value}' : '{value}' } },
      plotOptions: {
        areaspline: { stacking: 'normal', marker: { enabled: false }, fillOpacity: 0.7, lineWidth: 1 },
      },
      tooltip: {
        shared: true,
        valuePrefix: unit === 'cost' ? '$' : '',
        valueDecimals: unit === 'cost' ? 2 : 0,
      },
      series,
    }),

    lines: (series, categories) => ({
      chart: { type: 'line' },
      xAxis: {
        categories: categories.map((d) => SW.util.shortDate(d)),
        tickInterval: Math.max(1, Math.ceil(categories.length / 7)),
      },
      plotOptions: { line: { marker: { enabled: false }, lineWidth: 2 } },
      tooltip: { shared: true },
      series,
    }),

    horizontalBar: (data, unit) => ({
      chart: { type: 'bar' },
      xAxis: { categories: data.map((d) => d.name) },
      yAxis: { labels: { format: unit === 'cost' ? '${value}' : '{value}' } },
      plotOptions: { bar: { colorByPoint: true, borderRadius: 2 } },
      legend: { enabled: false },
      tooltip: { valuePrefix: unit === 'cost' ? '$' : '', valueDecimals: unit === 'cost' ? 2 : 0 },
      series: [{ name: unit === 'cost' ? 'Cost' : 'Value', data: data.map((d) => d.value) }],
    }),

    groupedColumn: (categories, series, unit) => ({
      chart: { type: 'column' },
      xAxis: { categories },
      yAxis: { labels: { format: unit === 'cost' ? '${value}' : '{value}' } },
      plotOptions: { column: { borderRadius: 2 } },
      tooltip: { shared: true },
      series,
    }),

    donut: (data) => ({
      chart: { type: 'pie' },
      plotOptions: {
        pie: {
          innerSize: '62%',
          borderWidth: 2,
          borderColor: '#FFFFFF',
          dataLabels: {
            enabled: true,
            format: '{point.name}<br/><span style="font-weight:400;color:#8F8FA3">{point.percentage:.0f}%</span>',
            style: { fontSize: '11px', fontWeight: '600', textOutline: 'none', color: '#2E2E38' },
            distance: 12,
          },
        },
      },
      legend: { enabled: false },
      series: [{ name: 'Viewers', data: data.map((d) => ({ name: d.name, y: d.value })) }],
    }),

    funnelColumn: (data) => ({
      chart: { type: 'column' },
      xAxis: { categories: data.map((d) => d.stage) },
      yAxis: { labels: { format: '{value}' } },
      legend: { enabled: false },
      plotOptions: {
        column: {
          borderRadius: 2,
          colorByPoint: true,
          dataLabels: {
            enabled: true,
            style: { fontSize: '11px', fontWeight: '600', textOutline: 'none', color: '#65657B' },
          },
        },
      },
      series: [{ name: 'Sessions', data: data.map((d) => d.value) }],
    }),
  };
})();
