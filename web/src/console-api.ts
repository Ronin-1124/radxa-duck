import { request } from './management-api'
import { isBrainStatus, isIdentityConfig, object, type BrainAction, type BrainStatus, type IdentityProfile } from './console-protocol'
export const consoleApi = {
  brain: (signal?: AbortSignal) => request('/api/brain/status', isBrainStatus, { signal, maxBytes: 65536 }),
  identity: (signal?: AbortSignal) => request('/api/identity', isIdentityConfig, { signal, maxBytes: 32768 }),
  saveIdentity: (revision: number, profile: IdentityProfile, signal?: AbortSignal) => request('/api/identity', isIdentityConfig,
    { method: 'PUT', body: { expected_revision: revision, profile }, signal, maxBytes: 32768 }),
  control: (status: BrainStatus, action: BrainAction, requestId: string, signal?: AbortSignal) => request('/api/brain/control',
    (v): v is { schema_version: 1; request_id: string; status: BrainStatus } => object(v) && v.schema_version === 1 && v.request_id === requestId && isBrainStatus(v.status),
    { method: 'POST', body: { request_id: requestId, action, expected_instance_id: status.instance_id,
      expected_revision: status.revision, ...(action === 'cancel_task' ? { task_id: status.task?.id } : {}) }, signal, maxBytes: 65536 }),
}
