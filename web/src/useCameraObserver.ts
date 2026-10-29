import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { createRequestId, ManagementApiError, request } from './management-api'
import { isObserverFrame, isObserverStatus, object, type ObserverFrame, type ObserverStatus } from './console-protocol'
interface ObserverInfo { schema_version:1; type:'vision_observer_info'; available:boolean; max_jpeg_bytes:number }
function isInfo(v:unknown):v is ObserverInfo {return object(v)&&v.schema_version===1&&v.type==='vision_observer_info'&&typeof v.available==='boolean'&&typeof v.max_jpeg_bytes==='number'&&Number.isSafeInteger(v.max_jpeg_bytes)&&v.max_jpeg_bytes>=4&&v.max_jpeg_bytes<=2097152}
/** Subscriptions observe only. Only an explicit preview.start may acquire a temporary camera lease. */
export function useCameraObserver(getImage: () => HTMLImageElement | undefined) {
 const status=shallowRef<ObserverStatus|null>(null),frame=shallowRef<ObserverFrame|null>(null)
 const imageUrl=ref<string|null>(null),connection=ref<'connecting'|'online'|'offline'|'pending'>('connecting')
 const error=ref<string|null>(null),pending=ref<'start'|'stop'|null>(null),stale=ref(false)
 const available=ref(false)
 let socket:WebSocket|undefined,disposed=false,generation=0,imageGeneration=0,binaryReceived=false,lastFrame=-1,maxBytes=2097152
 let controller:AbortController|undefined,metadata:ObserverFrame|null=null,decoder:HTMLImageElement|undefined,candidateUrl:string|null=null
 let commandId:string|null=null,timer:ReturnType<typeof setTimeout>|undefined,decodeTimer:ReturnType<typeof setTimeout>|undefined,freshTimer:ReturnType<typeof setTimeout>|undefined,commandTimer:ReturnType<typeof setTimeout>|undefined
 let raf1:number|undefined,raf2:number|undefined
 const current=(s:WebSocket,g:number)=>!disposed&&socket===s&&generation===g
 function clearImage() {
  imageGeneration++;binaryReceived=false
  clearTimeout(decodeTimer);clearTimeout(freshTimer)
  if(raf1!==undefined)cancelAnimationFrame(raf1);if(raf2!==undefined)cancelAnimationFrame(raf2)
  raf1=raf2=undefined
  if(decoder){decoder.src='';decoder=undefined}
  if(candidateUrl)URL.revokeObjectURL(candidateUrl)
  if(imageUrl.value)URL.revokeObjectURL(imageUrl.value)
  candidateUrl=null;imageUrl.value=null;frame.value=null;metadata=null;stale.value=false
 }
 function close() {
  generation++;controller?.abort();clearTimeout(timer);clearTimeout(commandTimer)
  if(socket){const old=socket;socket=undefined;old.onopen=old.onmessage=old.onclose=old.onerror=null;old.close()}
  clearImage();status.value=null;pending.value=null;commandId=null;lastFrame=-1
 }
 function fail(code:string) {close();error.value=code;connection.value=code==='not_supported'?'pending':'offline'}
 function send(value:unknown) {if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify(value))}
 function acknowledge(meta:ObserverFrame,displayed:boolean) {send({type:'ack',subscription_id:meta.subscription_id,frame_id:meta.frame_id,displayed})}
 async function decode(data:ArrayBuffer,meta:ObserverFrame,s:WebSocket,g:number) {
  const imageEpoch=imageGeneration
  const valid=()=>current(s,g)&&imageEpoch===imageGeneration&&metadata===meta&&status.value?.camera_active===true
  const bytes=new Uint8Array(data)
  if(bytes.byteLength!==meta.jpeg_bytes||bytes[0]!==255||bytes[1]!==216||bytes[bytes.length-2]!==255||bytes[bytes.length-1]!==217){fail('invalid_response');return}
  const url=URL.createObjectURL(new Blob([data],{type:'image/jpeg'}));candidateUrl=url
  const image=new Image();decoder=image
  let waitingForPaint=false
  decodeTimer=setTimeout(()=>{if(valid())fail('decode_error')},8000)
  try {
   image.src=url;await image.decode()
   if(!valid())return
   if(image.naturalWidth!==meta.width||image.naturalHeight!==meta.height){fail('invalid_response');return}
   const previous=imageUrl.value
   imageUrl.value=url;frame.value=meta;candidateUrl=null;stale.value=false;error.value=null
   clearTimeout(freshTimer);freshTimer=setTimeout(()=>{stale.value=true},5000)
   await nextTick()
   if(previous)URL.revokeObjectURL(previous)
   if(!valid())return
   waitingForPaint=true
   const checkPaint = () => {
    raf1=undefined
    if(!valid()||imageUrl.value!==url||document.hidden)return
    const rendered=getImage()
    if(!rendered?.isConnected||!rendered.complete||rendered.naturalWidth!==meta.width||rendered.src!==url||rendered.getBoundingClientRect().width<=0){raf1=requestAnimationFrame(checkPaint);return}
    raf2=requestAnimationFrame(()=>{
     raf2=undefined
     if(valid()&&getImage()===rendered&&rendered.isConnected&&imageUrl.value===url&&!document.hidden){
      clearTimeout(decodeTimer);metadata=null;acknowledge(meta,true)
     }
    })
   }
   raf1=requestAnimationFrame(checkPaint)
  } catch {if(valid()){metadata=null;error.value='decode_error';acknowledge(meta,false)}}
  finally {if(current(s,g)&&imageEpoch===imageGeneration){if(!waitingForPaint)clearTimeout(decodeTimer);decoder=undefined;if(candidateUrl===url){URL.revokeObjectURL(url);candidateUrl=null}}}
 }
 async function connect() {
  close();if(disposed||document.hidden)return
  const g=generation;connection.value='connecting';error.value=null;available.value=false
  controller=new AbortController()
  try {
   const info=await request('/api/vision/observer/status',isInfo,{signal:controller.signal,maxBytes:8192})
   if(disposed||g!==generation)return
   available.value=info.available;maxBytes=info.max_jpeg_bytes
   if(!info.available){fail('not_supported');return}
   const s=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/vision/observe`);s.binaryType='arraybuffer';socket=s
   timer=setTimeout(()=>{if(current(s,g))fail('timeout')},8000)
   s.onmessage=event=>{
    if(!current(s,g))return
    if(typeof event.data==='string'){
     if(metadata&&!binaryReceived){fail('invalid_response');return}
     if(event.data.length>65536){fail('invalid_response');return}
     let value:unknown
     try{value=JSON.parse(event.data)}catch{fail('invalid_response');return}
     if(isObserverStatus(value)){
      if(status.value&&value.subscription_id!==status.value.subscription_id){fail('invalid_response');return}
      if(status.value&&value.revision<status.value.revision)return
      if(value.preview_owned&&!status.value?.preview_owned&&!(pending.value==='start'&&value.request_id===commandId)){fail('invalid_response');return}
      clearTimeout(timer);status.value=value;connection.value='online';error.value=null
      if(value.request_id===commandId){clearTimeout(commandTimer);pending.value=null;commandId=null}
      if(!value.camera_active)clearImage()
     }else if(isObserverFrame(value)){
      if(!status.value?.camera_active||metadata||value.subscription_id!==status.value.subscription_id||value.frame_id<=lastFrame||value.jpeg_bytes>maxBytes){fail('invalid_response');return}
      metadata=value;binaryReceived=false;lastFrame=value.frame_id
      decodeTimer=setTimeout(()=>{if(current(s,g))fail('timeout')},5000)
     }else if(object(value)&&value.type==='error'&&typeof value.code==='string'&&value.code.length<=64){
      error.value=['busy','permission_denied','unavailable'].includes(value.code)?value.code:'unavailable'
      if(value.request_id===commandId){clearTimeout(commandTimer);pending.value=null;commandId=null}
     }else fail('invalid_response')
    }else if(event.data instanceof ArrayBuffer){
     if(!metadata||binaryReceived||event.data.byteLength>maxBytes||decoder){fail('invalid_response');return}
     binaryReceived=true;clearTimeout(decodeTimer);void decode(event.data,metadata,s,g)
    }else fail('invalid_response')
   }
   s.onclose=()=>{if(current(s,g))fail('network_error')}
   s.onerror=()=>{if(current(s,g))fail('network_error')}
  }catch(e){if(!disposed&&g===generation)fail(e instanceof ManagementApiError&&e.status===404?'not_supported':e instanceof ManagementApiError?e.code:'network_error')}
 }
 function preview() {
  if(connection.value!=='online'||!status.value||pending.value||(!status.value.preview_owned&&(!status.value.can_start_preview||status.value.camera_active)))return
  const action=status.value.preview_owned?'stop':'start'
  pending.value=action;commandId=createRequestId();error.value=null
  send({type:`preview.${action}`,request_id:commandId})
  commandTimer=setTimeout(()=>{if(pending.value)fail('outcome_unknown')},15000)
 }
 function pagehide(){close();connection.value='offline'}
 function visibility(){if(document.hidden)pagehide();else void connect()}
 function pageshow(event:PageTransitionEvent){if(event.persisted&&!document.hidden)void connect()}
 onMounted(()=>{void connect();document.addEventListener('visibilitychange',visibility);window.addEventListener('pagehide',pagehide);window.addEventListener('pageshow',pageshow)})
 onBeforeUnmount(()=>{disposed=true;close();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('pagehide',pagehide);window.removeEventListener('pageshow',pageshow)})
 const observing=computed(()=>connection.value==='online'&&!!status.value?.camera_active)
 return {status,frame,imageUrl,connection,error,pending,stale,available,observing,connect,preview}
}
