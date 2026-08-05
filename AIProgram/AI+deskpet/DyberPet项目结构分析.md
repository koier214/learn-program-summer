# DyberPet 项目结构完整分析

> 分析日期：2026-08-04
> 项目地址：[github.com/ChaozhongLiu/DyberPet](https://github.com/ChaozhongLiu/DyberPet)
> 当前版本：v0.6.7（代码内），README 标称 v0.8.5（LLM 模块未开源）
> 许可证：MIT

---

## 一、项目整体架构图

```
run_DyberPet.py                    ← 程序入口
    │
    └─ DyberPetApp (QApplication)
        ├── PetWidget              ← 核心：桌面宠物窗口
        │   ├── DP_HpBar           ← HP/饱食度进度条
        │   ├── BubbleManager      ← 气泡行为管理器
        │   ├── Animation_worker   ← 动画播放线程
        │   ├── Interaction_worker ← 交互处理线程
        │   └── Scheduler_worker   ← 定时任务调度线程
        ├── DPNote                 ← 通知系统
        ├── DPAccessory            ← 附属宠物/配件系统
        ├── ControlMainWindow      ← 系统设置面板（FluentWindow）
        └── DashboardMainWindow    ← 控制台面板（FluentWindow）
```

**架构模式**：桌面宠物窗口（PetWidget）是核心，设置面板和控制台是两个独立的 `FluentWindow`，三者通过 PySide6 信号槽进行通信。

---

## 二、完整目录树及文件说明

### 2.1 根目录

```
DyberPet/
├── run_DyberPet.py        ← ★ 程序入口，唯一启动文件
├── LICENSE                ← MIT 许可证
├── README.md              ← 中文说明文档
├── README_EN.md           ← 英文说明文档
├── langs.pro              ← Qt 国际化翻译项目文件
├── .gitignore
├── docs/                  ← 文档资源（GIF、截图、开发指南）
├── res/                   ← 静态资源（图标、角色、物品、音效、语言包）
└── DyberPet/              ← ★ 主代码包
```

---

### 2.2 程序入口：`run_DyberPet.py`

| 属性 | 说明 |
|------|------|
| **作用** | 唯一的程序入口文件，创建 QApplication 并启动所有子系统 |
| **核心类** | `DyberPetApp(QApplication)` |
| **依赖** | `tendo`（单实例锁） |

#### DyberPetApp 类结构

```python
class DyberPetApp(QApplication):
    # === 子系统对象 ===
    self.p       # PetWidget - 宠物主窗口
    self.note    # DPNote - 通知系统
    self.acc     # DPAccessory - 配件系统
    self.conp    # ControlMainWindow - 系统设置面板
    self.board   # DashboardMainWindow - 控制台面板

    # === 信号连接 ===
    # 约 40 条信号槽连接，将各子系统串联起来
```

#### 信号连接关系（对你开发最重要的部分）

```
PetWidget                   → 通知/配件/控制面板/控制台
  setup_notification        → 通知系统
  change_note               → 通知系统 + 设置面板 + 控制台
  show_dashboard            → 控制台
  show_controlPanel         → 设置面板
  hp_updated / fv_updated   → 控制台状态界面

Task/Timer 信号（★你加功能时要用的）:
  start_pomodoro / cancel_pomodoro  ← 控制台 → PetWidget
  start_focus / cancel_focus        ← 控制台 → PetWidget
  taskUI_Timer_update               → PetWidget → 控制台
  taskUI_task_end                   → PetWidget → 控制台
```

---

### 2.3 主包：`DyberPet/` — 核心模块

```
DyberPet/
├── __init__.py               ← 包标识（空文件）
├── settings.py               ← ★ 全局配置与常量定义
├── conf.py                   ← ★ 配置类（角色、动画、任务、物品、存档数据）
├── utils.py                  ← ★ 工具函数集合
├── DyberPet.py               ← ★★★ 核心：宠物主窗口 PetWidget（约 2200 行）
├── modules.py                ← ★★★ 动画/交互/调度三大工作线程
├── bubbleManager.py          ← ★ 气泡文本行为管理器
├── extra_windows.py          ← ★★ 额外弹窗（番茄钟、专注、提醒、物品栏、对话气泡）
├── Notification.py           ← 通知系统（约 1100 行）
├── Accessory.py              ← 附属宠物/配件系统（约 2200 行）
├── custom_widgets.py         ← 自定义 UI 组件（托盘、分隔线、进度条、对话气泡等）
├── custom_roundmenu.py       ← 自定义右键圆形菜单
├── Dashboard/                ← 控制台子模块（5 个面板）
├── DyberSettings/            ← 设置面板子模块（5 个面板 + 工具）
├── HideDock/                 ← 隐藏停靠模块
└── SelfStartup/              ← 开机自启模块
```

---

### 2.4 `DyberPet.py` — 宠物主窗口

> **最重要**：这是整个框架的心脏，你添加 LLM 聊天、事件簿、计时联动都直接与此文件交互。

| 属性 | 说明 |
|------|------|
| **文件大小** | ~2200 行，86KB |
| **核心类** | `PetWidget(QWidget)` |
| **依赖** | `PySide6`、`qfluentwidgets`、`pynput`、各子模块 |

#### PetWidget 关键组件

```python
class PetWidget(QWidget):
    # === 窗口属性 ===
    # 无边框、置顶、透明背景、可拖拽

    # === 内部组件 ===
    self.hpBar           # DP_HpBar - HP/饱食度进度条
    self.fvBar           # 好感度进度条
    self.status_indicator # 状态指示标签
    self.bubbleManger    # BubbleManager - 气泡行为管理

    # === 工作线程 ===
    self.anim_worker      # Animation_worker - 动画循环
    self.interact_worker  # Interaction_worker - 鼠标/键盘交互
    self.schedule_worker  # Scheduler_worker - 定时任务

    # === 关键信号（你加功能时会用） ===
    show_dashboard        # 打开控制台
    show_controlPanel     # 打开设置面板
    change_note           # 切换宠物时的通知
    hp_updated            # HP 更新
    fv_updated            # 好感度更新
    hptier_changed_main_note  # HP 层级变化
    taskUI_Timer_update   # 计时器 UI 更新
    taskUI_task_end       # 任务结束
    single_pomo_done      # 单次番茄完成
```

---

### 2.5 `modules.py` — 三大工作线程

> **次重要**：动画系统、行为决策、定时任务都在这里。

| 类 | 行数 | 功能 |
|---|------|------|
| `Animation_worker` | ~310 行 | 随机播放动画、处理动作队列、管理动画状态 |
| `Interaction_worker` | ~470 行 | 鼠标追踪、点击检测、拖拽、键盘事件 |
| `Scheduler_worker` | ~550 行 | 定时任务（HP 衰减、FV 变化、番茄钟、专注计时） |

#### Animation_worker 关键属性

```python
class Animation_worker(QObject):
    self.pet_conf           # 宠物配置对象
    self.current_status     # 当前 HP/FV 状态
    self.nonDefault_prob    # 非默认动画触发概率
    self.act_cmlt_prob      # 动画累积概率表

    # 关键方法
    def random_act()        # 随机选择并播放动画
    def _cal_status_type()  # 根据 HP/FV 计算状态层级
```

#### Interaction_worker 关键方法

```python
class Interaction_worker(QObject):
    # 处理事件类型：
    # - 鼠标移动（光标跟随）
    # - 左键点击（互动反馈）
    # - 右键菜单
    # - 拖拽移动
    # - 鼠标滚轮
```

#### Scheduler_worker 关键属性

```python
class Scheduler_worker(QObject):
    # 使用 APScheduler 进行定时任务调度
    self.hp_interval     # HP 衰减间隔
    self.fv_interval     # FV 变化间隔
    self.tomato_timer    # 番茄钟倒计时
    self.focus_timer     # 专注计时倒计时
```

---

### 2.6 `conf.py` — 配置类（数据模型层）

| 类 | 功能 |
|---|------|
| `PetConfig` | 宠物角色配置：尺寸、缩放、动作映射、随机动画列表、配件动画、物品偏好 |
| `Act` | 单个动作：帧序列、刷新率、尺寸 |
| `EmptyAct` | 空动作占位符 |
| `ActData` | 动画参数存档：每个动作的解锁状态、触发概率、HP/FV 条件 |
| `PetData` | 宠物运行数据：当前 HP、FV、金币、等级、经验值 |
| `TaskData` | 任务数据：每日目标、番茄钟设置、任务列表 |
| `ItemData` | 物品/背包数据：物品数量、获取记录 |

---

### 2.7 `utils.py` — 工具函数

> 约 300 行，包含文件读取、时间转换、图片处理、文本换行等通用工具。

| 关键函数 | 功能 |
|---------|------|
| `read_json()` | 读取 JSON 配置文件 |
| `text_wrap()` | 文本自动换行 |
| `SubPet_Manager` | 附属宠物管理类 |
| `get_file_time()` | 获取文件修改时间 |
| `rename_pet_action()` | 重命名宠物动作帧 |
| `source_path()` | 路径处理 |

---

### 2.8 `bubbleManager.py` — 气泡行为管理器

> **重要**：控制宠物头顶气泡的显示逻辑。

```python
class BubbleManager(QObject):
    # 管理的气泡行为类型：
    # 1. 好感度变化气泡（fv_lvlup / fv_drop）
    # 2. HP 相关气泡（hp_low / hp_zero）
    # 3. 喂食气泡（feed_done / feed_required）
    # 4. 抚摸气泡（pat_focus / pat_frequent / pat_random）

    # 气泡属性：
    # - icon: 表情图标
    # - message: 文本内容
    # - countdown: 倒计时
    # - start_audio / end_audio: 音效
```

---

### 2.9 `extra_windows.py` — 额外弹窗

> **关键**：番茄钟、专注计时、提醒、物品栏、对话气泡都在这里。

| 类 | 功能 | 意义 |
|---|------|------|
| `SettingUI` | 悬浮设置面板 | 右键→设置 |
| `Tomato` | **番茄钟弹窗** | 倒计时数字显示、开始/暂停/取消 |
| `Focus` | **专注计时弹窗** | 类似番茄钟但无周期性 |
| `Remindme` | 提醒弹窗 | 用户自定义提醒 |
| `Inventory_item` | 物品图标 | 可拖拽的单个物品 |
| `Inventory` | 物品栏窗口 | 物品网格布局 |
| `QToaster` | Toast 通知 | 临时弹出消息 |
| `DPDialogue` | **对话气泡窗口** | ★ 你的 LLM 对话可直接复用 |
| `DialogueButtom` | 对话按钮 | 对话中的交互按钮 |

#### DPDialogue 类（你加 LLM 对话的关键）

```python
class DPDialogue(QWidget):
    # 已有功能：
    # - 标题栏 + 关闭按钮
    # - 文本显示区域（支持分段消息）
    # - 消息文本切换
    # - 自动关闭计时器
    # - 位置跟随

    # 你需要扩展的：
    # - 增加 QTextEdit 作为聊天显示区
    # - 增加 QLineEdit 作为输入框
    # - 增加发送按钮
    # - 连接 DeepSeek API
```

---

### 2.10 `custom_widgets.py` — 自定义 UI 组件

| 类/组件 | 功能 |
|---------|------|
| `SystemTray` | 系统托盘图标 |
| `HorizontalSeparator` | 水平分割线 |
| `VerticalSeparator` | 垂直分割线 |
| `RoundBarBase` | 圆形进度条基类 |
| `LevelBadge` | 等级徽章 |
| `DPDialogue` | **对话气泡基组件**（被 extra_windows 和 Accessory 引用） |

---

### 2.11 `custom_roundmenu.py` — 自定义右键圆形菜单

| 类 | 功能 |
|---|------|
| `CustomMenuStyle` | 菜单样式代理 |
| `RoundMenu` | **圆形右键菜单**（宠物上右键弹出） |

---

### 2.12 `Notification.py` — 通知系统

> 约 1100 行，管理所有系统通知。

| 通知类型 | 说明 |
|---------|------|
| `system` | 系统通知，使用 DyberPet 图标 |
| `status_*` | HP/FV/金币变化通知 |
| `start/end/cancel_tomato` | 番茄钟开始/结束/取消 |
| `start/end/cancel_focus` | 专注计时开始/结束/取消 |
| `item` | 物品数量变化 |
| `feed_*` | 喂食通知 |
| `greeting_*` | 问好通知 |

---

### 2.13 `Accessory.py` — 配件/附属宠物系统

> 约 2200 行，管理可附加到主宠物上的额外组件。

| 功能 | 说明 |
|------|------|
| `DPAccessory` | 配件管理器：创建/删除/定位配件窗口 |
| `MouseMoveManager` | 鼠标移动管理器，用于配件的位置跟随 |
| 迷你宠物 | 附属小宠物，跟随主宠物移动 |

---

### 2.14 `HideDock/` — 隐藏停靠模块

| 文件 | 说明 |
|------|------|
| `HideDock.py` | 屏幕边缘吸附/隐藏功能实现 |
| `__init__.py` | 空文件 |

---

### 2.15 `SelfStartup/` — 开机自启模块

| 文件 | 说明 |
|------|------|
| `__init__.py` | Windows 开机自启动注册逻辑 |

---

## 三、Dashboard 控制台面板

```
Dashboard/
├── DashboardUI.py           ← ★ 控制台主窗口（FluentWindow，5 个子面板）
├── dashboard_widgets.py     ← ★★★ 所有面板的 UI 组件（约 3600 行，极其重要）
├── statusUI.py              ← 状态面板（HP/FV 状态、日志流、Buff 管理）
├── inventoryUI.py           ← 背包面板（物品网格、使用/丢弃）
├── shopUI.py                ← 商店面板（购买/出售）
├── taskUI.py                ← ★ 任务面板（每日任务 + 番茄钟 + 每日目标）
├── animationUI.py           ← 动画管理面板
├── animDesignUI.py          ← 动画设计器（自定义动画编辑器）
└── buffModule.py            ← Buff 系统（效果持续/定时触发）
```

### 3.1 `DashboardUI.py` — 控制台主窗口

```python
class DashboardMainWindow(FluentWindow):
    # 5 个子面板（左侧导航栏切换）：
    self.statusInterface      # 状态 (Status)
    self.backpackInterface    # 背包 (Backpack)
    self.shopInterface        # 商店 (Shop)
    self.taskInterface        # 每日任务 (Daily Tasks)
    self.animInterface        # 动画 (Animation)
```

### 3.2 `dashboard_widgets.py` — ★ 核心 UI 组件

> 最重要的文件之一，约 3600 行，包含所有面板的 UI 组件。

| 类 | 位置 | 功能 |
|---|------|------|
| `StatusCard` | ~前部 | 宠物状态卡片（HP/FV 槽、等级、头像） |
| `NoteFlowGroup` | ~中部 | 日志流组件 |
| `BuffCard` | ~中部 | Buff 效果卡片 |
| `InventoryGroup` | ~中部 | 背包物品网格 |
| `ShopGroup` | ~中部 | 商店物品列表 |
| `FocusPanel` | **2419 行** | ★★★ 番茄钟/专注计时面板（你加倒计时功能的核心） |
| `ProgressPanel` | **2755 行** | ★★ 每日目标进度面板 |
| `TaskPanel` | **3143 行** | ★★ 每日任务面板（你加事件簿的基础） |
| `TaskCard` | **3364 行** | 单个任务卡片 |

#### FocusPanel（番茄钟/专注计时）详细结构

```python
class FocusPanel(CardWidget):
    # 信号
    start_pomodoro     # → PetWidget.run_tomato()
    cancel_pomodoro    # → PetWidget.cancel_tomato()
    start_focus        # → PetWidget.run_focus()
    cancel_focus       # → PetWidget.cancel_focus()

    # UI 组件
    self.timePicker         # 时间选择器（QTimeEdit）
    self.pomodoroCheckBox   # 番茄钟模式开关
    self.startFocusButton   # 开始按钮
    self.cancelFocusButton  # 取消按钮

    # 功能
    # - 正计时/倒计时两种模式
    # - 番茄钟休息周期
    # - 计时完成奖励金币
```

#### TaskPanel（每日任务）详细结构

```python
class TaskPanel(CardWidget):
    # 已有功能：
    # - 任务列表显示（TaskCard 列表）
    # - 添加/删除任务
    # - 勾选完成/取消完成
    # - 任务进度统计
```

### 3.3 `statusUI.py` — 状态面板

```python
class statusInterface(ScrollArea):
    # 组件
    self.StatusCard       # HP/FV/等级/头像
    self.noteLog          # 日志流（NoteFlowGroup）
    self.buffCard         # Buff 状态

    # 信号
    changeStatus          # 请求更改宠物状态
```

### 3.4 `buffModule.py` — Buff 系统

```python
# Buff 效果类型
# - hp: 增加/减少 HP
# - fv: 增加/减少 FV
# - coin: 增加/减少金币
# - HP_stop: 暂停 HP 衰减
# - FV_stop: 暂停 FV 增长

class BuffAdd(QObject):       # 累加型 Buff
class BuffAlt(QObject):        # 状态改变型 Buff
class BuffThread(QThread):     # Buff 管理线程
```

---

## 四、DyberSettings 设置面板

```
DyberSettings/
├── DyberControlPanel.py      ← ★ 设置主窗口（FluentWindow，5 个子面板）
├── BasicSettingUI.py         ← ★★ 基础设置面板（置顶、缩放、语言、主题）
├── GameSaveUI.py             ← 存档管理面板（保存/加载/导出）
├── CharCardUI.py             ← ★ 角色管理面板（添加/切换角色）
├── ItemCardUI.py             ← 物品 MOD 管理面板
├── PetCardUI.py              ← 附属宠物管理面板
├── fileOp_utils.py           ← 存档文件操作（复制/MD5校验/打包）
├── custom_base.py            ← 自定义基础组件（对话框基类）
├── custom_combobox.py        ← 自定义下拉框组件
└── custom_utils.py           ← ★★ 自定义工具组件（角色卡片、物品卡片、设置卡片等）
```

### 4.1 `DyberControlPanel.py` — 设置主窗口

```python
class ControlMainWindow(FluentWindow):
    # 5 个子面板
    self.settingInterface      # 设置 (Settings)
    self.gamesaveInterface     # 存档 (Game Save)
    self.charCardInterface     # 角色管理 (Characters)
    self.itemCardInterface     # 物品 MOD (Item MOD)
    self.petCardInterface      # 迷你宠物 (Mini-Pets)
```

### 4.2 `BasicSettingUI.py` — 基础设置

| 设置项 | 说明 |
|-------|------|
| Always-On-Top | 窗口是否始终置顶 |
| Allow Drop | 释放鼠标后是否掉落 |
| Auto-Lock | 锁屏时是否冻结状态 |
| Gravity | 掉落重力 |
| Scale | 宠物缩放比例 |
| Language | 语言切换 |
| Theme | 主题色 |

**注意**：当前版本中**没有 LLM API 设置项**（API Key、模型选择等），这是你需要新增的。

### 4.3 `CharCardUI.py` — 角色管理

```python
class CharInterface(ScrollArea):
    change_pet = Signal(str)  # 切换宠物信号

    # 功能：
    # - 扫描 res/role/ 下的角色
    # - 显示角色卡片列表
    # - 导入新角色（从文件夹或 .zip）
    # - 切换当前宠物
```

---

## 五、资源文件结构 `res/`

### 5.1 整体概览

```
res/
├── icons/              ← 系统图标
│   ├── Dashboard/      ← 控制台图标（进度、商店、任务等 SVG）
│   ├── system/         ← 系统功能图标（菜单、拖拽、缩放等）
│   ├── bubbles/        ← 气泡表情图标（开心、困惑、哭泣等）
│   └── *.svg/png       ← 通用图标
├── pet/                ← ★ 正式宠物资源（框架自带）
│   └── 派蒙/           ← 原神派蒙
├── role/               ← ★ 角色资源（MOD 角色）
│   ├── Kitty/          ← 示例：Kitty 猫
│   ├── ChrisKitty/     ← 示例：Chris 猫
│   └── sys/            ← 系统特效（爱心气泡）
├── items/              ← 物品资源
│   └── Default/        ← 默认物品（食物、金币）
├── sounds/             ← 音效文件
│   └── Notification.wav
└── language/           ← 国际化翻译
    ├── langs.zh_CN.ts  ← 中文翻译源文件
    ├── langs.zh_CN.qm  ← 编译后的中文翻译
    └── language.json   ← 语言配置
```

### 5.2 宠物角色资源结构（★ 你添加一二布布时参考）

```
res/pet/派蒙/                    ← 角色文件夹
├── 派蒙.png                     ← 角色缩略图
├── pet_conf.json                ← ★ 宠物配置文件
├── act_conf.json                ← ★ 动作配置文件
├── items_config.json            ← 专属物品配置
├── action/                      ← 动作帧图片
│   ├── pm_0.png                 ← 精灵帧，命名格式：{前缀}_{序号}.png
│   ├── pm_1.png
│   └── ...（共 24 帧）
└── info/                        ← 角色信息
    ├── info.json                ← 角色元信息（名称、标签、作者、介绍）
    ├── 派蒙.png                 ← 头像
    └── cgg.png                  ← 作者头像
```

#### `pet_conf.json` 字段说明

```json
{
  "width": 112,           // 帧宽度（像素）
  "height": 128,          // 帧高度（像素）
  "scale": 1.0,           // 缩放比例
  "interact_speed": 0.02, // 交互响应速度

  // === 状态→动作映射 ===
  "default": "default",   // 默认站立动作
  "up": "default",        // 向上移动时的动作
  "down": "default",      // 向下移动时的动作
  "left": "default",      // 向左移动时的动作
  "right": "default",     // 向右移动时的动作
  "drag": "default",      // 被拖拽时的动作
  "fall": "default",      // 下落时的动作
  "on_floor": "default",  // 落地时的动作
  "patpat": "default",    // 被抚摸时的动作

  // === 窗口定位 ===
  "follow_main_x": true,  // 是否跟随主窗口 X 轴
  "follow_main_y": true,  // 是否跟随主窗口 Y 轴
  "anchor_to_main": [10, -80], // 相对主窗口的锚点偏移

  // === 随机动画列表 ===
  "random_act": [{
    "name": "default",          // 动画名称
    "act_list": ["default"],    // 动画动作列表
    "act_prob": 1.0,            // 触发概率
    "act_type": [0, 10000]      // [动作类型, 持续时间 ms]
  }],

  // === 交互动作 ===
  "main_interact": {}       // 与主宠物的交互动作定义
}
```

#### `act_conf.json` 字段说明

```json
{
  "default": {               // 动作名称
    "images": "pm",          // 帧图片前缀
    "act_num": 1,            // 帧数量（如果有多帧则自动编号 pm_0, pm_1...）
    "frame_refresh": 0.08    // 帧刷新间隔（秒），越小越流畅
  }
}
```

### 5.3 `res/role/` 与 `res/pet/` 的区别

| | `res/role/` | `res/pet/` |
|---|---|---|
| **用途** | MOD 角色（用户自行添加） | 框架自带角色 |
| **角色** | Kitty, ChrisKitty, sys | 派蒙 |
| **结构** | 相同 | 相同 |
| **管理** | CharCardUI 面板管理 | 默认预置 |

---

## 六、数据存储结构

### 6.1 存档文件（运行时生成在 `data/` 目录下）

| 文件 | 对应的配置类 | 内容 |
|------|------------|------|
| `settings.json` | - | 用户设置（置顶、缩放、语言等） |
| `pet_data.json` | `PetData` | 宠物运行数据（HP、FV、金币、等级） |
| `task_data.json` | `TaskData` | 任务数据（每日目标、番茄设置、任务列表） |
| `act_data.json` | `ActData` | 动画配置（动作解锁状态、概率） |
| `version` | - | 版本号 |

### 6.2 配置加载流程

```
程序启动
  → settings.init()                    ← 加载 settings.json
  → PetConfig.init_config(pet_name)    ← 加载 res/role/{name}/pet_conf.json + act_conf.json
  → PetData()                          ← 加载 data/pet_data.json
  → TaskData()                         ← 加载 data/task_data.json
  → ActData(petsList)                  ← 加载 data/act_data.json
```

---

## 七、你开发时需要关注的核心文件优先级

### 🔴 一级优先（必须理解）

| 文件 | 理由 |
|------|------|
| `run_DyberPet.py` | 理解整体启动流程和信号连接方式 |
| `DyberPet/DyberPet.py` | 宠物主窗口，你需要在这里加 LLM 聊天入口和事件簿入口 |
| `DyberPet/extra_windows.py` | DPDialogue（对话气泡）、Tomato（番茄钟）、Focus（专注）、Remindme（提醒），你的 LLM 对话和倒计时直接扩展这些 |
| `DyberPet/modules.py` | Scheduler_worker，你的定时事件簿需要用到 APScheduler |

### 🟡 二级优先（需要修改）

| 文件 | 理由 |
|------|------|
| `DyberPet/Dashboard/dashboard_widgets.py` | FocusPanel（倒计时面板）、TaskPanel（事件簿面板） |
| `DyberPet/Dashboard/taskUI.py` | 事件簿面板的容器界面 |
| `DyberPet/DyberSettings/BasicSettingUI.py` | 需要新增 LLM API 设置卡片 |
| `DyberPet/conf.py` | 可能需要新增 LLM 聊天数据的配置类 |

### 🟢 三级优先（只需了解）

| 文件 | 理由 |
|------|------|
| `res/pet/派蒙/pet_conf.json` | 参考格式，用来配置一二布布的角色 |
| `res/pet/派蒙/act_conf.json` | 参考格式，配置一二布布的动作 |
| `DyberPet/bubbleManager.py` | 了解气泡行为，LLM 回复可触发气泡 |
| `DyberPet/Notification.py` | 了解通知机制，事件簿提醒用 |

---

## 八、你新增功能的具体落点建议

### 8.1 LLM 智能对话

```
新增文件:
  DyberPet/llm_chat.py          ← LLM API 调用封装（DeepSeek SDK）
  DyberPet/chat_window.py       ← 聊天窗口 UI（继承 DPDialogue 或新建）

修改文件:
  DyberPet/DyberPet.py          ← PetWidget 增加聊天入口（右键菜单或点击）
  DyberPet/DyberSettings/BasicSettingUI.py ← 增加 API Key / Model 设置卡片
  DyberPet/custom_roundmenu.py  ← 右键菜单增加"聊天"选项
```

### 8.2 每日待定事件簿

```
新增文件:
  DyberPet/event_notebook.py    ← 事件簿核心逻辑（LLM 生成事件、提醒触发）

修改文件:
  DyberPet/Dashboard/taskUI.py           ← 扩展 TaskPanel，增加"事件簿"子面板
  DyberPet/Dashboard/dashboard_widgets.py ← 新增 EventCard 组件
  DyberPet/modules.py                    ← Scheduler_worker 增加事件提醒定时器
  DyberPet/Notification.py               ← 增加事件提醒通知类型
```

### 8.3 倒计时/番茄钟联动

```
修改文件（无需新增）:
  DyberPet/Dashboard/dashboard_widgets.py ← FocusPanel 增加一二布布动画触发
  DyberPet/DyberPet.py                    ← PetWidget 增加动画联动逻辑
  DyberPet/modules.py                    ← Scheduler_worker 增加计时回调中的动画触发
```

---

## 九、技术依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| PySide6 | 6.5.2+ | Qt Python 绑定（GUI 框架） |
| qfluentwidgets | - | Fluent Design 组件库 |
| qframelesswindow | - | 无边框窗口支持 |
| APScheduler | - | 定时任务调度 |
| pynput | - | 全局鼠标/键盘监听 |
| tendo | - | 单实例锁 |

---

## 十、关键设计模式与约定

1. **信号槽通信**：所有子系统之间通过 PySide6 Signal/Slot 通信，不直接调用方法
2. **JSON 驱动配置**：角色、动作、物品均通过 JSON 文件定义，不修改代码
3. **工作线程分离**：动画播放、交互检测、定时任务各自在独立线程中运行
4. **MVC 变体**：`conf.py` = Model（数据），`DyberPet.py` = View + Controller
5. **FluentWindow 面板化**：设置和控制台都使用 FluentWindow 的左侧导航+右侧内容布局
