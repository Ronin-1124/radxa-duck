import { expect, test } from '@playwright/test'
import { cloudConfig, deviceStatus, mockManagement, task } from './management-fixture'

test('management reads reject malformed metrics and any returned cloud key without exposing response text', async ({ page }) => {
  const mock = await mockManagement(page)
  await page.goto('/overview')
  const check = async (method: 'device' | 'cloud') => page.evaluate(async method => {
    const path = '/src/management-api.ts'
    const { managementApi } = await import(/* @vite-ignore */ path)
    try { await managementApi[method](); return 'accepted' } catch (error) { return String(error) }
  }, method)
  mock.reads['/api/device/status'] = { ...deviceStatus, cpu: { ...deviceStatus.cpu, usage_percent: 101 } }
  expect(await check('device')).toBe('ManagementApiError: invalid_response')
  mock.reads['/api/device/status'] = { ...deviceStatus, memory: { total_bytes: 100, used_bytes: 101, available_bytes: 0 } }
  expect(await check('device')).toBe('ManagementApiError: invalid_response')
  mock.reads['/api/cloud/config'] = { ...cloudConfig, api_key: 'should-never-be-returned' }
  expect(await check('cloud')).toBe('ManagementApiError: invalid_response')
  mock.reads['/api/device/status'] = { ...deviceStatus, cpu: { ...deviceStatus.cpu, usage_percent: null, sample_seconds: null } }
  expect(await check('device')).toBe('accepted')
})

test('task writes carry the required header, use a LAN-compatible ID, and never replay an uncertain request', async ({ page }) => {
  const submitted = task('cloud.test')
  let accept = false
  const mock = await mockManagement(page, { handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/cloud/test') return false
    if (!accept) await route.abort()
    else await route.fulfill({ status: 202, json: { ...submitted, request_id: route.request().postDataJSON().request_id } })
    return true
  } })
  await page.goto('/overview')
  const invoke = () => page.evaluate(async () => {
    const path = '/src/management-api.ts'
    const { managementApi, createRequestId } = await import(/* @vite-ignore */ path)
    const id = createRequestId()
    try { const task = await managementApi.testCloud(id, 2); return { id, task, error: null } }
    catch (error) { return { id, task: null, error: String(error) } }
  })
  const uncertain = await invoke()
  expect(uncertain.id).toMatch(/^[a-f0-9]{32}$/)
  expect(uncertain.error).toBe('ManagementApiError: network_error')
  const writes = () => mock.requests.filter(request => request.method !== 'GET')
  expect(writes()).toHaveLength(1)
  expect(writes()[0]!.headers['x-radxa-duck-request']).toBe('1')
  expect(writes()[0]!.headers['content-type']).toBe('application/json')
  expect(writes()[0]!.body).toEqual({ request_id: uncertain.id, expected_revision: 2 })
  accept = true
  const accepted = await invoke()
  expect(accepted.task?.task_id).toBe(submitted.task_id)
  expect(writes()).toHaveLength(2)
})

test('HTTP failures expose a bounded error code and ignore server messages', async ({ page }) => {
  await mockManagement(page, { handle: async route => {
    await route.fulfill({ status: 403, json: { schema_version: 1, error: { code: 'permission_denied', message: 'secret provider response' } } })
    return true
  } })
  await page.goto('/overview')
  const error = await page.evaluate(async () => {
    const path = '/src/management-api.ts'
    const { managementApi } = await import(/* @vite-ignore */ path)
    try { await managementApi.audio(); return null } catch (error) { return String(error) }
  })
  expect(error).toBe('ManagementApiError: permission_denied')
})
