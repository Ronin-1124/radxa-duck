import type { ObjectDetection } from './vision-types'

// Indices match the COCO class IDs used by vision.py.
const chineseLabels = [
  '人', '自行车', '汽车', '摩托车', '飞机', '公交车', '火车', '卡车', '船',
  '交通信号灯', '消防栓', '停止标志', '停车计时器', '长椅', '鸟', '猫',
  '狗', '马', '羊', '牛', '大象', '熊', '斑马', '长颈鹿', '背包',
  '雨伞', '手提包', '领带', '行李箱', '飞盘', '双板滑雪板', '单板滑雪板', '球',
  '风筝', '棒球棒', '棒球手套', '滑板', '冲浪板', '网球拍',
  '瓶子', '葡萄酒杯', '杯子', '叉子', '刀', '勺子', '碗', '香蕉', '苹果',
  '三明治', '橙子', '西兰花', '胡萝卜', '热狗', '披萨', '甜甜圈', '蛋糕', '椅子',
  '沙发', '盆栽', '床', '餐桌', '马桶', '电视', '笔记本电脑', '鼠标',
  '遥控器', '键盘', '手机', '微波炉', '烤箱', '烤面包机', '水槽',
  '冰箱', '书', '时钟', '花瓶', '剪刀', '泰迪熊', '吹风机', '牙刷',
] as const

export function detectionLabel(detection: ObjectDetection, locale: string): string {
  return locale.startsWith('zh') ? chineseLabels[detection.class_id] ?? detection.label : detection.label
}
