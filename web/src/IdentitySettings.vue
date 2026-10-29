<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, RefreshCw, Save } from '@lucide/vue'
import { consoleApi } from './console-api'
import { ManagementApiError } from './management-api'
import type { IdentityConfig, IdentityProfile } from './console-protocol'
import { useRobotConsole } from './useRobotConsole'
import UiAlert from './UiAlert.vue'
const id=useId()
const { refresh, brain, fresh }=useRobotConsole()
const config=shallowRef<IdentityConfig|null>(null)
const draft=reactive<IdentityProfile>({name:'',description:'',personality:'',expression_style:''})
const loading=ref(false),saving=ref(false),saved=ref(false),error=ref<string|null>(null),submitted=ref(false),unknown=ref(false)
let disposed=false,controller:AbortController|undefined
const fields=['name','description','personality','expression_style'] as const
const limits={name:40,description:2000,personality:1000,expression_style:1000}
const { t }=useI18n({useScope:'local',messages:{
 'zh-CN':{name:'名字',description:'角色介绍',personality:'性格',expression_style:'表达风格',namePlaceholder:'小鸭',descriptionPlaceholder:'它是谁',personalityPlaceholder:'例如：好奇、温和，有一点幽默',expression_stylePlaceholder:'例如：简短自然，优先用中文回答',save:'保存身份',saving:'正在保存',read:'重新读取',loading:'正在读取',pending:'身份配置待接入',loadError:'暂时无法读取角色身份',saveError:'保存未完成，请重新读取后确认。',conflict:'身份已被修改，请重新读取后编辑。',unknown:'保存结果未确认，请重新读取配置。',required:'请填写名字。',invalid:'内容含有不支持的控制字符。',done:'已保存',next:'下一轮交互生效',active:'已生效',modified:'未保存更改',retry:'重试',discard:'放弃修改'},
 en:{name:'Name',description:'About',personality:'Personality',expression_style:'Expression style',namePlaceholder:'Duck',descriptionPlaceholder:'Who they are',personalityPlaceholder:'e.g. Curious, gentle, with a little humor',expression_stylePlaceholder:'e.g. Brief, natural replies in English',save:'Save identity',saving:'Saving',read:'Read again',loading:'Reading',pending:'Identity configuration pending',loadError:'Identity could not be read',saveError:'Save was not completed. Read the configuration to check.',conflict:'Identity changed elsewhere. Read it again before editing.',unknown:'Save outcome unconfirmed. Read the configuration again.',required:'Enter a name.',invalid:'Remove unsupported control characters.',done:'Saved',next:'Applies to the next interaction',active:'Active',modified:'Unsaved changes',retry:'Retry',discard:'Discard changes'}
}})
const changed=computed(()=>fields.some(f=>draft[f] !== (config.value?.profile[f] ?? '')))
const invalid=computed(()=>!draft.name.trim() ? 'required' : fields.some(f=>/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(draft[f])) ? 'invalid' : null)
const canSave=computed(()=>!!config.value && changed.value && !loading.value && !saving.value && !unknown.value && !error.value)
const active=computed(()=>!!config.value && (fresh.value && brain.value && brain.value.identity.revision >= config.value.revision ? brain.value.identity.active_revision : config.value.active_revision) === config.value.revision)
function apply(value:IdentityConfig) { config.value=value; Object.assign(draft,value.profile) }
function failure(e:unknown) { return e instanceof ManagementApiError ? e.status===404 ? 'pending' : e.code==='conflict' ? 'conflict' : 'loadError' : 'loadError' }
async function load() {
 if(loading.value||saving.value)return
 loading.value=true;controller=new AbortController()
 try { const value=await consoleApi.identity(controller.signal);if(!disposed){apply(value);error.value=null;unknown.value=false;saved.value=false} }
 catch(e){if(!disposed)error.value=failure(e)}
 finally{if(!disposed)loading.value=false}
}
async function save() {
 submitted.value=true
 if(!canSave.value||invalid.value||!config.value)return
 saving.value=true;saved.value=false;controller=new AbortController()
 try {const value=await consoleApi.saveIdentity(config.value.revision,{...draft,name:draft.name.trim()},controller.signal);if(!disposed){apply(value);saved.value=true;error.value=null;void refresh()}}
 catch(e){if(!disposed){const code=e instanceof ManagementApiError ? e.code : 'network_error';unknown.value=['network_error','timeout','invalid_response','cancelled'].includes(code);error.value=unknown.value?'unknown':code==='conflict'?'conflict':'saveError'}}
 finally{if(!disposed)saving.value=false}
}
function reset() {if(!saving.value){Object.assign(draft,config.value?.profile??{name:'',description:'',personality:'',expression_style:''});saved.value=false;submitted.value=false}}
onMounted(()=>void load())
onBeforeUnmount(()=>{disposed=true;controller?.abort();Object.assign(draft,{name:'',description:'',personality:'',expression_style:''})})
defineExpose({hasUnsavedChanges:changed,busy:saving})
</script>
<template>
 <form class="identity-settings" novalidate @submit.prevent="save">
  <div class="identity-toolbar"><span class="console-label" role="status">{{ loading ? t('loading') : changed ? t('modified') : config ? t(active?'active':'next') : t('pending') }}</span><button class="ui-icon-button" type="button" :aria-label="t('read')" :disabled="loading||saving||changed" @click="load"><RefreshCw :size="16" aria-hidden="true" /></button></div>
  <UiAlert v-if="error && error!=='pending'" :tone="error==='pending'?'info':'error'" :role="error==='pending'?'status':'alert'">{{ t(error) }}</UiAlert>
  <div v-for="field in fields" :key="field" class="console-field"><label :for="`${id}-${field}`">{{ t(field) }}</label><input v-if="field==='name'" :id="`${id}-${field}`" v-model="draft.name" :maxlength="limits.name" :placeholder="t('namePlaceholder')" :aria-invalid="submitted&&!!invalid" :aria-describedby="submitted&&invalid?`${id}-error`:undefined" :disabled="saving||loading" autocomplete="off" @input="saved=false" /><textarea v-else :id="`${id}-${field}`" v-model="draft[field]" :maxlength="limits[field]" :placeholder="t(`${field}Placeholder`)" :disabled="saving||loading" rows="2" @input="saved=false"></textarea></div>
  <p v-if="submitted&&invalid" :id="`${id}-error`" class="console-field-error" role="alert">{{ t(invalid) }}</p>
  <div class="identity-actions"><button class="ui-button ui-button--primary" type="submit" :disabled="!canSave" :aria-busy="saving"><Save :size="16" aria-hidden="true" />{{ t(saving?'saving':'save') }}</button><button v-if="changed" type="button" class="ui-button" :disabled="saving" @click="reset">{{ t('discard') }}</button><span v-if="saved" class="identity-saved" role="status"><Check :size="15" aria-hidden="true" />{{ t('done') }} · {{ t(active?'active':'next') }}</span></div>
 </form>
</template>
<style scoped>
.identity-settings { display:grid; gap:20px; }
.identity-settings textarea { min-height:80px; }
.identity-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:-8px; }
.identity-actions { display:flex; align-items:center; flex-wrap:wrap; gap:12px; padding-top:8px; }
.identity-saved { display:flex; align-items:center; gap:5px; color:var(--accent); font-size:12px; }
</style>
