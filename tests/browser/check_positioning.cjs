const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path');
(async()=>{
  const base=process.env.POSITIONING_BASE_URL||'http://127.0.0.1:8018';
  const output=path.resolve('data/screenshots/step18-positioning');fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
  const results=[],examples=[];
  async function loaded(page){
    for(const id of ['summary','crowding','flows','squeeze','price','extremes','retail']){
      const panel=page.locator('#pos-'+id);await panel.scrollIntoViewIfNeeded();
      await panel.locator('.takeaway').waitFor({state:'attached',timeout:90000});
    }
    if(await page.locator('.state-error').count())throw Error('A panel failed');
    await page.waitForFunction(()=>!!window.echarts?.getInstanceByDom(document.querySelector('[data-position-chart]')));
  }
  async function explanations(page,on){
    const menu=page.getByRole('button',{name:'Menu',exact:true}),mobile=await menu.isVisible();
    if(mobile&&await menu.getAttribute('aria-expanded')!=='true')await menu.click();
    const button=page.locator('[data-explanations-toggle]');
    if((await button.getAttribute('aria-pressed')==='true')!==on)await button.click();
    if(mobile&&await menu.getAttribute('aria-expanded')==='true')await menu.click();
  }
  try{
    for(const width of [1440,390]){
      const page=await browser.newPage({viewport:{width,height:1000},hasTouch:true}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.goto(base+'/positioning');await loaded(page);
      for(const on of [true,false]){
        await explanations(page,on);await page.evaluate(()=>window.scrollTo(0,0));
        const visible=await page.locator('.takeaway:visible').count();
        if(visible!==(on?7:0))throw Error(`Explanations visibility: ${visible}`);
        const dimensions=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
          charts:document.querySelectorAll('.pos18-chart canvas').length,
          tables:[...document.querySelectorAll('.pos18-table-frame')].filter(n=>n.scrollWidth>n.clientWidth).length}));
        if(dimensions.scrollWidth>width+1)throw Error(`Page overflow ${dimensions.scrollWidth}/${width}`);
        if(dimensions.charts<2)throw Error('Charts missing');
        await page.screenshot({path:path.join(output,`${width}-${on?'on':'off'}.png`),fullPage:true,animations:'disabled'});
        await page.screenshot({path:path.join(output,`${width}-${on?'on':'off'}-viewport.png`),animations:'disabled'});
        results.push({...dimensions,explanations:on?'on':'off',visibleTakeaways:visible});
      }
      await page.reload();await loaded(page);
      if(await page.locator('.takeaway:visible').count())throw Error('Off cookie lost on reload');
      await explanations(page,true);
      const cardPanel=page.locator('#pos-crowding');await cardPanel.getByRole('button',{name:'Bar view',exact:true}).click();
      if(await page.locator('.pos18-bar-card:visible').count()!==8)throw Error('Bar view missing currencies');
      await cardPanel.getByText('Show all trader groups',{exact:true}).click();
      if(!(await cardPanel.getByRole('columnheader',{name:'Dealers',exact:true}).isVisible()))throw Error('Dealer detail missing');
      await cardPanel.getByRole('button',{name:'Simple view',exact:true}).click();
      const price=page.locator('#pos-price');
      await price.locator('select[name=instrument]').selectOption('EUR/JPY');
      await price.locator('select[name=units]').selectOption('pct_oi');
      await price.getByRole('button',{name:'Apply selection',exact:true}).click();await loaded(page);
      if(!(await page.locator('#pos-price .pos18-notice').innerText()).includes('two futures markets'))throw Error('Cross fallback missing');
      await price.locator('select[name=instrument]').selectOption('USD/JPY');
      await price.locator('select[name=group]').selectOption('legacy_noncommercial');
      await price.locator('select[name=range_key]').selectOption('1Y');
      await price.getByRole('button',{name:'Apply selection',exact:true}).click();await loaded(page);
      for(const key of ['long','short','oi','bands'])await price.locator(`[data-series=${key}]`).check();
      let names=await page.evaluate(()=>echarts.getInstanceByDom(document.querySelector('[data-position-chart]')).getOption().series.map(s=>s.name));
      if(!['Bets on','Bets against','Open interest','Bottom 20%','Top 20%','Price'].every(n=>names.includes(n)))throw Error('Series toggles failed');
      await price.locator('[data-series=price]').uncheck();
      names=await page.evaluate(()=>echarts.getInstanceByDom(document.querySelector('[data-position-chart]')).getOption().series.map(s=>s.name));
      if(names.includes('Price'))throw Error('Price toggle failed');
      await page.goto(base+'/positioning');await loaded(page);await explanations(page,true);
      const definition=page.locator('.takeaway .glossary-term summary').last();await definition.focus();await page.keyboard.press('Enter');
      await page.waitForFunction(()=>{const notes=[...document.querySelectorAll('.glossary-term[open] .glossary-definition')];return notes.length&&notes.every(n=>{const r=n.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1;});});
      await definition.click();
      if(width===1440)for(const id of ['crowding','flows','price']){
        const text=await page.locator(`#pos-${id} .takeaway-part`).nth(1).locator('.takeaway-sentence').evaluate(n=>{
          const copy=n.cloneNode(true);copy.querySelectorAll('.glossary-definition').forEach(d=>d.remove());return copy.textContent.replace(/\s+/g,' ').trim();});
        examples.push({panel:id,text,sentence:text.match(/.*?[.!?](?=\s|$)/)?.[0]||text});
      }
      if(errors.length)throw Error(errors.join('; '));await page.close();
    }
  }finally{await browser.close();}
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({results,examples},null,2));
  console.log(JSON.stringify({states:results.length,widths:[1440,390],overflow:0,cookies:'passed',controls:'passed',glossary:'passed',examples}));
})().catch(e=>{console.error(e.message);process.exit(1);});
