# 我会永远陪着你

`astrbot_plugin_private_companion` 是一个面向 AstrBot 私聊场景的主动陪伴插件。

它的目标不是让 bot 高频刷存在感,而是让 bot 拥有一套连续的生活状态：会起床、会做事、会发呆,会记得昨天发生过什么,也会在合适的时候轻轻发来一句话。

插件围绕几个核心层工作：

- 拟人状态：睡眠、梦境、情绪、体力、天气、身体小状态。
- 当日日程：从起床到睡前的一天生活框架。
- 当前细化：临近每个时间段时,把粗日程展开成具体生活细节。
- 主动消息：根据日程、状态、关系、最近对话和能力可用性决定是否开口。
- 记忆残留：日记、梦境碎片、昨日完整对话摘要、重要日期和互动造成的日程偏移。

## 版本

当前版本：`1.1.0`

`1.1.0` 重点更新：

- 默认开启私聊陪伴,移除开启/关闭命令。
- 日程命令改为 `查看今日日程` / `重置日程`。
- 细化命令改为 `当前细化` / `重置细化`。
- 细化模型只生成当前时间段,并输出完整细化正文、状态变量、主动契机和 QQ 状态建议。
- 新增昨日完整对话摘要：生成今日日程前会先总结昨日/最近完整对话,再把身体、情绪、关系、约定、物件和梦境碎片残留纳入参考。
- 梦境改为完整故事格式：梦境类型、梦境因子/碎片、梦境内容、梦境余韵。
- 主动消息加强“站位”约束：主动开口时不会把历史最后一句误当成用户刚刚来找她。
- 主动能力引入检索式提示：文字、图片、窥屏、语音、戳一戳等能力先判断可用性和场景再使用。
- 主动图片加入安全阀：真实生成出图片即计入当日图片额度,即使发送失败也不会立刻反复生图。
- QQ 状态同步更保守：优先在线/睡觉/自定义短状态,减少忙碌,避免离开/隐身。

## 运行环境

- AstrBot `>=4.22.0`
- 主要面向私聊场景
- 推荐平台：`aiocqhttp`

可选依赖：

- ComfyUI 插件：用于主动 `photo_text` 生图。
- 屏幕陪伴插件：用于 `screen_peek` 观察屏幕。
- poke 插件：用于戳一戳。
- TTS provider：用于主动语音。

这些能力都是可选的。缺失时插件会跳过或回退为普通文本。

## 安装

把插件目录放入 AstrBot 插件目录,并确保目录名为：

```text
astrbot_plugin_private_companion
```

例如：

```text
C:\Users\你的用户名\.astrbot\data\plugins\astrbot_plugin_private_companion
```

重启 AstrBot 后,在配置页填写至少这些内容：

- `target_user_ids`
- `target_platform`
- `LLM_PROVIDER_ID`
- `max_daily_messages`
- `idle_minutes`
- `min_interval_minutes`
- `quiet_hours`

如果 `target_user_ids` 填了 QQ 号,插件启动时会自动为这些目标预热主动私聊,不需要发送开启命令。

## 快速开始

1. 配置 `target_user_ids` 和 `target_platform`。
2. 确认 `max_daily_messages` 不为 0。
3. 私聊发送 `陪伴 状态`,查看当前用户状态和下次候选主动时间。
4. 发送 `陪伴 生成状态`,生成今天的拟人状态。
5. 发送 `陪伴 重置日程`,重新生成今日日程。
6. 发送 `陪伴 当前细化`,查看当前时间段细化。
7. 发送 `陪伴 查看主动判定`,排查为什么现在能或不能主动发消息。

## 常用命令

### 状态与调试

- `陪伴 状态`：查看当前用户的主动陪伴状态、今日主动数、下次候选时间、关系画像概要。
- `陪伴 查看主动判定`：查看当前主动发送条件是否满足。
- `陪伴 重置插件`：清空插件状态并重新初始化。
- `陪伴 增添状态 <状态描述>[|持续小时]`：手动添加一个临时状态。
- `陪伴 能力列表`：查看当前可用主动能力。

### 日程与细化

- `陪伴 查看今日日程`：查看今天粗日程。
- `陪伴 重置日程`：重新生成今天粗日程。
- `陪伴 当前细化`：查看当前时间段的完整细化结果。
- `陪伴 重置细化`：重新生成当前时间段细化。

### 梦境与日记

- `陪伴 生成状态`：刷新今天拟人状态,包括睡眠、梦境和身体状态。
- `陪伴 梦境`：查看今天完整梦境。
- `陪伴 梦境碎片`：查看梦境碎片池。
- `陪伴 日记`：查看最近日记。
- `陪伴 生成日记`：生成或刷新今天日记。

### 提示词检查

- `陪伴 查看提示词 日程`
- `陪伴 查看提示词 细化`
- `陪伴 查看提示词 主动`
- `陪伴 查看提示词 回复注入`

### 重要日期

- `陪伴 日期列表`
- `陪伴 日期添加 <标题> <YYYY-MM-DD或MM-DD> [备注]`
- `陪伴 日期删除 <标题关键词>`

### 偏好与关系

- `陪伴 昵称 <称呼>`
- `陪伴 语气 温柔|活泼|工作`
- `陪伴 画像`
- `陪伴 可做事项`
- `陪伴 清空记忆`

### 测试

- `陪伴 完整测试`
- `陪伴 结束完整测试`
- `陪伴模拟唤醒 <想模拟的用户消息>`

## 日程生成

日程生成会输出一天的粗框架,从起床覆盖到入睡前。它会参考：

- 日程专用人设
- AstrBot 默认人格
- 日期语境
- 天气
- 拟人状态
- 最近日记
- 昨日完整对话摘要
- 重要日期
- 今日互动造成的日程偏移

昨日完整对话摘要不是简单复制聊天记录,而是先由模型提炼成可影响今日的残留：

- 身体、饮食、作息、运动、天气暴露
- 情绪刺激、安慰、争执、关系变化
- 未完成约定、收到或送出的东西
- 可能进入梦境的物件、颜色、气味、半句话

这些残留只作为参考。模型会按强度自然继承,不会为了制造剧情强行安排事故。

## 当前细化

细化模型只负责当前最新时间段,不重写全天日程。

细化结果包含：

- `summary`：当前段结束后的状态摘要。
- `state_variables`：状态机变量,如体力、情绪、作业进度、等待回复等。
- `presence_status`：QQ 状态表现建议。
- `today_events`：当前时间段内真正发生的细节。
- `proactive_events`：当前段中可能自然触发的主动契机。

细化通常在对应时间段开始前约 3 分钟生成,用于让主动消息和被动回复都更贴近“现在”。

## 梦境

`陪伴 梦境` 会按下面格式展示：

```text
梦境类型：
梦境因子/碎片：
梦境内容：
梦境余韵：
```

梦境内容可以荒诞、跳跃、没有现实逻辑,但必须是一个能读出来的梦。它会参考：

- 昨日完整对话摘要
- 日记和梦境碎片池
- 当日状态
- 天气
- 重要日期
- 日程残留

梦境余韵可能影响当天的情绪底色、体力和主动话题。

## 主动消息

主动消息不是简单定时器。插件会先判断：

- 是否到候选时间
- 是否超过每日上限
- 是否在免打扰时间
- 用户最近是否刚活跃
- 距离上次主动是否太近
- 当前日程和状态是否适合开口
- 当前主动能力是否可用

主动消息生成时会明确区分站位：这是 bot 主动开口,不是用户刚刚来找 bot。历史对话只作为关系和话题背景。

## 主动能力

当前能力包括：

- `message`：普通文字私聊。
- `photo_text`：生成图片并附一句自然文本。
- `screen_peek`：轻看屏幕,只作为上下文。
- `voice`：短语音。
- `poke`：戳一戳。
- `typing_status`：发送前短暂正在输入。
- `qq_presence`：在线、睡觉、自定义短状态。

能力使用遵循“先检索,再判断场景,最后执行”。模型不会直接猜工具名,也不会在聊天正文里暴露内部能力、工具调用或状态同步。

## 主动生图安全阀

`photo_text` 会真实调用生图后端,所以插件做了额外限制：

- `photo_action_max_daily` 控制每个用户每日主动生图次数。
- 真实生成出图片就会消耗额度。
- 即使后续发送失败,也不会立刻继续生图。
- 发送失败后会清空当前计划并延后 6-12 小时重新安排。

不建议把 `photo_action_max_daily` 设为 0,因为 0 表示不限制。

## QQ 状态表现

细化模型可以输出 `presence_status`。插件优先使用：

- `online`
- `custom`
- `sleep`
- `unchanged`

`busy` 会尽量减少使用；如果模型输出忙碌,执行层会优先转成自定义短状态。`away` 和 `invisible` 会被避免。

自定义状态需要 `custom_text`,例如：

- `写题中`
- `路上`
- `犯困中`
- `看剧中`

如果自定义状态文本为空,插件会跳过自定义同步,避免设置空白状态。

## 主要配置项

基础主动陪伴：

- `enabled`
- `target_user_ids`
- `target_platform`
- `default_enable_configured_targets`
- `max_daily_messages`
- `idle_minutes`
- `min_interval_minutes`
- `quiet_hours`

日程和状态：

- `enable_daily_plan`
- `daily_plan_time`
- `daily_plan_item_count`
- `schedule_persona_prompt`
- `schedule_worldview_prompt`
- `enable_humanized_states`
- `humanized_state_intensity`
- `enable_daily_diary`
- `daily_diary_time`
- `enable_detail_enhancement`
- `detail_enhancement_lead_minutes`

主动消息：

- `enable_llm_proactive_message`
- `proactive_prompt_template`
- `enable_llm_timer_scheduling`
- `proactive_reply_context_hours`
- `enable_proactive_decorating_hooks`
- `enable_precise_platform_send`

图片：

- `enable_photo_text_action`
- `photo_action_max_daily`
- `photo_generation_backend`
- `PHOTO_PROMPT_PROVIDER_ID`
- `COMFYUI_TEXT2IMG_WORKFLOW_NAME`
- `COMFYUI_SELFIE_WORKFLOW_NAME`
- `EXTERNAL_IMAGE_API_BASE_URL`
- `EXTERNAL_IMAGE_API_KEY`
- `EXTERNAL_IMAGE_API_MODEL`
- `external_image_api_size`

其他联动：

- `enable_screen_glance_action`
- `enable_poke_action`
- `enable_voice_action`
- `enable_qq_presence_sync`
- `enable_weather_context`

## 设计原则

- 不刷屏：主动消息受上限、间隔、免打扰和关系状态约束。
- 不表演：主动消息像正常聊天,不把状态写成舞台动作。
- 不硬编码剧情：日程、梦境和主动话题由摘要、状态和上下文自然推导。
- 不假装：图片、语音、窥屏等能力不可用时会跳过或回退。
- 不无感消耗：生图成功即计入额度,避免失败重试导致资源被悄悄跑空。

