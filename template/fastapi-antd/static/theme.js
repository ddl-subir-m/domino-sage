// The Domino theme, for Ant Design and for Highcharts. Sage's file: read it, do not edit it.
//
// Hand `sage.theme` to the `ConfigProvider` at the root of the app (static/app.js does) and every
// Ant Design component draws in Domino's colours and type. Highcharts is configured once here, so a
// chart needs only its own series and axes.
window.sage = window.sage || {};

// A missing icon name — or an icon bundle that did not load — answers an empty element rather than
// crashing the component that asked for it.
(function guardIcons() {
  const real = window.icons || {};
  const blank = () => null;
  window.icons = new Proxy(real, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (typeof prop === 'string' && /^[A-Z]/.test(prop)) return blank;
      return undefined;
    },
  });
})();

sage.theme = {
  token: {
    colorPrimary: '#543FDE',
    colorPrimaryHover: '#3B23D1',
    colorPrimaryActive: '#311EAE',
    colorText: '#2E2E38',
    colorTextSecondary: '#65657B',
    colorTextTertiary: '#8F8FA3',
    colorSuccess: '#28A464',
    colorWarning: '#CCB718',
    colorError: '#C20A29',
    colorInfo: '#0070CC',
    colorBgContainer: '#FFFFFF',
    colorBgLayout: '#FAFAFA',
    colorBorder: '#E0E0E0',
    fontFamily: 'Inter, Lato, Helvetica Neue, Helvetica, Arial, sans-serif',
    fontSize: 14,
    borderRadius: 4,
    borderRadiusLG: 8,
  },
  components: {
    Button: { primaryShadow: 'none', defaultShadow: 'none' },
    Table: { headerBg: '#FAFAFA', rowHoverBg: '#F5F5F5' },
  },
};

// Series colours, in order. Status colours (`--ok`, `--warn`, `--danger`) are not among them on
// purpose: a green bar reads as "this is good" rather than "this is revenue".
sage.accents = ['#543FDE', '#0070CC', '#28A464', '#CCB718',
                '#FF6543', '#E835A7', '#2EDCC4', '#A9734C'];

if (typeof Highcharts !== 'undefined') Highcharts.setOptions({
  colors: sage.accents,
  chart: {
    style: { fontFamily: 'Inter, Lato, Helvetica Neue, Arial, sans-serif' },
    backgroundColor: 'transparent',
    spacing: [8, 8, 8, 8],
  },
  title: { text: null },
  subtitle: { text: null },
  credits: { enabled: false },
  legend: {
    align: 'right',
    verticalAlign: 'top',
    itemStyle: { fontWeight: '400', fontSize: '12px', color: '#65657B' },
    symbolRadius: 2,
  },
  xAxis: {
    lineColor: '#E0E0E0',
    tickColor: '#E0E0E0',
    labels: { style: { color: '#8F8FA3', fontSize: '11px' } },
  },
  yAxis: {
    gridLineColor: '#F0F0F3',
    title: { text: null },
    labels: { style: { color: '#8F8FA3', fontSize: '11px' } },
  },
  tooltip: {
    backgroundColor: '#2E2E38',
    borderWidth: 0,
    borderRadius: 4,
    style: { color: '#FFFFFF', fontSize: '12px' },
    shadow: false,
  },
  plotOptions: { series: { animation: { duration: 250 } } },
});
