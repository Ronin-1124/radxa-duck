import { expect, test, type Page, type Route } from '@playwright/test'
import { brainStatus, mockConsole, sendFrame } from './console-fixture'
async function openSettings(page: Page, tab?: string) {
  await page.getByRole('button', { name: '设置', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '设置', exact: true })
  await expect(dialog).toBeVisible()
  if (tab) await dialog.getByRole('button', { name: tab, exact: true }).click()
  return dialog
}
test('two views observe a resident brain without owning its lifecycle', async ({ page }) => {
  const io = await mockConsole(page)
  await page.goto('/overview')
  await expect(page.locator('.robot-phase')).toHaveText('自在待命')
  await expect(page.locator('.device-identity strong')).toHaveText('Radxa ZERO 3W')
  await expect(page.locator('.capability-strip')).toContainText('正在收音')
  await expect(page.getByRole('navigation', { name: '主视图' }).getByRole('link')).toHaveCount(2)
  expect(io.events).toHaveLength(1)
  io.publish({ activity: 'listening' })
  await expect(page.locator('.robot-phase')).toHaveText('正在听你说')
  await page.getByRole('link', { name: '视角', exact: true }).click()
  await expect(page.getByRole('heading', { name: '小鸭的视角' })).toBeVisible()
  await expect.poll(() => io.observers.length).toBe(1)
  expect(io.observers[0]!.commands).toEqual([])
  await page.getByRole('link', { name: '状态', exact: true }).click()
  await expect(page.locator('.robot-phase')).toHaveText('正在听你说')
  await expect.poll(() => io.observers[0]!.closed).toBe(true)
  expect(io.events[0]!.closed).toBe(false)
  expect([...io.requests, ...io.management.requests].every(r => r.method === 'GET')).toBe(true)
  await page.reload()
  await expect(page.locator('.robot-phase')).toHaveText('正在听你说')
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})
test('missing resident endpoints preserve real metrics without inventing brain state', async ({ page }) => {
  const io = await mockConsole(page, { missing: true })
  await page.goto('/overview')
  await expect(page.locator('.web-connection')).toHaveText('网页已连接')
  await expect(page.locator('.robot-phase')).toHaveText('等待大脑接入')
  await expect(page.locator('.capability-item strong').filter({ hasText: '状态未知' })).toHaveCount(4)
  await expect(page.getByRole('button', { name: '暂停交互', exact: true })).toHaveCount(0)
  await expect(page.locator('.device-metric')).toContainText(['23.4%', '424 MB', '48.2°C', '1 天 1 小时'])
  await page.getByRole('link', { name: '视角', exact: true }).click()
  await expect(page.locator('.camera-placeholder')).toContainText('实时视角待接入')
  expect(io.observers).toHaveLength(0)
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})
test('disconnect invalidates activity without claiming the robot stopped', async ({ page }) => {
  const io = await mockConsole(page)
  await page.goto('/overview');io.publish({ activity: 'speaking' })
  await expect(page.locator('.robot-phase')).toHaveText('正在回答')
  io.setOffline(true)
  await page.route('**/api/device/status', route => route.abort())
  io.events[0]!.socket.close()
  await expect(page.locator('.robot-phase')).toHaveText('状态未知')
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(page.locator('.web-connection')).toHaveText('网页已断连')
  await expect(page.locator('.console-state-notice')).toContainText('机器人运行状态未知')
  await expect(page.locator('.device-metrics')).toContainText('设备指标为上次读数')
  await expect(page.locator('.presence-panel')).not.toContainText('大脑未运行')
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})
test('older observations and previous brain instances cannot replace current state', async ({ page }) => {
  const io = await mockConsole(page)
  await page.goto('/overview');io.publish({ activity: 'thinking' })
  await expect(page.locator('.robot-phase')).toHaveText('正在思考')
  io.events[0]!.socket.send(JSON.stringify({ ...brainStatus, revision: 0, activity: 'speaking' }))
  await expect(page.locator('.robot-phase')).toHaveText('正在思考')
  Object.assign(io.brain, { instance_id: 'brain-2', revision: 0, state: 'paused' })
  await expect(page.locator('.robot-phase')).toHaveText('交互已暂停')
  await expect.poll(() => io.events.length).toBe(2)
  expect(io.events[0]!.closed).toBe(true)
  io.events[1]!.socket.send(JSON.stringify({ ...brainStatus, revision: 100, activity: 'speaking' }))
  await expect(page.locator('.robot-phase')).toHaveText('交互已暂停')
})
test('control waits for authoritative feedback and sends one request', async ({ page }) => {
  let held: Route | undefined
  const io = await mockConsole(page, { handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/brain/control') return false
    held = route;return true
  } })
  await page.goto('/overview')
  const pause = page.getByRole('button', { name: '暂停交互', exact: true })
  await expect(pause).toBeEnabled();await pause.click()
  await expect(page.locator('.brain-controls .ui-button')).toBeDisabled()
  await expect(page.locator('.robot-phase')).toHaveText('自在待命')
  const command = io.requests.find(r => r.method === 'POST')!
  expect(command.headers['x-radxa-duck-request']).toBe('1')
  expect(command.body).toMatchObject({ action: 'pause', expected_instance_id: 'brain-1', expected_revision: 1 })
  expect(command.body!.request_id).toMatch(/^[a-f0-9]{32}$/)
  io.publish({ state: 'paused', controls: { ...io.brain.controls, pause: false, resume: true } })
  await held!.fulfill({ json: { schema_version: 1, request_id: command.body!.request_id, status: io.brain } })
  await expect(page.getByRole('button', { name: '恢复交互', exact: true })).toBeEnabled()
  expect(io.requests.filter(r => r.method === 'POST')).toHaveLength(1)
})
test('identity saves to the board, applies next turn and guards unsaved changes', async ({ page }) => {
  const io = await mockConsole(page, { handle: async route => {
    if (route.request().method() !== 'PUT') return false
    const body = route.request().postDataJSON();expect(body.expected_revision).toBe(1)
    await route.fulfill({ json: { schema_version: 1, type: 'identity_config', revision: 2, active_revision: 1, profile: body.profile } });return true
  } })
  await page.goto('/overview');const dialog = await openSettings(page)
  await expect(dialog.getByLabel('名字', { exact: true })).toHaveValue('小鸭')
  await dialog.getByLabel('名字', { exact: true }).fill('豆豆')
  await dialog.getByRole('button', { name: '关闭设置', exact: true }).click()
  await expect(dialog.getByText('放弃未保存的更改？', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '继续编辑', exact: true }).click()
  await dialog.getByRole('button', { name: '保存身份', exact: true }).click()
  await expect(dialog.locator('.identity-saved')).toContainText('下一轮交互生效')
  expect(io.requests.filter(r => r.method === 'PUT')).toHaveLength(1)
  await dialog.getByRole('button', { name: '关闭设置', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('button', { name: '设置', exact: true })).toBeFocused()
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })) ).not.toContain('豆豆')
})
test('identity conflict retains the draft and allows safe recovery', async ({ page }) => {
  await mockConsole(page, { handle: async route => {
    if (route.request().method() !== 'PUT') return false
    await route.fulfill({ status: 409, json: { error: { code: 'conflict' } } });return true
  } })
  await page.goto('/overview');const dialog = await openSettings(page)
  await dialog.getByLabel('名字', { exact: true }).fill('草稿')
  await dialog.getByRole('button', { name: '保存身份', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('身份已被修改')
  await expect(dialog.getByLabel('名字', { exact: true })).toHaveValue('草稿')
  await dialog.getByRole('button', { name: '放弃修改', exact: true }).click()
  await dialog.getByRole('button', { name: '重新读取', exact: true }).click()
  await expect(dialog.getByRole('alert')).toHaveCount(0)
})
test('cloud configuration and lazy device controls share one modal', async ({ page }) => {
  const io = await mockConsole(page)
  await page.goto('/overview');const dialog = await openSettings(page, '大脑')
  await expect(dialog.getByLabel('模型名称', { exact: true })).toHaveValue('test-model')
  await dialog.getByRole('radio', { name: '替换密钥', exact: true }).check()
  await dialog.getByLabel('API 密钥', { exact: true }).fill('test-only-secret')
  await dialog.getByRole('button', { name: '设备', exact: true }).click()
  await expect(dialog.locator('.device-model')).toHaveText('Radxa ZERO 3W')
  expect(io.management.requests.some(r => r.path === '/api/audio')).toBe(false)
  await dialog.getByRole('button', { name: '音频设备', exact: true }).click()
  await expect(dialog.locator('.audio-panel')).toContainText('Pollen HAT')
  await dialog.getByRole('button', { name: '大脑', exact: true }).click()
  await expect(dialog.getByLabel('API 密钥', { exact: true })).toHaveValue('test-only-secret')
  await dialog.getByRole('button', { name: '关闭设置', exact: true }).click()
  await dialog.getByRole('button', { name: '放弃并关闭', exact: true }).click()
  await openSettings(page, '大脑')
  await dialog.getByRole('radio', { name: '替换密钥', exact: true }).check()
  await expect(dialog.getByLabel('API 密钥', { exact: true })).toHaveValue('')
  expect([...io.requests, ...io.management.requests].every(r => r.method === 'GET')).toBe(true)
})
for (const format of [
  { width: 320, height: 900, locale: 'zh-CN', theme: 'light' },
  { width: 375, height: 812, locale: 'en', theme: 'dark' },
  { width: 844, height: 390, locale: 'zh-CN', theme: 'dark' },
  { width: 1440, height: 1000, locale: 'en', theme: 'light' },
]) test(`console fits ${format.width}×${format.height} ${format.locale}/${format.theme}`, async ({ page }) => {
  await page.setViewportSize({ width: format.width, height: format.height })
  await page.addInitScript(({locale,theme})=>{localStorage.setItem('radxa-duck.ui.locale',locale);localStorage.setItem('radxa-duck.ui.theme',theme)},format)
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));const io=await mockConsole(page)
  for(const path of ['/overview','/vision','/overview?settings=1','/overview?settings=1&tab=brain','/overview?settings=1&tab=device']){
    await page.goto(path);await expect(page.locator('.console-header')).toBeAttached()
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  }
  expect(errors).toEqual([])
  expect([...io.requests,...io.management.requests].every(r=>r.method==='GET')).toBe(true)
})
test('bottom preference menus open above the dock and respect reduced motion', async ({ page }) => {
  await mockConsole(page);await page.goto('/overview')
  await page.getByRole('combobox',{name:/^语言:/}).click()
  const panel=await page.locator('.preference-panel').boundingBox(),dock=await page.locator('.utility-dock').boundingBox()
  expect(panel!.y+panel!.height).toBeLessThan(dock!.y)
  await page.getByRole('option',{name:'English',exact:true}).click()
  await expect(page.locator('html')).toHaveAttribute('lang','en')
  await expect(page.locator('.robot-phase')).toHaveText('At ease')
  await page.getByRole('combobox',{name:/^Appearance:/}).click()
  await page.getByRole('option',{name:'Dark',exact:true}).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark')
  await expect(page.locator('.presence-orb')).toHaveCSS('animation-name','none')
})
test('observer paints and acknowledges frames without requesting capture', async ({ page }) => {
  const io=await mockConsole(page,{cameraActive:true});await page.goto('/vision')
  await expect.poll(()=>io.observers.length).toBe(1);const observer=io.observers[0]!
  expect(observer.commands).toEqual([]);await sendFrame(page,observer)
  await expect(page.locator('.camera-image')).toBeVisible()
  await expect.poll(()=>observer.commands.filter(c=>c.type==='ack').length).toBe(1)
  expect(observer.commands[0]).toMatchObject({type:'ack',subscription_id:'observer-1',frame_id:1,displayed:true})
  expect(await page.locator('.camera-image').evaluate(img=>(img as HTMLImageElement).complete)).toBe(true)
  await page.getByRole('button',{name:'画面详情',exact:true}).click()
  await expect(page.locator('.object-list')).toContainText('test object')
  await page.getByRole('button',{name:'目标标注',exact:true}).click()
  await expect(page.locator('.detection-overlay rect')).toHaveCount(1)
  await page.getByRole('link',{name:'状态',exact:true}).click()
  await expect.poll(()=>observer.closed).toBe(true)
  expect(observer.commands.map(c=>c.type)).toEqual(['ack']);expect(io.requests.every(r=>r.method==='GET')).toBe(true)
})
test('temporary preview releases only its lease and leaves resident capture observable', async ({ page }) => {
  const camera={cameraActive:false};const io=await mockConsole(page,camera);await page.goto('/vision')
  await page.getByRole('button',{name:'临时采集',exact:true}).click();const observer=io.observers[0]!
  await expect(page.getByRole('button',{name:'结束临时采集',exact:true})).toBeEnabled()
  await sendFrame(page,observer);await expect(page.locator('.camera-image')).toBeVisible()
  camera.cameraActive=true
  await page.getByRole('button',{name:'结束临时采集',exact:true}).click()
  await expect(page.locator('.observer-label')).toHaveText('仅观察')
  await expect(page.locator('.camera-status')).toHaveText('实时画面')
  expect(observer.commands.filter(c=>String(c.type).startsWith('preview.')).map(c=>c.type)).toEqual(['preview.start','preview.stop'])
  await page.getByRole('link',{name:'状态',exact:true}).click();expect(io.requests.every(r=>r.method==='GET')).toBe(true)
})
test('view exit cancels unfinished decoding and revokes every image URL', async ({ page }) => {
  await page.addInitScript(()=>{
    const created:string[]=[],revoked:string[]=[];const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL)
    URL.createObjectURL=(blob:Blob)=>{const url=create(blob);created.push(url);return url}
    URL.revokeObjectURL=(url:string)=>{revoked.push(url);revoke(url)}
    Object.assign(window,{observerUrls:{created,revoked}})
    HTMLImageElement.prototype.decode=()=>new Promise<void>(()=>{})
  })
  const io=await mockConsole(page,{cameraActive:true});await page.goto('/vision')
  await expect.poll(()=>io.observers.length).toBe(1);await sendFrame(page,io.observers[0]!)
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {observerUrls:{created:string[]}}).observerUrls.created.length)).toBe(1)
  await page.getByRole('link',{name:'状态',exact:true}).click();await expect.poll(()=>io.observers[0]!.closed).toBe(true)
  const urls=await page.evaluate(()=>(window as unknown as {observerUrls:{created:string[];revoked:string[]}}).observerUrls)
  expect(urls.revoked).toEqual(urls.created);expect(io.observers[0]!.commands).toEqual([])
})
test('invalid binary pairing closes only the observer', async ({ page }) => {
  const io=await mockConsole(page,{cameraActive:true});await page.goto('/vision')
  await expect.poll(()=>io.observers.length).toBe(1);io.observers[0]!.socket.send(Buffer.from([255,216,255,217]))
  await expect.poll(()=>io.observers[0]!.closed).toBe(true)
  await expect(page.getByRole('status').filter({hasText:'画面数据暂时无法识别'})).toBeVisible()
  await page.getByRole('link',{name:'状态',exact:true}).click();await expect(page.locator('.robot-phase')).toHaveText('自在待命')
  expect(io.events[0]!.closed).toBe(false)
})

test('slow telemetry does not delay live interaction state', async ({ page }) => {
  const io=await mockConsole(page)
  let held:Route|undefined
  await page.route('**/api/device/status',route=>{held=route})
  await page.goto('/overview')
  await expect(page.locator('.robot-phase')).toHaveText('自在待命')
  io.publish({activity:'thinking'})
  await expect(page.locator('.robot-phase')).toHaveText('正在思考')
  await expect(page.locator('.web-connection')).toHaveText('网页已连接')
  await held!.abort()
  await expect(page.locator('.robot-phase')).toHaveText('正在思考')
})

test('uncertain controls cannot automatically repeat or claim completion', async ({ page }) => {
  const io=await mockConsole(page,{handle:async route=>{
    if(new URL(route.request().url()).pathname!=='/api/brain/control')return false
    await route.abort();return true
  }})
  await page.goto('/overview')
  await page.getByRole('button',{name:'暂停交互',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('操作结果未确认')
  await expect(page.getByRole('button',{name:'暂停交互',exact:true})).toBeDisabled()
  await expect(page.locator('.robot-phase')).toHaveText('自在待命')
  await page.getByRole('button',{name:'重新读取',exact:true}).click()
  await expect(page.getByRole('button',{name:'暂停交互',exact:true})).toBeEnabled()
  expect(io.requests.filter(r=>r.method==='POST')).toHaveLength(1)
})

test('task cancellation targets a task and waits for actual cancellation', async ({ page }) => {
  const io=await mockConsole(page,{handle:async route=>{
    if(new URL(route.request().url()).pathname!=='/api/brain/control')return false
    const body=route.request().postDataJSON()
    expect(body).toMatchObject({action:'cancel_task',task_id:'motion-task-1'})
    io.publish({task:{id:'motion-task-1',kind:'motion',state:'cancelling',summary:'动作任务'},controls:{...io.brain.controls,cancel_task:false}})
    await route.fulfill({json:{schema_version:1,request_id:body.request_id,status:io.brain}});return true
  }})
  await page.goto('/overview')
  io.publish({activity:'acting',task:{id:'motion-task-1',kind:'motion',state:'running',summary:'动作任务'},controls:{...io.brain.controls,cancel_task:true}})
  await page.getByRole('button',{name:'停止当前任务',exact:true}).click()
  await expect(page.locator('.task-ribbon')).toContainText('正在停止')
  await expect(page.getByRole('button',{name:'停止当前任务',exact:true})).toHaveCount(0)
  io.publish({activity:'idle',task:{id:'motion-task-1',kind:'motion',state:'cancelled',summary:'动作任务'}})
  await expect(page.locator('.task-ribbon')).toContainText('已取消')
  await expect(page.locator('.robot-phase')).toHaveText('自在待命')
})

test('pagehide releases temporary preview and restored browsing only observes', async ({ page }) => {
  const io=await mockConsole(page)
  await page.goto('/vision')
  await page.getByRole('button',{name:'临时采集',exact:true}).click()
  await expect(page.getByRole('button',{name:'结束临时采集',exact:true})).toBeEnabled()
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})))
  await expect.poll(()=>io.observers[0]!.closed).toBe(true)
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})))
  await expect.poll(()=>io.observers.length).toBe(2)
  expect(io.observers[0]!.commands.map(c=>c.type)).toEqual(['preview.start'])
  expect(io.observers[1]!.commands).toEqual([])
  await expect(page.getByRole('button',{name:'临时采集',exact:true})).toBeEnabled()
  expect(io.requests.every(r=>r.method==='GET')).toBe(true)
})

test('a missing JPEG times out without acknowledging or blocking the robot', async ({ page }) => {
  await page.clock.install()
  const io=await mockConsole(page,{cameraActive:true})
  await page.goto('/vision');await expect.poll(()=>io.observers.length).toBe(1)
  const observer=io.observers[0]!
  observer.socket.send(JSON.stringify({schema_version:1,type:'frame',subscription_id:observer.status.subscription_id,frame_id:1,width:1,height:1,jpeg_bytes:4,detections:[]}))
  // Run a browser task so that the incoming message starts its deadline first.
  await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)))
  await page.clock.runFor(5100)
  await expect.poll(()=>observer.closed).toBe(true)
  expect(observer.commands).toEqual([])
  expect(io.events[0]!.closed).toBe(false)
})

test('a failed JPEG decode drops one frame and a later frame recovers', async ({ page }) => {
  const io=await mockConsole(page,{cameraActive:true})
  await page.goto('/vision');await expect.poll(()=>io.observers.length).toBe(1)
  const observer=io.observers[0]!
  observer.socket.send(JSON.stringify({schema_version:1,type:'frame',subscription_id:observer.status.subscription_id,frame_id:1,width:1,height:1,jpeg_bytes:4,detections:[]}))
  observer.socket.send(Buffer.from([255,216,255,217]))
  await expect.poll(()=>observer.commands.length).toBe(1)
  expect(observer.commands[0]).toMatchObject({type:'ack',frame_id:1,displayed:false})
  await sendFrame(page,observer,2)
  await expect(page.locator('.camera-image')).toBeVisible()
  await expect.poll(()=>observer.commands.length).toBe(2)
  expect(observer.commands[1]).toMatchObject({type:'ack',frame_id:2,displayed:true})
  await expect(page.locator('.ui-alert')).toHaveCount(0)
})

test('the appearance menu stays inside a 320px viewport', async ({ page }) => {
  await page.setViewportSize({width:320,height:800});await mockConsole(page)
  await page.goto('/overview')
  await page.getByRole('combobox',{name:/^外观:/}).click()
  const rect=await page.locator('.preference-panel').boundingBox()
  expect(rect!.x).toBeGreaterThanOrEqual(16)
  expect(rect!.x+rect!.width).toBeLessThanOrEqual(304)
  await page.keyboard.press('ArrowUp');await page.keyboard.press('Enter')
  await expect(page.getByRole('combobox',{name:/^外观:/})).toHaveAttribute('aria-expanded','false')
})

test('stopping temporary preview invalidates a decode that finishes later', async ({ page }) => {
  await page.addInitScript(()=>{
    const decode=HTMLImageElement.prototype.decode
    HTMLImageElement.prototype.decode=function(){return decode.call(this).then(()=>new Promise<void>(resolve=>{Object.assign(window,{finishObserverDecode:resolve})}))}
  })
  const io=await mockConsole(page);await page.goto('/vision')
  await page.getByRole('button',{name:'临时采集',exact:true}).click()
  const observer=io.observers[0]!
  await sendFrame(page,observer)
  await expect.poll(()=>page.evaluate(()=>typeof (window as unknown as {finishObserverDecode:unknown}).finishObserverDecode)).toBe('function')
  await page.getByRole('button',{name:'结束临时采集',exact:true}).click()
  await expect(page.locator('.camera-placeholder')).toContainText('摄像头空闲')
  await page.evaluate(()=>(window as unknown as {finishObserverDecode:()=>void}).finishObserverDecode())
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
  await expect(page.locator('.camera-image')).toHaveCount(0)
  await expect(page.locator('.ui-alert')).toHaveCount(0)
  expect(observer.closed).toBe(false)
  expect(observer.commands.map(c=>c.type)).toEqual(['preview.start','preview.stop'])
})

test('metadata and JPEG must be adjacent messages', async ({ page }) => {
  const io=await mockConsole(page,{cameraActive:true});await page.goto('/vision')
  await expect.poll(()=>io.observers.length).toBe(1);const observer=io.observers[0]!
  observer.socket.send(JSON.stringify({schema_version:1,type:'frame',subscription_id:observer.status.subscription_id,frame_id:1,width:1,height:1,jpeg_bytes:4,detections:[]}))
  observer.publish()
  await expect.poll(()=>observer.closed).toBe(true)
  expect(observer.commands).toEqual([])
})

test('language and theme switches inside settings preserve identity and secret drafts', async ({ page }) => {
  await mockConsole(page);await page.goto('/overview')
  await openSettings(page);const dialog=page.locator('dialog.console-settings')
  await dialog.getByLabel('名字',{exact:true}).fill('豆豆')
  await dialog.getByRole('button',{name:'大脑',exact:true}).click()
  await dialog.getByRole('radio',{name:'替换密钥',exact:true}).check()
  await dialog.getByLabel('API 密钥',{exact:true}).fill('test-only-secret')
  await dialog.getByRole('combobox',{name:/^语言:/}).click()
  await page.getByRole('option',{name:'English',exact:true}).click()
  await dialog.getByRole('combobox',{name:/^Appearance:/}).click()
  await page.getByRole('option',{name:'Dark',exact:true}).click()
  await expect(page.locator('html')).toHaveAttribute('lang','en')
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark')
  await expect(dialog.getByLabel('API key',{exact:true})).toHaveValue('test-only-secret')
  await dialog.getByRole('button',{name:'Identity',exact:true}).click()
  await expect(dialog.getByLabel('Name',{exact:true})).toHaveValue('豆豆')
  await dialog.getByRole('button',{name:'Close settings',exact:true}).click()
  await dialog.getByRole('button',{name:'Discard and close',exact:true}).click()
  await expect(page.getByRole('combobox',{name:/^Language:/})).toHaveAttribute('aria-expanded','false')
  expect(await page.evaluate(() => ({ ...localStorage }))).toEqual({ 'radxa-duck.ui.locale': 'en', 'radxa-duck.ui.theme': 'dark' })
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
})
