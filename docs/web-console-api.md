# 常驻机器人管理网页接口

本契约对应管理网页的「状态 / 视角」主视图和「角色身份 / 大脑 / 设备」设置。新增接口待后端实现；现有设备、网络、音频、云端及维护接口继续复用。类型和校验位于 `web/src/console-protocol.ts`，请求适配位于 `web/src/console-api.ts`。

## 生命周期与权限

- 板端大脑独立运行。打开网页、切换主视图、刷新、隐藏和关闭浏览器均不得启停大脑，也不得改变机器人能力的启用策略。
- 大脑状态订阅只读。画面订阅只读，不因查看画面而启动摄像头。临时预览单独申请租约，断连只释放本订阅的临时租约；其他大脑/应用消费者仍按自己的策略使用摄像头。
- 新版网页不再依赖 `/api/speech/live`、`/api/vision/live`、`/api/apps/live` 验证协议。后端可独立保留这些能力验证接口；如保留，大脑占用能力时应返回 `busy`，不能抢占常驻运行。
- HTTP JSON 均返回 `Content-Type: application/json` 和 `Cache-Control: no-store`；错误统一为 `{"error":{"code":"conflict"}}` 等有限公开错误码，不回传密钥、原始 Prompt 或供应商错误原文。沿用同源和 WebSocket Origin 校验。HTTP 写入携带 `X-Radxa-Duck-Request: 1`。

## 大脑状态

`GET /api/brain/status` 返回完整快照：

```json
{
  "schema_version": 1,
  "type": "brain_status",
  "instance_id": "brain-boot-unique-id",
  "revision": 12,
  "sampled_at": "2026-10-28T02:00:00Z",
  "state": "running",
  "activity": "listening",
  "events_available": true,
  "identity": {"name": "小鸭", "revision": 2, "active_revision": 2},
  "microphone": {"available": true, "enabled": true, "active": true},
  "camera": {"available": true, "enabled": true, "active": false},
  "cloud": {"configured": true, "model": "configured-model-id", "health": "ready"},
  "motion": {"available": false, "state": "unavailable"},
  "task": null,
  "controls": {
    "pause": true,
    "resume": false,
    "enable_microphone": false,
    "disable_microphone": true,
    "cancel_task": false
  }
}
```

- `state`：`starting | running | paused | stopped | error`。`activity`：`idle | listening | thinking | speaking | acting`。活动表示实际执行阶段，不用网页连接状态代替。
- 传感器 `available` 表示硬件/软件能力，`enabled` 表示运行策略，`active` 表示此刻实际采集。云端 `health` 为 `ready | error | unknown`；配置存在不代表连接已验证。运动状态为 `ready | busy | unavailable | error`，未接入真实运控时不能声明就绪。
- `task` 为 `null` 或 `{"id":"task-id","kind":"voice","state":"running","summary":"当前任务的简短摘要"}`。状态为 `running | cancelling | succeeded | failed | cancelled`。只需当前/最近一项，摘要最长 1000 字符，不提供长期会话历史。动作完成、取消必须由运控确认。
- `revision` 是同一实例的单调状态版本；重启更换 `instance_id`。相同版本必须代表相同状态，采样时间可以更新。ID 使用 1–128 个字母、数字、`_ . : -`。
- `controls` 表示当前允许的操作。未验证硬件、权限不足、操作处理中均应声明不可用，网页据此显示控件。

当 `events_available: true`，网页连接 `WS /api/brain/events`。服务端连接后发送上述完整快照，变化时推送，空闲时建议每 5 秒发送一次。网页不发任何启动/控制消息；当前还每 2 秒读取快照以恢复状态，每约 10 秒刷新设备指标。快照上限 64 KiB；不支持事件时可先返回 `events_available: false`。

## 大脑控制

`POST /api/brain/control`：

```json
{
  "request_id": "32-hex-random-request-id",
  "action": "pause",
  "expected_instance_id": "brain-boot-unique-id",
  "expected_revision": 12
}
```

| action | 作用 |
|---|---|
| `pause` | 暂停接收新的交互；不自动取消正在回答或执行的任务 |
| `resume` | 恢复交互接收；不擅自打开用户已关闭的麦克风 |
| `disable_microphone` | 关闭机器人麦克风采集，不只是隐藏文字 |
| `enable_microphone` | 允许麦克风按板端策略工作；不隐式恢复已暂停的交互 |
| `cancel_task` | 停止当前回答/播放/动作任务；必须附带目标 `task_id`，不能误取消后续新任务 |

返回 `{"schema_version":1,"request_id":"原请求 ID","status":完整 brain_status}`。快照必须反映已接收操作后的真实状态；取消可先进入 `cancelling`，并关闭 `cancel_task` 控制权限，最终通过快照报告 `cancelled` 或失败。收到命令不等于物理动作已停止。

按 `request_id` 去重，重复请求返回同一操作结果，不重复执行。先查去重记录，再校验实例、版本和目标任务；状态不兼容返回 HTTP 409 `conflict`，不得操作新实例/新任务。HTTP 响应在 8 秒内返回，长任务用后续快照推进。网页遇到超时只显示结果未确认，不自动重发；重新读取状态后才允许用户再次操作。

## 角色身份

`GET /api/identity` 与成功的 `PUT /api/identity` 返回：

```json
{
  "schema_version": 1,
  "type": "identity_config",
  "revision": 2,
  "active_revision": 1,
  "profile": {
    "name": "小鸭",
    "description": "角色介绍",
    "personality": "性格",
    "expression_style": "表达风格"
  }
}
```

写入为 `{"expected_revision":2,"profile":{...}}`。名字必填且最长 40 字符；介绍最长 2000、性格/表达风格各最长 1000 字符。允许多行普通文字，拒绝控制字符，按纯文本处理。配置只维护一份，原子覆盖写入；`revision` 是并发版本号，不是多建配置文件。旧版本写入返回 409 `conflict`，不覆盖他人修改。

身份由板端统一用于语音、文字、视觉交互，下一轮交互生效，正在执行的任务继续使用原配置。保存返回的 `active_revision` 不得提前宣称新身份已用于当前任务；生效后通过大脑快照更新。首次未配置也应返回可编辑的默认档案；接口缺失时网页不伪造保存结果，也不把角色存入浏览器。

## 实时视角与临时预览

`GET /api/vision/observer/status`：

```json
{"schema_version":1,"type":"vision_observer_info","available":true,"max_jpeg_bytes":262144}
```

`max_jpeg_bytes` 为单帧限制，范围 4–2097152。网页仅在能力可用时连接 `WS /api/vision/observe`。连接本身是只读订阅，不发送 `start`。服务端立即发送：

```json
{
  "schema_version": 1,
  "type": "observer_status",
  "subscription_id": "observer-unique-id",
  "revision": 1,
  "camera_active": false,
  "preview_owned": false,
  "can_start_preview": true
}
```

- `camera_active` 是真实采集状态，与订阅数无关。无帧可发时保持空闲，不伪造图像。
- 每条连接分配独立 `subscription_id`，初始 `preview_owned` 为 `false`；相同订阅内状态版本单调递增。
- 用户显式点击临时预览时，网页发送 `{"type":"preview.start","request_id":"随机 ID"}`。关闭时发送 `{"type":"preview.stop","request_id":"随机 ID"}`。服务端按连接和请求 ID 去重，在状态消息带回对应 `request_id`，确认租约变更；失败发 `{"type":"error","code":"busy","request_id":"原 ID"}`。不能抢占大脑。15 秒未确认，网页关闭订阅并显示未确认。
- 离开视角、隐藏页面或断连时释放本连接的预览租约。重新连接只恢复观察，不恢复临时预览。最后一个使用者释放采集，才能关闭物理摄像头。

每帧严格按相邻的 JSON 元数据 + 二进制 JPEG 两条消息发送，不在这两条之间插入状态消息：

```json
{
  "schema_version": 1,
  "type": "frame",
  "subscription_id": "observer-unique-id",
  "frame_id": 123,
  "width": 640,
  "height": 480,
  "jpeg_bytes": 24000,
  "detections": [{"label":"person","score":0.92,"box_xyxy":[100,80,300,450]}]
}
```

`frame_id` 在订阅内严格递增，坐标对应 JPEG 实际像素和方向。无检测为 `[]`；不依赖固定模型、NPU 框架或麦克风声道。尺寸每边不超过 4096、总像素不超过 8388608；检测最多 256 项，标签最长 128 字符，置信度 0–1，坐标须落在画面内。元数据最多 64 KiB，JPEG 字节数必须精确匹配。

网页完成解码并给浏览器绘制机会后发送 `{"type":"ack","subscription_id":"observer-unique-id","frame_id":123,"displayed":true}`。解码失败可回复 `displayed:false` 以丢弃该帧。每个订阅最多一帧等待 ACK，只保留最新待发帧，不排队积压；慢客户端不能阻塞采集或其他消费者。摄像头变为空闲时，服务端应丢弃该订阅尚未确认的帧；网页会取消相应的解码与 ACK。服务端应清理超时订阅；网页在元数据后 5 秒未收到 JPEG、或 8 秒内未完成解码/展示时释放连接。连续 5 秒无新图像会标记画面过期并隐藏检测标注。

网页退出会关闭订阅、撤销 Object URL、取消尚未完成的解码和 ACK。任何这些操作都不能暂停大脑或关闭其他消费者使用中的视觉能力。

## 继续复用的管理接口

`/api/device/status`、`/api/network/*`、`/api/audio`、`/api/cloud/*`、`/api/power*`、`/api/update/status`、`/api/tasks*` 保持现有协议。云端配置继续更新同一份文件，密钥不回传；常驻大脑应从下一轮交互读取新配置，不打断当前任务。设备型号显示层将板端的 `Radxa ZERO 3` 规范为 `Radxa ZERO 3W`，不修改板端主机名和设备树。
