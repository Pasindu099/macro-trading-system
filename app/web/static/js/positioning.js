(() => {
  const charts = new Map();
  const color = name => getComputedStyle(document.querySelector('.pos18')).getPropertyValue(name).trim();
  const palette = () => ({ ink: color('--pos-ink'), helper: color('--pos-ink-2'), grid: color('--line'),
    surface: color('--surface'), on: color('--pos-on'), against: color('--pos-against') });
  function base() {
    const c = palette();
    return { animation: false, textStyle: { color: c.helper },
      tooltip: { trigger: 'axis', backgroundColor: c.surface, borderColor: c.grid, textStyle: { color: c.ink } },
      legend: { type: 'scroll', textStyle: { color: c.helper } },
      grid: { left: 12, right: 15, top: 40, bottom: 36, containLabel: true },
      xAxis: { type: 'time', axisLine: { lineStyle: { color: c.grid } } },
      yAxis: [{ type: 'value', scale: true, splitLine: { lineStyle: { color: c.grid } } }],
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 13, bottom: 1 }] };
  }
  function make(node) {
    if (!node || !window.echarts) return null;
    charts.get(node)?.dispose();
    const chart = echarts.init(node); charts.set(node, chart); return chart;
  }
  function position(panel) {
    const script = panel.querySelector('[data-position-points]');
    if (!script) return;
    const data = JSON.parse(script.textContent), main = make(panel.querySelector('[data-position-chart]'));
    const priceNode = panel.querySelector('[data-market-chart]'), market = make(priceNode);
    const checked = name => panel.querySelector(`[data-series="${name}"]`)?.checked;
    const line = (name,rows,key,extra={}) => ({ name,type:'line',showSymbol:false,
      data:rows.map(r=>[r.report_date,r[key]]),...extra });
    function render() {
      const c = palette(), series = [], options = base();
      options.yAxis[0].name = data.units === 'pct_oi' ? '% of open interest' : 'US dollars ($bn)';
      if (checked('net')) data.groups.forEach((g,i) => series.push(line(g.name,g.rows,'net',{
        lineStyle:{color:i ? c.helper : c.ink,type:i ? 'dashed':'solid'},itemStyle:{color:i ? c.helper : c.ink} })));
      if (checked('long')) series.push(line('Bets on',data.rows,'long',{areaStyle:{opacity:.12},itemStyle:{color:c.on}}));
      if (checked('short')) series.push(line('Bets against',data.rows,'short',{areaStyle:{opacity:.12},itemStyle:{color:c.against}}));
      if (checked('bands')) for (const [key,label] of [['p20','Bottom 20%'],['p80','Top 20%']])
        series.push(line(label,data.rows,key,{lineStyle:{type:'dotted',color:c.helper},itemStyle:{color:c.helper}}));
      if (checked('oi')) {
        options.yAxis.push({type:'value',name:'Open contracts',position:'right',scale:true,splitLine:{show:false}});
        series.push(line('Open interest',data.rows,'open_interest',{yAxisIndex:1,lineStyle:{type:'dashed',color:c.helper},itemStyle:{color:c.helper}}));
      }
      if (data.units === 'pct_oi' && checked('price')) {
        const index = options.yAxis.length;
        options.yAxis.push({type:'value',name:'Price',position:'right',offset:checked('oi') ? 70 : 0,scale:true,splitLine:{show:false}});
        series.push(line('Price',data.rows,'price',{yAxisIndex:index,lineStyle:{color:c.helper},itemStyle:{color:c.helper}}));
      }
      if (options.yAxis.length > 2) options.grid.right = 65;
      main?.setOption({...options,series},{notMerge:true});
      priceNode.hidden = data.units === 'pct_oi' || !checked('price');
      if (!priceNode.hidden) {
        market?.resize();
        market?.setOption({...base(),legend:{show:false},grid:{left:12,right:15,top:14,bottom:25,containLabel:true},
          dataZoom:[{type:'inside'}],series:[line('Price',data.rows,'price',{lineStyle:{color:c.helper},itemStyle:{color:c.helper}})]},{notMerge:true});
      }
    }
    panel.querySelectorAll('[data-series]').forEach(input=>input.addEventListener('change',render)); render();
  }
  function episodes(panel) {
    const script = panel.querySelector('[data-episode-points]'); if (!script) return;
    const rows = JSON.parse(script.textContent), c = palette(), chart = make(panel.querySelector('[data-episode-chart]'));
    chart?.setOption({...base(),legend:{show:false},yAxis:{type:'value',name:'8W price move (%)',splitLine:{lineStyle:{color:c.grid}}},
      series:[{name:'Past episode',type:'scatter',symbolSize:11,
        data:rows.map(r=>({value:[r.date,r.move_8w_pct],itemStyle:{color:r.move_8w_pct >= 0 ? c.on:c.against},
          label:{show:true,formatter:r.arrow,position:r.move_8w_pct >= 0 ? 'top':'bottom'}}))}]});
  }
  function init(root=document) {
    const panels = root.matches?.('.pos-panel') ? [root] : root.querySelectorAll('.pos-panel');
    panels.forEach(panel=>{
      if (panel.dataset.posInitialized === 'true') return;
      if (!panel.querySelector('.takeaway')) return;
      panel.dataset.posInitialized='true'; position(panel); episodes(panel);
    });
  }
  document.addEventListener('htmx:afterSwap',event=>init(event.detail.target.closest('.pos-panel') || event.detail.target));
  document.addEventListener('DOMContentLoaded',()=>init());
  document.addEventListener('click',event=>{
    const button = event.target.closest('[data-view]'); if (!button) return;
    const panel=button.closest('.pos-panel'), mode=button.dataset.view;
    panel.querySelectorAll('[data-pos-view]').forEach(node=>node.hidden=node.dataset.posView !== mode);
    panel.querySelectorAll('[data-view]').forEach(node=>node.setAttribute('aria-pressed',String(node===button)));
    const url=new URL(location.href); url.searchParams.set('view',mode); history.replaceState(null,'',url);
  });
  window.addEventListener('resize',()=>charts.forEach((chart,node)=>{if(node.isConnected&&!node.hidden)chart.resize();}));
})();
